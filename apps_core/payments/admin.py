from django.contrib import admin
from .models import Payment


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
        "created_at",
        "updated_at",
    )

    ordering = (
        "-created_at",
    )

