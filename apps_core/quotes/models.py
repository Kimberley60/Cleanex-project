from django.db import models
from apps_core.customers.models import Customer
from apps_core.services.models import Service


class QuoteRequest(models.Model):

    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("under_review", "Under Review"),
        ("prepared", "Prepared"),
        ("sent", "Sent"),
        ("accepted", "Accepted"),
        ("declined", "Declined"),
        ("expired", "Expired"),
        ("cancelled", "Cancelled"),
    )

    full_name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=20)

    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_requests",
    )

    service = models.ForeignKey(
        Service,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    property_size = models.IntegerField()
    rooms = models.IntegerField()
    bathrooms = models.IntegerField()

    extras = models.TextField(blank=True)

    address = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    city = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )
    preferred_date = models.DateField(null=True, blank=True)
    preferred_time = models.TimeField(null=True, blank=True)

    estimated_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        help_text="Current status of this quote request.",
    )
    internal_notes = models.TextField(blank=True)
    proposed_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Staff-prepared quotation amount, separate from the estimate.",
    )
    quote_valid_until = models.DateField(null=True, blank=True)
    quote_prepared_at = models.DateTimeField(null=True, blank=True)
    quote_sent_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.full_name