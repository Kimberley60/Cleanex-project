import base64
from datetime import datetime, timedelta
from contextlib import ExitStack
from decimal import Decimal
from email.utils import format_datetime
from io import StringIO
from zoneinfo import ZoneInfo
import requests
import uuid
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import connection
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from apps_core.bookings.models import Booking
from unittest.mock import Mock, patch
from apps_core.customers.models import Customer
from apps_core.payments.models import MpesaCallbackMetadata, Payment
from apps_core.payments.admin import PaymentAdmin
from apps_core.payments import views as payment_views
from apps_core.payments.services import (
    create_payment,
    get_payment_for_booking,
    initiate_payment,
    mark_payment_failed,
    mark_payment_paid,
    mark_payment_processing,
    process_mpesa_callback,
    verify_payment,
)
from apps_core.services.models import Service



class PaymentCreationTests(TestCase):
    """
    Tests for payment creation through the payment service.
    """

    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Payment Customer",
            email="payment@example.com",
            phone="0712345678",
            address="Payment Address",
            city="Nairobi",
        )

        self.service = Service.objects.create(
            name="Payment Test Cleaning",
            slug="payment-test-cleaning",
            description="Service for payment tests.",
            base_price=Decimal("3500.00"),
            duration_hours=2,
            is_active=True,
        )

        self.booking = Booking.objects.create(
            customer=self.customer,
            service=self.service,
            booking_date="2026-11-20",
            booking_time="10:00",
            address="Payment Address",
            city="Nairobi",
            property_size=100,
            rooms=2,
            total_price=Decimal("3500.00"),
            status="pending",
        )

    def test_payment_is_created(self):
        payment = create_payment(
            booking=self.booking,
            phone_number="0712345678",
        )

        self.assertIsNotNone(
            payment.pk
        )

        self.assertEqual(
            payment.booking,
            self.booking,
        )

    def test_payment_uses_booking_amount_by_default(self):
        payment = create_payment(
            booking=self.booking,
            phone_number="0712345678",
        )

        self.assertEqual(
            payment.amount,
            Decimal("3500.00"),
        )

    def test_payment_starts_as_pending(self):
        payment = create_payment(
            booking=self.booking,
            phone_number="0712345678",
        )

        self.assertEqual(
            payment.status,
            "pending",
        )

    def test_payment_defaults_to_mpesa(self):
        payment = create_payment(
            booking=self.booking,
            phone_number="0712345678",
        )

        self.assertEqual(
            payment.method,
            "mpesa",
        )

    def test_custom_payment_amount_can_be_used(self):
        payment = create_payment(
            booking=self.booking,
            phone_number="0712345678",
            amount=Decimal("2000.00"),
        )

        self.assertEqual(
            payment.amount,
            Decimal("2000.00"),
        )

    def test_payment_requires_booking(self):
        with self.assertRaises(ValueError):
            create_payment(
                booking=None,
                phone_number="0712345678",
            )

    def test_payment_requires_phone_number(self):
        with self.assertRaises(ValueError):
            create_payment(
                booking=self.booking,
                phone_number="",
            )

    def test_payment_rejects_non_positive_amount(self):
        with self.assertRaises(ValueError):
            create_payment(
                booking=self.booking,
                phone_number="0712345678",
                amount=Decimal("0.00"),
            )





class PaymentServiceTests(TestCase):

    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Payment Customer",
            email="payment@example.com",
            phone="0712345678",
            address="Test Address",
            city="Nairobi",
        )

        self.service = Service.objects.create(
            name="Payment Test Service",
            slug="payment-test-service",
            description="Payment test service.",
            base_price=Decimal("2500.00"),
            duration_hours=2,
            is_active=True,
        )

        self.booking = Booking.objects.create(
            customer=self.customer,
            service=self.service,
            booking_date="2026-11-20",
            booking_time="10:00",
            address="Test Address",
            city="Nairobi",
            total_price=Decimal("2500.00"),
            status="pending",
        )

    def test_create_payment_uses_booking_total(self):
        payment = create_payment(
            booking=self.booking,
            phone_number="0712345678",
        )

        self.assertEqual(
            payment.amount,
            Decimal("2500.00"),
        )

        self.assertEqual(
            payment.status,
            "pending",
        )

        self.assertEqual(
            payment.method,
            "mpesa",
        )

    def test_create_payment_is_linked_to_booking(self):
        payment = create_payment(
            booking=self.booking,
            phone_number="0712345678",
        )

        self.assertEqual(
            payment.booking,
            self.booking,
        )

    def test_get_payment_for_booking_returns_latest_payment(self):
        first_payment = create_payment(
            booking=self.booking,
            phone_number="0712345678",
        )

        second_payment = create_payment(
            booking=self.booking,
            phone_number="0712345678",
        )

        latest_payment = get_payment_for_booking(
            self.booking
        )

        self.assertEqual(
            latest_payment,
            second_payment,
        )

        self.assertNotEqual(
            latest_payment,
            first_payment,
        )

    def test_payment_moves_to_processing(self):
        payment = create_payment(
            booking=self.booking,
            phone_number="0712345678",
        )

        mark_payment_processing(
            payment,
            checkout_request_id="ws_CO_123456789",
            merchant_request_id="29115-123456",
        )

        payment.refresh_from_db()

        self.assertEqual(
            payment.status,
            "processing",
        )

        self.assertEqual(
            payment.checkout_request_id,
            "ws_CO_123456789",
        )

        self.assertEqual(
            payment.merchant_request_id,
            "29115-123456",
        )

    def test_processing_payment_can_be_marked_paid(self):
        payment = create_payment(
            booking=self.booking,
            phone_number="0712345678",
        )

        mark_payment_processing(
            payment,
            checkout_request_id="ws_CO_123456789",
        )

        mark_payment_paid(
            payment,
            mpesa_receipt="RCP123456",
        )

        payment.refresh_from_db()

        self.assertEqual(
            payment.status,
            "paid",
        )

        self.assertEqual(
            payment.mpesa_receipt,
            "RCP123456",
        )

        self.assertIsNone(payment.transaction_date)

    def test_processing_payment_can_be_marked_paid_without_receipt(self):
        payment = create_payment(
            booking=self.booking,
            phone_number="0712345678",
        )
        mark_payment_processing(
            payment,
            checkout_request_id="ws_CO_NO_RECEIPT",
        )

        mark_payment_paid(payment)
        payment.refresh_from_db()

        self.assertEqual(payment.status, "paid")
        self.assertIsNone(payment.mpesa_receipt)
        self.assertIsNone(payment.transaction_date)

    def test_processing_payment_can_fail(self):
        payment = create_payment(
            booking=self.booking,
            phone_number="0712345678",
        )

        mark_payment_processing(
            payment,
            checkout_request_id="ws_CO_123456789",
        )

        mark_payment_failed(
            payment,
            result_code="1032",
            result_description="Request cancelled by user.",
        )

        payment.refresh_from_db()

        self.assertEqual(
            payment.status,
            "failed",
        )

        self.assertEqual(
            payment.result_code,
            "1032",
        )

    def test_paid_payment_cannot_be_changed_to_failed(self):
        payment = create_payment(
            booking=self.booking,
            phone_number="0712345678",
        )

        mark_payment_processing(
            payment,
            checkout_request_id="ws_CO_123456789",
        )

        mark_payment_paid(
            payment,
            mpesa_receipt="RCP123456",
        )

        with self.assertRaises(ValueError):
            mark_payment_failed(
                payment,
                result_code="1032",
            )

    def test_paid_payment_cannot_be_processed_again(self):
        payment = create_payment(
            booking=self.booking,
            phone_number="0712345678",
        )

        mark_payment_processing(
            payment,
            checkout_request_id="ws_CO_123456789",
        )

        mark_payment_paid(
            payment,
            mpesa_receipt="RCP123456",
        )

        with self.assertRaises(ValueError):
            mark_payment_processing(
                payment,
                checkout_request_id="ws_CO_SECOND",
            )

    def test_failed_payment_cannot_be_marked_paid(self):
        payment = create_payment(
            booking=self.booking,
            phone_number="0712345678",
        )

        mark_payment_failed(
            payment,
            result_code="1032",
            result_description="Request cancelled.",
        )

        with self.assertRaises(ValueError):
            mark_payment_paid(
                payment,
                mpesa_receipt="RCP123456",

            )


