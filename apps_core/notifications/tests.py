from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages import get_messages
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import (
    RequestFactory,
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.urls import reverse
from django.utils import timezone

from apps_core.notifications.admin import QuoteStatusNotificationAdmin
from apps_core.notifications.email import send_template_email
from apps_core.notifications.models import QuoteStatusNotification
from apps_core.notifications.services import (
    STALE_SENDING_THRESHOLD,
    deliver_quote_status_notification,
    retry_quote_status_notification,
)
from apps_core.quotes.models import QuoteRequest
from apps_core.services.models import Service


User = get_user_model()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="CLEANEX <test@example.com>",
)
class SendTemplateEmailTests(SimpleTestCase):

    @patch(
        "apps_core.notifications.email.render_to_string",
        return_value="Plain-text message.",
    )
    def test_sends_plain_text_email(self, render_mock):
        sent_count = send_template_email(
            subject="Test email",
            text_template="emails/test.txt",
            recipient_list=["customer@example.com"],
            context={"name": "Customer"},
        )

        self.assertEqual(sent_count, 1)
        self.assertEqual(len(mail.outbox), 1)

        email = mail.outbox[0]

        self.assertEqual(email.subject, "Test email")
        self.assertEqual(email.body, "Plain-text message.")
        self.assertEqual(email.from_email, "CLEANEX <test@example.com>")
        self.assertEqual(email.to, ["customer@example.com"])
        self.assertEqual(email.alternatives, [])

        render_mock.assert_called_once_with(
            "emails/test.txt",
            {"name": "Customer"},
        )

    @patch(
        "apps_core.notifications.email.render_to_string",
        side_effect=["Plain-text message.", "<p>HTML message.</p>"],
    )
    def test_sends_email_with_html_alternative(self, render_mock):
        sent_count = send_template_email(
            subject="HTML test",
            text_template="emails/test.txt",
            html_template="emails/test.html",
            recipient_list=["customer@example.com"],
        )

        self.assertEqual(sent_count, 1)

        email = mail.outbox[0]

        self.assertEqual(email.body, "Plain-text message.")
        self.assertEqual(
            email.alternatives,
            [("<p>HTML message.</p>", "text/html")],
        )

        self.assertEqual(render_mock.call_count, 2)

    def test_rejects_empty_subject(self):
        with self.assertRaises(ValueError):
            send_template_email(
                subject=" ",
                text_template="emails/test.txt",
                recipient_list=["customer@example.com"],
            )

    def test_rejects_missing_text_template(self):
        with self.assertRaises(ValueError):
            send_template_email(
                subject="Test",
                text_template=" ",
                recipient_list=["customer@example.com"],
            )

    def test_rejects_empty_recipient_list(self):
        with self.assertRaises(ValueError):
            send_template_email(
                subject="Test",
                text_template="emails/test.txt",
                recipient_list=[],
            )

    def test_rejects_invalid_recipient_list(self):
        with self.assertRaises(ValueError):
            send_template_email(
                subject="Test",
                text_template="emails/test.txt",
                recipient_list="customer@example.com",
            )

    @patch(
        "apps_core.notifications.email.render_to_string",
        return_value="Plain-text message.",
    )
    @patch(
        "apps_core.notifications.email.EmailMultiAlternatives.send",
        side_effect=OSError("Email backend unavailable."),
    )
    def test_propagates_email_backend_failure(
        self,
        send_mock,
        render_mock,
    ):
        with self.assertRaisesMessage(
            OSError,
            "Email backend unavailable.",
        ):
            send_template_email(
                subject="Test",
                text_template="emails/test.txt",
                recipient_list=["customer@example.com"],
            )

        send_mock.assert_called_once_with(fail_silently=False)

    def test_rejects_invalid_email_address(self):
        with self.assertRaises(ValueError):
            send_template_email(
                subject="Test",
                text_template="emails/test.txt",
                recipient_list=["not-an-email"],
            )

    @patch(
        "apps_core.notifications.email.render_to_string",
        side_effect=AssertionError("Saved bodies should not be rendered."),
    )
    def test_sends_saved_rendered_bodies_without_rendering_templates(
        self,
        render_mock,
    ):
        sent_count = send_template_email(
            subject="Saved subject",
            recipient_list=["customer@example.com"],
            text_body="Saved plain text.",
            html_body="<p>Saved HTML.</p>",
        )

        self.assertEqual(sent_count, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Saved subject")
        self.assertEqual(mail.outbox[0].body, "Saved plain text.")
        self.assertEqual(
            mail.outbox[0].alternatives,
            [("<p>Saved HTML.</p>", "text/html")],
        )
        render_mock.assert_not_called()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="CLEANEX <test@example.com>",
)
class QuoteNotificationDeliveryTests(TestCase):
    def create_notification(self, *, status="pending"):
        return QuoteStatusNotification.objects.create(
            quote_reference="Q-123",
            event="declined",
            event_key=f"quote:123:status:declined:{status}",
            recipient="customer@example.com",
            subject="Saved subject",
            text_body="Saved plain-text snapshot.",
            html_body="<p>Saved HTML snapshot.</p>",
            status=status,
        )

    def test_successful_delivery_updates_attempt_metadata(self):
        notification = self.create_notification()

        with patch(
            "apps_core.notifications.services.send_template_email",
            return_value=1,
        ) as send_email:
            delivered = deliver_quote_status_notification(notification.pk)

        notification.refresh_from_db()
        self.assertTrue(delivered)
        self.assertEqual(notification.status, "sent")
        self.assertEqual(notification.attempt_count, 1)
        self.assertIsNotNone(notification.last_attempt_at)
        self.assertIsNotNone(notification.sent_at)
        self.assertGreaterEqual(
            notification.sent_at,
            notification.last_attempt_at,
        )
        self.assertEqual(notification.last_error, "")
        send_email.assert_called_once_with(
            subject="Saved subject",
            recipient_list=["customer@example.com"],
            text_body="Saved plain-text snapshot.",
            html_body="<p>Saved HTML snapshot.</p>",
        )

    def test_backend_exception_marks_notification_failed_safely(self):
        notification = self.create_notification()

        with patch(
            "apps_core.notifications.services.send_template_email",
            side_effect=RuntimeError("token=must-not-be-saved"),
        ):
            delivered = deliver_quote_status_notification(notification.pk)

        notification.refresh_from_db()
        self.assertFalse(delivered)
        self.assertEqual(notification.status, "failed")
        self.assertEqual(notification.attempt_count, 1)
        self.assertIsNotNone(notification.last_attempt_at)
        self.assertIsNone(notification.sent_at)
        self.assertIn("RuntimeError", notification.last_error)
        self.assertNotIn("must-not-be-saved", notification.last_error)
        self.assertLessEqual(len(notification.last_error), 500)

    def test_unexpected_send_count_marks_notification_failed(self):
        notification = self.create_notification()

        with patch(
            "apps_core.notifications.services.send_template_email",
            return_value=0,
        ):
            delivered = deliver_quote_status_notification(notification.pk)

        notification.refresh_from_db()
        self.assertFalse(delivered)
        self.assertEqual(notification.status, "failed")
        self.assertEqual(notification.attempt_count, 1)
        self.assertIsNone(notification.sent_at)
        self.assertIn("accepted 0 messages", notification.last_error)

    def test_non_pending_notifications_are_not_claimed_or_sent(self):
        for status in ("sending", "sent", "failed"):
            with self.subTest(status=status):
                notification = self.create_notification(status=status)
                with patch(
                    "apps_core.notifications.services.send_template_email"
                ) as send_email:
                    delivered = deliver_quote_status_notification(
                        notification.pk,
                    )

                notification.refresh_from_db()
                self.assertFalse(delivered)
                self.assertEqual(notification.status, status)
                self.assertEqual(notification.attempt_count, 0)
                self.assertIsNone(notification.last_attempt_at)
                send_email.assert_not_called()

    def test_non_integer_send_count_marks_notification_failed_safely(self):
        notification = self.create_notification()

        with patch(
            "apps_core.notifications.services.send_template_email",
            return_value="token=must-not-be-saved",
        ):
            delivered = deliver_quote_status_notification(notification.pk)

        notification.refresh_from_db()
        self.assertFalse(delivered)
        self.assertEqual(notification.status, "failed")
        self.assertEqual(notification.attempt_count, 1)
        self.assertIsNone(notification.sent_at)
        self.assertIn("invalid result", notification.last_error)
        self.assertNotIn("must-not-be-saved", notification.last_error)
        self.assertLessEqual(len(notification.last_error), 500)

    def test_successful_send_is_not_marked_sent_when_state_update_matches_zero_rows(
        self,
    ):
        notification = self.create_notification()
        original_filter = QuoteStatusNotification.objects.filter

        def filter_stub(*args, **kwargs):
            qs = original_filter(*args, **kwargs)
            if kwargs.get("status") == "sending":
                qs.update = lambda **_fields: 0
            return qs

        with (
            patch(
                "apps_core.notifications.services.send_template_email",
                return_value=1,
            ) as send_email,
            patch.object(
                QuoteStatusNotification.objects,
                "filter",
                side_effect=filter_stub,
            ),
            self.assertLogs(
                "apps_core.notifications.services",
                level="WARNING",
            ) as logs,
        ):
            delivered = deliver_quote_status_notification(notification.pk)

        self.assertFalse(delivered)
        send_email.assert_called_once()
        notification.refresh_from_db()
        self.assertEqual(notification.status, "sending")
        self.assertIsNone(notification.sent_at)
        self.assertTrue(
            any(str(notification.pk) in message for message in logs.output)
        )
        self.assertTrue(
            any("expected 1" in message for message in logs.output)
        )

    def test_failed_send_is_not_marked_failed_when_state_update_matches_zero_rows(
        self,
    ):
        notification = self.create_notification()
        original_filter = QuoteStatusNotification.objects.filter

        def filter_stub(*args, **kwargs):
            qs = original_filter(*args, **kwargs)
            if kwargs.get("status") == "sending":
                qs.update = lambda **_fields: 0
            return qs

        with (
            patch(
                "apps_core.notifications.services.send_template_email",
                side_effect=RuntimeError("token=must-not-be-saved"),
            ) as send_email,
            patch.object(
                QuoteStatusNotification.objects,
                "filter",
                side_effect=filter_stub,
            ),
            self.assertLogs(
                "apps_core.notifications.services",
                level="WARNING",
            ) as logs,
        ):
            delivered = deliver_quote_status_notification(notification.pk)

        self.assertFalse(delivered)
        send_email.assert_called_once()
        notification.refresh_from_db()
        self.assertEqual(notification.status, "sending")
        self.assertEqual(notification.last_error, "")
        log_text = "\n".join(logs.output)
        self.assertIn(str(notification.pk), log_text)
        self.assertIn("expected 1", log_text)
        self.assertNotIn("must-not-be-saved", log_text)

    def test_automatic_delivery_does_not_set_retrying_by(self):
        notification = self.create_notification()

        with patch(
            "apps_core.notifications.services.send_template_email",
            return_value=1,
        ):
            delivered = deliver_quote_status_notification(notification.pk)

        notification.refresh_from_db()
        self.assertTrue(delivered)
        self.assertIsNone(notification.retrying_by_id)


