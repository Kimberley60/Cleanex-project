from django.contrib import admin
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse

from apps_core.services.models import Service, ServiceCategory
from .models import Gallery, GalleryPhoto


class GalleryPhotoTests(TestCase):
    def setUp(self):
        self.service = Service.objects.create(
            name="Home Cleaning",
            slug="home-cleaning",
            description="Residential cleaning service.",
            base_price="100.00",
            duration_hours="2.00",
        )

    def create_photo(self, **overrides):
        fields = {
            "service": self.service,
            "title": "Living room refresh",
            "image": "gallery/photos/living-room.jpg",
            "alt_text": "A freshly cleaned living room",
        }
        fields.update(overrides)
        return GalleryPhoto.objects.create(**fields)

    def create_service(self, name, slug, category=None):
        return Service.objects.create(
            name=name,
            slug=slug,
            category=category,
            description=f"{name} service.",
            base_price="100.00",
            duration_hours="2.00",
        )

    def test_creates_photo_associated_with_service(self):
        photo = self.create_photo()

        self.assertEqual(photo.service, self.service)
        self.assertEqual(self.service.gallery_photos.get(), photo)

    def test_required_fields_and_defaults(self):
        for field_name in ("service", "title", "image", "alt_text"):
            field = GalleryPhoto._meta.get_field(field_name)
            self.assertFalse(field.blank)
            self.assertFalse(field.null)

        photo = self.create_photo()

        self.assertTrue(photo.is_active)
        self.assertEqual(photo.display_order, 0)
        self.assertIsNotNone(photo.created_at)
        self.assertIsNotNone(photo.updated_at)
        self.assertEqual(photo.caption, "")

    def test_orders_by_display_order_then_primary_key(self):
        later = self.create_photo(
            title="Later",
            display_order=2,
        )
        first = self.create_photo(
            title="First",
            display_order=1,
        )
        second = self.create_photo(
            title="Second",
            display_order=1,
        )

        self.assertEqual(
            list(GalleryPhoto.objects.all()),
            [first, second, later],
        )

    def test_service_cannot_be_deleted_while_photos_are_associated(self):
        self.create_photo()

        with self.assertRaises(ProtectedError):
            self.service.delete()

    def test_gallery_photo_is_registered_in_admin(self):
        self.assertIn(GalleryPhoto, admin.site._registry)

    def test_gallery_view_shows_active_photos_and_preserves_legacy_gallery(self):
        active_photo = self.create_photo()
        self.create_photo(
            title="Hidden photo",
            is_active=False,
        )
        legacy_gallery = Gallery.objects.create(
            title="Before and after",
            service=self.service,
            before_image="gallery/before/room.jpg",
            after_image="gallery/after/room.jpg",
        )

        response = self.client.get(reverse("gallery"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["photos"]), [active_photo])
        self.assertEqual(
            list(response.context["gallery"]),
            [legacy_gallery],
        )

    def test_gallery_view_filters_by_service(self):
        other_service = self.create_service("Office Cleaning", "office-cleaning")
        selected_photo = self.create_photo()
        self.create_photo(
            service=other_service,
            title="Office",
        )

        response = self.client.get(
            reverse("gallery"),
            {"service": self.service.slug},
        )

        self.assertEqual(list(response.context["photos"]), [selected_photo])
        self.assertEqual(response.context["selected_service"], self.service)

    def test_gallery_view_filters_by_category(self):
        selected_category = ServiceCategory.objects.create(
            name="Residential",
            slug="residential",
        )
        selected_service = self.create_service(
            "Residential Cleaning",
            "residential-cleaning",
            selected_category,
        )
        other_category = ServiceCategory.objects.create(
            name="Commercial",
            slug="commercial",
        )
        other_service = self.create_service(
            "Commercial Cleaning",
            "commercial-cleaning",
            other_category,
        )
        selected_photo = self.create_photo(service=selected_service)
        self.create_photo(service=other_service, title="Commercial")

        response = self.client.get(
            reverse("gallery"),
            {"category": selected_category.slug},
        )

        self.assertEqual(list(response.context["photos"]), [selected_photo])
        self.assertEqual(
            response.context["selected_category"],
            selected_category,
        )

    def test_gallery_view_combines_service_and_category_filters(self):
        category = ServiceCategory.objects.create(
            name="Residential",
            slug="residential",
        )
        selected_service = self.create_service(
            "Residential Cleaning",
            "residential-cleaning",
            category,
        )
        other_service = self.create_service(
            "Other Residential",
            "other-residential",
            category,
        )
        selected_photo = self.create_photo(service=selected_service)
        self.create_photo(service=other_service, title="Other service")

        response = self.client.get(
            reverse("gallery"),
            {
                "service": selected_service.slug,
                "category": category.slug,
            },
        )

        self.assertEqual(list(response.context["photos"]), [selected_photo])
        self.assertEqual(
            response.context["selected_service"],
            selected_service,
        )
        self.assertEqual(response.context["selected_category"], category)

    def test_invalid_gallery_filters_are_ignored(self):
        photo = self.create_photo()

        response = self.client.get(
            reverse("gallery"),
            {"service": "missing-service", "category": "missing-category"},
        )

        self.assertEqual(list(response.context["photos"]), [photo])
        self.assertIsNone(response.context["selected_service"])
        self.assertIsNone(response.context["selected_category"])

    def test_gallery_view_paginates_and_preserves_valid_filters(self):
        category = ServiceCategory.objects.create(
            name="Residential",
            slug="residential",
        )
        service = self.create_service(
            "Residential Cleaning",
            "residential-cleaning",
            category,
        )
        for index in range(13):
            self.create_photo(
                service=service,
                title=f"Photo {index}",
                display_order=index,
            )

        response = self.client.get(
            reverse("gallery"),
            {
                "service": service.slug,
                "category": category.slug,
                "page": "2",
            },
        )

        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertEqual(len(response.context["photos"]), 1)
        self.assertEqual(response.context["page_obj"].paginator.per_page, 12)
        self.assertEqual(
            response.context["pagination_query"],
            f"service={service.slug}&category={category.slug}",
        )

    def test_gallery_view_handles_empty_results(self):
        response = self.client.get(
            reverse("gallery"),
            {"service": self.service.slug},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["photos"]), [])
        self.assertEqual(response.context["page_obj"].paginator.count, 0)

    def test_gallery_view_handles_invalid_and_out_of_range_pages(self):
        for index in range(13):
            self.create_photo(
                title=f"Photo {index}",
                display_order=index,
            )
        page_url = reverse("gallery")

        invalid_page_response = self.client.get(page_url, {"page": "not-a-page"})
        out_of_range_response = self.client.get(page_url, {"page": "99"})

        self.assertEqual(invalid_page_response.status_code, 200)
        self.assertEqual(invalid_page_response.context["page_obj"].number, 1)
        self.assertEqual(out_of_range_response.status_code, 200)
        self.assertEqual(out_of_range_response.context["page_obj"].number, 2)
