from django.contrib import admin
from .models import Gallery, GalleryPhoto


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


@admin.register(GalleryPhoto)
class GalleryPhotoAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "service",
        "is_active",
        "display_order",
        "created_at",
    )
    list_editable = (
        "is_active",
        "display_order",
    )
    list_filter = (
        "service",
        "is_active",
    )
    search_fields = (
        "title",
        "service__name",
    )
    list_select_related = ("service",)
    ordering = ("display_order", "pk")
    autocomplete_fields = ("service",)
    readonly_fields = (
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            "Photo information",
            {
                "fields": (
                    "service",
                    "title",
                    "image",
                    "alt_text",
                    "caption",
                ),
            },
        ),
        (
            "Display settings",
            {
                "fields": (
                    "is_active",
                    "display_order",
                ),
            },
        ),
        (
            "Record dates",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )