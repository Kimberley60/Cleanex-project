from django.test import TestCase
from django.urls import reverse

from apps_core.gallery.models import GalleryPhoto
from apps_core.services.models import Service


class ServiceDetailGalleryTests(TestCase):
    def setUp(self):
        self.service = self.create_service("Home Cleaning", "home-cleaning")
        self.other_service = self.create_service("Office Cleaning", "office-cleaning")

    def create_service(self, name, slug):
        return Service.objects.create(
            name=name,
            slug=slug,
            description=f"{name} service.",
            base_price="100.00",
            duration_hours="2.00",
        )

    def create_photo(self, service, title, is_active=True):
        return GalleryPhoto.objects.create(
            service=service,
            title=title,
            image=f"gallery/photos/{title.lower().replace(' ', '-')}.jpg",
            alt_text=title,
            is_active=is_active,
        )

    def test_service_detail_context_contains_only_active_photos_for_service(self):
        active_photo = self.create_photo(self.service, "Active photo")
        self.create_photo(self.service, "Inactive photo", is_active=False)
        self.create_photo(self.other_service, "Other service photo")

        response = self.client.get(
            reverse("service_detail", kwargs={"slug": self.service.slug}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["gallery_photos"]),
            [active_photo],
        )
        self.assertEqual(response.context["service"], self.service)
        self.assertIn("service_features", response.context)
        self.assertIn("service_faqs", response.context)
        self.assertIn("related_services", response.context)
