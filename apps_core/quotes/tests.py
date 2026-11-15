from datetime import date, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core import mail
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import (
    RequestFactory,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.utils import timezone
from django.urls import reverse

from apps_core.bookings.models import Booking
from apps_core.customers.models import Customer
from apps_core.notifications.models import QuoteStatusNotification
from apps_core.payments.models import Payment
from apps_core.services.models import Service

from .forms import QuoteRequestForm
from .admin import QuoteRequestAdminForm
from .models import QuoteRequest
from .services import (
    accept_quote,
    calculate_quote,
    change_quote_status,
    create_quote_request,
    decline_quote,
    prepare_quotation,
)


User = get_user_model()


class QuoteFixtureMixin:
    def create_service(self, **overrides):
        values = {
            "name": "Quote Test Cleaning",
            "slug": "quote-test-cleaning",
            "description": "Cleaning service for quote tests.",
            "base_price": Decimal("1000.25"),
            "duration_hours": Decimal("2.00"),
            "is_active": True,
        }
        values.update(overrides)
        return Service.objects.create(**values)

    def quote_data(self, service=None, **overrides):
        values = {
            "full_name": "Amina Customer",
            "email": "AMINA@example.com",
            "phone": "+254 700 000 000",
            "service": service or self.create_service(),
            "property_size": 100,
            "rooms": 3,
            "bathrooms": 2,
            "extras": "",
            "address": "  12 Green Road  ",
            "city": " Nairobi ",
            "preferred_date": None,
            "preferred_time": None,
        }
        values.update(overrides)
        return values


class QuoteRequestFormTests(QuoteFixtureMixin, TestCase):
    def test_valid_quote_form_and_optional_schedule(self):
        service = self.create_service()
        data = self.quote_data(
            service=service.pk,
            preferred_date=timezone.localdate() + timedelta(days=2),
            preferred_time="10:30",
        )

        form = QuoteRequestForm(data=data)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["email"], "amina@example.com")
        self.assertEqual(form.cleaned_data["full_name"], "Amina Customer")
        self.assertEqual(form.cleaned_data["address"], "12 Green Road")
        self.assertEqual(form.cleaned_data["city"], "Nairobi")
        self.assertEqual(form.cleaned_data["preferred_time"], time(10, 30))
        self.assertEqual(
            set(form.fields),
            {
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
            },
        )

    def test_inactive_service_is_rejected(self):
        service = self.create_service(
            name="Inactive Quote Service",
            slug="inactive-quote-service",
            is_active=False,
        )

        form = QuoteRequestForm(
            data=self.quote_data(service=service.pk)
        )

        self.assertFalse(form.is_valid())
        self.assertIn("service", form.errors)

    def test_unknown_service_id_is_rejected(self):
        form = QuoteRequestForm(
            data=self.quote_data(service="999999")
        )

        self.assertFalse(form.is_valid())
        self.assertIn("service", form.errors)

    def test_missing_service_is_rejected(self):
        data = self.quote_data()
        data["service"] = ""

        form = QuoteRequestForm(data=data)

        self.assertFalse(form.is_valid())
        self.assertIn("service", form.errors)

    def test_missing_address_or_city_is_rejected(self):
        service = self.create_service()
        for field in ("address", "city"):
            with self.subTest(field=field):
                data = self.quote_data(service=service.pk)
                data[field] = "   "
                form = QuoteRequestForm(data=data)

                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_invalid_phone_and_email_are_rejected(self):
        service = self.create_service()
        for field, invalid_value in (
            ("phone", "abc"),
            ("email", "not-an-email"),
        ):
            with self.subTest(field=field):
                data = self.quote_data(
                    service=service.pk,
                    **{field: invalid_value},
                )
                form = QuoteRequestForm(data=data)

                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_numeric_fields_must_be_positive_and_bounded(self):
        service = self.create_service()
        limits = {
            "property_size": 100_000,
            "rooms": 100,
            "bathrooms": 100,
        }
        for field, maximum in limits.items():
            for value in (0, -1, maximum + 1):
                with self.subTest(field=field, value=value):
                    form = QuoteRequestForm(
                        data=self.quote_data(
                            service=service.pk,
                            **{field: value},
                        )
                    )

                    self.assertFalse(form.is_valid())
                    self.assertIn(field, form.errors)

    def test_non_integer_numeric_values_are_rejected(self):
        service = self.create_service()
        form = QuoteRequestForm(
            data=self.quote_data(service=service.pk, property_size="large")
        )

        self.assertFalse(form.is_valid())
        self.assertIn("property_size", form.errors)

    def test_past_preferred_date_is_rejected(self):
        service = self.create_service()
        form = QuoteRequestForm(
            data=self.quote_data(
                service=service.pk,
                preferred_date=timezone.localdate() - timedelta(days=1)
            )
        )

        self.assertFalse(form.is_valid())
        self.assertIn("preferred_date", form.errors)

    def test_schedule_fields_may_be_omitted(self):
        service = self.create_service()
        data = self.quote_data(service=service.pk)
        data.pop("preferred_date")
        data.pop("preferred_time")

        form = QuoteRequestForm(data=data)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["preferred_date"])
        self.assertIsNone(form.cleaned_data["preferred_time"])


