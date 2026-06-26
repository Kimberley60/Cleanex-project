from django.contrib import admin
from .models import Staff


@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "phone",
        "role",
        "city",
        "is_available",
        "is_active",
        "created_at",
    )

    list_filter = (
        "role",
        "city",
        "is_available",
        "is_active",
    )

    search_fields = (
        "full_name",
        "phone",
    )

    ordering = ("full_name",)