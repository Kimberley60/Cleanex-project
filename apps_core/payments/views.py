import json
import logging
from django.contrib import messages
from django.http import HttpResponseNotFound, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from apps_core.bookings.models import Booking
from .services import (
    create_payment,
    get_payment_for_booking,
    initiate_payment,
    process_mpesa_callback,
)

logger = logging.getLogger(__name__)


@require_POST
def initiate_booking_payment(request, booking_id):
    """
    Start an M-Pesa payment for the booking created in this browser session.

    This endpoint is intentionally POST-only and session-bound.
    """
    session_booking_id = request.session.get("booking_success_id")

    # Do not allow a customer to initiate payment for another booking
    # by changing the booking ID in the URL.
    if session_booking_id != booking_id:
        return HttpResponseNotFound("Booking not found.")

    booking = get_object_or_404(
        Booking.objects.select_related("customer"),
        pk=booking_id,
    )

    # Prefer the phone number submitted with this booking.
    phone_number = (
        booking.contact_phone
        or getattr(booking.customer, "phone", "")
    )

    if not phone_number:
        messages.error(
            request,
            "No phone number is available for this booking. "
            "Please contact CLEANEX for assistance.",
        )
        return redirect(
            f"{reverse('booking_success')}?booking={booking.pk}"
        )

    existing_payment = get_payment_for_booking(booking)

    # Never initiate another STK Push for a payment already completed.
    if existing_payment and existing_payment.status == "paid":
        messages.info(
            request,
            "This booking has already been paid for.",
        )
        return redirect(
            f"{reverse('booking_success')}?booking={booking.pk}"
        )

    # Avoid duplicate STK Push requests while one is in progress.
    if existing_payment and existing_payment.status == "processing":
        messages.info(
            request,
            "Your M-Pesa payment request is already being processed. "
            "Please check your phone and wait for the payment result.",
        )
        return redirect(
            f"{reverse('booking_success')}?booking={booking.pk}"
        )

    try:
        # Reuse a pending payment. Create a new record after a failed
        # attempt so that payment history is preserved.
        if existing_payment and existing_payment.status == "pending":
            payment = existing_payment
        else:
            payment = create_payment(
                booking=booking,
                phone_number=phone_number,
            )

        initiate_payment(payment)

        messages.success(
            request,
            "Your M-Pesa payment request has been sent. "
            "Check your phone and follow the Safaricom prompt. "
            "Your payment will be confirmed after the callback is received.",
        )

    except ValueError as exc:
        logger.warning(
            "Could not initiate payment for booking %s: %s",
            booking.pk,
            exc,
        )
        messages.error(request, str(exc))

    except Exception:
        logger.exception(
            "Unexpected error initiating payment for booking %s.",
            booking.pk,
        )
        messages.error(
            request,
            "We couldn't start your M-Pesa payment right now. "
            "Please try again later.",
        )

    return redirect(
        f"{reverse('booking_success')}?booking={booking.pk}"
    )


@csrf_exempt
@require_POST
def mpesa_callback(request):
    """
    Receive and process Safaricom M-Pesa STK callbacks.
    """
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {
                "ResultCode": 1,
                "ResultDesc": "Invalid callback payload.",
            },
            status=400,
        )

    try:
        process_mpesa_callback(payload)

    except ValueError as exc:
        return JsonResponse(
            {
                "ResultCode": 1,
                "ResultDesc": str(exc),
            },
            status=400,
        )

    except Exception:
        logger.exception("M-Pesa callback processing failed.")
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
            "ResultDesc": "Callback processed successfully.",
        }
    )

