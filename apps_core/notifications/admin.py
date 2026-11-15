from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied

from .models import QuoteStatusNotification
from .services import is_stale_sending, retry_quote_status_notification


@admin.register(QuoteStatusNotification)
class QuoteStatusNotificationAdmin(admin.ModelAdmin):
    list_display = (
        "quote_reference",
        "event",
        "recipient",
        "status",
        "stale_sending",
        "attempt_count",
        "last_attempt_at",
        "sent_at",
        "retrying_by",
        "created_at",
        "short_last_error",
    )
    list_filter = (
        "status",
        "event",
    )
    search_fields = (
        "quote_reference",
        "recipient",
        "event_key",
    )
    ordering = ("-created_at",)
    actions = ("retry_selected_notifications",)
    readonly_fields = (
        "id",
        "quote",
        "quote_reference",
        "event",
        "event_key",
        "recipient",
        "subject",
        "text_body",
        "html_body",
        "status",
        "attempt_count",
        "last_attempt_at",
        "sent_at",
        "last_error",
        "retrying_by",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            "Quote",
            {
                "fields": (
                    "id",
                    "quote",
                    "quote_reference",
                    "event",
                    "event_key",
                )
            },
        ),
        (
            "Message snapshot",
            {
                "fields": (
                    "recipient",
                    "subject",
                    "text_body",
                    "html_body",
                )
            },
        ),
        (
            "Delivery",
            {
                "fields": (
                    "status",
                    "attempt_count",
                    "last_attempt_at",
                    "sent_at",
                    "last_error",
                    "retrying_by",
                )
            },
        ),
        (
            "Timestamps",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    @admin.display(description="stale sending", boolean=True)
    def stale_sending(self, obj):
        return is_stale_sending(obj)

    @admin.display(description="last error")
    def short_last_error(self, obj):
        error = obj.last_error or ""
        if len(error) > 80:
            return f"{error[:77]}..."
        return error

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        return

    def save_related(self, request, form, formsets, change):
        return

    def changeform_view(
        self,
        request,
        object_id=None,
        form_url="",
        extra_context=None,
    ):
        extra_context = extra_context or {}
        extra_context.update(
            {
                "show_save": False,
                "show_save_and_continue": False,
                "show_save_and_add_another": False,
            }
        )
        return super().changeform_view(
            request,
            object_id,
            form_url,
            extra_context,
        )

    @admin.action(description="Retry selected notifications")
    def retry_selected_notifications(self, request, queryset):
        succeeded = 0
        unsuccessful = 0
        for notification_id in queryset.values_list("pk", flat=True):
            try:
                retried = retry_quote_status_notification(
                    notification_id,
                    request.user,
                )
            except PermissionDenied:
                unsuccessful += 1
                continue
            if retried:
                succeeded += 1
            else:
                unsuccessful += 1

        level = messages.SUCCESS if unsuccessful == 0 else messages.WARNING
        self.message_user(
            request,
            (
                f"{succeeded} notification(s) retried. "
                f"{unsuccessful} notification(s) could not be retried."
            ),
            level=level,
        )
