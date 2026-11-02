from django.core.paginator import Paginator
from django.utils.http import urlencode
from django.views.generic import ListView

from apps_core.services.models import Service, ServiceCategory
from .models import Gallery, GalleryPhoto


class GalleryView(ListView):
    model = GalleryPhoto
    template_name = "pages/gallery.html"
    context_object_name = "photos"
    paginate_by = 12

    def paginate_queryset(self, queryset, page_size):
        paginator = Paginator(queryset, page_size)
        page_obj = paginator.get_page(self.request.GET.get("page"))
        return paginator, page_obj, page_obj.object_list, page_obj.has_other_pages()

    def get_queryset(self):
        queryset = (
            GalleryPhoto.objects
            .filter(is_active=True)
            .select_related("service", "service__category")
            .order_by("display_order", "pk")
        )

        service = self.get_selected_service()
        category = self.get_selected_category()

        if service:
            queryset = queryset.filter(service=service)
        if category:
            queryset = queryset.filter(service__category=category)

        return queryset

    def get_selected_service(self):
        if hasattr(self, "_selected_service"):
            return self._selected_service

        service_slug = (self.request.GET.get("service") or "").strip()
        if not service_slug:
            self._selected_service = None
            return self._selected_service

        self._selected_service = Service.objects.filter(
            slug=service_slug,
            is_active=True,
        ).first()
        return self._selected_service

    def get_selected_category(self):
        if hasattr(self, "_selected_category"):
            return self._selected_category

        category_slug = (self.request.GET.get("category") or "").strip()
        if not category_slug:
            self._selected_category = None
            return self._selected_category

        self._selected_category = ServiceCategory.objects.filter(
            slug=category_slug,
            is_active=True,
        ).first()
        return self._selected_category

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_service = self.get_selected_service()
        selected_category = self.get_selected_category()
        filter_params = {}

        if selected_service:
            filter_params["service"] = selected_service.slug
        if selected_category:
            filter_params["category"] = selected_category.slug

        context.update(
            {
                "photos": context["page_obj"].object_list,
                "services": Service.objects.filter(is_active=True).select_related(
                    "category",
                ),
                "categories": ServiceCategory.objects.filter(
                    is_active=True,
                ),
                "selected_service": selected_service,
                "selected_category": selected_category,
                "pagination_query": urlencode(filter_params),
                # Keep the original before-and-after records available to existing
                # consumers while the public gallery transitions to GalleryPhoto.
                "gallery": Gallery.objects.all(),
            }
        )
        return context