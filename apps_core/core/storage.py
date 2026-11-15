import posixpath
import cloudinary
import cloudinary.api as cloudinary_api
import cloudinary.uploader as cloudinary_uploader
from cloudinary.exceptions import Error as CloudinaryError
from cloudinary.exceptions import NotFound as CloudinaryNotFound
from cloudinary.utils import cloudinary_url
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import Storage


_CLOUDINARY_ASSET_FOLDER_ROOT = "cleanex"
_CLOUDINARY_ASSET_FOLDER_TOP_LEVELS = frozenset(
    {
        "hero",
        "process_steps",
        "services",
        "gallery",
        "testimonials",
    }
)


class CloudinaryStorageError(Exception):
    """Raised when a Cloudinary media storage operation fails."""


class CloudinaryMediaStorage(Storage):
    """Store uploaded media in Cloudinary while preserving Django file names.

    The database keeps the same relative path Django would have used for
    local storage (for example ``hero/slide.jpg``). Cloudinary is addressed
    by a public ID derived from that path without its extension.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cloud_name = getattr(settings, "CLOUDINARY_CLOUD_NAME", "")
        api_key = getattr(settings, "CLOUDINARY_API_KEY", "")
        api_secret = getattr(settings, "CLOUDINARY_API_SECRET", "")

        if not all((cloud_name, api_key, api_secret)):
            raise ImproperlyConfigured(
                "Cloudinary media storage requires CLOUDINARY_CLOUD_NAME, "
                "CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET."
            )

        cloudinary.config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret,
        )

    @staticmethod
    def _normalize_name(name):
        return name.replace("\\", "/").lstrip("/")

    @classmethod
    def _split_name(cls, name):
        normalized = cls._normalize_name(name)
        public_id, extension = posixpath.splitext(normalized)
        file_format = extension[1:].lower() if extension else None
        return public_id, file_format

    @classmethod
    def _asset_folder(cls, name):
        normalized = cls._normalize_name(name)
        top_level = normalized.split("/", 1)[0].lower()
        if top_level not in _CLOUDINARY_ASSET_FOLDER_TOP_LEVELS:
            raise CloudinaryStorageError(
                f"Unsupported upload path for Cloudinary asset folder: {name}"
            )
        return f"{_CLOUDINARY_ASSET_FOLDER_ROOT}/{top_level}"

    def _resource(self, name):
        public_id, _ = self._split_name(name)
        try:
            return cloudinary_api.resource(
                public_id,
                resource_type="image",
                type="upload",
            )
        except CloudinaryNotFound as exc:
            raise CloudinaryStorageError(
                f"Cloudinary asset does not exist: {public_id}"
            ) from exc
        except CloudinaryError as exc:
            raise CloudinaryStorageError(
                f"Cloudinary resource lookup failed for {public_id}: "
                f"{type(exc).__name__}"
            ) from exc

    def _save(self, name, content):
        if not name:
            raise ValueError("Cloudinary storage requires a file name.")

        public_id, file_format = self._split_name(name)
        options = {
            "resource_type": "image",
            "public_id": public_id,
            "asset_folder": self._asset_folder(name),
            "overwrite": False,
            "unique_filename": False,
            "use_filename": False,
        }
        if file_format:
            options["format"] = file_format

        if hasattr(content, "seek"):
            try:
                content.seek(0)
            except (AttributeError, OSError):
                pass

        try:
            cloudinary_uploader.upload(content, **options)
        except CloudinaryError as exc:
            raise CloudinaryStorageError(
                f"Cloudinary upload failed for {public_id}: "
                f"{type(exc).__name__}"
            ) from exc

        return self._normalize_name(name)

    def url(self, name):
        if not name:
            return ""

        public_id, file_format = self._split_name(name)
        options = {
            "resource_type": "image",
            "secure": True,
        }
        if file_format:
            options["format"] = file_format

        try:
            return cloudinary_url(public_id, **options)[0]
        except CloudinaryError as exc:
            raise CloudinaryStorageError(
                f"Cloudinary URL generation failed for {public_id}: "
                f"{type(exc).__name__}"
            ) from exc

    def exists(self, name):
        if not name:
            return False

        public_id, _ = self._split_name(name)
        try:
            cloudinary_api.resource(
                public_id,
                resource_type="image",
                type="upload",
            )
        except CloudinaryNotFound:
            return False
        except CloudinaryError as exc:
            raise CloudinaryStorageError(
                f"Cloudinary exists check failed for {public_id}: "
                f"{type(exc).__name__}"
            ) from exc
        return True

    def delete(self, name):
        if not name:
            return

        public_id, _ = self._split_name(name)
        try:
            cloudinary_uploader.destroy(
                public_id,
                resource_type="image",
                type="upload",
                invalidate=True,
            )
        except CloudinaryNotFound:
            return
        except CloudinaryError as exc:
            raise CloudinaryStorageError(
                f"Cloudinary delete failed for {public_id}: "
                f"{type(exc).__name__}"
            ) from exc

    def size(self, name):
        resource = self._resource(name)
        try:
            return resource["bytes"]
        except (KeyError, TypeError) as exc:
            public_id, _ = self._split_name(name)
            raise CloudinaryStorageError(
                f"Cloudinary asset size unavailable for {public_id}."
            ) from exc

    def _open(self, name, mode="rb"):
        raise NotImplementedError(
            "Cloudinary media storage does not support opening files for reading."
        )

    def path(self, name):
        raise NotImplementedError(
            "Cloudinary media storage does not use local file paths."
        )

    def listdir(self, path):
        raise NotImplementedError(
            "Cloudinary media storage does not support directory listing."
        )