class MpesaIntegrationTests(TestCase):

    @patch("apps_core.payments.integrations.mpesa.datetime")
    @patch(
        "apps_core.payments.integrations.mpesa.MpesaIntegration."
        "get_access_token",
        return_value="test-access-token",
    )
    @patch("apps_core.payments.integrations.mpesa.requests.post")
    def test_query_stk_push_sends_expected_request(
        self,
        mock_post,
        mock_get_access_token,
        mock_datetime,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaIntegration,
        )

        expected_timestamp = "20261106010102"
        mock_datetime.now.return_value.strftime.return_value = (
            expected_timestamp
        )

        mock_response = Mock()
        mock_response.json.return_value = {
            "ResponseCode": "0",
            "ResponseDescription": "Success",
            "MerchantRequestID": "merchant-123",
            "CheckoutRequestID": "checkout-123",
            "ResultCode": "0",
            "ResultDesc": "Transaction processed.",
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        with patch.dict(
            "os.environ",
            {
                "MPESA_SHORTCODE": "174379",
                "MPESA_PASSKEY": "test-passkey",
                "MPESA_ENVIRONMENT": "sandbox",
            },
        ):
            integration = MpesaIntegration()
            result = integration.query_stk_push("checkout-123")

        expected_password = base64.b64encode(
            (
                "174379"
                "test-passkey"
                f"{expected_timestamp}"
            ).encode("utf-8")
        ).decode("utf-8")

        mock_get_access_token.assert_called_once_with()
        mock_datetime.now.return_value.strftime.assert_called_once_with(
            "%Y%m%d%H%M%S"
        )
        mock_post.assert_called_once_with(
            "https://sandbox.safaricom.co.ke/"
            "mpesa/stkpushquery/v1/query",
            json={
                "BusinessShortCode": "174379",
                "Password": expected_password,
                "Timestamp": expected_timestamp,
                "CheckoutRequestID": "checkout-123",
            },
            headers={
                "Authorization": "Bearer test-access-token",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        self.assertEqual(
            result,
            mock_response.json.return_value,
        )

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.get_access_token",
        return_value="test-access-token",
    )
    @patch("apps_core.payments.integrations.mpesa.requests.post")
    def test_query_stk_push_uses_production_endpoint(
        self,
        mock_post,
        mock_get_access_token,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaIntegration,
        )

        mock_response = Mock()
        mock_response.json.return_value = {"ResultCode": "0"}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        with patch.dict(
            "os.environ",
            {
                "MPESA_SHORTCODE": "174379",
                "MPESA_PASSKEY": "test-passkey",
                "MPESA_ENVIRONMENT": "production",
            },
        ):
            integration = MpesaIntegration()
            integration.query_stk_push("checkout-123")

        self.assertTrue(
            mock_post.call_args.args[0].startswith(
                "https://api.safaricom.co.ke/"
            )
        )

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.get_access_token",
        return_value="test-access-token",
    )
    @patch("apps_core.payments.integrations.mpesa.requests.post")
    def test_query_stk_push_propagates_http_errors(
        self,
        mock_post,
        mock_get_access_token,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaIntegration,
        )

        mock_response = Mock()
        mock_response.raise_for_status.side_effect = (
            requests.HTTPError("Provider returned an error.")
        )
        mock_post.return_value = mock_response

        with patch.dict(
            "os.environ",
            {
                "MPESA_SHORTCODE": "174379",
                "MPESA_PASSKEY": "test-passkey",
            },
        ):
            integration = MpesaIntegration()

            with self.assertRaises(requests.HTTPError):
                integration.query_stk_push("checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.get_access_token",
        return_value="test-access-token",
    )
    @patch("apps_core.payments.integrations.mpesa.requests.post")
    def test_query_stk_push_raises_provider_error_with_retry_after(
        self,
        mock_post,
        mock_get_access_token,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaIntegration,
            MpesaProviderError,
        )

        mock_response = Mock()
        mock_response.status_code = 429
        mock_response.headers = {
            "Retry-After": "30",
            "X-Request-Id": "req-429",
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        with patch.dict(
            "os.environ",
            {
                "MPESA_SHORTCODE": "174379",
                "MPESA_PASSKEY": "test-passkey",
            },
        ):
            integration = MpesaIntegration()

            with self.assertRaises(MpesaProviderError) as ctx:
                integration.query_stk_push("checkout-123")

        exc = ctx.exception
        self.assertEqual(exc.status_code, 429)
        self.assertEqual(exc.retry_after, "30")
        self.assertEqual(exc.request_id, "req-429")
        self.assertEqual(exc.operation, "stk_query")

    @patch("apps_core.payments.integrations.mpesa.requests.get")
    def test_get_access_token_raises_provider_error_for_http_403(
        self,
        mock_get,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaIntegration,
            MpesaProviderError,
        )

        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.headers = {"X-Correlation-Id": "corr-403"}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        with patch.dict(
            "os.environ",
            {
                "MPESA_CONSUMER_KEY": "test-key",
                "MPESA_CONSUMER_SECRET": "test-secret",
            },
        ):
            integration = MpesaIntegration()

            with self.assertRaises(MpesaProviderError) as ctx:
                integration.get_access_token()

        exc = ctx.exception
        self.assertEqual(exc.status_code, 403)
        self.assertEqual(exc.operation, "oauth")
        self.assertEqual(exc.request_id, "corr-403")
        self.assertIsNone(exc.retry_after)

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.get_access_token",
        return_value="test-access-token",
    )
    @patch("apps_core.payments.integrations.mpesa.requests.post")
    def test_query_stk_push_propagates_network_errors_and_timeouts(
        self,
        mock_post,
        mock_get_access_token,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaIntegration,
        )

        with patch.dict(
            "os.environ",
            {
                "MPESA_SHORTCODE": "174379",
                "MPESA_PASSKEY": "test-passkey",
            },
        ):
            integration = MpesaIntegration()

            for error in (
                requests.ConnectionError("Connection failed."),
                requests.Timeout("Request timed out."),
            ):
                mock_post.side_effect = error

                with self.assertRaises(type(error)):
                    integration.query_stk_push("checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.get_access_token",
        return_value="test-access-token",
    )
    @patch("apps_core.payments.integrations.mpesa.requests.post")
    def test_query_stk_push_rejects_invalid_json_and_non_object_response(
        self,
        mock_post,
        mock_get_access_token,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaIntegration,
        )

        with patch.dict(
            "os.environ",
            {
                "MPESA_SHORTCODE": "174379",
                "MPESA_PASSKEY": "test-passkey",
            },
        ):
            integration = MpesaIntegration()

            invalid_json_response = Mock()
            invalid_json_response.json.side_effect = ValueError(
                "Invalid JSON."
            )
            invalid_json_response.raise_for_status.return_value = None
            mock_post.return_value = invalid_json_response

            with self.assertRaisesMessage(
                ValueError,
                "M-Pesa STK Push Query returned invalid JSON.",
            ):
                integration.query_stk_push("checkout-123")

            unexpected_response = Mock()
            unexpected_response.json.return_value = ["not", "an", "object"]
            unexpected_response.raise_for_status.return_value = None
            mock_post.return_value = unexpected_response

            with self.assertRaisesMessage(
                ValueError,
                "M-Pesa STK Push Query returned an unexpected response.",
            ):
                integration.query_stk_push("checkout-123")

    def test_query_stk_push_requires_checkout_request_id(self):
        from apps_core.payments.integrations.mpesa import (
            MpesaIntegration,
        )

        with self.assertRaisesMessage(
            ValueError,
            "Checkout request ID is required.",
        ):
            MpesaIntegration().query_stk_push("")

    @patch("apps_core.payments.integrations.mpesa.requests.get")
    def test_get_access_token_returns_token(
        self,
        mock_get,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaIntegration,
        )

        mock_response = Mock()
        mock_response.json.return_value = {
            "access_token": "test-access-token",
        }
        mock_response.raise_for_status.return_value = None

        mock_get.return_value = mock_response

        with patch.dict(
            "os.environ",
            {
                "MPESA_CONSUMER_KEY": "test-key",
                "MPESA_CONSUMER_SECRET": "test-secret",
            },
        ):
            integration = MpesaIntegration()

            token = integration.get_access_token()

        self.assertEqual(
            token,
            "test-access-token",
        )

        mock_get.assert_called_once()

    @patch("apps_core.payments.integrations.mpesa.requests.post")
    @patch("apps_core.payments.integrations.mpesa.requests.get")
    def test_initiate_stk_push_returns_response(
        self,
        mock_get,
        mock_post,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaIntegration,
        )

        token_response = Mock()
        token_response.json.return_value = {
            "access_token": "test-access-token",
        }
        token_response.raise_for_status.return_value = None

        mock_get.return_value = token_response

        stk_response = Mock()
        stk_response.json.return_value = {
            "MerchantRequestID": "merchant-123",
            "CheckoutRequestID": "checkout-123",
            "ResponseCode": "0",
            "ResponseDescription": "Success",
        }
        stk_response.raise_for_status.return_value = None

        mock_post.return_value = stk_response

        with patch.dict(
            "os.environ",
            {
                "MPESA_CONSUMER_KEY": "test-key",
                "MPESA_CONSUMER_SECRET": "test-secret",
                "MPESA_SHORTCODE": "174379",
                "MPESA_PASSKEY": "test-passkey",
                "MPESA_CALLBACK_URL": (
                    "https://example.com/payments/mpesa/callback/"
                ),
                "MPESA_ENVIRONMENT": "sandbox",
            },
        ):
            integration = MpesaIntegration()

            result = integration.initiate_stk_push(
                amount=Decimal("1000.00"),
                phone_number="254712345678",
                account_reference="CLX-000001",
                transaction_description=(
                    "CLEANEX cleaning service"
                ),
            )

        self.assertEqual(
            result["ResponseCode"],
            "0",
        )

        self.assertEqual(
            result["CheckoutRequestID"],
            "checkout-123",
        )

        mock_get.assert_called_once()
        mock_post.assert_called_once()

    def test_generate_password(self):
        from apps_core.payments.integrations.mpesa import (
            MpesaIntegration,
        )

        with patch.dict(
            "os.environ",
            {
                "MPESA_SHORTCODE": "174379",
                "MPESA_PASSKEY": "test-passkey",
            },
        ):
            integration = MpesaIntegration()

            password = integration._generate_password(
                "20260924120000"
            )

        self.assertTrue(password)

    def test_rejects_zero_amount(self):
        from apps_core.payments.integrations.mpesa import (
            MpesaIntegration,
        )

        integration = MpesaIntegration()

        with self.assertRaisesMessage(
            ValueError,
            "STK Push amount must be greater than zero.",
        ):
            integration.initiate_stk_push(
                amount=Decimal("0.00"),
                phone_number="254712345678",
                account_reference="CLX-000001",
                transaction_description="Test payment",
            )



    @patch("apps_core.payments.integrations.mpesa.requests.post")
    @patch("apps_core.payments.integrations.mpesa.requests.get")
    def test_initiate_stk_push_missing_callback_url_is_local_validation(
        self,
        mock_get,
        mock_post,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaIntegration,
            MpesaLocalValidationError,
        )

        integration = MpesaIntegration()
        integration.shortcode = "174379"
        integration.passkey = "test-passkey"
        integration.callback_url = ""

        with self.assertRaises(MpesaLocalValidationError):
            integration.initiate_stk_push(
                amount=Decimal("1000.00"),
                phone_number="254712345678",
                account_reference="CLX-000001",
                transaction_description="Test payment",
            )

        mock_get.assert_not_called()
        mock_post.assert_not_called()

    @patch("apps_core.payments.integrations.mpesa.requests.get")
    def test_get_access_token_missing_credentials_is_local_validation(
        self,
        mock_get,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaIntegration,
            MpesaLocalValidationError,
        )

        for field in ("consumer_key", "consumer_secret"):
            with self.subTest(field=field):
                integration = MpesaIntegration()
                setattr(integration, field, "")

                with self.assertRaises(MpesaLocalValidationError):
                    integration.get_access_token()

                mock_get.assert_not_called()
                mock_get.reset_mock()


class PaymentInitiationTests(TestCase):

    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Test Customer",
            email="initiation@example.com",
            phone="254712345678",
        )

        self.service = Service.objects.create(
            name="Home Cleaning",
            slug="home-cleaning-initiation",
            description="Test cleaning service.",
            base_price=Decimal("5000.00"),
            duration_hours=2,
            is_active=True,
        )

        self.booking = Booking.objects.create(
            customer=self.customer,
            service=self.service,
            booking_date="2030-01-15",
            booking_time="10:00",
            address="Test Address",
            city="Nairobi",
            property_size=100,
            rooms=3,
            total_price=Decimal("5000.00"),
            status="pending",
        )

        self.payment = create_payment(
            booking=self.booking,
            phone_number="254712345678",
        )

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.initiate_stk_push"
    )
    def test_successful_stk_push_moves_payment_to_processing(
        self,
        mock_stk_push,
    ):
        mock_stk_push.return_value = {
            "MerchantRequestID": "merchant-123",
            "CheckoutRequestID": "checkout-123",
            "ResponseCode": "0",
            "ResponseDescription": "Success",
        }

        payment = initiate_payment(
            self.payment
        )

        self.assertEqual(
            payment.status,
            "processing",
        )

        self.assertEqual(
            payment.checkout_request_id,
            "checkout-123",
        )

        self.assertEqual(
            payment.merchant_request_id,
            "merchant-123",
        )

        expected_reference = (
            self.booking.booking_reference
        )

        mock_stk_push.assert_called_once_with(
            amount=Decimal("5000.00"),
            phone_number="254712345678",
            account_reference=expected_reference,
            transaction_description=(
                f"CLEANEX payment for {expected_reference}"
            ),
        )


    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.initiate_stk_push"
    )
    def test_rejected_stk_push_does_not_change_payment_status(
        self,
        mock_stk_push,
    ):
        mock_stk_push.return_value = {
            "ResponseCode": "1",
            "ResponseDescription": (
                "Unable to process request."
            ),
        }

        with self.assertRaisesMessage(
            ValueError,
            "Unable to process request.",
        ):
            initiate_payment(
                self.payment
            )

        self.payment.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            "failed",
        )

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.initiate_stk_push"
    )
    def test_local_validation_error_marks_payment_failed_without_provider_metadata(
        self,
        mock_stk_push,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaLocalValidationError,
        )

        mock_stk_push.side_effect = MpesaLocalValidationError(
            "MPESA_CALLBACK_URL is not configured."
        )

        with self.assertRaises(MpesaLocalValidationError):
            initiate_payment(self.payment)

        self.payment.refresh_from_db()

        self.assertEqual(self.payment.status, "failed")
        self.assertIsNone(self.payment.result_code)
        self.assertIsNone(self.payment.checkout_request_id)
        self.assertIsNone(self.payment.merchant_request_id)
        self.assertIsNone(self.payment.mpesa_receipt)
        self.assertIsNone(self.payment.transaction_date)
        self.assertEqual(
            self.payment.result_description,
            "The payment request could not be submitted to M-Pesa. "
            "Please contact CLEANEX support.",
        )
        mock_stk_push.assert_called_once()

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.initiate_stk_push"
    )
    def test_stk_push_network_and_provider_errors_mark_payment_unknown(
        self,
        mock_stk_push,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaProviderError,
        )

        cases = {
            "timeout": requests.Timeout("timed out"),
            "connection_error": requests.ConnectionError(
                "connection failed"
            ),
            "rate_limited": MpesaProviderError(
                "rate limited",
                operation="stk_push",
                status_code=429,
            ),
            "forbidden": MpesaProviderError(
                "forbidden",
                operation="stk_push",
                status_code=403,
            ),
        }

        for name, error in cases.items():
            with self.subTest(name=name):
                payment = create_payment(
                    booking=self.booking,
                    phone_number="254712345678",
                )
                mock_stk_push.reset_mock()
                mock_stk_push.side_effect = error

                with self.assertRaises(type(error)):
                    initiate_payment(payment)

                payment.refresh_from_db()

                self.assertEqual(payment.status, "unknown")
                self.assertIsNone(payment.checkout_request_id)
                self.assertIsNone(payment.merchant_request_id)
                mock_stk_push.assert_called_once()

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.initiate_stk_push"
    )
    def test_stk_push_malformed_or_incomplete_success_marks_payment_unknown(
        self,
        mock_stk_push,
    ):
        cases = {
            "non_dict_response": {
                "return_value": ["not", "a", "dict"],
            },
            "missing_checkout_id": {
                "return_value": {
                    "ResponseCode": "0",
                    "ResponseDescription": "Success",
                },
            },
            "invalid_json": {
                "side_effect": ValueError(
                    "M-Pesa STK Push returned invalid JSON."
                ),
            },
        }

        for name, config in cases.items():
            with self.subTest(name=name):
                payment = create_payment(
                    booking=self.booking,
                    phone_number="254712345678",
                )
                mock_stk_push.reset_mock()
                mock_stk_push.side_effect = config.get("side_effect")
                mock_stk_push.return_value = config.get("return_value")

                with self.assertRaises(ValueError):
                    initiate_payment(payment)

                payment.refresh_from_db()

                self.assertEqual(payment.status, "unknown")
                self.assertIsNone(payment.checkout_request_id)
                self.assertIsNone(payment.merchant_request_id)
                mock_stk_push.assert_called_once()

    def test_processing_payment_cannot_be_initiated_again(self):
        self.payment.status = "processing"
        self.payment.save(
            update_fields=["status"]
        )

        with self.assertRaisesMessage(
            ValueError,
            "Only pending payments can be initiated.",
        ):
            initiate_payment(
                self.payment
            )

    def test_paid_payment_cannot_be_initiated(self):
        self.payment.status = "paid"
        self.payment.save(
            update_fields=["status"]
        )

        with self.assertRaisesMessage(
            ValueError,
            "Only pending payments can be initiated.",
        ):
            initiate_payment(
                self.payment
            )

    def test_failed_payment_cannot_be_initiated(self):
        self.payment.status = "failed"
        self.payment.save(
            update_fields=["status"]
        )

        with self.assertRaisesMessage(
            ValueError,
            "Only pending payments can be initiated.",
        ):
            initiate_payment(
                self.payment
            )

    def test_unsaved_payment_cannot_be_initiated(self):
        unsaved_payment = Payment(
            booking=self.booking,
            amount=Decimal("5000.00"),
            method="mpesa",
            status="pending",
            phone_number="254712345678",
        )

        with self.assertRaisesMessage(
            ValueError,
            "The payment must be saved before an STK Push can be initiated.",
        ):
            initiate_payment(
                unsaved_payment
            )


