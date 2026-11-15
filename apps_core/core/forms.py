from django import forms


class ContactForm(forms.Form):
    name = forms.CharField(
        max_length=100,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "name",
                "class": "contact-input",
            }
        ),
    )
    email = forms.EmailField(
        max_length=254,
        widget=forms.EmailInput(
            attrs={
                "autocomplete": "email",
                "class": "contact-input",
            }
        ),
    )
    message = forms.CharField(
        max_length=5000,
        strip=True,
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "class": "contact-input contact-message",
            }
        ),
    )

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Please enter your name.")
        return name

    def clean_email(self):
        return self.cleaned_data["email"].strip()

    def clean_message(self):
        message = self.cleaned_data["message"].strip()
        if not message:
            raise forms.ValidationError("Please enter a message.")
        return message
