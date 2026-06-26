from django.db import models
from django.urls import reverse


class Service(models.Model):
    name = models.CharField(
        max_length=120
    )

    slug = models.SlugField(
        unique=True
    )

    # Card Description
    short_description = models.CharField(
        max_length=250,
        blank=True,
        default="",
        help_text="Short text displayed on service cards."
    )

    # Full Service Page Description
    description = models.TextField()

    base_price = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    price_per_hour = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True
    )

    duration_hours = models.DecimalField(
        max_digits=4,
        decimal_places=2
    )

    # Font Awesome Icon
    icon = models.CharField(
        max_length=100,
        blank=True,
        help_text="Example: fa-solid fa-house"
    )

    # Service Card Image
    image = models.ImageField(
        upload_to="services/",
        blank=True,
        null=True
    )

    # ==========================================
    # HERO SECTION
    # ==========================================

    hero_image = models.ImageField(
        upload_to="services/heroes/",
        blank=True,
        null=True,
        help_text="Banner image displayed on service detail page."
    )

    hero_title = models.CharField(
        max_length=255,
        blank=True
    )

    hero_subtitle = models.TextField(
        blank=True
    )

    primary_button_text = models.CharField(
        max_length=50,
        default="Book Now"
    )

    primary_button_url = models.CharField(
        max_length=255,
        default="/booking/"
    )

    secondary_button_text = models.CharField(
        max_length=50,
        default="View Services"
    )

    secondary_button_url = models.CharField(
        max_length=255,
        default="/services/"
    )

    # ==========================================
    # DISPLAY SETTINGS
    # ==========================================

    display_order = models.PositiveIntegerField(
        default=0,
        help_text="Controls card order on homepage."
    )

    is_active = models.BooleanField(
        default=True
    )

    is_featured = models.BooleanField(
        default=False
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    class Meta:
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse(
            "service_detail",
            kwargs={"slug": self.slug}
        )


# ==========================================
# ServiceFeature
# ==========================================

class ServiceFeature(models.Model):
    service = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name="features"
    )

    title = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.service.name} - {self.title}"


# ==========================================
# ExtraService
# ==========================================

class ExtraService(models.Model):
    name = models.CharField(max_length=100)

    price = models.DecimalField(
        max_digits=8,
        decimal_places=2
    )

    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name