from __future__ import annotations

import json
import re
import warnings
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from PIL import Image

from app.config import ALLOWED_IMAGE_MIME_TYPES, MAX_UPLOAD_SIZE_BYTES, UPLOAD_DIR
from app.db import get_asset as db_get_asset
from app.db import list_assets as db_list_assets
from app.db import upsert_asset
from app.responses import ApiError
from app.utils import new_id, public_asset, safe_filename, serialize_asset


ALLOWED_ASSET_TYPES = {"product_image", "scene_image", "logo", "qrcode", "bottom_bar", "background"}
MAX_PUBLIC_PRODUCT_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PUBLIC_PRODUCT_IMAGE_PIXELS = 100_000_000
MAX_PUBLIC_PRODUCT_IMAGE_EDGE = 12_000


def list_assets(
    asset_type: str | None = None,
    source: str | None = None,
    product_id: str | None = None,
) -> list[dict[str, Any]]:
    assets = db_list_assets(asset_type=asset_type, source=source, product_id=product_id)
    return [public_asset(asset) for asset in dedupe_system_brand_assets(assets) if is_public_asset_candidate(asset)]


def dedupe_system_brand_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for asset in assets:
        key = system_brand_asset_key(asset)
        if key:
            previous_index = seen.get(key)
            if previous_index is not None:
                if system_brand_asset_priority(asset) > system_brand_asset_priority(deduped[previous_index]):
                    deduped[previous_index] = asset
                continue
            seen[key] = len(deduped)
        deduped.append(asset)
    return deduped


def system_brand_asset_priority(asset: dict[str, Any]) -> int:
    public_url = str(asset.get("public_url") or "")
    file_path = str(asset.get("_file_path") or asset.get("file_path") or "")
    if "/api/v1/assets/" in public_url:
        return 30
    if "二维码汇总" in file_path or "qrcode" not in public_url.lower():
        return 20
    return 10


def system_brand_asset_key(asset: dict[str, Any]) -> str:
    asset_type = asset.get("asset_type", "")
    if asset.get("source") != "system" or asset_type not in {"logo", "qrcode", "bottom_bar"}:
        return ""

    name = str(asset.get("name") or asset.get("file_name") or "")
    if asset_type == "bottom_bar" and name in {"默认底部宣传条", "底部宣传图"}:
        return "bottom_bar:default"

    normalized = re.sub(r"(?i)logo", "", name)
    normalized = normalized.replace("二维码", "")
    normalized = re.sub(r"[\s_\-—（）()【】\[\].。·]+", "", normalized).casefold()
    return f"{asset_type}:{normalized}"


def is_public_asset_candidate(asset: dict[str, Any]) -> bool:
    if asset.get("asset_type") != "product_image" or asset.get("source") != "product_material":
        return True
    size = int(asset.get("size_bytes") or 0)
    if size <= 0 or size > MAX_PUBLIC_PRODUCT_IMAGE_BYTES:
        return False
    width = int(asset.get("width") or 0)
    height = int(asset.get("height") or 0)
    if not width or not height:
        return False
    if width * height > MAX_PUBLIC_PRODUCT_IMAGE_PIXELS:
        return False
    if max(width, height) > MAX_PUBLIC_PRODUCT_IMAGE_EDGE:
        return False
    aspect_ratio = max(width, height) / max(min(width, height), 1)
    return aspect_ratio <= 4.5


