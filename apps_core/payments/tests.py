from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from apps_core.bookings.models import Booking
from unittest.mock import Mock, patch
from apps_core.customers.models import Customer
from apps_core.payments.models import Payment
from apps_core.payments.services import (
    create_payment,
    get_payment_for_booking,
    mark_payment_failed,
    mark_payment_paid,
    mark_payment_processing,
    initiate_payment, process_mpesa_callback,
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

        self.assertIsNotNone(
            payment.transaction_date,
        )

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
            "pending",
        )

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

    def test_successful_callback_marks_payment_paid(self):
        payload = self.successful_callback_payload()

        payment = process_mpesa_callback(
            payload
        )

        payment.refresh_from_db()

        self.assertEqual(
            payment.status,
            "paid",
        )

        self.assertEqual(
            payment.mpesa_receipt,
            "ABC123XYZ",
        )

        self.assertEqual(
            payment.result_code,
            "0",
        )

        self.assertEqual(
            payment.result_description,
            "The service request is processed successfully.",
        )

        self.assertIsNotNone(
            payment.transaction_date
        )

    def test_failed_callback_marks_payment_failed(self):
        payload = self.failed_callback_payload()

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

        self.assertEqual(
            payment.result_description,
            "Request cancelled by user.",
        )

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

    def test_successful_callback_requires_receipt(self):
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

        with self.assertRaisesMessage(
            ValueError,
            "M-Pesa receipt is missing from successful callback.",
        ):
            process_mpesa_callback(
                payload
            )

        self.payment.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            "processing",
        )

    def test_duplicate_paid_callback_is_safe(self):
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

        self.assertEqual(
            payment.mpesa_receipt,
            "ABC123XYZ",
        )

    def test_duplicate_failed_callback_is_safe(self):
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

    def test_successful_callback_endpoint_marks_payment_paid(self):
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

        self.payment.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            "paid",
        )

        self.assertEqual(
            self.payment.mpesa_receipt,
            "VIEWABC123",
        )

    def test_failed_callback_endpoint_marks_payment_failed(self):
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

