import logging
import re
from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F
from django.template.loader import render_to_string
from django.utils import timezone

from .email import send_template_email
from .models import QuoteStatusNotification


logger = logging.getLogger(__name__)

# SMTP delivery normally completes in seconds; 15 minutes is conservative.
STALE_SENDING_THRESHOLD = timedelta(minutes=15)


def _safe_error_type(error):
    error_type = re.sub(r"[^A-Za-z0-9_.-]", "", type(error).__name__)
    return error_type[:80] or "Error"


def _update_sending_notification(notification_id, claim_time, **fields):
    updated = QuoteStatusNotification.objects.filter(
        pk=notification_id,
        status="sending",
        last_attempt_at=claim_time,
    ).update(**fields)
    if updated == 1:
        return True
    logger.warning(
        "Quote notification %s state update affected %s rows; expected 1.",
        notification_id,
        updated,
    )
    return False


def _user_may_retry_notifications(user):
    return bool(
        user is not None
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and getattr(user, "is_staff", False)
        and user.has_perm("notifications.change_quotestatusnotification")
    )


def _require_retry_user(user):
    if not _user_may_retry_notifications(user):
        raise PermissionDenied(
            "You are not authorized to retry quote notifications."
        )


def _has_deliverable_text_body(notification):
    return bool(
        isinstance(notification.text_body, str)
        and notification.text_body.strip()
    )


def is_stale_sending(notification, *, now=None):
    if (
        notification.status != "sending"
        or notification.last_attempt_at is None
    ):
        return False
    now = now or timezone.now()
    return (
        now - notification.last_attempt_at
        >= STALE_SENDING_THRESHOLD
    )


def _quote_notification_context(notification):
    quote = notification.quote
    if quote is None or quote.pk is None:
        return None
    context = {
        "quote_reference": quote.pk,
        "service_name": (
            quote.service.name if quote.service_id else "Cleaning service"
        ),
    }
    if notification.event == "sent":
        context["proposed_amount"] = quote.proposed_amount
        context["quote_valid_until"] = quote.quote_valid_until
    return context


def _render_quote_notification_bodies(notification):
    context = _quote_notification_context(notification)
    if context is None:
        return None
    try:
        from apps_core.quotes.services import QUOTE_STATUS_EMAILS
    except Exception:
        return None
    templates = QUOTE_STATUS_EMAILS.get(notification.event)
    if templates is None:
        return None
    _subject, text_template, html_template = templates
    try:
        text_body = render_to_string(text_template, context)
        html_body = render_to_string(html_template, context)
    except Exception as error:
        logger.warning(
            "Quote notification %s body reconstruction failed (%s).",
            notification.pk,
            _safe_error_type(error),
        )
        return None
    if not isinstance(text_body, str) or not text_body.strip():
        return None
    if not isinstance(html_body, str):
        return None
    return text_body, html_body


def _ensure_deliverable_body(notification):
    if _has_deliverable_text_body(notification):
        return notification
    rendered = _render_quote_notification_bodies(notification)
    if rendered is None:
        return None
    text_body, html_body = rendered
    updated = QuoteStatusNotification.objects.filter(
        pk=notification.pk,
    ).update(
        text_body=text_body,
        html_body=html_body,
        updated_at=timezone.now(),
    )
    if updated != 1:
        return None
    notification.text_body = text_body
    notification.html_body = html_body
    return notification


def deliver_quote_status_notification(notification_id, *, retrying_by=None):
    """Claim and attempt one pending quote notification."""
    attempted_at = timezone.now()
    claim_fields = {
        "status": "sending",
        "attempt_count": F("attempt_count") + 1,
        "last_attempt_at": attempted_at,
        "updated_at": attempted_at,
    }
    if retrying_by is not None:
        claim_fields["retrying_by"] = retrying_by
    with transaction.atomic():
        claimed = QuoteStatusNotification.objects.filter(
            pk=notification_id,
            status="pending",
        ).update(**claim_fields)
        if not claimed:
            return False

        notification = QuoteStatusNotification.objects.get(
            pk=notification_id,
        )

    try:
        sent_count = send_template_email(
            subject=notification.subject,
            recipient_list=[notification.recipient],
            text_body=notification.text_body,
            html_body=notification.html_body,
        )
    except Exception as error:
        error_summary = f"Delivery failed ({_safe_error_type(error)})."[:500]
        logger.warning(
            "Quote notification %s delivery failed (%s).",
            notification_id,
            _safe_error_type(error),
        )
        _update_sending_notification(
            notification_id,
            attempted_at,
            status="failed",
            last_error=error_summary,
            updated_at=timezone.now(),
        )
        return False

    if type(sent_count) is not int or sent_count != 1:
        if type(sent_count) is int:
            outcome = str(sent_count)
        else:
            outcome = f"invalid result ({_safe_error_type(sent_count)})"
        error_summary = (
            f"Email backend accepted {outcome} messages; expected 1."
        )[:500]
        logger.warning(
            "Quote notification %s was not accepted exactly once.",
            notification_id,
        )
        _update_sending_notification(
            notification_id,
            attempted_at,
            status="failed",
            last_error=error_summary,
            updated_at=timezone.now(),
        )
        return False

    sent_at = timezone.now()
    if not _update_sending_notification(
        notification_id,
        attempted_at,
        status="sent",
        sent_at=sent_at,
        last_error="",
        updated_at=sent_at,
    ):
        return False
    return True


def retry_quote_status_notification(notification_id, user):
    """Retry one eligible pending, failed, or stale sending notification."""
    _require_retry_user(user)

    notification = QuoteStatusNotification.objects.filter(
        pk=notification_id,
    ).first()
    if notification is None:
        return False

    now = timezone.now()
    if notification.status == "sending":
        if not is_stale_sending(notification, now=now):
            return False
        prepared = QuoteStatusNotification.objects.filter(
            pk=notification.pk,
            status="sending",
            last_attempt_at=notification.last_attempt_at,
        ).update(
            status="pending",
            retrying_by=user,
            updated_at=now,
        )
        if not prepared:
            return False
        notification.status = "pending"
        notification.retrying_by = user
    elif notification.status not in {"pending", "failed"}:
        return False

    notification = _ensure_deliverable_body(notification)
    if notification is None:
        return False

    if notification.status == "failed":
        prepared = QuoteStatusNotification.objects.filter(
            pk=notification.pk,
            status="failed",
        ).update(
            status="pending",
            retrying_by=user,
            updated_at=timezone.now(),
        )
        if not prepared:
            return False

    return deliver_quote_status_notification(
        notification_id,
        retrying_by=user,
    )
