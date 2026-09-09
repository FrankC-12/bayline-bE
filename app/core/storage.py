"""Local-disk file storage for user-uploaded images (e.g. return evidence
photos). Kept free of global state — callers pass in the directory/prefix
so this stays trivially testable and swappable for an object-store backend
later without touching call sites' business logic."""

import mimetypes
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.exceptions import BadRequestError

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


def _validate_image(data: bytes, content_type: str | None, max_mb: int) -> str:
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise BadRequestError(
            f"Unsupported image type '{content_type}'. Allowed: {', '.join(sorted(ALLOWED_IMAGE_TYPES))}.",
            error_code="unsupported_image_type",
        )
    if len(data) > max_mb * 1024 * 1024:
        raise BadRequestError(
            f"Image exceeds the {max_mb}MB limit.", error_code="image_too_large"
        )
    extension = mimetypes.guess_extension(content_type) or ""
    return extension


async def save_upload_image(
    file: UploadFile, *, directory: Path, subdir: str, url_prefix: str, max_mb: int
) -> str:
    data = await file.read()
    extension = _validate_image(data, file.content_type, max_mb)

    target_dir = directory / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4()}{extension}"
    (target_dir / filename).write_bytes(data)

    return f"{url_prefix}/{subdir}/{filename}"
