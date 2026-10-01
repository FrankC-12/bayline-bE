"""S3-backed uploads — when Settings.s3_bucket_name is configured,
save_upload_image/save_upload_attachment must upload to S3 instead of disk
and return the S3 URL. No real AWS calls here: _s3_client is monkeypatched
to a fake that just records put_object calls."""

import io
from pathlib import Path

import pytest
from starlette.datastructures import UploadFile

import app.core.storage as storage
from app.core.config import Settings, get_settings


class FakeS3Client:
    def __init__(self) -> None:
        self.put_object_calls: list[dict] = []

    def put_object(self, **kwargs):
        self.put_object_calls.append(kwargs)


def _upload(content: bytes, content_type: str, filename: str = "photo.jpg") -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(content), headers={"content-type": content_type})


@pytest.fixture
def s3_env(monkeypatch):
    base = get_settings()
    s3_settings = base.model_copy(update={"s3_bucket_name": "bayline-test-bucket", "aws_region": "us-east-1"})
    monkeypatch.setattr(storage, "get_settings", lambda: s3_settings)
    fake_client = FakeS3Client()
    monkeypatch.setattr(storage, "_s3_client", lambda: fake_client)
    return fake_client


@pytest.mark.asyncio
async def test_valid_image_uploads_to_s3_and_returns_its_url(s3_env, tmp_path: Path):
    url = await storage.save_upload_image(
        _upload(b"fake-jpeg-bytes", "image/jpeg"),
        directory=tmp_path,
        subdir="inspections",
        url_prefix="/api/v1/uploads",
        max_mb=8,
    )

    assert len(s3_env.put_object_calls) == 1
    uploaded_key = s3_env.put_object_calls[0]["Key"]
    assert url == f"https://bayline-test-bucket.s3.us-east-1.amazonaws.com/{uploaded_key}"
    assert uploaded_key.startswith("inspections/")
    assert uploaded_key.endswith(".jpg")
    # Nothing written to disk — the S3 branch must short-circuit before that.
    assert not (tmp_path / "inspections").exists()


@pytest.mark.asyncio
async def test_s3_upload_receives_the_right_bucket_and_content_type(s3_env, tmp_path: Path):
    await storage.save_upload_image(
        _upload(b"fake-png-bytes", "image/png"),
        directory=tmp_path,
        subdir="inspections",
        url_prefix="/api/v1/uploads",
        max_mb=8,
    )

    assert len(s3_env.put_object_calls) == 1
    call = s3_env.put_object_calls[0]
    assert call["Bucket"] == "bayline-test-bucket"
    assert call["Body"] == b"fake-png-bytes"
    assert call["ContentType"] == "image/png"
    assert call["Key"].startswith("inspections/")


@pytest.mark.asyncio
async def test_invalid_file_is_rejected_before_any_s3_call(s3_env, tmp_path: Path):
    from app.core.exceptions import BadRequestError

    with pytest.raises(BadRequestError):
        await storage.save_upload_image(
            _upload(b"not an image", "application/pdf"),
            directory=tmp_path,
            subdir="inspections",
            url_prefix="/api/v1/uploads",
            max_mb=8,
        )
    assert s3_env.put_object_calls == []


@pytest.mark.asyncio
async def test_without_s3_bucket_configured_falls_back_to_disk(tmp_path: Path):
    """Regression: the default (no AWS env vars) must behave exactly like
    before this feature existed — local disk, untouched by S3 config."""
    assert get_settings().s3_bucket_name is None

    url = await storage.save_upload_image(
        _upload(b"fake-jpeg-bytes", "image/jpeg"),
        directory=tmp_path,
        subdir="inspections",
        url_prefix="/api/v1/uploads",
        max_mb=8,
    )

    assert url.startswith("/api/v1/uploads/inspections/")
    assert (tmp_path / "inspections").exists()


def test_settings_s3_fields_default_to_none_or_sane_defaults():
    settings = Settings()
    assert settings.s3_bucket_name is None
    assert settings.aws_access_key_id is None
    assert settings.aws_secret_access_key is None
    assert settings.aws_region == "us-east-1"
