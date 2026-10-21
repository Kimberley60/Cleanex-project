from datetime import date, time
from decimal import Decimal
from django.test import TestCase
from apps_core.bookings.models import Booking
from apps_core.bookings.services import change_booking_status, calculate_booking_price, create_booking, get_availability_status
from apps_core.customers.models import Customer
from apps_core.services.models import Service, ExtraService
from apps_core.staff.models import Staff


class BookingStatusTransitionTests(TestCase):
    """
    Tests for the booking status workflow.

    The service layer is the single source of truth for
    allowed booking status transitions.
    """

    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Test Customer",
            email="test@example.com",
            phone="0712345678",
            address="Test Address",
            city="Nairobi",
        )

        self.service = Service.objects.create(
            name="Test Cleaning Service",
            slug="test-cleaning-service",
            description="Test service",
            base_price=Decimal("2000.00"),
            duration_hours=2,
            is_active=True,
        )

    def create_booking(self, status="pending"):
        return Booking.objects.create(
            customer=self.customer,
            service=self.service,
            booking_date=date(2026, 10, 1),
            booking_time=time(10, 0),
            address="Test Address",
            city="Nairobi",
            total_price=Decimal("2000.00"),
            status=status,
        )

    # ==========================================================
    # VALID TRANSITIONS
    # ==========================================================

    def test_pending_can_be_confirmed(self):
        booking = self.create_booking("pending")

        change_booking_status(
            booking,
            "confirmed",
        )

        booking.refresh_from_db()

        self.assertEqual(
            booking.status,
            "confirmed",
        )

    def test_pending_can_be_cancelled(self):
        booking = self.create_booking("pending")

        change_booking_status(
            booking,
            "cancelled",
        )

        booking.refresh_from_db()

        self.assertEqual(
            booking.status,
            "cancelled",
        )

    def test_confirmed_can_move_to_in_progress(self):
        booking = self.create_booking("confirmed")

        change_booking_status(
            booking,
            "in_progress",
        )

        booking.refresh_from_db()

        self.assertEqual(
            booking.status,
            "in_progress",
        )

    def test_confirmed_can_be_cancelled(self):
        booking = self.create_booking("confirmed")

        change_booking_status(
            booking,
            "cancelled",
        )

        booking.refresh_from_db()

        self.assertEqual(
            booking.status,
            "cancelled",
        )

    def test_in_progress_can_be_completed(self):
        booking = self.create_booking("in_progress")

        change_booking_status(
            booking,
            "completed",
        )

        booking.refresh_from_db()

        self.assertEqual(
            booking.status,
            "completed",
        )

    def test_in_progress_can_be_cancelled(self):
        booking = self.create_booking("in_progress")

        change_booking_status(
            booking,
            "cancelled",
        )

        booking.refresh_from_db()

        self.assertEqual(
            booking.status,
            "cancelled",
        )

    # ==========================================================
    # INVALID TRANSITIONS
    # ==========================================================

    def test_pending_cannot_move_directly_to_completed(self):
        booking = self.create_booking("pending")

        with self.assertRaises(ValueError):
            change_booking_status(
                booking,
                "completed",
            )

    def test_pending_cannot_move_back_to_pending(self):
        booking = self.create_booking("pending")

        with self.assertRaises(ValueError):
            change_booking_status(
                booking,
                "pending",
            )

    def test_confirmed_cannot_move_back_to_pending(self):
        booking = self.create_booking("confirmed")

        with self.assertRaises(ValueError):
            change_booking_status(
                booking,
                "pending",
            )

    def test_in_progress_cannot_move_back_to_confirmed(self):
        booking = self.create_booking("in_progress")

        with self.assertRaises(ValueError):
            change_booking_status(
                booking,
                "confirmed",
            )

    def test_completed_cannot_change_status(self):
        booking = self.create_booking("completed")

        with self.assertRaises(ValueError):
            change_booking_status(
                booking,
                "pending",
            )

    def test_cancelled_cannot_change_status(self):
        booking = self.create_booking("cancelled")

        with self.assertRaises(ValueError):
            change_booking_status(
                booking,
                "confirmed",
            )

    # ==========================================================
    # INVALID STATUS
    # ==========================================================

    def test_invalid_status_is_rejected(self):
        booking = self.create_booking("pending")

        with self.assertRaises(ValueError):
            change_booking_status(
                booking,
                "not_a_real_status",
            )

    # ==========================================================
    # SAME STATUS
    # ==========================================================

    def test_changing_to_current_status_is_rejected(self):
        booking = self.create_booking("confirmed")

        with self.assertRaises(ValueError):
            change_booking_status(
                booking,
                "confirmed",
            )



