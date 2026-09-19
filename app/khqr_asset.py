from __future__ import annotations

import hashlib
import io
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .config import get_settings
from .khqr_payload import KhqrPayloadError, KhqrValidation, validate_static_khqr


class KhqrAssetError(ValueError):
    pass


@dataclass(frozen=True)
class KhqrAsset:
    payload: str
    validation: KhqrValidation
    media_type: str
    filename: str
    sha256: str
    width: int
    height: int


def _root() -> Path:
    value = Path(get_settings().khqr_asset_root).expanduser()
    if value.is_absolute():
        return value
    return (Path(__file__).resolve().parents[1] / value).resolve()


def asset_dir(source_id: str) -> Path:
    safe = "".join(ch for ch in source_id if ch.isalnum() or ch in "-_")
    if not safe or safe != source_id:
        raise KhqrAssetError("invalid payment source id")
    return _root() / safe
def image_path(source_id: str) -> Path:
    return asset_dir(source_id) / "khqr-image"


def metadata_path(source_id: str) -> Path:
    return asset_dir(source_id) / "khqr-image.json"


def image_exists(source_id: str) -> bool:
    return image_path(source_id).is_file() and metadata_path(source_id).is_file()


def image_metadata(source_id: str) -> dict:
    path = metadata_path(source_id)
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _decode_image(data: bytes) -> tuple[str, KhqrValidation, int, int, str]:
    try:
        from PIL import Image
        import zxingcpp
    except ImportError as exc:
        raise KhqrAssetError("QR image decoder is unavailable") from exc

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise KhqrAssetError("uploaded file is not a readable image") from exc

    width, height = image.size
    detected_media_type = {
        "PNG": "image/png",
        "JPEG": "image/jpeg",
        "WEBP": "image/webp",
    }.get((image.format or "").upper())
    if not detected_media_type:
        raise KhqrAssetError("upload a PNG, JPEG, or WebP image")
    if width < 120 or height < 120:
        raise KhqrAssetError("QR image is too small to verify")
    if width > 8000 or height > 8000:
        raise KhqrAssetError("QR image dimensions are too large")

    try:
        results = zxingcpp.read_barcodes(image)
    except Exception as exc:
        raise KhqrAssetError("could not scan the uploaded QR image") from exc

    candidates: list[str] = []
    for result in results:
        text = (getattr(result, "text", "") or "").strip()
        if text:
            candidates.append(text)

    if not candidates:
        raise KhqrAssetError("no QR code was detected in the uploaded image")

    validation_error: Exception | None = None
    for payload in candidates:
        try:
            validation = validate_static_khqr(payload)
            return payload, validation, width, height, detected_media_type
        except KhqrPayloadError as exc:
            validation_error = exc

    message = "the detected QR is not a valid reusable Cambodia KHQR"
    if validation_error:
        message += f": {validation_error}"
    raise KhqrAssetError(message)


def save_uploaded_khqr(
    source_id: str,
    data: bytes,
    *,
    filename: str | None,
    media_type: str | None,
) -> KhqrAsset:
    settings = get_settings()
    if not data:
        raise KhqrAssetError("QR image is empty")
    if len(data) > settings.khqr_upload_max_bytes:
        raise KhqrAssetError("QR image is larger than the allowed upload size")

    payload, validation, width, height, mime = _decode_image(data)
    digest = hashlib.sha256(data).hexdigest()
    name = (filename or "khqr-image").strip()[:240] or "khqr-image"

    directory = asset_dir(source_id)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass

    image_target = image_path(source_id)
    meta_target = metadata_path(source_id)
    image_temp = directory / ".khqr-image.tmp"
    meta_temp = directory / ".khqr-image.json.tmp"

    image_temp.write_bytes(data)
    os.chmod(image_temp, 0o600)
    metadata = {
        "filename": name,
        "media_type": mime,
        "sha256": digest,
        "width": width,
        "height": height,
    }
    meta_temp.write_text(
        json.dumps(metadata, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(meta_temp, 0o600)
    os.replace(image_temp, image_target)
    os.replace(meta_temp, meta_target)
    os.chmod(image_target, 0o600)
    os.chmod(meta_target, 0o600)

    return KhqrAsset(
        payload=payload,
        validation=validation,
        media_type=mime,
        filename=name,
        sha256=digest,
        width=width,
        height=height,
    )
