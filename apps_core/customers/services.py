from hashlib import sha256
from django.db.models import OuterRef, Subquery
from django.db import IntegrityError, transaction
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from apps_core.bookings.models import Booking
from apps_core.payments.models import Payment
from apps_core.quotes.models import QuoteRequest
from .models import (
    Customer,
    PendingCustomerRegistration,
    normalize_customer_email,
    normalized_customer_email_expression,
)


class CustomerAccountError(ValueError):
    pass


class InvalidVerificationToken(CustomerAccountError):
    pass


class AmbiguousCustomerMatch(CustomerAccountError):
    pass


User = get_user_model()


def _customers_matching_email(email):
    return Customer.objects.alias(
        normalized_email=normalized_customer_email_expression()
    ).filter(
        normalized_email=email
    )


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

    email = normalize_customer_email(email or "")
    if not email:
        raise ValueError(
            "Customer email is required."
        )

    customer = _customers_matching_email(email).first()

    if customer:
        return customer, False

    try:
        with transaction.atomic():
            customer = Customer.objects.create(
                full_name=full_name,
                email=email,
                phone=phone,
                address=address,
                city=city,
            )
    except IntegrityError:
        customer = _customers_matching_email(email).first()
        if customer is None:
            raise
        return customer, False

    return customer, True


@transaction.atomic
def create_pending_customer_registration(
    *,
    first_name,
    last_name,
    email,
    phone,
    password,
):
    email = normalize_customer_email(email)

    if User.objects.filter(email__iexact=email).exists():
        raise CustomerAccountError(
            "An account with this email address already exists."
        )

    username = "customer-" + sha256(email.encode("utf-8")).hexdigest()
    user = User.objects.create_user(
        username=username,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        email=email,
        password=password,
        is_active=False,
    )

    PendingCustomerRegistration.objects.create(
        user=user,
        phone=phone.strip(),
    )

    return user


@transaction.atomic
def verify_customer_account(user, token):
    user = User.objects.select_for_update().get(pk=user.pk)

    if not default_token_generator.check_token(user, token):
        raise InvalidVerificationToken(
            "This verification link is invalid or has expired."
        )

    if user.is_active:
        customer = Customer.objects.filter(user=user).first()
        if customer:
            return customer
        raise CustomerAccountError(
            "This account does not have a customer profile."
        )

    try:
        registration = (
            PendingCustomerRegistration.objects
            .select_for_update()
            .get(user=user)
        )
    except PendingCustomerRegistration.DoesNotExist as error:
        raise InvalidVerificationToken(
            "This registration is no longer available."
        ) from error

    matching_customers = list(
        _customers_matching_email(
            normalize_customer_email(user.email or "")
        )
        .select_for_update()
        .order_by("pk")
    )

    if len(matching_customers) > 1:
        raise AmbiguousCustomerMatch(
            "We could not safely match your account to a customer record. "
            "Please contact CLEANEX support."
        )

    if matching_customers:
        customer = matching_customers[0]
    else:
        try:
            with transaction.atomic():
                customer = Customer.objects.create(
                    user=user,
                    full_name=f"{user.first_name} {user.last_name}".strip(),
                    email=normalize_customer_email(user.email or ""),
                    phone=registration.phone,
                    address="",
                    city="",
                )
        except IntegrityError:
            matching_customers = list(
                _customers_matching_email(
                    normalize_customer_email(user.email or "")
                )
                .select_for_update()
                .order_by("pk")
            )
            if not matching_customers:
                raise
            if len(matching_customers) > 1:
                raise AmbiguousCustomerMatch(
                    "We could not safely match your account to a customer "
                    "record. Please contact CLEANEX support."
                )
            customer = matching_customers[0]

    if customer.user_id not in (None, user.pk):
        raise CustomerAccountError(
            "This customer record is already linked to another account. "
            "Please contact CLEANEX support."
        )
    if customer.user_id is None:
        customer.user = user
        customer.save(update_fields=["user", "email"])

    user.is_active = True
    user.save(update_fields=["is_active"])
    registration.delete()

    from apps_core.quotes.services import link_guest_quote_requests

    link_guest_quote_requests(customer=customer, user=user)

    return customer