class BookingPricingTests(TestCase):
    """
    Tests for booking price calculation.

    Pricing rules are handled by the booking service layer.
    """

    def setUp(self):
        self.service = Service.objects.create(
            name="Test Cleaning Service",
            slug="pricing-test-service",
            description="Test service for pricing.",
            base_price=Decimal("2000.00"),
            duration_hours=2,
            is_active=True,
        )

        self.extra = ExtraService.objects.create(
            name="Deep Fridge Cleaning",
            price=Decimal("300.00"),
            is_active=True,
        )

    def test_base_service_price(self):
        total = calculate_booking_price(
            service=self.service
        )

        self.assertEqual(
            total,
            Decimal("2000.00")
        )

    def test_rooms_are_added_to_price(self):
        total = calculate_booking_price(
            service=self.service,
            rooms=2,
        )

        self.assertEqual(
            total,
            Decimal("3000.00")
        )

    def test_property_size_is_added_to_price(self):
        total = calculate_booking_price(
            service=self.service,
            property_size=100,
        )

        self.assertEqual(
            total,
            Decimal("2200.00")
        )

    def test_extra_service_is_added_to_price(self):
        total = calculate_booking_price(
            service=self.service,
            extras=[self.extra],
        )

        self.assertEqual(
            total,
            Decimal("2300.00")
        )

    def test_combined_booking_price(self):
        total = calculate_booking_price(
            service=self.service,
            property_size=100,
            rooms=2,
            extras=[self.extra],
        )

        self.assertEqual(
            total,
            Decimal("3500.00")
        )

    def test_service_is_required(self):
        with self.assertRaises(ValueError):
            calculate_booking_price(
                service=None

            )



