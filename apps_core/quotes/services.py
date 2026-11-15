import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from apps_core.customers.models import Customer
from apps_core.notifications.models import QuoteStatusNotification
from apps_core.notifications.services import deliver_quote_status_notification

from .models import QuoteRequest


logger = logging.getLogger(__name__)


MAX_PROPERTY_SIZE = 100_000
MAX_ROOM_COUNT = 100
MAX_BATHROOM_COUNT = 100
MAX_PROPOSED_AMOUNT = Decimal("99999999.99")

ALLOWED_STATUS_TRANSITIONS = {
    "pending": {"under_review", "cancelled"},
    "under_review": {"prepared", "cancelled"},
    "prepared": {"sent", "cancelled"},
    "sent": {"accepted", "declined", "expired", "cancelled"},
    "accepted": set(),
    "declined": set(),
    "expired": set(),
    "cancelled": set(),
}


def _validate_positive_integer(value, field_name, maximum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            {field_name: "Enter a whole number."}
        )
    if value <= 0 or value > maximum:
        raise ValidationError(
            {field_name: f"Enter a value between 1 and {maximum}."}
        )


def calculate_quote(
    service,
    property_size,
    rooms,
    bathrooms,
    extras=None,
):
    """Return a preliminary estimate, not a staff-approved quotation."""
    if service is None:
        raise ValidationError(
            {"service": "Select a cleaning service."}
        )
    if not service.is_active:
        raise ValidationError(
            {"service": "Select an active cleaning service."}
        )

    _validate_positive_integer(
        property_size,
        "property_size",
        MAX_PROPERTY_SIZE,
    )
    _validate_positive_integer(rooms, "rooms", MAX_ROOM_COUNT)
    _validate_positive_integer(
        bathrooms,
        "bathrooms",
        MAX_BATHROOM_COUNT,
    )

    try:
        base_price = Decimal(service.base_price)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValidationError(
            {"service": "The selected service has an invalid base price."}
        ) from error

    if not base_price.is_finite() or base_price < 0:
        raise ValidationError(
            {"service": "The selected service has an invalid base price."}
        )

    price = base_price
    price += Decimal(rooms) * Decimal("400.00")
    price += Decimal(bathrooms) * Decimal("300.00")
    price += Decimal(property_size) * Decimal("1.50")

    # This preserves the existing rough-estimate allowance. It does not
    # represent validation or pricing of a specific extra service.
    if extras and str(extras).strip():
        price += Decimal("1000.00")

    return price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@transaction.atomic
def create_quote_request(data, user=None):
    """Persist validated customer data with a separate preliminary estimate."""
    quote_fields = (
        "full_name",
        "email",
        "phone",
        "service",
        "property_size",
        "rooms",
        "bathrooms",
        "extras",
        "address",
        "city",
        "preferred_date",
        "preferred_time",
    )
    missing_fields = [
        field for field in quote_fields if field not in data
    ]
    if missing_fields:
        raise ValidationError(
            {
                field: "This field is required."
                for field in missing_fields
            }
        )

    estimate = calculate_quote(
        service=data["service"],
        property_size=data["property_size"],
        rooms=data["rooms"],
        bathrooms=data["bathrooms"],
        extras=data["extras"],
    )

    customer = None
    if user is not None and getattr(user, "is_authenticated", False):
        customer = Customer.objects.filter(user=user).first()

    return QuoteRequest.objects.create(
        **{field: data[field] for field in quote_fields},
        customer=customer,
        estimated_price=estimate,
        status="pending",
    )


@transaction.atomic
def link_guest_quote_requests(*, customer, user):
    """Link matching unassociated quote requests after email verification."""
    if customer is None or customer.pk is None:
        raise ValidationError("A saved customer profile is required.")
    if user is None or user.pk is None:
        raise ValidationError("A saved verified user is required.")

    locked_user = (
        user.__class__.objects
        .select_for_update()
        .get(pk=user.pk)
    )
    if not locked_user.is_active:
        raise ValidationError(
            "The user must have a verified, active account."
        )

    locked_customer = (
        Customer.objects
        .select_for_update()
        .get(pk=customer.pk)
    )
    if locked_customer.user_id != locked_user.pk:
        raise ValidationError(
            "The customer profile is not linked to the verified user."
        )

    verified_email = locked_user.email.strip()
    if not verified_email:
        raise ValidationError(
            "The verified user must have an email address."
        )

    guest_quotes = list(
        QuoteRequest.objects
        .select_for_update()
        .filter(
            customer__isnull=True,
            email__iexact=verified_email,
        )
        .order_by("pk")
    )
    for quote in guest_quotes:
        quote.customer = locked_customer
        quote.save(update_fields=["customer", "updated_at"])

    return len(guest_quotes)


