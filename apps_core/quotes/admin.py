from django.contrib import admin
from .models import QuoteRequest


@admin.register(QuoteRequest)
class QuoteRequestAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "phone",
        "service",
        "property_size",
        "rooms",
        "bathrooms",
        "estimated_price",
        "created_at",
    )

    list_filter = (
        "service",
        "created_at",
    )

    search_fields = (
        "full_name",
        "phone",
        "email",
    )

    ordering = ("-created_at",)