class QuoteServiceTests(QuoteFixtureMixin, TestCase):
    def setUp(self):
        self.service = self.create_service()

    def test_estimate_uses_decimal_formula_and_rounds_to_cents(self):
        estimate = calculate_quote(
            service=self.service,
            property_size=100,
            rooms=3,
            bathrooms=2,
        )

        self.assertEqual(estimate, Decimal("2950.25"))

    def test_estimate_preserves_legacy_free_text_allowance(self):
        estimate = calculate_quote(
            service=self.service,
            property_size=100,
            rooms=3,
            bathrooms=2,
            extras="Customer mentioned a special request",
        )

        self.assertEqual(estimate, Decimal("3950.25"))

    def test_estimate_rejects_invalid_service_and_numeric_inputs(self):
        with self.assertRaises(ValidationError):
            calculate_quote(None, 100, 3, 2)

        with self.assertRaises(ValidationError):
            calculate_quote(self.service, -1, 3, 2)

        self.service.is_active = False
        with self.assertRaises(ValidationError):
            calculate_quote(self.service, 100, 3, 2)

    def test_guest_quote_is_saved_without_customer_association(self):
        quote = create_quote_request(self.quote_data(service=self.service))

        self.assertEqual(quote.status, "pending")
        self.assertIsNone(quote.customer)
        self.assertEqual(quote.estimated_price, Decimal("2950.25"))
        self.assertEqual(quote.proposed_amount, None)

    def test_authenticated_quote_uses_only_existing_linked_customer(self):
        user = User.objects.create_user(
            username="quote-customer",
            email="account@example.com",
            password="Secure-test-password-984!",
        )
        customer = Customer.objects.create(
            user=user,
            full_name="Customer Profile Name",
            email="account@example.com",
            phone="0711111111",
            address="Customer Profile Address",
            city="Mombasa",
        )

        quote = create_quote_request(
            self.quote_data(
                service=self.service,
                full_name="Quote Snapshot Name",
                email="quote-contact@example.com",
                phone="0722222222",
            ),
            user=user,
        )

        self.assertEqual(quote.customer_id, customer.pk)
        self.assertEqual(quote.full_name, "Quote Snapshot Name")
        self.assertEqual(quote.email, "quote-contact@example.com")
        self.assertEqual(quote.phone, "0722222222")
        customer.refresh_from_db()
        self.assertEqual(customer.full_name, "Customer Profile Name")
        self.assertEqual(customer.phone, "0711111111")
        self.assertEqual(customer.address, "Customer Profile Address")

    def test_authenticated_user_without_profile_is_not_given_one(self):
        user = User.objects.create_user(
            username="no-quote-profile",
            email="no-profile@example.com",
            password="Secure-test-password-984!",
        )

        quote = create_quote_request(
            self.quote_data(service=self.service),
            user=user,
        )

        self.assertIsNone(quote.customer)
        self.assertFalse(Customer.objects.filter(user=user).exists())

    def test_quote_creation_preserves_snapshots_and_has_no_booking_or_payment(self):
        data = self.quote_data(
            service=self.service,
            full_name="Historical Quote Name",
            email="historical@example.com",
            phone="0712345678",
        )

        quote = create_quote_request(data)

        self.assertEqual(quote.full_name, "Historical Quote Name")
        self.assertEqual(quote.email, "historical@example.com")
        self.assertEqual(quote.phone, "0712345678")
        self.assertEqual(quote.address, "  12 Green Road  ")
        self.assertEqual(quote.city, " Nairobi ")
        self.assertEqual(quote.status, "pending")
        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(quote.estimated_price, Decimal("2950.25"))

    def create_quote(self, status="pending"):
        return QuoteRequest.objects.create(
            full_name="Quote Lifecycle Customer",
            email="lifecycle@example.com",
            phone="0712345678",
            service=self.service,
            property_size=100,
            rooms=3,
            bathrooms=2,
            address="12 Green Road",
            city="Nairobi",
            status=status,
        )

    def create_decision_customer(self, suffix=""):
        user = User.objects.create_user(
            username=f"decision-customer{suffix}",
            email=f"decision{suffix}@example.com",
            password="Strong-Decision-Password-984!",
        )
        customer = Customer.objects.create(
            user=user,
            full_name="Decision Customer",
            email=user.email,
            phone="0712345678",
            address="Customer Address",
            city="Nairobi",
        )
        return user, customer

    def create_sent_quote(self, customer=None, **overrides):
        values = {
            "full_name": "Quotation Snapshot",
            "email": "snapshot@example.com",
            "phone": "0722222222",
            "service": self.service,
            "customer": customer,
            "property_size": 150,
            "rooms": 4,
            "bathrooms": 2,
            "extras": "Inside windows",
            "address": "Saved Quote Address",
            "city": "Nairobi",
            "estimated_price": Decimal("3500.25"),
            "status": "sent",
            "proposed_amount": Decimal("5200.50"),
            "quote_valid_until": timezone.localdate() + timedelta(days=3),
            "quote_prepared_at": timezone.now() - timedelta(days=1),
            "quote_sent_at": timezone.now() - timedelta(hours=2),
            "internal_notes": "Staff-only decision note.",
        }
        values.update(overrides)
        return QuoteRequest.objects.create(**values)

    def create_quote_ready_to_send(self, customer=None):
        quote = self.create_quote()
        if customer is not None:
            quote.customer = customer
            quote.save(update_fields=["customer"])
        change_quote_status(quote, "under_review")
        prepare_quotation(
            quote,
            Decimal("5200.50"),
            timezone.localdate() + timedelta(days=3),
        )
        return quote

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="CLEANEX <test@example.com>",
    )
    def test_sent_transition_emails_linked_customer_once(self):
        _user, customer = self.create_decision_customer("-sent-email")
        quote = self.create_quote_ready_to_send(customer=customer)
        mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            change_quote_status(quote, "sent")

        self.assertEqual(len(callbacks), 1)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(
            message.subject,
            "Your CLEANEX quotation is ready for review",
        )
        self.assertEqual(message.to, [quote.email])
        self.assertNotEqual(message.to, [customer.email])
        self.assertIn(f"Q-{quote.pk}", message.body)
        self.assertIn(self.service.name, message.body)
        self.assertIn("5200.50", message.body)
        self.assertIn(
            quote.quote_valid_until.strftime("%B"),
            message.body,
        )
        self.assertNotIn("Staff-only decision note.", message.body)
        self.assertEqual(len(message.alternatives), 1)
        self.assertIn(f"Q-{quote.pk}", message.alternatives[0][0])

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="CLEANEX <test@example.com>",
    )
    def test_declined_transition_emails_linked_customer_once(self):
        _user, customer = self.create_decision_customer("-declined-email")
        quote = self.create_sent_quote(customer=customer)
        mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            change_quote_status(quote, "declined")

        self.assertEqual(len(callbacks), 1)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(
            message.subject,
            "Your CLEANEX quotation has been declined",
        )
        self.assertEqual(message.to, [quote.email])
        self.assertNotEqual(message.to, [customer.email])
        self.assertIn(f"Q-{quote.pk}", message.body)
        self.assertIn(self.service.name, message.body)
        self.assertNotIn("Staff-only decision note.", message.body)
        self.assertEqual(len(message.alternatives), 1)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="CLEANEX <test@example.com>",
    )
    def test_expired_transition_emails_linked_customer_once(self):
        _user, customer = self.create_decision_customer("-expired-email")
        quote = self.create_sent_quote(customer=customer)
        mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            change_quote_status(quote, "expired")

        self.assertEqual(len(callbacks), 1)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(
            message.subject,
            "Your CLEANEX quotation has expired",
        )
        self.assertEqual(message.to, [quote.email])
        self.assertNotEqual(message.to, [customer.email])
        self.assertIn(f"Q-{quote.pk}", message.body)
        self.assertIn(self.service.name, message.body)
        self.assertNotIn("Staff-only decision note.", message.body)
        self.assertEqual(len(message.alternatives), 1)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="CLEANEX <test@example.com>",
    )
    def test_customer_decline_sends_only_one_notification(self):
        user, customer = self.create_decision_customer("-decline-email")
        quote = self.create_sent_quote(customer=customer)
        mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            decline_quote(quote_id=quote.pk, user=user)

        self.assertEqual(len(callbacks), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [quote.email])
        self.assertEqual(
            mail.outbox[0].subject,
            "Your CLEANEX quotation has been declined",
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="CLEANEX <test@example.com>",
    )
    def test_saving_without_status_change_does_not_send_notification(self):
        _user, customer = self.create_decision_customer("-no-transition")
        quote = self.create_sent_quote(customer=customer)
        mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            quote.internal_notes = "Updated staff-only note."
            quote.save(update_fields=["internal_notes"])

        self.assertEqual(callbacks, [])
        self.assertEqual(mail.outbox, [])

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="CLEANEX <test@example.com>",
    )
    def test_unrelated_transitions_do_not_send_notifications(self):
        _user, customer = self.create_decision_customer("-unrelated")
        pending_quote = self.create_quote()
        pending_quote.customer = customer
        pending_quote.save(update_fields=["customer"])
        sent_quote = self.create_sent_quote(customer=customer)
        mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            change_quote_status(pending_quote, "under_review")
            prepare_quotation(
                pending_quote,
                Decimal("5200.50"),
                timezone.localdate() + timedelta(days=3),
            )
            change_quote_status(sent_quote, "accepted")

        self.assertEqual(callbacks, [])
        self.assertEqual(mail.outbox, [])

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="CLEANEX <test@example.com>",
    )
    def test_unlinked_guest_transition_emails_saved_quote_address(self):
        quote = self.create_quote_ready_to_send()
        mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            change_quote_status(quote, "sent")

        self.assertEqual(len(callbacks), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIsNone(quote.customer_id)
        self.assertEqual(mail.outbox[0].to, [quote.email])

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="CLEANEX <test@example.com>",
    )
    def test_empty_or_whitespace_quote_email_does_not_attempt_delivery(self):
        for email in ("", " \t\n"):
            with self.subTest(email=repr(email)):
                quote = self.create_sent_quote(email=email)
                with (
                    patch(
                        "apps_core.notifications.services."
                        "deliver_quote_status_notification"
                    ) as send_email,
                    self.captureOnCommitCallbacks(execute=True) as callbacks,
                ):
                    change_quote_status(quote, "declined")

                self.assertEqual(callbacks, [])
                send_email.assert_not_called()
                self.assertFalse(
                    QuoteStatusNotification.objects.filter(
                        quote=quote,
                    ).exists()
                )

    def test_notification_events_use_expected_templates_and_context(self):
        _user, customer = self.create_decision_customer("-template-context")
        expiry = timezone.localdate() + timedelta(days=3)
        cases = (
            ("sent", "Your CLEANEX quotation is ready for review"),
            ("declined", "Your CLEANEX quotation has been declined"),
            ("expired", "Your CLEANEX quotation has expired"),
        )

        for status, expected_subject in cases:
            with self.subTest(status=status):
                if status == "sent":
                    quote = self.create_quote_ready_to_send(customer)
                else:
                    quote = self.create_sent_quote(customer=customer)

                mail.outbox.clear()
                with self.captureOnCommitCallbacks(execute=True):
                    change_quote_status(quote, status)

                notification = QuoteStatusNotification.objects.get(
                    event_key=f"quote:{quote.pk}:status:{status}",
                )
                self.assertEqual(notification.quote_id, quote.pk)
                self.assertEqual(notification.quote_reference, f"Q-{quote.pk}")
                self.assertEqual(notification.event, status)
                self.assertEqual(notification.recipient, quote.email)
                self.assertEqual(notification.subject, expected_subject)
                self.assertIn(f"Q-{quote.pk}", notification.text_body)
                self.assertIn(self.service.name, notification.text_body)
                self.assertIn(f"Q-{quote.pk}", notification.html_body)
                self.assertIn(self.service.name, notification.html_body)
                self.assertEqual(notification.status, "sent")
                self.assertEqual(len(mail.outbox), 1)
                self.assertEqual(mail.outbox[0].subject, notification.subject)
                self.assertEqual(mail.outbox[0].body, notification.text_body)
                self.assertEqual(
                    mail.outbox[0].alternatives,
                    [(notification.html_body, "text/html")],
                )

                if status == "sent":
                    self.assertIn("5200.50", notification.text_body)
                    self.assertIn(
                        expiry.strftime("%B"),
                        notification.text_body,
                    )
                else:
                    self.assertNotIn("proposed_amount", notification.text_body)
                    self.assertNotIn(
                        "quote_valid_until",
                        notification.text_body,
                    )

    def test_event_key_prevents_duplicate_notification_records(self):
        quote = self.create_sent_quote()
        with self.captureOnCommitCallbacks(execute=True):
            change_quote_status(quote, "declined")

        notification = QuoteStatusNotification.objects.get(
            event_key=f"quote:{quote.pk}:status:declined",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                QuoteStatusNotification.objects.create(
                    quote=quote,
                    quote_reference=notification.quote_reference,
                    event=notification.event,
                    event_key=notification.event_key,
                    recipient=notification.recipient,
                    subject=notification.subject,
                    text_body=notification.text_body,
                    html_body=notification.html_body,
                )
        self.assertEqual(
            QuoteStatusNotification.objects.filter(
                event_key=notification.event_key,
            ).count(),
            1,
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="CLEANEX <test@example.com>",
    )
    def test_delivery_failure_does_not_undo_committed_transition(self):
        _user, customer = self.create_decision_customer("-delivery-failure")
        quote = self.create_sent_quote(customer=customer)

        with (
            patch(
                "apps_core.notifications.services.send_template_email",
                side_effect=RuntimeError("Sensitive backend details."),
            ),
            self.assertLogs(
                "apps_core.notifications.services",
                level="WARNING",
            ) as logs,
            self.captureOnCommitCallbacks(execute=True) as callbacks,
        ):
            change_quote_status(quote, "declined")

        quote.refresh_from_db()
        notification = QuoteStatusNotification.objects.get(
            event_key=f"quote:{quote.pk}:status:declined",
        )
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(quote.status, "declined")
        self.assertEqual(notification.status, "failed")
        self.assertEqual(notification.attempt_count, 1)
        self.assertIn("RuntimeError", notification.last_error)
        self.assertNotIn("Sensitive backend details", notification.last_error)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("Sensitive backend details", logs.output[0])

    def test_customer_accepts_own_sent_valid_quote_preserving_details(self):
        user, customer = self.create_decision_customer("-accept")
        quote = self.create_sent_quote(customer=customer)
        booking_count = Booking.objects.count()
        payment_count = Payment.objects.count()
        original_details = {
            field: getattr(quote, field)
            for field in (
                "full_name",
                "email",
                "phone",
                "customer_id",
                "service_id",
                "property_size",
                "rooms",
                "bathrooms",
                "extras",
                "address",
                "city",
                "estimated_price",
                "proposed_amount",
                "quote_valid_until",
                "quote_prepared_at",
                "quote_sent_at",
                "internal_notes",
                "created_at",
            )
        }

        result = accept_quote(quote_id=quote.pk, user=user)

        self.assertEqual(result.status, "accepted")
        quote.refresh_from_db()
        for field, value in original_details.items():
            self.assertEqual(getattr(quote, field), value, field)
        self.assertEqual(quote.status, "accepted")
        self.assertEqual(Booking.objects.count(), booking_count)
        self.assertEqual(Payment.objects.count(), payment_count)

    def test_customer_declines_own_sent_valid_quote_preserving_details(self):
        user, customer = self.create_decision_customer("-decline")
        quote = self.create_sent_quote(customer=customer)
        booking_count = Booking.objects.count()
        payment_count = Payment.objects.count()
        original_details = {
            field: getattr(quote, field)
            for field in (
                "full_name",
                "email",
                "phone",
                "customer_id",
                "service_id",
                "property_size",
                "rooms",
                "bathrooms",
                "extras",
                "address",
                "city",
                "estimated_price",
                "proposed_amount",
                "quote_valid_until",
                "quote_prepared_at",
                "quote_sent_at",
                "internal_notes",
                "created_at",
            )
        }

        result = decline_quote(quote_id=quote.pk, user=user)

        self.assertEqual(result.status, "declined")
        quote.refresh_from_db()
        for field, value in original_details.items():
            self.assertEqual(getattr(quote, field), value, field)
        self.assertEqual(quote.status, "declined")
        self.assertEqual(Booking.objects.count(), booking_count)
        self.assertEqual(Payment.objects.count(), payment_count)

    def test_unauthenticated_user_cannot_decide_quote(self):
        quote = self.create_sent_quote()

        with self.assertRaises(ValidationError):
            accept_quote(quote_id=quote.pk, user=None)

        quote.refresh_from_db()
        self.assertEqual(quote.status, "sent")

    def test_inactive_customer_cannot_accept_or_decline_quote(self):
        user, customer = self.create_decision_customer("-inactive")
        user.is_active = False
        user.save(update_fields=["is_active"])
        quotes = [
            self.create_sent_quote(
                customer=customer,
                full_name=f"Inactive Customer Quote {index}",
            )
            for index in range(2)
        ]
        original_values = [
            {
                field: getattr(quote, field)
                for field in (
                    "status",
                    "updated_at",
                    "proposed_amount",
                    "quote_valid_until",
                    "quote_sent_at",
                    "full_name",
                    "email",
                    "phone",
                    "customer_id",
                )
            }
            for quote in quotes
        ]

        for operation, quote in zip(
            (accept_quote, decline_quote),
            quotes,
        ):
            with self.subTest(operation=operation.__name__):
                with self.assertRaisesMessage(
                    ValidationError,
                    "The customer account must be active.",
                ):
                    operation(quote_id=quote.pk, user=user)

        for quote, expected_values in zip(quotes, original_values):
            quote.refresh_from_db()
            for field, value in expected_values.items():
                self.assertEqual(getattr(quote, field), value, field)

    def test_customer_cannot_decide_another_customers_quote(self):
        user, _customer = self.create_decision_customer("-owner")
        _other_user, other_customer = self.create_decision_customer("-other")
        quote = self.create_sent_quote(customer=other_customer)
        original_updated_at = quote.updated_at
        original_amount = quote.proposed_amount

        with self.assertRaises(ValidationError):
            accept_quote(quote_id=quote.pk, user=user)

        quote.refresh_from_db()
        self.assertEqual(quote.status, "sent")
        self.assertEqual(quote.customer_id, other_customer.pk)
        self.assertEqual(quote.proposed_amount, original_amount)
        self.assertEqual(quote.updated_at, original_updated_at)

    def test_user_without_customer_profile_cannot_decide_quote(self):
        user = User.objects.create_user(
            username="decision-without-customer",
            email="decision-no-customer@example.com",
            password="Strong-Decision-Password-984!",
        )
        quote = self.create_sent_quote()

        with self.assertRaises(ValidationError):
            accept_quote(quote_id=quote.pk, user=user)

        quote.refresh_from_db()
        self.assertEqual(quote.status, "sent")

    def test_unlinked_guest_quote_is_not_decidable_with_matching_email(self):
        user, _customer = self.create_decision_customer("-guest")
        quote = self.create_sent_quote(
            email=user.email.upper(),
            customer=None,
        )

        with self.assertRaises(ValidationError):
            decline_quote(quote_id=quote.pk, user=user)

        quote.refresh_from_db()
        self.assertIsNone(quote.customer_id)
        self.assertEqual(quote.status, "sent")

    def test_prepared_and_pending_quotes_cannot_be_decided(self):
        user, customer = self.create_decision_customer("-unsent")
        for status in ("prepared", "pending"):
            with self.subTest(status=status):
                quote = self.create_sent_quote(
                    customer=customer,
                    status=status,
                    quote_sent_at=None,
                )
                for operation in (accept_quote, decline_quote):
                    with self.subTest(operation=operation.__name__):
                        with self.assertRaises(ValidationError):
                            operation(quote_id=quote.pk, user=user)
                quote.refresh_from_db()
                self.assertEqual(quote.status, status)

    def test_expired_quote_cannot_be_accepted_or_declined(self):
        user, customer = self.create_decision_customer("-expired")
        for operation in (accept_quote, decline_quote):
            with self.subTest(operation=operation.__name__):
                quote = self.create_sent_quote(
                    customer=customer,
                    quote_valid_until=timezone.localdate() - timedelta(days=1),
                )
                with self.assertRaises(ValidationError):
                    operation(quote_id=quote.pk, user=user)
                quote.refresh_from_db()
                self.assertEqual(quote.status, "sent")

    @patch("apps_core.quotes.services.timezone.localdate")
    def test_quote_remains_valid_on_its_expiry_date(self, mock_localdate):
        expiry_date = date(2026, 11, 4)
        mock_localdate.return_value = expiry_date
        user, customer = self.create_decision_customer("-expiry-day")
        quote = self.create_sent_quote(
            customer=customer,
            quote_valid_until=expiry_date,
        )

        result = accept_quote(quote_id=quote.pk, user=user)

        self.assertEqual(result.status, "accepted")

    def test_missing_sent_timestamp_or_expiry_date_rejects_decision(self):
        user, customer = self.create_decision_customer("-missing-fields")
        for overrides in (
            {"quote_sent_at": None},
            {"quote_valid_until": None},
        ):
            with self.subTest(overrides=overrides):
                quote = self.create_sent_quote(
                    customer=customer,
                    **overrides,
                )
                with self.assertRaises(ValidationError):
                    accept_quote(quote_id=quote.pk, user=user)
                quote.refresh_from_db()
                self.assertEqual(quote.status, "sent")

    def test_terminal_quotes_cannot_be_accepted_or_declined_again(self):
        user, customer = self.create_decision_customer("-terminal")
        for status in ("accepted", "declined", "cancelled", "expired"):
            with self.subTest(status=status):
                quote = self.create_sent_quote(
                    customer=customer,
                    status=status,
                )
                for operation in (accept_quote, decline_quote):
                    with self.subTest(operation=operation.__name__):
                        with self.assertRaises(ValidationError):
                            operation(quote_id=quote.pk, user=user)
                quote.refresh_from_db()
                self.assertEqual(quote.status, status)

    def test_conflicting_decisions_cannot_both_succeed(self):
        user, customer = self.create_decision_customer("-conflict")
        quote = self.create_sent_quote(customer=customer)

        accept_quote(quote_id=quote.pk, user=user)

        with self.assertRaises(ValidationError):
            decline_quote(quote_id=quote.pk, user=user)

        quote.refresh_from_db()
        self.assertEqual(quote.status, "accepted")

    def test_valid_status_transitions_and_terminal_states(self):
        quote = self.create_quote()
        change_quote_status(quote, "under_review")
        quote.refresh_from_db()
        self.assertEqual(quote.status, "under_review")

        prepare_quotation(
            quote,
            Decimal("5000.00"),
            timezone.localdate() + timedelta(days=7),
        )
        quote.refresh_from_db()
        self.assertEqual(quote.status, "prepared")

        change_quote_status(quote, "sent")
        quote.refresh_from_db()
        self.assertEqual(quote.status, "sent")
        self.assertIsNotNone(quote.quote_sent_at)

        change_quote_status(quote, "accepted")
        quote.refresh_from_db()
        self.assertEqual(quote.status, "accepted")
        with self.assertRaises(ValidationError):
            change_quote_status(quote, "cancelled")

    def test_allowed_cancellation_and_sent_outcomes(self):
        transitions = (
            ("pending", "cancelled"),
            ("under_review", "cancelled"),
            ("prepared", "cancelled"),
            ("sent", "declined"),
            ("sent", "expired"),
            ("sent", "cancelled"),
        )
        for current_status, next_status in transitions:
            with self.subTest(
                current_status=current_status,
                next_status=next_status,
            ):
                quote = self.create_quote(status=current_status)
                if current_status in {"prepared", "sent"}:
                    quote.proposed_amount = Decimal("5000.00")
                    quote.quote_valid_until = (
                        timezone.localdate() + timedelta(days=7)
                    )
                    quote.quote_prepared_at = timezone.now()
                    quote.save(
                        update_fields=[
                            "proposed_amount",
                            "quote_valid_until",
                            "quote_prepared_at",
                        ]
                    )

                change_quote_status(quote, next_status)
                quote.refresh_from_db()
                self.assertEqual(quote.status, next_status)

    def test_invalid_status_transitions_are_rejected(self):
        quote = self.create_quote()

        for new_status in ("prepared", "sent", "accepted", "not-a-status"):
            with self.subTest(new_status=new_status):
                with self.assertRaises(ValidationError):
                    change_quote_status(quote, new_status)

        self.assertEqual(quote.status, "pending")

    def test_all_terminal_statuses_reject_further_transitions(self):
        terminal_statuses = ("accepted", "declined", "expired", "cancelled")
        possible_statuses = {
            status for status, _label in QuoteRequest.STATUS_CHOICES
        }

        for terminal_status in terminal_statuses:
            with self.subTest(terminal_status=terminal_status):
                quote = self.create_quote(status=terminal_status)
                for new_status in possible_statuses:
                    with self.subTest(new_status=new_status):
                        with self.assertRaises(ValidationError):
                            change_quote_status(quote, new_status)
                quote.refresh_from_db()
                self.assertEqual(quote.status, terminal_status)

    def test_under_review_cannot_be_marked_prepared_without_quotation_data(self):
        quote = self.create_quote(status="under_review")

        with self.assertRaises(ValidationError):
            change_quote_status(quote, "prepared")

    def test_prepare_quotation_rejects_non_positive_amounts(self):
        quote = self.create_quote(status="under_review")
        valid_until = timezone.localdate() + timedelta(days=1)

        for amount in (Decimal("0"), Decimal("-1")):
            with self.subTest(amount=amount):
                with self.assertRaises(ValidationError):
                    prepare_quotation(quote, amount, valid_until)

    def test_prepare_quotation_rejects_invalid_precision_and_amount_range(self):
        quote = self.create_quote(status="under_review")
        valid_until = timezone.localdate() + timedelta(days=1)

        for amount in (
            Decimal("1.001"),
            Decimal("100000000.00"),
            Decimal("NaN"),
        ):
            with self.subTest(amount=amount):
                with self.assertRaises(ValidationError):
                    prepare_quotation(quote, amount, valid_until)

    def test_prepare_quotation_rejects_today_and_past_expiry_dates(self):
        quote = self.create_quote(status="under_review")

        for valid_until in (
            timezone.localdate(),
            timezone.localdate() - timedelta(days=1),
        ):
            with self.subTest(valid_until=valid_until):
                with self.assertRaises(ValidationError):
                    prepare_quotation(quote, Decimal("5000.00"), valid_until)

    def test_prepare_quotation_saves_staff_fields_and_does_not_create_booking(self):
        quote = self.create_quote(status="under_review")
        quote.estimated_price = Decimal("2950.25")
        quote.save(update_fields=["estimated_price"])
        valid_until = timezone.localdate() + timedelta(days=7)

        result = prepare_quotation(quote, "5000.00", valid_until)
        result.refresh_from_db()

        self.assertEqual(result.status, "prepared")
        self.assertEqual(result.proposed_amount, Decimal("5000.00"))
        self.assertEqual(result.quote_valid_until, valid_until)
        self.assertIsNotNone(result.quote_prepared_at)
        self.assertIsNone(result.quote_sent_at)
        self.assertEqual(result.estimated_price, Decimal("2950.25"))
        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)

    def test_preparation_only_allowed_under_review(self):
        quote = self.create_quote(status="pending")

        with self.assertRaises(ValidationError):
            prepare_quotation(
                quote,
                Decimal("5000.00"),
                timezone.localdate() + timedelta(days=1),
            )


