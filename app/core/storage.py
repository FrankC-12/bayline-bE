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
ALLOWED_ATTACHMENT_TYPES = ALLOWED_IMAGE_TYPES | {"application/pdf"}


def _validate(data: bytes, content_type: str | None, max_mb: int, allowed: set[str]) -> str:
    if content_type not in allowed:
        raise BadRequestError(
            f"Unsupported file type '{content_type}'. Allowed: {', '.join(sorted(allowed))}.",
            error_code="unsupported_file_type",
        )
    if len(data) > max_mb * 1024 * 1024:
        raise BadRequestError(
            f"File exceeds the {max_mb}MB limit.", error_code="file_too_large"
        )
    extension = mimetypes.guess_extension(content_type) or ""
    return extension


def _validate_image(data: bytes, content_type: str | None, max_mb: int) -> str:
    return _validate(data, content_type, max_mb, ALLOWED_IMAGE_TYPES)


async def _save_upload(
    file: UploadFile, *, directory: Path, subdir: str, url_prefix: str, max_mb: int, allowed: set[str]
) -> str:
    data = await file.read()
    extension = _validate(data, file.content_type, max_mb, allowed)

    target_dir = directory / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4()}{extension}"
    (target_dir / filename).write_bytes(data)

    return f"{url_prefix}/{subdir}/{filename}"


async def save_upload_image(
    file: UploadFile, *, directory: Path, subdir: str, url_prefix: str, max_mb: int
) -> str:
    return await _save_upload(
        file, directory=directory, subdir=subdir, url_prefix=url_prefix, max_mb=max_mb, allowed=ALLOWED_IMAGE_TYPES
    )


async def save_upload_attachment(
    file: UploadFile, *, directory: Path, subdir: str, url_prefix: str, max_mb: int
) -> str:
    """Same as save_upload_image but also accepts PDF — for supporting
    documents (receipts, transfer confirmations) rather than photos."""
    return await _save_upload(
        file, directory=directory, subdir=subdir, url_prefix=url_prefix, max_mb=max_mb, allowed=ALLOWED_ATTACHMENT_TYPES
    )
