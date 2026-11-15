from django.conf import settings

from apps_core.notifications.email import send_template_email

from .models import ContactEnquiry


CONTACT_EMAIL_SUBJECT = "New message from the CLEANEX contact form"
CONTACT_ACKNOWLEDGEMENT_EMAIL_SUBJECT = (
    "We've received your message - CLEANEX"
)


class ContactEmailConfigurationError(ValueError):
    pass


def create_contact_enquiry(*, name, email, message):
    return ContactEnquiry.objects.create(
        name=name,
        email=email,
        message=message,
    )


def send_contact_email(*, name, email, message):
    configured_recipient = getattr(
        settings,
        "CONTACT_RECIPIENT_EMAIL",
        "",
    )
    if (
        not isinstance(configured_recipient, str)
        or not configured_recipient.strip()
    ):
        raise ContactEmailConfigurationError(
            "The contact recipient email is not configured."
        )
    recipient = configured_recipient.strip()

    sent_count = send_template_email(
        subject=CONTACT_EMAIL_SUBJECT,
        text_template="emails/contact_submission.txt",
        recipient_list=[recipient],
        context={
            "name": name,
            "email": email,
            "message": message,
        },
    )
    if sent_count != 1:
        raise RuntimeError("The contact email was not accepted.")

    return sent_count


def send_contact_acknowledgement_email(*, name, email):
    sent_count = send_template_email(
        subject=CONTACT_ACKNOWLEDGEMENT_EMAIL_SUBJECT,
        text_template="emails/contact_acknowledgement.txt",
        recipient_list=[email],
        context={"name": name},
    )
    if sent_count != 1:
        raise RuntimeError(
            "The contact acknowledgement email was not accepted."
        )

    return sent_count
