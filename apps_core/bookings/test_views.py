from datetime import date, time, timedelta
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps_core.bookings.models import Booking
from apps_core.customers.models import Customer
from apps_core.services.models import Service, ServiceCategory
from apps_core.staff.models import Staff


class BookingViewSecurityTests(TestCase):

    def setUp(self):
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

