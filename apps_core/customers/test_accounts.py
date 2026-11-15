import re
from datetime import date, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import resolve, reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils import timezone

from apps_core.bookings.models import Booking
from apps_core.customers.models import (
    Customer,
    PendingCustomerRegistration,
)
from apps_core.customers.services import (
    CustomerAccountError,
    get_customer_dashboard_data,
    verify_customer_account,
)
from apps_core.payments.models import Payment
from apps_core.quotes.models import QuoteRequest
from apps_core.quotes.services import link_guest_quote_requests
from apps_core.services.models import Service
from unittest.mock import patch

User = get_user_model()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class CustomerAccountTests(TestCase):
    password = "Strong-Account-Password-984!"

    def setUp(self):
        cache.clear()

    def registration_data(self, **overrides):
        data = {
            "first_name": "Amina",
            "last_name": "Customer",
            "email": "amina@example.com",
            "phone": "+254700000000",
            "password1": self.password,
            "password2": self.password,
        }
        data.update(overrides)
        return data

    def register_and_get_verification_path(self, **overrides):
        response = self.client.post(
            reverse("customer_register"),
            self.registration_data(**overrides),
        )
        self.assertRedirects(
            response,
            reverse("customer_registration_pending"),
        )
        self.assertEqual(len(mail.outbox), 1)
        match = re.search(
            r"http://testserver(/account/verify/\S+)",
            mail.outbox[-1].body,
        )
        self.assertIsNotNone(match)
        return match.group(1)

    def verification_path_for(self, user, token):
        return reverse(
            "customer_verify",
            kwargs={
                "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
                "token": token,
            },
        )

    def test_generated_verification_url_resolves_and_verifies_account(self):
        verification_path = self.register_and_get_verification_path()
        user = User.objects.get(email="amina@example.com")
        match = resolve(verification_path)
        self.assertEqual(match.url_name, "customer_verify")
        self.assertEqual(
            match.kwargs["uidb64"],
            urlsafe_base64_encode(force_bytes(user.pk)),
        )
        self.assertTrue(match.kwargs["token"])

        response = self.client.get(verification_path)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(verification_path)

        self.assertRedirects(response, reverse("login"))
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(Customer.objects.filter(user=user).exists())

    def test_verification_route_captures_equals_without_slash_redirect(self):
        verification_path = reverse(
            "customer_verify",
            kwargs={"uidb64": "MQ", "token": "invalid="},
        )
        self.assertTrue(verification_path.endswith("="))
        match = resolve(verification_path)
        self.assertEqual(match.kwargs["token"], "invalid=")

        response = self.client.get(verification_path, follow=False)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.has_header("Location"))

    def test_invalid_verification_token_is_rejected_by_view(self):
        self.client.post(
            reverse("customer_register"),
            self.registration_data(),
        )
        user = User.objects.get(email="amina@example.com")
        invalid_path = self.verification_path_for(user, "invalid-token")

        response = self.client.post(invalid_path)

        self.assertEqual(response.status_code, 400)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertFalse(Customer.objects.filter(user=user).exists())

    def test_expired_verification_token_is_rejected_by_view(self):
        self.client.post(
            reverse("customer_register"),
            self.registration_data(),
        )
        user = User.objects.get(email="amina@example.com")
        expired_token = default_token_generator._make_token_with_timestamp(
            user,
            timestamp=0,
            secret=default_token_generator.secret,
        )
        expired_path = self.verification_path_for(user, expired_token)

        response = self.client.post(expired_path)

        self.assertEqual(response.status_code, 400)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertFalse(Customer.objects.filter(user=user).exists())

    def test_resent_verification_link_can_verify_account(self):
        self.client.post(
            reverse("customer_register"),
            self.registration_data(),
        )
        response = self.client.post(
            reverse("customer_resend_verification"),
            {"email": "amina@example.com"},
        )
        self.assertRedirects(response, reverse("customer_resend_verification"))
        self.assertEqual(len(mail.outbox), 2)

        match = re.search(
            r"http://testserver(/account/verify/\S+)",
            mail.outbox[-1].body,
        )
        self.assertIsNotNone(match)
        verification_path = match.group(1)
        self.assertEqual(resolve(verification_path).url_name, "customer_verify")
        self.assertEqual(self.client.get(verification_path).status_code, 200)
        response = self.client.post(verification_path)

        self.assertRedirects(response, reverse("login"))
        user = User.objects.get(email="amina@example.com")
        self.assertTrue(user.is_active)
        self.assertTrue(Customer.objects.filter(user=user).exists())

    def create_service(self):
        return Service.objects.create(
            name="Account Test Cleaning",
            slug="account-test-cleaning",
            description="Service for customer account tests.",
            base_price=Decimal("2000.00"),
            duration_hours=2,
            is_active=True,
        )

    def create_customer_booking(self, customer):
        return Booking.objects.create(
            customer=customer,
            contact_name="Saved Booking Contact",
            contact_email="snapshot@example.com",
            contact_phone="0711111111",
            service=self.create_service(),
            booking_date=date.today() + timedelta(days=5),
            booking_time=time(10, 0),
            address="Original Booking Address",
            city="Nairobi",
            total_price=Decimal("2000.00"),
            status="pending",
        )

    def create_quote_request(self, email, customer=None, **overrides):
        values = {
            "full_name": "Guest Quote Snapshot",
            "email": email,
            "phone": "0712345678",
            "service": None,
            "property_size": 100,
            "rooms": 3,
            "bathrooms": 2,
            "extras": "Window cleaning",
            "address": "12 Guest Road",
            "city": "Nairobi",
            "customer": customer,
        }
        values.update(overrides)
        return QuoteRequest.objects.create(**values)

    def test_registration_creates_inactive_account_and_sends_verification(self):
        verification_path = self.register_and_get_verification_path()
        user = User.objects.get(email="amina@example.com")

        self.assertFalse(user.is_active)
        self.assertNotEqual(user.password, self.password)
        self.assertEqual(Customer.objects.count(), 0)
        self.assertTrue(
            PendingCustomerRegistration.objects.filter(user=user).exists()
        )

        response = self.client.get(verification_path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Customer.objects.count(), 0)

        response = self.client.post(verification_path)
        self.assertRedirects(response, reverse("login"))
        user.refresh_from_db()
        customer = Customer.objects.get(user=user)
        self.assertTrue(user.is_active)
        self.assertEqual(customer.phone, "+254700000000")
        self.assertFalse(
            PendingCustomerRegistration.objects.filter(user=user).exists()
        )

    def test_registration_rejects_malformed_and_duplicate_emails(self):
        response = self.client.post(
            reverse("customer_register"),
            self.registration_data(email="not-an-email"),
        )
        self.assertEqual(response.status_code, 200)

        User.objects.create_user(
            username="existing-account",
            email="AMINA@example.com",
            password=self.password,
        )
        response = self.client.post(
            reverse("customer_register"),
            self.registration_data(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 1)

    def test_registration_uses_django_password_validators(self):
        response = self.client.post(
            reverse("customer_register"),
            self.registration_data(
                password1="password",
                password2="password",
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 0)

    def test_registration_rejects_mismatched_passwords(self):
        response = self.client.post(
            reverse("customer_register"),
            self.registration_data(password2="Different-Password-984!"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 0)

    def test_email_login_and_logout_use_django_authentication(self):
        verification_path = self.register_and_get_verification_path()
        self.client.post(verification_path)

        response = self.client.post(
            reverse("login"),
            {
                "username": "AMINA@example.com",
                "password": self.password,
            },
        )
        self.assertRedirects(response, reverse("customer_profile"))
        self.assertEqual(
            int(self.client.session["_auth_user_id"]),
            User.objects.get(email="amina@example.com").pk,
        )

        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("home"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_unverified_account_cannot_log_in(self):
        self.client.post(
            reverse("customer_register"),
            self.registration_data(),
        )
        response = self.client.post(
            reverse("login"),
            {
                "username": "amina@example.com",
                "password": self.password,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_password_reset_uses_django_email_workflow(self):
        User.objects.create_user(
            username="password-reset-account",
            email="reset@example.com",
            password=self.password,
        )
        response = self.client.post(
            reverse("password_reset"),
            {"email": "reset@example.com"},
        )
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/account/reset/", mail.outbox[0].body)

    def test_profile_requires_authentication_and_own_customer_record(self):
        response = self.client.get(reverse("customer_profile"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('customer_profile')}",
        )

        user = User.objects.create_user(
            username="without-profile",
            email="without-profile@example.com",
            password=self.password,
        )
        self.client.force_login(user)
        response = self.client.get(reverse("customer_profile"))
        self.assertEqual(response.status_code, 409)
        self.assertTemplateUsed(response, "accounts/profile_unavailable.html")
        self.assertTrue(
            "no customer profile" in response.content.decode().lower()
        )
        self.assertIn(
            f'href="{reverse("contact")}"',
            response.content.decode(),
        )
        self.assertIn(
            f'action="{reverse("logout")}"',
            response.content.decode(),
        )
        self.assertIn('name="csrfmiddlewaretoken"', response.content.decode())
        self.assertEqual(Customer.objects.count(), 0)

    def test_quote_history_shows_only_the_authenticated_customers_quotes(self):
        user_a = User.objects.create_user(
            username="quote-history-customer-a",
            email="history-a@example.com",
            password=self.password,
        )
        customer_a = Customer.objects.create(
            user=user_a,
            full_name="History Customer A",
            email=user_a.email,
            phone="0710000001",
            address="A address",
            city="Nairobi",
        )
        user_b = User.objects.create_user(
            username="quote-history-customer-b",
            email="history-b@example.com",
            password=self.password,
        )
        customer_b = Customer.objects.create(
            user=user_b,
            full_name="History Customer B",
            email=user_b.email,
            phone="0710000002",
            address="B address",
            city="Nairobi",
        )
        customer_a_quote = self.create_quote_request(
            user_a.email,
            customer=customer_a,
            full_name="CUSTOMER_A_PRIVATE_QUOTE_MARKER",
        )
        customer_b_quote = self.create_quote_request(
            user_b.email,
            customer=customer_b,
            full_name="CUSTOMER_B_PRIVATE_QUOTE_MARKER",
        )
        self.client.force_login(user_a)

        response = self.client.get(reverse("customer_quote_history"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Quote request #{customer_a_quote.pk}")
        self.assertNotContains(response, f"Quote request #{customer_b_quote.pk}")

    def test_quote_history_excludes_unlinked_guest_quotes_with_matching_email(self):
        user = User.objects.create_user(
            username="quote-history-guest-isolation",
            email="guest-isolation@example.com",
            password=self.password,
        )
        customer = Customer.objects.create(
            user=user,
            full_name="Registered Customer",
            email=user.email,
            phone="0710000003",
            address="Customer address",
            city="Nairobi",
        )
        guest_quote = self.create_quote_request(
            user.email.upper(),
            full_name="UNLINKED_GUEST_QUOTE_MARKER",
        )
        linked_quote = self.create_quote_request(
            user.email,
            customer=customer,
            full_name="LINKED_CUSTOMER_QUOTE_MARKER",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("customer_quote_history"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Quote request #{linked_quote.pk}")
        self.assertNotContains(response, f"Quote request #{guest_quote.pk}")
        guest_quote.refresh_from_db()
        self.assertIsNone(guest_quote.customer_id)

    def test_quote_history_displays_empty_state(self):
        user = User.objects.create_user(
            username="quote-history-empty",
            email="history-empty@example.com",
            password=self.password,
        )
        Customer.objects.create(
            user=user,
            full_name="Empty History Customer",
            email=user.email,
            phone="0710000004",
            address="Empty address",
            city="Nairobi",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("customer_quote_history"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/quote_history.html")
        self.assertContains(response, "No quote requests yet")

    def test_quote_history_requires_authentication(self):
        response = self.client.get(reverse("customer_quote_history"))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('customer_quote_history')}",
        )

    def test_quote_history_without_customer_uses_profile_unavailable_response(self):
        user = User.objects.create_user(
            username="quote-history-no-profile",
            email="history-no-profile@example.com",
            password=self.password,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("customer_quote_history"))

        self.assertEqual(response.status_code, 409)
        self.assertTemplateUsed(response, "accounts/profile_unavailable.html")

    def test_quote_history_only_shows_details_for_sent_quotations(self):
        user = User.objects.create_user(
            username="quote-history-visibility",
            email="history-visibility@example.com",
            password=self.password,
        )
        customer = Customer.objects.create(
            user=user,
            full_name="Quotation Visibility Customer",
            email=user.email,
            phone="0710000005",
            address="Visibility address",
            city="Nairobi",
        )
        self.create_quote_request(
            user.email,
            customer=customer,
            status="sent",
            proposed_amount=Decimal("4200.50"),
            quote_valid_until=date(2026, 12, 1),
            quote_sent_at=timezone.now(),
            internal_notes="PRIVATE_SENT_QUOTE_STAFF_NOTE",
        )
        self.create_quote_request(
            user.email,
            customer=customer,
            status="prepared",
            proposed_amount=Decimal("9876.54"),
            quote_valid_until=date(2027, 1, 15),
            internal_notes="PRIVATE_PREPARED_QUOTE_STAFF_NOTE",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("customer_quote_history"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "KSh 4200.50")
        self.assertContains(response, "2026-12-01")
        self.assertNotContains(response, "KSh 9876.54")
        self.assertNotContains(response, "2027-01-15")
        self.assertNotContains(response, "PRIVATE_SENT_QUOTE_STAFF_NOTE")
        self.assertNotContains(response, "PRIVATE_PREPARED_QUOTE_STAFF_NOTE")

    def test_deleting_user_preserves_customer_and_booking_records(self):
        user = User.objects.create_user(
            username="delete-account",
            email="delete@example.com",
            password=self.password,
        )
        customer = Customer.objects.create(
            user=user,
            full_name="Preserved Customer",
            email=user.email,
            phone="0710000000",
            address="Preserved Address",
            city="Nairobi",
        )
        booking = self.create_customer_booking(customer)

        user.delete()

        customer.refresh_from_db()
        booking.refresh_from_db()
        self.assertIsNone(customer.user_id)
        self.assertEqual(booking.customer_id, customer.pk)

    def test_profile_update_preserves_staff_and_booking_history(self):
        customer = Customer.objects.create(
            full_name="Original Guest",
            email="guest@example.com",
            phone="0710000000",
            address="Original Profile Address",
            city="Nairobi",
            county="Nairobi",
            notes="Staff-only note",
            is_repeat_customer=True,
        )
        booking = self.create_customer_booking(customer)
        user = User.objects.create_user(
            username="profile-customer",
            email=customer.email,
            first_name="Original",
            last_name="Name",
            password=self.password,
        )
        customer.user = user
        customer.save(update_fields=["user"])
        self.client.force_login(user)

        response = self.client.post(
            reverse("customer_profile"),
            {
                "first_name": "Updated",
                "last_name": "Customer",
                "phone": "0722222222",
                "county": "Kiambu",
                "city": "Ruiru",
                "address": "New Profile Address",
                "notes": "Attempt to change staff notes",
                "is_repeat_customer": "false",
                "email": "attacker@example.com",
            },
        )
        self.assertRedirects(response, reverse("customer_profile"))

        customer.refresh_from_db()
        user.refresh_from_db()
        booking.refresh_from_db()
        self.assertEqual(user.first_name, "Updated")
        self.assertEqual(customer.full_name, "Updated Customer")
        self.assertEqual(customer.phone, "0722222222")
        self.assertEqual(customer.county, "Kiambu")
        self.assertEqual(customer.address, "New Profile Address")
        self.assertEqual(customer.email, "guest@example.com")
        self.assertEqual(customer.notes, "Staff-only note")
        self.assertTrue(customer.is_repeat_customer)
        self.assertEqual(booking.contact_name, "Saved Booking Contact")
        self.assertEqual(booking.contact_email, "snapshot@example.com")
        self.assertEqual(booking.contact_phone, "0711111111")
        self.assertEqual(booking.address, "Original Booking Address")

    def test_verified_account_links_only_one_unlinked_customer(self):
        customer = Customer.objects.create(
            full_name="Existing Guest Name",
            email="amina@example.com",
            phone="0700000000",
            address="Existing Address",
            city="Mombasa",
        )
        booking = self.create_customer_booking(customer)
        verification_path = self.register_and_get_verification_path()
        self.client.post(verification_path)

        customer.refresh_from_db()
        booking.refresh_from_db()
        self.assertEqual(
            customer.user.email,
            "amina@example.com",
        )
        self.assertEqual(customer.full_name, "Existing Guest Name")
        self.assertEqual(customer.phone, "0700000000")
        self.assertEqual(customer.address, "Existing Address")
        self.assertEqual(booking.customer_id, customer.pk)
        self.assertEqual(booking.contact_name, "Saved Booking Contact")

    def test_verification_normalizes_existing_customer_email(self):
        customer = Customer.objects.create(
            full_name="Existing Guest Name",
            email="amina@example.com",
            phone="0700000000",
            address="Existing Address",
            city="Mombasa",
        )
        Customer.objects.filter(pk=customer.pk).update(
            email=" \tAMINA@example.com\r\n"
        )
        verification_path = self.register_and_get_verification_path()

        response = self.client.post(verification_path)

        self.assertRedirects(response, reverse("login"))
        user = User.objects.get(email="amina@example.com")
        customer.refresh_from_db()
        self.assertEqual(customer.user_id, user.pk)
        self.assertEqual(customer.email, "amina@example.com")

    def test_verification_links_matching_guest_quotes_case_insensitively(self):
        matching_quotes = [
            self.create_quote_request("amina@example.com"),
            self.create_quote_request("AMINA@EXAMPLE.COM"),
        ]
        nonmatching_quote = self.create_quote_request("other@example.com")
        other_user = User.objects.create_user(
            username="quote-owner",
            email="owner@example.com",
        )
        other_customer = Customer.objects.create(
            user=other_user,
            full_name="Other Quote Owner",
            email="owner@example.com",
            phone="0700000000",
            address="Owner Road",
            city="Nairobi",
        )
        already_linked_quote = self.create_quote_request(
            "amina@example.com",
            customer=other_customer,
        )
        verification_path = self.register_and_get_verification_path()

        response = self.client.post(verification_path)

        self.assertRedirects(response, reverse("login"))
        user = User.objects.get(email="amina@example.com")
        customer = Customer.objects.get(user=user)
        for quote in matching_quotes:
            quote.refresh_from_db()
            self.assertEqual(quote.customer_id, customer.pk)
        nonmatching_quote.refresh_from_db()
        already_linked_quote.refresh_from_db()
        self.assertIsNone(nonmatching_quote.customer_id)
        self.assertEqual(already_linked_quote.customer_id, other_customer.pk)

    def test_guest_quote_linking_preserves_snapshots_and_is_idempotent(self):
        quote = self.create_quote_request(
            "AMINA@example.com",
            full_name="Original Quote Name",
            phone="0700111222",
            address="Original Quote Address",
            city="Mombasa",
        )
        verification_path = self.register_and_get_verification_path()
        self.client.post(verification_path)

        user = User.objects.get(email="amina@example.com")
        customer = Customer.objects.get(user=user)
        quote.refresh_from_db()
        saved_snapshot = (
            quote.email,
            quote.full_name,
            quote.phone,
            quote.address,
            quote.city,
        )
        self.assertEqual(quote.customer_id, customer.pk)
        self.assertEqual(
            saved_snapshot,
            (
                "AMINA@example.com",
                "Original Quote Name",
                "0700111222",
                "Original Quote Address",
                "Mombasa",
            ),
        )

        self.assertEqual(
            link_guest_quote_requests(customer=customer, user=user),
            0,
        )
        quote.refresh_from_db()
        self.assertEqual(
            (
                quote.email,
                quote.full_name,
                quote.phone,
                quote.address,
                quote.city,
            ),
            saved_snapshot,
        )
        self.assertEqual(quote.customer_id, customer.pk)

    def test_already_active_account_does_not_link_guest_quotes(self):
        quote = self.create_quote_request("amina@example.com")

        user = User.objects.create_user(
            username="already-active-customer",
            email="amina@example.com",
            password=self.password,
            is_active=True,
        )

        customer = Customer.objects.create(
            user=user,
            full_name="Existing Active Customer",
            email=user.email,
            phone="0700000000",
            address="Customer Road",
            city="Nairobi",
        )

        token = default_token_generator.make_token(user)

        result = verify_customer_account(user, token)

        self.assertEqual(result.pk, customer.pk)

        quote.refresh_from_db()
        self.assertIsNone(quote.customer_id)



    def test_inactive_user_cannot_link_guest_quotes(self):
        quote = self.create_quote_request("unverified@example.com")
        user = User.objects.create_user(
            username="unverified-quote-user",
            email="unverified@example.com",
            is_active=False,
        )
        customer = Customer.objects.create(
            user=user,
            full_name="Unverified User",
            email=user.email,
            phone="0700000000",
            address="User Road",
            city="Nairobi",
        )

        with self.assertRaisesMessage(
            ValidationError,
            "verified, active account",
        ):
            link_guest_quote_requests(customer=customer, user=user)

        quote.refresh_from_db()
        self.assertIsNone(quote.customer_id)

    def test_customer_not_linked_to_user_cannot_claim_guest_quotes(self):
        quote = self.create_quote_request("claimant@example.com")
        user = User.objects.create_user(
            username="quote-claimant",
            email="claimant@example.com",
        )
        customer = Customer.objects.create(
            full_name="Unlinked Customer",
            email="claimant@example.com",
            phone="0700000000",
            address="Customer Road",
            city="Nairobi",
        )

        with self.assertRaisesMessage(
            ValidationError,
            "not linked to the verified user",
        ):
            link_guest_quote_requests(customer=customer, user=user)

        quote.refresh_from_db()
        self.assertIsNone(quote.customer_id)

    def test_normalized_email_uniqueness_prevents_ambiguous_verification_match(self):
        customer = Customer.objects.create(
            full_name="First Guest",
            email="amina@example.com",
            phone="0700000000",
            address="Guest Address",
            city="Nairobi",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Customer.objects.create(
                    full_name="Second Guest",
                    email="AMINA@example.com",
                    phone="0700000000",
                    address="Guest Address",
                    city="Nairobi",
                )

        verification_path = self.register_and_get_verification_path()
        response = self.client.post(verification_path)

        self.assertRedirects(response, reverse("login"))
        user = User.objects.get(email="amina@example.com")
        customer.refresh_from_db()
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(customer.user_id, user.pk)
        self.assertEqual(Customer.objects.filter(email__iexact=user.email).count(), 1)

    def test_customer_already_linked_to_another_user_is_not_reused(self):
        existing_user = User.objects.create_user(
            username="another-customer",
            email="other@example.com",
            password=self.password,
        )
        Customer.objects.create(
            user=existing_user,
            full_name="Already Linked",
            email="amina@example.com",
            phone="0700000000",
            address="Existing Address",
            city="Nairobi",
        )
        verification_path = self.register_and_get_verification_path()
        user = User.objects.get(email="amina@example.com")
        token = verification_path.rstrip("/").split("/")[-1]

        with self.assertRaises(CustomerAccountError):
            verify_customer_account(user, token)

        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_invalid_verification_token_is_rejected(self):
        self.client.post(
            reverse("customer_register"),
            self.registration_data(),
        )
        user = User.objects.get(email="amina@example.com")
        with self.assertRaises(CustomerAccountError):
            verify_customer_account(user, "invalid-token")
        user.refresh_from_db()
        self.assertFalse(user.is_active)


    @patch(
        "apps_core.customers.views.send_template_email",
        side_effect=OSError("Email backend unavailable."),
    )
    def test_registration_remains_pending_when_verification_email_fails(
        self,
        send_mock,
    ):
        response = self.client.post(
            reverse("customer_register"),
            self.registration_data(),
        )

        self.assertRedirects(
            response,
            reverse("customer_resend_verification"),
        )

        user = User.objects.get(email="amina@example.com")

        self.assertFalse(user.is_active)
        self.assertTrue(
            PendingCustomerRegistration.objects.filter(user=user).exists()
        )
        self.assertFalse(Customer.objects.filter(user=user).exists())


    @patch(
        "apps_core.customers.views.send_template_email",
        side_effect=OSError("Email backend unavailable."),
    )
    def test_resend_failure_keeps_generic_response(
        self,
        send_mock,
    ):
        self.client.post(
            reverse("customer_register"),
            self.registration_data(),
        )

        # Consume the registration failure message before testing resend.
        self.client.get(reverse("customer_resend_verification"))

        # Reset the mock so only the resend attempt is counted.
        send_mock.reset_mock()

        mail.outbox.clear()

        response = self.client.post(
            reverse("customer_resend_verification"),
            {"email": "amina@example.com"},
        )

        self.assertRedirects(
            response,
            reverse("customer_resend_verification"),
        )

        messages_list = list(response.wsgi_request._messages)

        self.assertEqual(len(messages_list), 1)
        self.assertIn(
            "If a pending account exists",
            str(messages_list[0]),
        )

        self.assertEqual(len(mail.outbox), 0)
        send_mock.assert_called_once()


class CustomerDashboardDataTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="dashboard-user",
            email="dashboard@example.com",
            password="test-password",
        )
        self.customer = Customer.objects.create(
            user=self.user,
            full_name="Dashboard Customer",
            email=self.user.email,
            phone="0712345678",
            address="Customer Address",
            city="Nairobi",
        )
        self.other_user = User.objects.create_user(
            username="other-dashboard-user",
            email="other-dashboard@example.com",
            password="test-password",
        )
        self.other_customer = Customer.objects.create(
            user=self.other_user,
            full_name="Other Customer",
            email=self.other_user.email,
            phone="0711111111",
            address="Other Address",
            city="Mombasa",
        )
        self.service = Service.objects.create(
            name="Dashboard Cleaning",
            slug="dashboard-cleaning",
            description="Service for dashboard data tests.",
            base_price=Decimal("2500.00"),
            duration_hours=2,
            is_active=True,
        )

    def create_booking(self, *, customer, created_by, contact_email):
        return Booking.objects.create(
            customer=customer,
            created_by=created_by,
            contact_name="Dashboard Booking",
            contact_email=contact_email,
            contact_phone="0712345678",
            service=self.service,
            booking_date=date.today() + timedelta(days=5),
            booking_time=time(10, 0),
            address="Booking Address",
            city="Nairobi",
            total_price=Decimal("2500.00"),
            status="confirmed",
        )

    def create_quote(self, *, customer, email, **overrides):
        values = {
            "full_name": "Dashboard Quote",
            "email": email,
            "phone": "0712345678",
            "service": self.service,
            "customer": customer,
            "property_size": 100,
            "rooms": 3,
            "bathrooms": 2,
            "extras": "",
            "address": "Quote Address",
            "city": "Nairobi",
            "status": "pending",
        }
        values.update(overrides)
        return QuoteRequest.objects.create(**values)

    def get_dashboard_data(self, **overrides):
        values = {"user": self.user, "customer": self.customer}
        values.update(overrides)
        return get_customer_dashboard_data(**values)

    def test_dashboard_includes_only_bookings_attributed_to_user(self):
        own_booking = self.create_booking(
            customer=self.customer,
            created_by=self.user,
            contact_email="different-contact@example.com",
        )
        guest_booking = self.create_booking(
            customer=self.customer,
            created_by=None,
            contact_email=self.user.email,
        )
        other_booking = self.create_booking(
            customer=self.other_customer,
            created_by=self.other_user,
            contact_email=self.other_user.email,
        )

        data = self.get_dashboard_data()

        self.assertEqual(data["booking_count"], 1)
        self.assertEqual(
            [item["reference"] for item in data["recent_bookings"]],
            [own_booking.booking_reference],
        )
        self.assertNotIn(
            guest_booking.booking_reference,
            [item["reference"] for item in data["recent_bookings"]],
        )
        self.assertNotIn(
            other_booking.booking_reference,
            [item["reference"] for item in data["recent_bookings"]],
        )
        self.assertEqual(
            data["recent_bookings"][0]["service_name"],
            self.service.name,
        )
        self.assertEqual(
            data["recent_bookings"][0]["payment_status"],
            None,
        )

    def test_latest_payment_attempt_is_returned_for_each_booking(self):
        booking = self.create_booking(
            customer=self.customer,
            created_by=self.user,
            contact_email="dashboard@example.com",
        )
        Payment.objects.create(
            booking=booking,
            amount=Decimal("2500.00"),
            method="mpesa",
            status="failed",
            phone_number="0712345678",
        )
        Payment.objects.create(
            booking=booking,
            amount=Decimal("2500.00"),
            method="mpesa",
            status="processing",
            phone_number="0712345678",
        )
        other_booking = self.create_booking(
            customer=self.other_customer,
            created_by=self.other_user,
            contact_email="other-dashboard@example.com",
        )
        Payment.objects.create(
            booking=other_booking,
            amount=Decimal("2500.00"),
            method="mpesa",
            status="paid",
            phone_number="0711111111",
        )

        data = self.get_dashboard_data()

        self.assertEqual(data["booking_count"], 1)
        self.assertEqual(len(data["recent_bookings"]), 1)
        self.assertEqual(
            data["recent_bookings"][0]["payment_status"],
            "processing",
        )
        self.assertEqual(
            data["recent_bookings"][0]["payment_status_display"],
            "Processing",
        )

    def test_dashboard_lists_only_quotes_linked_to_supplied_customer(self):
        linked_quote = self.create_quote(
            customer=self.customer,
            email="linked-quote@example.com",
            status="sent",
            proposed_amount=Decimal("4200.50"),
            quote_valid_until=date.today() + timedelta(days=7),
            internal_notes="PRIVATE_QUOTE_NOTES",
        )
        self.create_quote(
            customer=self.other_customer,
            email=self.user.email,
        )
        unlinked_guest_quote = self.create_quote(
            customer=None,
            email=self.user.email,
            full_name="UNLINKED_GUEST_QUOTE",
        )

        data = self.get_dashboard_data()

        self.assertEqual(data["quotation_count"], 1)
        self.assertEqual(
            [item["reference"] for item in data["recent_quotations"]],
            [f"Q-{linked_quote.pk}"],
        )
        self.assertNotIn(
            f"Q-{unlinked_guest_quote.pk}",
            [item["reference"] for item in data["recent_quotations"]],
        )
        self.assertEqual(
            data["recent_quotations"][0]["proposed_amount"],
            Decimal("4200.50"),
        )
        self.assertEqual(
            data["recent_quotations"][0]["quote_valid_until"],
            linked_quote.quote_valid_until,
        )
        self.assertNotIn(
            "internal_notes",
            data["recent_quotations"][0],
        )

    def test_non_sent_quotes_do_not_expose_proposed_amount_or_expiry(self):
        self.create_quote(
            customer=self.customer,
            email=self.user.email,
            status="prepared",
            proposed_amount=Decimal("9876.54"),
            quote_valid_until=date.today() + timedelta(days=9),
        )

        quotation = self.get_dashboard_data()["recent_quotations"][0]

        self.assertIsNone(quotation["proposed_amount"])
        self.assertIsNone(quotation["quote_valid_until"])

    def test_dashboard_data_excludes_private_booking_and_payment_fields(self):
        booking = self.create_booking(
            customer=self.customer,
            created_by=self.user,
            contact_email="dashboard@example.com",
        )
        Payment.objects.create(
            booking=booking,
            amount=Decimal("2500.00"),
            method="mpesa",
            status="paid",
            phone_number="0712345678",
            checkout_request_id="PRIVATE_CHECKOUT_ID",
            merchant_request_id="PRIVATE_MERCHANT_ID",
            mpesa_receipt="PRIVATE_MPESA_RECEIPT",
            result_description="PRIVATE_CALLBACK_DETAILS",
        )
        self.create_quote(
            customer=self.customer,
            email=self.user.email,
            internal_notes="PRIVATE_INTERNAL_NOTES",
        )

        data = self.get_dashboard_data()
        booking_data = data["recent_bookings"][0]
        quote_data = data["recent_quotations"][0]

        self.assertEqual(
            set(booking_data),
            {
                "reference",
                "service_name",
                "booking_date",
                "booking_time",
                "status",
                "status_display",
                "created_at",
                "payment_status",
                "payment_status_display",
            },
        )
        self.assertEqual(
            set(quote_data),
            {
                "reference",
                "status",
                "status_display",
                "service_name",
                "created_at",
                "proposed_amount",
                "quote_valid_until",
            },
        )
        self.assertNotIn("PRIVATE_INTERNAL_NOTES", repr(data))
        for secret in (
            "PRIVATE_CHECKOUT_ID",
            "PRIVATE_MERCHANT_ID",
            "PRIVATE_MPESA_RECEIPT",
            "PRIVATE_CALLBACK_DETAILS",
            "0712345678",
        ):
            self.assertNotIn(secret, repr(data))

    def test_empty_booking_and_quote_histories_return_zero_counts(self):
        data = self.get_dashboard_data()

        self.assertEqual(data["booking_count"], 0)
        self.assertEqual(data["recent_bookings"], [])
        self.assertEqual(data["quotation_count"], 0)
        self.assertEqual(data["recent_quotations"], [])

    def test_recent_lists_are_limited_and_counts_include_all_records(self):
        for index in range(7):
            self.create_booking(
                customer=self.customer,
                created_by=self.user,
                contact_email=f"booking-{index}@example.com",
            )
            self.create_quote(
                customer=self.customer,
                email=f"quote-{index}@example.com",
            )

        data = self.get_dashboard_data()

        self.assertEqual(data["booking_count"], 7)
        self.assertEqual(data["quotation_count"], 7)
        self.assertEqual(len(data["recent_bookings"]), 5)
        self.assertEqual(len(data["recent_quotations"]), 5)

    def test_customer_linked_to_another_user_is_rejected(self):
        with self.assertRaisesMessage(
            CustomerAccountError,
            "not linked to the supplied user",
        ):
            self.get_dashboard_data(customer=self.other_customer)

    def test_unsaved_or_missing_customer_is_rejected(self):
        unsaved_customer = Customer(
            user=self.user,
            full_name="Unsaved Customer",
            email=self.user.email,
            phone="0712345678",
            address="Address",
            city="Nairobi",
        )
        unsaved_customer.pk = 987654
        for customer in (unsaved_customer, None):
            with self.subTest(customer=customer):
                with self.assertRaisesMessage(
                    CustomerAccountError,
                    "saved customer profile",
                ):
                    self.get_dashboard_data(customer=customer)

    def test_customer_database_association_is_rechecked(self):
        Customer.objects.filter(pk=self.customer.pk).update(user=None)

        with self.assertRaisesMessage(
            CustomerAccountError,
            "not linked to the supplied user",
        ):
            self.get_dashboard_data()

    def test_missing_or_unauthenticated_user_is_rejected(self):
        unsaved_user = User(username="unsaved-dashboard-user")
        unsaved_user.pk = 987655
        for user in (None, unsaved_user):
            with self.subTest(user=user):
                with self.assertRaisesMessage(
                    CustomerAccountError,
                    "authenticated, saved user",
                ):
                    self.get_dashboard_data(user=user)

    def test_inactive_or_deleted_user_is_rejected(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        with self.assertRaisesMessage(
            CustomerAccountError,
            "authenticated, saved user",
        ):
            self.get_dashboard_data()

        stale_user = User.objects.get(pk=self.user.pk)
        self.user.is_active = True
        self.user.save(update_fields=["is_active"])
        self.user.delete()

        with self.assertRaisesMessage(
            CustomerAccountError,
            "authenticated, saved user",
        ):
            self.get_dashboard_data(user=stale_user)

    def test_dashboard_queries_remain_bounded_as_lists_grow(self):
        for index in range(3):
            self.create_booking(
                customer=self.customer,
                created_by=self.user,
                contact_email=f"query-{index}@example.com",
            )
            self.create_quote(
                customer=self.customer,
                email=f"quote-query-{index}@example.com",
            )

        with self.assertNumQueries(6):
            self.get_dashboard_data()

    def test_dashboard_requires_authentication(self):
        response = self.client.get(reverse("customer_dashboard"))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('customer_dashboard')}",
        )

    def test_dashboard_without_customer_uses_profile_unavailable_response(self):
        user_without_profile = User.objects.create_user(
            username="dashboard-no-profile",
            email="dashboard-no-profile@example.com",
            password="dashboard-test-password",
        )
        self.client.force_login(user_without_profile)

        response = self.client.get(reverse("customer_dashboard"))

        self.assertEqual(response.status_code, 409)
        self.assertTemplateUsed(response, "accounts/profile_unavailable.html")

    def test_dashboard_renders_only_current_customers_records(self):
        own_booking = self.create_booking(
            customer=self.customer,
            created_by=self.user,
            contact_email="different-contact@example.com",
        )
        self.create_booking(
            customer=self.customer,
            created_by=None,
            contact_email=self.user.email,
        )
        self.create_booking(
            customer=self.other_customer,
            created_by=self.other_user,
            contact_email=self.other_user.email,
        )
        own_quote = self.create_quote(
            customer=self.customer,
            email="quote-contact@example.com",
            status="sent",
            proposed_amount=Decimal("4200.50"),
            quote_valid_until=date.today() + timedelta(days=7),
            internal_notes="PRIVATE_DASHBOARD_STAFF_NOTE",
        )
        self.create_quote(
            customer=self.other_customer,
            email=self.user.email,
            full_name="OTHER_CUSTOMER_QUOTE",
        )
        self.create_quote(
            customer=None,
            email=self.user.email,
            full_name="UNLINKED_GUEST_QUOTE",
        )
        booking_payment = Payment.objects.create(
            booking=own_booking,
            amount=Decimal("2500.00"),
            method="mpesa",
            status="paid",
            phone_number="PRIVATE_PHONE",
            checkout_request_id="PRIVATE_CHECKOUT_ID",
            merchant_request_id="PRIVATE_MERCHANT_ID",
            mpesa_receipt="PRIVATE_MPESA_RECEIPT",
            result_description="PRIVATE_CALLBACK_DETAILS",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("customer_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/dashboard.html")
        self.assertEqual(response.context["booking_count"], 1)
        self.assertEqual(response.context["quotation_count"], 1)
        self.assertContains(response, own_booking.booking_reference)
        self.assertContains(response, own_quote.service.name)
        self.assertContains(response, "Paid")
        self.assertContains(response, "KSh 4200.50")
        self.assertNotContains(response, "OTHER_CUSTOMER_QUOTE")
        self.assertNotContains(response, "UNLINKED_GUEST_QUOTE")
        for sensitive_value in (
            "PRIVATE_DASHBOARD_STAFF_NOTE",
            "PRIVATE_PHONE",
            "PRIVATE_CHECKOUT_ID",
            "PRIVATE_MERCHANT_ID",
            "PRIVATE_MPESA_RECEIPT",
            "PRIVATE_CALLBACK_DETAILS",
        ):
            with self.subTest(sensitive_value=sensitive_value):
                self.assertNotContains(response, sensitive_value)
        self.assertEqual(booking_payment.status, "paid")

    def test_dashboard_empty_states_and_navigation_links_render(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("customer_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Total bookings")
        self.assertContains(response, "0")
        self.assertContains(response, "No bookings yet")
        self.assertContains(response, "No quotation requests yet")
        for route_name in (
            "customer_profile",
            "customer_quote_history",
            "booking",
        ):
            with self.subTest(route_name=route_name):
                self.assertContains(response, reverse(route_name))
        self.assertContains(
            response,
            f'href="{reverse("customer_dashboard")}">My Account</a>',
        )

    def test_existing_profile_and_quote_history_routes_remain_available(self):
        self.client.force_login(self.user)

        profile_response = self.client.get(reverse("customer_profile"))
        quote_history_response = self.client.get(
            reverse("customer_quote_history")
        )

        self.assertEqual(profile_response.status_code, 200)
        self.assertTemplateUsed(profile_response, "accounts/profile.html")
        self.assertEqual(quote_history_response.status_code, 200)
        self.assertTemplateUsed(
            quote_history_response,
            "accounts/quote_history.html",
        )