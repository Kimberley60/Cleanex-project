from django.db.models import Q
from django.views.generic import ListView, DetailView
from apps_core.services.models import Service, ServiceCategory
from apps_core.website.models import HeroSection


class ServiceListView(ListView):
    model = Service
    template_name = "pages/services.html"
    context_object_name = "services"

    def get_category_queryset(self):
        return ServiceCategory.objects.filter(
            is_active=True,
        ).order_by(
            "display_order",
            "name",
        )

    def get_requested_category_slug(self):
        return (
            self.request.GET.get("category") or ""
        ).strip()

    def get_requested_search_query(self):
        return (
            self.request.GET.get("search") or ""
        ).strip()

    def get_service_queryset(self):
        queryset = (
            Service.objects
            .filter(
                is_active=True,
            )
            .select_related(
                "category",
            )
        )

        return self.apply_service_filters(queryset)

    def apply_service_filters(self, queryset):
        category_slug = self.get_requested_category_slug()
        search_query = self.get_requested_search_query()

        # Category filtering
        if category_slug:
            queryset = queryset.filter(
                category__slug=category_slug,
                category__is_active=True,
            )

        # Search filtering
        if search_query:
            queryset = queryset.filter(
                Q(name__icontains=search_query)
                | Q(short_description__icontains=search_query)
                | Q(description__icontains=search_query)
            )

        return queryset

    def get_queryset(self):
        return self.get_service_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        category_slug = self.get_requested_category_slug()
        search_query = self.get_requested_search_query()

        categories = self.get_category_queryset()

        # Page-specific: service categories
        context["categories"] = categories

        # Page-specific: active category
        context["active_category"] = category_slug

        # Page-specific: search value
        context["search_query"] = search_query

        # Page-specific: filtered result count
        context["result_count"] = context["services"].count()

        # Page-specific: selected category object
        context["selected_category"] = (
            categories.filter(
                slug=category_slug,
            ).first()
            if category_slug
            else None
        )

        # Page-specific: services hero
        context["hero_section"] = HeroSection.objects.filter(
            page="services",
            is_active=True,
        ).first()

        return context





class ServiceDetailView(DetailView):
    model = Service
    template_name = "services/service_detail.html"
    context_object_name = "service"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        return (
            Service.objects
            .filter(
                is_active=True,
            )
            .select_related(
                "category",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        service = self.object

        context["service_features"] = (
            service.features
            .filter(
                is_active=True,
            )
            .order_by(
                "display_order",
                "id",
            )
        )

        context["service_faqs"] = (
            service.faqs
            .filter(
                is_active=True,
            )
            .order_by(
                "display_order",
                "id",
            )
        )

        if service.category:
            context["related_services"] = (
                Service.objects
                .filter(
                    is_active=True,
                    category=service.category,
                )
                .exclude(
                    pk=service.pk,
                )
                .select_related(
                    "category",
                )[:3]
            )
        else:
            context["related_services"] = Service.objects.none()

        return context


