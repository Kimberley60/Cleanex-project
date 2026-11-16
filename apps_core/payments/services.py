from decimal import Decimal
from datetime import datetime, timedelta, timezone as datetime_timezone
from email.utils import parsedate_to_datetime
import logging
import random
import re
import requests
import uuid
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone
from apps_core.bookings.models import Booking
from apps_core.payments.models import (
    MpesaCallbackMetadata,
    MpesaCallbackResult,
    MpesaQueryThrottle,
    Payment,
)


logger = logging.getLogger(__name__)


# Payment state machine.
#
# "pending" -> "initiating" -> "processing" -> "paid"
#                                      \----> "failed"
#                                      \----> "unknown"
#
# "unknown" can be resolved by independent verification to "paid" or
# "failed". "paid" and "failed" are terminal states.
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

# STK Push Query result codes that Safaricom reports as a completed,
# non-successful attempt. These are safe to treat as failed so the customer
# can start a new prompt. Inconclusive or still-processing codes are not
# included and must remain uncertain.
MPESA_TERMINAL_FAILURE_RESULT_CODES = {
    "1019",  # Transaction expired.
    "1032",  # Request cancelled by user.
    "1037",  # Prompt timed out / handset unreachable.
    "1",     # Insufficient M-Pesa balance.
}


VERIFICATION_LEASE_SECONDS = 120
VERIFICATION_BASE_DELAY_SECONDS = 30
VERIFICATION_MAX_DELAY_SECONDS = 3600
VERIFICATION_BACKOFF_EXPONENT_CAP = 7
VERIFICATION_JITTER_RATIO = 0.2
VERIFICATION_ELIGIBLE_STATUSES = {"processing", "unknown", "paid"}

# Shared STK Push Query throttle. Daraja rate limits apply per API
# consumer, not per payment, so query requests are coordinated across
# all payments and workers through a single database row.
MPESA_QUERY_THROTTLE_PK = 1
MPESA_QUERY_WINDOW_SECONDS = 60
MPESA_QUERY_MAX_PER_WINDOW = 10
MPESA_QUERY_DEFAULT_BLOCK_SECONDS = 60
MPESA_QUERY_MAX_BLOCK_SECONDS = 3600


def _retry_after_seconds(value):
    """Return Retry-After seconds from a header value, if valid."""

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        seconds = int(value)
        return max(seconds, 0) if seconds >= 0 else None

    text = str(value).strip()

    if not text:
        return None

    if text.isdigit():
        return int(text)

    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime_timezone.utc)

    seconds = int((parsed - timezone.now()).total_seconds())
    return max(seconds, 0)


def _verification_delay_seconds(attempt_count, *, retry_after=None):
    """Return a bounded verification delay with optional Retry-After."""

    retry_seconds = _retry_after_seconds(retry_after)

    if retry_seconds is not None:
        return min(retry_seconds, VERIFICATION_MAX_DELAY_SECONDS)

    exponent = max(
        0,
        min(int(attempt_count) - 1, VERIFICATION_BACKOFF_EXPONENT_CAP),
    )
    base_delay = VERIFICATION_BASE_DELAY_SECONDS * (2 ** exponent)
    base_delay = min(base_delay, VERIFICATION_MAX_DELAY_SECONDS)
    jitter = random.uniform(
        0,
        base_delay * VERIFICATION_JITTER_RATIO,
    )

    return min(base_delay + jitter, VERIFICATION_MAX_DELAY_SECONDS)


def _next_verification_at(attempt_count, *, retry_after=None):
    return timezone.now() + timedelta(
        seconds=_verification_delay_seconds(
            attempt_count,
            retry_after=retry_after,
        )
    )


def _claim_payment_verification(payment):
    """
    Atomically claim the right to run one STK Push Query for a payment.

    Returns a lease ID when the claim succeeds, otherwise None. The lease
    expires automatically so a crashed worker cannot block verification
    forever.
    """

    now = timezone.now()
    lease_id = uuid.uuid4()
    lease_expires_at = now + timedelta(seconds=VERIFICATION_LEASE_SECONDS)

    claimed = (
        Payment.objects
        .filter(
            pk=payment.pk,
            status__in=VERIFICATION_ELIGIBLE_STATUSES,
        )
        .filter(
            Q(next_verification_at__isnull=True)
            | Q(next_verification_at__lte=now)
        )
        .filter(
            Q(verification_lease_expires_at__isnull=True)
            | Q(verification_lease_expires_at__lte=now)
        )
        .update(
            verification_attempt_count=F("verification_attempt_count") + 1,
            last_verification_at=now,
            verification_lease_id=lease_id,
            verification_lease_expires_at=lease_expires_at,
        )
    )

    if claimed != 1:
        return None

    return lease_id