class BookingCreationTests(TestCase):
    """
    Tests for the complete booking creation workflow.
    """

    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Booking Customer",
            email="booking@example.com",
            phone="0712345678",
            address="Test Address",
            city="Nairobi",
        )

        self.service = Service.objects.create(
            name="Home Cleaning",
            slug="home-cleaning-test",
            description="Test home cleaning service.",
            base_price=Decimal("2000.00"),
            duration_hours=2,
            is_active=True,
        )

        self.extra = ExtraService.objects.create(
            name="Window Cleaning",
            price=Decimal("300.00"),
            is_active=True,
        )

        self.staff = Staff.objects.create(
            full_name="Test Cleaner",
            phone="0799999999",
            role="cleaner",
            city="Nairobi",
            is_available=True,
            is_active=True,
        )

    def test_booking_is_created_successfully(self):
        booking = create_booking(
            customer=self.customer,
            data={
                "service": self.service,
                "booking_date": date(2026, 11, 10),
                "booking_time": time(10, 0),
                "address": "Test Address",
                "city": "Nairobi",
                "property_size": 100,
                "rooms": 2,
                "extras": [self.extra],
                "notes": "Please clean the windows.",
            },
        )

        self.assertIsNotNone(booking.pk)

        self.assertEqual(
            booking.customer,
            self.customer,
        )

        self.assertEqual(
            booking.service,
            self.service,
        )

        self.assertEqual(
            booking.booking_date,
            date(2026, 11, 10),
        )

        self.assertEqual(
            booking.booking_time,
            time(10, 0),
        )

    def test_new_booking_starts_as_pending(self):
        booking = create_booking(
            customer=self.customer,
            data={
                "service": self.service,
                "booking_date": date(2026, 11, 11),
                "booking_time": time(10, 0),
                "address": "Test Address",
                "city": "Nairobi",
                "property_size": 100,
                "rooms": 2,
                "extras": [],
            },
        )

        self.assertEqual(
            booking.status,
            "pending",
        )

    def test_booking_gets_assigned_staff(self):
        booking = create_booking(
            customer=self.customer,
            data={
                "service": self.service,
                "booking_date": date(2026, 11, 12),
                "booking_time": time(10, 0),
                "address": "Test Address",
                "city": "Nairobi",
                "property_size": 100,
                "rooms": 2,
                "extras": [],
            },
        )

        self.assertEqual(
            booking.staff,
            self.staff,
        )

    def test_booking_total_price_is_calculated(self):
        booking = create_booking(
            customer=self.customer,
            data={
                "service": self.service,
                "booking_date": date(2026, 11, 13),
                "booking_time": time(10, 0),
                "address": "Test Address",
                "city": "Nairobi",
                "property_size": 100,
                "rooms": 2,
                "extras": [self.extra],
            },
        )

        self.assertEqual(
            booking.total_price,
            Decimal("3500.00"),
        )

    def test_booking_extra_is_created(self):
        booking = create_booking(
            customer=self.customer,
            data={
                "service": self.service,
                "booking_date": date(2026, 11, 14),
                "booking_time": time(10, 0),
                "address": "Test Address",
                "city": "Nairobi",
                "property_size": 100,
                "rooms": 2,
                "extras": [self.extra],
            },
        )

        self.assertEqual(
            booking.extras.count(),
            1,
        )

        self.assertEqual(
            booking.extras.first().extra_service,
            self.extra,
        )



class BookingAvailabilityStatusTests(TestCase):
    """
    Tests for customer-facing booking availability status.

    The booking service distinguishes between:
    - no available staff serving the selected city
    - staff available in the city but unavailable at the selected time
    - staff available for the requested date and time
    """

    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Availability Customer",
            email="availability@example.com",
            phone="0712345678",
            address="Test Address",
            city="Nairobi",
        )

        self.service = Service.objects.create(
            name="Availability Cleaning",
            slug="availability-cleaning-test",
            description="Test service for availability.",
            base_price=Decimal("2000.00"),
            duration_hours=2,
            is_active=True,
        )

        self.staff = Staff.objects.create(
            full_name="Availability Cleaner",
            phone="0788888888",
            role="cleaner",
            city="Nairobi",
            is_available=True,
            is_active=True,
        )

    def test_available_staff_returns_available_status(self):
        status = get_availability_status(
            date=date(2026, 11, 20),
            time=time(10, 0),
            city="Nairobi",
            duration_hours=2,
        )

        self.assertEqual(
            status,
            "available",
        )

    def test_city_without_available_staff_returns_no_city_staff(self):
        status = get_availability_status(
            date=date(2026, 11, 20),
            time=time(10, 0),
            city="Mombasa",
            duration_hours=2,
        )

        self.assertEqual(
            status,
            "no_city_staff",
        )

    def test_busy_staff_returns_time_unavailable(self):
        Booking.objects.create(
            customer=self.customer,
            service=self.service,
            staff=self.staff,
            booking_date=date(2026, 11, 20),
            booking_time=time(10, 0),
            address="Existing Booking Address",
            city="Nairobi",
            property_size=100,
            rooms=2,
            total_price=Decimal("2500.00"),
            status="confirmed",
        )

        status = get_availability_status(
            date=date(2026, 11, 20),
            time=time(10, 0),
            city="Nairobi",
            duration_hours=2,
        )

        self.assertEqual(
            status,
            "time_unavailable",
        )