def _valid_prepared_quotation(quote, today):
    return (
        quote.proposed_amount is not None
        and quote.proposed_amount > 0
        and quote.quote_valid_until is not None
        and quote.quote_valid_until > today
        and quote.quote_prepared_at is not None
    )


QUOTE_STATUS_EMAILS = {
    "sent": (
        "Your CLEANEX quotation is ready for review",
        "emails/quote_sent.txt",
        "emails/quote_sent.html",
    ),
    "declined": (
        "Your CLEANEX quotation has been declined",
        "emails/quote_declined.txt",
        "emails/quote_declined.html",
    ),
    "expired": (
        "Your CLEANEX quotation has expired",
        "emails/quote_expired.txt",
        "emails/quote_expired.html",
    ),
}


@transaction.atomic
def change_quote_status(quote, new_status):
    if quote is None or quote.pk is None:
        raise ValidationError("A saved quote request is required.")

    valid_statuses = {value for value, _label in QuoteRequest.STATUS_CHOICES}
    if new_status not in valid_statuses:
        raise ValidationError({"status": "Select a valid quote status."})

    locked_quote = (
        QuoteRequest.objects
        .select_for_update()
        .get(pk=quote.pk)
    )
    allowed = ALLOWED_STATUS_TRANSITIONS.get(locked_quote.status)
    if allowed is None:
        raise ValidationError(
            {"status": "The quote request has an invalid current status."}
        )
    if new_status not in allowed:
        raise ValidationError(
            {
                "status": (
                    f"A quote request in '{locked_quote.status}' status "
                    f"cannot be changed to '{new_status}'."
                )
            }
        )

    if new_status in {"prepared", "sent"} and not _valid_prepared_quotation(
        locked_quote,
        timezone.localdate(),
    ):
        raise ValidationError(
            "Prepare the quotation with a positive amount and future "
            "validity date before advancing its status."
        )

    previous_status = locked_quote.status
    locked_quote.status = new_status
    update_fields = ["status", "updated_at"]
    if new_status == "sent":
        locked_quote.quote_sent_at = timezone.now()
        update_fields.append("quote_sent_at")

    locked_quote.save(update_fields=update_fields)
    quote.status = locked_quote.status
    quote.updated_at = locked_quote.updated_at
    if new_status == "sent":
        quote.quote_sent_at = locked_quote.quote_sent_at

    if (
        previous_status == new_status
        or new_status not in {"sent", "declined", "expired"}
    ):
        return quote

    recipient = locked_quote.email
    if not recipient or not recipient.strip():
        return quote

    context = {
        "quote_reference": locked_quote.pk,
        "service_name": (
            locked_quote.service.name
            if locked_quote.service_id
            else "Cleaning service"
        ),
    }
    if new_status == "sent":
        context.update(
            {
                "proposed_amount": locked_quote.proposed_amount,
                "quote_valid_until": locked_quote.quote_valid_until,
            }
        )
    subject, text_template, html_template = QUOTE_STATUS_EMAILS[new_status]
    render_failed = False
    try:
        text_body = render_to_string(text_template, context)
        html_body = render_to_string(html_template, context)
    except Exception:
        text_body = ""
        html_body = ""
        render_failed = True
    event_key = f"quote:{locked_quote.pk}:status:{new_status}"

    try:
        with transaction.atomic():
            notification = QuoteStatusNotification.objects.create(
                quote=locked_quote,
                quote_reference=f"Q-{locked_quote.pk}",
                event=new_status,
                event_key=event_key,
                recipient=recipient.strip(),
                subject=subject,
                text_body=text_body,
                html_body=html_body,
                status="failed" if render_failed else "pending",
                last_error=(
                    "Template rendering failed." if render_failed else ""
                ),
            )
    except IntegrityError:
        if not QuoteStatusNotification.objects.filter(
            event_key=event_key,
        ).exists():
            raise
        logger.warning(
            "Duplicate quote notification event ignored for quote id %s "
            "(%s).",
            locked_quote.pk,
            new_status,
        )
        return quote

    if render_failed:
        logger.warning(
            "Failed to render quote status notification for quote id %s "
            "(%s).",
            locked_quote.pk,
            new_status,
        )
        return quote

    transaction.on_commit(
        lambda notification_id=notification.pk: (
            deliver_quote_status_notification(notification_id)
        )
    )
    return quote


