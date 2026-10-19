from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.core import storage
from app.core.exceptions import BadRequestError


@pytest.mark.asyncio
async def test_pickup_upload_is_outside_public_uploads_and_only_resolves_safe_paths(
    tmp_path, monkeypatch
):
    settings = SimpleNamespace(
        private_uploads_dir=str(tmp_path / "private"),
        max_upload_mb=1,
        s3_private_bucket_name=None,
        s3_bucket_name=None,
        api_v1_prefix="/api/v1",
    )
    monkeypatch.setattr(storage, "get_settings", lambda: settings)
    photo = UploadFile(
        filename="retirada.jpg",
        file=BytesIO(b"test-photo"),
        headers=Headers({"content-type": "image/jpeg"}),
    )
    reference = await storage.save_private_pickup(photo)
    assert reference.startswith("private://")
    kind, bucket, path = storage.private_pickup_location(reference)
    assert kind == "local"
    assert path.startswith(str(tmp_path / "private"))
    with pytest.raises(BadRequestError):
        storage.private_pickup_location("private://../secret.txt")
    with pytest.raises(BadRequestError):
        storage.private_pickup_location("https://other.example/secret")


@pytest.mark.asyncio
async def test_pickup_evidence_refuses_the_public_s3_bucket(monkeypatch):
    settings = SimpleNamespace(
        max_upload_mb=1, s3_private_bucket_name="same", s3_bucket_name="same"
    )
    monkeypatch.setattr(storage, "get_settings", lambda: settings)
    photo = UploadFile(
        filename="retirada.jpg",
        file=BytesIO(b"photo"),
        headers=Headers({"content-type": "image/jpeg"}),
    )
    with pytest.raises(BadRequestError, match="bucket privado separado"):
        await storage.save_private_pickup(photo)
