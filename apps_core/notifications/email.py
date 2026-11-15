from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.template.loader import render_to_string


def send_template_email(
    *,
    subject,
    text_template=None,
    recipient_list,
    context=None,
    html_template=None,
    from_email=None,
    text_body=None,
    html_body=None,
):
    """
    Render and send a template-based email or send saved rendered bodies.

    Returns the number of messages accepted by Django's email backend.
    Delivery and rendering errors are allowed to propagate to the caller.
    """

    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("A non-empty email subject is required.")

    if (
        not isinstance(recipient_list, (list, tuple))
        or not recipient_list
        or any(
            not isinstance(address, str) or not address.strip()
            for address in recipient_list
        )
    ):
        raise ValueError("At least one valid recipient address is required.")

    # Validate the format of every recipient email address.
    try:
        for address in recipient_list:
            validate_email(address.strip())
    except ValidationError as error:
        raise ValueError(
            "A valid recipient email address is required."
        ) from error

    if context is None:
        context = {}

    if not isinstance(context, dict):
        raise ValueError("Email context must be a dictionary.")

    if text_body is None:
        if not isinstance(text_template, str) or not text_template.strip():
            raise ValueError("A plain-text email template is required.")
        if html_body is not None:
            raise ValueError("An HTML body requires a plain-text body.")
        if html_template is not None and (
            not isinstance(html_template, str) or not html_template.strip()
        ):
            raise ValueError("The HTML template must be a non-empty string.")
    else:
        if not isinstance(text_body, str):
            raise ValueError("A plain-text email body is required.")
        if text_template is not None or html_template is not None:
            raise ValueError("Use templates or rendered bodies, not both.")
        if html_body is not None and not isinstance(html_body, str):
            raise ValueError("The HTML email body must be a string.")

    sender = from_email or settings.DEFAULT_FROM_EMAIL

    if not isinstance(sender, str) or not sender.strip():
        raise ValueError("A valid sender address is required.")

    if text_body is None:
        text_body = render_to_string(text_template, context)

    email = EmailMultiAlternatives(
        subject=subject.strip(),
        body=text_body,
        from_email=sender.strip(),
        to=[address.strip() for address in recipient_list],
    )

    if html_template:
        html_body = render_to_string(html_template, context)
    if html_body:
        email.attach_alternative(html_body, "text/html")

    return email.send(fail_silently=False)
