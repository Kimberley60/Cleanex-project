from django.contrib import admin, messages
from .models import Booking, BookingExtra
from .services import change_booking_status



class BookingExtraInline(admin.TabularInline):
    model = BookingExtra
    extra = 1
    autocomplete_fields = ("extra_service",)


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):

    list_display = (
        "id",
        "customer",
        "service",
        "booking_date",
        "booking_time",
        "city",
        "staff",
        "total_price",
        "status",
        "created_at",
    )

    list_filter = (
        "status",
        "service",
        "city",
        "booking_date",
        "created_at",
    )

    search_fields = (
        "customer__full_name",
        "customer__email",
        "customer__phone",
        "contact_name",
        "contact_email",
        "contact_phone",
        "service__name",
        "city",
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

    readonly_fields = (
        "status",
        "created_at",
        "updated_at",
    )

    actions = (
        "confirm_bookings",
        "start_bookings",
        "complete_bookings",
        "cancel_bookings",
    )

    fieldsets = (
        (
            "Booking Information",
            {
                "fields": (
                    "customer",
                    "service",
                    "staff",
                    "status",
                )
            },
        ),

        (
            "Booking Contact",
            {
                "fields": (
                    "contact_name",
                    "contact_email",
                    "contact_phone",
                )
            },
        ),

        (
            "Schedule",
            {
                "fields": (
                    "booking_date",
                    "booking_time",
                )
            },
        ),
        (
            "Property Information",
            {
                "fields": (
                    "property_size",
                    "rooms",
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
            "Pricing",
            {
                "fields": (
                    "total_price",
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
                    "updated_at",
                )
            },
        ),
    )

    inlines = [
        BookingExtraInline,
    ]

    @admin.action(description="Confirm selected bookings")
    def confirm_bookings(self, request, queryset):
        self._change_status(
            request,
            queryset,
            "confirmed",
            "confirmed",
        )

    @admin.action(description="Start selected bookings")
    def start_bookings(self, request, queryset):
        self._change_status(
            request,
            queryset,
            "in_progress",
            "started",
        )

    @admin.action(description="Complete selected bookings")
    def complete_bookings(self, request, queryset):
        self._change_status(
            request,
            queryset,
            "completed",
            "completed",
        )

    @admin.action(description="Cancel selected bookings")
    def cancel_bookings(self, request, queryset):
        self._change_status(
            request,
            queryset,
            "cancelled",
            "cancelled",
        )

    def _change_status(
        self,
        request,
        queryset,
        new_status,
        success_word,
    ):
        success_count = 0
        error_count = 0

        for booking in queryset:
            try:
                change_booking_status(booking, new_status)
                success_count += 1
            except ValueError as error:
                error_count += 1
                self.message_user(
                    request,
                    f"Booking #{booking.pk}: {error}",
                    level=messages.ERROR,
                )

        if success_count:
            self.message_user(
                request,
                f"{success_count} booking(s) successfully {success_word}.",
                level=messages.SUCCESS,
            )

        if error_count:
            self.message_user(
                request,
                f"{error_count} booking(s) could not be updated.",
                level=messages.WARNING,
            )


@admin.register(BookingExtra)
class BookingExtraAdmin(admin.ModelAdmin):

    list_display = (
        "booking",
        "extra_service",
    )

    list_filter = (
        "extra_service",
    )

    search_fields = (
        "booking__customer__full_name",
        "booking__customer__email",
        "extra_service__name",
    )

    autocomplete_fields = (
        "booking",
        "extra_service",
    )