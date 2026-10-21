from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import Booking, BookingExtra
from apps_core.staff.services import (
    assign_available_staff,
    check_staff_availability,
    has_available_staff_in_city
)


ALLOWED_STATUS_TRANSITIONS = {
    "pending": {"confirmed", "cancelled"},
    "confirmed": {"in_progress", "cancelled"},
    "in_progress": {"completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


def calculate_booking_price(
    service,
    property_size=None,
    extras=None,
    rooms=None,
):
    """
    Calculate the total price for a booking.
    """

    if not service:
        raise ValueError(
            "A service is required to calculate the booking price."
        )

    if property_size is not None and property_size <= 0:
        raise ValueError(
            "Property size must be greater than zero."
        )

    if rooms is not None and rooms <= 0:
        raise ValueError(
            "Number of rooms must be greater than zero."
        )

    total_price = Decimal(service.base_price)

    if rooms:
        total_price += Decimal(rooms) * Decimal("500.00")

    if property_size:
        total_price += Decimal(property_size) * Decimal("2.00")

    if extras:
        for extra in extras:
            total_price += Decimal(extra.price)

    return total_price


def check_availability(
    date,
    time,
    city=None,
    duration_hours=None,
):
    """
    Check whether staff capacity exists for a booking.
    """

    return check_staff_availability(
        date=date,
        time=time,
        city=city,
        duration_hours=duration_hours,
    )

def get_availability_status(
    date,
    time,
    city=None,
    duration_hours=None,
):
    """
    Return a customer-friendly availability status.

    Possible statuses:
    - available
    - no_city_staff
    - time_unavailable
    """

    if not city:
        return "time_unavailable"

    if not has_available_staff_in_city(city):
        return "no_city_staff"

    if not check_staff_availability(
        date=date,
        time=time,
        city=city,
        duration_hours=duration_hours,
    ):
        return "time_unavailable"

    return "available"



def change_booking_status(booking, new_status):
    """
    Safely change a booking's status according to the
    allowed booking lifecycle.
    """

    if not booking:
        raise ValueError("A booking is required.")

    valid_statuses = {
        choice[0]
        for choice in Booking.STATUS_CHOICES
    }

    if new_status not in valid_statuses:
        raise ValueError(
            f"Invalid booking status: {new_status}"
        )

    current_status = booking.status

    if current_status == new_status:
        raise ValueError(
            f"Booking is already "
            f"{booking.get_status_display()}."
        )

    allowed_transitions = ALLOWED_STATUS_TRANSITIONS.get(
        current_status,
        set(),
    )

    if new_status not in allowed_transitions:
        current_label = booking.get_status_display()

        new_label = dict(
            Booking.STATUS_CHOICES
        ).get(
            new_status,
            new_status,
        )

        raise ValueError(
            f"Booking cannot move from "
            f"{current_label} to {new_label}."
        )

    booking.status = new_status

    booking.save(
        update_fields=[
            "status",
            "updated_at",
        ]
    )

    return booking


@transaction.atomic
def create_booking(customer, data):
    """
    Create a booking and its associated extras.

    Booking creation remains the responsibility of the
    bookings domain. Staff-specific logic is delegated
    to the staff service layer.
    """

    if not customer:
        raise ValueError(
            "A customer is required."
        )

    service = data.get("service")
    booking_date = data.get("booking_date")
    booking_time = data.get("booking_time")
    city = (data.get("city") or "").strip()
    address = (data.get("address") or "").strip()
    extras = list(data.get("extras") or [])

    if not service:
        raise ValueError(
            "A service is required."
        )

    if not service.is_active:
        raise ValueError(
            "The selected service is currently unavailable."
        )

    if not booking_date:
        raise ValueError(
            "A booking date is required."
        )

    if not booking_time:
        raise ValueError(
            "A booking time is required."
        )

    if booking_date < timezone.localdate():
        raise ValueError(
            "The booking date cannot be in the past."
        )

    if (
        booking_date == timezone.localdate()
        and booking_time <= timezone.localtime().time()
    ):
        raise ValueError(
            "The selected booking time has already passed."
        )

    if not city:
        raise ValueError(
            "A city is required."
        )

    if not address:
        raise ValueError(
            "A service address is required."
        )

    property_size = data.get("property_size")
    rooms = data.get("rooms")

    if property_size is not None and property_size <= 0:
        raise ValueError(
            "Property size must be greater than zero."
        )

    if rooms is not None and rooms <= 0:
        raise ValueError(
            "Number of rooms must be greater than zero."
        )

    inactive_extras = [
        extra
        for extra in extras
        if not extra.is_active
    ]

    if inactive_extras:
        raise ValueError(
            "One or more selected additional services "
            "are no longer available."
        )

    duration_hours = Decimal(
        service.duration_hours
    )

    if duration_hours <= 0:
        raise ValueError(
            "The selected service has an invalid duration."
        )

    if not check_availability(
        booking_date,
        booking_time,
        city=city,
        duration_hours=duration_hours,
    ):
        raise ValueError(
            "The selected date and time are "
            "currently unavailable."
        )

    total_price = calculate_booking_price(
        service=service,
        property_size=property_size,
        extras=extras,
        rooms=rooms,
    )

    staff = assign_available_staff(
        city=city,
        date=booking_date,
        time=booking_time,
        duration_hours=duration_hours,
    )

    if not staff:
        raise ValueError(
            "No cleaner is currently available "
            "for the selected date and time."
        )

    booking = Booking.objects.create(
        customer=customer,

        contact_name=data.get(
            "contact_name",
            customer.full_name,
        ),

        contact_email=data.get(
            "contact_email",
            customer.email,
        ),

        contact_phone=data.get(
            "contact_phone",
            customer.phone,
        ),

        service=service,
        staff=staff,
        booking_date=booking_date,
        booking_time=booking_time,
        address=address,
        city=city,
        property_size=property_size or None,
        rooms=rooms or None,
        total_price=total_price,
        notes=data.get("notes", ""),
        status="pending",
    )

    BookingExtra.objects.bulk_create(
        [
            BookingExtra(
                booking=booking,
                extra_service=extra,
            )
            for extra in extras
        ]
    )

    return booking

