"""File storage for user-uploaded images (e.g. return evidence photos).
Callers keep passing a local directory/prefix (see save_upload_image); when
Settings.s3_bucket_name is configured, _save_upload uploads to S3 instead
and those arguments are simply unused for that call — so every existing
call site keeps working unchanged in both modes, and dev/test environments
with no AWS credentials transparently keep using local disk."""

import asyncio
import mimetypes
import uuid
from functools import lru_cache
from pathlib import Path

import boto3
from fastapi import UploadFile

from app.core.config import get_settings
from app.core.exceptions import BadRequestError

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
ALLOWED_ATTACHMENT_TYPES = ALLOWED_IMAGE_TYPES | {"application/pdf"}


@lru_cache
def _s3_client():
    settings = get_settings()
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )


async def _upload_to_s3(*, key: str, data: bytes, content_type: str | None) -> str:
    settings = get_settings()
    if settings.uploads_environment and settings.uploads_environment != settings.app_env:
        raise BadRequestError("El almacenamiento configurado pertenece a otro entorno.")
    # boto3 is synchronous — offload to a thread so this never blocks the
    # event loop other requests are running on.
    await asyncio.to_thread(
        _s3_client().put_object,
        Bucket=settings.s3_bucket_name,
        Key=key,
        Body=data,
        ContentType=content_type or "application/octet-stream",
    )
    return f"https://{settings.s3_bucket_name}.s3.{settings.aws_region}.amazonaws.com/{key}"


def _validate(data: bytes, content_type: str | None, max_mb: int, allowed: set[str]) -> str:
    if content_type not in allowed:
        raise BadRequestError(
            f"Unsupported file type '{content_type}'. Allowed: {', '.join(sorted(allowed))}.",
            error_code="unsupported_file_type",
        )
    if len(data) > max_mb * 1024 * 1024:
        raise BadRequestError(f"File exceeds the {max_mb}MB limit.", error_code="file_too_large")
    extension = mimetypes.guess_extension(content_type) or ""
    return extension


def _validate_image(data: bytes, content_type: str | None, max_mb: int) -> str:
    return _validate(data, content_type, max_mb, ALLOWED_IMAGE_TYPES)


async def _save_upload(
    file: UploadFile,
    *,
    directory: Path,
    subdir: str,
    url_prefix: str,
    max_mb: int,
    allowed: set[str],
) -> str:
    # Reject the declared type before reading and bound memory even for huge uploads.
    _validate(b"", file.content_type, max_mb, allowed)
    data = await file.read(max_mb * 1024 * 1024 + 1)
    extension = _validate(data, file.content_type, max_mb, allowed)
    filename = f"{uuid.uuid4()}{extension}"

    if get_settings().s3_bucket_name:
        return await _upload_to_s3(
            key=f"{subdir}/{filename}", data=data, content_type=file.content_type
        )

    target_dir = directory / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread((target_dir / filename).write_bytes, data)

    return f"{url_prefix}/{subdir}/{filename}"


async def save_upload_image(
    file: UploadFile, *, directory: Path, subdir: str, url_prefix: str, max_mb: int
) -> str:
    return await _save_upload(
        file,
        directory=directory,
        subdir=subdir,
        url_prefix=url_prefix,
        max_mb=max_mb,
        allowed=ALLOWED_IMAGE_TYPES,
    )


async def save_upload_attachment(
    file: UploadFile, *, directory: Path, subdir: str, url_prefix: str, max_mb: int
) -> str:
    """Same as save_upload_image but also accepts PDF — for supporting
    documents (receipts, transfer confirmations) rather than photos."""
    return await _save_upload(
        file,
        directory=directory,
        subdir=subdir,
        url_prefix=url_prefix,
        max_mb=max_mb,
        allowed=ALLOWED_ATTACHMENT_TYPES,
    )


async def save_private_pickup(file: UploadFile) -> str:
    return await save_private_attachment(
        file, allowed=ALLOWED_IMAGE_TYPES, category="dispatch-pickups"
    )


async def save_private_attachment(
    file: UploadFile,
    *,
    allowed: set[str] = ALLOWED_ATTACHMENT_TYPES,
    category: str = "vehicle-documents",
) -> str:
    """Store sensitive supporting files outside the public uploads directory/bucket."""
    settings = get_settings()
    _validate(b"", file.content_type, settings.max_upload_mb, allowed)
    data = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    extension = _validate(data, file.content_type, settings.max_upload_mb, allowed)
    name = f"{uuid.uuid4()}{extension}"
    if settings.s3_private_bucket_name:
        if settings.s3_private_bucket_name == settings.s3_bucket_name:
            raise BadRequestError(
                "Configura un bucket privado separado para los documentos y evidencias."
            )
        key = f"{settings.app_env}/{category}/{name}"
        await asyncio.to_thread(
            _s3_client().put_object,
            Bucket=settings.s3_private_bucket_name,
            Key=key,
            Body=data,
            ContentType=file.content_type,
        )
        return f"s3://{settings.s3_private_bucket_name}/{key}"
    directory = Path(settings.private_uploads_dir)
    directory.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread((directory / name).write_bytes, data)
    return f"private://{name}"


def pickup_photo_endpoint(transfer) -> str | None:
    if not transfer.pickup_photo_url:
        return None
    return f"{get_settings().api_v1_prefix}/service-order-transfers/{transfer.id}/pickup-photo"


def private_pickup_location(reference: str):
    """Only configured storage locations are allowed; never fetch arbitrary URLs."""
    from urllib.parse import urlparse

    settings = get_settings()
    parsed = urlparse(reference)
    if parsed.scheme == "s3" and parsed.netloc in {
        settings.s3_private_bucket_name,
        settings.s3_bucket_name,
    }:
        return "s3", parsed.netloc, parsed.path.lstrip("/")
    if (
        parsed.scheme == "https"
        and settings.s3_bucket_name
        and parsed.netloc == f"{settings.s3_bucket_name}.s3.{settings.aws_region}.amazonaws.com"
    ):
        return "s3", settings.s3_bucket_name, parsed.path.lstrip("/")
    if reference.startswith("private://"):
        name = reference.removeprefix("private://")
        if name != Path(name).name:
            raise BadRequestError("Referencia de archivo inválida.")
        return "local", None, str(Path(settings.private_uploads_dir) / name)
    prefix = f"{settings.api_v1_prefix}/uploads/dispatch-pickups/"
    if (
        reference.startswith(prefix)
        and Path(reference[len(prefix) :]).name == reference[len(prefix) :]
    ):
        return (
            "local",
            None,
            str(Path(settings.uploads_dir) / "dispatch-pickups" / reference[len(prefix) :]),
        )
    raise BadRequestError("Referencia de archivo inválida.")
