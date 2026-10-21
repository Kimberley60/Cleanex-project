from django.db import transaction
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views import View
from .forms import BookingTrackingForm, BookingForm
from .models import Booking
from django.core.cache import cache
from .services import create_booking
from ..customers.services import get_or_create_customer, update_repeat_customer_status


# BookingCreateView

class BookingCreateView(View):
    """
    Display and process the public booking form.
    """

    template_name = "components/booking_form.html"

    def get_selected_service(self, request):
        """
        Get the service passed from a service detail CTA.

        Expected URL:
            /booking/book/?service=home-cleaning
        """

        service_slug = request.GET.get("service")

        if not service_slug:
            return None

        from apps_core.services.models import Service

        return Service.objects.filter(
            slug=service_slug,
            is_active=True,
        ).first()

    def get(self, request):
        selected_service = self.get_selected_service(request)

        form = BookingForm(
            selected_service=selected_service
        )

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "selected_service": selected_service,
            },
        )

    def post(self, request):
        selected_service = self.get_selected_service(request)

        form = BookingForm(
            request.POST,
            selected_service=selected_service,
        )

        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "selected_service": selected_service,
                },
            )

        cleaned_data = form.cleaned_data

        try:
            with transaction.atomic():
                customer, created = get_or_create_customer(
                    full_name=cleaned_data["full_name"],
                    email=cleaned_data["email"],
                    phone=cleaned_data["phone"],
                    address=cleaned_data["address"],
                    city=cleaned_data["city"],
                )

                booking_data = {
                    "service": cleaned_data["service"],
                    "booking_date": cleaned_data["booking_date"],
                    "booking_time": cleaned_data["booking_time"],
                    "address": cleaned_data["address"],
                    "city": cleaned_data["city"],
                    "property_size": cleaned_data["property_size"],
                    "rooms": cleaned_data["rooms"],
                    "extras": cleaned_data["extras"],
                    "notes": cleaned_data["notes"],
                    "contact_name": cleaned_data["full_name"],
                    "contact_email": cleaned_data["email"],
                    "contact_phone": cleaned_data["phone"],
                }

                booking = create_booking(
                    customer,
                    booking_data,
                )

                update_repeat_customer_status(customer)

        except ValueError as error:
            form.add_error(
                None,
                str(error),
            )

            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "selected_service": selected_service,
                },
            )

        # Bind the success page to the browser session that
        # created the booking.
        request.session["booking_success_id"] = booking.pk

        return redirect(
            f"{reverse('booking_success')}?booking={booking.pk}"
        )





#booking success view

class BookingSuccessView(View):
    """
    Display booking confirmation only to the browser session
    that created the booking.
    """

    def get(self, request):
        booking_id = request.GET.get("booking")

        if not booking_id:
            return render(
                request,
                "pages/booking_success.html",
                {
                    "booking": None,
                    "booking_id": None,
                },
            )

        try:
            booking_id = int(booking_id)
        except (TypeError, ValueError):
            return render(
                request,
                "pages/booking_success.html",
                {
                    "booking": None,
                    "booking_id": None,
                },
            )

        session_booking_id = request.session.get(
            "booking_success_id"
        )

        if session_booking_id != booking_id:
            return render(
                request,
                "pages/booking_success.html",
                {
                    "booking": None,
                    "booking_id": None,
                },
                status=404,
            )

        booking = (
            Booking.objects
            .select_related(
                "customer",
                "service",
                "staff",
            )
            .prefetch_related(
                "extras__extra_service"
            )
            .filter(pk=booking_id)
            .first()
        )

        if not booking:
            return render(
                request,
                "pages/booking_success.html",
                {
                    "booking": None,
                    "booking_id": None,
                },
                status=404,
            )

        return render(
            request,
            "pages/booking_success.html",
            {
                "booking": booking,
                "booking_id": booking_id,
            },
        )




#booking track view

class BookingTrackView(View):
    """
    Display and process the public booking tracking interface.
    """

    template_name = "pages/booking_track.html"

    STATUS_ORDER = [
        "pending",
        "confirmed",
        "in_progress",
        "completed",
    ]

    STATUS_LABELS = {
        "pending": "Booking Received",
        "confirmed": "Confirmed",
        "in_progress": "Cleaning In Progress",
        "completed": "Completed",
    }

    MAX_TRACK_ATTEMPTS = 5
    TRACK_ATTEMPT_TIMEOUT = 600

    def get(self, request):
        form = BookingTrackingForm()

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "booking": None,
                "timeline": [],
            },
        )

    def post(self, request):
        form = BookingTrackingForm(request.POST)

        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "booking": None,
                    "timeline": [],
                },
            )

        reference = form.cleaned_data[
            "booking_reference"
        ]

        email = form.cleaned_data[
            "email"
        ].strip().lower()

        ip_address = request.META.get(
            "REMOTE_ADDR",
            "unknown",
        )

        cache_key = (
            f"booking-track:"
            f"{ip_address}:"
            f"{reference}:"
            f"{email}"
        )

        attempt_count = cache.get(
            cache_key,
            0,
        )

        if attempt_count >= self.MAX_TRACK_ATTEMPTS:
            form.add_error(
                None,
                "Too many tracking attempts. "
                "Please try again later.",
            )

            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "booking": None,
                    "timeline": [],
                },
                status=429,
            )

        cache.set(
            cache_key,
            attempt_count + 1,
            self.TRACK_ATTEMPT_TIMEOUT,
        )

        booking_id = int(reference[4:])

        booking = (
            Booking.objects
            .select_related(
                "customer",
                "service",
                "staff",
            )
            .prefetch_related(
                "extras__extra_service"
            )
            .filter(
                pk=booking_id,
                customer__email__iexact=email,
            )
            .first()
        )

        if not booking:
            form.add_error(
                None,
                "We couldn't find a booking matching "
                "that reference and email address.",
            )

            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "booking": None,
                    "timeline": [],
                },
            )

        timeline = []

        current_status = booking.status

        if current_status == "cancelled":
            timeline = [
                {
                    "key": "pending",
                    "label": "Booking Received",
                    "state": "completed",
                },
                {
                    "key": "cancelled",
                    "label": "Booking Cancelled",
                    "state": "cancelled",
                },
            ]

        else:
            current_index = self.STATUS_ORDER.index(
                current_status
            )

            for index, status in enumerate(
                self.STATUS_ORDER
            ):
                if index < current_index:
                    state = "completed"

                elif index == current_index:
                    state = "current"

                else:
                    state = "upcoming"

                timeline.append(
                    {
                        "key": status,
                        "label": self.STATUS_LABELS[status],
                        "state": state,
                    }
                )

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "booking": booking,
                "timeline": timeline,
            },
        )

