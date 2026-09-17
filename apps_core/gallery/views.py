from django.views.generic import ListView
from .models import Gallery


class GalleryView(ListView):
    model = Gallery
    template_name = "pages/gallery.html"
    context_object_name = "gallery"

    def get_queryset(self):
        return Gallery.objects.all()