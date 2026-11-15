import json
import logging
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseNotFound, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from apps_core.bookings.models import Booking
from apps_core.core.rate_limit import (
    ip_booking_scope,
    ip_scope,
    rate_limited,
    user_scope,
)
from .models import MpesaCallbackMetadata, Payment
from .services import (
    create_payment,
    get_payment_for_booking,
    initiate_payment,
    process_mpesa_callback,
    reconcile_callback_metadata,
    verify_payment,
)

logger = logging.getLogger(__name__)

PAYMENT_STATUS_RATE_LIMITS = (
    ip_scope("payment_status", 10, 3600),
)

PAYMENT_INITIATION_RATE_LIMITS = (
    ip_scope("payment_initiation", 10, 3600),
    user_scope("payment_initiation", 10, 86400),
    ip_booking_scope("payment_initiation", 10, 3600, "booking_id"),
)


def _booking_success_redirect(booking):
    """Return the booking success URL."""
    return f"{reverse('booking_success')}?booking={booking.pk}"


@require_POST
@rate_limited(*PAYMENT_INITIATION_RATE_LIMITS)
def initiate_booking_payment(request, booking_id):
    """
    Start an M-Pesa payment for a booking created in this browser session.

    This endpoint is POST-only and session-bound. It blocks duplicate
    requests for payments that are already paid, initiating, processing,
    or awaiting reconciliation.
    """
    session_booking_id = request.session.get("booking_success_id")

    # Do not allow payment initiation for another booking by changing
    # the booking ID in the URL.
    if session_booking_id != booking_id:
        return HttpResponseNotFound("Booking not found.")

    booking = get_object_or_404(
        Booking.objects.select_related("customer"),
        pk=booking_id,
    )

    success_url = _booking_success_redirect(booking)

    if booking.status == "cancelled":
        messages.error(
            request,
            "This booking has been cancelled. Payment cannot be initiated.",
        )
        return redirect(success_url)

    phone_number = (
        getattr(booking, "contact_phone", "")
        or getattr(booking.customer, "phone", "")
    )

    if not phone_number:
        messages.error(
            request,
            "No phone number is available for this booking. "
            "Please contact CLEANEX for assistance.",
        )
        return redirect(success_url)

    try:
        # Lock the booking while deciding whether a new attempt can start.
        # This reduces the chance of two simultaneous submissions creating
        # separate payment attempts for the same booking.
        with transaction.atomic():
            locked_booking = Booking.objects.select_for_update().get(
                pk=booking.pk
            )

            if locked_booking.status == "cancelled":
                messages.error(
                    request,
                    "This booking has been cancelled. Payment cannot be initiated.",
                )
                return redirect(success_url)

            existing_payment = (
                Payment.objects.select_for_update()
                .filter(booking=locked_booking)
                .order_by("-created_at", "-pk")
                .first()
            )

            if existing_payment:
                if existing_payment.status == "paid":
                    messages.info(
                        request,
                        "This booking has already been paid for.",
                    )
                    return redirect(success_url)

                if existing_payment.status == "processing":
                    messages.info(
                        request,
                        "Your M-Pesa payment request is already being "
                        "processed. Please check your phone and wait "
                        "for the payment result.",
                    )
                    return redirect(success_url)

                if existing_payment.status == "initiating":
                    messages.warning(
                        request,
                        "Your payment request is being submitted. "
                        "Please wait before taking further action.",
                    )
                    return redirect(success_url)

                if existing_payment.status == "unknown":
                    messages.warning(
                        request,
                        "We could not confirm the result of your previous "
                        "payment request. Please contact CLEANEX support "
                        "to verify the transaction before trying again.",
                    )
                    return redirect(success_url)

                if existing_payment.status == "pending":
                    payment = existing_payment

                elif existing_payment.status == "failed":
                    # Preserve the failed record and create a separate
                    # record for this new attempt.
                    payment = create_payment(
                        booking=locked_booking,
                        phone_number=phone_number,
                    )

                else:
                    logger.error(
                        "Unexpected payment status %r for payment %s "
                        "on booking %s.",
                        existing_payment.status,
                        existing_payment.pk,
                        locked_booking.pk,
                    )
                    messages.error(
                        request,
                        "We couldn't verify your payment status. "
                        "Please contact CLEANEX support before retrying.",
                    )
                    return redirect(success_url)

            else:
                payment = create_payment(
                    booking=locked_booking,
                    phone_number=phone_number,
                )

        # Do not hold the booking database lock while making a network
        # request to Safaricom. The payment service must persist an
        # initiating state before sending the STK request and must mark
        # ambiguous outcomes as unknown.
        initiate_payment(payment)

        messages.success(
            request,
            "Your M-Pesa payment request has been sent. Check your phone "
            "and follow the Safaricom prompt. Your payment will be "
            "confirmed after the callback is received.",
        )

    except ValueError as exc:
        logger.warning(
            "Payment initiation validation/rejection for booking %s: %s",
            booking.pk,
            exc,
        )

        # Do not expose arbitrary service/provider errors to the customer.
        # The service must persist the correct payment state before raising.
        messages.error(
            request,
            "We couldn't complete the payment request. Please check the "
            "payment status below. If the outcome is uncertain, contact "
            "CLEANEX support before trying again.",
        )

    except Exception:
        logger.exception(
            "Unexpected error initiating payment for booking %s.",
            booking.pk,
        )
        messages.error(
            request,
            "We couldn't confirm the outcome of your payment request. "
            "Please check the payment status below and contact CLEANEX "
            "support before retrying.",
        )

    return redirect(success_url)


