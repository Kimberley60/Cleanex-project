from django.db import transaction
from .models import Customer



@transaction.atomic
def get_or_create_customer(
    *,
    full_name,
    email,
    phone,
    address="",
    city="",
):
    """
    Find an existing customer by email or create a new one.

    Existing customer records are not overwritten by a public
    booking submission. Booking-specific contact information is
    stored on the Booking record instead.
    """

    if not email:
        raise ValueError(
            "Customer email is required."
        )

    email = email.strip().lower()

    customer = (
        Customer.objects
        .filter(email__iexact=email)
        .first()
    )

    if customer:
        return customer, False

    customer = Customer.objects.create(
        full_name=full_name,
        email=email,
        phone=phone,
        address=address,
        city=city,
    )

    return customer, True


def get_customer_by_email(email):
    if not email:
        return None

    return (
        Customer.objects
        .filter(email__iexact=email.strip())
        .first()
    )


def get_customer_booking_count(customer):
    if not customer:
        return 0

    return customer.bookings.count()


def is_repeat_customer(customer):
    return get_customer_booking_count(customer) > 1


def update_repeat_customer_status(customer):
    """
    Synchronize the stored repeat-customer flag
    with the customer's booking history.
    """

    if not customer:
        return None

    repeat_status = get_customer_booking_count(customer) > 1

    if customer.is_repeat_customer != repeat_status:
        customer.is_repeat_customer = repeat_status

        customer.save(
            update_fields=[
                "is_repeat_customer",
            ]
        )

    return customer