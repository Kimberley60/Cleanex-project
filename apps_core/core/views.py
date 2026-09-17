from django.views.generic import TemplateView
from apps_core.services.models import Service
from apps_core.website.models import HeroSection


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

