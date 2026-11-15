from datetime import date, time, timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps_core.bookings.models import Booking
from apps_core.core import rate_limit
from apps_core.customers.models import Customer
from apps_core.quotes.models import QuoteRequest
from apps_core.services.models import Service, ServiceCategory
from apps_core.staff.models import Staff


User = get_user_model()


class RateLimitUtilityTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_cache_key_uses_digest_and_omits_raw_identifier(self):
        key = rate_limit.cache_key(
            "contact:ip-email",
            "203.0.113.10:a@example.com",
        )

        self.assertTrue(
            key.startswith("public-rate-limit:contact:ip-email:")
        )
        self.assertNotIn("203.0.113.10", key)
        self.assertNotIn("a@example.com", key)
        self.assertEqual(len(key.rsplit(":", 1)[-1]), 64)

    def test_email_identifier_is_normalized_and_missing_field_is_skipped(self):
        scope = rate_limit.ip_identifier_scope(
            "contact", 5, 3600, "email"
        )
        request = RequestFactory().post(
            "/contact/",
            {"email": "  A@Example.COM "},
        )
        request.META["REMOTE_ADDR"] = "203.0.113.10"

        self.assertEqual(
            scope.identifier(request),
            "203.0.113.10:a@example.com",
        )

        request = RequestFactory().post("/contact/", {})
        request.META["REMOTE_ADDR"] = "203.0.113.10"
        self.assertIsNone(scope.identifier(request))

    def test_booking_scope_uses_resolver_match_booking_id(self):
        scope = rate_limit.ip_booking_scope(
            "payment_initiation", 10, 3600, "booking_id"
        )
        request = RequestFactory().post(
            "/payments/booking/32/initiate/"
        )
        request.META["REMOTE_ADDR"] = "203.0.113.10"
        request.resolver_match = Mock(kwargs={"booking_id": 32})

        self.assertEqual(
            scope.identifier(request),
            "203.0.113.10:32",
        )

        request.resolver_match = Mock(kwargs={})
        self.assertIsNone(scope.identifier(request))

    def test_user_scope_only_applies_to_authenticated_users(self):
        scope = rate_limit.user_scope("quote", 20, 86400)
        request = RequestFactory().post("/quote/", {})
        request.user = AnonymousUser()
        self.assertIsNone(scope.identifier(request))

        user = User.objects.create_user(
            username="rate-limit-user",
            email="rate-limit@example.com",
        )
        request.user = user
        self.assertEqual(scope.identifier(request), f"user:{user.pk}")

    def test_get_requests_do_not_consume_rate_limit_counters(self):
        def view(request):
            return HttpResponse("ok")

        wrapped = rate_limit.rate_limited(
            rate_limit.ip_scope("utility-get", 1, 60)
        )(view)
        get_request = RequestFactory().get("/utility/")
        get_request.META["REMOTE_ADDR"] = "198.51.100.1"
        self.assertEqual(wrapped(get_request).status_code, 200)
        self.assertEqual(wrapped(get_request).status_code, 200)

        post_request = RequestFactory().post("/utility/", {})
        post_request.META["REMOTE_ADDR"] = "198.51.100.1"
        self.assertEqual(wrapped(post_request).status_code, 200)
        self.assertEqual(wrapped(post_request).status_code, 429)

    def test_missing_identifier_still_uses_ip_scope(self):
        def view(request):
            return HttpResponse("ok")

        wrapped = rate_limit.rate_limited(
            rate_limit.ip_scope("utility-missing", 1, 60),
            rate_limit.ip_identifier_scope(
                "utility-missing", 0, 60, "email"
            ),
        )(view)
        request = RequestFactory().post("/utility/", {})
        request.META["REMOTE_ADDR"] = "198.51.100.2"

        self.assertEqual(wrapped(request).status_code, 200)
        self.assertEqual(wrapped(request).status_code, 429)

    def test_cache_failure_fails_open(self):
        def view(request):
            return HttpResponse("ok")

        wrapped = rate_limit.rate_limited(
            rate_limit.ip_scope("utility-fail", 1, 60)
        )(view)
        request = RequestFactory().post("/utility/", {})
        request.META["REMOTE_ADDR"] = "198.51.100.3"

        with patch.object(
            rate_limit.cache, "add", side_effect=Exception("down")
        ):
            self.assertEqual(wrapped(request).status_code, 200)

    def test_counter_recovery_retries_value_error(self):
        with patch.object(
            rate_limit.cache, "add", return_value=False
        ), patch.object(
            rate_limit.cache,
            "incr",
            side_effect=[ValueError, 2],
        ) as incr:
            self.assertEqual(
                rate_limit._increment_counter("key", 60), 2
            )
            self.assertEqual(incr.call_count, 2)

    def test_counter_recovery_returns_none_after_bounded_retries(self):
        with patch.object(
            rate_limit.cache, "add", return_value=False
        ), patch.object(
            rate_limit.cache, "incr", side_effect=ValueError
        ) as incr:
            self.assertIsNone(rate_limit._increment_counter("key", 60))
            self.assertEqual(
                incr.call_count,
                rate_limit.COUNTER_RECOVERY_ATTEMPTS,
            )

    def test_counter_uses_configured_timeout(self):
        with patch.object(
            rate_limit.cache, "add", return_value=True
        ) as add:
            rate_limit._increment_counter("key", 123)
            add.assert_called_once_with("key", 1, 123)


