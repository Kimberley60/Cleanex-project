from django.contrib import admin
from .models import MpesaCallbackMetadata, Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "booking",
        "amount",
        "method",
        "status",
        "phone_number",
        "mpesa_receipt",
        "transaction_date",
        "created_at",
    )

    list_filter = (
        "status",
        "method",
        "created_at",
    )

    search_fields = (
        "booking__id",
        "booking__customer__full_name",
        "booking__customer__email",
        "phone_number",
        "checkout_request_id",
        "merchant_request_id",
        "mpesa_receipt",
    )

    readonly_fields = (
        "checkout_request_id",
        "merchant_request_id",
        "mpesa_receipt",
        "transaction_date",
        "result_code",
        "result_description",
        "callback_candidate_receipt",
        "callback_candidate_transaction_date",
        "callback_metadata_status",
        "callback_metadata_attempt_count",
        "callback_metadata_received_at",
        "callback_metadata_last_attempt_at",
        "callback_metadata_last_error",
        "callback_metadata_verified_at",
        "created_at",
        "updated_at",
    )

    ordering = (
        "-created_at",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "callback_metadata"
        )

    def _callback_metadata(self, obj):
        try:
            return obj.callback_metadata
        except MpesaCallbackMetadata.DoesNotExist:
            return None

    def _pending_callback_metadata(self, obj):
        metadata = self._callback_metadata(obj)
        if metadata is None or metadata.status != "pending":
            return None
        return metadata

    @admin.display(description="Candidate M-Pesa receipt (unverified)")
    def callback_candidate_receipt(self, obj):
        metadata = self._pending_callback_metadata(obj)
        if metadata is None or not metadata.mpesa_receipt:
            return self.get_empty_value_display()
        return metadata.mpesa_receipt

    @admin.display(description="Candidate transaction date (unverified)")
    def callback_candidate_transaction_date(self, obj):
        metadata = self._pending_callback_metadata(obj)
        if metadata is None or metadata.transaction_date is None:
            return self.get_empty_value_display()
        return metadata.transaction_date

    @admin.display(description="Callback metadata status")
    def callback_metadata_status(self, obj):
        metadata = self._callback_metadata(obj)
        if metadata is None:
            return self.get_empty_value_display()
        return metadata.get_status_display()

    @admin.display(description="Callback metadata attempts")
    def callback_metadata_attempt_count(self, obj):
        metadata = self._callback_metadata(obj)
        if metadata is None:
            return self.get_empty_value_display()
        return metadata.attempt_count

    @admin.display(description="Callback received at")
    def callback_metadata_received_at(self, obj):
        metadata = self._callback_metadata(obj)
        if metadata is None:
            return self.get_empty_value_display()
        return metadata.received_at

    @admin.display(description="Callback metadata last attempt")
    def callback_metadata_last_attempt_at(self, obj):
        metadata = self._callback_metadata(obj)
        if metadata is None or metadata.last_attempt_at is None:
            return self.get_empty_value_display()
        return metadata.last_attempt_at

    @admin.display(description="Callback metadata last error")
    def callback_metadata_last_error(self, obj):
        metadata = self._callback_metadata(obj)
        if metadata is None or not metadata.last_error:
            return self.get_empty_value_display()
        return metadata.last_error

    @admin.display(description="Callback metadata verified at")
    def callback_metadata_verified_at(self, obj):
        metadata = self._callback_metadata(obj)
        if metadata is None or metadata.verified_at is None:
            return self.get_empty_value_display()
        return metadata.verified_at

    def has_delete_permission(self, request, obj=None):
        return False
