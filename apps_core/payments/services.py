from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from apps_core.bookings.models import Booking
from apps_core.payments.models import Payment



PAYMENT_STATUS_TRANSITIONS = {
    "pending": {"processing", "failed"},
    "processing": {"paid", "failed"},
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
    Create a pending payment for a booking.

    If amount is not supplied, the booking's total_price is used.
    A custom amount may be supplied when the payment flow requires it.
    """

    if booking is None:
        raise ValueError("Booking is required.")

    if not phone_number:
        raise ValueError("Phone number is required.")

    if amount is None:
        amount = booking.total_price

    amount = Decimal(str(amount))

    if amount <= Decimal("0.00"):
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
        .order_by("-created_at")
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
    Mark a payment as processing after Safaricom accepts
    the STK Push request.
    """

    _validate_payment(payment)

    if not checkout_request_id:
        raise ValueError("Checkout request ID is required.")

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

    This is the point at which the system records the M-Pesa receipt
    and treats the payment as financially successful.
    """

    _validate_payment(payment)

    if not mpesa_receipt:
        raise ValueError("M-Pesa receipt is required.")

    _validate_status_transition(
        payment.status,
        "paid",
    )

    payment.status = "paid"
    payment.mpesa_receipt = mpesa_receipt
    payment.result_code = str(result_code)
    payment.result_description = result_description

    if transaction_date:
        payment.transaction_date = transaction_date
    else:
        payment.transaction_date = timezone.now()

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
    Mark a payment as failed.

    Failed payments remain attached to the booking so that the
    payment history is preserved.
    """

    _validate_payment(payment)

    _validate_status_transition(
        payment.status,
        "failed",
    )

    payment.status = "failed"

    if result_code is not None:
        payment.result_code = str(result_code)

    payment.result_description = result_description

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
    Internal validation helper.
    """

    if not isinstance(payment, Payment):
        raise ValueError("A valid payment is required.")

    if not payment.pk:
        raise ValueError("The payment must be saved before its status can change.")


def _validate_status_transition(current_status, new_status):
    """
    Enforce the payment status state machine.
    """

    valid_statuses = dict(Payment.STATUS_CHOICES)

    if new_status not in valid_statuses:
        raise ValueError(
            f"Invalid payment status: {new_status}"
        )

    if current_status == new_status:
        raise ValueError(
            f"Payment is already {current_status}."
        )

    allowed_statuses = PAYMENT_STATUS_TRANSITIONS.get(
        current_status,
        set(),
    )

    if new_status not in allowed_statuses:
        raise ValueError(
            f"Payment cannot move from "
            f"{current_status} to {new_status}."
        )



@transaction.atomic
def initiate_payment(
    payment,
):
    """
    Initiate an M-Pesa STK Push for an existing payment.

    The payment must already exist and be in pending status.

    The payment is moved to processing only after
    Daraja accepts the STK Push request.
    """

    if not isinstance(payment, Payment):
        raise ValueError(
            "A valid payment is required."
        )

    if not payment.pk:
        raise ValueError(
            "The payment must be saved before "
            "an STK Push can be initiated."
        )

    if payment.status != "pending":
        raise ValueError(
            "Only pending payments can be initiated."
        )

    from .integrations.mpesa import MpesaIntegration

    mpesa = MpesaIntegration()

    response = mpesa.initiate_stk_push(
        amount=payment.amount,
        phone_number=payment.phone_number,
        account_reference=(
            payment.booking.booking_reference
        ),
        transaction_description=(
            f"CLEANEX payment for "
            f"{payment.booking.booking_reference}"
        ),
    )

    response_code = response.get(
        "ResponseCode"
    )

    if response_code != "0":
        raise ValueError(
            response.get(
                "ResponseDescription",
                "M-Pesa STK Push was rejected.",
            )
        )

    checkout_request_id = response.get(
        "CheckoutRequestID"
    )

    merchant_request_id = response.get(
        "MerchantRequestID"
    )

    if not checkout_request_id:
        raise ValueError(
            "M-Pesa did not return a CheckoutRequestID."
        )

    mark_payment_processing(
        payment,
        checkout_request_id=checkout_request_id,
        merchant_request_id=merchant_request_id,
    )

    return payment



@transaction.atomic
def process_mpesa_callback(payload):
    """
    Process a Safaricom M-Pesa STK callback.

    The callback is the authoritative source for the
    final payment result.

    Successful callbacks move the payment to paid.
    Failed callbacks move the payment to failed.

    Duplicate callbacks are handled safely and do not
    attempt to change an already-paid or already-failed
    payment again.
    """

    if not isinstance(payload, dict):
        raise ValueError(
            "A valid callback payload is required."
        )

    body = payload.get("Body")

    if not isinstance(body, dict):
        raise ValueError(
            "M-Pesa callback Body is missing."
        )

    stk_callback = body.get("stkCallback")

    if not isinstance(stk_callback, dict):
        raise ValueError(
            "M-Pesa stkCallback is missing."
        )

    checkout_request_id = stk_callback.get(
        "CheckoutRequestID"
    )

    if not checkout_request_id:
        raise ValueError(
            "Checkout request ID is missing from callback."
        )

    payment = (
        Payment.objects
        .select_for_update()
        .filter(
            checkout_request_id=checkout_request_id
        )
        .first()
    )

    if payment is None:
        raise ValueError(
            "Payment matching the callback was not found."
        )

    result_code = stk_callback.get(
        "ResultCode"
    )

    result_description = stk_callback.get(
        "ResultDesc",
        "",
    )

    # Safely ignore duplicate callbacks.
    if payment.status in {
        "paid",
        "failed",
    }:
        return payment

    if str(result_code) == "0":
        callback_items = stk_callback.get(
            "CallbackMetadata",
            {},
        )

        item_list = callback_items.get(
            "Item",
            [],
        )

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
                "M-Pesa receipt is missing from "
                "successful callback."
            )

        if transaction_date:
            try:
                transaction_date = timezone.datetime.strptime(
                    str(transaction_date),
                    "%Y%m%d%H%M%S",
                )
                transaction_date = timezone.make_aware(
                    transaction_date,
                    timezone.get_current_timezone(),
                )
            except (TypeError, ValueError):
                transaction_date = timezone.now()

        return mark_payment_paid(
            payment,
            mpesa_receipt=str(mpesa_receipt),
            transaction_date=transaction_date,
            result_code=result_code,
            result_description=result_description
            or "Payment completed successfully.",
        )

    return mark_payment_failed(
        payment,
        result_code=result_code,
        result_description=(
            result_description
            or "Payment failed."
        ),
    )

