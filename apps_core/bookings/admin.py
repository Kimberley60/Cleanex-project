from django.contrib import admin
from .models import Booking, BookingExtra


class BookingExtraInline(admin.TabularInline):
    model = BookingExtra
    extra = 1


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "customer",
        "service",
        "booking_date",
        "booking_time",
        "city",
        "status",
        "staff",
        "total_price",
    )

    list_filter = (
        "status",
        "city",
        "booking_date",
        "service",
    )

    search_fields = (
        "customer__full_name",
        "customer__phone",
        "address",
    )

    date_hierarchy = "booking_date"

    ordering = (
        "-booking_date",
        "-booking_time",
    )

    autocomplete_fields = (
        "customer",
        "service",
        "staff",
    )

    inlines = [BookingExtraInline]