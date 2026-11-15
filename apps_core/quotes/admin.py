from django import forms
from django.contrib import admin
from django.contrib.admin.utils import unquote
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import QuoteRequest
from .services import change_quote_status, prepare_quotation


class QuoteRequestAdminForm(forms.ModelForm):
    class Meta:
        model = QuoteRequest
        fields = (
            "status",
            "internal_notes",
            "proposed_amount",
            "quote_valid_until",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "status" in self.fields:
            self.fields["status"].help_text = (
                "Choose the next workflow status. Invalid transitions are rejected."
            )
        if "proposed_amount" in self.fields:
            self.fields["proposed_amount"].help_text = (
                "Enter a positive amount and future expiry while under review "
                "to prepare the quotation."
            )

    def clean(self):
        return super().clean()


@admin.register(QuoteRequest)
class QuoteRequestAdmin(admin.ModelAdmin):
    form = QuoteRequestAdminForm

    list_display = (
        "id",
        "full_name",
        "email",
        "phone",
        "service",
        "status",
        "estimated_price",
        "proposed_amount",
        "created_at",
    )
    list_filter = (
        "status",
        "service",
        ("created_at", admin.DateFieldListFilter),
    )
    search_fields = (
        "=id",
        "full_name",
        "email",
        "phone",
        "customer__full_name",
        "customer__email",
        "customer__phone",
    )
    ordering = ("-created_at",)
    actions = None

    fieldsets = (
        (
            "Customer-submitted request",
            {
                "fields": (
                    "full_name",
                    "email",
                    "phone",
                    "customer",
                    "service",
                    "property_size",
                    "rooms",
                    "bathrooms",
                    "extras",
                    "address",
                    "city",
                    "preferred_date",
                    "preferred_time",
                    "estimated_price",
                    "created_at",
                )
            },
        ),
        (
            "Staff review",
            {
                "fields": (
                    "status",
                    "internal_notes",
                    "proposed_amount",
                    "quote_valid_until",
                    "quote_prepared_at",
                    "quote_sent_at",
                    "updated_at",
                )
            },
        ),
    )

    customer_readonly_fields = (
        "full_name",
        "email",
        "phone",
        "customer",
        "service",
        "property_size",
        "rooms",
        "bathrooms",
        "extras",
        "address",
        "city",
        "preferred_date",
        "preferred_time",
        "estimated_price",
        "created_at",
        "quote_prepared_at",
        "quote_sent_at",
        "updated_at",
    )

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(self.customer_readonly_fields)
        if obj is None or obj.status != "under_review":
            readonly_fields.extend(("proposed_amount", "quote_valid_until"))
        return tuple(readonly_fields)

    def get_form(self, request, obj=None, change=False, **kwargs):
        form_class = super().get_form(
            request,
            obj,
            change=change,
            **kwargs,
        )
        lifecycle_errors = getattr(request, "_quote_lifecycle_errors", None)
        if not lifecycle_errors:
            return form_class

        class QuoteRequestAdminFormWithLifecycleErrors(form_class):
            def clean(self):
                cleaned_data = super().clean()
                for field, messages in lifecycle_errors:
                    for message in messages:
                        self.add_error(
                            field if field in self.fields else None,
                            message,
                        )
                return cleaned_data

        return QuoteRequestAdminFormWithLifecycleErrors

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changeform_view(
        self,
        request,
        object_id=None,
        form_url="",
        extra_context=None,
    ):
        if request.method != "POST" or object_id is None:
            return super().changeform_view(
                request,
                object_id,
                form_url,
                extra_context,
            )

        quote = self.get_object(request, unquote(object_id))
        if quote is None or not self.has_change_permission(request, quote):
            return super().changeform_view(
                request,
                object_id,
                form_url,
                extra_context,
            )

        form_class = self.get_form(request, quote, change=True)
        form = form_class(
            request.POST,
            request.FILES,
            instance=quote,
        )
        if not form.is_valid():
            return super().changeform_view(
                request,
                object_id,
                form_url,
                extra_context,
            )

        try:
            with transaction.atomic():
                self._apply_lifecycle_change(quote, form.cleaned_data)
                return super().changeform_view(
                    request,
                    object_id,
                    form_url,
                    extra_context,
                )
        except ValidationError as error:
            request._quote_lifecycle_errors = self._form_errors(error)
            return super().changeform_view(
                request,
                object_id,
                form_url,
                extra_context,
            )

    @staticmethod
    def _form_errors(error):
        if hasattr(error, "error_dict"):
            return tuple(
                (field, tuple(item.message for item in errors))
                for field, errors in error.error_dict.items()
            )
        return ((None, tuple(error.messages)),)

    @staticmethod
    def _apply_lifecycle_change(quote, cleaned_data):
        current = QuoteRequest.objects.get(pk=quote.pk)
        new_status = cleaned_data["status"]
        proposed_amount = cleaned_data.get("proposed_amount")
        quote_valid_until = cleaned_data.get("quote_valid_until")
        preparation_changed = (
            proposed_amount != current.proposed_amount
            or quote_valid_until != current.quote_valid_until
        )

        if (
            current.status == "under_review"
            and new_status == "cancelled"
            and preparation_changed
        ):
            raise ValidationError(
                {
                    "status": (
                        "A request cannot be cancelled while quotation "
                        "details are being changed. Submit either action "
                        "separately."
                    )
                }
            )

        if current.status == "under_review" and (
            new_status == "prepared" or preparation_changed
        ):
            prepare_quotation(
                current,
                proposed_amount,
                quote_valid_until,
            )
        elif new_status != current.status:
            change_quote_status(current, new_status)

    def save_model(self, request, obj, form, change):
        if not change:
            return

        obj.save(update_fields=("internal_notes", "updated_at"))
