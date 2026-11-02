from django.db import models
from apps_core.services.models import Service


class Gallery(models.Model):

    title = models.CharField(max_length=200)

    service = models.ForeignKey(
        Service,
        on_delete=models.SET_NULL,
        null=True
    )

    before_image = models.ImageField(upload_to="gallery/before/")
    after_image = models.ImageField(upload_to="gallery/after/")

    description = models.TextField(blank=True)

    is_featured = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class GalleryPhoto(models.Model):
    """An individual service photo displayed in the public gallery."""

    service = models.ForeignKey(
        Service,
        on_delete=models.PROTECT,
        related_name="gallery_photos",
    )
    title = models.CharField(max_length=200)
    image = models.ImageField(upload_to="gallery/photos/")
    caption = models.TextField(blank=True)
    alt_text = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "gallery photo"
        verbose_name_plural = "gallery photos"
        ordering = ["display_order", "pk"]

    def __str__(self):
        return self.title