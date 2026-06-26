from django.db import models


class Staff(models.Model):

    ROLE_CHOICES = (
        ("cleaner", "Cleaner"),
        ("supervisor", "Supervisor"),
        ("manager", "Manager"),
    )

    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20)

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default="cleaner"
    )

    city = models.CharField(max_length=100)

    is_available = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    hire_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name