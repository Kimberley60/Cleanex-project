from django import forms
from django.utils import timezone
from apps_core.services.models import Service, ExtraService


class BookingForm(forms.Form):
    """
    Public booking form.

    Customer information is collected here and used to create or
    update a Customer record.

    Booking-specific information is passed to the booking service
    layer after validation.
    """

    # ==========================================================
    # CUSTOMER INFORMATION
    # ==========================================================

    full_name = forms.CharField(
        max_length=150,
        label="Full Name",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Enter your full name",
            }
        ),
    )

    email = forms.EmailField(
        label="Email Address",
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "placeholder": "you@example.com",
            }
        ),
    )

    phone = forms.CharField(
        max_length=30,
        label="Phone Number",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "e.g. 0712 345 678",
            }
        ),
    )

    # ==========================================================
    # SERVICE
    # ==========================================================

    service = forms.ModelChoiceField(
        queryset=Service.objects.none(),
        label="Cleaning Service",
        empty_label="Select a cleaning service",
        widget=forms.Select(
            attrs={
                "class": "form-select",
            }
        ),
    )

    extras = forms.ModelMultipleChoiceField(
        queryset=ExtraService.objects.none(),
        required=False,
        label="Additional Services",
        widget=forms.CheckboxSelectMultiple(
            attrs={
                "class": "booking-extra-options",
            }
        ),
    )

    # ==========================================================
    # DATE & TIME
    # ==========================================================

    booking_date = forms.DateField(
        label="Preferred Date",
        widget=forms.DateInput(
            attrs={
                "class": "form-control",
                "type": "date",
            }
        ),
    )

    booking_time = forms.TimeField(
        label="Preferred Time",
        widget=forms.TimeInput(
            attrs={
                "class": "form-control",
                "type": "time",
            }
        ),
    )

    # ==========================================================
    # PROPERTY INFORMATION
    # ==========================================================

    property_size = forms.IntegerField(
        required=False,
        min_value=1,
        label="Property Size",
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "placeholder": "e.g. 100",
                "min": "1",
            }
        ),
    )

    rooms = forms.IntegerField(
        required=False,
        min_value=1,
        label="Number of Rooms",
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "placeholder": "e.g. 3",
                "min": "1",
            }
        ),
    )

    # ==========================================================
    # LOCATION
    # ==========================================================

    address = forms.CharField(
        max_length=255,
        label="Service Address",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Enter the service address",
            }
        ),
    )

    city = forms.CharField(
        max_length=100,
        label="City",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "e.g. Nairobi",
            }
        ),
    )

    # ==========================================================
    # NOTES
    # ==========================================================

    notes = forms.CharField(
        required=False,
        label="Additional Notes",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": "Tell us anything we should know about the cleaning job.",
            }
        ),
    )

    def __init__(self, *args, selected_service=None, **kwargs):
        super().__init__(*args, **kwargs)

        # Only active services should be bookable.
        self.fields["service"].queryset = Service.objects.filter(
            is_active=True
        )

        # Only active extras should be selectable.
        self.fields["extras"].queryset = ExtraService.objects.filter(
            is_active=True
        )

        # Allow the service detail page to preselect a service.
        if selected_service:
            self.fields["service"].initial = selected_service

        # Prevent selecting dates in the past.
        self.fields["booking_date"].widget.attrs["min"] = (
            timezone.localdate().isoformat()
        )

    def clean_booking_date(self):
        booking_date = self.cleaned_data["booking_date"]

        if booking_date < timezone.localdate():
            raise forms.ValidationError(
                "Please select today or a future date."
            )

        return booking_date



    def clean(self):
        cleaned_data = super().clean()

        booking_date = cleaned_data.get("booking_date")
        booking_time = cleaned_data.get("booking_time")
        service = cleaned_data.get("service")
        city = cleaned_data.get("city")

        if booking_date and booking_time and service and city:
            from .services import get_availability_status

            availability_status = get_availability_status(
                date=booking_date,
                time=booking_time,
                city=city,
                duration_hours=service.duration_hours,
            )

            if availability_status == "no_city_staff":
                self.add_error(
                    "city",
                    "CLEANEX currently does not have an available "
                    "cleaner serving this city. Please choose another city."
                )

            elif availability_status == "time_unavailable":
                self.add_error(
                    "booking_time",
                    "Our cleaners are currently unavailable at this "
                    "date and time. Please choose another date or time."
                )

        return cleaned_data




class BookingTrackingForm(forms.Form):
    """
    Form used by customers to securely locate an existing booking.
    """

    booking_reference = forms.CharField(
        max_length=20,
        label="Booking Reference",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "e.g. CLX-000123",
                "autocomplete": "off",
            }
        ),
    )

    email = forms.EmailField(
        label="Email Address",
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "placeholder": "you@example.com",
                "autocomplete": "email",
            }
        ),
    )

    def clean_booking_reference(self):
        reference = self.cleaned_data["booking_reference"].strip().upper()

        if not reference.startswith("CLX-"):
            raise forms.ValidationError(
                "Enter a valid CLEANEX booking reference."
            )
        reference_number = reference[4:]

        if (
                len(reference_number) != 6
                or not reference_number.isdigit()
        ):

            raise forms.ValidationError(
                    "Enter a valid CLEANEX booking reference."
                )

        return reference