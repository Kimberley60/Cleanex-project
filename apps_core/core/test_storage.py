from unittest.mock import patch
from cloudinary.exceptions import Error as CloudinaryError
from cloudinary.exceptions import NotFound as CloudinaryNotFound
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, override_settings

from apps_core.core.storage import CloudinaryMediaStorage, CloudinaryStorageError


CLOUDINARY_SETTINGS = {
    "CLOUDINARY_CLOUD_NAME": "test-cloud",
    "CLOUDINARY_API_KEY": "test-key",
    "CLOUDINARY_API_SECRET": "test-secret",
}

UPLOAD_PATH = "apps_core.core.storage.cloudinary_uploader.upload"
DESTROY_PATH = "apps_core.core.storage.cloudinary_uploader.destroy"
RESOURCE_PATH = "apps_core.core.storage.cloudinary_api.resource"
URL_PATH = "apps_core.core.storage.cloudinary_url"


@override_settings(**CLOUDINARY_SETTINGS)
class CloudinaryMediaStorageTests(SimpleTestCase):
    def setUp(self):
        self.storage = CloudinaryMediaStorage()

    def test_save_uploads_with_public_id_format_and_returns_relative_name(self):
        content = ContentFile(b"fake-image")

        with patch(UPLOAD_PATH) as mock_upload:
            result = self.storage._save("hero/slide.jpg", content)

        self.assertEqual(result, "hero/slide.jpg")
        mock_upload.assert_called_once()
        args, kwargs = mock_upload.call_args
        self.assertEqual(args[0], content)
        self.assertEqual(kwargs["resource_type"], "image")
        self.assertEqual(kwargs["public_id"], "hero/slide")
        self.assertEqual(kwargs["format"], "jpg")
        self.assertFalse(kwargs["overwrite"])
        self.assertFalse(kwargs["unique_filename"])
        self.assertFalse(kwargs["use_filename"])

    def test_url_generates_secure_cloudinary_url(self):
        expected_url = (
            "https://res.cloudinary.com/test-cloud/image/upload/"
            "v1/hero/slide.jpg"
        )

        with patch(URL_PATH, return_value=(expected_url, {})) as mock_url:
            result = self.storage.url("hero/slide.jpg")

        self.assertEqual(result, expected_url)
        mock_url.assert_called_once_with(
            "hero/slide",
            format="jpg",
            resource_type="image",
            secure=True,
        )

    def test_upload_to_directory_is_preserved_in_public_id(self):
        with patch(UPLOAD_PATH) as mock_upload:
            result = self.storage._save(
                "gallery/photos/living room.jpg",
                ContentFile(b"fake-image"),
            )

        self.assertEqual(result, "gallery/photos/living room.jpg")
        _, kwargs = mock_upload.call_args
        self.assertEqual(kwargs["public_id"], "gallery/photos/living room")
        self.assertEqual(kwargs["format"], "jpg")

    def test_file_extension_is_normalized_for_format(self):
        with patch(UPLOAD_PATH) as mock_upload:
            self.storage._save("services/heroes/banner.PNG", ContentFile(b"x"))

        _, kwargs = mock_upload.call_args
        self.assertEqual(kwargs["public_id"], "services/heroes/banner")
        self.assertEqual(kwargs["format"], "png")

    def test_save_without_extension_omits_format(self):
        with patch(UPLOAD_PATH) as mock_upload:
            result = self.storage._save("hero/slide", ContentFile(b"x"))

        self.assertEqual(result, "hero/slide")
        _, kwargs = mock_upload.call_args
        self.assertEqual(kwargs["public_id"], "hero/slide")
        self.assertNotIn("format", kwargs)

    def test_url_without_extension_omits_format(self):
        with patch(URL_PATH, return_value=("https://example.com/hero/slide", {})) as mock_url:
            self.storage.url("hero/slide")

        mock_url.assert_called_once_with(
            "hero/slide",
            resource_type="image",
            secure=True,
        )

    def test_exists_returns_true_when_cloudinary_asset_exists(self):
        with patch(RESOURCE_PATH, return_value={"public_id": "hero/slide"}) as mock_resource:
            self.assertTrue(self.storage.exists("hero/slide.jpg"))

        mock_resource.assert_called_once_with(
            "hero/slide",
            resource_type="image",
            type="upload",
        )

    def test_exists_returns_false_when_cloudinary_asset_is_missing(self):
        with patch(RESOURCE_PATH, side_effect=CloudinaryNotFound("missing")):
            self.assertFalse(self.storage.exists("hero/slide.jpg"))

    def test_exists_propagates_cloudinary_errors(self):
        with patch(RESOURCE_PATH, side_effect=CloudinaryError("boom")):
            with self.assertRaises(CloudinaryStorageError):
                self.storage.exists("hero/slide.jpg")

    def test_save_propagates_cloudinary_upload_errors_without_credentials(self):
        with patch(UPLOAD_PATH, side_effect=CloudinaryError("boom")):
            with self.assertRaises(CloudinaryStorageError) as ctx:
                self.storage._save("hero/slide.jpg", ContentFile(b"x"))

        message = str(ctx.exception)
        self.assertIn("hero/slide", message)
        self.assertNotIn("test-key", message)
        self.assertNotIn("test-secret", message)

    def test_delete_calls_cloudinary_destroy(self):
        with patch(DESTROY_PATH) as mock_destroy:
            self.storage.delete("hero/slide.jpg")

        mock_destroy.assert_called_once_with(
            "hero/slide",
            resource_type="image",
            type="upload",
            invalidate=True,
        )

    def test_delete_ignores_missing_asset(self):
        with patch(DESTROY_PATH, side_effect=CloudinaryNotFound("missing")):
            self.storage.delete("hero/slide.jpg")

    def test_size_returns_cloudinary_bytes(self):
        with patch(RESOURCE_PATH, return_value={"bytes": 1234}):
            self.assertEqual(self.storage.size("hero/slide.jpg"), 1234)

    def test_save_maps_asset_folder_for_current_upload_paths(self):
        expectations = [
            ("hero/example.jpg", "cleanex/hero", "hero/example", "jpg"),
            (
                "process_steps/example.jpg",
                "cleanex/process_steps",
                "process_steps/example",
                "jpg",
            ),
            ("services/example.jpg", "cleanex/services", "services/example", "jpg"),
            (
                "services/heroes/example.jpg",
                "cleanex/services",
                "services/heroes/example",
                "jpg",
            ),
            (
                "gallery/before/example.jpg",
                "cleanex/gallery",
                "gallery/before/example",
                "jpg",
            ),
            (
                "gallery/after/example.jpg",
                "cleanex/gallery",
                "gallery/after/example",
                "jpg",
            ),
            (
                "gallery/photos/example.jpg",
                "cleanex/gallery",
                "gallery/photos/example",
                "jpg",
            ),
            (
                "testimonials/example.jpg",
                "cleanex/testimonials",
                "testimonials/example",
                "jpg",
            ),
        ]

        with patch(UPLOAD_PATH) as mock_upload:
            for name, expected_folder, expected_public_id, expected_format in expectations:
                with self.subTest(name=name):
                    mock_upload.reset_mock()
                    result = self.storage._save(name, ContentFile(b"x"))

                    self.assertEqual(result, name)
                    mock_upload.assert_called_once()
                    _, kwargs = mock_upload.call_args
                    self.assertEqual(kwargs["asset_folder"], expected_folder)
                    self.assertEqual(kwargs["public_id"], expected_public_id)
                    self.assertEqual(kwargs["format"], expected_format)
                    self.assertEqual(kwargs["resource_type"], "image")
                    self.assertFalse(kwargs["overwrite"])
                    self.assertFalse(kwargs["unique_filename"])
                    self.assertFalse(kwargs["use_filename"])
                    self.assertNotIn("folder", kwargs)
                    self.assertNotIn("public_id_prefix", kwargs)
                    self.assertNotIn("use_asset_folder_as_public_id_prefix", kwargs)

    def test_save_rejects_unsupported_top_level_path(self):
        with patch(UPLOAD_PATH) as mock_upload:
            with self.assertRaises(CloudinaryStorageError):
                self.storage._save("unknown/example.jpg", ContentFile(b"x"))

        mock_upload.assert_not_called()

    def test_url_for_nested_path_uses_public_id_without_asset_folder(self):
        expected_url = (
            "https://res.cloudinary.com/test-cloud/image/upload/"
            "v1/gallery/before/room.jpg"
        )

        with patch(URL_PATH, return_value=(expected_url, {})) as mock_url:
            result = self.storage.url("gallery/before/room.jpg")

        self.assertEqual(result, expected_url)
        mock_url.assert_called_once_with(
            "gallery/before/room",
            format="jpg",
            resource_type="image",
            secure=True,
        )

    def test_save_with_different_extension_uses_alternative_name_when_public_id_exists(self):
        def resource_side_effect(public_id, **kwargs):
            if public_id == "gallery/before/room":
                return {"public_id": public_id}
            raise CloudinaryNotFound("missing")

        with patch(RESOURCE_PATH, side_effect=resource_side_effect), patch(
            UPLOAD_PATH
        ) as mock_upload:
            result = self.storage.save("gallery/before/room.png", ContentFile(b"png"))

        self.assertNotEqual(result, "gallery/before/room.png")
        self.assertTrue(result.startswith("gallery/before/room_"))
        self.assertTrue(result.endswith(".png"))

        mock_upload.assert_called_once()
        _, kwargs = mock_upload.call_args
        self.assertNotEqual(kwargs["public_id"], "gallery/before/room")
        self.assertTrue(kwargs["public_id"].startswith("gallery/before/room_"))
        self.assertEqual(kwargs["format"], "png")
        self.assertEqual(kwargs["asset_folder"], "cleanex/gallery")

    @override_settings(
        CLOUDINARY_CLOUD_NAME="",
        CLOUDINARY_API_KEY="",
        CLOUDINARY_API_SECRET="",
    )
    def test_unconfigured_storage_raises_improperly_configured(self):
        with self.assertRaises(ImproperlyConfigured):
            CloudinaryMediaStorage()


class CloudinarySettingsTests(SimpleTestCase):
    def test_default_storage_matches_cloudinary_configuration(self):
        if settings.CLOUDINARY_CONFIGURED:
            expected = "apps_core.core.storage.CloudinaryMediaStorage"
        else:
            expected = "django.core.files.storage.FileSystemStorage"

        self.assertEqual(settings.STORAGES["default"]["BACKEND"], expected)

    def test_staticfiles_storage_remains_independent(self):
        self.assertEqual(
            settings.STORAGES["staticfiles"]["BACKEND"],
            "django.contrib.staticfiles.storage.StaticFilesStorage",
        )

    def test_media_settings_are_preserved_for_transition(self):
        self.assertEqual(settings.MEDIA_URL, "/media/")
        self.assertEqual(settings.MEDIA_ROOT, settings.BASE_DIR / "media")
