from datetime import date, time, timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib import admin
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse
from unittest.mock import Mock, patch

from apps_core.bookings.admin import (
    BookingAdmin,
    BookingExtraAdmin,
    BookingExtraAdminForm,
    BookingExtraInline,
)
from apps_core.bookings.models import Booking, BookingExtra
from apps_core.bookings.services import change_booking_status
from apps_core.bookings.views import BookingTrackView
from apps_core.customers.models import Customer
from apps_core.payments.models import Payment
from apps_core.services.models import ExtraService, Service, ServiceCategory
from apps_core.staff.models import Staff


User = get_user_model()


class BookingViewSecurityTests(TestCase):

    def setUp(self):
        cache.clear()
        self.category = ServiceCategory.objects.create(
            name="Residential",
            slug="residential",
            description="Residential cleaning services.",
        )

        self.service = Service.objects.create(
            name="Home Cleaning",
            slug="home-cleaning",
            category=self.category,
            short_description="Professional home cleaning.",
            description="Professional home cleaning service.",
            base_price=2500,
            price_per_hour=1000,
            duration_hours=2,
            is_active=True,
        )

        self.staff = Staff.objects.create(
            full_name="Test Cleaner",
            phone="0712345678",
            role="cleaner",
            city="Nairobi",
            is_available=True,
            is_active=True,
        )

        self.customer = Customer.objects.create(
            full_name="Original Customer",
            email="customer@example.com",
            phone="0700000000",
            address="Original Address",
            city="Nairobi",
        )

    def create_booking(self, customer=None):
        customer = customer or self.customer

        return Booking.objects.create(
            customer=customer,
            contact_name=customer.full_name,
            contact_email=customer.email,
            contact_phone=customer.phone,
            service=self.service,
            staff=self.staff,
            booking_date=date.today() + timedelta(days=5),
            booking_time=time(10, 0),
            address="Test Address",
            city="Nairobi",
            property_size=100,
            rooms=3,
            total_price=3000,
            status="pending",
        )

    def booking_submission_data(self, *, email, name, booking_day=10):
        return {
            "full_name": name,
            "email": email,
            "phone": "0712345678",
            "service": self.service.pk,
            "booking_date": (
                date.today() + timedelta(days=booking_day)
            ).isoformat(),
            "booking_time": "10:00",
            "property_size": "100",
            "rooms": "3",
            "address": "Booking Address",
            "city": "Nairobi",
            "notes": "",
        }

    def submit_booking(self, *, email, name, booking_day=10):
        return self.client.post(
            reverse("booking"),
            self.booking_submission_data(
                email=email,
                name=name,
                booking_day=booking_day,
            ),
        )

    def test_guest_booking_with_linked_customer_email_is_unattributed(self):
        user = User.objects.create_user(
            username="existing-account",
            email="account@example.com",
            password="test-password",
        )
        self.customer.user = user
        self.customer.email = user.email
        self.customer.save(update_fields=["user", "email"])

        response = self.submit_booking(
            email=user.email,
            name="Guest Requester",
        )

        self.assertEqual(response.status_code, 302)
        booking = Booking.objects.get(contact_name="Guest Requester")
        self.assertEqual(booking.customer_id, self.customer.pk)
        self.assertIsNone(booking.created_by_id)

    def test_guest_booking_with_new_email_is_unattributed(self):
        response = self.submit_booking(
            email="new-guest@example.com",
            name="New Guest",
        )

        self.assertEqual(response.status_code, 302)
        booking = Booking.objects.get(contact_name="New Guest")
        self.assertIsNone(booking.created_by_id)
        self.assertEqual(booking.customer.email, "new-guest@example.com")

    def test_authenticated_booking_is_attributed_to_signed_in_user(self):
        user = User.objects.create_user(
            username="booking-account",
            email="account@example.com",
            password="test-password",
        )
        self.customer.user = user
        self.customer.email = user.email
        self.customer.save(update_fields=["user", "email"])
        self.client.force_login(user)

        response = self.submit_booking(
            email=user.email,
            name="Account Holder",
        )

        self.assertEqual(response.status_code, 302)
        booking = Booking.objects.get(contact_name="Account Holder")
        self.assertEqual(booking.customer_id, self.customer.pk)
        self.assertEqual(booking.created_by_id, user.pk)

    def test_authenticated_booking_uses_own_customer_and_contact_snapshots(self):
        user = User.objects.create_user(
            username="different-contact-account",
            email="account@example.com",
            password="test-password",
        )
        own_customer = Customer.objects.create(
            user=user,
            full_name="Account Profile",
            email=user.email,
            phone="0700000001",
            address="Profile Address",
            city="Nairobi",
        )
        other_customer = Customer.objects.create(
            full_name="Other Email Owner",
            email="booking-contact@example.com",
            phone="0700000002",
            address="Other Address",
            city="Mombasa",
        )
        self.client.force_login(user)

        response = self.submit_booking(
            email="booking-contact@example.com",
            name="Different Contact",
        )

        self.assertEqual(response.status_code, 302)
        booking = Booking.objects.get(contact_name="Different Contact")
        self.assertEqual(booking.customer_id, own_customer.pk)
        self.assertNotEqual(booking.customer_id, other_customer.pk)
        self.assertEqual(booking.created_by_id, user.pk)
        self.assertEqual(booking.contact_email, "booking-contact@example.com")
        self.assertEqual(booking.contact_name, "Different Contact")
        self.assertEqual(booking.contact_phone, "0712345678")

    def test_authenticated_user_without_customer_profile_cannot_book(self):
        user = User.objects.create_user(
            username="booking-no-profile",
            email="no-profile@example.com",
        )
        self.client.force_login(user)
        customer_count = Customer.objects.count()

        response = self.submit_booking(
            email="new-customer@example.com",
            name="Unlinked Account",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "does not have a customer profile")
        self.assertEqual(Customer.objects.count(), customer_count)
        self.assertFalse(
            Customer.objects.filter(email="new-customer@example.com").exists()
        )
        self.assertFalse(Booking.objects.filter(created_by=user).exists())

    def test_guest_booking_reuses_case_insensitive_customer_email(self):
        self.customer.email = "original-customer@example.com"
        self.customer.save(update_fields=["email"])

        response = self.submit_booking(
            email="ORIGINAL-CUSTOMER@EXAMPLE.COM",
            name="Guest Contact Snapshot",
        )

        self.assertEqual(response.status_code, 302)
        booking = Booking.objects.get(contact_name="Guest Contact Snapshot")
        self.assertEqual(booking.customer_id, self.customer.pk)
        self.assertIsNone(booking.created_by_id)
        self.assertEqual(
            booking.contact_email,
            "ORIGINAL-CUSTOMER@EXAMPLE.COM",
        )

    def test_admin_cannot_add_booking_without_booking_service(self):
        request = RequestFactory().get("/admin/bookings/booking/add/")
        request.user = User.objects.create_superuser(
            username="booking-add-admin",
            email="booking-add-admin@example.com",
            password="test-password",
        )
        model_admin = BookingAdmin(Booking, admin.site)

        self.assertFalse(model_admin.has_add_permission(request))

    def test_success_page_requires_session_booking(self):
        booking = self.create_booking()

        response = self.client.get(
            reverse("booking_success"),
            {"booking": booking.pk},
        )

        self.assertEqual(
            response.status_code,
            404,
        )


    def test_success_page_allows_creating_session_booking(self):
        booking = self.create_booking()

        session = self.client.session
        session["booking_success_id"] = booking.pk
        session.save()

        response = self.client.get(
            reverse("booking_success"),
            {"booking": booking.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            booking.booking_reference,
        )

    def test_success_page_uses_deterministic_latest_payment(self):
        booking = self.create_booking()

        older_payment = Payment.objects.create(
            booking=booking,
            amount=Decimal("5000.00"),
            phone_number="0712345678",
            status="failed",
            mpesa_receipt="OLDRECEIPT",
            result_code="1032",
            result_description="Previous payment attempt failed.",
        )
        newer_payment = Payment.objects.create(
            booking=booking,
            amount=Decimal("5000.00"),
            phone_number="0712345678",
            status="paid",
            mpesa_receipt="NEWRECEIPT",
        )

        Payment.objects.filter(
            pk__in=[older_payment.pk, newer_payment.pk]
        ).update(created_at=booking.created_at)

        session = self.client.session
        session["booking_success_id"] = booking.pk
        session.save()

        response = self.client.get(
            reverse("booking_success"),
            {"booking": booking.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["latest_payment"].pk,
            newer_payment.pk,
        )
        self.assertContains(response, "NEWRECEIPT")
        self.assertNotContains(response, "OLDRECEIPT")

    def test_success_page_refresh_does_not_create_payment(self):
        booking = self.create_booking()

        session = self.client.session
        session["booking_success_id"] = booking.pk
        session.save()

        response = self.client.get(
            reverse("booking_success"),
            {"booking": booking.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Payment.objects.filter(booking=booking).count(),
            0,
        )

    def test_success_page_shows_retry_for_failed_payment(self):
        booking = self.create_booking()
        Payment.objects.create(
            booking=booking,
            amount=Decimal("3000.00"),
            phone_number="0712345678",
            status="failed",
            result_code="1019",
            result_description="Transaction Expired",
        )

        session = self.client.session
        session["booking_success_id"] = booking.pk
        session.save()

        response = self.client.get(
            reverse("booking_success"),
            {"booking": booking.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Try M-Pesa Payment Again")
        self.assertContains(
            response,
            f"/payments/booking/{booking.pk}/initiate/",
        )

    def test_success_page_hides_retry_for_processing_payment(self):
        booking = self.create_booking()
        Payment.objects.create(
            booking=booking,
            amount=Decimal("3000.00"),
            phone_number="0712345678",
            status="processing",
            checkout_request_id="success-checkout-123",
            merchant_request_id="success-merchant-123",
        )

        session = self.client.session
        session["booking_success_id"] = booking.pk
        session.save()

        response = self.client.get(
            reverse("booking_success"),
            {"booking": booking.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Check M-Pesa status")
        self.assertNotContains(response, "Try M-Pesa Payment Again")
        self.assertNotContains(
            response,
            f"/payments/booking/{booking.pk}/initiate/",
        )

    def test_success_page_hides_retry_for_unknown_payment(self):
        booking = self.create_booking()
        Payment.objects.create(
            booking=booking,
            amount=Decimal("3000.00"),
            phone_number="0712345678",
            status="unknown",
            checkout_request_id="success-unknown-123",
            merchant_request_id="success-merchant-123",
        )

        session = self.client.session
        session["booking_success_id"] = booking.pk
        session.save()

        response = self.client.get(
            reverse("booking_success"),
            {"booking": booking.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Check M-Pesa status")
        self.assertNotContains(response, "Try M-Pesa Payment Again")
        self.assertNotContains(
            response,
            f"/payments/booking/{booking.pk}/initiate/",
        )

    def test_success_page_hides_retry_for_paid_payment(self):
        booking = self.create_booking()
        Payment.objects.create(
            booking=booking,
            amount=Decimal("3000.00"),
            phone_number="0712345678",
            status="paid",
            mpesa_receipt="SUCCESS123",
        )

        session = self.client.session
        session["booking_success_id"] = booking.pk
        session.save()

        response = self.client.get(
            reverse("booking_success"),
            {"booking": booking.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "payment has been confirmed")
        self.assertNotContains(response, "Try M-Pesa Payment Again")
        self.assertNotContains(response, "Pay Now with M-Pesa")
        self.assertNotContains(
            response,
            f"/payments/booking/{booking.pk}/initiate/",
        )

    def test_success_page_cannot_access_different_booking(self):
        first_booking = self.create_booking()

        second_customer = Customer.objects.create(
            full_name="Second Customer",
            email="second@example.com",
            phone="0711111111",
            address="Second Address",
            city="Nairobi",
        )

        second_booking = self.create_booking(
            customer=second_customer
        )

        session = self.client.session
        session["booking_success_id"] = first_booking.pk
        session.save()

        response = self.client.get(
            reverse("booking_success"),
            {"booking": second_booking.pk},
        )

        self.assertEqual(response.status_code, 404)

    def test_invalid_success_booking_id_is_safe(self):
        response = self.client.get(
            reverse("booking_success"),
            {"booking": "not-a-number"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response,
            "not-a-number",
        )


class BookingAdminLifecycleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="booking-lifecycle-admin",
            email="booking-lifecycle-admin@example.com",
            password="test-password",
        )
        self.request = RequestFactory().get("/admin/bookings/booking/")
        self.request.user = self.user
        self.customer = Customer.objects.create(
            full_name="Lifecycle Customer",
            email="lifecycle-customer@example.com",
            phone="0700000000",
            address="Test Address",
            city="Nairobi",
        )
        self.service = Service.objects.create(
            name="Lifecycle Cleaning",
            slug="lifecycle-cleaning",
            description="Service for Admin lifecycle tests.",
            base_price=Decimal("3000.00"),
            duration_hours=2,
            is_active=True,
        )
        self.staff = Staff.objects.create(
            full_name="Lifecycle Cleaner",
            phone="0712345678",
            role="cleaner",
            city="Nairobi",
            is_available=True,
            is_active=True,
        )
        self.booking = Booking.objects.create(
            customer=self.customer,
            service=self.service,
            staff=self.staff,
            booking_date=date.today() + timedelta(days=5),
            booking_time=time(10, 0),
            address="Test Address",
            city="Nairobi",
            total_price=Decimal("3000.00"),
        )

    def test_existing_schedule_and_assignment_fields_are_read_only(self):
        model_admin = BookingAdmin(Booking, admin.site)
        form_class = model_admin.get_form(self.request, self.booking)

        for field in ("service", "staff", "booking_date", "booking_time"):
            self.assertIn(field, model_admin.get_readonly_fields(
                self.request,
                self.booking,
            ))
            self.assertNotIn(field, form_class.base_fields)

        self.assertIn("notes", form_class.base_fields)
        self.assertIn("contact_name", form_class.base_fields)

    def test_status_action_uses_central_transition_service(self):
        model_admin = BookingAdmin(Booking, admin.site)
        model_admin.message_user = Mock()

        with patch(
            "apps_core.bookings.admin.change_booking_status",
            wraps=change_booking_status,
        ) as transition:
            model_admin.confirm_bookings(
                self.request,
                Booking.objects.filter(pk=self.booking.pk),
            )

        transition.assert_called_once()
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "confirmed")


class BookingAdminRetentionTests(TestCase):
    def test_booking_delete_and_bulk_delete_are_disabled(self):
        user = User.objects.create_superuser(
            username="booking-admin",
            email="booking-admin@example.com",
            password="test-password",
        )
        request = RequestFactory().get("/admin/bookings/booking/")
        request.user = user
        model_admin = BookingAdmin(Booking, admin.site)

        self.assertFalse(model_admin.has_delete_permission(request))
        self.assertNotIn("delete_selected", model_admin.get_actions(request))


class BookingExtraAdminRetentionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="booking-extra-admin",
            email="booking-extra-admin@example.com",
            password="test-password",
        )
        self.request = RequestFactory().get("/admin/bookings/bookingextra/")
        self.request.user = self.user
        self.customer = Customer.objects.create(
            full_name="Extra Admin Customer",
            email="extra-admin-customer@example.com",
            phone="0712345678",
            address="Test Address",
            city="Nairobi",
        )
        self.service = Service.objects.create(
            name="Extra Admin Cleaning",
            slug="extra-admin-cleaning",
            description="Service for booking extra admin tests.",
            base_price=Decimal("3000.00"),
            duration_hours=2,
            is_active=True,
        )
        self.booking = Booking.objects.create(
            customer=self.customer,
            service=self.service,
            booking_date=date.today() + timedelta(days=5),
            booking_time=time(10, 0),
            address="Test Address",
            city="Nairobi",
            total_price=Decimal("3000.00"),
        )
        self.extra_service = ExtraService.objects.create(
            name="Original Extra",
            price=Decimal("300.00"),
        )
        self.other_extra_service = ExtraService.objects.create(
            name="Other Extra",
            price=Decimal("450.00"),
        )
        self.booking_extra = BookingExtra.objects.create(
            booking=self.booking,
            extra_service=self.extra_service,
            extra_service_name="Original Extra",
            extra_service_price=Decimal("300.00"),
        )

    def test_existing_booking_extra_delete_is_disabled(self):
        model_admin = BookingExtraAdmin(BookingExtra, admin.site)

        self.assertFalse(model_admin.has_delete_permission(
            self.request,
            self.booking_extra,
        ))
        self.assertNotIn(
            "delete_selected",
            model_admin.get_actions(self.request),
        )

    def test_existing_booking_extra_cannot_be_moved_to_another_booking(self):
        other_booking = Booking.objects.create(
            customer=self.customer,
            service=self.service,
            booking_date=date.today() + timedelta(days=7),
            booking_time=time(10, 0),
            address="Other Booking Address",
            city="Nairobi",
            total_price=Decimal("3000.00"),
        )
        form = BookingExtraAdminForm(
            data={
                "booking": other_booking.pk,
                "extra_service": self.extra_service.pk,
            },
            instance=self.booking_extra,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("booking", form.errors)

        self.booking_extra.refresh_from_db()
        self.assertEqual(
            self.booking_extra.booking_id,
            self.booking.pk,
        )
        self.assertEqual(
            self.booking_extra.extra_service_name,
            "Original Extra",
        )
        self.assertEqual(
            self.booking_extra.extra_service_price,
            Decimal("300.00"),
        )

    def test_existing_booking_extra_cannot_be_relinked(self):
        form = BookingExtraAdminForm(
            data={
                "booking": self.booking.pk,
                "extra_service": self.other_extra_service.pk,
            },
            instance=self.booking_extra,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("extra_service", form.errors)

        self.booking_extra.refresh_from_db()
        self.assertEqual(
            self.booking_extra.extra_service_id,
            self.extra_service.pk,
        )
        self.assertEqual(
            self.booking_extra.extra_service_name,
            "Original Extra",
        )
        self.assertEqual(
            self.booking_extra.extra_service_price,
            Decimal("300.00"),
        )

    def test_inline_allows_addition_but_disallows_deletion(self):
        inline = BookingExtraInline(Booking, admin.site)

        self.assertTrue(inline.has_add_permission(self.request, self.booking))
        self.assertFalse(inline.has_delete_permission(
            self.request,
            self.booking,
        ))
        self.assertFalse(inline.can_delete)

    def test_inline_can_add_booking_extra(self):
        booking = Booking.objects.create(
            customer=self.customer,
            service=self.service,
            booking_date=date.today() + timedelta(days=6),
            booking_time=time(10, 0),
            address="New Extra Address",
            city="Nairobi",
            total_price=Decimal("3000.00"),
        )
        inline = BookingExtraInline(Booking, admin.site)
        formset_class = inline.get_formset(self.request, booking)
        prefix = formset_class.get_default_prefix()
        formset = formset_class(
            data={
                f"{prefix}-TOTAL_FORMS": "1",
                f"{prefix}-INITIAL_FORMS": "0",
                f"{prefix}-MIN_NUM_FORMS": "0",
                f"{prefix}-MAX_NUM_FORMS": "1000",
                f"{prefix}-0-extra_service": str(self.other_extra_service.pk),
            },
            instance=booking,
        )

        self.assertTrue(formset.is_valid(), formset.errors)
        associations = formset.save()

        self.assertEqual(len(associations), 1)
        self.assertEqual(associations[0].extra_service, self.other_extra_service)
        self.assertEqual(associations[0].extra_service_name, "Other Extra")
        self.assertEqual(
            associations[0].extra_service_price,
            Decimal("450.00"),
        )
        associations[0].refresh_from_db()
        self.assertEqual(associations[0].extra_service_name, "Other Extra")
        self.assertEqual(
            associations[0].extra_service_price,
            Decimal("450.00"),
        )

    def test_standalone_admin_creation_populates_snapshots(self):
        form = BookingExtraAdminForm(
            data={
                "booking": self.booking.pk,
                "extra_service": self.other_extra_service.pk,
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        association = form.save(commit=False)
        model_admin = BookingExtraAdmin(BookingExtra, admin.site)
        model_admin.save_model(
            self.request,
            association,
            form,
            change=False,
        )

        association.refresh_from_db()
        self.assertEqual(
            association.extra_service_name,
            "Other Extra",
        )
        self.assertEqual(
            association.extra_service_price,
            Decimal("450.00"),
        )


class BookingTrackViewTests(TestCase):
    def setUp(self):
        cache.clear()

        self.category = ServiceCategory.objects.create(
            name="Residential",
            slug="residential",
            description="Residential cleaning services.",
        )
        self.service = Service.objects.create(
            name="Home Cleaning",
            slug="home-cleaning",
            category=self.category,
            short_description="Professional home cleaning.",
            description="Professional home cleaning service.",
            base_price=2500,
            price_per_hour=1000,
            duration_hours=2,
            is_active=True,
        )
        self.staff = Staff.objects.create(
            full_name="Track Cleaner",
            phone="0712345678",
            role="cleaner",
            city="Nairobi",
            is_available=True,
            is_active=True,
        )
        self.customer = Customer.objects.create(
            full_name="Track Customer",
            email="track@example.com",
            phone="0700000000",
            address="Track Address",
            city="Nairobi",
        )
        self.booking = Booking.objects.create(
            customer=self.customer,
            contact_name=self.customer.full_name,
            contact_email=self.customer.email,
            contact_phone=self.customer.phone,
            service=self.service,
            staff=self.staff,
            booking_date=date.today() + timedelta(days=5),
            booking_time=time(10, 0),
            address="Track Address",
            city="Nairobi",
            property_size=100,
            rooms=3,
            total_price=3000,
            status="pending",
        )

    def track(self, reference, email):
        return self.client.post(
            reverse("booking_track"),
            {
                "booking_reference": reference,
                "email": email,
            },
        )

    def create_second_booking(self):
        return Booking.objects.create(
            customer=self.customer,
            contact_name=self.customer.full_name,
            contact_email=self.customer.email,
            contact_phone=self.customer.phone,
            service=self.service,
            staff=self.staff,
            booking_date=date.today() + timedelta(days=6),
            booking_time=time(11, 0),
            address="Track Address",
            city="Nairobi",
            property_size=100,
            rooms=3,
            total_price=3000,
            status="pending",
        )

    def test_track_booking_with_matching_email(self):
        response = self.track(
            self.booking.booking_reference,
            self.customer.email,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.booking.booking_reference)
        self.assertContains(response, "Pending")

    def test_track_booking_with_wrong_email_does_not_reveal_booking(self):
        response = self.track(
            self.booking.booking_reference,
            "wrong@example.com",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "find a booking")
        self.assertNotContains(response, "BOOKING FOUND")
        self.assertNotContains(response, "Total Price")

    def test_rate_limit_uses_reference_and_ip_not_email(self):
        reference = self.booking.booking_reference

        for index in range(
            BookingTrackView.MAX_TRACK_ATTEMPTS_PER_REFERENCE
        ):
            response = self.track(
                reference,
                f"wrong{index}@example.com",
            )
            self.assertEqual(response.status_code, 200)

        response = self.track(reference, "another@example.com")

        self.assertEqual(response.status_code, 429)
        self.assertContains(
            response,
            "Too many tracking attempts",
            status_code=429,
        )

    def test_rate_limit_includes_overall_ip_attempts(self):
        second_booking = self.create_second_booking()

        with patch.object(
            BookingTrackView,
            "MAX_TRACK_ATTEMPTS_PER_IP",
            3,
        ):
            for index in range(3):
                response = self.track(
                    self.booking.booking_reference,
                    f"wrong{index}@example.com",
                )
                self.assertEqual(response.status_code, 200)

            response = self.track(
                second_booking.booking_reference,
                "different@example.com",
            )

        self.assertEqual(response.status_code, 429)
        self.assertContains(
            response,
            "Too many tracking attempts",
            status_code=429,
        )

    def test_tracking_still_works_when_cache_is_unavailable(self):
        broken_cache = Mock()
        broken_cache.add.side_effect = Exception("cache unavailable")
        broken_cache.incr.side_effect = Exception("cache unavailable")

        with patch("apps_core.bookings.views.cache", broken_cache):
            response = self.track(
                self.booking.booking_reference,
                self.customer.email,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.booking.booking_reference)


class BookingTrackCounterTests(TestCase):
    def setUp(self):
        cache.clear()
        self.view = BookingTrackView()

    def test_increment_attempt_counter_creates_and_increments(self):
        self.assertEqual(
            self.view._increment_attempt_counter("counter-test"),
            1,
        )
        self.assertEqual(
            self.view._increment_attempt_counter("counter-test"),
            2,
        )
        self.assertEqual(cache.get("counter-test"), 2)

    def test_increment_attempt_counter_recovers_from_expiration_before_increment(self):
        fake_cache = Mock()
        fake_cache.add.side_effect = [False, True]
        fake_cache.incr.side_effect = ValueError("missing")

        with patch("apps_core.bookings.views.cache", fake_cache):
            result = self.view._increment_attempt_counter("counter-test")

        self.assertEqual(result, 1)
        self.assertEqual(fake_cache.add.call_count, 2)
        self.assertEqual(fake_cache.incr.call_count, 1)

    def test_increment_attempt_counter_recovers_from_repeated_expiration(self):
        fake_cache = Mock()
        fake_cache.add.side_effect = [False, False, True]
        fake_cache.incr.side_effect = [
            ValueError("missing"),
            ValueError("missing"),
        ]

        with patch("apps_core.bookings.views.cache", fake_cache):
            result = self.view._increment_attempt_counter("counter-test")

        self.assertEqual(result, 1)
        self.assertEqual(fake_cache.add.call_count, 3)
        self.assertEqual(fake_cache.incr.call_count, 2)

    def test_increment_attempt_counter_returns_none_after_bounded_retries(self):
        fake_cache = Mock()
        fake_cache.add.return_value = False
        fake_cache.incr.side_effect = ValueError("missing")

        with patch("apps_core.bookings.views.cache", fake_cache):
            result = self.view._increment_attempt_counter("counter-test")

        self.assertIsNone(result)
        self.assertEqual(
            fake_cache.add.call_count,
            BookingTrackView.COUNTER_RECOVERY_ATTEMPTS,
        )
        self.assertEqual(
            fake_cache.incr.call_count,
            BookingTrackView.COUNTER_RECOVERY_ATTEMPTS,
        )

    def test_increment_attempt_counter_returns_none_on_cache_failure(self):
        fake_cache = Mock()
        fake_cache.add.side_effect = Exception("cache unavailable")

        with patch("apps_core.bookings.views.cache", fake_cache):
            result = self.view._increment_attempt_counter("counter-test")

        self.assertIsNone(result)
