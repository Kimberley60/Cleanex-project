from django.db import models
from apps_core.services.models import Service, ExtraService
from apps_core.customers.models import Customer
from apps_core.staff.models import Staff


class Booking(models.Model):

    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("confirmed", "Confirmed"),
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    )

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="bookings"

    )

    contact_name = models.CharField(
        max_length=150,
        blank=True,
    )
    contact_email = models.EmailField(
        blank=True,
    )
    contact_phone = models.CharField(
        max_length=20,
        blank=True,
    )

    service = models.ForeignKey(
        Service,
        on_delete=models.SET_NULL,
        null=True
    )

    staff = models.ForeignKey(
        Staff,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    booking_date = models.DateField()
    booking_time = models.TimeField()

    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)

    property_size = models.IntegerField(
        null=True,
        blank=True
    )

    rooms = models.IntegerField(
        null=True,
        blank=True
    )

    total_price = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending"
    )

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-booking_date", "-booking_time"]

        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["city"]),
            models.Index(fields=["booking_date"]),
        ]

    def __str__(self):
        return f"Booking #{self.pk} - {self.customer}"

    @property
    def booking_reference(self):
        """
        Return a customer-friendly public booking reference.
        """
        return f"CLX-{self.pk:06d}"


class BookingExtra(models.Model):

    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name="extras"
    )

    extra_service = models.ForeignKey(
        ExtraService,
        on_delete=models.CASCADE
    )

    def __str__(self):
        return f"{self.booking} - {self.extra_service}"