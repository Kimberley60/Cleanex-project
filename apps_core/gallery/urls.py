from django.urls import path
from .views import GalleryView

urlpatterns = [
    path("gallery/", GalleryView.as_view(), name="gallery"),
]