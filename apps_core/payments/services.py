from decimal import Decimal
import logging
from django.db import transaction
from django.utils import timezone
from apps_core.bookings.models import Booking
from apps_core.payments.models import Payment


logger = logging.getLogger(__name__)


# Payment state machine.
#
# "pending" -> "initiating" -> "processing" -> "paid"
#                                      \----> "failed"
#                                      \----> "unknown"
#
# "unknown" can be resolved by a verified callback to "paid" or "failed".
# "paid" and "failed" are terminal states.
#
# pending -> processing is retained for compatibility with direct service
# calls and existing tests. initiate_payment() always persists "initiating"
# before making the external Daraja request.
PAYMENT_STATUS_TRANSITIONS = {
    "pending": {"initiating", "processing", "failed"},
    "initiating": {"processing", "failed", "unknown"},
    "processing": {"paid", "failed", "unknown"},
    "unknown": {"paid", "failed"},
    "paid": set(),
    "failed": set(),
}


@transaction.atomic
def create_payment(
    booking,
    phone_number,
    amount=None,
):
    """
    Create a new pending payment for a booking.

    A separate Payment record is created for each new attempt, preserving
    the history of previous failed attempts.
    """

    if booking is None or not isinstance(booking, Booking):
        raise ValueError("A valid booking is required.")

    if not phone_number:
        raise ValueError("Phone number is required.")

    if amount is None:
        amount = booking.total_price

    try:
        amount = Decimal(str(amount))
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError("A valid payment amount is required.") from exc

    if not amount.is_finite() or amount <= Decimal("0.00"):
        raise ValueError("Payment amount must be greater than zero.")

    return Payment.objects.create(
        booking=booking,
        amount=amount,
        method="mpesa",
        status="pending",
        phone_number=phone_number,
    )


def get_payment_for_booking(booking):
    """
    Return the most recent payment associated with a booking.
    """

    if not isinstance(booking, Booking):
        raise ValueError("A valid booking is required.")

    return (
        Payment.objects
        .filter(booking=booking)
        .order_by("-created_at", "-pk")
        .first()
    )


@transaction.atomic
def mark_payment_processing(
    payment,
    *,
    checkout_request_id,
    merchant_request_id=None,
):
    """
    Mark a payment as processing after Daraja accepts the STK Push.

    The checkout request ID is stored so the callback can be matched to
    this payment attempt.
    """

    _validate_payment(payment)

    if not checkout_request_id:
        raise ValueError("Checkout request ID is required.")

    payment = Payment.objects.select_for_update().get(pk=payment.pk)

    _validate_status_transition(
        payment.status,
        "processing",
    )

    payment.status = "processing"
    payment.checkout_request_id = checkout_request_id

    if merchant_request_id:
        payment.merchant_request_id = merchant_request_id

    payment.save(
        update_fields=[
            "status",
            "checkout_request_id",
            "merchant_request_id",
            "updated_at",
        ]
    )

    return payment


@transaction.atomic
def mark_payment_paid(
    payment,
    *,
    mpesa_receipt,
    transaction_date=None,
    result_code="0",
    result_description="Payment completed successfully.",
):
    """
    Mark a payment as successfully paid.

    A valid M-Pesa receipt is required before the payment can be marked
    as paid. Payments already paid or failed cannot be changed again.
    """

    _validate_payment(payment)

    if not mpesa_receipt:
        raise ValueError("M-Pesa receipt is required.")

    payment = Payment.objects.select_for_update().get(pk=payment.pk)

    _validate_status_transition(
        payment.status,
        "paid",
    )

    payment.status = "paid"
    payment.mpesa_receipt = str(mpesa_receipt)
    payment.result_code = str(result_code)
    payment.result_description = (
        result_description or "Payment completed successfully."
    )
    payment.transaction_date = transaction_date or timezone.now()

    payment.save(
        update_fields=[
            "status",
            "mpesa_receipt",
            "result_code",
            "result_description",
            "transaction_date",
            "updated_at",
        ]
    )

    return payment


@transaction.atomic
def mark_payment_failed(
    payment,
    *,
    result_code=None,
    result_description="Payment failed.",
):
    """
    Mark a payment as definitively failed.

    Failed records are retained so that previous payment attempts are
    not deleted when a customer starts a new attempt.
    """

    _validate_payment(payment)

    payment = Payment.objects.select_for_update().get(pk=payment.pk)

    _validate_status_transition(
        payment.status,
        "failed",
    )

    payment.status = "failed"

    if result_code is not None:
        payment.result_code = str(result_code)

    payment.result_description = (
        result_description or "Payment failed."
    )

    payment.save(
        update_fields=[
            "status",
            "result_code",
            "result_description",
            "updated_at",
        ]
    )

    return payment


