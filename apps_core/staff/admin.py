from django.contrib import admin
from .models import Staff


@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):

    list_display = (
        "full_name",
        "role",
        "phone",
        "city",
        "is_available",
        "is_active",
        "hire_date",
        "created_at",
    )

    list_filter = (
        "role",
        "city",
        "is_available",
        "is_active",
        "hire_date",
    )

    search_fields = (
        "full_name",
        "phone",
        "city",
    )

    ordering = (
        "full_name",
    )

    list_editable = (
        "is_available",
        "is_active",
    )

    readonly_fields = (
        "created_at",
    )

    fieldsets = (
        (
            "Staff Information",
            {
                "fields": (
                    "full_name",
                    "phone",
                    "role",
                )
            },
        ),
        (
            "Work Location",
            {
                "fields": (
                    "city",
                )
            },
        ),
        (
            "Availability",
            {
                "fields": (
                    "is_available",
                    "is_active",
                )
            },
        ),
        (
            "Employment",
            {
                "fields": (
                    "hire_date",
                )
            },
        ),
        (
            "System Information",
            {
                "fields": (
                    "created_at",
                )
            },
        ),
    )