async def save_upload(
    file: UploadFile,
    asset_type: str,
    name: str | None,
    tags: str | None,
    product_id: str | None = None,
) -> dict[str, Any]:
    if asset_type not in ALLOWED_ASSET_TYPES:
        raise ApiError(
            "BAD_REQUEST",
            "asset_type 不支持",
            details={"allowed_asset_types": sorted(ALLOWED_ASSET_TYPES)},
        )
    if file.content_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise ApiError(
            "ASSET_TYPE_NOT_SUPPORTED",
            "仅支持 JPG、PNG、WEBP 图片",
            details={"content_type": file.content_type},
        )

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise ApiError(
            "ASSET_FILE_TOO_LARGE",
            "图片单张不能超过10MB",
            details={"max_size_mb": 10},
        )

    validate_image_content(content, asset_type=asset_type, content_type=file.content_type)

    day = datetime.now().strftime("%Y%m%d")
    upload_dir = UPLOAD_DIR / "demo" / day
    upload_dir.mkdir(parents=True, exist_ok=True)

    asset_id = new_id("asset")
    target_name = f"{asset_id}_{safe_filename(file.filename or 'upload.jpg', ALLOWED_IMAGE_MIME_TYPES[file.content_type])}"
    target_path = upload_dir / target_name
    target_path.write_bytes(content)

    try:
        with Image.open(target_path) as image:
            image.verify()
    except Exception as exc:
        target_path.unlink(missing_ok=True)
        raise ApiError("ASSET_TYPE_NOT_SUPPORTED", "上传文件不是有效图片") from exc

    parsed_tags = _parse_tags(tags)
    asset = serialize_asset(
        asset_id=asset_id,
        asset_type=asset_type,
        name=name or Path(file.filename or "上传素材").stem,
        file_path=target_path,
        source="user",
        tags=parsed_tags,
    )
    asset["product_id"] = product_id or ""
    upsert_asset(asset)
    return public_asset(asset)


def validate_image_content(content: bytes, *, asset_type: str, content_type: str | None) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                width, height = image.size
                if width <= 0 or height <= 0:
                    raise ApiError("ASSET_TYPE_NOT_SUPPORTED", "Uploaded image has invalid dimensions")
                if width * height > MAX_PUBLIC_PRODUCT_IMAGE_PIXELS:
                    raise ApiError(
                        "ASSET_FILE_TOO_LARGE",
                        "Uploaded image dimensions are too large",
                        details={"max_pixels": MAX_PUBLIC_PRODUCT_IMAGE_PIXELS},
                    )
                if max(width, height) > MAX_PUBLIC_PRODUCT_IMAGE_EDGE:
                    raise ApiError(
                        "ASSET_FILE_TOO_LARGE",
                        "Uploaded image edge is too large",
                        details={"max_edge": MAX_PUBLIC_PRODUCT_IMAGE_EDGE},
                    )
                image.verify()
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError("ASSET_TYPE_NOT_SUPPORTED", "Uploaded file is not a valid image") from exc

    if asset_type == "product_image":
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as image:
                    image_has_transparency(image)
        except Exception as exc:
            raise ApiError("ASSET_TYPE_NOT_SUPPORTED", "产品图不是有效图片") from exc


def image_has_transparency(image: Image.Image) -> bool:
    if image.mode in ("RGBA", "LA"):
        alpha = image.getchannel("A")
        extrema = alpha.getextrema()
        return bool(extrema and extrema[0] < 255)
    if image.mode == "P" and "transparency" in image.info:
        return True
    return False


def get_asset(asset_id: str | None) -> dict[str, Any] | None:
    if not asset_id:
        return None
    return db_get_asset(asset_id)


def require_asset(asset_id: str, expected_type: str | None = None) -> dict[str, Any]:
    asset = get_asset(asset_id)
    if not asset:
        raise ApiError("NOT_FOUND", "素材不存在", status_code=404, details={"asset_id": asset_id})
    if expected_type and asset["asset_type"] != expected_type:
        raise ApiError(
            "BAD_REQUEST",
            "素材类型不匹配",
            details={"asset_id": asset_id, "expected_type": expected_type, "actual_type": asset["asset_type"]},
        )
    return asset


def _parse_tags(tags: str | None) -> list[str]:
    if not tags:
        return []
    tags = tags.strip()
    if not tags:
        return []
    try:
        value = json.loads(tags)
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in tags.split(",") if item.strip()]
