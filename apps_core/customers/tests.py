from decimal import Decimal
from django.test import TestCase
from apps_core.bookings.models import Booking
from apps_core.services.models import Service
from .models import Customer
from .services import (
    get_customer_booking_count,
    get_customer_by_email,
    get_or_create_customer,
    is_repeat_customer,
    update_repeat_customer_status,
)


class CustomerServiceTests(TestCase):
    """
    Tests for customer service-layer operations.
    """

    def setUp(self):
        self.service = Service.objects.create(
            name="Customer Test Service",
            slug="customer-test-service",
            description="Test service for customer bookings.",
            base_price=Decimal("2000.00"),
            duration_hours=2,
            is_active=True,
        )

    def test_customer_is_created(self):
        customer, created = get_or_create_customer(
            full_name="Jane Customer",
            email="jane@example.com",
            phone="0712345678",
            address="Nairobi",
            city="Nairobi",
        )

        self.assertTrue(
            created
        )

        self.assertEqual(
            customer.email,
            "jane@example.com",
        )

    def test_existing_customer_is_reused(self):
        customer = Customer.objects.create(
            full_name="Jane Customer",
            email="jane@example.com",
            phone="0712345678",
            address="Original Address",
            city="Nairobi",
        )

        reused_customer, created = get_or_create_customer(
            full_name="Jane Updated",
            email="jane@example.com",
            phone="0799999999",
            address="Updated Address",
            city="Mombasa",
        )

        self.assertFalse(created)
        self.assertEqual(
            reused_customer.pk,
            customer.pk,
        )

        # Existing customer profile must not be overwritten
        # by a public booking submission.
        self.assertEqual(
            reused_customer.full_name,
            "Jane Customer",
        )

        self.assertEqual(
            reused_customer.phone,
            "0712345678",
        )

        self.assertEqual(
            reused_customer.address,
            "Original Address",
        )

        self.assertEqual(
            reused_customer.city,
            "Nairobi",
        )


    def test_customer_can_be_found_by_email(self):
        customer = Customer.objects.create(
            full_name="Jane Customer",
            email="jane@example.com",
            phone="0712345678",
            address="Nairobi",
            city="Nairobi",
        )

        found_customer = get_customer_by_email(
            "JANE@EXAMPLE.COM"
        )

        self.assertEqual(
            found_customer,
            customer,
        )

    def test_missing_email_returns_none(self):
        found_customer = get_customer_by_email("")

        self.assertIsNone(
            found_customer
        )

    def test_customer_with_no_bookings_is_not_repeat_customer(self):
        customer = Customer.objects.create(
            full_name="Jane Customer",
            email="jane@example.com",
            phone="0712345678",
            address="Nairobi",
            city="Nairobi",
        )

        self.assertEqual(
            get_customer_booking_count(customer),
            0,
        )

        self.assertFalse(
            is_repeat_customer(customer)
        )

    def test_customer_booking_count_is_correct(self):
        customer = Customer.objects.create(
            full_name="Repeat Customer",
            email="repeat@example.com",
            phone="0722222222",
            address="Nairobi",
            city="Nairobi",
        )

        Booking.objects.create(
            customer=customer,
            service=self.service,
            booking_date="2026-10-10",
            booking_time="10:00",
            address="Nairobi",
            city="Nairobi",
            total_price=Decimal("2000.00"),
            status="completed",
        )

        Booking.objects.create(
            customer=customer,
            service=self.service,
            booking_date="2026-10-11",
            booking_time="10:00",
            address="Nairobi",
            city="Nairobi",
            total_price=Decimal("2000.00"),
            status="completed",
        )

        self.assertEqual(
            get_customer_booking_count(customer),
            2,
        )

    def test_customer_with_multiple_bookings_is_repeat_customer(self):
        customer = Customer.objects.create(
            full_name="Repeat Customer",
            email="repeat2@example.com",
            phone="0733333333",
            address="Nairobi",
            city="Nairobi",
        )

        Booking.objects.create(
            customer=customer,
            service=self.service,
            booking_date="2026-10-12",
            booking_time="10:00",
            address="Nairobi",
            city="Nairobi",
            total_price=Decimal("2000.00"),
            status="completed",
        )

        Booking.objects.create(
            customer=customer,
            service=self.service,
            booking_date="2026-10-13",
            booking_time="10:00",
            address="Nairobi",
            city="Nairobi",
            total_price=Decimal("2000.00"),
            status="completed",
        )

        self.assertTrue(
            is_repeat_customer(customer)
        )

    def test_repeat_customer_status_is_updated(self):
        customer = Customer.objects.create(
            full_name="Repeat Status Customer",
            email="status@example.com",
            phone="0744444444",
            address="Nairobi",
            city="Nairobi",
            is_repeat_customer=False,
        )

        Booking.objects.create(
            customer=customer,
            service=self.service,
            booking_date="2026-10-14",
            booking_time="10:00",
            address="Nairobi",
            city="Nairobi",
            total_price=Decimal("2000.00"),
            status="completed",
        )

        Booking.objects.create(
            customer=customer,
            service=self.service,
            booking_date="2026-10-15",
            booking_time="10:00",
            address="Nairobi",
            city="Nairobi",
            total_price=Decimal("2000.00"),
            status="completed",
        )

        update_repeat_customer_status(customer)

        customer.refresh_from_db()

        self.assertTrue(
            customer.is_repeat_customer
        )

    def test_repeat_customer_status_is_not_set_for_one_booking(self):
        customer = Customer.objects.create(
            full_name="Single Booking Customer",
            email="single@example.com",
            phone="0755555555",
            address="Nairobi",
            city="Nairobi",
            is_repeat_customer=False,
        )

        Booking.objects.create(
            customer=customer,
            service=self.service,
            booking_date="2026-10-16",
            booking_time="10:00",
            address="Nairobi",
            city="Nairobi",
            total_price=Decimal("2000.00"),
            status="completed",
        )

        update_repeat_customer_status(customer)

        customer.refresh_from_db()

        self.assertFalse(
            customer.is_repeat_customer
        )

    def test_customer_email_is_normalized_when_created(self):
        customer, created = get_or_create_customer(
            full_name="Email Customer",
            email="  TEST@EXAMPLE.COM  ",
            phone="0766666666",
            address="Nairobi",
            city="Nairobi",
        )

        self.assertTrue(
            created
        )

        self.assertEqual(
            customer.email,
            "test@example.com",
        )

    def test_missing_email_cannot_create_customer(self):
        with self.assertRaises(ValueError):
            get_or_create_customer(
                full_name="Invalid Customer",
                email="",
                phone="0777777777",
                address="Nairobi",
                city="Nairobi",
            )