def _finish_payment_verification(
    payment,
    lease_id,
    *,
    next_verification_at,
):
    """Release a verification lease and schedule the next attempt."""

    Payment.objects.filter(
        pk=payment.pk,
        verification_lease_id=lease_id,
    ).update(
        verification_lease_id=None,
        verification_lease_expires_at=None,
        next_verification_at=next_verification_at,
    )


def _claim_mpesa_query_slot():
    """
    Atomically claim one shared STK Push Query slot.

    Returns True when a provider query may proceed. The claim is
    serialized across workers by a row lock on the singleton throttle
    record, so concurrent payments cannot exceed the shared window.
    """

    now = timezone.now()
    window_cutoff = now - timedelta(
        seconds=MPESA_QUERY_WINDOW_SECONDS
    )

    with transaction.atomic():
        throttle, _ = (
            MpesaQueryThrottle.objects
            .select_for_update()
            .get_or_create(
                pk=MPESA_QUERY_THROTTLE_PK,
                defaults={
                    "window_started_at": now,
                    "request_count": 0,
                    "blocked_until": None,
                },
            )
        )

        if (
            throttle.blocked_until is not None
            and throttle.blocked_until > now
        ):
            return False

        if throttle.window_started_at <= window_cutoff:
            throttle.window_started_at = now
            throttle.request_count = 0

        if throttle.request_count >= MPESA_QUERY_MAX_PER_WINDOW:
            return False

        throttle.request_count += 1
        throttle.save(
            update_fields=[
                "window_started_at",
                "request_count",
                "updated_at",
            ]
        )
        return True


def _record_mpesa_query_throttled(retry_after=None):
    """
    Persist a shared backoff after the provider rate-limits queries.

    Honors Retry-After when present and otherwise applies a conservative
    default block. The block is shared so no entry point can immediately
    retry against an exhausted consumer quota.
    """

    delay = _retry_after_seconds(retry_after)

    if delay is None:
        delay = MPESA_QUERY_DEFAULT_BLOCK_SECONDS

    delay = min(delay, MPESA_QUERY_MAX_BLOCK_SECONDS)
    blocked_until = timezone.now() + timedelta(seconds=delay)

    with transaction.atomic():
        throttle, _ = (
            MpesaQueryThrottle.objects
            .select_for_update()
            .get_or_create(
                pk=MPESA_QUERY_THROTTLE_PK,
                defaults={
                    "window_started_at": timezone.now(),
                    "request_count": 0,
                    "blocked_until": None,
                },
            )
        )

        if (
            throttle.blocked_until is None
            or throttle.blocked_until < blocked_until
        ):
            throttle.blocked_until = blocked_until
            throttle.save(
                update_fields=["blocked_until", "updated_at"]
            )


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

    The checkout request ID is stored so a later independent query can
    verify this payment attempt.
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
    mpesa_receipt=None,
    transaction_date=None,
    result_code="0",
    result_description="Payment completed successfully.",
):
    """
    Mark a payment as successfully paid.

    The payment must have been independently verified before this function
    is called. A receipt and transaction date are optional because STK Push
    Query does not return them. Payments already paid or failed cannot be
    changed again.
    """

    _validate_payment(payment)

    payment = Payment.objects.select_for_update().get(pk=payment.pk)

    _validate_status_transition(
        payment.status,
        "paid",
    )

    payment.status = "paid"
    payment.mpesa_receipt = (
        str(mpesa_receipt) if mpesa_receipt else None
    )
    payment.result_code = str(result_code)
    payment.result_description = (
        result_description or "Payment completed successfully."
    )
    payment.transaction_date = transaction_date

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


def _callback_metadata_map(stk_callback):
    """
    Return a safe name/value mapping from M-Pesa callback metadata.

    Malformed metadata is ignored so payment verification can continue.
    """

    metadata = stk_callback.get("CallbackMetadata")

    if not isinstance(metadata, dict):
        return {}

    items = metadata.get("Item")

    if not isinstance(items, list):
        return {}

    values = {}

    for item in items:
        if not isinstance(item, dict):
            continue

        name = item.get("Name")

        if not isinstance(name, str) or not name:
            continue

        values[name] = item.get("Value")

    return values


def _normalize_mpesa_receipt(value):
    """
    Normalize an M-Pesa receipt number without truncating it.
    """

    if value is None:
        return None

    receipt = str(value).strip()
    max_length = Payment._meta.get_field("mpesa_receipt").max_length

    if not receipt or len(receipt) > max_length:
        return None

    return receipt


def _parse_mpesa_transaction_date(value):
    """
    Parse an M-Pesa transaction date into an aware datetime.

    Invalid or missing values return None. The callback arrival time is
    never substituted for the provider transaction date.
    """

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    try:
        parsed = datetime.strptime(text, "%Y%m%d%H%M%S")
    except (TypeError, ValueError):
        return None

    return timezone.make_aware(
        parsed,
        timezone.get_current_timezone(),
    )