@transaction.atomic
def mark_payment_unknown(
    payment,
    *,
    result_code=None,
    result_description=(
        "The payment outcome could not be confirmed. "
        "Verify the transaction before retrying."
    ),
):
    """
    Mark a payment as having an uncertain outcome.

    Use this when a request may have reached Safaricom but the application
    cannot confirm the result, such as after a timeout or connection error.

    Unknown payments must not be automatically retried. A verified callback
    can later resolve the payment to paid or failed.
    """

    _validate_payment(payment)

    payment = Payment.objects.select_for_update().get(pk=payment.pk)

    _validate_status_transition(
        payment.status,
        "unknown",
    )

    payment.status = "unknown"

    if result_code is not None:
        payment.result_code = str(result_code)

    payment.result_description = (
        result_description
        or "The payment outcome could not be confirmed."
    )

    payment.save(
        update_fields=[
            "status",
            "result_code",
            "result_description",
            "updated_at",
        ]
    )

    return payment


def _validate_payment(payment):
    """
    Validate that the supplied object is a saved Payment instance.
    """

    if not isinstance(payment, Payment):
        raise ValueError("A valid payment is required.")

    if not payment.pk:
        raise ValueError(
            "The payment must be saved before its status can change."
        )


def _validate_status_transition(current_status, new_status):
    """
    Enforce the payment status state machine.
    """

    valid_statuses = dict(Payment.STATUS_CHOICES)

    if new_status not in valid_statuses:
        raise ValueError(f"Invalid payment status: {new_status}")

    if current_status not in valid_statuses:
        raise ValueError(f"Invalid current payment status: {current_status}")

    if current_status == new_status:
        raise ValueError(f"Payment is already {current_status}.")

    allowed_statuses = PAYMENT_STATUS_TRANSITIONS.get(
        current_status,
        set(),
    )

    if new_status not in allowed_statuses:
        raise ValueError(
            f"Payment cannot move from {current_status} to {new_status}."
        )


def initiate_payment(payment):
    if not isinstance(payment, Payment) or payment.pk is None:
        raise ValueError(
            "The payment must be saved before an STK Push can be initiated."
        )

    """
    Initiate an M-Pesa STK Push for an existing pending payment.

    Workflow:
      1. Lock and validate the pending payment.
      2. Persist "initiating" and commit that state.
      3. Send the external STK Push request outside the transaction.
      4. Mark the payment "processing" if Daraja accepts the request.
      5. Mark a definite rejection "failed".
      6. Mark an ambiguous outcome "unknown".

    This prevents a second request from being started against the same
    payment record while an earlier request may already be in flight.
    """

    _validate_payment(payment)

    # Commit the initiating state before making the network request.
    # Do not keep a database transaction open during the HTTP request.
    with transaction.atomic():
        locked_payment = (
            Payment.objects
            .select_for_update()
            .select_related("booking")
            .get(pk=payment.pk)
        )

        if locked_payment.status != "pending":
            raise ValueError(
                "Only pending payments can be initiated."
            )

        _validate_status_transition(
            locked_payment.status,
            "initiating",
        )

        locked_payment.status = "initiating"
        locked_payment.save(
            update_fields=["status", "updated_at"]
        )

    # Keep the caller's instance consistent with the database.
    payment.refresh_from_db()

    from .integrations.mpesa import MpesaIntegration

    try:
        mpesa = MpesaIntegration()

        response = mpesa.initiate_stk_push(
            amount=payment.amount,
            phone_number=payment.phone_number,
            account_reference=payment.booking.booking_reference,
            transaction_description=(
                f"CLEANEX payment for "
                f"{payment.booking.booking_reference}"
            ),
        )

    except Exception:
        # A timeout or connection failure does not prove that Safaricom
        # rejected the request. Do not automatically make it retryable.
        logger.exception(
            "Uncertain outcome while submitting STK Push for payment %s.",
            payment.pk,
        )

        try:
            mark_payment_unknown(
                payment,
                result_description=(
                    "The payment request outcome could not be confirmed. "
                    "Verify the transaction before retrying."
                ),
            )
        except Exception:
            logger.exception(
                "Could not mark payment %s as unknown after an "
                "STK Push submission error.",
                payment.pk,
            )

        # Preserve the original exception for the calling view to handle.
        raise

    if not isinstance(response, dict):
        mark_payment_unknown(
            payment,
            result_description=(
                "Safaricom returned an unexpected response. "
                "Verify the transaction before retrying."
            ),
        )
        raise ValueError(
            "The payment outcome could not be confirmed. "
            "Verify the transaction before retrying."
        )

    response_code = response.get("ResponseCode")

    # Only a clear non-zero Daraja response is treated as a definite
    # rejection. Missing or malformed response codes are ambiguous.
    if response_code is not None and str(response_code) != "0":
        description = (
            response.get("ResponseDescription")
            or response.get("errorMessage")
            or "Safaricom rejected the STK Push request."
        )

        mark_payment_failed(
            payment,
            result_code=response_code,
            result_description=str(description),
        )

        raise ValueError(str(description))

    if str(response_code) != "0":
        mark_payment_unknown(
            payment,
            result_description=(
                "Safaricom returned no valid response code. "
                "Verify the transaction before retrying."
            ),
        )
        raise ValueError(
            "The payment outcome could not be confirmed. "
            "Verify the transaction before retrying."
        )

    checkout_request_id = response.get("CheckoutRequestID")
    merchant_request_id = response.get("MerchantRequestID")

    # A successful response code without a checkout ID cannot be safely
    # correlated with a later callback. Treat the outcome as uncertain.
    if not checkout_request_id:
        mark_payment_unknown(
            payment,
            result_description=(
                "Safaricom accepted the request but did not return a "
                "checkout request ID. Verify the transaction before retrying."
            ),
        )
        raise ValueError(
            "The payment outcome could not be confirmed. "
            "Contact CLEANEX support before retrying."
        )

    mark_payment_processing(
        payment,
        checkout_request_id=checkout_request_id,
        merchant_request_id=merchant_request_id,
    )

    payment.refresh_from_db()
    return payment


