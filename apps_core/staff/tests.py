from datetime import date, time
from django.test import TestCase
from apps_core.bookings.models import Booking
from apps_core.customers.models import Customer
from apps_core.services.models import Service
from .models import Staff
from .services import (
    assign_available_staff,
    check_staff_availability,
    count_available_staff,
    get_available_staff,
)


class StaffServiceTests(TestCase):
    """
    Tests for staff availability, filtering, assignment,
    and booking capacity.
    """

    def setUp(self):
        self.staff = Staff.objects.create(
            full_name="John Cleaner",
            phone="0712345678",
            role="cleaner",
            city="Nairobi",
            is_available=True,
            is_active=True,
        )

    def test_available_staff_is_returned(self):
        staff = get_available_staff()

        self.assertEqual(
            staff.count(),
            1,
        )

    def test_unavailable_staff_is_excluded(self):
        self.staff.is_available = False
        self.staff.save()

        self.assertEqual(
            count_available_staff(),
            0,
        )

    def test_inactive_staff_is_excluded(self):
        self.staff.is_active = False
        self.staff.save()

        self.assertEqual(
            count_available_staff(),
            0,
        )

    def test_city_filter_returns_only_matching_staff(self):
        Staff.objects.create(
            full_name="Mombasa Cleaner",
            phone="0798765432",
            role="cleaner",
            city="Mombasa",
            is_available=True,
            is_active=True,
        )

        staff = get_available_staff(
            city="Nairobi"
        )

        self.assertEqual(
            staff.count(),
            1,
        )

        self.assertEqual(
            staff.first(),
            self.staff,
        )

    def test_city_filter_is_case_insensitive(self):
        staff = get_available_staff(
            city="nAiRoBi"
        )

        self.assertEqual(
            staff.count(),
            1,
        )

        self.assertEqual(
            staff.first(),
            self.staff,
        )

    def test_city_staff_is_assigned(self):
        Staff.objects.create(
            full_name="Mombasa Cleaner",
            phone="0798765432",
            role="cleaner",
            city="Mombasa",
            is_available=True,
            is_active=True,
        )

        assigned_staff = assign_available_staff(
            city="Nairobi"
        )

        self.assertEqual(
            assigned_staff,
            self.staff,
        )

    def test_staff_from_wrong_city_is_not_assigned(self):
        assigned_staff = assign_available_staff(
            city="Mombasa"
        )

        self.assertIsNone(
            assigned_staff
        )

    def test_unavailable_staff_is_not_assigned(self):
        self.staff.is_available = False
        self.staff.save()

        assigned_staff = assign_available_staff(
            city="Nairobi"
        )

        self.assertIsNone(
            assigned_staff
        )

    def test_inactive_staff_is_not_assigned(self):
        self.staff.is_active = False
        self.staff.save()

        assigned_staff = assign_available_staff(
            city="Nairobi"
        )

        self.assertIsNone(
            assigned_staff
        )

    def test_staff_availability_exists_when_no_booking_conflict(self):
        available = check_staff_availability(
            date(2026, 10, 10),
            time(10, 0),
            city="Nairobi",
        )

        self.assertTrue(
            available
        )

    def test_staff_capacity_is_consumed_by_active_booking(self):
        customer = Customer.objects.create(
            full_name="Test Customer",
            email="test@example.com",
            phone="0700000000",
            address="Test Address",
            city="Nairobi",
        )

        service = Service.objects.create(
            name="Home Cleaning",
            slug="home-cleaning",
            description="Test cleaning service",
            base_price=2000,
            price_per_hour=500,
            duration_hours=2,
            is_active=True,
            is_featured=True,
        )

        Booking.objects.create(
            customer=customer,
            service=service,
            staff=self.staff,
            booking_date=date(2026, 10, 10),
            booking_time=time(10, 0),
            address="Test Address",
            city="Nairobi",
            total_price=2000,
            status="confirmed",
        )

        available = check_staff_availability(
            date(2026, 10, 10),
            time(10, 0),
            city="Nairobi",
        )

        self.assertFalse(
            available
        )

    def test_completed_booking_does_not_consume_staff_capacity(self):
        customer = Customer.objects.create(
            full_name="Completed Customer",
            email="completed@example.com",
            phone="0700000001",
            address="Test Address",
            city="Nairobi",
        )

        service = Service.objects.create(
            name="Completed Cleaning",
            slug="completed-cleaning",
            description="Completed cleaning service",
            base_price=2000,
            duration_hours=2,
            is_active=True,
        )

        Booking.objects.create(
            customer=customer,
            service=service,
            staff=self.staff,
            booking_date=date(2026, 10, 11),
            booking_time=time(10, 0),
            address="Test Address",
            city="Nairobi",
            total_price=2000,
            status="completed",
        )

        available = check_staff_availability(
            date(2026, 10, 11),
            time(10, 0),
            city="Nairobi",
        )

        self.assertTrue(
            available
        )

    def test_cancelled_booking_does_not_consume_staff_capacity(self):
        customer = Customer.objects.create(
            full_name="Cancelled Customer",
            email="cancelled@example.com",
            phone="0700000002",
            address="Test Address",
            city="Nairobi",
        )

        service = Service.objects.create(
            name="Cancelled Cleaning",
            slug="cancelled-cleaning",
            description="Cancelled cleaning service",
            base_price=2000,
            duration_hours=2,
            is_active=True,
        )

        Booking.objects.create(
            customer=customer,
            service=service,
            staff=self.staff,
            booking_date=date(2026, 10, 12),
            booking_time=time(10, 0),
            address="Test Address",
            city="Nairobi",
            total_price=2000,
            status="cancelled",
        )

        available = check_staff_availability(
            date(2026, 10, 12),
            time(10, 0),
            city="Nairobi",
        )

        self.assertTrue(
            available
        )

