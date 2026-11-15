import logging

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.generic import TemplateView
from apps_core.services.models import Service
from apps_core.website.models import HeroSection

from .forms import ContactForm
from .rate_limit import CONTACT_RATE_LIMITS, rate_limited
from .services import (
    create_contact_enquiry,
    send_contact_acknowledgement_email,
    send_contact_email,
)


logger = logging.getLogger(__name__)


def health_check(request):
    return JsonResponse({"status": "ok"})


class HomeView(TemplateView):
    template_name = "pages/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Page-specific: featured services
        context["services"] = Service.objects.filter(
            is_active=True,
            is_featured=True,
        )[:6]

        # Page-specific: home hero
        context["hero_section"] = HeroSection.objects.filter(
            page="home",
            is_active=True,
        ).first()

        return context


class ContactView(TemplateView):
    template_name = "pages/contact.html"

    def get(self, request, *args, **kwargs):
        return self.render_to_response({"form": ContactForm()})

    @rate_limited(*CONTACT_RATE_LIMITS)
    def post(self, request, *args, **kwargs):
        form = ContactForm(request.POST)
        if not form.is_valid():
            return self.render_to_response({"form": form})

        create_contact_enquiry(
            name=form.cleaned_data["name"],
            email=form.cleaned_data["email"],
            message=form.cleaned_data["message"],
        )

        try:
            send_contact_email(
                name=form.cleaned_data["name"],
                email=form.cleaned_data["email"],
                message=form.cleaned_data["message"],
            )
        except Exception as error:
            logger.warning(
                "Contact form business notification failed (%s).",
                type(error).__name__,
            )

        try:
            send_contact_acknowledgement_email(
                name=form.cleaned_data["name"],
                email=form.cleaned_data["email"],
            )
        except Exception as error:
            logger.warning(
                "Contact form acknowledgement email failed (%s).",
                type(error).__name__,
            )

        messages.success(
            request,
            "Thank you. Your enquiry has been received. Our team will review "
            "it and get back to you as soon as possible.",
        )
        return redirect("contact")
