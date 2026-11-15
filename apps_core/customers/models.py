from django.db import models
from django.conf import settings
from django.db.models import F, Func, Value
from django.db.models.functions import Lower


def normalize_customer_email(email):
    return email.strip().lower()


def normalized_customer_email_expression():
    return Lower(
        Func(
            F("email"),
            Value(r"^[[:space:]]+|[[:space:]]+$"),
            Value(""),
            Value("g"),
            function="REGEXP_REPLACE",
        )
    )


class Customer(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    full_name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=20)

    address = models.CharField(max_length=255)
    county = models.CharField(max_length=100, blank=True, default="")
    city = models.CharField(max_length=100)

    is_repeat_customer = models.BooleanField(default=False)

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                normalized_customer_email_expression(),
                name="customer_normalized_email_unique",
                violation_error_message=(
                    "A customer with this email address already exists."
                ),
                violation_error_code="unique",
            ),
        ]

    def clean(self):
        super().clean()
        if self.email:
            self.email = normalize_customer_email(self.email)

    def save(self, *args, **kwargs):
        if self.email:
            self.email = normalize_customer_email(self.email)
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.full_name


class PendingCustomerRegistration(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pending_customer_registration",
    )
    phone = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Pending registration for {self.user.email}"