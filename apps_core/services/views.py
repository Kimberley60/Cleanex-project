from django.views.generic import ListView, DetailView
from apps_core.services.models import Service
from apps_core.website.models import HeroSection


class ServiceListView(ListView):
    model = Service
    template_name = "services/service_list.html"
    context_object_name = "services"

    def get_queryset(self):
        return Service.objects.filter(
            is_active=True
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["hero_section"] = HeroSection.objects.filter(
            page="services",
            is_active=True
        ).first()

        return context



class ServiceDetailView(DetailView):
    model = Service
    template_name = "services/service_detail.html"
    context_object_name = "service"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        return Service.objects.filter(
            is_active=True
        )