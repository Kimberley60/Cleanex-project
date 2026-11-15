from django.db import models
from django.utils import timezone
from apps_core.bookings.models import Booking


class Payment(models.Model):
    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("initiating", "Initiating"),
        ("processing", "Processing"),
        ("unknown", "Outcome Unknown"),
        ("paid", "Paid"),
        ("failed", "Failed"),
    )

    METHOD_CHOICES = (
        ("mpesa", "M-Pesa"),
    )

    booking = models.ForeignKey(
        Booking,
        on_delete=models.PROTECT,
        related_name="payments",
    )

    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    method = models.CharField(
        max_length=20,
        choices=METHOD_CHOICES,
        default="mpesa",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )

    phone_number = models.CharField(
        max_length=20,
    )

    checkout_request_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        unique=True,
    )

    merchant_request_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
    )

    mpesa_receipt = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        unique=True,
    )

    transaction_date = models.DateTimeField(
        blank=True,
        null=True,
    )

    verification_attempt_count = models.PositiveIntegerField(
        default=0,
    )

    last_verification_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    next_verification_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    verification_lease_id = models.UUIDField(
        blank=True,
        null=True,
    )

    verification_lease_expires_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    result_code = models.CharField(
        max_length=20,
        blank=True,
        null=True,
    )

    result_description = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["booking", "status"]
            ),
            models.Index(
                fields=["status"]
            ),
        ]

    def __str__(self):
        return (
            f"Payment #{self.pk} - "
            f"{self.booking.booking_reference}"
        )


class MpesaCallbackMetadata(models.Model):
    """
    Validated M-Pesa callback metadata awaiting independent verification.

    This record stores only the callback fields needed to recover receipt
    and transaction-date metadata for an eligible payment. It never
    authorizes a payment state change by itself.
    """

    STATUS_CHOICES = (
        ("pending", "Pending Verification"),
        ("applied", "Applied"),
        ("failed", "Failed"),
    )

    payment = models.OneToOneField(
        Payment,
        on_delete=models.CASCADE,
        related_name="callback_metadata",
    )

    mpesa_receipt = models.CharField(
        max_length=100,
        blank=True,
        null=True,
    )

    transaction_date = models.DateTimeField(
        blank=True,
        null=True,
    )

    received_at = models.DateTimeField(
        default=timezone.now,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )

    attempt_count = models.PositiveIntegerField(
        default=0,
    )

    last_attempt_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    last_error = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )

    verified_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["received_at", "pk"]
        indexes = [
            models.Index(
                fields=["status", "received_at"]
            ),
        ]

    def __str__(self):
        return (
            f"M-Pesa callback metadata for payment #{self.payment_id}"
        )
