import base64
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
import requests
from decouple import config



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

    def get_access_token(self):
        """
        Request an OAuth access token from Daraja.
        """

        if not self.consumer_key:
            raise ValueError(
                "MPESA_CONSUMER_KEY is not configured."
            )

        if not self.consumer_secret:
            raise ValueError(
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

        response.raise_for_status()

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
            raise ValueError(
                "MPESA_SHORTCODE is not configured."
            )

        if not self.passkey:
            raise ValueError(
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
            raise ValueError(
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
            raise ValueError(
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
            raise ValueError(
                "A valid payment amount is required."
            ) from None

        if not amount.is_finite() or amount <= Decimal("0"):
            raise ValueError(
                "STK Push amount must be greater than zero."
            )

        if amount != amount.to_integral_value():
            raise ValueError(
                "STK Push amount must be a whole number "
                "of Kenyan shillings."
            )

        amount = int(amount)

        # Validate and normalize the phone number.
        phone_number = self._normalize_phone_number(
            phone_number
        )

        if not account_reference:
            raise ValueError(
                "An account reference is required."
            )

        if not transaction_description:
            raise ValueError(
                "A transaction description is required."
            )

        if not self.shortcode:
            raise ValueError(
                "MPESA_SHORTCODE is not configured."
            )

        if not self.callback_url:
            raise ValueError(
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

        response.raise_for_status()

        return response.json()
