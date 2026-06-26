from django.db import models
from apps_core.services.models import Service


class QuoteRequest(models.Model):

    full_name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=20)

    service = models.ForeignKey(
        Service,
        on_delete=models.SET_NULL,
        null=True
    )

    property_size = models.IntegerField()
    rooms = models.IntegerField()
    bathrooms = models.IntegerField()

    extras = models.TextField(blank=True)

    estimated_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.full_name