def _available_mpesa_receipt(payment, receipt):
    """
    Return the receipt only when it is not already assigned elsewhere.
    """

    if not receipt:
        return None

    conflict = (
        Payment.objects
        .select_for_update()
        .filter(mpesa_receipt=receipt)
        .exclude(pk=payment.pk)
        .exists()
    )

    if conflict:
        logger.warning(
            "Ignoring duplicate M-Pesa receipt for payment %s.",
            payment.pk,
        )
        return None

    return receipt


def _log_mpesa_provider_error(operation_context, payment_pk, exc):
    """
    Log safe M-Pesa provider HTTP diagnostics without secrets or bodies.
    """

    status_code = getattr(exc, "status_code", None)

    if status_code is None:
        return False

    provider_operation = getattr(exc, "operation", None) or operation_context
    retry_after = getattr(exc, "retry_after", None)
    request_id = getattr(exc, "request_id", None)
    error_code = getattr(exc, "error_code", None)
    error_message = getattr(exc, "error_message", None)

    if status_code == 403:
        logger.warning(
            "M-Pesa %s forbidden (HTTP 403) while verifying payment %s; "
            "retry_after=%s request_id=%s error_code=%s error_message=%s",
            provider_operation,
            payment_pk,
            retry_after,
            request_id,
            error_code,
            error_message,
        )
    elif status_code == 429:
        logger.warning(
            "M-Pesa %s rate limited (HTTP 429) while verifying payment %s; "
            "retry_after=%s request_id=%s error_code=%s error_message=%s",
            provider_operation,
            payment_pk,
            retry_after,
            request_id,
            error_code,
            error_message,
        )
    else:
        logger.warning(
            "M-Pesa %s request failed (HTTP %s) while verifying payment %s; "
            "retry_after=%s request_id=%s error_code=%s error_message=%s",
            provider_operation,
            status_code,
            payment_pk,
            retry_after,
            request_id,
            error_code,
            error_message,
        )

    return True


def _sanitize_log_text(value, *, max_length, fallback):
    """
    Return safe single-line log text for provider result fields.
    """

    if not isinstance(value, str):
        return fallback

    sanitized = re.sub(r"[\r\n]+", " ", value).strip()

    if not sanitized:
        return fallback

    return sanitized[:max_length]


def _safe_query_result_code(value):
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)

    return _sanitize_log_text(
        value,
        max_length=50,
        fallback="missing",
    )


