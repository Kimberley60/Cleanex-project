import re
from unittest.mock import patch

from django.contrib import admin
from django.core import mail
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from apps_core.core.admin import ContactEnquiryAdmin
from apps_core.core.models import ContactEnquiry
from apps_core.core.services import (
    CONTACT_ACKNOWLEDGEMENT_EMAIL_SUBJECT,
    CONTACT_EMAIL_SUBJECT,
    send_contact_acknowledgement_email,
)
from apps_core.customers.models import Customer


class FooterTests(TestCase):
    def test_footer_renders_named_destinations_and_confirmed_contact(self):
        response = self.client.get(reverse("home"))
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        footer_match = re.search(
            r'<footer class="site-footer">.*?</footer>',
            content,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(footer_match)
        footer = footer_match.group()
        expected_destinations = (
            "home",
            "services",
            "quote",
            "contact",
        )
        for route_name in expected_destinations:
            with self.subTest(route_name=route_name):
                self.assertIn(f'href="{reverse(route_name)}"', footer)

        public_email = "cleanexbusiness@gmail.com"
        self.assertIn(public_email, footer)
        self.assertIn(f'href="mailto:{public_email}"', footer)
        self.assertNotIn("+254 700 000 000", footer)
        self.assertNotIn("Nairobi, Kenya", footer)
        self.assertIn(
            f"&copy; {timezone.localtime().year} CLEANEX.",
            footer,
        )

    def test_contact_page_functionality_remains_available(self):
        response = self.client.get(reverse("contact"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "pages/contact.html")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')


class HealthCheckTests(TestCase):
    def test_health_check_returns_ok(self):
        response = self.client.get(reverse("health_check"))

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {"status": "ok"})


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="CLEANEX <test@example.com>",
    CONTACT_RECIPIENT_EMAIL="support@example.com",
)
class ContactFormTests(TestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse("contact")
        self.valid_data = {
            "name": "Alex Customer",
            "email": "alex@example.com",
            "message": "I would like to ask about your services.",
        }

    def test_contact_page_get_and_named_url(self):
        self.assertEqual(resolve(self.url).url_name, "contact")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "pages/contact.html")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'for="id_name"')
        self.assertContains(response, 'for="id_email"')
        self.assertContains(response, 'for="id_message"')

    def test_valid_guest_submission_sends_business_and_acknowledgement_emails_then_redirects(self):
        response = self.client.post(self.url, self.valid_data, follow=True)

        self.assertRedirects(response, self.url)
        self.assertEqual(len(mail.outbox), 2)

        business_email = mail.outbox[0]
        self.assertEqual(business_email.to, ["support@example.com"])
        self.assertEqual(business_email.subject, CONTACT_EMAIL_SUBJECT)
        self.assertIn("Alex Customer", business_email.body)
        self.assertIn("alex@example.com", business_email.body)
        self.assertIn(self.valid_data["message"], business_email.body)

        acknowledgement_email = mail.outbox[1]
        self.assertEqual(acknowledgement_email.to, ["alex@example.com"])
        self.assertEqual(
            acknowledgement_email.subject,
            CONTACT_ACKNOWLEDGEMENT_EMAIL_SUBJECT,
        )
        self.assertIn("Alex Customer", acknowledgement_email.body)
        self.assertIn("CLEANEX Team", acknowledgement_email.body)
        self.assertNotIn(
            self.valid_data["message"],
            acknowledgement_email.body,
        )

        self.assertContains(
            response,
            "Your enquiry has been received",
            status_code=200,
        )
        self.assertContains(
            response,
            "Our team will review it",
            status_code=200,
        )
        self.assertEqual(ContactEnquiry.objects.count(), 1)
        enquiry = ContactEnquiry.objects.get()
        self.assertEqual(enquiry.name, "Alex Customer")
        self.assertEqual(enquiry.email, "alex@example.com")
        self.assertEqual(enquiry.message, self.valid_data["message"])
        self.assertIsNotNone(enquiry.created_at)
        self.assertEqual(Customer.objects.count(), 0)

    def test_valid_authenticated_submission_does_not_create_customer(self):
        from django.contrib.auth import get_user_model

        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="contact-user",
            email="account@example.com",
            password="contact-test-password",
        )
        self.client.force_login(user)

        response = self.client.post(self.url, self.valid_data, follow=True)

        self.assertRedirects(response, self.url)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(ContactEnquiry.objects.count(), 1)
        self.assertEqual(Customer.objects.count(), 0)

    def test_missing_and_invalid_fields_do_not_send_email(self):
        cases = (
            ({}, "This field is required."),
            ({**self.valid_data, "name": ""}, "This field is required."),
            (
                {**self.valid_data, "email": "not-an-email"},
                "Enter a valid email address.",
            ),
            ({**self.valid_data, "message": ""}, "This field is required."),
        )
        for data, expected_error in cases:
            with self.subTest(data=data):
                response = self.client.post(self.url, data)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, expected_error)
                self.assertEqual(len(mail.outbox), 0)
                self.assertEqual(ContactEnquiry.objects.count(), 0)

    def test_whitespace_only_message_is_rejected(self):
        response = self.client.post(
            self.url,
            {**self.valid_data, "message": " \t\r\n "},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(ContactEnquiry.objects.count(), 0)

    def test_oversized_input_is_rejected(self):
        cases = (
            {**self.valid_data, "name": "n" * 101},
            {**self.valid_data, "email": f"{'a' * 245}@example.com"},
            {**self.valid_data, "message": "m" * 5001},
        )
        for data in cases:
            lengths = {key: len(value) for key, value in data.items()}
            with self.subTest(lengths=lengths):
                response = self.client.post(self.url, data)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(mail.outbox), 0)
                self.assertEqual(ContactEnquiry.objects.count(), 0)

    def test_submission_trims_surrounding_whitespace(self):
        response = self.client.post(
            self.url,
            {
                "name": "  Alex Customer  ",
                "email": "  alex@example.com  ",
                "message": "  A trimmed message.  ",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[1].to, ["alex@example.com"])
        self.assertIn("Alex Customer", mail.outbox[0].body)
        self.assertIn("alex@example.com", mail.outbox[0].body)
        self.assertIn("A trimmed message.", mail.outbox[0].body)
        self.assertNotIn("  A trimmed message.  ", mail.outbox[0].body)
        enquiry = ContactEnquiry.objects.get()
        self.assertEqual(enquiry.name, "Alex Customer")
        self.assertEqual(enquiry.email, "alex@example.com")
        self.assertEqual(enquiry.message, "A trimmed message.")

    def test_business_email_failure_preserves_enquiry_and_still_sends_acknowledgement(self):
        with patch(
            "apps_core.core.views.send_contact_email",
            side_effect=OSError("Private SMTP backend details."),
        ):
            response = self.client.post(
                self.url,
                self.valid_data,
                follow=True,
            )

        self.assertRedirects(response, self.url)
        self.assertEqual(ContactEnquiry.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["alex@example.com"])
        self.assertEqual(
            mail.outbox[0].subject,
            CONTACT_ACKNOWLEDGEMENT_EMAIL_SUBJECT,
        )
        self.assertContains(
            response,
            "Your enquiry has been received",
            status_code=200,
        )
        self.assertNotContains(
            response,
            "could not send the email notification",
            status_code=200,
        )
        self.assertNotContains(
            response,
            "Your message has been sent.",
            status_code=200,
        )
        self.assertNotContains(
            response,
            "Private SMTP backend details.",
            status_code=200,
        )

    @override_settings(CONTACT_RECIPIENT_EMAIL="")
    def test_missing_recipient_preserves_enquiry_and_still_sends_acknowledgement(self):
        response = self.client.post(self.url, self.valid_data, follow=True)

        self.assertRedirects(response, self.url)
        self.assertEqual(ContactEnquiry.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["alex@example.com"])
        self.assertEqual(
            mail.outbox[0].subject,
            CONTACT_ACKNOWLEDGEMENT_EMAIL_SUBJECT,
        )
        self.assertContains(
            response,
            "Your enquiry has been received",
            status_code=200,
        )
        self.assertNotContains(response, "not configured", status_code=200)
        self.assertNotContains(
            response,
            "could not send the email notification",
            status_code=200,
        )
        self.assertNotContains(
            response,
            "Your message has been sent.",
            status_code=200,
        )

    def test_post_requires_csrf_token_when_csrf_checks_are_enabled(self):
        csrf_client = Client(enforce_csrf_checks=True)

        rejected_response = csrf_client.post(self.url, self.valid_data)
        self.assertEqual(rejected_response.status_code, 403)

        page_response = csrf_client.get(self.url)
        self.assertEqual(page_response.status_code, 200)
        csrf_token = csrf_client.cookies["csrftoken"].value
        accepted_response = csrf_client.post(
            self.url,
            self.valid_data,
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(accepted_response.status_code, 302)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(ContactEnquiry.objects.count(), 1)

    def test_invalid_http_methods_are_rejected(self):
        response = self.client.put(self.url, self.valid_data)

        self.assertEqual(response.status_code, 405)
        self.assertEqual(len(mail.outbox), 0)

    def test_repeated_posts_send_one_email_pair_per_request(self):
        first_response = self.client.post(self.url, self.valid_data)
        second_response = self.client.post(self.url, self.valid_data)

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(len(mail.outbox), 4)
        self.assertEqual(ContactEnquiry.objects.count(), 2)

    def test_valid_submission_saves_exactly_one_enquiry(self):
        response = self.client.post(self.url, self.valid_data)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ContactEnquiry.objects.count(), 1)
        enquiry = ContactEnquiry.objects.get()
        self.assertEqual(enquiry.name, "Alex Customer")
        self.assertEqual(enquiry.email, "alex@example.com")
        self.assertEqual(enquiry.message, self.valid_data["message"])
        self.assertIsNotNone(enquiry.created_at)

    def test_invalid_submission_saves_no_enquiry(self):
        response = self.client.post(
            self.url,
            {"name": "", "email": "not-an-email", "message": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ContactEnquiry.objects.count(), 0)

    def test_successful_submission_saves_enquiry_and_sends_business_and_acknowledgement_emails(self):
        response = self.client.post(self.url, self.valid_data)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(ContactEnquiry.objects.count(), 1)

    def test_acknowledgement_email_failure_preserves_enquiry_and_business_email(self):
        with patch(
            "apps_core.core.views.send_contact_acknowledgement_email",
            side_effect=OSError("Private acknowledgement details."),
        ):
            response = self.client.post(
                self.url,
                self.valid_data,
                follow=True,
            )

        self.assertRedirects(response, self.url)
        self.assertEqual(ContactEnquiry.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["support@example.com"])
        self.assertEqual(mail.outbox[0].subject, CONTACT_EMAIL_SUBJECT)
        self.assertContains(
            response,
            "Your enquiry has been received",
            status_code=200,
        )
        self.assertNotContains(
            response,
            "could not send the email notification",
            status_code=200,
        )
        self.assertNotContains(
            response,
            "Your message has been sent.",
            status_code=200,
        )
        self.assertNotContains(
            response,
            "Private acknowledgement details.",
            status_code=200,
        )

    def test_both_email_attempts_fail_preserves_enquiry_and_confirms_receipt(self):
        with (
            patch(
                "apps_core.core.views.send_contact_email",
                side_effect=OSError("Private business details."),
            ),
            patch(
                "apps_core.core.views.send_contact_acknowledgement_email",
                side_effect=OSError("Private acknowledgement details."),
            ),
        ):
            response = self.client.post(
                self.url,
                self.valid_data,
                follow=True,
            )

        self.assertRedirects(response, self.url)
        self.assertEqual(ContactEnquiry.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(
            response,
            "Your enquiry has been received",
            status_code=200,
        )
        self.assertNotContains(
            response,
            "could not send the email notification",
            status_code=200,
        )
        self.assertNotContains(
            response,
            "Your message has been sent.",
            status_code=200,
        )
        self.assertNotContains(
            response,
            "Private business details.",
            status_code=200,
        )
        self.assertNotContains(
            response,
            "Private acknowledgement details.",
            status_code=200,
        )

    def test_send_contact_acknowledgement_email_uses_visitor_recipient_template_and_name_context(self):
        with patch(
            "apps_core.core.services.send_template_email",
            return_value=1,
        ) as send_email:
            sent_count = send_contact_acknowledgement_email(
                name="Alex Customer",
                email="alex@example.com",
            )

        self.assertEqual(sent_count, 1)
        send_email.assert_called_once_with(
            subject=CONTACT_ACKNOWLEDGEMENT_EMAIL_SUBJECT,
            text_template="emails/contact_acknowledgement.txt",
            recipient_list=["alex@example.com"],
            context={"name": "Alex Customer"},
        )

    def test_send_contact_acknowledgement_email_rejects_unaccepted_results(self):
        for sent_count in (0, 2, "1"):
            with self.subTest(sent_count=sent_count), patch(
                "apps_core.core.services.send_template_email",
                return_value=sent_count,
            ):
                with self.assertRaises(RuntimeError):
                    send_contact_acknowledgement_email(
                        name="Alex Customer",
                        email="alex@example.com",
                    )


class ContactEnquiryAdminTests(TestCase):
    def test_contact_enquiry_admin_registration_and_read_only(self):
        self.assertTrue(admin.site.is_registered(ContactEnquiry))
        model_admin = admin.site._registry[ContactEnquiry]
        self.assertIsInstance(model_admin, ContactEnquiryAdmin)
        self.assertEqual(
            model_admin.list_display,
            ("name", "email", "created_at"),
        )
        self.assertEqual(model_admin.search_fields, ("name", "email"))
        self.assertEqual(model_admin.date_hierarchy, "created_at")
        self.assertEqual(
            set(model_admin.readonly_fields),
            {"name", "email", "message", "created_at"},
        )

        request = RequestFactory().get("/admin/core/contactenquiry/")
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
