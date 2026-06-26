from django.contrib import admin
from .models import Gallery


@admin.register(Gallery)
class GalleryAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "service",
        "created_at",
    )

    list_filter = (
        "service",
    )

    search_fields = (
        "title",
    )

    ordering = ("-created_at",)