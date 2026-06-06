from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from PIL import Image

from app.config import ALLOWED_IMAGE_MIME_TYPES, MAX_UPLOAD_SIZE_BYTES, UPLOAD_DIR
from app.responses import ApiError
from app.state import ASSETS, assets_lock
from app.utils import new_id, public_asset, safe_filename, serialize_asset


ALLOWED_ASSET_TYPES = {"product_image", "scene_image", "logo", "qrcode", "bottom_bar", "background"}


def list_assets(asset_type: str | None = None, source: str | None = None) -> list[dict[str, Any]]:
    with assets_lock:
        assets = list(ASSETS.values())
    if asset_type:
        assets = [asset for asset in assets if asset["asset_type"] == asset_type]
    if source:
        assets = [asset for asset in assets if asset.get("source") == source]
    return [public_asset(asset) for asset in sorted(assets, key=lambda item: (item["asset_type"], item["name"]))]


async def save_upload(file: UploadFile, asset_type: str, name: str | None, tags: str | None) -> dict[str, Any]:
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
    with assets_lock:
        ASSETS[asset_id] = asset
    return public_asset(asset)


def get_asset(asset_id: str | None) -> dict[str, Any] | None:
    if not asset_id:
        return None
    with assets_lock:
        return ASSETS.get(asset_id)


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