class QuoteNotificationRetryFixtures:
    def create_notification(self, *, status="failed", **overrides):
        values = {
            "quote_reference": "Q-123",
            "event": "declined",
            "event_key": f"quote:123:status:declined:{status}:{id(self)}",
            "recipient": "customer@example.com",
            "subject": "Saved subject",
            "text_body": "Saved plain-text snapshot.",
            "html_body": "<p>Saved HTML snapshot.</p>",
            "status": status,
            "attempt_count": 1 if status == "failed" else 0,
        }
        values.update(overrides)
        return QuoteStatusNotification.objects.create(**values)

    def create_quote(self, *, event="declined", **overrides):
        self._quote_service_counter = (
            getattr(self, "_quote_service_counter", 0) + 1
        )
        service = Service.objects.create(
            name="Retry Cleaning Service",
            slug=(
                f"retry-cleaning-{event}-{id(self)}-"
                f"{self._quote_service_counter}"
            ),
            description="Service for notification retry tests.",
            base_price=Decimal("1000.00"),
            duration_hours=2,
            is_active=True,
        )
        values = {
            "full_name": "Retry Customer",
            "email": "retry-customer@example.com",
            "phone": "0712345678",
            "service": service,
            "property_size": 100,
            "rooms": 2,
            "bathrooms": 1,
            "address": "Retry Address",
            "city": "Nairobi",
            "status": "sent" if event == "sent" else "declined",
        }
        if event == "sent":
            values.update(
                {
                    "proposed_amount": Decimal("2500.00"),
                    "quote_valid_until": (
                        timezone.localdate() + timedelta(days=3)
                    ),
                    "quote_prepared_at": (
                        timezone.now() - timedelta(days=1)
                    ),
                    "quote_sent_at": timezone.now() - timedelta(hours=1),
                }
            )
        values.update(overrides)
        return QuoteRequest.objects.create(**values)

    def grant_permission(self, user, codename):
        content_type = ContentType.objects.get_for_model(
            QuoteStatusNotification,
        )
        permission, _created = Permission.objects.get_or_create(
            content_type=content_type,
            codename=codename,
            defaults={
                "name": f"Can {codename.replace('_', ' ')}",
            },
        )
        user.user_permissions.add(permission)
        return User.objects.get(pk=user.pk)

    def grant_change_permission(self, user):
        return self.grant_permission(
            user,
            "change_quotestatusnotification",
        )

    def create_staff_user(self, *, suffix, with_change=True):
        user = User.objects.create_user(
            username=f"notification-staff-{suffix}",
            email=f"staff-{suffix}@example.com",
            password="test-password",
            is_staff=True,
            is_active=True,
        )
        if with_change:
            user = self.grant_change_permission(user)
        return user


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="CLEANEX <test@example.com>",
)
class QuoteNotificationRetryTests(QuoteNotificationRetryFixtures, TestCase):
    def test_unauthenticated_user_cannot_retry(self):
        notification = self.create_notification()
        with self.assertRaises(PermissionDenied):
            retry_quote_status_notification(
                notification.pk,
                AnonymousUser(),
            )
        notification.refresh_from_db()
        self.assertEqual(notification.status, "failed")
        self.assertEqual(notification.attempt_count, 1)

    def test_non_staff_user_cannot_retry(self):
        notification = self.create_notification()
        user = User.objects.create_user(
            username="notification-customer",
            email="customer-user@example.com",
            password="test-password",
        )
        user = self.grant_change_permission(user)
        with self.assertRaises(PermissionDenied):
            retry_quote_status_notification(notification.pk, user)
        notification.refresh_from_db()
        self.assertEqual(notification.status, "failed")

    def test_staff_without_change_permission_cannot_retry(self):
        notification = self.create_notification()
        user = self.create_staff_user(
            suffix="no-change",
            with_change=False,
        )
        with self.assertRaises(PermissionDenied):
            retry_quote_status_notification(notification.pk, user)
        notification.refresh_from_db()
        self.assertEqual(notification.status, "failed")

    def test_authorized_staff_can_retry_failed_notification(self):
        notification = self.create_notification()
        user = self.create_staff_user(suffix="failed-retry")

        with patch(
            "apps_core.notifications.services.send_template_email",
            return_value=1,
        ) as send_email:
            retried = retry_quote_status_notification(notification.pk, user)

        notification.refresh_from_db()
        self.assertTrue(retried)
        self.assertEqual(notification.status, "sent")
        self.assertEqual(notification.attempt_count, 2)
        self.assertEqual(notification.retrying_by_id, user.pk)
        self.assertEqual(notification.text_body, "Saved plain-text snapshot.")
        self.assertEqual(notification.html_body, "<p>Saved HTML snapshot.</p>")
        self.assertEqual(notification.recipient, "customer@example.com")
        self.assertEqual(notification.subject, "Saved subject")
        send_email.assert_called_once_with(
            subject="Saved subject",
            recipient_list=["customer@example.com"],
            text_body="Saved plain-text snapshot.",
            html_body="<p>Saved HTML snapshot.</p>",
        )

    def test_authorized_staff_can_retry_pending_notification(self):
        notification = self.create_notification(
            status="pending",
            attempt_count=0,
            event_key="quote:124:status:declined:pending-retry",
        )
        user = self.create_staff_user(suffix="pending-retry")

        with patch(
            "apps_core.notifications.services.send_template_email",
            return_value=1,
        ):
            retried = retry_quote_status_notification(notification.pk, user)

        notification.refresh_from_db()
        self.assertTrue(retried)
        self.assertEqual(notification.status, "sent")
        self.assertEqual(notification.attempt_count, 1)
        self.assertEqual(notification.retrying_by_id, user.pk)

    def test_sent_notification_cannot_be_retried(self):
        notification = self.create_notification(
            status="sent",
            event_key="quote:125:status:declined:sent",
        )
        user = self.create_staff_user(suffix="sent-retry")
        with patch(
            "apps_core.notifications.services.send_template_email"
        ) as send_email:
            retried = retry_quote_status_notification(notification.pk, user)

        notification.refresh_from_db()
        self.assertFalse(retried)
        self.assertEqual(notification.status, "sent")
        send_email.assert_not_called()

    def test_sending_notification_cannot_be_retried(self):
        notification = self.create_notification(
            status="sending",
            event_key="quote:126:status:declined:sending",
        )
        user = self.create_staff_user(suffix="sending-retry")
        with patch(
            "apps_core.notifications.services.send_template_email"
        ) as send_email:
            retried = retry_quote_status_notification(notification.pk, user)

        notification.refresh_from_db()
        self.assertFalse(retried)
        self.assertEqual(notification.status, "sending")
        send_email.assert_not_called()

    def test_empty_text_body_cannot_be_retried(self):
        notification = self.create_notification(
            text_body="",
            event_key="quote:127:status:declined:empty",
        )
        user = self.create_staff_user(suffix="empty-body")
        with patch(
            "apps_core.notifications.services.send_template_email"
        ) as send_email:
            retried = retry_quote_status_notification(notification.pk, user)

        notification.refresh_from_db()
        self.assertFalse(retried)
        self.assertEqual(notification.status, "failed")
        send_email.assert_not_called()

    def test_whitespace_text_body_cannot_be_retried(self):
        notification = self.create_notification(
            text_body=" \t\n",
            event_key="quote:128:status:declined:whitespace",
        )
        user = self.create_staff_user(suffix="whitespace-body")
        with patch(
            "apps_core.notifications.services.send_template_email"
        ) as send_email:
            retried = retry_quote_status_notification(notification.pk, user)

        notification.refresh_from_db()
        self.assertFalse(retried)
        self.assertEqual(notification.status, "failed")
        send_email.assert_not_called()

    def test_missing_notification_is_handled_safely(self):
        user = self.create_staff_user(suffix="missing")
        with patch(
            "apps_core.notifications.services.send_template_email"
        ) as send_email:
            retried = retry_quote_status_notification(999999, user)

        self.assertFalse(retried)
        send_email.assert_not_called()

    def test_failed_retry_records_staff_and_preserves_snapshots(self):
        notification = self.create_notification()
        user = self.create_staff_user(suffix="failed-smtp")

        with patch(
            "apps_core.notifications.services.send_template_email",
            side_effect=RuntimeError("token=must-not-be-saved"),
        ):
            retried = retry_quote_status_notification(notification.pk, user)

        notification.refresh_from_db()
        self.assertFalse(retried)
        self.assertEqual(notification.status, "failed")
        self.assertEqual(notification.attempt_count, 2)
        self.assertEqual(notification.retrying_by_id, user.pk)
        self.assertEqual(notification.text_body, "Saved plain-text snapshot.")
        self.assertNotIn("must-not-be-saved", notification.last_error)

    def test_failed_rendering_is_reconstructed_and_delivered(self):
        quote = self.create_quote(event="declined")
        notification = self.create_notification(
            status="failed",
            quote=quote,
            event="declined",
            event_key=f"quote:{quote.pk}:status:declined:render-fail",
            subject="Your CLEANEX quotation has been declined",
            text_body="",
            html_body="",
            last_error="Template rendering failed.",
        )
        user = self.create_staff_user(suffix="render-recover")

        with patch(
            "apps_core.notifications.services.send_template_email",
            return_value=1,
        ) as send_email:
            retried = retry_quote_status_notification(notification.pk, user)

        notification.refresh_from_db()
        self.assertTrue(retried)
        self.assertEqual(notification.status, "sent")
        self.assertEqual(notification.attempt_count, 2)
        self.assertEqual(notification.retrying_by_id, user.pk)
        self.assertEqual(notification.recipient, "customer@example.com")
        self.assertEqual(
            notification.subject,
            "Your CLEANEX quotation has been declined",
        )
        self.assertIn(f"Q-{quote.pk}", notification.text_body)
        self.assertIn("Retry Cleaning Service", notification.text_body)
        self.assertIn(f"Q-{quote.pk}", notification.html_body)
        self.assertEqual(notification.last_error, "")
        send_email.assert_called_once()
        sent_kwargs = send_email.call_args.kwargs
        self.assertEqual(sent_kwargs["recipient_list"], ["customer@example.com"])
        self.assertIn(f"Q-{quote.pk}", sent_kwargs["text_body"])

    def test_sent_rendering_recovery_uses_quote_historical_fields(self):
        quote = self.create_quote(event="sent")
        notification = self.create_notification(
            status="failed",
            quote=quote,
            event="sent",
            event_key=f"quote:{quote.pk}:status:sent:render-fail",
            subject="Your CLEANEX quotation is ready for review",
            text_body="",
            html_body="",
            last_error="Template rendering failed.",
        )
        user = self.create_staff_user(suffix="sent-render-recover")

        with patch(
            "apps_core.notifications.services.send_template_email",
            return_value=1,
        ):
            retried = retry_quote_status_notification(notification.pk, user)

        notification.refresh_from_db()
        self.assertTrue(retried)
        self.assertEqual(notification.status, "sent")
        self.assertIn("2500.00", notification.text_body)
        self.assertIn(
            quote.quote_valid_until.strftime("%B"),
            notification.text_body,
        )
        self.assertIn("2500.00", notification.html_body)
        self.assertEqual(notification.last_error, "")

    def test_recently_started_sending_cannot_be_reclaimed(self):
        started_at = timezone.now()
        notification = self.create_notification(
            status="sending",
            attempt_count=1,
            last_attempt_at=started_at,
            event_key="quote:400:status:declined:recent-sending",
        )
        user = self.create_staff_user(suffix="recent-sending")

        with patch(
            "apps_core.notifications.services.send_template_email"
        ) as send_email:
            retried = retry_quote_status_notification(notification.pk, user)

        notification.refresh_from_db()
        self.assertFalse(retried)
        self.assertEqual(notification.status, "sending")
        self.assertEqual(notification.attempt_count, 1)
        self.assertEqual(notification.last_attempt_at, started_at)
        send_email.assert_not_called()

    def test_stale_sending_is_recovered_by_staff_retry(self):
        stale_at = (
            timezone.now()
            - STALE_SENDING_THRESHOLD
            - timedelta(minutes=1)
        )
        notification = self.create_notification(
            status="sending",
            attempt_count=1,
            last_attempt_at=stale_at,
            event_key="quote:401:status:declined:stale-sending",
        )
        user = self.create_staff_user(suffix="stale-sending")

        with patch(
            "apps_core.notifications.services.send_template_email",
            return_value=1,
        ) as send_email:
            retried = retry_quote_status_notification(notification.pk, user)

        notification.refresh_from_db()
        self.assertTrue(retried)
        self.assertEqual(notification.status, "sent")
        self.assertEqual(notification.attempt_count, 2)
        self.assertEqual(notification.retrying_by_id, user.pk)
        self.assertGreater(notification.last_attempt_at, stale_at)
        self.assertEqual(notification.text_body, "Saved plain-text snapshot.")
        self.assertEqual(notification.last_error, "")
        send_email.assert_called_once_with(
            subject="Saved subject",
            recipient_list=["customer@example.com"],
            text_body="Saved plain-text snapshot.",
            html_body="<p>Saved HTML snapshot.</p>",
        )


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="CLEANEX <test@example.com>",
)
class QuoteNotificationRetryConcurrencyTests(
    QuoteNotificationRetryFixtures,
    TransactionTestCase,
):
    def test_competing_retries_do_not_duplicate_delivery_claims(self):
        notification = self.create_notification(
            event_key="quote:200:status:declined:concurrent",
        )
        first_user = self.create_staff_user(suffix="concurrent-a")
        second_user = self.create_staff_user(suffix="concurrent-b")
        notification_id = notification.pk

        def retry_as(user_id):
            user = User.objects.get(pk=user_id)
            try:
                return retry_quote_status_notification(notification_id, user)
            finally:
                connection.close()

        with patch(
            "apps_core.notifications.services.send_template_email",
            return_value=1,
        ) as send_email:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(retry_as, first_user.pk),
                    pool.submit(retry_as, second_user.pk),
                ]
                results = [future.result() for future in as_completed(futures)]

        notification.refresh_from_db()
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 1)
        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(notification.status, "sent")
        self.assertEqual(notification.attempt_count, 2)
        self.assertIn(
            notification.retrying_by_id,
            {first_user.pk, second_user.pk},
        )
        self.assertEqual(
            QuoteStatusNotification.objects.filter(
                event_key=notification.event_key,
            ).count(),
            1,
        )

    def test_competing_stale_sending_retries_do_not_duplicate_delivery(self):
        stale_at = (
            timezone.now()
            - STALE_SENDING_THRESHOLD
            - timedelta(minutes=1)
        )
        notification = self.create_notification(
            status="sending",
            attempt_count=1,
            last_attempt_at=stale_at,
            event_key="quote:201:status:declined:stale-concurrent",
        )
        first_user = self.create_staff_user(suffix="stale-concurrent-a")
        second_user = self.create_staff_user(suffix="stale-concurrent-b")
        notification_id = notification.pk

        def retry_as(user_id):
            user = User.objects.get(pk=user_id)
            try:
                return retry_quote_status_notification(notification_id, user)
            finally:
                connection.close()

        with patch(
            "apps_core.notifications.services.send_template_email",
            return_value=1,
        ) as send_email:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(retry_as, first_user.pk),
                    pool.submit(retry_as, second_user.pk),
                ]
                results = [future.result() for future in as_completed(futures)]

        notification.refresh_from_db()
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 1)
        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(notification.status, "sent")
        self.assertEqual(notification.attempt_count, 2)
        self.assertIn(
            notification.retrying_by_id,
            {first_user.pk, second_user.pk},
        )


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="CLEANEX <test@example.com>",
)
class QuoteStatusNotificationAdminTests(
    QuoteNotificationRetryFixtures,
    TestCase,
):
    def setUp(self):
        self.factory = RequestFactory()
        self.superuser = User.objects.create_superuser(
            username="notification-admin",
            email="notification-admin@example.com",
            password="admin-password",
        )
        self.model_admin = QuoteStatusNotificationAdmin(
            QuoteStatusNotification,
            admin.site,
        )

    def changelist_url(self):
        return reverse(
            "admin:notifications_quotestatusnotification_changelist"
        )

    def test_notification_is_registered_and_listed(self):
        notification = self.create_notification(
            event_key="quote:300:status:declined:admin-list",
        )
        self.client.force_login(self.superuser)
        response = self.client.get(self.changelist_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, notification.quote_reference)
        self.assertContains(response, notification.recipient)
        self.assertContains(response, notification.status)
        self.assertNotContains(response, notification.text_body)
        self.assertNotContains(response, notification.html_body)
        self.assertIn(
            QuoteStatusNotification,
            admin.site._registry,
        )
        self.assertEqual(
            self.model_admin.list_filter,
            ("status", "event"),
        )
        self.assertEqual(
            self.model_admin.search_fields,
            ("quote_reference", "recipient", "event_key"),
        )

    def test_stale_sending_indicator_is_visible_to_staff(self):
        stale = self.create_notification(
            status="sending",
            attempt_count=1,
            last_attempt_at=(
                timezone.now()
                - STALE_SENDING_THRESHOLD
                - timedelta(minutes=1)
            ),
            event_key="quote:306:status:declined:admin-stale",
        )
        recent = self.create_notification(
            status="sending",
            attempt_count=1,
            last_attempt_at=timezone.now(),
            event_key="quote:307:status:declined:admin-recent",
        )

        self.assertTrue(self.model_admin.stale_sending(stale))
        self.assertFalse(self.model_admin.stale_sending(recent))
        self.assertIn("stale_sending", self.model_admin.list_display)

    def test_all_fields_are_read_only_and_history_cannot_be_added_or_deleted(
        self,
    ):
        notification = self.create_notification(
            event_key="quote:301:status:declined:admin-readonly",
        )
        request = self.factory.get("/")
        request.user = self.superuser
        readonly_fields = self.model_admin.get_readonly_fields(
            request,
            notification,
        )
        field_names = [
            field.name for field in QuoteStatusNotification._meta.fields
        ]
        for field_name in field_names:
            with self.subTest(field=field_name):
                self.assertIn(field_name, readonly_fields)
        self.assertFalse(self.model_admin.has_add_permission(request))
        self.assertFalse(
            self.model_admin.has_delete_permission(request, notification)
        )

        self.client.force_login(self.superuser)
        change_url = reverse(
            "admin:notifications_quotestatusnotification_change",
            args=(notification.pk,),
        )
        response = self.client.post(
            change_url,
            {
                "recipient": "changed@example.com",
                "subject": "Changed subject",
                "text_body": "Changed body",
                "status": "sent",
            },
        )
        notification.refresh_from_db()
        self.assertEqual(notification.recipient, "customer@example.com")
        self.assertEqual(notification.subject, "Saved subject")
        self.assertEqual(notification.text_body, "Saved plain-text snapshot.")
        self.assertEqual(notification.status, "failed")
        self.assertEqual(response.status_code, 302)

    def test_authorized_staff_can_retry_through_post_action(self):
        notification = self.create_notification(
            event_key="quote:302:status:declined:admin-post",
        )
        staff = self.create_staff_user(suffix="admin-post")
        self.client.force_login(staff)
        with patch(
            "apps_core.notifications.services.send_template_email",
            return_value=1,
        ) as send_email:
            response = self.client.post(
                self.changelist_url(),
                {
                    "action": "retry_selected_notifications",
                    "index": "0",
                    "_selected_action": [str(notification.pk)],
                },
            )

        notification.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(send_email.called)
        self.assertEqual(notification.status, "sent")
        self.assertEqual(notification.retrying_by_id, staff.pk)
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(messages)
        self.assertNotIn(notification.text_body, "".join(messages))
        self.assertNotIn("token=", "".join(messages))

    def test_get_request_cannot_trigger_retry(self):
        notification = self.create_notification(
            event_key="quote:303:status:declined:admin-get",
        )
        staff = self.create_staff_user(suffix="admin-get")
        self.client.force_login(staff)
        with patch(
            "apps_core.notifications.services.send_template_email"
        ) as send_email:
            response = self.client.get(
                self.changelist_url(),
                {
                    "action": "retry_selected_notifications",
                    "_selected_action": str(notification.pk),
                },
            )

        notification.refresh_from_db()
        self.assertIn(response.status_code, {200, 302})
        send_email.assert_not_called()
        self.assertEqual(notification.status, "failed")

    def test_unauthorized_staff_cannot_execute_retry_action(self):
        notification = self.create_notification(
            event_key="quote:304:status:declined:admin-unauth",
        )
        staff = self.create_staff_user(
            suffix="admin-unauth",
            with_change=False,
        )
        staff = self.grant_permission(
            staff,
            "view_quotestatusnotification",
        )
        self.client.force_login(staff)
        with patch(
            "apps_core.notifications.services.send_template_email"
        ) as send_email:
            response = self.client.post(
                self.changelist_url(),
                {
                    "action": "retry_selected_notifications",
                    "index": "0",
                    "_selected_action": [str(notification.pk)],
                },
            )

        notification.refresh_from_db()
        self.assertIn(response.status_code, {200, 302, 403})
        send_email.assert_not_called()
        self.assertEqual(notification.status, "failed")
        self.assertIsNone(notification.retrying_by_id)

    def test_ineligible_admin_retry_is_not_sent(self):
        notification = self.create_notification(
            status="sent",
            event_key="quote:305:status:declined:admin-ineligible",
        )
        staff = self.create_staff_user(suffix="admin-ineligible")
        self.client.force_login(staff)
        with patch(
            "apps_core.notifications.services.send_template_email"
        ) as send_email:
            response = self.client.post(
                self.changelist_url(),
                {
                    "action": "retry_selected_notifications",
                    "index": "0",
                    "_selected_action": [str(notification.pk)],
                },
                follow=True,
            )

        notification.refresh_from_db()
        send_email.assert_not_called()
        self.assertEqual(notification.status, "sent")
        self.assertContains(response, "could not be retried")
        self.assertNotContains(response, notification.text_body)
