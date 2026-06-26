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