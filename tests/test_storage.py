"""Regression tests for the local-disk upload helper used by part-return
evidence photos: valid images are saved and rejected ones never touch disk."""

import io
from pathlib import Path

import pytest
from starlette.datastructures import UploadFile

from app.core.exceptions import BadRequestError
from app.core.storage import save_upload_image


def _upload(content: bytes, content_type: str, filename: str = "photo.jpg") -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(content), headers={"content-type": content_type})


@pytest.mark.asyncio
async def test_valid_image_is_saved_and_returns_its_url(tmp_path: Path):
    url = await save_upload_image(
        _upload(b"fake-jpeg-bytes", "image/jpeg"),
        directory=tmp_path,
        subdir="part-returns",
        url_prefix="/api/v1/uploads",
        max_mb=8,
    )
    assert url.startswith("/api/v1/uploads/part-returns/")
    assert url.endswith(".jpg")
    saved_files = list((tmp_path / "part-returns").iterdir())
    assert len(saved_files) == 1
    assert saved_files[0].read_bytes() == b"fake-jpeg-bytes"


@pytest.mark.asyncio
async def test_unsupported_content_type_is_rejected(tmp_path: Path):
    with pytest.raises(BadRequestError):
        await save_upload_image(
            _upload(b"not an image", "application/pdf"),
            directory=tmp_path,
            subdir="part-returns",
            url_prefix="/api/v1/uploads",
            max_mb=8,
        )
    assert not (tmp_path / "part-returns").exists()


@pytest.mark.asyncio
async def test_oversized_image_is_rejected(tmp_path: Path):
    with pytest.raises(BadRequestError):
        await save_upload_image(
            _upload(b"x" * (2 * 1024 * 1024), "image/jpeg"),
            directory=tmp_path,
            subdir="part-returns",
            url_prefix="/api/v1/uploads",
            max_mb=1,
        )
    assert not (tmp_path / "part-returns").exists()


@pytest.mark.asyncio
async def test_upload_reads_only_limit_plus_one_byte_and_never_writes_oversize(tmp_path):
    class HugeUpload:
        content_type = "image/jpeg"
        async def read(self, size=-1):
            assert size == 1024 * 1024 + 1
            return b"x" * size
    with pytest.raises(BadRequestError) as exc:
        await save_upload_image(HugeUpload(), directory=tmp_path, subdir="evidence", url_prefix="/uploads", max_mb=1)
    assert exc.value.error_code == "file_too_large"
    assert not (tmp_path / "evidence").exists()


@pytest.mark.asyncio
async def test_unsupported_upload_is_rejected_without_reading(tmp_path):
    class InvalidUpload:
        content_type = "text/html"
        async def read(self, size=-1):
            raise AssertionError("Unsupported uploads must not be read")
    with pytest.raises(BadRequestError):
        await save_upload_image(InvalidUpload(), directory=tmp_path, subdir="evidence", url_prefix="/uploads", max_mb=1)