class QuoteRequestViewTests(QuoteFixtureMixin, TestCase):
    def setUp(self):
        cache.clear()
        self.service = self.create_service()
        self.quote_url = reverse("quote")
        self.success_url = reverse("quote_success")

    def post_data(self, **overrides):
        values = {
            "full_name": "Amina Customer",
            "email": "AMINA@example.com",
            "phone": "+254 700 000 000",
            "service": str(self.service.pk),
            "property_size": "100",
            "rooms": "3",
            "bathrooms": "2",
            "extras": "",
            "address": "  12 Green Road  ",
            "city": " Nairobi ",
            "preferred_date": "",
            "preferred_time": "",
        }
        values.update(overrides)
        return values

    def test_get_displays_form_with_active_services_only(self):
        inactive_service = self.create_service(
            name="Inactive Quote Service",
            slug="inactive-quote-service",
            is_active=False,
        )

        response = self.client.get(self.quote_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "pages/get_quote.html")
        form = response.context["form"]
        self.assertIsInstance(form, QuoteRequestForm)
        offered_service_ids = {
            service.pk for service in form.fields["service"].queryset
        }
        self.assertEqual(offered_service_ids, {self.service.pk})
        self.assertNotIn(inactive_service.pk, offered_service_ids)

    def test_valid_guest_submission_persists_pending_request_and_redirects(self):
        response = self.client.post(
            self.quote_url,
            self.post_data(),
        )

        self.assertRedirects(response, self.success_url)
        self.assertEqual(QuoteRequest.objects.count(), 1)
        quote = QuoteRequest.objects.get()
        self.assertEqual(quote.status, "pending")
        self.assertIsNone(quote.customer)
        self.assertEqual(quote.service, self.service)
        self.assertEqual(quote.full_name, "Amina Customer")
        self.assertEqual(quote.email, "amina@example.com")
        self.assertEqual(quote.phone, "+254 700 000 000")
        self.assertEqual(quote.address, "12 Green Road")
        self.assertEqual(quote.city, "Nairobi")
        self.assertEqual(quote.property_size, 100)
        self.assertEqual(quote.rooms, 3)
        self.assertEqual(quote.bathrooms, 2)
        self.assertEqual(quote.estimated_price, Decimal("2950.25"))
        self.assertEqual(Customer.objects.count(), 0)
        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)

    def test_authenticated_submission_uses_existing_customer_profile(self):
        user = User.objects.create_user(
            username="quote-view-customer",
            email="account@example.com",
        )
        customer = Customer.objects.create(
            user=user,
            full_name="Existing Customer Profile",
            email="account@example.com",
            phone="0711111111",
            address="Existing Customer Address",
            city="Mombasa",
        )
        self.client.force_login(user)

        response = self.client.post(
            self.quote_url,
            self.post_data(
                full_name="Submitted Contact Snapshot",
                email="different-contact@example.com",
                phone="0722222222",
            ),
        )

        self.assertRedirects(response, self.success_url)
        quote = QuoteRequest.objects.get()
        self.assertEqual(quote.customer_id, customer.pk)
        self.assertEqual(quote.full_name, "Submitted Contact Snapshot")
        self.assertEqual(quote.email, "different-contact@example.com")
        self.assertEqual(quote.phone, "0722222222")
        customer.refresh_from_db()
        self.assertEqual(customer.full_name, "Existing Customer Profile")
        self.assertEqual(customer.phone, "0711111111")
        self.assertEqual(customer.address, "Existing Customer Address")

    def test_authenticated_submission_without_profile_does_not_create_customer(self):
        user = User.objects.create_user(
            username="quote-view-no-profile",
            email="no-profile@example.com",
        )
        self.client.force_login(user)

        response = self.client.post(
            self.quote_url,
            self.post_data(),
        )

        self.assertRedirects(response, self.success_url)
        quote = QuoteRequest.objects.get()
        self.assertIsNone(quote.customer)
        self.assertFalse(Customer.objects.filter(user=user).exists())

    def test_invalid_submission_redisplays_errors_without_creating_request(self):
        response = self.client.post(
            self.quote_url,
            self.post_data(address="   "),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "pages/get_quote.html")
        self.assertIn("address", response.context["form"].errors)
        self.assertContains(response, "This field is required.")
        self.assertEqual(QuoteRequest.objects.count(), 0)

    def test_missing_malformed_unknown_and_inactive_services_are_rejected(self):
        inactive_service = self.create_service(
            name="Inactive Quote Service",
            slug="inactive-quote-service",
            is_active=False,
        )
        invalid_service_values = (
            "",
            "not-a-service-id",
            "999999",
            str(inactive_service.pk),
        )

        for service_value in invalid_service_values:
            with self.subTest(service=service_value):
                response = self.client.post(
                    self.quote_url,
                    self.post_data(service=service_value),
                )

                self.assertEqual(response.status_code, 200)
                self.assertIn("service", response.context["form"].errors)
                self.assertEqual(QuoteRequest.objects.count(), 0)

    def test_success_page_get_and_refresh_do_not_resubmit_request(self):
        self.client.post(self.quote_url, self.post_data())
        self.assertEqual(QuoteRequest.objects.count(), 1)

        response = self.client.get(self.success_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "pages/quote_success.html")
        self.assertContains(response, "Your quote request has been received")
        self.assertEqual(QuoteRequest.objects.count(), 1)

        refresh_response = self.client.get(self.success_url)

        self.assertEqual(refresh_response.status_code, 200)
        self.assertEqual(QuoteRequest.objects.count(), 1)


