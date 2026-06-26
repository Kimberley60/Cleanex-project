from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import Booking, BookingExtra
from apps_core.staff.models import Staff


# -----------------------------
# PRICE CALCULATOR
# -----------------------------
def calculate_booking_price(
    service,
    property_size=None,
    extras=None,
    rooms=None
):
    """
    Calculate booking price dynamically
    """

    price = Decimal(service.base_price)

    # price per room logic
    if rooms:
        price += Decimal(rooms) * Decimal("500")

    # property size pricing
    if property_size:
        price += Decimal(property_size) * Decimal("2")

    # extras pricing
    if extras:
        for extra in extras:
            price += extra.price

    return price


# -----------------------------
# AVAILABILITY CHECK
# -----------------------------
def check_availability(date, time):
    """
    Check if staff is available at given date/time
    """

    existing = Booking.objects.filter(
        booking_date=date,
        booking_time=time,
        status__in=["pending", "confirmed", "in_progress"]
    ).count()

    available_staff = Staff.objects.filter(
        is_available=True,
        is_active=True
    ).count()

    return available_staff > existing


# -----------------------------
# AUTO STAFF ASSIGNMENT
# -----------------------------
def assign_available_staff(city=None):
    """
    Assign first available staff
    """

    staff = Staff.objects.filter(
        is_available=True,
        is_active=True
    )

    if city:
        staff = staff.filter(city=city)

    return staff.first()


# -----------------------------
# CREATE BOOKING SERVICE
# -----------------------------
@transaction.atomic
def create_booking(customer, data):
    """
    Central booking creation logic
    """

    service = data.get("service")
    extras = data.get("extras", [])

    # calculate price
    total_price = calculate_booking_price(
        service=service,
        property_size=data.get("property_size"),
        extras=extras,
        rooms=data.get("rooms")
    )

    # assign staff
    staff = assign_available_staff(data.get("city"))

    booking = Booking.objects.create(
        customer=customer,
        service=service,
        staff=staff,
        booking_date=data.get("booking_date"),
        booking_time=data.get("booking_time"),
        address=data.get("address"),
        city=data.get("city"),
        property_size=data.get("property_size"),
        rooms=data.get("rooms"),
        total_price=total_price,
        notes=data.get("notes", ""),
        status="pending",
    )

    # attach extras
    for extra in extras:
        BookingExtra.objects.create(
            booking=booking,
            extra_service=extra
        )

    return booking