@transaction.atomic
def update_customer_profile(*, user, customer, data):
    if customer.user_id != user.pk:
        raise CustomerAccountError(
            "You can only update your own customer profile."
        )

    user.first_name = data["first_name"].strip()
    user.last_name = data["last_name"].strip()
    customer.full_name = f"{user.first_name} {user.last_name}".strip()
    customer.phone = data["phone"].strip()
    customer.county = data["county"].strip()
    customer.city = data["city"].strip()
    customer.address = data["address"].strip()

    user.save(update_fields=["first_name", "last_name"])
    customer.save(
        update_fields=[
            "full_name",
            "phone",
            "county",
            "city",
            "address",
        ]
    )

    return customer


def get_customer_by_email(email):
    email = normalize_customer_email(email or "")
    if not email:
        return None

    return _customers_matching_email(email).first()


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


def get_customer_dashboard_data(*, user, customer, recent_limit=5):
    """Return a safe, customer-scoped summary for the account dashboard."""
    if (
        user is None
        or not isinstance(user, User)
        or user.pk is None
        or user._state.adding
        or not getattr(user, "is_authenticated", False)
        or not User.objects.filter(pk=user.pk, is_active=True).exists()
    ):
        raise CustomerAccountError(
            "An authenticated, saved user is required."
        )
    if (
        not isinstance(customer, Customer)
        or customer.pk is None
        or customer._state.adding
    ):
        raise CustomerAccountError(
            "A saved customer profile is required."
        )
    if not Customer.objects.filter(pk=customer.pk, user_id=user.pk).exists():
        raise CustomerAccountError(
            "The customer profile is not linked to the supplied user."
        )
    if isinstance(recent_limit, bool) or not isinstance(recent_limit, int):
        raise ValueError("The recent item limit must be a positive integer.")
    if recent_limit <= 0:
        raise ValueError("The recent item limit must be a positive integer.")

    latest_payment_status = Subquery(
        Payment.objects
        .filter(booking_id=OuterRef("pk"))
        .order_by("-created_at", "-pk")
        .values("status")[:1]
    )
    booking_queryset = (
        Booking.objects
        .filter(created_by=user)
        .select_related("service")
        .annotate(latest_payment_status=latest_payment_status)
        .order_by("-created_at", "-pk")
    )
    booking_count = booking_queryset.count()
    recent_bookings = []
    payment_labels = dict(Payment.STATUS_CHOICES)
    for booking in booking_queryset[:recent_limit]:
        payment_status = booking.latest_payment_status
        recent_bookings.append(
            {
                "reference": booking.booking_reference,
                "service_name": (
                    booking.service.name
                    if booking.service_id
                    else "Service unavailable"
                ),
                "booking_date": booking.booking_date,
                "booking_time": booking.booking_time,
                "status": booking.status,
                "status_display": booking.get_status_display(),
                "created_at": booking.created_at,
                "payment_status": payment_status,
                "payment_status_display": (
                    payment_labels.get(payment_status, payment_status)
                    if payment_status
                    else None
                ),
            }
        )

    quote_queryset = (
        customer.quote_requests
        .select_related("service")
        .order_by("-created_at", "-pk")
    )
    quote_count = quote_queryset.count()
    recent_quotations = []
    for quote in quote_queryset[:recent_limit]:
        is_sent = quote.status == "sent"
        recent_quotations.append(
            {
                "reference": f"Q-{quote.pk}",
                "status": quote.status,
                "status_display": quote.get_status_display(),
                "service_name": (
                    quote.service.name
                    if quote.service_id
                    else "Service unavailable"
                ),
                "created_at": quote.created_at,
                "proposed_amount": quote.proposed_amount if is_sent else None,
                "quote_valid_until": (
                    quote.quote_valid_until if is_sent else None
                ),
            }
        )

    return {
        "booking_count": booking_count,
        "recent_bookings": recent_bookings,
        "quotation_count": quote_count,
        "recent_quotations": recent_quotations,
    }