class MpesaCallbackTests(TestCase):

    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Callback Customer",
            email="callback@example.com",
            phone="254712345678",
        )

        self.service = Service.objects.create(
            name="Callback Cleaning",
            slug="callback-cleaning",
            description="Callback test service.",
            base_price=Decimal("5000.00"),
            duration_hours=2,
            is_active=True,
        )

        self.booking = Booking.objects.create(
            customer=self.customer,
            service=self.service,
            booking_date="2030-01-15",
            booking_time="10:00",
            address="Callback Address",
            city="Nairobi",
            property_size=100,
            rooms=3,
            total_price=Decimal("5000.00"),
            status="pending",
        )

        self.payment = create_payment(
            booking=self.booking,
            phone_number="254712345678",
        )

        self.payment.status = "processing"
        self.payment.checkout_request_id = "callback-checkout-123"
        self.payment.merchant_request_id = "callback-merchant-123"

        self.payment.save(
            update_fields=[
                "status",
                "checkout_request_id",
                "merchant_request_id",
            ]
        )

    def successful_callback_payload(self):
        return {
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": (
                        "callback-merchant-123"
                    ),
                    "CheckoutRequestID": (
                        "callback-checkout-123"
                    ),
                    "ResultCode": 0,
                    "ResultDesc": (
                        "The service request is processed successfully."
                    ),
                    "CallbackMetadata": {
                        "Item": [
                            {
                                "Name": "Amount",
                                "Value": 5000,
                            },
                            {
                                "Name": "MpesaReceiptNumber",
                                "Value": "ABC123XYZ",
                            },
                            {
                                "Name": "TransactionDate",
                                "Value": 20300115101530,
                            },
                            {
                                "Name": "PhoneNumber",
                                "Value": 254712345678,
                            },
                        ]
                    },
                }
            }
        }

    def failed_callback_payload(self):
        return {
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": (
                        "callback-merchant-123"
                    ),
                    "CheckoutRequestID": (
                        "callback-checkout-123"
                    ),
                    "ResultCode": 1032,
                    "ResultDesc": (
                        "Request cancelled by user."
                    ),
                }
            }
        }

    def successful_query_response(self, **overrides):
        response = {
            "ResponseCode": "0",
            "CheckoutRequestID": "callback-checkout-123",
            "MerchantRequestID": "callback-merchant-123",
            "ResultCode": "0",
            "ResultDesc": "Transaction processed successfully.",
        }
        response.update(overrides)
        return response

    def prepare_paid_payment(
        self,
        *,
        receipt=None,
        clear_transaction_date=False,
    ):
        mark_payment_paid(
            self.payment,
            mpesa_receipt=receipt,
        )
        self.payment.refresh_from_db()

        if clear_transaction_date:
            self.payment.transaction_date = None
            self.payment.save(
                update_fields=["transaction_date"]
            )

        return self.payment

    def reset_verification_schedule(self):
        Payment.objects.filter(pk=self.payment.pk).update(
            next_verification_at=None,
            verification_lease_id=None,
            verification_lease_expires_at=None,
        )
        self.payment.refresh_from_db()

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_query_success_marks_payment_paid_with_callback_receipt(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()

        payment = process_mpesa_callback(
            self.successful_callback_payload()
        )
        payment.refresh_from_db()

        self.assertEqual(payment.status, "paid")
        self.assertEqual(payment.mpesa_receipt, "ABC123XYZ")
        self.assertIsNotNone(payment.transaction_date)
        self.assertEqual(
            timezone.localtime(
                payment.transaction_date,
                ZoneInfo("Africa/Nairobi"),
            ).strftime("%Y%m%d%H%M%S"),
            "20300115101530",
        )
        self.assertIsNotNone(payment.transaction_date.tzinfo)
        self.assertEqual(payment.result_code, "0")
        self.assertEqual(
            payment.result_description,
            "Transaction processed successfully.",
        )
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_query_confirmed_cancellation_marks_payment_failed(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response(
            ResultCode="1032",
            ResultDesc="Request cancelled by user.",
        )

        payment = process_mpesa_callback(self.failed_callback_payload())
        payment.refresh_from_db()

        self.assertEqual(payment.status, "failed")
        self.assertEqual(payment.result_code, "1032")
        self.assertEqual(
            payment.result_description,
            "Request cancelled by user.",
        )
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_callback_results_and_receipt_are_not_trusted(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response(
            ResultCode="pending",
        )

        process_mpesa_callback(self.successful_callback_payload())
        self.reset_verification_schedule()
        process_mpesa_callback(self.failed_callback_payload())
        self.payment.refresh_from_db()

        self.assertEqual(self.payment.status, "processing")
        self.assertIsNone(self.payment.mpesa_receipt)
        self.assertIsNone(self.payment.transaction_date)
        self.assertEqual(mock_query.call_count, 2)

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_success_callback_code_does_not_override_query_cancellation(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response(
            ResultCode="1032",
            ResultDesc="Request cancelled by user.",
        )
        payload = self.successful_callback_payload()
        payload["Body"]["stkCallback"]["ResultCode"] = "0"

        payment = process_mpesa_callback(payload)

        self.assertEqual(payment.status, "failed")
        self.assertEqual(payment.result_code, "1032")
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_nonzero_callback_code_does_not_override_query_success(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()
        payload = self.failed_callback_payload()
        payload["Body"]["stkCallback"]["ResultCode"] = "1032"

        payment = process_mpesa_callback(payload)

        self.assertEqual(payment.status, "paid")
        self.assertIsNone(payment.mpesa_receipt)
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_missing_optional_query_merchant_id_is_allowed(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response(
            MerchantRequestID=None,
        )

        payment = verify_payment(self.payment)

        self.assertEqual(payment.status, "paid")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_unknown_payment_can_be_verified_independently(
        self,
        mock_query,
    ):
        self.payment.status = "unknown"
        self.payment.save(update_fields=["status"])
        mock_query.return_value = self.successful_query_response()

        payment = verify_payment(self.payment)

        self.assertEqual(payment.status, "paid")
        self.assertIsNone(payment.mpesa_receipt)
        self.assertIsNone(payment.transaction_date)
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_payment_without_checkout_id_is_not_queried(
        self,
        mock_query,
    ):
        self.payment.checkout_request_id = ""
        self.payment.save(update_fields=["checkout_request_id"])

        payment = verify_payment(self.payment)

        self.assertEqual(payment.status, "processing")
        mock_query.assert_not_called()

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_nonzero_or_missing_response_code_is_inconclusive(
        self,
        mock_query,
    ):
        for response in (
            self.successful_query_response(ResponseCode="1"),
            self.successful_query_response(ResponseCode=None),
        ):
            mock_query.return_value = response
            payment = verify_payment(self.payment)
            self.assertEqual(payment.status, "processing")
            self.payment.refresh_from_db()
            self.assertEqual(self.payment.status, "processing")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_missing_or_mismatched_query_identifiers_are_inconclusive(
        self,
        mock_query,
    ):
        responses = (
            self.successful_query_response(CheckoutRequestID=None),
            self.successful_query_response(
                CheckoutRequestID="different-checkout"
            ),
            self.successful_query_response(
                MerchantRequestID="different-merchant"
            ),
        )

        for response in responses:
            mock_query.return_value = response
            payment = verify_payment(self.payment)
            self.assertEqual(payment.status, "processing")
            self.payment.refresh_from_db()
            self.assertEqual(self.payment.status, "processing")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_missing_or_unknown_query_result_is_recoverable(
        self,
        mock_query,
    ):
        for response in (
            self.successful_query_response(ResultCode=None),
            self.successful_query_response(ResultCode="9999"),
        ):
            mock_query.return_value = response
            payment = verify_payment(self.payment)
            self.assertEqual(payment.status, "processing")
            self.payment.refresh_from_db()
            self.assertEqual(self.payment.status, "processing")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_query_1037_marks_payment_failed_and_allows_retry(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response(
            ResultCode="1037",
            ResultDesc="DS timeout user cannot be reached",
        )

        payment = verify_payment(self.payment)

        self.payment.refresh_from_db()
        self.assertEqual(payment.status, "failed")
        self.assertEqual(self.payment.status, "failed")
        self.assertEqual(self.payment.result_code, "1037")
        self.assertEqual(
            self.payment.result_description,
            "DS timeout user cannot be reached",
        )

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_query_1019_marks_payment_failed(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response(
            ResultCode="1019",
            ResultDesc="Transaction Expired",
        )

        payment = verify_payment(self.payment)

        self.payment.refresh_from_db()
        self.assertEqual(payment.status, "failed")
        self.assertEqual(self.payment.status, "failed")
        self.assertEqual(self.payment.result_code, "1019")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_query_insufficient_balance_marks_payment_failed(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response(
            ResultCode="1",
            ResultDesc="The balance is insufficient for the transaction.",
        )

        payment = verify_payment(self.payment)

        self.payment.refresh_from_db()
        self.assertEqual(payment.status, "failed")
        self.assertEqual(self.payment.status, "failed")
        self.assertEqual(self.payment.result_code, "1")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_query_http_500_remains_uncertain(
        self,
        mock_query,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaProviderError,
        )

        mock_query.side_effect = MpesaProviderError(
            "M-Pesa stk_query request failed with HTTP 500.",
            operation="stk_query",
            status_code=500,
        )

        with self.assertLogs(
            "apps_core.payments.services",
            level="WARNING",
        ) as logs:
            payment = verify_payment(self.payment)

        self.payment.refresh_from_db()
        self.assertEqual(payment.status, "processing")
        self.assertEqual(self.payment.status, "processing")
        self.assertTrue(
            any("HTTP 500" in record.getMessage() for record in logs.records)
        )

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_unknown_query_result_code_is_logged_safely(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response(
            ResultCode="9999",
            ResultDesc="Undocumented provider result.",
        )

        with self.assertLogs(
            "apps_core.payments.services",
            level="INFO",
        ) as logs:
            payment = verify_payment(self.payment)

        self.payment.refresh_from_db()
        self.assertEqual(payment.status, "processing")
        self.assertEqual(self.payment.status, "processing")

        message = logs.records[0].getMessage()
        self.assertIn("result_code=9999", message)
        self.assertIn(
            "result_description=Undocumented provider result.",
            message,
        )

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_missing_query_result_code_and_description_are_safe(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response(
            ResultCode=None,
            ResultDesc=None,
        )

        with self.assertLogs(
            "apps_core.payments.services",
            level="INFO",
        ) as logs:
            payment = verify_payment(self.payment)

        self.payment.refresh_from_db()
        self.assertEqual(payment.status, "processing")
        self.assertEqual(self.payment.status, "processing")

        message = logs.records[0].getMessage()
        self.assertIn("result_code=missing", message)
        self.assertIn("result_description=unavailable", message)

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_query_result_description_newlines_are_sanitized(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response(
            ResultCode="9999",
            ResultDesc="First line\nSecond line\r\nThird line",
        )

        with self.assertLogs(
            "apps_core.payments.services",
            level="INFO",
        ) as logs:
            payment = verify_payment(self.payment)

        self.payment.refresh_from_db()
        self.assertEqual(payment.status, "processing")
        self.assertEqual(self.payment.status, "processing")

        message = logs.records[0].getMessage()
        self.assertIn(
            "result_description=First line Second line Third line",
            message,
        )
        self.assertNotIn("\n", message)
        self.assertNotIn("\r", message)

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_query_result_description_is_truncated(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response(
            ResultCode="9999",
            ResultDesc="x" * 250,
        )

        with self.assertLogs(
            "apps_core.payments.services",
            level="INFO",
        ) as logs:
            payment = verify_payment(self.payment)

        self.payment.refresh_from_db()
        self.assertEqual(payment.status, "processing")
        self.assertEqual(self.payment.status, "processing")

        message = logs.records[0].getMessage()
        self.assertIn("x" * 200, message)
        self.assertNotIn("x" * 201, message)

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_inconclusive_query_log_excludes_sensitive_identifiers(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response(
            ResultCode="9999",
            ResultDesc="Undocumented provider result.",
        )

        with self.assertLogs(
            "apps_core.payments.services",
            level="INFO",
        ) as logs:
            payment = verify_payment(self.payment)

        self.payment.refresh_from_db()
        self.assertEqual(payment.status, "processing")
        self.assertEqual(self.payment.status, "processing")

        output = "\n".join(logs.output)
        self.assertIn("result_code=9999", output)
        self.assertNotIn("callback-checkout-123", output)
        self.assertNotIn("callback-merchant-123", output)
        self.assertNotIn("254712345678", output)
        self.assertNotIn("access_token", output)
        self.assertNotIn("ABC123XYZ", output)

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_query_errors_leave_payment_recoverable(
        self,
        mock_query,
    ):
        for error in (
            requests.HTTPError("Provider error."),
            requests.ConnectionError("Network error."),
            requests.Timeout("Provider timed out."),
            ValueError("Malformed provider response."),
            None,
        ):
            mock_query.return_value = error
            if isinstance(error, Exception):
                mock_query.side_effect = error
            else:
                mock_query.side_effect = None
            payment = verify_payment(self.payment)
            self.assertEqual(payment.status, "processing")
            self.payment.refresh_from_db()
            self.assertEqual(self.payment.status, "processing")
        mock_query.side_effect = None

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_http_403_during_verification_leaves_payment_unchanged(
        self,
        mock_query,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaProviderError,
        )

        mock_query.side_effect = MpesaProviderError(
            "M-Pesa stk_query request failed with HTTP 403.",
            operation="stk_query",
            status_code=403,
            retry_after=None,
            request_id="req-403",
        )

        with self.assertLogs(
            "apps_core.payments.services",
            level="WARNING",
        ) as logs:
            payment = verify_payment(self.payment)

        self.payment.refresh_from_db()
        self.assertEqual(payment.status, "processing")
        self.assertEqual(self.payment.status, "processing")

        output = "\n".join(logs.output)
        self.assertIn("HTTP 403", output)
        self.assertIn("req-403", output)
        self.assertNotIn("callback-checkout-123", output)

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_http_429_retry_after_is_logged_without_sensitive_data(
        self,
        mock_query,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaProviderError,
        )

        mock_query.side_effect = MpesaProviderError(
            "M-Pesa stk_query request failed with HTTP 429.",
            operation="stk_query",
            status_code=429,
            retry_after="30",
            request_id="req-429",
        )

        with self.assertLogs(
            "apps_core.payments.services",
            level="WARNING",
        ) as logs:
            payment = verify_payment(self.payment)

        self.payment.refresh_from_db()
        self.assertEqual(payment.status, "processing")
        self.assertEqual(self.payment.status, "processing")

        output = "\n".join(logs.output)
        self.assertIn("HTTP 429", output)
        self.assertIn("retry_after=30", output)
        self.assertIn("req-429", output)
        self.assertNotIn("callback-checkout-123", output)
        self.assertNotIn("254712345678", output)
        self.assertNotIn("access_token", output)

        with self.assertRaises(ValueError):
            initiate_payment(self.payment)

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_older_payment_verification_does_not_change_newer_payment(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()
        newer_payment = create_payment(
            booking=self.booking,
            phone_number="254712345678",
        )
        mark_payment_processing(
            newer_payment,
            checkout_request_id="newer-checkout-123",
            merchant_request_id="newer-merchant-123",
        )

        payment = verify_payment(self.payment)
        newer_payment.refresh_from_db()

        self.assertEqual(payment.status, "paid")
        self.assertEqual(newer_payment.status, "processing")
        self.assertEqual(
            newer_payment.checkout_request_id,
            "newer-checkout-123",
        )

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_older_callback_does_not_change_newer_payment(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()
        newer_payment = create_payment(
            booking=self.booking,
            phone_number="254712345678",
        )
        mark_payment_processing(
            newer_payment,
            checkout_request_id="newer-checkout-123",
            merchant_request_id="newer-merchant-123",
        )

        payment = process_mpesa_callback(
            self.successful_callback_payload()
        )
        newer_payment.refresh_from_db()

        self.assertEqual(payment.status, "paid")
        self.assertEqual(newer_payment.status, "processing")
        self.assertEqual(
            newer_payment.checkout_request_id,
            "newer-checkout-123",
        )

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_duplicate_verified_callbacks_are_idempotent(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()
        payload = self.successful_callback_payload()

        first = process_mpesa_callback(payload)
        second = process_mpesa_callback(payload)

        self.assertEqual(first.status, "paid")
        self.assertEqual(second.status, "paid")
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_terminal_payment_is_not_queried_or_changed(
        self,
        mock_query,
    ):
        self.payment.status = "paid"
        self.payment.mpesa_receipt = "EXISTING123"
        self.payment.save(
            update_fields=["status", "mpesa_receipt"]
        )

        paid = verify_payment(self.payment)
        self.assertEqual(paid.status, "paid")
        self.assertEqual(paid.mpesa_receipt, "EXISTING123")

        self.payment.status = "failed"
        self.payment.save(update_fields=["status"])
        failed = verify_payment(self.payment)
        self.assertEqual(failed.status, "failed")
        mock_query.assert_not_called()

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_stale_query_result_does_not_overwrite_new_status(
        self,
        mock_query,
    ):
        def fail_payment_during_query(checkout_request_id):
            Payment.objects.filter(pk=self.payment.pk).update(
                status="failed"
            )
            return self.successful_query_response()

        mock_query.side_effect = fail_payment_during_query

        payment = verify_payment(self.payment)
        self.payment.refresh_from_db()

        self.assertEqual(payment.status, "failed")
        self.assertEqual(self.payment.status, "failed")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_changed_checkout_id_invalidates_query_result(
        self,
        mock_query,
    ):
        def change_checkout_during_query(checkout_request_id):
            Payment.objects.filter(pk=self.payment.pk).update(
                checkout_request_id="new-checkout"
            )
            return self.successful_query_response()

        mock_query.side_effect = change_checkout_during_query

        payment = verify_payment(self.payment)
        self.payment.refresh_from_db()

        self.assertEqual(payment.status, "processing")
        self.assertEqual(self.payment.checkout_request_id, "new-checkout")

    def test_callback_requires_checkout_request_id(self):
        payload = self.successful_callback_payload()

        del payload["Body"]["stkCallback"][
            "CheckoutRequestID"
        ]

        with self.assertRaisesMessage(
            ValueError,
            "Checkout request ID is missing from callback.",
        ):
            process_mpesa_callback(
                payload
            )

    def test_callback_requires_matching_payment(self):
        payload = self.successful_callback_payload()

        payload["Body"]["stkCallback"][
            "CheckoutRequestID"
        ] = "unknown-checkout-id"

        with self.assertRaisesMessage(
            ValueError,
            "Payment matching the callback was not found.",
        ):
            process_mpesa_callback(
                payload
            )

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_callback_receipt_is_not_required_or_stored(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()
        payload = self.successful_callback_payload()

        payload["Body"]["stkCallback"][
            "CallbackMetadata"
        ]["Item"] = [
            {
                "Name": "Amount",
                "Value": 5000,
            },
            {
                "Name": "PhoneNumber",
                "Value": 254712345678,
            },
        ]

        payment = process_mpesa_callback(payload)
        self.payment.refresh_from_db()
        self.assertEqual(payment.status, "paid")
        self.assertEqual(self.payment.status, "paid")
        self.assertIsNone(self.payment.mpesa_receipt)
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_duplicate_paid_callback_is_safe(self, mock_query):
        mock_query.return_value = self.successful_query_response()
        payload = self.successful_callback_payload()

        process_mpesa_callback(
            payload
        )

        payment = process_mpesa_callback(
            payload
        )

        payment.refresh_from_db()

        self.assertEqual(
            payment.status,
            "paid",
        )

        self.assertEqual(payment.mpesa_receipt, "ABC123XYZ")
        self.assertIsNotNone(payment.transaction_date)
        self.assertEqual(
            timezone.localtime(
                payment.transaction_date,
                ZoneInfo("Africa/Nairobi"),
            ).strftime("%Y%m%d%H%M%S"),
            "20300115101530",
        )
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_duplicate_failed_callback_is_safe(self, mock_query):
        mock_query.return_value = self.successful_query_response(
            ResultCode="1032",
            ResultDesc="Request cancelled by user.",
        )
        payload = self.failed_callback_payload()

        process_mpesa_callback(
            payload
        )

        payment = process_mpesa_callback(
            payload
        )

        payment.refresh_from_db()

        self.assertEqual(
            payment.status,
            "failed",
        )

        self.assertEqual(
            payment.result_code,
            "1032",
        )
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_callback_receipt_is_not_applied_when_query_cancels(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response(
            ResultCode="1032",
            ResultDesc="Request cancelled by user.",
        )

        payment = process_mpesa_callback(
            self.successful_callback_payload()
        )
        payment.refresh_from_db()
        metadata = MpesaCallbackMetadata.objects.get(
            payment=self.payment
        )

        self.assertEqual(payment.status, "failed")
        self.assertIsNone(payment.mpesa_receipt)
        self.assertIsNone(payment.transaction_date)
        self.assertEqual(metadata.status, "failed")
        self.assertEqual(metadata.last_error, "result_not_success")
        self.assertEqual(metadata.mpesa_receipt, "ABC123XYZ")
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_callback_receipt_is_not_applied_when_query_inconclusive(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response(
            ResultCode="pending",
        )

        payment = process_mpesa_callback(
            self.successful_callback_payload()
        )
        payment.refresh_from_db()
        metadata = MpesaCallbackMetadata.objects.get(
            payment=self.payment
        )

        self.assertEqual(payment.status, "processing")
        self.assertIsNone(payment.mpesa_receipt)
        self.assertIsNone(payment.transaction_date)
        self.assertEqual(metadata.status, "pending")
        self.assertEqual(metadata.last_error, "verification_unavailable")
        self.assertEqual(metadata.mpesa_receipt, "ABC123XYZ")
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_callback_without_metadata_leaves_transaction_date_null(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()
        payload = self.successful_callback_payload()
        del payload["Body"]["stkCallback"]["CallbackMetadata"]

        payment = process_mpesa_callback(payload)
        payment.refresh_from_db()

        self.assertEqual(payment.status, "paid")
        self.assertIsNone(payment.mpesa_receipt)
        self.assertIsNone(payment.transaction_date)
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_non_dict_callback_metadata_is_ignored(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()
        payload = self.successful_callback_payload()
        payload["Body"]["stkCallback"]["CallbackMetadata"] = "not-a-dict"

        payment = process_mpesa_callback(payload)
        payment.refresh_from_db()

        self.assertEqual(payment.status, "paid")
        self.assertIsNone(payment.mpesa_receipt)
        self.assertIsNone(payment.transaction_date)
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_malformed_metadata_items_are_ignored(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()
        payload = self.successful_callback_payload()
        payload["Body"]["stkCallback"]["CallbackMetadata"] = {
            "Item": "not-a-list"
        }

        payment = process_mpesa_callback(payload)
        payment.refresh_from_db()

        self.assertEqual(payment.status, "paid")
        self.assertIsNone(payment.mpesa_receipt)
        self.assertIsNone(payment.transaction_date)
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_invalid_transaction_date_is_ignored(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()
        payload = self.successful_callback_payload()

        for item in payload["Body"]["stkCallback"][
            "CallbackMetadata"
        ]["Item"]:
            if item.get("Name") == "TransactionDate":
                item["Value"] = "not-a-date"

        payment = process_mpesa_callback(payload)
        payment.refresh_from_db()

        self.assertEqual(payment.status, "paid")
        self.assertEqual(payment.mpesa_receipt, "ABC123XYZ")
        self.assertIsNone(payment.transaction_date)
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_empty_receipt_is_ignored(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()
        payload = self.successful_callback_payload()

        for item in payload["Body"]["stkCallback"][
            "CallbackMetadata"
        ]["Item"]:
            if item.get("Name") == "MpesaReceiptNumber":
                item["Value"] = "   "

        payment = process_mpesa_callback(payload)
        payment.refresh_from_db()

        self.assertEqual(payment.status, "paid")
        self.assertIsNone(payment.mpesa_receipt)
        self.assertEqual(
            timezone.localtime(
                payment.transaction_date,
                ZoneInfo("Africa/Nairobi"),
            ).strftime("%Y%m%d%H%M%S"),
            "20300115101530",
        )
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_overlength_receipt_is_ignored(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()
        payload = self.successful_callback_payload()

        for item in payload["Body"]["stkCallback"][
            "CallbackMetadata"
        ]["Item"]:
            if item.get("Name") == "MpesaReceiptNumber":
                item["Value"] = "R" * 101

        payment = process_mpesa_callback(payload)
        payment.refresh_from_db()

        self.assertEqual(payment.status, "paid")
        self.assertIsNone(payment.mpesa_receipt)
        self.assertEqual(
            timezone.localtime(
                payment.transaction_date,
                ZoneInfo("Africa/Nairobi"),
            ).strftime("%Y%m%d%H%M%S"),
            "20300115101530",
        )
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_duplicate_receipt_belongs_to_another_payment(
        self,
        mock_query,
    ):
        other_payment = create_payment(
            booking=self.booking,
            phone_number="254700000000",
        )
        mark_payment_processing(
            other_payment,
            checkout_request_id="other-checkout-123",
        )
        mark_payment_paid(
            other_payment,
            mpesa_receipt="ABC123XYZ",
        )

        mock_query.return_value = self.successful_query_response()

        payment = process_mpesa_callback(
            self.successful_callback_payload()
        )
        payment.refresh_from_db()
        other_payment.refresh_from_db()

        self.assertEqual(payment.status, "paid")
        self.assertIsNone(payment.mpesa_receipt)
        self.assertEqual(
            timezone.localtime(
                payment.transaction_date,
                ZoneInfo("Africa/Nairobi"),
            ).strftime("%Y%m%d%H%M%S"),
            "20300115101530",
        )
        self.assertEqual(other_payment.mpesa_receipt, "ABC123XYZ")
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_callback_metadata_saved_when_merchant_id_matches(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()

        payment = process_mpesa_callback(
            self.successful_callback_payload()
        )
        payment.refresh_from_db()

        self.assertEqual(payment.status, "paid")
        self.assertEqual(payment.mpesa_receipt, "ABC123XYZ")
        self.assertEqual(
            timezone.localtime(
                payment.transaction_date,
                ZoneInfo("Africa/Nairobi"),
            ).strftime("%Y%m%d%H%M%S"),
            "20300115101530",
        )
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_missing_callback_merchant_id_still_verifies_without_metadata(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()
        payload = self.successful_callback_payload()
        del payload["Body"]["stkCallback"]["MerchantRequestID"]

        payment = process_mpesa_callback(payload)
        payment.refresh_from_db()

        self.assertEqual(payment.status, "paid")
        self.assertIsNone(payment.mpesa_receipt)
        self.assertIsNone(payment.transaction_date)
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_mismatched_callback_merchant_id_still_verifies_without_metadata(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()
        payload = self.successful_callback_payload()
        payload["Body"]["stkCallback"]["MerchantRequestID"] = (
            "wrong-merchant"
        )

        payment = process_mpesa_callback(payload)
        payment.refresh_from_db()

        self.assertEqual(payment.status, "paid")
        self.assertIsNone(payment.mpesa_receipt)
        self.assertIsNone(payment.transaction_date)
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.services._available_mpesa_receipt",
        return_value="ABC123XYZ",
    )
    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_verify_payment_retries_without_receipt_after_integrity_error(
        self,
        mock_query,
        mock_available_receipt,
    ):
        mock_query.return_value = self.successful_query_response()

        other_payment = create_payment(
            booking=self.booking,
            phone_number="254700000000",
        )
        mark_payment_processing(
            other_payment,
            checkout_request_id="other-checkout-123",
        )
        mark_payment_paid(
            other_payment,
            mpesa_receipt="ABC123XYZ",
        )

        payment = process_mpesa_callback(
            self.successful_callback_payload()
        )
        payment.refresh_from_db()
        other_payment.refresh_from_db()

        self.assertEqual(payment.status, "paid")
        self.assertIsNone(payment.mpesa_receipt)
        self.assertEqual(
            timezone.localtime(
                payment.transaction_date,
                ZoneInfo("Africa/Nairobi"),
            ).strftime("%Y%m%d%H%M%S"),
            "20300115101530",
        )
        self.assertEqual(other_payment.mpesa_receipt, "ABC123XYZ")
        mock_query.assert_called_once_with("callback-checkout-123")
        self.assertEqual(mock_available_receipt.call_count, 2)

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_late_callback_enriches_query_paid_payment_receipt(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()

        payment = verify_payment(self.payment)

        self.assertEqual(payment.status, "paid")
        self.assertIsNone(payment.mpesa_receipt)
        self.assertIsNone(payment.transaction_date)

        mock_query.reset_mock()

        result = process_mpesa_callback(
            self.successful_callback_payload()
        )
        result.refresh_from_db()
        self.booking.refresh_from_db()

        self.assertEqual(result.status, "paid")
        self.assertEqual(result.mpesa_receipt, "ABC123XYZ")
        self.assertIsNotNone(result.transaction_date)
        self.assertEqual(
            timezone.localtime(
                result.transaction_date,
                ZoneInfo("Africa/Nairobi"),
            ).strftime("%Y%m%d%H%M%S"),
            "20300115101530",
        )
        self.assertIsNotNone(result.transaction_date.tzinfo)
        self.assertEqual(result.result_code, "0")
        self.assertEqual(
            result.result_description,
            "Transaction processed successfully.",
        )
        self.assertEqual(self.booking.status, "pending")
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_late_callback_fills_null_transaction_date_without_overwriting_receipt(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()
        self.prepare_paid_payment(
            receipt="EXISTING123",
            clear_transaction_date=True,
        )

        result = process_mpesa_callback(
            self.successful_callback_payload()
        )
        result.refresh_from_db()

        self.assertEqual(result.mpesa_receipt, "EXISTING123")
        self.assertEqual(
            timezone.localtime(
                result.transaction_date,
                ZoneInfo("Africa/Nairobi"),
            ).strftime("%Y%m%d%H%M%S"),
            "20300115101530",
        )
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_late_callback_does_not_overwrite_existing_paid_metadata(
        self,
        mock_query,
    ):
        payment = self.prepare_paid_payment(receipt="EXISTING123")
        payment.transaction_date = timezone.now()
        payment.save(update_fields=["transaction_date"])
        original_transaction_date = payment.transaction_date

        result = process_mpesa_callback(
            self.successful_callback_payload()
        )
        result.refresh_from_db()

        self.assertEqual(result.mpesa_receipt, "EXISTING123")
        self.assertEqual(
            result.transaction_date,
            original_transaction_date,
        )
        mock_query.assert_not_called()

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_late_callback_requires_matching_merchant_request_id(
        self,
        mock_query,
    ):
        self.prepare_paid_payment(clear_transaction_date=True)

        payload = self.successful_callback_payload()
        del payload["Body"]["stkCallback"]["MerchantRequestID"]

        result = process_mpesa_callback(payload)
        result.refresh_from_db()

        self.assertIsNone(result.mpesa_receipt)
        self.assertIsNone(result.transaction_date)

        payload = self.successful_callback_payload()
        payload["Body"]["stkCallback"]["MerchantRequestID"] = (
            "wrong-merchant"
        )

        result = process_mpesa_callback(payload)
        result.refresh_from_db()

        self.assertIsNone(result.mpesa_receipt)
        self.assertIsNone(result.transaction_date)
        mock_query.assert_not_called()

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_late_callback_checkout_mismatch_cannot_enrich_paid_payment(
        self,
        mock_query,
    ):
        self.prepare_paid_payment(clear_transaction_date=True)
        payload = self.successful_callback_payload()
        payload["Body"]["stkCallback"]["CheckoutRequestID"] = (
            "other-checkout-123"
        )

        with self.assertRaisesMessage(
            ValueError,
            "Payment matching the callback was not found.",
        ):
            process_mpesa_callback(payload)

        self.payment.refresh_from_db()
        self.assertIsNone(self.payment.mpesa_receipt)
        self.assertIsNone(self.payment.transaction_date)
        mock_query.assert_not_called()

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_paid_metadata_enrichment_rejects_unsuccessful_query_results(
        self,
        mock_query,
    ):
        self.prepare_paid_payment(clear_transaction_date=True)

        cases = {
            "request_not_accepted": self.successful_query_response(
                ResponseCode="1"
            ),
            "checkout_mismatch": self.successful_query_response(
                CheckoutRequestID="other-checkout"
            ),
            "merchant_mismatch": self.successful_query_response(
                MerchantRequestID="other-merchant"
            ),
            "cancelled_result": self.successful_query_response(
                ResultCode="1032",
                ResultDesc="Request cancelled by user.",
            ),
            "inconclusive_result": self.successful_query_response(
                ResultCode="pending"
            ),
            "non_dict_response": ["not-a-dict"],
        }

        for name, response in cases.items():
            with self.subTest(name=name):
                mock_query.reset_mock()
                self.reset_verification_schedule()
                mock_query.return_value = response

                result = process_mpesa_callback(
                    self.successful_callback_payload()
                )
                result.refresh_from_db()

                self.assertEqual(result.status, "paid")
                self.assertIsNone(result.mpesa_receipt)
                self.assertIsNone(result.transaction_date)
                mock_query.assert_called_once_with(
                    "callback-checkout-123"
                )

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_paid_metadata_enrichment_handles_query_failures(
        self,
        mock_query,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaProviderError,
        )

        self.prepare_paid_payment(clear_transaction_date=True)

        cases = {
            "connection_error": requests.ConnectionError("boom"),
            "rate_limited": MpesaProviderError(
                "rate limited",
                operation="stk_query",
                status_code=429,
                retry_after="1",
                request_id="req-429",
            ),
            "forbidden": MpesaProviderError(
                "forbidden",
                operation="stk_query",
                status_code=403,
                request_id="req-403",
            ),
            "invalid_json": ValueError(
                "M-Pesa STK Push Query returned invalid JSON."
            ),
        }

        for name, exc in cases.items():
            with self.subTest(name=name):
                mock_query.reset_mock()
                self.reset_verification_schedule()
                mock_query.side_effect = exc

                result = process_mpesa_callback(
                    self.successful_callback_payload()
                )
                result.refresh_from_db()

                self.assertEqual(result.status, "paid")
                self.assertIsNone(result.mpesa_receipt)
                self.assertIsNone(result.transaction_date)
                mock_query.assert_called_once_with(
                    "callback-checkout-123"
                )

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_duplicate_late_callbacks_are_idempotent(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()
        self.prepare_paid_payment()

        first = process_mpesa_callback(
            self.successful_callback_payload()
        )
        first.refresh_from_db()
        original_transaction_date = first.transaction_date

        mock_query.reset_mock()

        second = process_mpesa_callback(
            self.successful_callback_payload()
        )
        second.refresh_from_db()

        self.assertEqual(second.mpesa_receipt, "ABC123XYZ")
        self.assertEqual(
            second.transaction_date,
            original_transaction_date,
        )
        mock_query.assert_not_called()

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_late_callback_duplicate_receipt_does_not_break_paid_payment(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()

        other_payment = create_payment(
            booking=self.booking,
            phone_number="254700000000",
        )
        mark_payment_processing(
            other_payment,
            checkout_request_id="other-checkout-123",
        )
        mark_payment_paid(
            other_payment,
            mpesa_receipt="ABC123XYZ",
        )

        self.prepare_paid_payment(clear_transaction_date=True)

        result = process_mpesa_callback(
            self.successful_callback_payload()
        )
        result.refresh_from_db()
        other_payment.refresh_from_db()

        self.assertEqual(result.status, "paid")
        self.assertIsNone(result.mpesa_receipt)
        self.assertEqual(
            timezone.localtime(
                result.transaction_date,
                ZoneInfo("Africa/Nairobi"),
            ).strftime("%Y%m%d%H%M%S"),
            "20300115101530",
        )
        self.assertEqual(other_payment.mpesa_receipt, "ABC123XYZ")
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_older_paid_callback_enrichment_does_not_change_newer_payment(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()
        self.prepare_paid_payment()

        newer_payment = create_payment(
            booking=self.booking,
            phone_number="254712345678",
        )
        mark_payment_processing(
            newer_payment,
            checkout_request_id="newer-checkout-123",
            merchant_request_id="newer-merchant-123",
        )

        result = process_mpesa_callback(
            self.successful_callback_payload()
        )
        result.refresh_from_db()
        newer_payment.refresh_from_db()

        self.assertEqual(result.mpesa_receipt, "ABC123XYZ")
        self.assertEqual(newer_payment.status, "processing")
        self.assertIsNone(newer_payment.mpesa_receipt)
        self.assertEqual(
            newer_payment.checkout_request_id,
            "newer-checkout-123",
        )
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_paid_callback_metadata_is_persisted_verified_and_applied(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()
        self.prepare_paid_payment(clear_transaction_date=True)

        payment = process_mpesa_callback(
            self.successful_callback_payload()
        )
        payment.refresh_from_db()
        metadata = MpesaCallbackMetadata.objects.get(
            payment=self.payment
        )

        self.assertEqual(payment.status, "paid")
        self.assertEqual(payment.mpesa_receipt, "ABC123XYZ")
        self.assertEqual(
            timezone.localtime(
                payment.transaction_date,
                ZoneInfo("Africa/Nairobi"),
            ).strftime("%Y%m%d%H%M%S"),
            "20300115101530",
        )
        self.assertEqual(metadata.status, "applied")
        self.assertEqual(metadata.attempt_count, 1)
        self.assertIsNotNone(metadata.verified_at)
        self.assertEqual(metadata.last_error, "")
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_paid_callback_provider_http_errors_keep_metadata_pending(
        self,
        mock_query,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaProviderError,
        )

        self.prepare_paid_payment(clear_transaction_date=True)

        for status_code, error_code in (
            (429, "http_429"),
            (403, "http_403"),
        ):
            with self.subTest(status_code=status_code):
                mock_query.reset_mock()
                self.reset_verification_schedule()
                mock_query.side_effect = MpesaProviderError(
                    "provider error",
                    operation="stk_query",
                    status_code=status_code,
                    request_id=f"req-{status_code}",
                )

                payment = process_mpesa_callback(
                    self.successful_callback_payload()
                )
                payment.refresh_from_db()
                metadata = MpesaCallbackMetadata.objects.get(
                    payment=self.payment
                )

                self.assertEqual(payment.status, "paid")
                self.assertIsNone(payment.mpesa_receipt)
                self.assertIsNone(payment.transaction_date)
                self.assertEqual(metadata.status, "pending")
                self.assertEqual(metadata.last_error, error_code)
                self.assertIsNotNone(metadata.last_attempt_at)
                self.assertIsNone(metadata.verified_at)
                mock_query.assert_called_once_with(
                    "callback-checkout-123"
                )

        mock_query.side_effect = None

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_paid_callback_inconclusive_query_keeps_metadata_pending(
        self,
        mock_query,
    ):
        self.prepare_paid_payment(clear_transaction_date=True)

        cases = {
            "connection_error": {
                "side_effect": requests.ConnectionError("boom"),
                "error_code": "request_error",
            },
            "invalid_json": {
                "side_effect": ValueError(
                    "M-Pesa STK Push Query returned invalid JSON."
                ),
                "error_code": "invalid_response",
            },
            "non_dict_response": {
                "return_value": ["not", "a", "dict"],
                "error_code": "invalid_response",
            },
        }

        for name, config in cases.items():
            with self.subTest(name=name):
                mock_query.reset_mock()
                self.reset_verification_schedule()
                mock_query.side_effect = config.get("side_effect")
                mock_query.return_value = config.get("return_value")

                payment = process_mpesa_callback(
                    self.successful_callback_payload()
                )
                payment.refresh_from_db()
                metadata = MpesaCallbackMetadata.objects.get(
                    payment=self.payment
                )

                self.assertEqual(payment.status, "paid")
                self.assertIsNone(payment.mpesa_receipt)
                self.assertIsNone(payment.transaction_date)
                self.assertEqual(metadata.status, "pending")
                self.assertEqual(
                    metadata.last_error,
                    config["error_code"],
                )
                mock_query.assert_called_once_with(
                    "callback-checkout-123"
                )

        mock_query.side_effect = None

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_pending_callback_metadata_is_applied_by_later_reconciliation(
        self,
        mock_query,
    ):
        from apps_core.payments.services import (
            reconcile_callback_metadata,
        )

        mock_query.side_effect = requests.ConnectionError("boom")
        self.prepare_paid_payment(clear_transaction_date=True)

        process_mpesa_callback(self.successful_callback_payload())
        metadata = MpesaCallbackMetadata.objects.get(
            payment=self.payment
        )
        self.assertEqual(metadata.status, "pending")

        mock_query.side_effect = None
        mock_query.return_value = self.successful_query_response()
        self.reset_verification_schedule()

        result = reconcile_callback_metadata(metadata)
        self.payment.refresh_from_db()

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.payment.status, "paid")
        self.assertEqual(self.payment.mpesa_receipt, "ABC123XYZ")
        self.assertEqual(
            timezone.localtime(
                self.payment.transaction_date,
                ZoneInfo("Africa/Nairobi"),
            ).strftime("%Y%m%d%H%M%S"),
            "20300115101530",
        )
        self.assertEqual(mock_query.call_count, 2)

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_duplicate_paid_callbacks_do_not_duplicate_pending_metadata(
        self,
        mock_query,
    ):
        mock_query.side_effect = requests.ConnectionError("boom")
        self.prepare_paid_payment(clear_transaction_date=True)

        process_mpesa_callback(self.successful_callback_payload())
        process_mpesa_callback(self.successful_callback_payload())

        metadata = MpesaCallbackMetadata.objects.get(
            payment=self.payment
        )
        self.assertEqual(
            MpesaCallbackMetadata.objects.filter(
                payment=self.payment
            ).count(),
            1,
        )
        self.assertEqual(metadata.status, "pending")
        self.assertEqual(metadata.attempt_count, 1)
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_nonzero_callback_result_code_does_not_create_metadata(
        self,
        mock_query,
    ):
        self.prepare_paid_payment(clear_transaction_date=True)
        payload = self.successful_callback_payload()
        payload["Body"]["stkCallback"]["ResultCode"] = 1032

        payment = process_mpesa_callback(payload)
        payment.refresh_from_db()

        self.assertEqual(payment.status, "paid")
        self.assertIsNone(payment.mpesa_receipt)
        self.assertIsNone(payment.transaction_date)
        self.assertFalse(
            MpesaCallbackMetadata.objects.filter(
                payment=self.payment
            ).exists()
        )
        mock_query.assert_not_called()

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_paid_callback_requires_matching_merchant_for_metadata(
        self,
        mock_query,
    ):
        self.prepare_paid_payment(clear_transaction_date=True)

        payload = self.successful_callback_payload()
        del payload["Body"]["stkCallback"]["MerchantRequestID"]
        process_mpesa_callback(payload)

        payload = self.successful_callback_payload()
        payload["Body"]["stkCallback"]["MerchantRequestID"] = (
            "wrong-merchant"
        )
        process_mpesa_callback(payload)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "paid")
        self.assertIsNone(self.payment.mpesa_receipt)
        self.assertIsNone(self.payment.transaction_date)
        self.assertFalse(
            MpesaCallbackMetadata.objects.filter(
                payment=self.payment
            ).exists()
        )
        mock_query.assert_not_called()

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_pending_metadata_rejects_unsuccessful_query_results(
        self,
        mock_query,
    ):
        from apps_core.payments.services import (
            reconcile_callback_metadata,
        )

        mock_query.side_effect = requests.ConnectionError("boom")
        self.prepare_paid_payment(clear_transaction_date=True)
        process_mpesa_callback(self.successful_callback_payload())
        metadata = MpesaCallbackMetadata.objects.get(
            payment=self.payment
        )

        cases = {
            "checkout_mismatch": (
                self.successful_query_response(
                    CheckoutRequestID="other-checkout"
                ),
                "checkout_mismatch",
            ),
            "merchant_mismatch": (
                self.successful_query_response(
                    MerchantRequestID="other-merchant"
                ),
                "merchant_mismatch",
            ),
            "result_not_success": (
                self.successful_query_response(
                    ResultCode="1032",
                    ResultDesc="Request cancelled by user.",
                ),
                "result_not_success",
            ),
            "request_not_accepted": (
                self.successful_query_response(ResponseCode="1"),
                "request_not_accepted",
            ),
        }

        for name, (response, error_code) in cases.items():
            with self.subTest(name=name):
                mock_query.reset_mock()
                self.reset_verification_schedule()
                mock_query.side_effect = None
                mock_query.return_value = response

                result = reconcile_callback_metadata(metadata)
                self.payment.refresh_from_db()

                self.assertEqual(result.status, "pending")
                self.assertEqual(result.last_error, error_code)
                self.assertEqual(self.payment.status, "paid")
                self.assertIsNone(self.payment.mpesa_receipt)
                self.assertIsNone(self.payment.transaction_date)
                mock_query.assert_called_once_with(
                    "callback-checkout-123"
                )

        mock_query.side_effect = None

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_callback_replay_does_not_overwrite_existing_paid_metadata(
        self,
        mock_query,
    ):
        payment = self.prepare_paid_payment(receipt="EXISTING123")
        payment.transaction_date = timezone.now()
        payment.save(update_fields=["transaction_date"])
        original_transaction_date = payment.transaction_date

        result = process_mpesa_callback(
            self.successful_callback_payload()
        )
        result.refresh_from_db()

        self.assertEqual(result.mpesa_receipt, "EXISTING123")
        self.assertEqual(
            result.transaction_date,
            original_transaction_date,
        )
        self.assertFalse(
            MpesaCallbackMetadata.objects.filter(
                payment=self.payment
            ).exists()
        )
        mock_query.assert_not_called()

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_pending_metadata_does_not_overwrite_fields_filled_later(
        self,
        mock_query,
    ):
        from apps_core.payments.services import (
            reconcile_callback_metadata,
        )

        mock_query.side_effect = requests.ConnectionError("boom")
        self.prepare_paid_payment(clear_transaction_date=True)
        process_mpesa_callback(self.successful_callback_payload())
        metadata = MpesaCallbackMetadata.objects.get(
            payment=self.payment
        )

        filled_date = timezone.now()
        self.payment.mpesa_receipt = "OTHER123"
        self.payment.transaction_date = filled_date
        self.payment.save(
            update_fields=["mpesa_receipt", "transaction_date"]
        )

        mock_query.side_effect = None
        mock_query.return_value = self.successful_query_response()
        mock_query.reset_mock()

        result = reconcile_callback_metadata(metadata)
        self.payment.refresh_from_db()

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.payment.mpesa_receipt, "OTHER123")
        self.assertEqual(self.payment.transaction_date, filled_date)
        mock_query.assert_not_called()

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_processing_callback_metadata_survives_rate_limit_and_reconciles(
        self,
        mock_query,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaProviderError,
        )
        from apps_core.payments.services import (
            reconcile_callback_metadata,
        )

        mock_query.side_effect = MpesaProviderError(
            "rate limited",
            operation="stk_query",
            status_code=429,
            request_id="req-429",
        )

        payment = process_mpesa_callback(
            self.successful_callback_payload()
        )
        payment.refresh_from_db()
        metadata = MpesaCallbackMetadata.objects.get(
            payment=self.payment
        )

        self.assertEqual(payment.status, "processing")
        self.assertIsNone(payment.mpesa_receipt)
        self.assertIsNone(payment.transaction_date)
        self.assertEqual(metadata.status, "pending")
        self.assertEqual(metadata.mpesa_receipt, "ABC123XYZ")
        self.assertIsNotNone(metadata.transaction_date)
        self.assertEqual(metadata.last_error, "verification_unavailable")
        self.assertEqual(metadata.attempt_count, 1)
        mock_query.assert_called_once_with("callback-checkout-123")

        mock_query.side_effect = None
        mock_query.return_value = self.successful_query_response()
        self.reset_verification_schedule()

        result = reconcile_callback_metadata(metadata)
        self.payment.refresh_from_db()

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.payment.status, "paid")
        self.assertEqual(self.payment.mpesa_receipt, "ABC123XYZ")
        self.assertEqual(
            timezone.localtime(
                self.payment.transaction_date,
                ZoneInfo("Africa/Nairobi"),
            ).strftime("%Y%m%d%H%M%S"),
            "20300115101530",
        )
        self.assertEqual(mock_query.call_count, 2)

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_unknown_callback_metadata_survives_request_error_and_reconciles(
        self,
        mock_query,
    ):
        from apps_core.payments.services import (
            reconcile_callback_metadata,
        )

        self.payment.status = "unknown"
        self.payment.save(update_fields=["status"])
        mock_query.side_effect = requests.ConnectionError("boom")

        payment = process_mpesa_callback(
            self.successful_callback_payload()
        )
        payment.refresh_from_db()
        metadata = MpesaCallbackMetadata.objects.get(
            payment=self.payment
        )

        self.assertEqual(payment.status, "unknown")
        self.assertIsNone(payment.mpesa_receipt)
        self.assertIsNone(payment.transaction_date)
        self.assertEqual(metadata.status, "pending")
        self.assertEqual(metadata.mpesa_receipt, "ABC123XYZ")
        self.assertEqual(metadata.last_error, "verification_unavailable")
        self.assertEqual(metadata.attempt_count, 1)
        mock_query.assert_called_once_with("callback-checkout-123")

        mock_query.side_effect = None
        mock_query.return_value = self.successful_query_response()
        self.reset_verification_schedule()

        result = reconcile_callback_metadata(metadata)
        self.payment.refresh_from_db()

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.payment.status, "paid")
        self.assertEqual(self.payment.mpesa_receipt, "ABC123XYZ")
        self.assertEqual(
            timezone.localtime(
                self.payment.transaction_date,
                ZoneInfo("Africa/Nairobi"),
            ).strftime("%Y%m%d%H%M%S"),
            "20300115101530",
        )
        self.assertEqual(mock_query.call_count, 2)

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_processing_callback_mismatched_merchant_does_not_capture_metadata(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response()
        payload = self.successful_callback_payload()
        payload["Body"]["stkCallback"]["MerchantRequestID"] = (
            "wrong-merchant"
        )

        payment = process_mpesa_callback(payload)
        payment.refresh_from_db()

        self.assertEqual(payment.status, "paid")
        self.assertIsNone(payment.mpesa_receipt)
        self.assertFalse(
            MpesaCallbackMetadata.objects.filter(
                payment=self.payment
            ).exists()
        )
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_processing_callback_nonzero_result_does_not_capture_metadata(
        self,
        mock_query,
    ):
        mock_query.return_value = self.successful_query_response(
            ResultCode="1032",
            ResultDesc="Request cancelled by user.",
        )

        payment = process_mpesa_callback(
            self.failed_callback_payload()
        )
        payment.refresh_from_db()

        self.assertEqual(payment.status, "failed")
        self.assertIsNone(payment.mpesa_receipt)
        self.assertFalse(
            MpesaCallbackMetadata.objects.filter(
                payment=self.payment
            ).exists()
        )
        mock_query.assert_called_once_with("callback-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_conflicting_callbacks_do_not_overwrite_saved_candidate(
        self,
        mock_query,
    ):
        mock_query.side_effect = requests.ConnectionError("boom")

        process_mpesa_callback(self.successful_callback_payload())
        self.reset_verification_schedule()

        conflicting = self.successful_callback_payload()
        for item in conflicting["Body"]["stkCallback"][
            "CallbackMetadata"
        ]["Item"]:
            if item.get("Name") == "MpesaReceiptNumber":
                item["Value"] = "DIFFERENT999"
            elif item.get("Name") == "TransactionDate":
                item["Value"] = 20300115111530

        process_mpesa_callback(conflicting)

        metadata = MpesaCallbackMetadata.objects.get(
            payment=self.payment
        )
        self.payment.refresh_from_db()

        self.assertEqual(
            MpesaCallbackMetadata.objects.filter(
                payment=self.payment
            ).count(),
            1,
        )
        self.assertEqual(metadata.status, "pending")
        self.assertEqual(metadata.mpesa_receipt, "ABC123XYZ")
        self.assertEqual(
            timezone.localtime(
                metadata.transaction_date,
                ZoneInfo("Africa/Nairobi"),
            ).strftime("%Y%m%d%H%M%S"),
            "20300115101530",
        )
        self.assertEqual(metadata.attempt_count, 2)
        self.assertEqual(self.payment.status, "processing")
        self.assertIsNone(self.payment.mpesa_receipt)
        self.assertEqual(mock_query.call_count, 2)

    def test_callback_requires_body(self):
        with self.assertRaisesMessage(
            ValueError,
            "M-Pesa callback Body is missing.",
        ):
            process_mpesa_callback({})

    def test_callback_requires_stk_callback(self):
        payload = {
            "Body": {}
        }

        with self.assertRaisesMessage(
            ValueError,
            "M-Pesa stkCallback is missing.",
        ):
            process_mpesa_callback(
                payload
            )


class PaymentVerificationSchedulingTests(TestCase):
    def setUp(self):
        customer = Customer.objects.create(
            full_name="Scheduling Customer",
            email="scheduling@example.com",
            phone="254712345678",
        )
        service = Service.objects.create(
            name="Scheduling Cleaning",
            slug="scheduling-cleaning",
            description="Scheduling test service.",
            base_price=Decimal("5000.00"),
            duration_hours=2,
            is_active=True,
        )
        booking = Booking.objects.create(
            customer=customer,
            service=service,
            booking_date="2030-01-15",
            booking_time="10:00",
            address="Scheduling Address",
            city="Nairobi",
            total_price=Decimal("5000.00"),
            status="pending",
        )
        self.payment = create_payment(
            booking=booking,
            phone_number="254712345678",
        )
        mark_payment_processing(
            self.payment,
            checkout_request_id="schedule-checkout-123",
            merchant_request_id="schedule-merchant-123",
        )
        self.payment.refresh_from_db()

    def successful_query_response(self, **overrides):
        response = {
            "ResponseCode": "0",
            "CheckoutRequestID": "schedule-checkout-123",
            "MerchantRequestID": "schedule-merchant-123",
            "ResultCode": "0",
            "ResultDesc": "Transaction processed successfully.",
        }
        response.update(overrides)
        return response

    def make_eligible(self):
        Payment.objects.filter(pk=self.payment.pk).update(
            next_verification_at=timezone.now() - timedelta(seconds=1),
            verification_lease_id=None,
            verification_lease_expires_at=None,
        )
        self.payment.refresh_from_db()

    def assert_next_between(self, minimum_seconds, maximum_seconds):
        self.payment.refresh_from_db()
        self.assertIsNotNone(self.payment.next_verification_at)
        delta = (
            self.payment.next_verification_at - timezone.now()
        ).total_seconds()
        self.assertGreaterEqual(delta, minimum_seconds)
        self.assertLessEqual(delta, maximum_seconds)

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_cooldown_prevents_another_provider_query(self, mock_query):
        mock_query.return_value = self.successful_query_response(
            ResultCode="9999",
            ResultDesc="Still processing.",
        )

        first = verify_payment(self.payment)
        second = verify_payment(self.payment)
        self.payment.refresh_from_db()

        self.assertEqual(first.status, "processing")
        self.assertEqual(second.status, "processing")
        self.assertIs(second._verification_attempted, False)
        self.assertEqual(mock_query.call_count, 1)
        self.assertEqual(self.payment.verification_attempt_count, 1)
        self.assertIsNotNone(self.payment.last_verification_at)
        self.assertIsNotNone(self.payment.next_verification_at)
        self.assertIsNone(self.payment.verification_lease_id)
        self.assertIsNone(self.payment.verification_lease_expires_at)

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_retry_after_seconds_are_respected(self, mock_query):
        from apps_core.payments.integrations.mpesa import (
            MpesaProviderError,
        )

        mock_query.side_effect = MpesaProviderError(
            "rate limited",
            operation="stk_query",
            status_code=429,
            retry_after="45",
            request_id="req-429",
        )

        payment = verify_payment(self.payment)
        self.payment.refresh_from_db()

        self.assertEqual(payment.status, "processing")
        self.assertEqual(self.payment.verification_attempt_count, 1)
        self.assert_next_between(40, 50)

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_retry_after_http_date_is_respected(self, mock_query):
        from apps_core.payments.integrations.mpesa import (
            MpesaProviderError,
        )

        retry_at = timezone.now() + timedelta(seconds=30)
        mock_query.side_effect = MpesaProviderError(
            "rate limited",
            operation="stk_query",
            status_code=429,
            retry_after=format_datetime(retry_at, usegmt=True),
        )

        verify_payment(self.payment)

        self.assert_next_between(25, 35)

    @patch("apps_core.payments.services.random.uniform")
    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_missing_retry_after_uses_bounded_exponential_backoff(
        self,
        mock_query,
        mock_uniform,
    ):
        mock_uniform.return_value = 0
        mock_query.return_value = self.successful_query_response(
            ResultCode="9999",
            ResultDesc="Still processing.",
        )

        verify_payment(self.payment)
        self.assert_next_between(25, 35)

        self.make_eligible()
        verify_payment(self.payment)

        self.payment.refresh_from_db()
        self.assertEqual(mock_query.call_count, 2)
        self.assertEqual(self.payment.verification_attempt_count, 2)
        self.assert_next_between(55, 65)

    @patch("apps_core.payments.services.random.uniform")
    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_http_500_schedules_later_attempt_without_failing(
        self,
        mock_query,
        mock_uniform,
    ):
        from apps_core.payments.integrations.mpesa import (
            MpesaProviderError,
        )

        mock_uniform.return_value = 0
        mock_query.side_effect = MpesaProviderError(
            "provider error",
            operation="stk_query",
            status_code=500,
            error_code="500.001.1001",
            error_message="System busy.",
        )

        payment = verify_payment(self.payment)
        self.payment.refresh_from_db()

        self.assertEqual(payment.status, "processing")
        self.assertEqual(self.payment.verification_attempt_count, 1)
        self.assert_next_between(25, 35)

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_active_lease_blocks_until_expiry_then_recovers(
        self,
        mock_query,
    ):
        Payment.objects.filter(pk=self.payment.pk).update(
            verification_lease_id=uuid.uuid4(),
            verification_lease_expires_at=(
                timezone.now() + timedelta(seconds=60)
            ),
            next_verification_at=None,
        )
        self.payment.refresh_from_db()

        blocked = verify_payment(self.payment)
        self.payment.refresh_from_db()

        self.assertEqual(blocked.status, "processing")
        mock_query.assert_not_called()
        self.assertEqual(self.payment.verification_attempt_count, 0)
        self.assertIsNone(self.payment.last_verification_at)

        Payment.objects.filter(pk=self.payment.pk).update(
            verification_lease_expires_at=(
                timezone.now() - timedelta(seconds=1)
            ),
        )
        mock_query.return_value = self.successful_query_response()

        recovered = verify_payment(self.payment)

        self.assertEqual(recovered.status, "paid")
        mock_query.assert_called_once_with("schedule-checkout-123")
        self.payment.refresh_from_db()
        self.assertIsNone(self.payment.verification_lease_id)
        self.assertIsNone(self.payment.next_verification_at)

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_pending_metadata_reconcile_uses_same_verification_guard(
        self,
        mock_query,
    ):
        from apps_core.payments.services import (
            reconcile_callback_metadata,
        )

        metadata = MpesaCallbackMetadata.objects.create(
            payment=self.payment,
            mpesa_receipt="SCHED123",
            transaction_date=timezone.now(),
            status="pending",
        )
        Payment.objects.filter(pk=self.payment.pk).update(
            next_verification_at=timezone.now() + timedelta(seconds=60),
        )

        reconcile_callback_metadata(metadata)
        metadata.refresh_from_db()

        mock_query.assert_not_called()
        self.assertEqual(metadata.status, "pending")
        self.assertEqual(metadata.attempt_count, 0)

        self.make_eligible()
        mock_query.return_value = self.successful_query_response()

        result = reconcile_callback_metadata(metadata)
        self.payment.refresh_from_db()

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.payment.status, "paid")
        self.assertEqual(self.payment.mpesa_receipt, "SCHED123")
        mock_query.assert_called_once_with("schedule-checkout-123")

    def test_http_error_diagnostics_are_safe_and_structured(self):
        from apps_core.payments.integrations.mpesa import (
            MpesaProviderError,
            _raise_for_status,
        )

        response = Mock(
            status_code=500,
            headers={
                "Retry-After": "7",
                "X-Request-Id": "req-header",
            },
        )
        response.json.return_value = {
            "errorCode": "500.001.1001",
            "errorMessage": "System busy for 254712345678",
            "requestId": "req-body",
        }

        with self.assertRaises(MpesaProviderError) as raised:
            _raise_for_status(response, "stk_query")

        exc = raised.exception
        self.assertEqual(exc.status_code, 500)
        self.assertEqual(exc.retry_after, "7")
        self.assertEqual(exc.request_id, "req-header")
        self.assertEqual(exc.error_code, "500.001.1001")
        self.assertEqual(exc.error_message, "System busy for [redacted]")
        self.assertNotIn("254712345678", str(exc))
        self.assertNotIn("254712345678", exc.error_message)


class PaymentVerificationTransactionTests(TransactionTestCase):
    def setUp(self):
        customer = Customer.objects.create(
            full_name="Verification Customer",
            email="verification@example.com",
            phone="254712345678",
        )
        service = Service.objects.create(
            name="Verification Cleaning",
            slug="verification-cleaning",
            description="Verification test service.",
            base_price=Decimal("5000.00"),
            duration_hours=2,
            is_active=True,
        )
        booking = Booking.objects.create(
            customer=customer,
            service=service,
            booking_date="2030-01-15",
            booking_time="10:00",
            address="Verification Address",
            city="Nairobi",
            total_price=Decimal("5000.00"),
            status="pending",
        )
        self.payment = create_payment(
            booking=booking,
            phone_number="254712345678",
        )
        mark_payment_processing(
            self.payment,
            checkout_request_id="transaction-checkout-123",
            merchant_request_id="transaction-merchant-123",
        )

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_query_runs_outside_transaction_then_payment_is_locked(
        self,
        mock_query,
    ):
        def query_outside_transaction(checkout_request_id):
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(
                checkout_request_id,
                "transaction-checkout-123",
            )
            return {
                "ResponseCode": "0",
                "CheckoutRequestID": "transaction-checkout-123",
                "MerchantRequestID": "transaction-merchant-123",
                "ResultCode": "0",
            }

        mock_query.side_effect = query_outside_transaction

        with patch.object(
            Payment.objects,
            "select_for_update",
            wraps=Payment.objects.select_for_update,
        ) as locked_query:
            payment = verify_payment(self.payment)

        self.assertTrue(locked_query.called)
        self.assertEqual(payment.status, "paid")


class MpesaCallbackViewTests(TestCase):

    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="Callback Customer",
            email="callback@example.com",
            phone="0712345678",
        )

        self.service = Service.objects.create(
            name="Callback Cleaning",
            slug="callback-cleaning",
            description="Callback test service.",
            base_price=Decimal("5000.00"),
            duration_hours=2,
            is_active=True,
        )

        self.booking = Booking.objects.create(
            customer=self.customer,
            contact_name=self.customer.full_name,
            contact_email=self.customer.email,
            contact_phone=self.customer.phone,
            service=self.service,
            booking_date="2030-01-15",
            booking_time="10:00",
            address="Callback Test Address",
            city="Nairobi",
            property_size=100,
            rooms=3,
            total_price=Decimal("5000.00"),
            status="pending",
        )

        self.payment = Payment.objects.create(
            booking=self.booking,
            amount=Decimal("5000.00"),
            method="mpesa",
            status="processing",
            phone_number="0712345678",
            checkout_request_id="view-checkout-123",
            merchant_request_id="view-merchant-123",
        )

        self.url = reverse(
            "payments:mpesa_callback"
        )

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_successful_callback_endpoint_verifies_before_marking_paid(
        self,
        mock_query,
    ):
        mock_query.return_value = {
            "ResponseCode": "0",
            "CheckoutRequestID": "view-checkout-123",
            "MerchantRequestID": "view-merchant-123",
            "ResultCode": "0",
            "ResultDesc": "Transaction processed.",
        }
        payload = {
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": "view-merchant-123",
                    "CheckoutRequestID": "view-checkout-123",
                    "ResultCode": 0,
                    "ResultDesc": (
                        "The service request is "
                        "processed successfully."
                    ),
                    "CallbackMetadata": {
                        "Item": [
                            {
                                "Name": "Amount",
                                "Value": 5000,
                            },
                            {
                                "Name": "MpesaReceiptNumber",
                                "Value": "VIEWABC123",
                            },
                            {
                                "Name": "TransactionDate",
                                "Value": 20300115101530,
                            },
                            {
                                "Name": "PhoneNumber",
                                "Value": 254712345678,
                            },
                        ]
                    },
                }
            }
        }

        response = self.client.post(
            self.url,
            data=payload,
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.json()["ResultCode"],
            0,
        )
        self.assertEqual(
            response.json()["ResultDesc"],
            "Callback received for verification.",
        )
        self.assertNotIn(
            "completed",
            response.json()["ResultDesc"].lower(),
        )

        self.payment.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            "paid",
        )

        self.assertEqual(self.payment.mpesa_receipt, "VIEWABC123")
        self.assertIsNotNone(self.payment.transaction_date)
        self.assertEqual(
            timezone.localtime(
                self.payment.transaction_date,
                ZoneInfo("Africa/Nairobi"),
            ).strftime("%Y%m%d%H%M%S"),
            "20300115101530",
        )
        mock_query.assert_called_once_with("view-checkout-123")

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_failed_callback_endpoint_uses_verified_query_result(
        self,
        mock_query,
    ):
        mock_query.return_value = {
            "ResponseCode": "0",
            "CheckoutRequestID": "view-checkout-123",
            "MerchantRequestID": "view-merchant-123",
            "ResultCode": "1032",
            "ResultDesc": "Request cancelled by user.",
        }
        payload = {
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": "view-merchant-123",
                    "CheckoutRequestID": "view-checkout-123",
                    "ResultCode": 1032,
                    "ResultDesc": (
                        "Request cancelled by user."
                    ),
                }
            }
        }

        response = self.client.post(
            self.url,
            data=payload,
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.json()["ResultCode"],
            0,
        )

        self.payment.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            "failed",
        )

        self.assertEqual(
            self.payment.result_code,
            "1032",
        )

    def test_callback_rejects_invalid_json(self):
        response = self.client.post(
            self.url,
            data="not valid json",
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            400,
        )

        self.assertEqual(
            response.json()["ResultCode"],
            1,
        )




    def test_callback_rejects_get_request(self):
        response = self.client.get(
            self.url
        )

        self.assertEqual(
            response.status_code,
            405,
        )




class BookingPaymentViewTests(TestCase):
    def setUp(self):
        cache.clear()

        self.customer = Customer.objects.create(
            full_name="Booking Payment Customer",
            email="bookingpayment@example.com",
            phone="0712345678",
            address="Test Address",
            city="Nairobi",
        )

        self.service = Service.objects.create(
            name="Booking Payment Cleaning",
            slug="booking-payment-cleaning",
            description="Service for booking payment view tests.",
            base_price=Decimal("5000.00"),
            duration_hours=2,
            is_active=True,
        )

        self.booking = Booking.objects.create(
            customer=self.customer,
            contact_name=self.customer.full_name,
            contact_email=self.customer.email,
            contact_phone=self.customer.phone,
            service=self.service,
            booking_date="2030-01-15",
            booking_time="10:00",
            address="Test Address",
            city="Nairobi",
            property_size=100,
            rooms=3,
            total_price=Decimal("5000.00"),
            status="pending",
        )

        self.url = reverse(
            "payments:initiate_booking_payment",
            kwargs={"booking_id": self.booking.pk},
        )

        session = self.client.session
        session["booking_success_id"] = self.booking.pk
        session.save()

    @patch("apps_core.payments.views.initiate_payment")
    def test_successful_request_uses_saved_booking_total(
        self, mock_initiate
    ):
        response = self.client.post(
            self.url,
            {"amount": "1"},
        )

        self.assertEqual(response.status_code, 302)

        payment = Payment.objects.get(booking=self.booking)

        self.assertEqual(payment.amount, Decimal("5000.00"))
        self.assertEqual(payment.status, "pending")

        mock_initiate.assert_called_once_with(payment)

    @patch("apps_core.payments.views.initiate_payment")
    def test_booking_id_not_in_session_returns_404(
        self, mock_initiate
    ):
        session = self.client.session
        session["booking_success_id"] = self.booking.pk + 999
        session.save()

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 404)
        mock_initiate.assert_not_called()
        self.assertEqual(Payment.objects.count(), 0)

    @patch("apps_core.payments.views.initiate_payment")
    def test_cancelled_booking_cannot_initiate_payment(
        self, mock_initiate
    ):
        self.booking.status = "cancelled"
        self.booking.save(update_fields=["status"])

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        mock_initiate.assert_not_called()
        self.assertEqual(Payment.objects.count(), 0)

    @patch("apps_core.payments.views.initiate_payment")
    def test_locked_booking_status_is_authoritative_for_cancellation(
        self, mock_initiate
    ):
        stale_booking = Booking.objects.select_related("customer").get(
            pk=self.booking.pk
        )
        self.booking.status = "cancelled"
        self.booking.save(update_fields=["status"])

        with patch(
            "apps_core.payments.views.get_object_or_404",
            return_value=stale_booking,
        ):
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        mock_initiate.assert_not_called()
        self.assertEqual(Payment.objects.count(), 0)

    @patch("apps_core.payments.views.initiate_payment")
    def test_paid_payment_does_not_start_another_request(
        self, mock_initiate
    ):
        payment = create_payment(
            booking=self.booking,
            phone_number=self.customer.phone,
        )
        payment.status = "processing"
        payment.checkout_request_id = "test-checkout-paid"
        payment.save(
            update_fields=["status", "checkout_request_id"]
        )
        mark_payment_paid(
            payment,
            mpesa_receipt="TESTPAID123",
        )

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        mock_initiate.assert_not_called()
        self.assertEqual(Payment.objects.count(), 1)

    @patch("apps_core.payments.views.initiate_payment")
    def test_processing_payment_does_not_start_duplicate_request(
        self, mock_initiate
    ):
        payment = create_payment(
            booking=self.booking,
            phone_number=self.customer.phone,
        )
        mark_payment_processing(
            payment,
            checkout_request_id="test-checkout-processing",
        )

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        mock_initiate.assert_not_called()
        self.assertEqual(Payment.objects.count(), 1)

    @patch("apps_core.payments.views.initiate_payment")
    def test_pending_payment_is_reused(
        self, mock_initiate
    ):
        payment = create_payment(
            booking=self.booking,
            phone_number=self.customer.phone,
        )

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        mock_initiate.assert_called_once_with(payment)
        self.assertEqual(Payment.objects.count(), 1)

    @patch("apps_core.payments.views.initiate_payment")
    def test_failed_payment_history_is_preserved(
        self, mock_initiate
    ):
        failed_payment = create_payment(
            booking=self.booking,
            phone_number=self.customer.phone,
        )
        mark_payment_failed(
            failed_payment,
            result_code="1032",
            result_description="Request cancelled.",
        )

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Payment.objects.count(), 2)

        failed_payment.refresh_from_db()
        self.assertEqual(failed_payment.status, "failed")

        new_payment = Payment.objects.exclude(
            pk=failed_payment.pk
        ).get()

        self.assertEqual(new_payment.status, "pending")
        mock_initiate.assert_called_once_with(new_payment)

    @patch("apps_core.payments.views.initiate_payment")
    def test_failed_retry_creates_one_active_attempt(
        self, mock_initiate
    ):
        failed_payment = create_payment(
            booking=self.booking,
            phone_number=self.customer.phone,
        )
        mark_payment_failed(
            failed_payment,
            result_code="1019",
            result_description="Transaction Expired",
        )

        def mark_processing(payment):
            return mark_payment_processing(
                payment,
                checkout_request_id="retry-checkout-123",
                merchant_request_id="retry-merchant-123",
            )

        mock_initiate.side_effect = mark_processing

        first = self.client.post(self.url)
        second = self.client.post(self.url)

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            Payment.objects.filter(booking=self.booking).count(),
            2,
        )
        self.assertEqual(mock_initiate.call_count, 1)

        failed_payment.refresh_from_db()
        self.assertEqual(failed_payment.status, "failed")

        active_payment = Payment.objects.exclude(
            pk=failed_payment.pk
        ).get()
        self.assertEqual(active_payment.status, "processing")
        self.assertEqual(
            active_payment.checkout_request_id,
            "retry-checkout-123",
        )

    @patch("apps_core.payments.views.initiate_payment")
    def test_stk_push_error_preserves_payment_record(
        self, mock_initiate
    ):
        mock_initiate.side_effect = ValueError(
            "Unable to process request."
        )

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Payment.objects.count(), 1)

        payment = Payment.objects.get(booking=self.booking)
        self.assertEqual(payment.status, "pending")

    @patch("apps_core.payments.views.initiate_payment")
    def test_initiating_payment_does_not_start_duplicate_request(
        self, mock_initiate
    ):
        payment = create_payment(
            booking=self.booking,
            phone_number=self.customer.phone,
        )
        payment.status = "initiating"
        payment.save(update_fields=["status"])

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        mock_initiate.assert_not_called()
        self.assertEqual(Payment.objects.count(), 1)

    @patch("apps_core.payments.views.initiate_payment")
    def test_unknown_payment_outcome_does_not_start_duplicate_request(
        self, mock_initiate
    ):
        payment = create_payment(
            booking=self.booking,
            phone_number=self.customer.phone,
        )
        payment.status = "unknown"
        payment.save(update_fields=["status"])

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        mock_initiate.assert_not_called()
        self.assertEqual(Payment.objects.count(), 1)

    @patch("apps_core.payments.views.initiate_payment")
    def test_unexpected_payment_status_blocks_retry(
        self, mock_initiate
    ):
        payment = create_payment(
            booking=self.booking,
            phone_number=self.customer.phone,
        )
        payment.status = "unexpected"
        payment.save(update_fields=["status"])

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        mock_initiate.assert_not_called()
        self.assertEqual(Payment.objects.count(), 1)

    @patch("apps_core.payments.views.initiate_payment")
    def test_unexpected_initiation_exception_preserves_payment_record(
        self, mock_initiate
    ):
        mock_initiate.side_effect = RuntimeError("network outcome uncertain")

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Payment.objects.count(), 1)
        payment = Payment.objects.get(booking=self.booking)
        self.assertEqual(payment.status, "pending")

    def test_payment_initiation_rate_limits_include_ip_user_and_booking_scopes(
        self,
    ):
        scope_names = {
            scope.name
            for scope in payment_views.PAYMENT_INITIATION_RATE_LIMITS
        }

        self.assertIn("payment_initiation:ip", scope_names)
        self.assertIn("payment_initiation:user", scope_names)
        self.assertIn("payment_initiation:ip-booking_id", scope_names)

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.initiate_stk_push"
    )
    @patch("apps_core.payments.views.initiate_payment")
    def test_rate_limited_initiation_does_not_create_payment_or_call_provider(
        self, mock_initiate, mock_stk_push
    ):
        cache.clear()

        with ExitStack() as stack:
            for scope in payment_views.PAYMENT_INITIATION_RATE_LIMITS:
                stack.enter_context(patch.object(scope, "limit", 1))

            first = self.client.post(self.url)
            second = self.client.post(self.url)

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(
            Payment.objects.filter(booking=self.booking).count(),
            1,
        )
        self.assertEqual(mock_initiate.call_count, 1)
        mock_stk_push.assert_not_called()

    def test_initiation_endpoint_rejects_get_request(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)


class BookingPaymentStatusViewTests(TestCase):
    def setUp(self):
        cache.clear()

        self.customer = Customer.objects.create(
            full_name="Booking Status Customer",
            email="bookingstatus@example.com",
            phone="0712345678",
            address="Test Address",
            city="Nairobi",
        )

        self.service = Service.objects.create(
            name="Booking Status Cleaning",
            slug="booking-status-cleaning",
            description="Service for booking status view tests.",
            base_price=Decimal("5000.00"),
            duration_hours=2,
            is_active=True,
        )

        self.booking = Booking.objects.create(
            customer=self.customer,
            contact_name=self.customer.full_name,
            contact_email=self.customer.email,
            contact_phone=self.customer.phone,
            service=self.service,
            booking_date="2030-01-15",
            booking_time="10:00",
            address="Test Address",
            city="Nairobi",
            property_size=100,
            rooms=3,
            total_price=Decimal("5000.00"),
            status="pending",
        )

        self.payment = create_payment(
            booking=self.booking,
            phone_number=self.customer.phone,
        )
        mark_payment_processing(
            self.payment,
            checkout_request_id="status-checkout-123",
            merchant_request_id="status-merchant-123",
        )

        self.url = reverse(
            "payments:check_booking_payment_status",
            kwargs={"booking_id": self.booking.pk},
        )

        session = self.client.session
        session["booking_success_id"] = self.booking.pk
        session.save()

    @patch("apps_core.payments.views.verify_payment")
    def test_status_check_requires_session_booking(self, mock_verify):
        session = self.client.session
        session["booking_success_id"] = self.booking.pk + 999
        session.save()

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 404)
        mock_verify.assert_not_called()

    def test_status_check_rejects_get_request(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)

    @patch("apps_core.payments.views.verify_payment")
    def test_processing_payment_is_verified_without_new_payment(
        self, mock_verify
    ):
        def mark_paid(payment):
            return mark_payment_paid(
                payment,
                mpesa_receipt="STATUS123",
            )

        mock_verify.side_effect = mark_paid

        response = self.client.post(self.url, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Payment.objects.filter(booking=self.booking).count(),
            1,
        )
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "paid")
        self.assertEqual(self.payment.mpesa_receipt, "STATUS123")
        mock_verify.assert_called_once()
        self.assertEqual(
            mock_verify.call_args[0][0].pk,
            self.payment.pk,
        )
        self.assertContains(
            response,
            "Your M-Pesa payment has been confirmed.",
        )

    @patch(
        "apps_core.payments.integrations.mpesa."
        "MpesaIntegration.query_stk_push"
    )
    def test_status_check_applies_pending_callback_metadata(self, mock_query):
        provider_date = datetime(
            2030,
            1,
            15,
            10,
            15,
            30,
            tzinfo=ZoneInfo("Africa/Nairobi"),
        )
        MpesaCallbackMetadata.objects.create(
            payment=self.payment,
            mpesa_receipt="STATUSMETA123",
            transaction_date=provider_date,
            status="pending",
        )
        mock_query.return_value = {
            "ResponseCode": "0",
            "CheckoutRequestID": "status-checkout-123",
            "MerchantRequestID": "status-merchant-123",
            "ResultCode": "0",
            "ResultDesc": "Transaction processed successfully.",
        }

        response = self.client.post(self.url, follow=True)

        self.assertEqual(response.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "paid")
        self.assertEqual(self.payment.mpesa_receipt, "STATUSMETA123")
        self.assertEqual(self.payment.transaction_date, provider_date)
        mock_query.assert_called_once_with("status-checkout-123")
        metadata = MpesaCallbackMetadata.objects.get(payment=self.payment)
        self.assertEqual(metadata.status, "applied")
        self.assertContains(
            response,
            "Your M-Pesa payment has been confirmed.",
        )

    @patch("apps_core.payments.views.verify_payment")
    def test_processing_payment_still_processing_does_not_create_payment(
        self, mock_verify
    ):
        mock_verify.side_effect = lambda payment: payment

        response = self.client.post(self.url, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Payment.objects.filter(booking=self.booking).count(),
            1,
        )
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "processing")
        mock_verify.assert_called_once()
        self.assertContains(response, "still being processed")

    @patch("apps_core.payments.views.verify_payment")
    def test_failed_after_verification_does_not_initiate_payment(
        self, mock_verify
    ):
        def mark_failed(payment):
            return mark_payment_failed(
                payment,
                result_code="1032",
                result_description="Request cancelled by user.",
            )

        mock_verify.side_effect = mark_failed

        response = self.client.post(self.url, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Payment.objects.filter(booking=self.booking).count(),
            1,
        )
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "failed")
        mock_verify.assert_called_once()
        self.assertContains(
            response,
            "previous M-Pesa payment attempt was unsuccessful",
        )

    @patch("apps_core.payments.views.verify_payment")
    @patch("apps_core.payments.views.initiate_payment")
    def test_verified_cancellation_allows_a_new_payment_attempt(
        self, mock_initiate, mock_verify
    ):
        def mark_failed(payment):
            return mark_payment_failed(
                payment,
                result_code="1032",
                result_description="Request cancelled by user.",
            )

        mock_verify.side_effect = mark_failed

        status_response = self.client.post(self.url, follow=True)

        self.assertEqual(status_response.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "failed")

        initiate_url = reverse(
            "payments:initiate_booking_payment",
            kwargs={"booking_id": self.booking.pk},
        )
        initiate_response = self.client.post(initiate_url)

        self.assertEqual(initiate_response.status_code, 302)
        self.assertEqual(
            Payment.objects.filter(booking=self.booking).count(),
            2,
        )

        new_payment = Payment.objects.exclude(
            pk=self.payment.pk
        ).get()
        self.assertEqual(new_payment.status, "pending")
        mock_initiate.assert_called_once_with(new_payment)

    @patch("apps_core.payments.views.verify_payment")
    def test_unknown_payment_with_checkout_id_is_verified(
        self, mock_verify
    ):
        self.payment.status = "unknown"
        self.payment.save(update_fields=["status"])
        mock_verify.side_effect = lambda payment: payment

        response = self.client.post(self.url, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Payment.objects.filter(booking=self.booking).count(),
            1,
        )
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "unknown")
        mock_verify.assert_called_once()
        self.assertContains(
            response,
            "could not confirm the M-Pesa payment outcome",
        )

    @patch("apps_core.payments.views.verify_payment")
    def test_pending_payment_is_not_verified(self, mock_verify):
        self.payment.status = "pending"
        self.payment.checkout_request_id = ""
        self.payment.save(
            update_fields=["status", "checkout_request_id"]
        )

        response = self.client.post(self.url, follow=True)

        self.assertEqual(response.status_code, 200)
        mock_verify.assert_not_called()
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "pending")
        self.assertContains(
            response,
            "No M-Pesa payment request is currently awaiting confirmation",
        )

    @patch("apps_core.payments.views.verify_payment")
    def test_initiating_payment_is_not_verified(self, mock_verify):
        self.payment.status = "initiating"
        self.payment.checkout_request_id = ""
        self.payment.save(
            update_fields=["status", "checkout_request_id"]
        )

        response = self.client.post(self.url, follow=True)

        self.assertEqual(response.status_code, 200)
        mock_verify.assert_not_called()
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "initiating")
        self.assertContains(response, "being submitted")

    @patch("apps_core.payments.views.verify_payment")
    def test_paid_payment_is_not_verified(self, mock_verify):
        mark_payment_paid(
            self.payment,
            mpesa_receipt="PAID123",
        )

        response = self.client.post(self.url, follow=True)

        self.assertEqual(response.status_code, 200)
        mock_verify.assert_not_called()
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "paid")
        self.assertContains(
            response,
            "Your M-Pesa payment has been confirmed.",
        )

    @patch("apps_core.payments.views.verify_payment")
    def test_failed_payment_is_not_verified(self, mock_verify):
        mark_payment_failed(
            self.payment,
            result_code="1032",
            result_description="Request cancelled by user.",
        )

        response = self.client.post(self.url, follow=True)

        self.assertEqual(response.status_code, 200)
        mock_verify.assert_not_called()
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "failed")
        self.assertContains(
            response,
            "previous M-Pesa payment attempt was unsuccessful",
        )

    @patch("apps_core.payments.views.verify_payment")
    def test_processing_without_checkout_id_is_not_verified(
        self, mock_verify
    ):
        self.payment.status = "processing"
        self.payment.checkout_request_id = ""
        self.payment.save(
            update_fields=["status", "checkout_request_id"]
        )

        response = self.client.post(self.url, follow=True)

        self.assertEqual(response.status_code, 200)
        mock_verify.assert_not_called()
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "processing")
        self.assertContains(response, "M-Pesa reference is missing")

    @patch("apps_core.payments.views.verify_payment")
    def test_status_check_uses_latest_payment(self, mock_verify):
        newer = create_payment(
            booking=self.booking,
            phone_number=self.customer.phone,
        )
        mark_payment_processing(
            newer,
            checkout_request_id="newer-checkout-123",
        )
        mock_verify.side_effect = lambda payment: payment

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        mock_verify.assert_called_once()
        self.assertEqual(
            mock_verify.call_args[0][0].pk,
            newer.pk,
        )


class ReconcileMpesaPaymentsCommandTestMixin:
    def setUp(self):
        super().setUp()
        customer = Customer.objects.create(
            full_name="Reconciliation Customer",
            email="reconciliation@example.com",
            phone="254712345678",
        )
        service = Service.objects.create(
            name="Reconciliation Cleaning",
            slug="reconciliation-cleaning",
            description="Service for reconciliation command tests.",
            base_price=Decimal("5000.00"),
            duration_hours=2,
            is_active=True,
        )
        self.booking = Booking.objects.create(
            customer=customer,
            service=service,
            booking_date="2030-01-15",
            booking_time="10:00",
            address="Reconciliation Address",
            city="Nairobi",
            total_price=Decimal("5000.00"),
            status="pending",
        )

    def create_payment(self, *, status, checkout_request_id):
        return Payment.objects.create(
            booking=self.booking,
            amount=Decimal("5000.00"),
            method="mpesa",
            status=status,
            phone_number="254712345678",
            checkout_request_id=checkout_request_id,
        )


class ReconcileMpesaPaymentsCommandTests(
    ReconcileMpesaPaymentsCommandTestMixin,
    TestCase,
):
    command_name = "reconcile_mpesa_payments"

    def create_paid_payment_with_metadata(
        self,
        *,
        suffix,
        metadata_status="pending",
    ):
        payment = self.create_payment(
            status="paid",
            checkout_request_id=f"paid-{suffix}-checkout",
        )
        payment.merchant_request_id = f"paid-{suffix}-merchant"
        payment.save(update_fields=["merchant_request_id"])
        metadata = MpesaCallbackMetadata.objects.create(
            payment=payment,
            mpesa_receipt=f"REC-{suffix}",
            transaction_date=timezone.now(),
            status=metadata_status,
        )
        return payment, metadata

    def create_unresolved_payment_with_metadata(
        self,
        *,
        suffix,
        status,
    ):
        payment = self.create_payment(
            status=status,
            checkout_request_id=f"{status}-{suffix}-checkout",
        )
        payment.merchant_request_id = f"{status}-{suffix}-merchant"
        payment.save(update_fields=["merchant_request_id"])
        metadata = MpesaCallbackMetadata.objects.create(
            payment=payment,
            mpesa_receipt=f"REC-{suffix}",
            transaction_date=timezone.now(),
            status="pending",
        )
        return payment, metadata

    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.verify_payment"
    )
    def test_only_eligible_statuses_with_checkout_ids_are_selected(
        self,
        mock_verify,
    ):
        eligible = [
            self.create_payment(
                status="processing",
                checkout_request_id="processing-checkout",
            ),
            self.create_payment(
                status="unknown",
                checkout_request_id="unknown-checkout",
            ),
        ]
        self.create_payment(
            status="pending",
            checkout_request_id="pending-checkout",
        )
        self.create_payment(status="processing", checkout_request_id=None)
        self.create_payment(status="unknown", checkout_request_id="")
        self.create_payment(
            status="paid",
            checkout_request_id="paid-checkout",
        )
        self.create_payment(
            status="failed",
            checkout_request_id="failed-checkout",
        )
        mock_verify.side_effect = lambda payment: payment

        call_command(
            self.command_name,
            stdout=StringIO(),
        )

        self.assertEqual(
            [call.args[0].pk for call in mock_verify.call_args_list],
            [payment.pk for payment in eligible],
        )

    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.verify_payment"
    )
    def test_selection_order_is_created_at_then_primary_key(
        self,
        mock_verify,
    ):
        payments = [
            self.create_payment(
                status="processing",
                checkout_request_id=f"order-checkout-{index}",
            )
            for index in range(3)
        ]
        timestamp = payments[0].created_at
        Payment.objects.filter(pk__in=[payment.pk for payment in payments]).update(
            created_at=timestamp,
        )
        mock_verify.side_effect = lambda payment: payment

        call_command(self.command_name, stdout=StringIO())

        self.assertEqual(
            [call.args[0].pk for call in mock_verify.call_args_list],
            sorted(payment.pk for payment in payments),
        )

    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.verify_payment"
    )
    def test_default_limit_is_100(self, mock_verify):
        for index in range(101):
            self.create_payment(
                status="processing",
                checkout_request_id=f"default-limit-{index}",
            )
        mock_verify.side_effect = lambda payment: payment

        call_command(self.command_name, stdout=StringIO())

        self.assertEqual(mock_verify.call_count, 100)

    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.verify_payment"
    )
    def test_supplied_limit_is_respected(self, mock_verify):
        for index in range(5):
            self.create_payment(
                status="unknown",
                checkout_request_id=f"custom-limit-{index}",
            )
        mock_verify.side_effect = lambda payment: payment

        call_command(self.command_name, limit=3, stdout=StringIO())

        self.assertEqual(mock_verify.call_count, 3)

    def test_invalid_limits_raise_command_error(self):
        for limit in (0, -1, 501):
            with self.subTest(limit=limit):
                with self.assertRaises(CommandError):
                    call_command(
                        self.command_name,
                        limit=limit,
                        stdout=StringIO(),
                    )

    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.verify_payment"
    )
    def test_outcome_counts_use_returned_payment_status(
        self,
        mock_verify,
    ):
        for index in range(3):
            self.create_payment(
                status="processing",
                checkout_request_id=f"outcome-{index}",
            )
        mock_verify.side_effect = [
            Mock(status="paid"),
            Mock(status="failed"),
            Mock(status="unknown"),
        ]
        output = StringIO()

        call_command(self.command_name, stdout=output)

        self.assertIn(
            "checked=3, paid=1, failed=1, unresolved=1",
            output.getvalue(),
        )

    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.verify_payment"
    )
    def test_payment_id_targets_single_payment(self, mock_verify):
        self.create_payment(
            status="processing",
            checkout_request_id="first-checkout",
        )
        second = self.create_payment(
            status="processing",
            checkout_request_id="second-checkout",
        )
        mock_verify.side_effect = lambda payment: payment

        call_command(
            self.command_name,
            payment_id=second.pk,
            delay=0,
            stdout=StringIO(),
        )

        self.assertEqual(
            [call.args[0].pk for call in mock_verify.call_args_list],
            [second.pk],
        )

    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.verify_payment"
    )
    def test_cooldown_skipped_payment_is_reported(self, mock_verify):
        self.create_payment(
            status="processing",
            checkout_request_id="cooldown-checkout",
        )

        def skipped(selected_payment):
            selected_payment._verification_attempted = False
            return selected_payment

        mock_verify.side_effect = skipped
        output = StringIO()

        call_command(self.command_name, delay=0, stdout=output)

        self.assertIn(
            "checked=1, paid=0, failed=0, unresolved=0, skipped=1",
            output.getvalue(),
        )

    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.logger.exception"
    )
    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.verify_payment"
    )
    def test_exception_does_not_stop_later_payments(
        self,
        mock_verify,
        mock_log_exception,
    ):
        payments = [
            self.create_payment(
                status="processing",
                checkout_request_id=f"exception-{index}",
            )
            for index in range(2)
        ]
        mock_verify.side_effect = [
            RuntimeError("verification error"),
            Mock(status="paid"),
        ]
        output = StringIO()

        call_command(self.command_name, stdout=output)

        self.assertEqual(
            [call.args[0].pk for call in mock_verify.call_args_list],
            [payment.pk for payment in payments],
        )
        self.assertIn(
            "checked=2, paid=1, failed=0, unresolved=1",
            output.getvalue(),
        )
        mock_log_exception.assert_called_once()

    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.verify_payment"
    )
    def test_dry_run_counts_batch_without_calling_verifier(
        self,
        mock_verify,
    ):
        self.create_payment(
            status="processing",
            checkout_request_id="dry-run-valid-1",
        )
        self.create_payment(
            status="unknown",
            checkout_request_id="dry-run-valid-2",
        )
        self.create_payment(status="processing", checkout_request_id=None)
        self.create_payment(status="unknown", checkout_request_id="")
        output = StringIO()

        call_command(
            self.command_name,
            limit=1,
            dry_run=True,
            stdout=output,
        )

        self.assertIn("Eligible payments in selected batch: 1.", output.getvalue())
        self.assertIn("no provider queries were made", output.getvalue())
        self.assertIn("no payment records were changed", output.getvalue())
        mock_verify.assert_not_called()

    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.verify_payment"
    )
    def test_terminal_payments_are_never_selected(self, mock_verify):
        self.create_payment(
            status="paid",
            checkout_request_id="terminal-paid-checkout",
        )
        self.create_payment(
            status="failed",
            checkout_request_id="terminal-failed-checkout",
        )

        call_command(self.command_name, stdout=StringIO())

        mock_verify.assert_not_called()

    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.reconcile_callback_metadata"
    )
    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.verify_payment"
    )
    def test_pending_callback_metadata_for_paid_payments_are_selected(
        self,
        mock_verify,
        mock_reconcile,
    ):
        _, metadata = self.create_paid_payment_with_metadata(
            suffix="eligible"
        )

        non_paid = self.create_payment(
            status="pending",
            checkout_request_id="non-paid-metadata-checkout",
        )
        MpesaCallbackMetadata.objects.create(
            payment=non_paid,
            mpesa_receipt="REC-NONPAID",
            status="pending",
        )

        self.create_paid_payment_with_metadata(
            suffix="applied",
            metadata_status="applied",
        )

        def reconcile(record):
            record.status = "applied"
            record.save(update_fields=["status"])
            return record

        mock_reconcile.side_effect = reconcile
        output = StringIO()

        call_command(self.command_name, stdout=output)

        self.assertEqual(
            [call.args[0].pk for call in mock_reconcile.call_args_list],
            [metadata.pk],
        )
        mock_verify.assert_not_called()
        self.assertIn(
            "callback_metadata=checked:1 applied:1",
            output.getvalue(),
        )

    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.reconcile_callback_metadata"
    )
    def test_callback_metadata_dry_run_does_not_mutate_records(
        self,
        mock_reconcile,
    ):
        payment, metadata = self.create_paid_payment_with_metadata(
            suffix="dry-run"
        )
        original_attempt_count = metadata.attempt_count
        output = StringIO()

        call_command(
            self.command_name,
            dry_run=True,
            stdout=output,
        )

        metadata.refresh_from_db()
        payment.refresh_from_db()

        self.assertEqual(metadata.status, "pending")
        self.assertEqual(metadata.attempt_count, original_attempt_count)
        self.assertIsNone(payment.mpesa_receipt)
        self.assertIsNone(payment.transaction_date)
        mock_reconcile.assert_not_called()
        self.assertIn(
            "Eligible callback metadata records in selected batch: 1.",
            output.getvalue(),
        )
        self.assertIn("not changed", output.getvalue())

    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.reconcile_callback_metadata"
    )
    def test_callback_metadata_reconciliation_continues_after_failure(
        self,
        mock_reconcile,
    ):
        _, first = self.create_paid_payment_with_metadata(
            suffix="failure"
        )
        _, second = self.create_paid_payment_with_metadata(
            suffix="success"
        )

        def reconcile(record):
            if record.pk == first.pk:
                raise RuntimeError("metadata reconciliation failed")
            record.status = "applied"
            record.save(update_fields=["status"])
            return record

        mock_reconcile.side_effect = reconcile
        output = StringIO()

        call_command(self.command_name, stdout=output)

        self.assertEqual(mock_reconcile.call_count, 2)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, "pending")
        self.assertEqual(second.status, "applied")
        self.assertIn("errors:1", output.getvalue())
        self.assertIn("applied:1", output.getvalue())

    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.reconcile_callback_metadata"
    )
    def test_callback_metadata_limit_is_respected(self, mock_reconcile):
        for index in range(3):
            self.create_paid_payment_with_metadata(
                suffix=f"limit-{index}"
            )
        mock_reconcile.side_effect = lambda record: record

        call_command(
            self.command_name,
            limit=2,
            stdout=StringIO(),
        )

        self.assertEqual(mock_reconcile.call_count, 2)

    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.reconcile_callback_metadata"
    )
    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.verify_payment"
    )
    def test_pending_callback_metadata_for_unresolved_payments_are_selected(
        self,
        mock_verify,
        mock_reconcile,
    ):
        created = [
            self.create_unresolved_payment_with_metadata(
                suffix=status,
                status=status,
            )
            for status in ("processing", "unknown")
        ]

        def reconcile(record):
            record.status = "applied"
            record.save(update_fields=["status"])
            return record

        mock_reconcile.side_effect = reconcile
        output = StringIO()

        call_command(self.command_name, stdout=output)

        self.assertEqual(mock_reconcile.call_count, 2)
        mock_verify.assert_not_called()
        for _, metadata in created:
            metadata.refresh_from_db()
            self.assertEqual(metadata.status, "applied")
        self.assertIn(
            "callback_metadata=checked:2 applied:2",
            output.getvalue(),
        )

    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.reconcile_callback_metadata"
    )
    def test_callback_metadata_dry_run_includes_unresolved_payments(
        self,
        mock_reconcile,
    ):
        payment, metadata = self.create_unresolved_payment_with_metadata(
            suffix="dry-run",
            status="processing",
        )
        original_attempt_count = metadata.attempt_count
        output = StringIO()

        call_command(
            self.command_name,
            dry_run=True,
            stdout=output,
        )

        payment.refresh_from_db()
        metadata.refresh_from_db()

        self.assertEqual(payment.status, "processing")
        self.assertEqual(metadata.status, "pending")
        self.assertEqual(metadata.attempt_count, original_attempt_count)
        mock_reconcile.assert_not_called()
        self.assertIn(
            "Eligible payments in selected batch: 0.",
            output.getvalue(),
        )
        self.assertIn(
            "Eligible callback metadata records in selected batch: 1.",
            output.getvalue(),
        )
        self.assertIn("not changed", output.getvalue())


