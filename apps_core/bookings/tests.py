from datetime import date, time, timedelta
from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.db.models.deletion import ProtectedError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from unittest.mock import Mock
from apps_core.bookings.models import Booking, BookingExtra
from apps_core.bookings.services import change_booking_status, calculate_booking_price, create_booking, get_availability_status
from apps_core.customers.models import Customer
from apps_core.services.models import Service, ExtraService
from apps_core.staff.models import Staff


User = get_user_model()


class BookingExtraSnapshotMigrationTests(SimpleTestCase):
    def test_missing_extra_service_fails_with_association_identifier(self):
        migration_module = import_module(
            "apps_core.bookings.migrations."
            "0005_preserve_booking_history_and_extra_snapshots"
        )

        class MissingExtraService(Exception):
            pass

        class HistoricalExtraService:
            DoesNotExist = MissingExtraService

        class HistoricalBookingExtra:
            class DoesNotExist(Exception):
                pass

            def __init__(self):
                self.pk = 42
                self.extra_service_id = 99

            @property
            def extra_service(self):
                raise MissingExtraService

        row = HistoricalBookingExtra()
        queryset = Mock()
        queryset.using.return_value.iterator.return_value = [row]
        HistoricalBookingExtra.objects = queryset

        historical_apps = Mock()
        historical_apps.get_model.side_effect = [
            HistoricalBookingExtra,
            HistoricalExtraService,
        ]
        schema_editor = SimpleNamespace(
            connection=SimpleNamespace(alias="reporting")
        )

        with self.assertRaisesRegex(
            RuntimeError,
            r"BookingExtra #42: ExtraService #99 does not exist",
        ):
            migration_module.backfill_booking_extra_snapshots(
                historical_apps,
                schema_editor,
            )

        queryset.using.assert_called_once_with("reporting")
        queryset.using.return_value.select_related.assert_not_called()


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

    def test_stale_booking_instance_cannot_bypass_current_status(self):
        booking = self.create_booking("pending")
        stale_booking = Booking.objects.get(pk=booking.pk)

        change_booking_status(booking, "cancelled")

        with self.assertRaisesRegex(
            ValueError,
            "Booking cannot move from Cancelled to Confirmed",
        ):
            change_booking_status(stale_booking, "confirmed")

        stale_booking.refresh_from_db()
        self.assertEqual(stale_booking.status, "cancelled")

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

        self.booking_date = timezone.localdate() + timedelta(days=2)

    def test_booking_is_created_successfully(self):
        booking = create_booking(
            customer=self.customer,
            data={
                "service": self.service,
                "booking_date": self.booking_date,
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
            self.booking_date,
        )

        self.assertEqual(
            booking.booking_time,
            time(10, 0),
        )
        self.assertIsNone(booking.created_by)

    def test_booking_can_be_attributed_to_authenticated_user(self):
        user = User.objects.create_user(
            username="booking-owner",
            email="booking-owner@example.com",
            password="test-password",
        )

        self.customer.user = user
        self.customer.save(update_fields=["user"])

        booking = create_booking(
            customer=self.customer,
            data={
                "service": self.service,
                "booking_date": self.booking_date,
                "booking_time": time(10, 0),
                "address": "Test Address",
                "city": "Nairobi",
                "property_size": 100,
                "rooms": 2,
                "extras": [],
            },
            created_by=user,
        )

        self.assertEqual(booking.customer_id, self.customer.pk)
        self.assertEqual(booking.created_by_id, user.pk)

    def test_authenticated_user_without_customer_profile_is_rejected(self):
        user = User.objects.create_user(
            username="booking-owner-no-profile",
            email="booking-owner-no-profile@example.com",
        )

        with self.assertRaisesMessage(
            ValueError,
            "Your account does not have a customer profile. "
            "Please contact CLEANEX support before booking.",
        ):
            create_booking(
                customer=self.customer,
                data={},
                created_by=user,
            )

    def test_booking_without_authenticated_user_is_unattributed(self):
        booking = create_booking(
            customer=self.customer,
            data={
                "service": self.service,
                "booking_date": self.booking_date,
                "booking_time": time(10, 0),
                "address": "Test Address",
                "city": "Nairobi",
                "property_size": 100,
                "rooms": 2,
                "extras": [],
            },
        )

        self.assertIsNone(booking.created_by_id)

    def test_new_booking_starts_as_pending(self):
        booking = create_booking(
            customer=self.customer,
            data={
                "service": self.service,
                "booking_date": self.booking_date,
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
                "booking_date": self.booking_date,
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
                "booking_date": self.booking_date,
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
                "booking_date": self.booking_date,
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

        booking_extra = booking.extras.first()
        self.assertEqual(
            booking_extra.extra_service_name,
            "Window Cleaning",
        )
        self.assertEqual(
            booking_extra.extra_service_price,
            Decimal("300.00"),
        )

    def test_extra_snapshots_do_not_change_with_catalogue(self):
        booking = create_booking(
            customer=self.customer,
            data={
                "service": self.service,
                "booking_date": self.booking_date,
                "booking_time": time(10, 0),
                "address": "Test Address",
                "city": "Nairobi",
                "property_size": 100,
                "rooms": 2,
                "extras": [self.extra],
            },
        )

        self.extra.name = "Updated Window Cleaning"
        self.extra.price = Decimal("450.00")
        self.extra.save(update_fields=["name", "price"])

        booking_extra = booking.extras.get()
        self.assertEqual(booking_extra.extra_service_name, "Window Cleaning")
        self.assertEqual(
            booking_extra.extra_service_price,
            Decimal("300.00"),
        )

    def test_deactivating_extra_preserves_existing_booking_extra(self):
        booking = create_booking(
            customer=self.customer,
            data={
                "service": self.service,
                "booking_date": self.booking_date,
                "booking_time": time(10, 0),
                "address": "Test Address",
                "city": "Nairobi",
                "property_size": 100,
                "rooms": 2,
                "extras": [self.extra],
            },
        )

        self.extra.is_active = False
        self.extra.save(update_fields=["is_active"])

        self.assertEqual(booking.extras.count(), 1)

    def test_inactive_extra_cannot_be_selected_for_new_booking(self):
        self.extra.is_active = False
        self.extra.save(update_fields=["is_active"])

        with self.assertRaisesMessage(
            ValueError,
            "One or more selected additional services are no longer available.",
        ):
            create_booking(
                customer=self.customer,
                data={
                    "service": self.service,
                    "booking_date": self.booking_date,
                    "booking_time": time(10, 0),
                    "address": "Test Address",
                    "city": "Nairobi",
                    "property_size": 100,
                    "rooms": 2,
                    "extras": [self.extra],
                },
            )

    def test_extra_with_booking_history_cannot_be_deleted(self):
        create_booking(
            customer=self.customer,
            data={
                "service": self.service,
                "booking_date": self.booking_date,
                "booking_time": time(10, 0),
                "address": "Test Address",
                "city": "Nairobi",
                "property_size": 100,
                "rooms": 2,
                "extras": [self.extra],
            },
        )

        with self.assertRaises(ProtectedError):
            self.extra.delete()


class CustomerBookingRetentionTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Retention Customer",
            email="retention@example.com",
            phone="0712345678",
            address="Test Address",
            city="Nairobi",
        )

    def test_customer_with_booking_history_cannot_be_deleted(self):
        Booking.objects.create(
            customer=self.customer,
            booking_date=date(2026, 11, 20),
            booking_time=time(10, 0),
            address="Test Address",
            city="Nairobi",
            total_price=Decimal("1000.00"),
        )

        with self.assertRaises(ProtectedError):
            self.customer.delete()

    def test_customer_without_bookings_can_be_deleted(self):
        customer_id = self.customer.pk

        self.customer.delete()

        self.assertFalse(Customer.objects.filter(pk=customer_id).exists())



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