class RateLimitPolicyTests(TestCase):
    def assert_policy(self, scopes, expected):
        self.assertEqual(
            [
                (scope.name, scope.limit, scope.window)
                for scope in scopes
            ],
            expected,
        )

    def test_contact_policy(self):
        self.assert_policy(
            rate_limit.CONTACT_RATE_LIMITS,
            [
                ("contact:ip", 5, 3600),
                ("contact:ip-email", 5, 86400),
            ],
        )

    def test_quote_policy(self):
        self.assert_policy(
            rate_limit.QUOTE_RATE_LIMITS,
            [
                ("quote:ip", 10, 3600),
                ("quote:ip-email", 5, 86400),
                ("quote:user", 20, 86400),
            ],
        )

    def test_booking_policy(self):
        self.assert_policy(
            rate_limit.BOOKING_RATE_LIMITS,
            [
                ("booking:ip", 10, 3600),
                ("booking:ip-email", 5, 86400),
                ("booking:user", 10, 86400),
            ],
        )

    def test_customer_register_policy(self):
        self.assert_policy(
            rate_limit.CUSTOMER_REGISTER_RATE_LIMITS,
            [
                ("customer_register:ip", 5, 3600),
                ("customer_register:ip-email", 3, 86400),
            ],
        )

    def test_customer_resend_verification_policy(self):
        self.assert_policy(
            rate_limit.CUSTOMER_RESEND_VERIFICATION_RATE_LIMITS,
            [
                (
                    "customer_resend_verification:ip",
                    5,
                    3600,
                ),
                (
                    "customer_resend_verification:ip-email",
                    3,
                    86400,
                ),
            ],
        )

    def test_login_policy(self):
        self.assert_policy(
            rate_limit.LOGIN_RATE_LIMITS,
            [
                ("login:ip", 20, 3600),
                ("login:ip-username", 10, 3600),
            ],
        )

    def test_password_reset_policy(self):
        self.assert_policy(
            rate_limit.PASSWORD_RESET_RATE_LIMITS,
            [
                ("password_reset:ip", 5, 3600),
                ("password_reset:ip-email", 3, 86400),
            ],
        )


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class RateLimitedEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.service = self.create_service()
        Staff.objects.create(
            full_name="Rate Limit Cleaner",
            phone="0712345678",
            role="cleaner",
            city="Nairobi",
            is_available=True,
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="rate-limit-account",
            email="rate-limit-account@example.com",
            password="Strong-Account-Password-984!",
        )

    def create_service(self):
        category, _created = ServiceCategory.objects.get_or_create(
            slug="residential",
            defaults={
                "name": "Residential",
                "description": "Residential cleaning services.",
            },
        )
        return Service.objects.create(
            name="Home Cleaning",
            slug="home-cleaning",
            category=category,
            short_description="Professional home cleaning.",
            description="Professional home cleaning service.",
            base_price=2500,
            price_per_hour=1000,
            duration_hours=2,
            is_active=True,
        )

    def contact_data(self, **overrides):
        data = {
            "name": "Alex Customer",
            "email": "alex@example.com",
            "message": "I would like to ask about your services.",
        }
        data.update(overrides)
        return data

    def quote_data(self, **overrides):
        data = {
            "full_name": "Amina Customer",
            "email": "amina@example.com",
            "phone": "+254 700 000 000",
            "service": str(self.service.pk),
            "property_size": "100",
            "rooms": "3",
            "bathrooms": "2",
            "extras": "",
            "address": "12 Green Road",
            "city": "Nairobi",
            "preferred_date": "",
            "preferred_time": "",
        }
        data.update(overrides)
        return data

    def booking_data(self, **overrides):
        data = {
            "full_name": "Amina Customer",
            "email": "amina@example.com",
            "phone": "0712345678",
            "service": self.service.pk,
            "booking_date": (
                date.today() + timedelta(days=10)
            ).isoformat(),
            "booking_time": time(10, 0).isoformat(),
            "property_size": "100",
            "rooms": "3",
            "address": "Booking Address",
            "city": "Nairobi",
            "notes": "",
        }
        data.update(overrides)
        return data

    def registration_data(self, **overrides):
        data = {
            "first_name": "Amina",
            "last_name": "Customer",
            "email": "amina@example.com",
            "phone": "+254700000000",
            "password1": "Strong-Account-Password-984!",
            "password2": "Strong-Account-Password-984!",
        }
        data.update(overrides)
        return data

    def test_contact_ip_scope_exceeded_returns_429_without_sending_email(self):
        with patch.object(
            rate_limit.CONTACT_RATE_LIMITS[0], "limit", 0
        ), patch(
            "apps_core.core.views.send_contact_email"
        ) as send_email:
            response = self.client.post(
                reverse("contact"), self.contact_data()
            )

        self.assertEqual(response.status_code, 429)
        self.assertTemplateUsed(response, "errors/429.html")
        send_email.assert_not_called()

    def test_contact_email_scope_normalizes_identifier(self):
        with patch.object(
            rate_limit.CONTACT_RATE_LIMITS[1], "limit", 1
        ), patch(
            "apps_core.core.views.send_contact_email"
        ) as send_email:
            first = self.client.post(
                reverse("contact"),
                self.contact_data(email=" A@Example.com "),
            )
            second = self.client.post(
                reverse("contact"),
                self.contact_data(email="a@example.com"),
            )

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(send_email.call_count, 1)

    def test_contact_get_is_not_throttled_but_post_is(self):
        with patch.object(
            rate_limit.CONTACT_RATE_LIMITS[0], "limit", 0
        ), patch.object(
            rate_limit.CONTACT_RATE_LIMITS[1], "limit", 0
        ):
            self.assertEqual(
                self.client.get(reverse("contact")).status_code, 200
            )
            response = self.client.post(
                reverse("contact"), self.contact_data()
            )

        self.assertEqual(response.status_code, 429)

    def test_contact_missing_email_still_uses_ip_scope(self):
        data = self.contact_data()
        del data["email"]

        with patch.object(
            rate_limit.CONTACT_RATE_LIMITS[1], "limit", 0
        ), patch.object(
            rate_limit.CONTACT_RATE_LIMITS[0], "limit", 1
        ):
            first = self.client.post(reverse("contact"), data)
            second = self.client.post(reverse("contact"), data)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)

    def test_quote_ip_scope_exceeded_returns_429_without_creating_quote(self):
        with patch.object(
            rate_limit.QUOTE_RATE_LIMITS[0], "limit", 0
        ), patch(
            "apps_core.quotes.views.create_quote_request"
        ) as create_quote:
            response = self.client.post(
                reverse("quote"), self.quote_data()
            )

        self.assertEqual(response.status_code, 429)
        self.assertTemplateUsed(response, "errors/429.html")
        create_quote.assert_not_called()
        self.assertEqual(QuoteRequest.objects.count(), 0)

    def test_quote_email_scope_exceeded_returns_429_without_creating_quote(self):
        with patch.object(
            rate_limit.QUOTE_RATE_LIMITS[1], "limit", 0
        ), patch(
            "apps_core.quotes.views.create_quote_request"
        ) as create_quote:
            response = self.client.post(
                reverse("quote"), self.quote_data()
            )

        self.assertEqual(response.status_code, 429)
        create_quote.assert_not_called()
        self.assertEqual(QuoteRequest.objects.count(), 0)

    def test_quote_authenticated_user_scope_exceeded_returns_429(self):
        self.client.force_login(self.user)

        with patch.object(
            rate_limit.QUOTE_RATE_LIMITS[2], "limit", 0
        ), patch(
            "apps_core.quotes.views.create_quote_request"
        ) as create_quote:
            response = self.client.post(
                reverse("quote"), self.quote_data()
            )

        self.assertEqual(response.status_code, 429)
        create_quote.assert_not_called()
        self.assertEqual(QuoteRequest.objects.count(), 0)

    def test_quote_user_scope_does_not_apply_to_anonymous_users(self):
        with patch.object(
            rate_limit.QUOTE_RATE_LIMITS[2], "limit", 0
        ), patch(
            "apps_core.quotes.views.create_quote_request"
        ) as create_quote:
            response = self.client.post(
                reverse("quote"), self.quote_data()
            )

        self.assertEqual(response.status_code, 302)
        create_quote.assert_called_once()

    def test_booking_ip_scope_exceeded_returns_429_without_side_effects(self):
        with patch.object(
            rate_limit.BOOKING_RATE_LIMITS[0], "limit", 0
        ), patch(
            "apps_core.bookings.views.get_or_create_customer"
        ) as get_customer, patch(
            "apps_core.bookings.views.create_booking"
        ) as create_booking:
            response = self.client.post(
                reverse("booking"), self.booking_data()
            )

        self.assertEqual(response.status_code, 429)
        self.assertTemplateUsed(response, "errors/429.html")
        get_customer.assert_not_called()
        create_booking.assert_not_called()
        self.assertEqual(Customer.objects.count(), 0)
        self.assertEqual(Booking.objects.count(), 0)

    def test_booking_email_scope_exceeded_returns_429_without_side_effects(self):
        with patch.object(
            rate_limit.BOOKING_RATE_LIMITS[1], "limit", 0
        ), patch(
            "apps_core.bookings.views.get_or_create_customer"
        ) as get_customer, patch(
            "apps_core.bookings.views.create_booking"
        ) as create_booking:
            response = self.client.post(
                reverse("booking"), self.booking_data()
            )

        self.assertEqual(response.status_code, 429)
        get_customer.assert_not_called()
        create_booking.assert_not_called()
        self.assertEqual(Customer.objects.count(), 0)
        self.assertEqual(Booking.objects.count(), 0)

    def test_booking_authenticated_user_scope_exceeded_returns_429(self):
        self.client.force_login(self.user)

        with patch.object(
            rate_limit.BOOKING_RATE_LIMITS[2], "limit", 0
        ), patch(
            "apps_core.bookings.views.create_booking"
        ) as create_booking:
            response = self.client.post(
                reverse("booking"), self.booking_data()
            )

        self.assertEqual(response.status_code, 429)
        create_booking.assert_not_called()
        self.assertEqual(Booking.objects.count(), 0)

    def test_booking_user_scope_does_not_apply_to_anonymous_users(self):
        with patch.object(
            rate_limit.BOOKING_RATE_LIMITS[2], "limit", 0
        ):
            response = self.client.post(
                reverse("booking"), self.booking_data()
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Booking.objects.count(), 1)

    def test_registration_ip_scope_exceeded_returns_429_without_creating_user(self):
        with patch.object(
            rate_limit.CUSTOMER_REGISTER_RATE_LIMITS[0], "limit", 0
        ), patch(
            "apps_core.customers.views.create_pending_customer_registration"
        ) as create_registration, patch(
            "apps_core.customers.views._send_verification_email"
        ) as send_verification:
            response = self.client.post(
                reverse("customer_register"), self.registration_data()
            )

        self.assertEqual(response.status_code, 429)
        self.assertTemplateUsed(response, "errors/429.html")
        create_registration.assert_not_called()
        send_verification.assert_not_called()
        self.assertEqual(User.objects.count(), 1)

    def test_registration_email_scope_exceeded_returns_429_without_creating_user(self):
        with patch.object(
            rate_limit.CUSTOMER_REGISTER_RATE_LIMITS[1], "limit", 0
        ), patch(
            "apps_core.customers.views.create_pending_customer_registration"
        ) as create_registration, patch(
            "apps_core.customers.views._send_verification_email"
        ) as send_verification:
            response = self.client.post(
                reverse("customer_register"), self.registration_data()
            )

        self.assertEqual(response.status_code, 429)
        create_registration.assert_not_called()
        send_verification.assert_not_called()
        self.assertEqual(User.objects.count(), 1)

    def test_registration_get_is_not_throttled_but_post_is(self):
        with patch.object(
            rate_limit.CUSTOMER_REGISTER_RATE_LIMITS[0], "limit", 0
        ), patch.object(
            rate_limit.CUSTOMER_REGISTER_RATE_LIMITS[1], "limit", 0
        ):
            self.assertEqual(
                self.client.get(reverse("customer_register")).status_code,
                200,
            )
            response = self.client.post(
                reverse("customer_register"), self.registration_data()
            )

        self.assertEqual(response.status_code, 429)

    def test_resend_verification_ip_scope_exceeded_returns_429_without_email(self):
        with patch.object(
            rate_limit.CUSTOMER_RESEND_VERIFICATION_RATE_LIMITS[0],
            "limit",
            0,
        ), patch(
            "apps_core.customers.views._send_verification_email"
        ) as send_verification:
            response = self.client.post(
                reverse("customer_resend_verification"),
                {"email": "amina@example.com"},
            )

        self.assertEqual(response.status_code, 429)
        send_verification.assert_not_called()
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_verification_email_scope_exceeded_returns_429_without_email(self):
        with patch.object(
            rate_limit.CUSTOMER_RESEND_VERIFICATION_RATE_LIMITS[1],
            "limit",
            0,
        ), patch(
            "apps_core.customers.views._send_verification_email"
        ) as send_verification:
            response = self.client.post(
                reverse("customer_resend_verification"),
                {"email": "amina@example.com"},
            )

        self.assertEqual(response.status_code, 429)
        send_verification.assert_not_called()
        self.assertEqual(len(mail.outbox), 0)

    def test_login_ip_scope_exceeded_returns_429_without_authenticating(self):
        with patch.object(
            rate_limit.LOGIN_RATE_LIMITS[0], "limit", 0
        ):
            response = self.client.post(
                reverse("login"),
                {
                    "username": "nobody@example.com",
                    "password": "not-the-password",
                },
            )

        self.assertEqual(response.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_email_scope_exceeded_returns_429_without_authenticating(self):
        with patch.object(
            rate_limit.LOGIN_RATE_LIMITS[1], "limit", 0
        ):
            response = self.client.post(
                reverse("login"),
                {
                    "username": "nobody@example.com",
                    "password": "not-the-password",
                },
            )

        self.assertEqual(response.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_password_reset_ip_scope_exceeded_returns_429_without_email(self):
        with patch.object(
            rate_limit.PASSWORD_RESET_RATE_LIMITS[0], "limit", 0
        ):
            response = self.client.post(
                reverse("password_reset"),
                {"email": self.user.email},
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(mail.outbox), 0)

    def test_password_reset_email_scope_exceeded_returns_429_without_email(self):
        with patch.object(
            rate_limit.PASSWORD_RESET_RATE_LIMITS[1], "limit", 0
        ):
            response = self.client.post(
                reverse("password_reset"),
                {"email": self.user.email},
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(mail.outbox), 0)

    def test_endpoint_counters_are_independent(self):
        with patch.object(
            rate_limit.CONTACT_RATE_LIMITS[0], "limit", 0
        ), patch(
            "apps_core.core.views.send_contact_email"
        ):
            contact_response = self.client.post(
                reverse("contact"), self.contact_data()
            )
            quote_response = self.client.post(
                reverse("quote"), self.quote_data()
            )

        self.assertEqual(contact_response.status_code, 429)
        self.assertEqual(quote_response.status_code, 302)
        self.assertEqual(QuoteRequest.objects.count(), 1)
