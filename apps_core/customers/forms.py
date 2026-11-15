from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm
from django.contrib.auth.password_validation import (
    password_validators_help_text_html,
    validate_password,
)
from django.core.exceptions import ValidationError

from .models import normalize_customer_email


User = get_user_model()


class CustomerRegistrationForm(forms.Form):
    first_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "given-name"}),
    )
    last_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "family-name"}),
    )
    email = forms.EmailField(
        max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    phone = forms.CharField(
        max_length=20,
        widget=forms.TelInput(attrs={"autocomplete": "tel"}),
    )
    password1 = forms.CharField(
        label="Password",
        strip=False,
        help_text=password_validators_help_text_html(),
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password"}
        ),
    )
    password2 = forms.CharField(
        label="Confirm password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password"}
        ),
    )

    def clean_email(self):
        email = normalize_customer_email(self.cleaned_data["email"])
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "An account with this email address already exists."
            )
        return email

    def clean_phone(self):
        phone = self.cleaned_data["phone"].strip()
        if not phone:
            raise forms.ValidationError("Enter a phone number.")
        return phone

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")

        if password1 and password2 and password1 != password2:
            self.add_error("password2", "The passwords do not match.")

        if password1 and not self.has_error("password2"):
            user = User(
                first_name=cleaned_data.get("first_name", ""),
                last_name=cleaned_data.get("last_name", ""),
                email=cleaned_data.get("email", ""),
            )
            try:
                validate_password(password1, user=user)
            except ValidationError as error:
                self.add_error("password1", error)

        return cleaned_data


class CustomerAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(
            attrs={"autofocus": True, "autocomplete": "email"}
        ),
    )

    def clean(self):
        email = self.cleaned_data.get("username", "").strip()
        password = self.cleaned_data.get("password")

        if email and password:
            matching_users = User.objects.filter(
                email__iexact=email
            ).order_by("pk")
            if matching_users.count() == 1:
                self.cleaned_data["username"] = (
                    matching_users.get().get_username()
                )
            else:
                self.cleaned_data["username"] = ""

        return super().clean()


class CustomerPasswordResetForm(PasswordResetForm):
    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if User.objects.filter(email__iexact=email).count() > 1:
            raise forms.ValidationError(
                "We could not safely identify a single account for this email."
            )
        return email


class CustomerProfileForm(forms.Form):
    first_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "given-name"}),
    )
    last_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "family-name"}),
    )
    phone = forms.CharField(
        max_length=20,
        widget=forms.TelInput(attrs={"autocomplete": "tel"}),
    )
    county = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "address-level1"}),
    )
    city = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={"autocomplete": "address-level2"}),
    )
    address = forms.CharField(
        max_length=255,
        label="Default address",
        widget=forms.TextInput(attrs={"autocomplete": "street-address"}),
    )

    def __init__(self, *args, user, customer, **kwargs):
        initial = kwargs.pop("initial", {})
        initial.update(
            {
                "first_name": user.first_name,
                "last_name": user.last_name,
                "phone": customer.phone,
                "county": customer.county,
                "city": customer.city,
                "address": customer.address,
            }
        )
        super().__init__(*args, initial=initial, **kwargs)

    def clean_phone(self):
        phone = self.cleaned_data["phone"].strip()
        if not phone:
            raise forms.ValidationError("Enter a phone number.")
        return phone

    def clean(self):
        cleaned_data = super().clean()
        for field in ("first_name", "last_name", "city", "address"):
            value = cleaned_data.get(field)
            if value is not None and not value.strip():
                self.add_error(field, "This field is required.")
        return cleaned_data


class ResendVerificationForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"autocomplete": "email"})
    )

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()
