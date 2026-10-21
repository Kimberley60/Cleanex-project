from django.contrib import admin
from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):

    list_display = (
        "full_name",
        "email",
        "phone",
        "city",
        "is_repeat_customer",
        "created_at",
    )

    list_filter = (
        "city",
        "is_repeat_customer",
        "created_at",
    )

    search_fields = (
        "full_name",
        "email",
        "phone",
        "city",
    )

    ordering = (
        "-created_at",
    )

    readonly_fields = (
        "created_at",
    )

    fieldsets = (
        (
            "Customer Information",
            {
                "fields": (
                    "full_name",
                    "email",
                    "phone",
                )
            },
        ),
        (
            "Location",
            {
                "fields": (
                    "address",
                    "city",
                )
            },
        ),
        (
            "Customer Status",
            {
                "fields": (
                    "is_repeat_customer",
                )
            },
        ),
        (
            "Account",
            {
                "fields": (
                    "user",
                )
            },
        ),
        (
            "Additional Information",
            {
                "fields": (
                    "notes",
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