@require_POST
@rate_limited(*PAYMENT_STATUS_RATE_LIMITS)
def check_booking_payment_status(request, booking_id):
    """
    Check the latest M-Pesa payment status without starting a new payment.
    """
    session_booking_id = request.session.get("booking_success_id")

    if session_booking_id != booking_id:
        return HttpResponseNotFound("Booking not found.")

    booking = get_object_or_404(
        Booking.objects.select_related("customer"),
        pk=booking_id,
    )

    success_url = _booking_success_redirect(booking)
    payment = get_payment_for_booking(booking)

    if payment is None:
        messages.info(
            request,
            "No M-Pesa payment attempt has been recorded for this booking yet.",
        )
        return redirect(success_url)

    checkout_request_id = payment.checkout_request_id
    has_checkout_id = (
        isinstance(checkout_request_id, str)
        and bool(checkout_request_id.strip())
    )
    verification_failed = False

    if payment.status in {"processing", "unknown"} and has_checkout_id:
        pending_metadata = MpesaCallbackMetadata.objects.filter(
            payment=payment,
            status="pending",
        ).first()

        try:
            if pending_metadata is not None:
                reconcile_callback_metadata(pending_metadata)
            else:
                verify_payment(payment)
        except Exception:
            verification_failed = True
            logger.exception(
                "Unexpected error checking M-Pesa status for booking %s.",
                booking.pk,
            )

        payment = get_payment_for_booking(booking)

        if payment is None:
            messages.error(
                request,
                "We couldn't verify your payment status. "
                "Please contact CLEANEX support.",
            )
            return redirect(success_url)

    if (
        verification_failed
        and payment.status in {"processing", "unknown"}
    ):
        messages.warning(
            request,
            "We could not check the M-Pesa payment status right now. "
            "Please try again later; do not start another payment yet.",
        )
        return redirect(success_url)

    if payment.status == "paid":
        messages.success(
            request,
            "Your M-Pesa payment has been confirmed.",
        )

    elif payment.status == "failed":
        messages.warning(
            request,
            "Your previous M-Pesa payment attempt was unsuccessful. "
            "You can try again below.",
        )

    elif payment.status == "processing":
        if has_checkout_id:
            messages.info(
                request,
                "Your M-Pesa payment is still being processed, or "
                "verification is temporarily unavailable. Please wait "
                "for the result; do not start another payment while "
                "the outcome is unresolved.",
            )
        else:
            messages.warning(
                request,
                "We cannot check this payment status right now because "
                "the M-Pesa reference is missing. Please contact "
                "CLEANEX support.",
            )

    elif payment.status == "unknown":
        if has_checkout_id:
            messages.warning(
                request,
                "We could not confirm the M-Pesa payment outcome yet. "
                "Verification may be temporarily unavailable; do not "
                "start another payment while the outcome is unresolved. "
                "Please check again later or contact CLEANEX support.",
            )
        else:
            messages.error(
                request,
                "We cannot check this payment status right now because "
                "the M-Pesa reference is missing. Please contact "
                "CLEANEX support.",
            )

    elif payment.status == "initiating":
        messages.info(
            request,
            "Your M-Pesa payment request is being submitted. "
            "Please wait before taking further action.",
        )

    elif payment.status == "pending":
        messages.info(
            request,
            "No M-Pesa payment request is currently awaiting confirmation. "
            "You can start a payment below.",
        )

    else:
        logger.error(
            "Unexpected payment status %r for payment %s on booking %s.",
            payment.status,
            payment.pk,
            booking.pk,
        )
        messages.error(
            request,
            "We couldn't verify your payment status. "
            "Please contact CLEANEX support.",
        )

    return redirect(success_url)


@csrf_exempt
@require_POST
def mpesa_callback(request):
    """
    Receive and process Safaricom M-Pesa STK callbacks.

    Callback processing must be idempotent. The payment service should
    match callbacks using CheckoutRequestID and avoid changing a completed
    payment when duplicate callbacks arrive.
    """
    try:
        payload = json.loads(request.body.decode("utf-8"))

        if not isinstance(payload, dict):
            raise ValueError("Callback payload must be a JSON object.")

    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        logger.warning("Received an invalid M-Pesa callback payload.")
        return JsonResponse(
            {
                "ResultCode": 1,
                "ResultDesc": "Invalid callback payload.",
            },
            status=400,
        )

    try:
        process_mpesa_callback(payload)

    except ValueError:
        # Log the details internally. Do not return internal payment
        # identifiers or processing details to the external caller.
        logger.warning(
            "M-Pesa callback could not be processed because its data "
            "was invalid or no matching payment was found.",
            exc_info=True,
        )
        return JsonResponse(
            {
                "ResultCode": 1,
                "ResultDesc": "Callback could not be matched or processed.",
            },
            status=400,
        )

    except Exception:
        logger.exception("Unexpected error processing M-Pesa callback.")
        return JsonResponse(
            {
                "ResultCode": 1,
                "ResultDesc": "Callback processing failed.",
            },
            status=500,
        )

    return JsonResponse(
        {
            "ResultCode": 0,
            "ResultDesc": "Callback received for verification.",
        },
        status=200,
    )