class CustomerQuoteDecisionViewTests(QuoteFixtureMixin, TestCase):
    def setUp(self):
        self.service = self.create_service()
        self.user = User.objects.create_user(
            username="quote-decision-view-customer",
            email="quote-decision-view@example.com",
            password="Strong-Decision-View-Password-984!",
        )
        self.customer = Customer.objects.create(
            user=self.user,
            full_name="Decision View Customer",
            email=self.user.email,
            phone="0712345678",
            address="Customer Address",
            city="Nairobi",
        )
        self.client.force_login(self.user)

    def create_sent_quote(self, customer="current", **overrides):
        values = {
            "full_name": "Decision View Quote",
            "email": "quote-snapshot@example.com",
            "phone": "0722222222",
            "service": self.service,
            "customer": (
                self.customer if customer == "current" else customer
            ),
            "property_size": 120,
            "rooms": 3,
            "bathrooms": 2,
            "extras": "",
            "address": "Quote Address",
            "city": "Nairobi",
            "status": "sent",
            "proposed_amount": Decimal("5100.00"),
            "quote_valid_until": timezone.localdate() + timedelta(days=5),
            "quote_prepared_at": timezone.now() - timedelta(days=1),
            "quote_sent_at": timezone.now(),
        }
        values.update(overrides)
        return QuoteRequest.objects.create(**values)

    def decision_url(self, action, quote):
        return reverse(
            f"{action}_customer_quote",
            kwargs={"quote_id": quote.pk},
        )

    def test_anonymous_users_are_redirected_from_both_decision_endpoints(self):
        quote = self.create_sent_quote()
        self.client.logout()

        for action in ("accept", "decline"):
            with self.subTest(action=action):
                for method in (self.client.get, self.client.post):
                    with self.subTest(method=method.__name__):
                        response = method(self.decision_url(action, quote))
                        self.assertRedirects(
                            response,
                            f"{reverse('login')}?next={self.decision_url(action, quote)}",
                        )

    def test_authenticated_get_requests_return_405_without_changing_status(self):
        quote = self.create_sent_quote()

        for action in ("accept", "decline"):
            with self.subTest(action=action):
                response = self.client.get(self.decision_url(action, quote))
                self.assertEqual(response.status_code, 405)
                quote.refresh_from_db()
                self.assertEqual(quote.status, "sent")

    def test_valid_decision_posts_call_service_redirect_and_refresh_history(self):
        for action, expected_status, expected_message in (
            ("accept", "accepted", "Quotation accepted."),
            ("decline", "declined", "Quotation declined."),
        ):
            with self.subTest(action=action):
                quote = self.create_sent_quote()
                booking_count = Booking.objects.count()
                payment_count = Payment.objects.count()
                service_name = (
                    "apps_core.quotes.views.accept_quote"
                    if action == "accept"
                    else "apps_core.quotes.views.decline_quote"
                )
                with (
                    patch(service_name, wraps=(
                        accept_quote if action == "accept" else decline_quote
                    )) as decision_service,
                    patch(
                        "apps_core.payments.integrations.mpesa."
                        "MpesaIntegration.initiate_stk_push"
                    ) as mpesa_request,
                    patch(
                        "apps_core.notifications.services.send_template_email",
                        return_value=1,
                    ) as notification_sender,
                    self.captureOnCommitCallbacks(execute=True),
                ):
                    response = self.client.post(
                        self.decision_url(action, quote),
                        follow=True,
                    )

                decision_service.assert_called_once_with(
                    quote_id=quote.pk,
                    user=self.user,
                )
                mpesa_request.assert_not_called()
                if action == "accept":
                    notification_sender.assert_not_called()
                else:
                    notification_sender.assert_called_once()
                self.assertEqual(
                    response.redirect_chain,
                    [(reverse("customer_quote_history"), 302)],
                )
                self.assertContains(response, expected_message)
                self.assertContains(response, dict(QuoteRequest.STATUS_CHOICES)[expected_status])
                quote.refresh_from_db()
                self.assertEqual(quote.status, expected_status)
                self.assertEqual(Booking.objects.count(), booking_count)
                self.assertEqual(Payment.objects.count(), payment_count)

    def test_another_customers_quote_cannot_be_changed(self):
        other_user = User.objects.create_user(
            username="quote-decision-view-other",
            email="quote-decision-view-other@example.com",
        )
        other_customer = Customer.objects.create(
            user=other_user,
            full_name="Other Customer",
            email=other_user.email,
            phone="0711111111",
            address="Other Address",
            city="Nairobi",
        )
        quote = self.create_sent_quote(customer=other_customer)

        response = self.client.post(self.decision_url("accept", quote), follow=True)

        self.assertContains(
            response,
            "not linked to your customer profile",
        )
        quote.refresh_from_db()
        self.assertEqual(quote.status, "sent")
        self.assertEqual(quote.customer_id, other_customer.pk)

    def test_unlinked_guest_quote_cannot_be_changed_even_with_matching_email(self):
        quote = self.create_sent_quote(
            customer=None,
            email=self.user.email.upper(),
        )

        response = self.client.post(self.decision_url("decline", quote), follow=True)

        self.assertContains(
            response,
            "not linked to your customer profile",
        )
        quote.refresh_from_db()
        self.assertIsNone(quote.customer_id)
        self.assertEqual(quote.status, "sent")

    def test_customer_without_profile_gets_error_and_quote_is_unchanged(self):
        no_profile_user = User.objects.create_user(
            username="quote-decision-view-no-profile",
            email="quote-decision-view-no-profile@example.com",
        )
        quote = self.create_sent_quote()
        self.client.force_login(no_profile_user)

        response = self.client.post(self.decision_url("accept", quote), follow=True)

        self.assertContains(
            response,
            "not linked to your customer profile",
            status_code=409,
        )
        quote.refresh_from_db()
        self.assertEqual(quote.status, "sent")

    def test_invalid_quote_id_shows_safe_error_and_redirects(self):
        response = self.client.post(
            reverse("accept_customer_quote", kwargs={"quote_id": 999999}),
            follow=True,
        )

        self.assertEqual(
            response.redirect_chain,
            [(reverse("customer_quote_history"), 302)],
        )
        self.assertContains(response, "The quotation could not be found.")

    def test_service_validation_errors_are_messaged_without_changing_quote(self):
        invalid_cases = (
            ("expired", {"quote_valid_until": timezone.localdate() - timedelta(days=1)}),
            ("pending", {"status": "pending"}),
            ("prepared", {"status": "prepared", "quote_sent_at": None}),
            ("accepted", {"status": "accepted"}),
            ("declined", {"status": "declined"}),
            ("cancelled", {"status": "cancelled"}),
            ("expired-status", {"status": "expired"}),
        )
        for label, overrides in invalid_cases:
            with self.subTest(case=label):
                for action, verb in (
                    ("accept", "accepted"),
                    ("decline", "declined"),
                ):
                    with self.subTest(action=action):
                        quote = self.create_sent_quote(**overrides)
                        response = self.client.post(
                            self.decision_url(action, quote),
                            follow=True,
                        )

                        self.assertContains(
                            response,
                            f"Your quotation could not be {verb}:",
                        )
                        quote.refresh_from_db()
                        self.assertEqual(
                            quote.status,
                            overrides.get("status", "sent"),
                        )

    def test_decision_forms_are_csrf_protected_and_only_rendered_for_sent_quotes(self):
        sent_quote = self.create_sent_quote()
        pending_quote = self.create_sent_quote(status="pending")
        response = self.client.get(reverse("customer_quote_history"))

        self.assertContains(
            response,
            reverse(
                "accept_customer_quote",
                kwargs={"quote_id": sent_quote.pk},
            ),
        )
        self.assertContains(
            response,
            reverse(
                "decline_customer_quote",
                kwargs={"quote_id": sent_quote.pk},
            ),
        )
        self.assertNotContains(
            response,
            reverse(
                "accept_customer_quote",
                kwargs={"quote_id": pending_quote.pk},
            ),
        )
        self.assertNotContains(
            response,
            reverse(
                "decline_customer_quote",
                kwargs={"quote_id": pending_quote.pk},
            ),
        )
        self.assertEqual(response.content.count(b'name="csrfmiddlewaretoken"'), 3)

        csrf_client = self.client_class(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        denied = csrf_client.post(
            reverse(
                "accept_customer_quote",
                kwargs={"quote_id": sent_quote.pk},
            )
        )
        self.assertEqual(denied.status_code, 403)
        sent_quote.refresh_from_db()
        self.assertEqual(sent_quote.status, "sent")


class QuoteRequestAdminTests(QuoteFixtureMixin, TestCase):
    def setUp(self):
        self.service = self.create_service()
        self.staff = User.objects.create_superuser(
            username="quote-admin",
            email="quote-admin@example.com",
            password="admin-password",
        )
        self.client.force_login(self.staff)

    def create_quote(self, status="pending", **overrides):
        values = {
            "full_name": "Quote Lifecycle Customer",
            "email": "lifecycle@example.com",
            "phone": "0712345678",
            "service": self.service,
            "property_size": 100,
            "rooms": 3,
            "bathrooms": 2,
            "address": "12 Green Road",
            "city": "Nairobi",
            "status": status,
        }
        values.update(overrides)
        return QuoteRequest.objects.create(**values)

    def change_url(self, quote):
        return reverse("admin:quotes_quoterequest_change", args=(quote.pk,))

    def test_changelist_searches_quote_id_snapshots_and_linked_customer(self):
        customer = Customer.objects.create(
            full_name="Linked Customer Search",
            email="linked-customer@example.com",
            phone="0700001111",
            address="Customer Road",
            city="Nairobi",
        )
        linked_quote = self.create_quote(
            customer=customer,
            full_name="Snapshot Name",
            email="snapshot@example.com",
            phone="0711112222",
        )
        unrelated_quote = self.create_quote(
            full_name="Different Contact",
            email="different@example.com",
            phone="0722223333",
        )
        changelist = reverse("admin:quotes_quoterequest_changelist")

        for search in (
            "Linked Customer Search",
            "linked-customer@example.com",
            "0700001111",
            "Snapshot Name",
            "snapshot@example.com",
            "0711112222",
            str(linked_quote.pk),
        ):
            with self.subTest(search=search):
                response = self.client.get(changelist, {"q": search})
                self.assertEqual(response.status_code, 200)
                result_ids = {
                    quote.pk
                    for quote in response.context["cl"].result_list
                }
                self.assertIn(linked_quote.pk, result_ids)
                self.assertNotIn(unrelated_quote.pk, result_ids)

    def test_changelist_filters_by_status_service_and_submission_date(self):
        matching = self.create_quote(status="under_review")
        other_status = self.create_quote(status="pending")
        other_service = self.create_service(
            name="Second Quote Service",
            slug="second-quote-service",
        )
        other_service_quote = self.create_quote(
            status="under_review",
            service=other_service,
        )
        old_date = timezone.now() - timedelta(days=400)
        QuoteRequest.objects.filter(pk=other_status.pk).update(
            created_at=old_date
        )
        changelist = reverse("admin:quotes_quoterequest_changelist")

        response = self.client.get(
            changelist,
            {
                "status__exact": "under_review",
                "service__id__exact": str(self.service.pk),
                "created_at__year": str(timezone.localdate().year),
            },
        )

        self.assertEqual(response.status_code, 200)
        result_ids = {
            quote.pk for quote in response.context["cl"].result_list
        }
        self.assertEqual(result_ids, {matching.pk})
        self.assertNotIn(other_status.pk, result_ids)
        self.assertNotIn(other_service_quote.pk, result_ids)
        list_display = admin.site._registry[QuoteRequest].list_display
        self.assertIn("estimated_price", list_display)
        self.assertIn("proposed_amount", list_display)

    def test_customer_submitted_fields_and_prepared_quotation_are_read_only(self):
        quote = self.create_quote()
        quote_admin = admin.site._registry[QuoteRequest]
        request = RequestFactory().get("/")
        request.user = self.staff

        readonly_fields = quote_admin.get_readonly_fields(
            request,
            quote,
        )

        for field in (
            "full_name",
            "email",
            "phone",
            "customer",
            "service",
            "property_size",
            "estimated_price",
            "proposed_amount",
            "quote_valid_until",
        ):
            self.assertIn(field, readonly_fields)
        review_quote = self.create_quote(status="under_review")
        review_readonly_fields = quote_admin.get_readonly_fields(
            request,
            review_quote,
        )
        self.assertNotIn("proposed_amount", review_readonly_fields)
        self.assertNotIn("quote_valid_until", review_readonly_fields)
        self.assertFalse(quote_admin.has_add_permission(request))
        self.assertFalse(quote_admin.has_delete_permission(request))
        self.assertIsNone(quote_admin.actions)

    def test_staff_fieldsets_have_no_duplicate_fields_and_form_clean_is_read_only(self):
        quote_admin = admin.site._registry[QuoteRequest]
        field_names = [
            field
            for _title, options in quote_admin.fieldsets
            for field in options["fields"]
        ]
        self.assertEqual(len(field_names), len(set(field_names)))

        quote = self.create_quote(status="under_review")
        form = QuoteRequestAdminForm(
            data={
                "status": "under_review",
                "internal_notes": "Validation should not save this.",
                "proposed_amount": "5000.00",
                "quote_valid_until": (
                    timezone.localdate() + timedelta(days=7)
                ).isoformat(),
            },
            instance=quote,
        )
        self.assertIn("Invalid transitions are rejected", form.fields["status"].help_text)
        self.assertIn("positive amount", form.fields["proposed_amount"].help_text)
        with (
            patch("apps_core.quotes.admin.prepare_quotation") as prepare,
            patch("apps_core.quotes.admin.change_quote_status") as change,
        ):
            self.assertTrue(form.is_valid(), form.errors)

        prepare.assert_not_called()
        change.assert_not_called()
        quote.refresh_from_db()
        self.assertEqual(quote.status, "under_review")
        self.assertIsNone(quote.proposed_amount)
        self.assertEqual(quote.internal_notes, "")

    def test_admin_status_change_uses_quote_lifecycle(self):
        quote = self.create_quote()

        response = self.client.post(
            self.change_url(quote),
            {
                "status": "under_review",
                "internal_notes": "Review started.",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        quote.refresh_from_db()
        self.assertEqual(quote.status, "under_review")
        self.assertEqual(quote.internal_notes, "Review started.")

    def test_admin_preparation_and_invalid_transitions_are_form_errors(self):
        quote = self.create_quote(status="under_review")
        expiry = timezone.localdate() + timedelta(days=7)

        valid_response = self.client.post(
            self.change_url(quote),
            {
                "status": "under_review",
                "internal_notes": "Amount approved for preparation.",
                "proposed_amount": "5000.00",
                "quote_valid_until": expiry.isoformat(),
                "_save": "Save",
            },
        )

        self.assertEqual(valid_response.status_code, 302)
        quote.refresh_from_db()
        self.assertEqual(quote.status, "prepared")
        self.assertEqual(quote.proposed_amount, Decimal("5000.00"))
        self.assertEqual(quote.quote_valid_until, expiry)
        self.assertIsNotNone(quote.quote_prepared_at)

        sent_response = self.client.post(
            self.change_url(quote),
            {
                "status": "sent",
                "internal_notes": "Quotation sent.",
                "_save": "Save",
            },
        )
        self.assertEqual(sent_response.status_code, 302)
        quote.refresh_from_db()
        self.assertEqual(quote.status, "sent")
        self.assertIsNotNone(quote.quote_sent_at)

        pending_quote = self.create_quote(status="pending")
        invalid_response = self.client.post(
            self.change_url(pending_quote),
            {
                "status": "prepared",
                "internal_notes": "Must not bypass review.",
                "_save": "Save",
            },
        )

        self.assertEqual(
            invalid_response.status_code,
            200,
            getattr(invalid_response, "url", ""),
        )
        self.assertContains(invalid_response, "cannot be changed")
        pending_quote.refresh_from_db()
        self.assertEqual(pending_quote.status, "pending")
        self.assertEqual(pending_quote.internal_notes, "")

    def test_invalid_preparation_is_rejected_without_persisting_staff_changes(self):
        quote = self.create_quote(status="under_review")

        response = self.client.post(
            self.change_url(quote),
            {
                "status": "under_review",
                "internal_notes": "Should not persist on invalid prepare.",
                "proposed_amount": "0.00",
                "quote_valid_until": (
                    timezone.localdate() + timedelta(days=7)
                ).isoformat(),
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 200, getattr(response, "url", ""))
        self.assertContains(response, "must be positive")
        quote.refresh_from_db()
        self.assertEqual(quote.status, "under_review")
        self.assertIsNone(quote.proposed_amount)
        self.assertEqual(quote.internal_notes, "")

    def test_cancellation_with_quotation_edits_is_rejected_without_saving(self):
        quote = self.create_quote(status="under_review")
        expiry = timezone.localdate() + timedelta(days=7)

        response = self.client.post(
            self.change_url(quote),
            {
                "status": "cancelled",
                "internal_notes": "Do not save this conflicting update.",
                "proposed_amount": "5000.00",
                "quote_valid_until": expiry.isoformat(),
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "A request cannot be cancelled while quotation details are being changed.",
        )
        quote.refresh_from_db()
        self.assertEqual(quote.status, "under_review")
        self.assertEqual(quote.internal_notes, "")
        self.assertIsNone(quote.proposed_amount)
        self.assertIsNone(quote.quote_valid_until)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="CLEANEX <test@example.com>",
)
class QuoteNotificationTransactionTests(QuoteFixtureMixin, TransactionTestCase):
    def setUp(self):
        self.service = self.create_service()
        user = User.objects.create_user(
            username="quote-transaction-customer",
            email="account@example.com",
            password="test-password",
        )
        self.customer = Customer.objects.create(
            user=user,
            full_name="Transaction Customer",
            email="account@example.com",
            phone="0712345678",
            address="Customer Address",
            city="Nairobi",
        )
        self.quote = QuoteRequest.objects.create(
            full_name="Quotation Snapshot",
            email="quote-recipient@example.com",
            phone="0722222222",
            service=self.service,
            customer=self.customer,
            property_size=150,
            rooms=4,
            bathrooms=2,
            address="Saved Quote Address",
            city="Nairobi",
            status="sent",
            proposed_amount=Decimal("5200.50"),
            quote_valid_until=timezone.localdate() + timedelta(days=3),
            quote_prepared_at=timezone.now() - timedelta(days=1),
            quote_sent_at=timezone.now() - timedelta(hours=2),
        )

    def test_notification_is_sent_only_after_transaction_commit(self):
        mail.outbox.clear()

        with transaction.atomic():
            change_quote_status(self.quote, "declined")
            self.assertEqual(mail.outbox, [])
            notification = QuoteStatusNotification.objects.get(
                event_key=f"quote:{self.quote.pk}:status:declined",
            )
            self.assertEqual(notification.status, "pending")

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.quote.email])
        notification.refresh_from_db()
        self.assertEqual(notification.status, "sent")
        self.assertEqual(notification.attempt_count, 1)
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, "declined")

    def test_rolled_back_transition_does_not_send_notification(self):
        mail.outbox.clear()

        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                change_quote_status(self.quote, "declined")
                raise RuntimeError("Roll back quotation transition.")

        self.assertEqual(mail.outbox, [])
        self.assertFalse(
            QuoteStatusNotification.objects.filter(
                quote=self.quote,
            ).exists()
        )
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, "sent")

    def test_delivery_failure_after_commit_keeps_transition_committed(self):
        with (
            patch(
                "apps_core.notifications.services.send_template_email",
                side_effect=RuntimeError("Email backend unavailable."),
            ),
            self.assertLogs(
                "apps_core.notifications.services",
                level="WARNING",
            ),
        ):
            with transaction.atomic():
                change_quote_status(self.quote, "declined")

        self.quote.refresh_from_db()
        notification = QuoteStatusNotification.objects.get(
            event_key=f"quote:{self.quote.pk}:status:declined",
        )
        self.assertEqual(self.quote.status, "declined")
        self.assertEqual(notification.status, "failed")