def _safe_query_result_description(value):
    return _sanitize_log_text(
        value,
        max_length=200,
        fallback="unavailable",
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

    from .integrations.mpesa import (
        MpesaIntegration,
        MpesaLocalValidationError,
    )

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

    except MpesaLocalValidationError as exc:
        logger.warning(
            "Local M-Pesa validation rejected payment %s before "
            "submission: %s",
            payment.pk,
            exc,
        )

        try:
            mark_payment_failed(
                payment,
                result_description=(
                    "The payment request could not be submitted to "
                    "M-Pesa. Please contact CLEANEX support."
                ),
            )
        except Exception:
            logger.exception(
                "Could not mark payment %s as failed after a local "
                "M-Pesa validation error.",
                payment.pk,
            )

        raise

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


def verify_payment(payment, *, mpesa_receipt=None, transaction_date=None):
    """
    Independently verify a payment using its saved STK checkout ID.

    Provider requests happen outside database transactions. A short locked
    update rechecks payment state and identifiers before applying results.
    """

    _validate_payment(payment)
    payment = Payment.objects.get(pk=payment.pk)
    payment._verification_attempted = False

    if payment.status in {"paid", "failed"}:
        return payment

    if payment.status not in {"processing", "unknown"}:
        return payment

    checkout_request_id = payment.checkout_request_id

    if not checkout_request_id:
        logger.warning(
            "Cannot verify payment %s without a checkout request ID.",
            payment.pk,
        )
        return payment

    saved_status = payment.status
    saved_merchant_request_id = payment.merchant_request_id

    lease_id = _claim_payment_verification(payment)

    if lease_id is None:
        return payment

    payment.refresh_from_db()
    payment._verification_attempted = True
    attempt_count = payment.verification_attempt_count
    next_verification_at = _next_verification_at(attempt_count)

    from .integrations.mpesa import MpesaIntegration

    try:
        if not _claim_mpesa_query_slot():
            logger.info(
                "Deferring M-Pesa STK Push Query for payment %s: "
                "shared query rate limit reached.",
                payment.pk,
            )
            return payment

        try:
            response = MpesaIntegration().query_stk_push(
                checkout_request_id
            )
        except requests.RequestException as exc:
            if getattr(exc, "status_code", None) == 429:
                _record_mpesa_query_throttled(
                    getattr(exc, "retry_after", None)
                )
            next_verification_at = _next_verification_at(
                attempt_count,
                retry_after=getattr(exc, "retry_after", None),
            )
            if not _log_mpesa_provider_error("stk_query", payment.pk, exc):
                logger.warning(
                    "Could not verify payment %s with M-Pesa STK Push Query.",
                    payment.pk,
                    exc_info=True,
                )
            return payment

        except ValueError:
            next_verification_at = _next_verification_at(attempt_count)
            logger.warning(
                "Could not verify payment %s with M-Pesa STK Push Query.",
                payment.pk,
                exc_info=True,
            )
            return payment

        if not isinstance(response, dict):
            logger.warning(
                "M-Pesa STK Push Query returned an invalid response "
                "for payment %s.",
                payment.pk,
            )
            return payment

        response_code = response.get("ResponseCode")
        response_checkout_id = response.get("CheckoutRequestID")
        response_merchant_id = response.get("MerchantRequestID")
        result_code = response.get("ResultCode")

        if str(response_code) != "0":
            logger.warning(
                "M-Pesa STK Push Query did not confirm request acceptance "
                "for payment %s.",
                payment.pk,
            )
            return payment

        if response_checkout_id != checkout_request_id:
            logger.warning(
                "M-Pesa STK Push Query checkout ID did not match payment %s.",
                payment.pk,
            )
            return payment

        if (
            response_merchant_id is not None
            and saved_merchant_request_id
            and response_merchant_id != saved_merchant_request_id
        ):
            logger.warning(
                "M-Pesa STK Push Query merchant ID did not match payment %s.",
                payment.pk,
            )
            return payment

        result_code_text = (
            str(result_code) if result_code is not None else ""
        )

        if result_code_text == "0":
            confirmed_success = True

        elif result_code_text in MPESA_TERMINAL_FAILURE_RESULT_CODES:
            confirmed_success = False

        else:
            logger.info(
                "M-Pesa STK Push Query result for payment %s is "
                "inconclusive; payment_status=%s result_code=%s "
                "result_description=%s",
                payment.pk,
                saved_status,
                _safe_query_result_code(result_code),
                _safe_query_result_description(response.get("ResultDesc")),
            )
            return payment

        with transaction.atomic():
            locked_payment = (
                Payment.objects
                .select_for_update()
                .get(pk=payment.pk)
            )

            if locked_payment.status != saved_status:
                if locked_payment.status in {"paid", "failed"}:
                    next_verification_at = None
                return locked_payment

            if (
                locked_payment.checkout_request_id != checkout_request_id
                or locked_payment.merchant_request_id
                != saved_merchant_request_id
            ):
                if locked_payment.status in {"paid", "failed"}:
                    next_verification_at = None
                return locked_payment

            if locked_payment.status not in {"processing", "unknown"}:
                if locked_payment.status in {"paid", "failed"}:
                    next_verification_at = None
                return locked_payment

            if confirmed_success:
                next_verification_at = None
                receipt = _available_mpesa_receipt(
                    locked_payment,
                    mpesa_receipt,
                )
                result_description = (
                    response.get("ResultDesc")
                    or "Payment independently confirmed by M-Pesa."
                )

                try:
                    return mark_payment_paid(
                        locked_payment,
                        mpesa_receipt=receipt,
                        transaction_date=transaction_date,
                        result_code=result_code,
                        result_description=result_description,
                    )
                except IntegrityError:
                    logger.warning(
                        "Ignoring duplicate M-Pesa receipt for payment %s.",
                        locked_payment.pk,
                    )
                    return mark_payment_paid(
                        locked_payment,
                        mpesa_receipt=None,
                        transaction_date=transaction_date,
                        result_code=result_code,
                        result_description=result_description,
                    )

            next_verification_at = None
            return mark_payment_failed(
                locked_payment,
                result_code=result_code,
                result_description=(
                    response.get("ResultDesc")
                    or "Payment was not completed."
                ),
            )
    finally:
        _finish_payment_verification(
            payment,
            lease_id,
            next_verification_at=next_verification_at,
        )


def _stk_query_paid_success_result(payment):
    """
    Return ``(confirmed, error_code)`` for paid-success STK Push Query.

    The confirmation rules are identical to the historical paid-metadata
    verification helper: request acceptance, exact checkout ID, merchant ID
    when returned, and ResultCode 0 are all required.
    """

    checkout_request_id = payment.checkout_request_id
    saved_merchant_request_id = payment.merchant_request_id

    if not checkout_request_id:
        return False, "missing_checkout_id"

    lease_id = _claim_payment_verification(payment)

    if lease_id is None:
        return False, "cooldown"

    payment.refresh_from_db()
    attempt_count = payment.verification_attempt_count
    next_verification_at = _next_verification_at(attempt_count)

    from .integrations.mpesa import MpesaIntegration

    try:
        if not _claim_mpesa_query_slot():
            logger.info(
                "Deferring M-Pesa STK Push Query for paid payment %s "
                "metadata enrichment: shared query rate limit reached.",
                payment.pk,
            )
            return False, "cooldown"

        try:
            response = MpesaIntegration().query_stk_push(
                checkout_request_id
            )
        except requests.RequestException as exc:
            if getattr(exc, "status_code", None) == 429:
                _record_mpesa_query_throttled(
                    getattr(exc, "retry_after", None)
                )
            next_verification_at = _next_verification_at(
                attempt_count,
                retry_after=getattr(exc, "retry_after", None),
            )
            if not _log_mpesa_provider_error(
                "stk_query",
                payment.pk,
                exc,
            ):
                logger.warning(
                    "Could not independently verify paid payment %s "
                    "before metadata enrichment.",
                    payment.pk,
                    exc_info=True,
                )

            status_code = getattr(exc, "status_code", None)
            if status_code == 429:
                return False, "http_429"
            if status_code == 403:
                return False, "http_403"
            return False, "request_error"

        except ValueError:
            next_verification_at = _next_verification_at(attempt_count)
            logger.warning(
                "Could not independently verify paid payment %s "
                "before metadata enrichment.",
                payment.pk,
                exc_info=True,
            )
            return False, "invalid_response"

        if not isinstance(response, dict):
            logger.warning(
                "M-Pesa STK Push Query returned an invalid response "
                "for paid payment %s metadata enrichment.",
                payment.pk,
            )
            return False, "invalid_response"

        if str(response.get("ResponseCode")) != "0":
            logger.warning(
                "M-Pesa STK Push Query did not confirm request acceptance "
                "for paid payment %s metadata enrichment.",
                payment.pk,
            )
            return False, "request_not_accepted"

        if response.get("CheckoutRequestID") != checkout_request_id:
            logger.warning(
                "M-Pesa STK Push Query checkout ID did not match "
                "paid payment %s metadata enrichment.",
                payment.pk,
            )
            return False, "checkout_mismatch"

        response_merchant_id = response.get("MerchantRequestID")

        if (
            response_merchant_id is not None
            and saved_merchant_request_id
            and response_merchant_id != saved_merchant_request_id
        ):
            logger.warning(
                "M-Pesa STK Push Query merchant ID did not match "
                "paid payment %s metadata enrichment.",
                payment.pk,
            )
            return False, "merchant_mismatch"

        result_code = response.get("ResultCode")

        if result_code is None or str(result_code) != "0":
            logger.info(
                "M-Pesa STK Push Query did not confirm paid success for "
                "payment %s metadata enrichment; result_code=%s "
                "result_description=%s",
                payment.pk,
                _safe_query_result_code(result_code),
                _safe_query_result_description(response.get("ResultDesc")),
            )
            return False, "result_not_success"

        next_verification_at = None
        return True, None
    finally:
        _finish_payment_verification(
            payment,
            lease_id,
            next_verification_at=next_verification_at,
        )


def _stk_query_confirms_paid_success(payment):
    """
    Return True only when STK Push Query independently confirms the paid
    result for this exact payment attempt.
    """

    return _stk_query_paid_success_result(payment)[0]


def _enrich_paid_payment_metadata(
    payment,
    *,
    mpesa_receipt,
    transaction_date,
    merchant_request_id,
):
    """
    Fill missing M-Pesa metadata on an already-paid payment after a late
    callback.

    Callback data is never trusted by itself. The payment must already be
    paid, the callback merchant identifier must match the saved payment, and
    an independent STK Push Query must confirm ResultCode 0 before metadata
    is saved. This helper never changes payment status or result fields.
    """

    _validate_payment(payment)
    payment = Payment.objects.get(pk=payment.pk)

    if payment.status != "paid":
        return payment

    saved_checkout_request_id = payment.checkout_request_id
    saved_merchant_request_id = payment.merchant_request_id

    if not saved_checkout_request_id or not saved_merchant_request_id:
        return payment

    if (
        not isinstance(merchant_request_id, str)
        or not merchant_request_id
        or merchant_request_id != saved_merchant_request_id
    ):
        return payment

    needs_receipt = (
        not payment.mpesa_receipt
        and mpesa_receipt is not None
    )
    needs_transaction_date = (
        payment.transaction_date is None
        and transaction_date is not None
    )

    if not (needs_receipt or needs_transaction_date):
        return payment

    if not _stk_query_confirms_paid_success(payment):
        return payment

    with transaction.atomic():
        locked_payment = (
            Payment.objects
            .select_for_update()
            .get(pk=payment.pk)
        )

        if locked_payment.status != "paid":
            return locked_payment

        if (
            locked_payment.checkout_request_id
            != saved_checkout_request_id
            or locked_payment.merchant_request_id
            != saved_merchant_request_id
        ):
            return locked_payment

        metadata_fields = []
        original_receipt = locked_payment.mpesa_receipt
        attempted_receipt = False

        if not locked_payment.mpesa_receipt and mpesa_receipt is not None:
            receipt = _available_mpesa_receipt(
                locked_payment,
                mpesa_receipt,
            )
            if receipt is not None:
                locked_payment.mpesa_receipt = receipt
                metadata_fields.append("mpesa_receipt")
                attempted_receipt = True

        if (
            locked_payment.transaction_date is None
            and transaction_date is not None
        ):
            locked_payment.transaction_date = transaction_date
            metadata_fields.append("transaction_date")

        if not metadata_fields:
            return locked_payment

        try:
            with transaction.atomic():
                locked_payment.save(
                    update_fields=metadata_fields + ["updated_at"]
                )
        except IntegrityError:
            if not attempted_receipt:
                raise

            logger.warning(
                "Ignoring duplicate M-Pesa receipt during paid "
                "metadata enrichment for payment %s.",
                locked_payment.pk,
            )
            locked_payment.mpesa_receipt = original_receipt

            retry_fields = [
                field
                for field in metadata_fields
                if field != "mpesa_receipt"
            ]

            if retry_fields:
                with transaction.atomic():
                    locked_payment.save(
                        update_fields=retry_fields + ["updated_at"]
                    )

        return locked_payment


def _callback_metadata_rejection_reason(
    stk_callback,
    merchant_request_id,
    payment,
):
    """
    Return a safe reason code when callback metadata is not eligible.

    Returns None when the callback is eligible for metadata capture.
    The reason never includes callback identifiers or payload values.
    """

    result_code = stk_callback.get("ResultCode")

    if result_code is None:
        return "missing_result_code"

    if str(result_code) != "0":
        return "nonzero_result_code"

    if (
        not isinstance(merchant_request_id, str)
        or not merchant_request_id
    ):
        return "merchant_request_id_missing"

    if merchant_request_id != payment.merchant_request_id:
        return "merchant_request_id_mismatch"

    return None


def _callback_metadata_allowed(stk_callback, merchant_request_id, payment):
    """
    Return True only when callback metadata may be captured for this payment.

    Callback ResultCode is required to be successful for metadata capture,
    but it is never treated as proof that the payment succeeded.
    """

    return (
        _callback_metadata_rejection_reason(
            stk_callback,
            merchant_request_id,
            payment,
        )
        is None
    )


def _log_callback_metadata_rejection(payment, stk_callback, reason):
    """
    Log safe diagnostics for a rejected callback-metadata capture.

    Only the rejection reason and sanitized provider result fields are
    logged. Callback identifiers, phone numbers and raw payload values
    are never included.
    """

    logger.warning(
        "Ignoring M-Pesa callback metadata for payment %s: %s "
        "(result_code=%s result_description=%s).",
        payment.pk,
        reason,
        _safe_query_result_code(stk_callback.get("ResultCode")),
        _safe_query_result_description(stk_callback.get("ResultDesc")),
    )


MPESA_CALLBACK_RESULT_CATEGORIES = {
    "1019": "expired",
    "1032": "user_cancelled",
    "1037": "timeout",
    "1": "insufficient_balance",
}


def _callback_result_category(result_code):
    """
    Return a diagnostic category for a callback ResultCode.

    Unknown non-zero codes are categorized as "other"; categories are
    diagnostic labels only and never drive payment state changes.
    """

    return MPESA_CALLBACK_RESULT_CATEGORIES.get(
        str(result_code),
        "other",
    )


def _record_callback_result_diagnostic(payment, stk_callback):
    """
    Persist a safe diagnostic record for a non-zero callback ResultCode.

    The record is informational only: it never authorizes a payment
    state change and never stores callback identifiers or raw payload
    values. Duplicate deliveries of the same result are idempotent.
    """

    result_code = _safe_query_result_code(stk_callback.get("ResultCode"))

    MpesaCallbackResult.objects.get_or_create(
        payment=payment,
        result_code=result_code,
        defaults={
            "result_description": _safe_query_result_description(
                stk_callback.get("ResultDesc")
            ),
            "category": _callback_result_category(result_code),
        },
    )


def _store_callback_metadata(
    payment,
    *,
    mpesa_receipt,
    transaction_date,
):
    """
    Persist validated callback metadata for an eligible payment.

    Eligible payments are processing, unknown, or paid. At most one
    candidate record exists per payment. Existing pending candidates are
    updated only for fields that are still missing.
    """

    if payment.status not in {"processing", "unknown", "paid"}:
        return None

    if not payment.checkout_request_id or not payment.merchant_request_id:
        return None

    needs_receipt = (
        not payment.mpesa_receipt
        and mpesa_receipt is not None
    )
    needs_transaction_date = (
        payment.transaction_date is None
        and transaction_date is not None
    )

    if not (needs_receipt or needs_transaction_date):
        return None

    metadata, created = MpesaCallbackMetadata.objects.get_or_create(
        payment=payment,
        defaults={
            "mpesa_receipt": mpesa_receipt,
            "transaction_date": transaction_date,
            "received_at": timezone.now(),
            "status": "pending",
        },
    )

    if not created and metadata.status == "pending":
        update_fields = []

        if metadata.mpesa_receipt is None and mpesa_receipt is not None:
            metadata.mpesa_receipt = mpesa_receipt
            update_fields.append("mpesa_receipt")

        if (
            metadata.transaction_date is None
            and transaction_date is not None
        ):
            metadata.transaction_date = transaction_date
            update_fields.append("transaction_date")

        if update_fields:
            metadata.save(
                update_fields=update_fields + ["updated_at"]
            )

    if metadata.status != "pending":
        return None

    return metadata


@transaction.atomic
def _set_callback_metadata_status(metadata, status, error_code=""):
    """Set a terminal callback-metadata status under a row lock."""

    locked_metadata = MpesaCallbackMetadata.objects.select_for_update().get(
        pk=metadata.pk
    )

    if locked_metadata.status != "pending":
        return locked_metadata

    locked_metadata.status = status
    locked_metadata.last_error = error_code or ""
    locked_metadata.save(
        update_fields=["status", "last_error", "updated_at"]
    )
    return locked_metadata


@transaction.atomic
def _record_callback_metadata_attempt(
    metadata,
    *,
    confirmed,
    error_code=None,
):
    """Record a safe verification attempt for a pending metadata record."""

    locked_metadata = MpesaCallbackMetadata.objects.select_for_update().get(
        pk=metadata.pk
    )

    if locked_metadata.status != "pending":
        return locked_metadata

    locked_metadata.attempt_count += 1
    locked_metadata.last_attempt_at = timezone.now()
    locked_metadata.last_error = error_code or ""

    update_fields = [
        "attempt_count",
        "last_attempt_at",
        "last_error",
        "updated_at",
    ]

    if confirmed:
        locked_metadata.verified_at = locked_metadata.last_attempt_at
        update_fields.append("verified_at")

    locked_metadata.save(update_fields=update_fields)
    return locked_metadata


@transaction.atomic
def _apply_verified_callback_metadata(metadata, payment):
    """
    Apply independently verified callback metadata to a paid payment.

    The payment row is locked and rechecked immediately before updating.
    Existing receipt and transaction-date values are never overwritten.
    """

    locked_metadata = MpesaCallbackMetadata.objects.select_for_update().get(
        pk=metadata.pk
    )

    if locked_metadata.status != "pending":
        return Payment.objects.get(pk=payment.pk)

    locked_payment = Payment.objects.select_for_update().get(pk=payment.pk)

    if (
        locked_payment.status != "paid"
        or not locked_payment.checkout_request_id
        or not locked_payment.merchant_request_id
    ):
        locked_metadata.status = "failed"
        locked_metadata.last_error = "payment_not_eligible"
        locked_metadata.save(
            update_fields=["status", "last_error", "updated_at"]
        )
        return locked_payment

    metadata_fields = []
    original_receipt = locked_payment.mpesa_receipt
    attempted_receipt = False

    if (
        not locked_payment.mpesa_receipt
        and locked_metadata.mpesa_receipt is not None
    ):
        receipt = _available_mpesa_receipt(
            locked_payment,
            locked_metadata.mpesa_receipt,
        )
        if receipt is not None:
            locked_payment.mpesa_receipt = receipt
            metadata_fields.append("mpesa_receipt")
            attempted_receipt = True

    if (
        locked_payment.transaction_date is None
        and locked_metadata.transaction_date is not None
    ):
        locked_payment.transaction_date = locked_metadata.transaction_date
        metadata_fields.append("transaction_date")

    if metadata_fields:
        try:
            with transaction.atomic():
                locked_payment.save(
                    update_fields=metadata_fields + ["updated_at"]
                )
        except IntegrityError:
            if not attempted_receipt:
                raise

            logger.warning(
                "Ignoring duplicate M-Pesa receipt during callback "
                "metadata recovery for payment %s.",
                locked_payment.pk,
            )
            locked_payment.mpesa_receipt = original_receipt

            retry_fields = [
                field
                for field in metadata_fields
                if field != "mpesa_receipt"
            ]

            if retry_fields:
                with transaction.atomic():
                    locked_payment.save(
                        update_fields=retry_fields + ["updated_at"]
                    )

    locked_metadata.status = "applied"
    locked_metadata.last_error = ""
    locked_metadata.save(
        update_fields=["status", "last_error", "updated_at"]
    )

    return locked_payment


def _finalize_callback_metadata_after_verification(metadata, payment):
    """
    Update a pending metadata record after payment verification.

    Paid outcomes apply the candidate metadata through the existing
    safeguards. Failed outcomes discard the candidate. Non-terminal
    outcomes keep the candidate pending with a safe error code.
    """

    verification_attempted = getattr(
        payment,
        "_verification_attempted",
        True,
    )
    metadata.refresh_from_db()
    payment = Payment.objects.get(pk=payment.pk)

    if metadata.status != "pending":
        return payment

    if not verification_attempted:
        return payment

    if payment.status == "paid":
        _record_callback_metadata_attempt(metadata, confirmed=True)
        return _apply_verified_callback_metadata(metadata, payment)

    if payment.status == "failed":
        _record_callback_metadata_attempt(
            metadata,
            confirmed=False,
            error_code="result_not_success",
        )
        _set_callback_metadata_status(
            metadata,
            "failed",
            "result_not_success",
        )
        return payment

    _record_callback_metadata_attempt(
        metadata,
        confirmed=False,
        error_code="verification_unavailable",
    )
    return payment


def _verify_and_apply_callback_metadata(metadata):
    """
    Verify a pending callback-metadata record and apply it if confirmed.

    The provider query happens outside database transactions. The payment
    is rechecked under a row lock before any metadata is written.
    """

    metadata.refresh_from_db()
    payment = Payment.objects.get(pk=metadata.payment_id)

    if metadata.status != "pending":
        return payment

    if (
        payment.status not in {"processing", "unknown", "paid"}
        or not payment.checkout_request_id
        or not payment.merchant_request_id
    ):
        _set_callback_metadata_status(
            metadata,
            "failed",
            "payment_not_eligible",
        )
        return payment

    if payment.status == "paid":
        needs_receipt = (
            not payment.mpesa_receipt
            and metadata.mpesa_receipt is not None
        )
        needs_transaction_date = (
            payment.transaction_date is None
            and metadata.transaction_date is not None
        )

        if not (needs_receipt or needs_transaction_date):
            _set_callback_metadata_status(metadata, "applied")
            return payment

        confirmed, error_code = _stk_query_paid_success_result(payment)

        if error_code == "cooldown":
            return payment

        _record_callback_metadata_attempt(
            metadata,
            confirmed=confirmed,
            error_code=error_code,
        )

        if not confirmed:
            return payment

        return _apply_verified_callback_metadata(metadata, payment)

    verified_payment = verify_payment(
        payment,
        mpesa_receipt=metadata.mpesa_receipt,
        transaction_date=metadata.transaction_date,
    )
    return _finalize_callback_metadata_after_verification(
        metadata,
        verified_payment,
    )


def reconcile_callback_metadata(metadata):
    """
    Attempt verification for one pending callback-metadata record.

    Returns the refreshed metadata record so callers can report its final
    status without exposing payment identifiers.
    """

    _verify_and_apply_callback_metadata(metadata)
    metadata.refresh_from_db()
    return metadata


def process_mpesa_callback(payload):
    """
    Use a callback only as a trigger for independent payment verification.
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

    if not isinstance(checkout_request_id, str) or not checkout_request_id:
        raise ValueError("Checkout request ID is missing from callback.")

    merchant_request_id = stk_callback.get("MerchantRequestID")

    payment = (
        Payment.objects
        .filter(checkout_request_id=checkout_request_id)
        .first()
    )

    if payment is None:
        raise ValueError(
            "Payment matching the callback was not found."
        )

    metadata = _callback_metadata_map(stk_callback)
    receipt_value = metadata.get("MpesaReceiptNumber")
    transaction_value = metadata.get("TransactionDate")

    mpesa_receipt = _normalize_mpesa_receipt(receipt_value)
    transaction_date = _parse_mpesa_transaction_date(transaction_value)

    if receipt_value is not None and mpesa_receipt is None:
        logger.warning(
            "Ignoring invalid M-Pesa receipt metadata during "
            "callback verification."
        )

    if transaction_value is not None and transaction_date is None:
        logger.warning(
            "Ignoring invalid M-Pesa transaction date metadata during "
            "callback verification."
        )

    metadata_rejection_reason = _callback_metadata_rejection_reason(
        stk_callback,
        merchant_request_id,
        payment,
    )
    callback_metadata_allowed = metadata_rejection_reason is None

    if metadata_rejection_reason == "nonzero_result_code":
        _record_callback_result_diagnostic(payment, stk_callback)

    if payment.status == "paid":
        if not callback_metadata_allowed:
            _log_callback_metadata_rejection(
                payment,
                stk_callback,
                metadata_rejection_reason,
            )
            return payment

        metadata_record = _store_callback_metadata(
            payment,
            mpesa_receipt=mpesa_receipt,
            transaction_date=transaction_date,
        )

        if metadata_record is None:
            return payment

        return _verify_and_apply_callback_metadata(metadata_record)

    if not callback_metadata_allowed:
        _log_callback_metadata_rejection(
            payment,
            stk_callback,
            metadata_rejection_reason,
        )
        return verify_payment(payment)

    metadata_record = _store_callback_metadata(
        payment,
        mpesa_receipt=mpesa_receipt,
        transaction_date=transaction_date,
    )

    if metadata_record is not None:
        return _verify_and_apply_callback_metadata(metadata_record)

    return verify_payment(
        payment,
        mpesa_receipt=mpesa_receipt,
        transaction_date=transaction_date,
    )