class ReconcileMpesaPaymentsCommandTransactionTests(
    ReconcileMpesaPaymentsCommandTestMixin,
    TransactionTestCase,
):
    @patch(
        "apps_core.payments.management.commands."
        "reconcile_mpesa_payments.verify_payment"
    )
    def test_verification_runs_outside_command_transaction(
        self,
        mock_verify,
    ):
        payment = self.create_payment(
            status="processing",
            checkout_request_id="transaction-checkout",
        )

        def verify_outside_transaction(selected_payment):
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(selected_payment.pk, payment.pk)
            return Mock(status="unknown")

        mock_verify.side_effect = verify_outside_transaction

        call_command(
            "reconcile_mpesa_payments",
            stdout=StringIO(),
        )

        mock_verify.assert_called_once()


class PaymentAdminRetentionTests(TestCase):
    def test_payment_delete_and_bulk_delete_are_disabled(self):
        user = get_user_model().objects.create_superuser(
            username="payment-admin",
            email="payment-admin@example.com",
            password="test-password",
        )
        request = RequestFactory().get("/admin/payments/payment/")
        request.user = user
        model_admin = PaymentAdmin(Payment, admin.site)

        self.assertFalse(model_admin.has_delete_permission(request))
        self.assertNotIn("delete_selected", model_admin.get_actions(request))


class PaymentAdminCallbackMetadataTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="payment-callback-admin",
            email="payment-callback-admin@example.com",
            password="test-password",
        )
        self.client.force_login(self.user)

        self.customer = Customer.objects.create(
            full_name="Admin Callback Customer",
            email="admin-callback@example.com",
            phone="254700000000",
        )
        self.service = Service.objects.create(
            name="Admin Callback Cleaning",
            slug="admin-callback-cleaning",
            description="Admin callback test service.",
            base_price=Decimal("4000.00"),
            duration_hours=2,
            is_active=True,
        )
        self.booking = Booking.objects.create(
            customer=self.customer,
            service=self.service,
            booking_date="2030-02-01",
            booking_time="09:00",
            address="Admin Callback Address",
            city="Nairobi",
            property_size=80,
            rooms=2,
            total_price=Decimal("4000.00"),
            status="pending",
        )
        self.payment = create_payment(
            booking=self.booking,
            phone_number="254700000000",
        )
        self.payment.status = "paid"
        self.payment.checkout_request_id = "admin-callback-checkout"
        self.payment.merchant_request_id = "admin-callback-merchant"
        self.payment.save(
            update_fields=[
                "status",
                "checkout_request_id",
                "merchant_request_id",
            ]
        )

    def change_url(self):
        return reverse(
            "admin:payments_payment_change",
            args=[self.payment.pk],
        )

    def changelist_url(self):
        return reverse("admin:payments_payment_changelist")

    def test_confirmed_receipt_is_displayed_in_change_form_and_list(self):
        self.payment.mpesa_receipt = "CONFIRMED123"
        self.payment.transaction_date = timezone.now()
        self.payment.save(
            update_fields=["mpesa_receipt", "transaction_date"]
        )

        change_response = self.client.get(self.change_url())
        list_response = self.client.get(self.changelist_url())

        self.assertEqual(change_response.status_code, 200)
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(change_response, "CONFIRMED123")
        self.assertContains(list_response, "CONFIRMED123")

    def test_pending_callback_metadata_is_displayed_as_read_only_candidate(self):
        now = timezone.now()
        metadata = MpesaCallbackMetadata.objects.create(
            payment=self.payment,
            mpesa_receipt="CANDIDATE123",
            transaction_date=now,
            received_at=now,
            status="pending",
            attempt_count=3,
            last_attempt_at=now,
            last_error="http_429",
        )

        response = self.client.get(self.change_url())
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Candidate M-Pesa receipt (unverified)", content)
        self.assertIn("CANDIDATE123", content)
        self.assertIn("Candidate transaction date (unverified)", content)
        self.assertIn("Callback metadata status", content)
        self.assertIn("Pending Verification", content)
        self.assertIn("Callback metadata attempts", content)
        self.assertIn("http_429", content)

        self.payment.refresh_from_db()
        self.assertIsNone(self.payment.mpesa_receipt)
        self.assertIsNone(self.payment.transaction_date)
        metadata.refresh_from_db()
        self.assertEqual(metadata.status, "pending")

    def test_receipt_fields_are_read_only_in_admin(self):
        request = RequestFactory().get(self.change_url())
        request.user = self.user
        model_admin = PaymentAdmin(Payment, admin.site)

        readonly_fields = model_admin.get_readonly_fields(
            request,
            self.payment,
        )
        form = model_admin.get_form(request, self.payment)

        self.assertIn("mpesa_receipt", readonly_fields)
        self.assertIn("transaction_date", readonly_fields)
        self.assertIn("callback_candidate_receipt", readonly_fields)
        self.assertIn("callback_candidate_transaction_date", readonly_fields)
        self.assertIn("callback_metadata_status", readonly_fields)
        self.assertIn("callback_metadata_attempt_count", readonly_fields)
        self.assertNotIn("mpesa_receipt", form.base_fields)
        self.assertNotIn("transaction_date", form.base_fields)
        self.assertNotIn("callback_candidate_receipt", form.base_fields)

    def test_applied_metadata_does_not_show_unverified_candidate(self):
        now = timezone.now()
        self.payment.mpesa_receipt = "CONFIRMED456"
        self.payment.transaction_date = now
        self.payment.save(
            update_fields=["mpesa_receipt", "transaction_date"]
        )
        MpesaCallbackMetadata.objects.create(
            payment=self.payment,
            mpesa_receipt="APPLIED456",
            transaction_date=now,
            received_at=now,
            status="applied",
            attempt_count=1,
            last_attempt_at=now,
            verified_at=now,
        )

        response = self.client.get(self.change_url())
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("CONFIRMED456", content)
        self.assertNotIn("APPLIED456", content)
        self.assertIn("Applied", content)
