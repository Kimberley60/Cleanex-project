import base64
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
import requests
from decouple import config


class MpesaProviderError(requests.RequestException):
    """Provider HTTP error with safe diagnostic metadata."""

    def __init__(
        self,
        message,
        *,
        operation,
        status_code=None,
        retry_after=None,
        request_id=None,
        error_code=None,
        error_message=None,
    ):
        super().__init__(message)
        self.operation = operation
        self.status_code = status_code
        self.retry_after = retry_after
        self.request_id = request_id
        self.error_code = error_code
        self.error_message = error_message


class MpesaLocalValidationError(ValueError):
    """Local validation/configuration failure before any Daraja HTTP request."""


_REQUEST_ID_HEADERS = (
    "x-request-id",
    "x-correlation-id",
    "request-id",
    "x-requestid",
    "x-amzn-requestid",
    "x-amzn-trace-id",
)


def _header_value(headers, name):
    lowered = {
        str(key).lower(): value
        for key, value in headers.items()
    }
    value = lowered.get(name.lower())
    if value:
        return str(value).strip()
    return None


def _response_diagnostics(response):
    headers = getattr(response, "headers", None) or {}
    retry_after = _header_value(headers, "retry-after")
    request_id = None
    for header_name in _REQUEST_ID_HEADERS:
        request_id = _header_value(headers, header_name)
        if request_id:
            request_id = request_id[:200]
            break
    return retry_after, request_id


def _safe_error_value(value, max_length=200, *, redact_phone=False):
    if value is None:
        return None

    text = re.sub(r"[\r\n]+", " ", str(value)).strip()

    if not text:
        return None

    if redact_phone:
        text = re.sub(
            r"(?<!\d)(?:\+?254|0)?[17]\d{8}(?!\d)",
            "[redacted]",
            text,
        )

    return text[:max_length]


def _safe_error_details(response):
    """Extract safe structured error fields without logging bodies."""

    error_code = None
    error_message = None
    request_id = None

    try:
        data = response.json()
    except Exception:
        data = None

    if isinstance(data, dict):
        error_code = _safe_error_value(
            data.get("errorCode")
            or data.get("error_code")
            or data.get("code"),
            max_length=100,
        )
        error_message = _safe_error_value(
            data.get("errorMessage")
            or data.get("error_message")
            or data.get("message"),
            max_length=200,
            redact_phone=True,
        )
        request_id = _safe_error_value(
            data.get("requestId")
            or data.get("request_id")
            or data.get("RequestId"),
            max_length=200,
        )

    return error_code, error_message, request_id


def _raise_for_status(response, operation):
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int) and status_code >= 400:
        retry_after, request_id = _response_diagnostics(response)
        error_code, error_message, body_request_id = _safe_error_details(
            response
        )
        request_id = request_id or body_request_id
        raise MpesaProviderError(
            f"M-Pesa {operation} request failed with HTTP {status_code}.",
            operation=operation,
            status_code=status_code,
            retry_after=retry_after,
            request_id=request_id,
            error_code=error_code,
            error_message=error_message,
        )
    response.raise_for_status()


