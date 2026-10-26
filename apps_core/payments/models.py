from django.db import models
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
