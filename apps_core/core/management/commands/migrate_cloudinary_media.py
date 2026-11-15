from pathlib import Path

import cloudinary.api as cloudinary_api
import cloudinary.uploader as cloudinary_uploader
from cloudinary.exceptions import Error as CloudinaryError
from cloudinary.exceptions import NotFound as CloudinaryNotFound
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from apps_core.core.models import Testimonial
from apps_core.core.storage import CloudinaryMediaStorage, CloudinaryStorageError
from apps_core.gallery.models import Gallery, GalleryPhoto
from apps_core.services.models import Service
from apps_core.website.models import HeroSlide, ProcessStep


class Command(BaseCommand):
    help = (
        "Migrate existing local media files to Cloudinary while preserving "
        "Django-relative names and Cloudinary public IDs. Defaults to a "
        "dry run; pass --execute to upload MISSING assets."
    )

    MEDIA_FIELDS = (
        ("HeroSlide.image", HeroSlide, "image"),
        ("ProcessStep.image", ProcessStep, "image"),
        ("Service.image", Service, "image"),
        ("Service.hero_image", Service, "hero_image"),
        ("Gallery.before_image", Gallery, "before_image"),
        ("Gallery.after_image", Gallery, "after_image"),
        ("GalleryPhoto.image", GalleryPhoto, "image"),
        ("Testimonial.customer_image", Testimonial, "customer_image"),
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--execute",
            action="store_true",
            help=(
                "Perform uploads for assets confirmed missing in Cloudinary. "
                "Without this flag the command only reports a dry run."
            ),
        )

    def handle(self, *args, **options):
        execute = options["execute"]

        self._configure_cloudinary()
        items = self._collect_items()
        unreferenced = self._find_unreferenced_files(items)
        preflight = self._preflight_counts(items, len(unreferenced))

        self._print_report(items, unreferenced, preflight)

        blocking = (
            preflight["local_missing"]
            or preflight["errors"]
            or preflight["conflicts"]
        )

        if not execute:
            if blocking:
                raise CommandError(
                    "Preflight found blocking issues; no uploads were performed."
                )
            return

        if blocking:
            raise CommandError(
                "Preflight failed; no uploads were performed."
            )

        uploaded, upload_failed, new_conflicts = self._upload_missing(items)

        final = dict(preflight)
        final.update(
            {
                "uploaded": uploaded,
                "upload_failed": upload_failed,
                "skipped": preflight["exists"],
                "conflicts": preflight["conflicts"] + new_conflicts,
            }
        )
        self._print_final_summary(final)

        if upload_failed or new_conflicts:
            raise CommandError(
                "Cloudinary media migration completed with upload errors."
            )

    def _configure_cloudinary(self):
        if not getattr(settings, "CLOUDINARY_CONFIGURED", False):
            raise CommandError(
                "Cloudinary is not fully configured. Set "
                "CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and "
                "CLOUDINARY_API_SECRET before running this command."
            )

        try:
            CloudinaryMediaStorage()
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc

    def _collect_items(self):
        references = {}

        for label, model, field_name in self.MEDIA_FIELDS:
            queryset = (
                model.objects
                .filter(**{f"{field_name}__isnull": False})
                .exclude(**{field_name: ""})
                .order_by("pk")
            )
            for obj in queryset.iterator():
                field_file = getattr(obj, field_name)
                raw_name = getattr(field_file, "name", "") or ""
                if not raw_name:
                    continue
                name = CloudinaryMediaStorage._normalize_name(raw_name)
                entry = references.setdefault(
                    name,
                    {"labels": set(), "references": 0},
                )
                entry["labels"].add(label)
                entry["references"] += 1

        items = []
        for name in sorted(references):
            entry = references[name]
            item = {
                "name": name,
                "labels": sorted(entry["labels"]),
                "references": entry["references"],
                "status": None,
                "reason": "",
                "public_id": None,
                "file_format": None,
                "asset_folder": None,
                "source_path": None,
            }
            public_id, file_format = CloudinaryMediaStorage._split_name(name)
            item["public_id"] = public_id
            item["file_format"] = file_format
            try:
                item["asset_folder"] = CloudinaryMediaStorage._asset_folder(name)
            except CloudinaryStorageError as exc:
                item["status"] = "CONFLICT"
                item["reason"] = str(exc)
            items.append(item)

        self._detect_collisions(items)

        for item in items:
            if item["status"] is not None:
                continue

            path, error = self._resolve_media_path(item["name"])
            if error:
                item["status"] = "CONFLICT"
                item["reason"] = error
                continue

            local_exists = path.is_file()
            if local_exists:
                item["source_path"] = path

            lookup_status, detail = self._lookup(
                item["public_id"],
                item["asset_folder"],
            )
            if lookup_status == "exists":
                item["status"] = "EXISTS"
            elif lookup_status == "missing":
                if local_exists:
                    item["status"] = "MISSING"
                else:
                    item["status"] = "LOCAL_MISSING"
                    item["reason"] = "local file not found"
            elif lookup_status == "conflict":
                item["status"] = "CONFLICT"
                item["reason"] = detail
            else:
                item["status"] = "ERROR"
                item["reason"] = detail

        return items

    def _detect_collisions(self, items):
        by_public_id = {}
        for item in items:
            if item["public_id"]:
                by_public_id.setdefault(item["public_id"], []).append(item)

        for public_id, group in by_public_id.items():
            names = {item["name"] for item in group}
            if len(names) <= 1:
                continue
            for item in group:
                if item["status"] is None:
                    item["status"] = "CONFLICT"
                    item["reason"] = (
                        f"public ID collision: {public_id} is used by "
                        f"{', '.join(sorted(names))}"
                    )

    def _resolve_media_path(self, name):
        media_root = Path(settings.MEDIA_ROOT).resolve()

        if not name or ".." in name.split("/"):
            return None, "unsafe path"

        candidate = (media_root / name).resolve()
        try:
            candidate.relative_to(media_root)
        except ValueError:
            return None, "path escapes MEDIA_ROOT"

        return candidate, None

    def _lookup(self, public_id, asset_folder):
        try:
            resource = cloudinary_api.resource(
                public_id,
                resource_type="image",
                type="upload",
            )
        except CloudinaryNotFound:
            return "missing", ""
        except CloudinaryError as exc:
            return "error", type(exc).__name__
        except Exception as exc:
            return "error", type(exc).__name__

        if resource.get("public_id") != public_id:
            return "conflict", "Cloudinary public_id mismatch"
        if resource.get("asset_folder") != asset_folder:
            return "conflict", "Cloudinary asset_folder mismatch"
        return "exists", ""

    def _find_unreferenced_files(self, items):
        referenced = {item["name"] for item in items}
        media_root = Path(settings.MEDIA_ROOT)
        if not media_root.is_dir():
            return []

        unreferenced = []
        for path in sorted(media_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(media_root).as_posix()
            normalized = CloudinaryMediaStorage._normalize_name(relative)
            if normalized not in referenced:
                unreferenced.append(relative)
        return unreferenced

    def _preflight_counts(self, items, unreferenced_count):
        return {
            "referenced": len(items),
            "exists": sum(item["status"] == "EXISTS" for item in items),
            "missing": sum(item["status"] == "MISSING" for item in items),
            "local_missing": sum(
                item["status"] == "LOCAL_MISSING" for item in items
            ),
            "errors": sum(item["status"] == "ERROR" for item in items),
            "conflicts": sum(item["status"] == "CONFLICT" for item in items),
            "unreferenced": unreferenced_count,
        }

    def _print_report(self, items, unreferenced, counts):
        self.stdout.write("Cloudinary media migration preflight")
        self.stdout.write("=" * 40)

        for item in items:
            line = f"[{item['status']}] {item['name']}"
            if item["public_id"]:
                line += (
                    f" -> public_id={item['public_id']}"
                    f" asset_folder={item['asset_folder'] or '-'}"
                )
            if item["reason"]:
                line += f" ({item['reason']})"
            self.stdout.write(line)

        if unreferenced:
            self.stdout.write("Unreferenced local files (not uploaded):")
            for path in unreferenced:
                self.stdout.write(f"- {path}")
        else:
            self.stdout.write("Unreferenced local files: 0")

        self._write_counts(counts, "Preflight summary")

    def _write_counts(self, counts, title):
        self.stdout.write(title)
        self.stdout.write(f"  Total referenced files: {counts['referenced']}")
        self.stdout.write(f"  Existing Cloudinary assets: {counts['exists']}")
        self.stdout.write(f"  Missing Cloudinary assets: {counts['missing']}")
        if "uploaded" in counts:
            self.stdout.write(f"  Successfully uploaded assets: {counts['uploaded']}")
            self.stdout.write(f"  Skipped assets: {counts['skipped']}")
            self.stdout.write(f"  Upload failures: {counts['upload_failed']}")
        self.stdout.write(f"  Local missing files: {counts['local_missing']}")
        self.stdout.write(f"  Errors: {counts['errors']}")
        self.stdout.write(f"  Conflicts: {counts['conflicts']}")
        self.stdout.write(f"  Unreferenced local files: {counts['unreferenced']}")

    def _print_final_summary(self, counts):
        self.stdout.write("Cloudinary media migration complete")
        self.stdout.write("=" * 40)
        self._write_counts(counts, "Final summary")

    def _upload_missing(self, items):
        uploaded = 0
        upload_failed = 0
        new_conflicts = 0

        for item in items:
            if item["status"] != "MISSING":
                continue

            options = {
                "resource_type": "image",
                "public_id": item["public_id"],
                "asset_folder": item["asset_folder"],
                "overwrite": False,
                "unique_filename": False,
                "use_filename": False,
            }
            if item["file_format"]:
                options["format"] = item["file_format"]

            try:
                result = cloudinary_uploader.upload(
                    str(item["source_path"]),
                    **options,
                )
            except CloudinaryError as exc:
                lookup_status, _ = self._lookup(
                    item["public_id"],
                    item["asset_folder"],
                )
                if lookup_status in {"exists", "conflict"}:
                    item["status"] = "CONFLICT"
                    item["reason"] = (
                        "asset already exists after failed upload "
                        f"({type(exc).__name__})"
                    )
                    new_conflicts += 1
                    self.stdout.write(
                        f"[CONFLICT] {item['name']} -> public_id="
                        f"{item['public_id']} ({item['reason']})"
                    )
                else:
                    item["status"] = "UPLOAD_FAILED"
                    item["reason"] = type(exc).__name__
                    upload_failed += 1
                    self.stdout.write(
                        f"[UPLOAD_FAILED] {item['name']} -> public_id="
                        f"{item['public_id']} ({item['reason']})"
                    )
                continue
            except Exception as exc:
                item["status"] = "UPLOAD_FAILED"
                item["reason"] = type(exc).__name__
                upload_failed += 1
                self.stdout.write(
                    f"[UPLOAD_FAILED] {item['name']} -> public_id="
                    f"{item['public_id']} ({item['reason']})"
                )
                continue

            validation_error = self._validate_upload_result(result, item)
            if validation_error:
                item["status"] = "UPLOAD_FAILED"
                item["reason"] = validation_error
                upload_failed += 1
                self.stdout.write(
                    f"[UPLOAD_FAILED] {item['name']} -> public_id="
                    f"{item['public_id']} ({validation_error})"
                )
                continue

            item["status"] = "UPLOADED"
            item["secure_url"] = result.get("secure_url", "")
            item["version"] = result.get("version", "")
            uploaded += 1
            self.stdout.write(
                f"[UPLOADED] {item['name']} -> public_id="
                f"{item['public_id']} asset_folder={item['asset_folder']} "
                f"version={item['version']} url={item['secure_url']}"
            )

        return uploaded, upload_failed, new_conflicts

    def _validate_upload_result(self, result, item):
        if not isinstance(result, dict):
            return "upload response was not a mapping"

        returned_public_id = result.get("public_id")
        returned_asset_folder = result.get("asset_folder")

        if returned_public_id != item["public_id"]:
            return (
                "upload response public_id mismatch: "
                f"expected {item['public_id']}, got {returned_public_id}"
            )
        if returned_asset_folder != item["asset_folder"]:
            return (
                "upload response asset_folder mismatch: "
                f"expected {item['asset_folder']}, got {returned_asset_folder}"
            )
        return None
