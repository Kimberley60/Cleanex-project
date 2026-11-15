import re

from django import forms
from django.utils import timezone

from apps_core.services.models import Service

from .models import QuoteRequest
from .services import (
    MAX_BATHROOM_COUNT,
    MAX_PROPERTY_SIZE,
    MAX_ROOM_COUNT,
)


class QuoteRequestForm(forms.ModelForm):
    class Meta:
        model = QuoteRequest
        fields = (
            "full_name",
            "email",
            "phone",
            "service",
            "property_size",
            "rooms",
            "bathrooms",
            "extras",
            "address",
            "city",
            "preferred_date",
            "preferred_time",
        )
        widgets = {
            "full_name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "autocomplete": "name",
                    "placeholder": "Enter your full name",
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "class": "form-control",
                    "autocomplete": "email",
                    "placeholder": "you@example.com",
                }
            ),
            "phone": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "autocomplete": "tel",
                    "placeholder": "e.g. 0712 345 678",
                }
            ),
            "service": forms.Select(
                attrs={"class": "form-select"}
            ),
            "property_size": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": "1",
                    "placeholder": "e.g. 100",
                }
            ),
            "rooms": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": "1",
                    "placeholder": "e.g. 3",
                }
            ),
            "bathrooms": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": "1",
                    "placeholder": "e.g. 2",
                }
            ),
            "extras": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Tell us about any additional requirements",
                }
            ),
            "address": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "autocomplete": "street-address",
                    "placeholder": "Enter the property address",
                }
            ),
            "city": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "autocomplete": "address-level2",
                    "placeholder": "e.g. Nairobi",
                }
            ),
            "preferred_date": forms.DateInput(
                attrs={
                    "class": "form-control",
                    "type": "date",
                }
            ),
            "preferred_time": forms.TimeInput(
                attrs={
                    "class": "form-control",
                    "type": "time",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service"].queryset = Service.objects.filter(
            is_active=True
        )
        self.fields["service"].required = True
        self.fields["service"].empty_label = "Select a cleaning service"

        self.fields["property_size"].min_value = 1
        self.fields["property_size"].max_value = MAX_PROPERTY_SIZE
        self.fields["property_size"].widget.attrs["max"] = str(
            MAX_PROPERTY_SIZE
        )
        self.fields["rooms"].min_value = 1
        self.fields["rooms"].max_value = MAX_ROOM_COUNT
        self.fields["rooms"].widget.attrs["max"] = str(MAX_ROOM_COUNT)
        self.fields["bathrooms"].min_value = 1
        self.fields["bathrooms"].max_value = MAX_BATHROOM_COUNT
        self.fields["bathrooms"].widget.attrs["max"] = str(
            MAX_BATHROOM_COUNT
        )

        self.fields["address"].required = True
        self.fields["city"].required = True
        self.fields["preferred_date"].widget.attrs["min"] = (
            timezone.localdate().isoformat()
        )

    def clean_full_name(self):
        return " ".join(self.cleaned_data["full_name"].split())

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean_phone(self):
        phone = self.cleaned_data["phone"].strip()
        digits = re.sub(r"\D", "", phone)
        if (
            not re.fullmatch(r"\+?[0-9().\-\s]+", phone)
            or not 7 <= len(digits) <= 15
        ):
            raise forms.ValidationError(
                "Enter a valid phone number containing 7 to 15 digits."
            )
        return phone

    def clean_property_size(self):
        value = self.cleaned_data["property_size"]
        if not 1 <= value <= MAX_PROPERTY_SIZE:
            raise forms.ValidationError(
                f"Enter a value between 1 and {MAX_PROPERTY_SIZE}."
            )
        return value

    def clean_rooms(self):
        value = self.cleaned_data["rooms"]
        if not 1 <= value <= MAX_ROOM_COUNT:
            raise forms.ValidationError(
                f"Enter a value between 1 and {MAX_ROOM_COUNT}."
            )
        return value

    def clean_bathrooms(self):
        value = self.cleaned_data["bathrooms"]
        if not 1 <= value <= MAX_BATHROOM_COUNT:
            raise forms.ValidationError(
                f"Enter a value between 1 and {MAX_BATHROOM_COUNT}."
            )
        return value

    def clean_extras(self):
        return self.cleaned_data["extras"].strip()

    def clean_address(self):
        address = " ".join(self.cleaned_data["address"].split())
        if not address:
            raise forms.ValidationError("Enter the property address.")
        return address

    def clean_city(self):
        city = " ".join(self.cleaned_data["city"].split())
        if not city:
            raise forms.ValidationError("Enter the city.")
        return city

    def clean_preferred_date(self):
        preferred_date = self.cleaned_data.get("preferred_date")
        if (
            preferred_date is not None
            and preferred_date < timezone.localdate()
        ):
            raise forms.ValidationError(
                "Choose today or a future preferred date."
            )
        return preferred_date
