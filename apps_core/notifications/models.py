from django.conf import settings
from django.db import models


class QuoteStatusNotification(models.Model):
    EVENT_CHOICES = (
        ("sent", "Sent"),
        ("declined", "Declined"),
        ("expired", "Expired"),
    )

    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("sending", "Sending"),
        ("sent", "Sent"),
        ("failed", "Failed"),
    )

    quote = models.ForeignKey(
        "quotes.QuoteRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    quote_reference = models.CharField(max_length=50)
    event = models.CharField(max_length=20, choices=EVENT_CHOICES)
    event_key = models.CharField(max_length=255, unique=True)
    recipient = models.EmailField()
    subject = models.CharField(max_length=255)
    text_body = models.TextField()
    html_body = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )
    attempt_count = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    retrying_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="retried_quote_notifications",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "quote status notification"
        verbose_name_plural = "quote status notifications"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"], name="notif_status_idx"),
            models.Index(fields=["event"], name="notif_event_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(event__in=["sent", "declined", "expired"]),
                name="notif_event_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["pending", "sending", "sent", "failed"]
                ),
                name="notif_status_valid",
            ),
        ]

    def __str__(self):
        return f"{self.quote_reference} {self.get_event_display()} notification"
