from datetime import datetime, timedelta
from decimal import Decimal
from django.db import transaction
from apps_core.bookings.models import Booking
from .models import Staff


ACTIVE_BOOKING_STATUSES = (
    "pending",
    "confirmed",
    "in_progress",
)


def get_available_staff(city=None):
    """
    Return active staff members who are currently available.

    When a city is provided, only staff assigned to that city
    are considered.
    """

    staff = Staff.objects.filter(
        is_available=True,
        is_active=True,
    )

    if city:
        staff = staff.filter(
            city__iexact=city.strip()
        )

    return staff

def has_available_staff_in_city(city):
    """
    Check whether CLEANEX currently has at least one
    active and available staff member serving the city.
    """
    if not city:
        return False

    return get_available_staff(city).exists()



def _booking_time_window(booking):
    """
    Return the start and end time for an existing booking.
    """

    start = datetime.combine(
        booking.booking_date,
        booking.booking_time,
    )

    duration_hours = Decimal(
        booking.service.duration_hours
        if booking.service
        else 1
    )

    end = start + timedelta(
        hours=float(duration_hours)
    )

    return start, end


def _time_windows_overlap(
    start_a,
    end_a,
    start_b,
    end_b,
):
    return (
        start_a < end_b
        and start_b < end_a
    )


def _staff_has_conflicting_booking(
    staff,
    date,
    time,
    duration_hours,
):
    """
    Determine whether a staff member already has an
    overlapping active booking.
    """

    requested_start = datetime.combine(
        date,
        time,
    )

    requested_end = requested_start + timedelta(
        hours=float(duration_hours)
    )

    bookings = (
        Booking.objects
        .select_related("service")
        .filter(
            staff=staff,
            booking_date=date,
            status__in=ACTIVE_BOOKING_STATUSES,
        )
    )

    for booking in bookings:
        existing_start, existing_end = (
            _booking_time_window(booking)
        )

        if _time_windows_overlap(
            requested_start,
            requested_end,
            existing_start,
            existing_end,
        ):
            return True

    return False


def count_available_staff(
    city=None,
    date=None,
    time=None,
    duration_hours=None,
):
    """
    Count staff who are available for the requested
    date/time window.
    """

    staff = get_available_staff(city)

    if not date or not time:
        return staff.count()

    duration_hours = Decimal(
        duration_hours or 1
    )

    count = 0

    for member in staff:
        if not _staff_has_conflicting_booking(
            member,
            date,
            time,
            duration_hours,
        ):
            count += 1

    return count


def check_staff_availability(
    date,
    time,
    city=None,
    duration_hours=None,
):
    """
    Determine whether at least one staff member is available
    for the requested date/time window.
    """

    return count_available_staff(
        city=city,
        date=date,
        time=time,
        duration_hours=duration_hours,
    ) > 0


@transaction.atomic
def assign_available_staff(
    city=None,
    date=None,
    time=None,
    duration_hours=None,
):
    """
    Safely assign the first staff member who is available
    for the requested city and time window.

    Staff rows are locked while the assignment decision is made
    to reduce concurrent double-booking.
    """

    staff_queryset = (
        get_available_staff(city)
        .select_for_update()
    )

    if not date or not time:
        return staff_queryset.first()

    duration_hours = Decimal(
        duration_hours or 1
    )

    for staff in staff_queryset:
        if not _staff_has_conflicting_booking(
            staff,
            date,
            time,
            duration_hours,
        ):
            return staff

    return None