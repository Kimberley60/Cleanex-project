from decimal import Decimal
from io import StringIO
import tempfile
from pathlib import Path
from unittest.mock import patch

from cloudinary.exceptions import Error as CloudinaryError
from cloudinary.exceptions import NotFound as CloudinaryNotFound
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps_core.core.models import Testimonial
from apps_core.gallery.models import Gallery, GalleryPhoto
from apps_core.services.models import Service
from apps_core.website.models import HeroSection, HeroSlide, ProcessStep


class MigrateCloudinaryMediaCommandTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.media_root = Path(self.tmp.name) / "media"
        self.media_root.mkdir(parents=True, exist_ok=True)

        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_root,
            CLOUDINARY_CONFIGURED=True,
            CLOUDINARY_CLOUD_NAME="test-cloud",
            CLOUDINARY_API_KEY="test-key",
            CLOUDINARY_API_SECRET="test-secret",
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

        self.resource_patcher = patch("cloudinary.api.resource")
        self.mock_resource = self.resource_patcher.start()
        self.addCleanup(self.resource_patcher.stop)

        self.upload_patcher = patch("cloudinary.uploader.upload")
        self.mock_upload = self.upload_patcher.start()
        self.addCleanup(self.upload_patcher.stop)

        self.mock_resource.side_effect = CloudinaryNotFound("not found")

    def _write_media(self, relative_name, content=b"fake-image"):
        path = self.media_root / relative_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def _run(self, execute=False):
        output = StringIO()
        kwargs = {"stdout": output}
        if execute:
            kwargs["execute"] = True
        call_command("migrate_cloudinary_media", **kwargs)
        return output.getvalue()

    def _run_expecting_error(self, execute=False):
        output = StringIO()
        kwargs = {"stdout": output}
        if execute:
            kwargs["execute"] = True
        with self.assertRaises(CommandError):
            call_command("migrate_cloudinary_media", **kwargs)
        return output.getvalue()

    def _create_hero_slide(self, image_name, title="Slide"):
        section = HeroSection.objects.create(
            name="Home",
            page="home",
        )
        return HeroSlide.objects.create(
            hero_section=section,
            title=title,
            subtitle="Subtitle",
            image=image_name,
        )

    def _create_service(self, slug="service", **kwargs):
        defaults = {
            "name": "Service",
            "slug": slug,
            "description": "Description",
            "base_price": Decimal("1000.00"),
            "duration_hours": Decimal("2.00"),
        }
        defaults.update(kwargs)
        return Service.objects.create(**defaults)

    def test_dry_run_reports_missing_without_uploading(self):
        self._create_hero_slide("hero/test.jpg")
        self._write_media("hero/test.jpg")

        output = self._run()

        self.assertIn("[MISSING] hero/test.jpg", output)
        self.assertIn("public_id=hero/test", output)
        self.assertIn("asset_folder=cleanex/hero", output)
        self.assertIn("Missing Cloudinary assets: 1", output)
        self.mock_upload.assert_not_called()
        self.mock_resource.assert_called_once_with(
            "hero/test",
            resource_type="image",
            type="upload",
        )
        self.assertEqual(
            HeroSlide.objects.get().image.name,
            "hero/test.jpg",
        )
        self.assertTrue((self.media_root / "hero/test.jpg").is_file())
        self.assertNotIn("test-secret", output)
        self.assertNotIn("test-key", output)

    def test_execute_uploads_missing_with_expected_options(self):
        self._create_hero_slide("hero/test.jpg")
        self._write_media("hero/test.jpg")
        self.mock_upload.return_value = {
            "public_id": "hero/test",
            "asset_folder": "cleanex/hero",
            "secure_url": "https://res.cloudinary.com/test/image/upload/v1/hero/test.jpg",
            "version": 123,
        }

        output = self._run(execute=True)

        self.mock_upload.assert_called_once()
        args, kwargs = self.mock_upload.call_args
        self.assertTrue(str(args[0]).endswith("hero\\test.jpg") or str(args[0]).endswith("hero/test.jpg"))
        self.assertEqual(kwargs["resource_type"], "image")
        self.assertEqual(kwargs["public_id"], "hero/test")
        self.assertEqual(kwargs["asset_folder"], "cleanex/hero")
        self.assertEqual(kwargs["format"], "jpg")
        self.assertFalse(kwargs["overwrite"])
        self.assertFalse(kwargs["unique_filename"])
        self.assertFalse(kwargs["use_filename"])
        self.assertNotIn("folder", kwargs)
        self.assertNotIn("public_id_prefix", kwargs)
        self.assertNotIn("use_asset_folder_as_public_id_prefix", kwargs)

        self.assertIn("[UPLOADED] hero/test.jpg", output)
        self.assertIn("version=123", output)
        self.assertIn("Successfully uploaded assets: 1", output)
        self.assertEqual(
            HeroSlide.objects.get().image.name,
            "hero/test.jpg",
        )
        self.assertTrue((self.media_root / "hero/test.jpg").is_file())

    def test_existing_assets_are_skipped(self):
        self._create_hero_slide("hero/test.jpg")
        self._write_media("hero/test.jpg")
        self.mock_resource.side_effect = None
        self.mock_resource.return_value = {
            "public_id": "hero/test",
            "asset_folder": "cleanex/hero",
        }

        output = self._run(execute=True)

        self.mock_upload.assert_not_called()
        self.assertIn("[EXISTS] hero/test.jpg", output)
        self.assertIn("Existing Cloudinary assets: 1", output)
        self.assertIn("Skipped assets: 1", output)
        self.assertIn("Successfully uploaded assets: 0", output)

    def test_provider_errors_are_not_treated_as_missing(self):
        self._create_hero_slide("hero/test.jpg")
        self._write_media("hero/test.jpg")
        self.mock_resource.side_effect = CloudinaryError("401 unauthorized")

        output = self._run_expecting_error(execute=True)

        self.assertIn("[ERROR] hero/test.jpg", output)
        self.mock_upload.assert_not_called()

    def test_network_failures_prevent_upload(self):
        self._create_hero_slide("hero/test.jpg")
        self._write_media("hero/test.jpg")
        self.mock_resource.side_effect = Exception("network timeout")

        output = self._run_expecting_error(execute=True)

        self.assertIn("[ERROR] hero/test.jpg", output)
        self.mock_upload.assert_not_called()

    def test_missing_local_files_prevent_upload(self):
        self._create_hero_slide("hero/missing.jpg")

        output = self._run_expecting_error(execute=True)

        self.assertIn("[LOCAL_MISSING] hero/missing.jpg", output)
        self.mock_upload.assert_not_called()

    def test_cloudinary_exists_without_local_file_is_skipped(self):
        self._create_hero_slide("hero/cloudinary-only.jpg")
        self.mock_resource.side_effect = None
        self.mock_resource.return_value = {
            "public_id": "hero/cloudinary-only",
            "asset_folder": "cleanex/hero",
        }

        output = self._run(execute=True)

        self.mock_upload.assert_not_called()
        self.assertIn("[EXISTS] hero/cloudinary-only.jpg", output)
        self.assertIn("Existing Cloudinary assets: 1", output)
        self.assertIn("Skipped assets: 1", output)
        self.assertIn("Successfully uploaded assets: 0", output)
        self.assertFalse(
            (self.media_root / "hero/cloudinary-only.jpg").exists()
        )
        self.assertEqual(
            HeroSlide.objects.get().image.name,
            "hero/cloudinary-only.jpg",
        )

    def test_cloudinary_error_without_local_file_is_error(self):
        self._create_hero_slide("hero/error-only.jpg")
        self.mock_resource.side_effect = CloudinaryError("401 unauthorized")

        output = self._run_expecting_error(execute=True)

        self.assertIn("[ERROR] hero/error-only.jpg", output)
        self.assertNotIn("[LOCAL_MISSING] hero/error-only.jpg", output)
        self.mock_upload.assert_not_called()

    def test_cloudinary_asset_folder_mismatch_is_conflict(self):
        self._create_hero_slide("hero/mismatch.jpg")
        self._write_media("hero/mismatch.jpg")
        self.mock_resource.side_effect = None
        self.mock_resource.return_value = {
            "public_id": "hero/mismatch",
            "asset_folder": "wrong-folder",
        }

        output = self._run_expecting_error(execute=True)

        self.assertIn("[CONFLICT] hero/mismatch.jpg", output)
        self.assertIn("asset_folder mismatch", output)
        self.mock_upload.assert_not_called()

    def test_cloudinary_public_id_mismatch_is_conflict(self):
        self._create_hero_slide("hero/mismatch.jpg")
        self._write_media("hero/mismatch.jpg")
        self.mock_resource.side_effect = None
        self.mock_resource.return_value = {
            "public_id": "hero/other",
            "asset_folder": "cleanex/hero",
        }

        output = self._run_expecting_error(execute=True)

        self.assertIn("[CONFLICT] hero/mismatch.jpg", output)
        self.assertIn("public_id mismatch", output)
        self.mock_upload.assert_not_called()

    def test_paths_outside_media_root_are_rejected(self):
        Testimonial.objects.create(
            customer_name="Customer",
            review="Review",
            customer_image="hero/../outside.jpg",
        )

        output = self._run_expecting_error(execute=True)

        self.assertIn("[CONFLICT] hero/../outside.jpg", output)
        self.mock_upload.assert_not_called()

    def test_unsupported_media_paths_are_rejected(self):
        Testimonial.objects.create(
            customer_name="Customer",
            review="Review",
            customer_image="unknown/foo.jpg",
        )

        output = self._run_expecting_error(execute=True)

        self.assertIn("[CONFLICT] unknown/foo.jpg", output)
        self.assertIn("Unsupported upload path", output)
        self.mock_upload.assert_not_called()

    def test_public_id_collisions_are_detected(self):
        section = HeroSection.objects.create(name="Home", page="home")
        HeroSlide.objects.create(
            hero_section=section,
            title="JPG",
            subtitle="Subtitle",
            image="hero/collision.jpg",
        )
        HeroSlide.objects.create(
            hero_section=section,
            title="PNG",
            subtitle="Subtitle",
            image="hero/collision.png",
        )
        self._write_media("hero/collision.jpg")
        self._write_media("hero/collision.png")

        output = self._run_expecting_error(execute=True)

        self.assertIn("public ID collision", output)
        self.assertEqual(output.count("[CONFLICT]"), 2)
        self.mock_upload.assert_not_called()

    def test_asset_folders_for_all_eight_upload_paths(self):
        section = HeroSection.objects.create(name="Home", page="home")
        HeroSlide.objects.create(
            hero_section=section,
            title="Hero",
            subtitle="Subtitle",
            image="hero/hero.jpg",
        )
        ProcessStep.objects.create(
            step_number=1,
            title="Step",
            description="Description",
            icon="fa-solid fa-check",
            image="process_steps/step.jpg",
        )
        service = self._create_service(
            image="services/card.jpg",
            hero_image="services/heroes/hero.jpg",
        )
        Gallery.objects.create(
            title="Gallery",
            before_image="gallery/before/before.jpg",
            after_image="gallery/after/after.jpg",
        )
        GalleryPhoto.objects.create(
            service=service,
            title="Photo",
            image="gallery/photos/photo.jpg",
            alt_text="Alt",
        )
        Testimonial.objects.create(
            customer_name="Customer",
            review="Review",
            customer_image="testimonials/customer.png",
        )

        for name in (
            "hero/hero.jpg",
            "process_steps/step.jpg",
            "services/card.jpg",
            "services/heroes/hero.jpg",
            "gallery/before/before.jpg",
            "gallery/after/after.jpg",
            "gallery/photos/photo.jpg",
            "testimonials/customer.png",
        ):
            self._write_media(name)

        output = self._run()

        self.mock_upload.assert_not_called()
        self.assertIn("Missing Cloudinary assets: 8", output)
        self.assertIn("public_id=hero/hero asset_folder=cleanex/hero", output)
        self.assertIn(
            "public_id=process_steps/step asset_folder=cleanex/process_steps",
            output,
        )
        self.assertIn(
            "public_id=services/card asset_folder=cleanex/services",
            output,
        )
        self.assertIn(
            "public_id=services/heroes/hero asset_folder=cleanex/services",
            output,
        )
        self.assertIn(
            "public_id=gallery/before/before asset_folder=cleanex/gallery",
            output,
        )
        self.assertIn(
            "public_id=gallery/after/after asset_folder=cleanex/gallery",
            output,
        )
        self.assertIn(
            "public_id=gallery/photos/photo asset_folder=cleanex/gallery",
            output,
        )
        self.assertIn(
            "public_id=testimonials/customer asset_folder=cleanex/testimonials",
            output,
        )

    def test_upload_response_validation_failure_is_reported(self):
        self._create_hero_slide("hero/test.jpg")
        self._write_media("hero/test.jpg")
        self.mock_upload.return_value = {
            "public_id": "hero/test",
            "asset_folder": "wrong-folder",
            "secure_url": "https://res.cloudinary.com/test/image/upload/v1/hero/test.jpg",
            "version": 1,
        }

        output = self._run_expecting_error(execute=True)

        self.assertIn("[UPLOAD_FAILED] hero/test.jpg", output)
        self.assertIn("asset_folder mismatch", output)
        self.assertIn("Upload failures: 1", output)

    def test_partial_upload_failures_are_reported(self):
        section = HeroSection.objects.create(name="Home", page="home")
        HeroSlide.objects.create(
            hero_section=section,
            title="A",
            subtitle="Subtitle",
            image="hero/a.jpg",
        )
        HeroSlide.objects.create(
            hero_section=section,
            title="B",
            subtitle="Subtitle",
            image="hero/b.jpg",
        )
        self._write_media("hero/a.jpg")
        self._write_media("hero/b.jpg")

        self.mock_upload.side_effect = [
            {
                "public_id": "hero/a",
                "asset_folder": "cleanex/hero",
                "secure_url": "https://res.cloudinary.com/test/image/upload/v1/hero/a.jpg",
                "version": 1,
            },
            CloudinaryError("boom"),
        ]

        output = self._run_expecting_error(execute=True)

        self.assertIn("[UPLOADED] hero/a.jpg", output)
        self.assertIn("[UPLOAD_FAILED] hero/b.jpg", output)
        self.assertIn("Successfully uploaded assets: 1", output)
        self.assertIn("Upload failures: 1", output)

    def test_unreferenced_local_files_are_reported_not_uploaded(self):
        self._create_hero_slide("hero/test.jpg")
        self._write_media("hero/test.jpg")
        self._write_media("hero/unreferenced.jpg")

        output = self._run()

        self.assertIn("Unreferenced local files: 1", output)
        self.assertIn("- hero/unreferenced.jpg", output)
        self.mock_upload.assert_not_called()