class MpesaIntegration:
    """
    Provider integration for Safaricom Daraja APIs.

    This class is responsible only for communicating
    with the M-Pesa API.

    Payment business rules remain in payments/services.py.
    """

    SANDBOX_BASE_URL = "https://sandbox.safaricom.co.ke"

    PRODUCTION_BASE_URL = "https://api.safaricom.co.ke"

    def __init__(self):
        self.consumer_key = config(
            "MPESA_CONSUMER_KEY",
            default="",
        )

        self.consumer_secret = config(
            "MPESA_CONSUMER_SECRET",
            default="",
        )

        self.shortcode = config(
            "MPESA_SHORTCODE",
            default="",
        )

        self.passkey = config(
            "MPESA_PASSKEY",
            default="",
        )

        self.callback_url = config(
            "MPESA_CALLBACK_URL",
            default="",
        )

        self.environment = config(
            "MPESA_ENVIRONMENT",
            default="sandbox",
        ).lower()

    @property
    def base_url(self):
        if self.environment == "production":
            return self.PRODUCTION_BASE_URL

        return self.SANDBOX_BASE_URL

    @property
    def token_url(self):
        return (
            f"{self.base_url}"
            "/oauth/v1/generate"
            "?grant_type=client_credentials"
        )

    @property
    def stk_push_url(self):
        return (
            f"{self.base_url}"
            "/mpesa/stkpush/v1/processrequest"
        )

    def query_stk_push(self, checkout_request_id):
        """
        Query the result of an existing M-Pesa STK Push request.

        Payment status decisions remain in the payment service layer.
        """

        if not checkout_request_id:
            raise ValueError("Checkout request ID is required.")

        timestamp = datetime.now().strftime(
            "%Y%m%d%H%M%S"
        )

        payload = {
            "BusinessShortCode": self.shortcode,
            "Password": self._generate_password(timestamp),
            "Timestamp": timestamp,
            "CheckoutRequestID": checkout_request_id,
        }

        response = requests.post(
            f"{self.base_url}/mpesa/stkpushquery/v1/query",
            json=payload,
            headers={
                "Authorization": (
                    f"Bearer {self.get_access_token()}"
                ),
                "Content-Type": "application/json",
            },
            timeout=30,
        )

        _raise_for_status(response, "stk_query")

        try:
            result = response.json()
        except ValueError:
            raise ValueError(
                "M-Pesa STK Push Query returned invalid JSON."
            ) from None

        if not isinstance(result, dict):
            raise ValueError(
                "M-Pesa STK Push Query returned an unexpected response."
            )

        return result

    def get_access_token(self):
        """
        Request an OAuth access token from Daraja.
        """

        if not self.consumer_key:
            raise MpesaLocalValidationError(
                "MPESA_CONSUMER_KEY is not configured."
            )

        if not self.consumer_secret:
            raise MpesaLocalValidationError(
                "MPESA_CONSUMER_SECRET is not configured."
            )

        response = requests.get(
            self.token_url,
            auth=(
                self.consumer_key,
                self.consumer_secret,
            ),
            timeout=30,
        )

        _raise_for_status(response, "oauth")

        data = response.json()

        access_token = data.get("access_token")

        if not access_token:
            raise ValueError(
                "M-Pesa access token was not returned."
            )

        return access_token

    def _generate_password(self, timestamp):
        """
        Generate the Base64 password required
        by the M-Pesa STK Push API.
        """

        if not self.shortcode:
            raise MpesaLocalValidationError(
                "MPESA_SHORTCODE is not configured."
            )

        if not self.passkey:
            raise MpesaLocalValidationError(
                "MPESA_PASSKEY is not configured."
            )

        raw_password = (
            f"{self.shortcode}"
            f"{self.passkey}"
            f"{timestamp}"
        )

        return base64.b64encode(
            raw_password.encode("utf-8")
        ).decode("utf-8")

    @staticmethod
    def _normalize_phone_number(phone_number):
        """
        Normalize a Kenyan mobile number to 254XXXXXXXXX.

        Accepts common formats such as:
        0712345678
        0112345678
        254712345678
        +254712345678
        """

        if not phone_number:
            raise MpesaLocalValidationError(
                "A phone number is required."
            )

        phone = str(phone_number).strip()

        # Remove common formatting characters.
        phone = re.sub(r"[\s()+-]", "", phone)

        if phone.startswith("0") and len(phone) == 10:
            phone = "254" + phone[1:]

        elif len(phone) == 9 and phone[0] in {"7", "1"}:
            phone = "254" + phone

        if not re.fullmatch(r"254[71]\d{8}", phone):
            raise MpesaLocalValidationError(
                "Enter a valid Kenyan mobile number, "
                "for example 0712345678 or 254712345678."
            )

        return phone

    def initiate_stk_push(
        self,
        *,
        amount,
        phone_number,
        account_reference,
        transaction_description,
    ):
        """
        Initiate an M-Pesa STK Push.

        This method communicates with Daraja only.
        Payment records are managed by the payment
        service layer.
        """

        # Validate and normalize the amount.
        try:
            amount = Decimal(str(amount))
        except (InvalidOperation, TypeError, ValueError):
            raise MpesaLocalValidationError(
                "A valid payment amount is required."
            ) from None

        if not amount.is_finite() or amount <= Decimal("0"):
            raise MpesaLocalValidationError(
                "STK Push amount must be greater than zero."
            )

        if amount != amount.to_integral_value():
            raise MpesaLocalValidationError(
                "STK Push amount must be a whole number "
                "of Kenyan shillings."
            )

        amount = int(amount)

        # Validate and normalize the phone number.
        phone_number = self._normalize_phone_number(
            phone_number
        )

        if not account_reference:
            raise MpesaLocalValidationError(
                "An account reference is required."
            )

        if not transaction_description:
            raise MpesaLocalValidationError(
                "A transaction description is required."
            )

        if not self.shortcode:
            raise MpesaLocalValidationError(
                "MPESA_SHORTCODE is not configured."
            )

        if not self.callback_url:
            raise MpesaLocalValidationError(
                "MPESA_CALLBACK_URL is not configured."
            )

        timestamp = datetime.now().strftime(
            "%Y%m%d%H%M%S"
        )

        password = self._generate_password(
            timestamp
        )

        access_token = self.get_access_token()

        headers = {
            "Authorization": (
                f"Bearer {access_token}"
            ),
            "Content-Type": "application/json",
        }

        payload = {
            "BusinessShortCode": self.shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": (
                "CustomerPayBillOnline"
            ),
            "Amount": amount,
            "PartyA": phone_number,
            "PartyB": self.shortcode,
            "PhoneNumber": phone_number,
            "CallBackURL": self.callback_url,
            "AccountReference": account_reference,
            "TransactionDesc": (
                transaction_description
            ),
        }

        response = requests.post(
            self.stk_push_url,
            json=payload,
            headers=headers,
            timeout=30,
        )

        _raise_for_status(response, "stk_push")

        return response.json()
