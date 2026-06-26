from django.db import models

# Create your models here.


# HeroSection
class HeroSection(models.Model):

    PAGE_CHOICES = [
        ("home", "Homepage"),
        ("services", "Services Listing"),
    ]

    name = models.CharField(max_length=100)

    page = models.CharField(
        max_length=50,
        choices=PAGE_CHOICES,
        unique=True
    )

    autoplay = models.BooleanField(default=True)

    autoplay_speed = models.PositiveIntegerField(
        default=5000
    )

    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name



# HeroSlide
class HeroSlide(models.Model):

    hero_section = models.ForeignKey(
        HeroSection,
        on_delete=models.CASCADE,
        related_name="slides"
    )

    title = models.CharField(max_length=255)

    subtitle = models.TextField()

    image = models.ImageField(
        upload_to="hero/"
    )

    primary_button_text = models.CharField(
        max_length=50,
        blank=True
    )

    primary_button_url = models.URLField(
        blank=True
    )

    secondary_button_text = models.CharField(
        max_length=50,
        blank=True
    )

    secondary_button_url = models.URLField(
    blank=True
    )

    display_order = models.PositiveIntegerField(
        default=0
    )

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order"]

    def __str__(self):
        return self.title



# How it works
class ProcessStep(models.Model):
    step_number = models.PositiveIntegerField()

    title = models.CharField(
        max_length=120
    )

    description = models.TextField()

    icon = models.CharField(
        max_length=100,
        help_text="Font Awesome class"
    )

    is_active = models.BooleanField(
        default=True
    )

    class Meta:
        ordering = ["step_number"]

    def __str__(self):
        return f"Step {self.step_number}: {self.title}"