def _decide_quote(*, quote_id, user, decision):
    if (
        user is None
        or user.pk is None
        or not getattr(user, "is_authenticated", False)
    ):
        raise ValidationError("An authenticated customer is required.")
    if not user.is_active:
        raise ValidationError("The customer account must be active.")

    # Lock Customer before QuoteRequest; guest linking has additional locks.
    customer = (
        Customer.objects
        .select_for_update()
        .filter(user_id=user.pk)
        .first()
    )
    if customer is None:
        raise ValidationError(
            "This quotation is not linked to your customer profile."
        )

    locked_quote = (
        QuoteRequest.objects
        .select_for_update()
        .get(pk=quote_id)
    )
    if locked_quote.customer_id != customer.pk:
        raise ValidationError(
            "This quotation is not linked to your customer profile."
        )

    if locked_quote.status != "sent":
        raise ValidationError(
            "Only sent quotations can be accepted or declined."
        )
    if (
        locked_quote.quote_sent_at is None
        or locked_quote.quote_valid_until is None
    ):
        raise ValidationError(
            "The quotation is missing its sent date or validity date."
        )
    if locked_quote.quote_valid_until < timezone.localdate():
        raise ValidationError("This quotation has expired.")

    return change_quote_status(locked_quote, decision)


@transaction.atomic
def accept_quote(*, quote_id, user):
    return _decide_quote(
        quote_id=quote_id,
        user=user,
        decision="accepted",
    )


@transaction.atomic
def decline_quote(*, quote_id, user):
    return _decide_quote(
        quote_id=quote_id,
        user=user,
        decision="declined",
    )


@transaction.atomic
def prepare_quotation(quote, proposed_amount, quote_valid_until):
    if quote is None or quote.pk is None:
        raise ValidationError("A saved quote request is required.")

    locked_quote = (
        QuoteRequest.objects
        .select_for_update()
        .get(pk=quote.pk)
    )
    if locked_quote.status != "under_review":
        raise ValidationError(
            {
                "status": (
                    "A quotation can only be prepared while the request "
                    "is under review."
                )
            }
        )

    try:
        amount = Decimal(proposed_amount)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValidationError(
            {"proposed_amount": "Enter a valid proposed amount."}
        ) from error

    if (
        not amount.is_finite()
        or amount <= 0
        or amount > MAX_PROPOSED_AMOUNT
    ):
        raise ValidationError(
            {
                "proposed_amount": (
                    "The proposed amount must be positive and within the "
                    "supported amount range."
                )
            }
        )

    if amount.as_tuple().exponent < -2:
        raise ValidationError(
            {
                "proposed_amount": (
                    "The proposed amount can have at most two decimal places."
                )
            }
        )

    if (
        not isinstance(quote_valid_until, date)
        or isinstance(quote_valid_until, datetime)
    ):
        raise ValidationError(
            {"quote_valid_until": "Enter a valid quotation expiry date."}
        )
    if quote_valid_until <= timezone.localdate():
        raise ValidationError(
            {
                "quote_valid_until": (
                    "The quotation expiry date must be later than today."
                )
            }
        )

    locked_quote.proposed_amount = amount
    locked_quote.quote_valid_until = quote_valid_until
    locked_quote.quote_prepared_at = timezone.now()
    locked_quote.status = "prepared"
    locked_quote.save(
        update_fields=[
            "proposed_amount",
            "quote_valid_until",
            "quote_prepared_at",
            "status",
            "updated_at",
        ]
    )

    quote.proposed_amount = locked_quote.proposed_amount
    quote.quote_valid_until = locked_quote.quote_valid_until
    quote.quote_prepared_at = locked_quote.quote_prepared_at
    quote.status = locked_quote.status
    quote.updated_at = locked_quote.updated_at
    return quote