@transaction.atomic
def process_mpesa_callback(payload):
    """
    Process a Safaricom M-Pesa STK callback.

    The callback is used to resolve the payment result. Duplicate callbacks
    for already-paid or already-failed payments are ignored.

    Callbacks can resolve payments in "processing" or "unknown" states.
    """

    if not isinstance(payload, dict):
        raise ValueError("A valid callback payload is required.")

    body = payload.get("Body")

    if not isinstance(body, dict):
        raise ValueError("M-Pesa callback Body is missing.")

    stk_callback = body.get("stkCallback")

    if not isinstance(stk_callback, dict):
        raise ValueError("M-Pesa stkCallback is missing.")

    checkout_request_id = stk_callback.get("CheckoutRequestID")

    if not checkout_request_id:
        raise ValueError("Checkout request ID is missing from callback.")

    payment = (
        Payment.objects
        .select_for_update()
        .filter(checkout_request_id=checkout_request_id)
        .first()
    )

    if payment is None:
        raise ValueError(
            "Payment matching the callback was not found."
        )

    result_code = stk_callback.get("ResultCode")

    if result_code is None:
        raise ValueError("M-Pesa callback ResultCode is missing.")

    result_description = stk_callback.get("ResultDesc", "")

    # Safely ignore duplicate callbacks for terminal states.
    if payment.status in {"paid", "failed"}:
        return payment

    if payment.status not in {"initiating", "processing", "unknown"}:
        raise ValueError(
            f"Cannot process callback for payment status {payment.status}."
        )

    if str(result_code) == "0":
        callback_metadata = stk_callback.get("CallbackMetadata", {})

        if not isinstance(callback_metadata, dict):
            raise ValueError("Callback metadata is invalid.")

        item_list = callback_metadata.get("Item", [])

        if not isinstance(item_list, list):
            raise ValueError("Callback metadata items are invalid.")

        mpesa_receipt = None
        transaction_date = None

        for item in item_list:
            if not isinstance(item, dict):
                continue

            name = item.get("Name")
            value = item.get("Value")

            if name == "MpesaReceiptNumber":
                mpesa_receipt = value

            elif name == "TransactionDate":
                transaction_date = value

        if not mpesa_receipt:
            raise ValueError(
                "M-Pesa receipt is missing from successful callback."
            )

        parsed_transaction_date = None

        if transaction_date:
            try:
                parsed_transaction_date = timezone.datetime.strptime(
                    str(transaction_date),
                    "%Y%m%d%H%M%S",
                )
                parsed_transaction_date = timezone.make_aware(
                    parsed_transaction_date,
                    timezone.get_current_timezone(),
                )
            except (TypeError, ValueError, OverflowError):
                # A malformed transaction timestamp should not discard
                # a valid receipt and successful callback.
                logger.warning(
                    "Invalid transaction date in callback for payment %s.",
                    payment.pk,
                )
                parsed_transaction_date = timezone.now()

        return mark_payment_paid(
            payment,
            mpesa_receipt=str(mpesa_receipt),
            transaction_date=parsed_transaction_date,
            result_code=result_code,
            result_description=(
                result_description
                or "Payment completed successfully."
            ),
        )

    return mark_payment_failed(
        payment,
        result_code=result_code,
        result_description=result_description or "Payment failed.",
    )
