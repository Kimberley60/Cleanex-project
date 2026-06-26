from django.contrib import admin
from .models import Customer


# Register your models here.

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "phone",
        "city",
        "created_at",
    )

    search_fields = (
        "user__username",
        "user__email",
        "phone",
    )

    list_filter = (
        "city",
        "created_at",
    )

    ordering = ("-created_at",)