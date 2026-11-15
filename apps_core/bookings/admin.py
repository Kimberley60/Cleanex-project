from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from .models import Booking, BookingExtra
from .services import change_booking_status


class BookingExtraAdminForm(forms.ModelForm):
    class Meta:
        model = BookingExtra
        fields = ("booking", "extra_service")

    def clean(self):
        cleaned_data = super().clean()
        if self.instance.pk:
            original_relationships = (
                BookingExtra.objects
                .filter(pk=self.instance.pk)
                .values_list("booking_id", "extra_service_id")
                .first()
            )
            if original_relationships is None:
                raise ValidationError(
                    "This historical extra association no longer exists."
                )

            original_booking_id, original_extra_service_id = (
                original_relationships
            )
            if (
                "booking" in cleaned_data
                and cleaned_data["booking"].pk != original_booking_id
            ):
                self.add_error(
                    "booking",
                    "An existing booking extra cannot be moved.",
                )
            if (
                "extra_service" in cleaned_data
                and cleaned_data["extra_service"].pk
                != original_extra_service_id
            ):
                self.add_error(
                    "extra_service",
                    "An existing booking extra cannot be reassigned.",
                )

        return cleaned_data


class BookingExtraInline(admin.TabularInline):
    model = BookingExtra
    form = BookingExtraAdminForm
    extra = 1
    can_delete = False
    autocomplete_fields = ("extra_service",)
    readonly_fields = (
        "extra_service_name",
        "extra_service_price",
    )

    def has_delete_permission(self, request, obj=None):
        return False



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
        "service",
        "staff",
        "booking_date",
        "booking_time",
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

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False

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
    form = BookingExtraAdminForm

    list_display = (
        "booking",
        "extra_service",
        "extra_service_name",
        "extra_service_price",
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

    readonly_fields = (
        "extra_service_name",
        "extra_service_price",
    )

    def has_delete_permission(self, request, obj=None):
        return False
