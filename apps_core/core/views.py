from django.views.generic import TemplateView
from apps_core.core.models import WhyChooseSection, WhyChooseFeature, CompanyStatistic, TestimonialSection
from apps_core.services.models import Service
from apps_core.core.models import Testimonial
from apps_core.website.models import HeroSection, ProcessStep


class HomeView(TemplateView):
    template_name = "pages/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["services"] = Service.objects.filter(
            is_active=True,
            is_featured=True
        )[:6]


        context["hero_section"] = HeroSection.objects.filter(
            page="home",
            is_active=True
        ).first()

        # How It Works Steps
        context["process_steps"] = ProcessStep.objects.filter(
            is_active=True
        )

        context["why_choose"] =  WhyChooseSection.objects.filter(
            is_active=True

        ).first()

        context["why_features"] =  WhyChooseFeature.objects.filter(
            is_active=True
        )

        context["statistics"] =  CompanyStatistic.objects.filter(
            is_active=True
        )

        context["testimonial_section"] =  TestimonialSection.objects.filter(
            is_active=True

        ).first()

        context["testimonials"] = Testimonial.objects.filter(
                is_active=True,
                featured=True

        )

        return context

class ContactView(TemplateView):
    template_name = "pages/contact.html"



