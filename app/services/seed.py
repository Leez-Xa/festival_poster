from __future__ import annotations

import hashlib
import warnings
from pathlib import Path
from typing import Any

from PIL import Image

from app.config import ROOT_DIR, SYSTEM_DIR, ensure_storage_dirs
from app.db import upsert_asset, upsert_product
from app.utils import copy_or_create_placeholder, image_metadata, now_iso, serialize_asset
from config.seed_data import BACKGROUND_SOURCES, PRODUCTS, SYSTEM_ASSET_SOURCES

MAX_INDEX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_INDEX_IMAGE_PIXELS = 50_000_000
MAX_INDEX_IMAGE_EDGE = 12_000
PRODUCT_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
DETAIL_IMAGE_TERMS = ("详情页", "详情图", "页面", "长图", "易拉宝", "折页", "看稿", "._")


def initialize_seed_assets() -> None:
    ensure_storage_dirs()
    seed_products()
    seed_brand_assets()
    seed_product_material_assets()


def seed_products() -> None:
    for product in PRODUCTS:
        upsert_product({**product, "source": "seed"})

    product_root = ROOT_DIR / "素材" / "产品资料"
    if not product_root.exists():
        return
    for directory in sorted((path for path in product_root.iterdir() if path.is_dir()), key=lambda item: item.name):
        product_id = product_id_from_name(directory.name)
        upsert_product(
            {
                "id": product_id,
                "name": directory.name,
                "model": directory.name,
                "category": "产品资料",
                "short_description": f"{directory.name} 产品素材",
                "selling_points": [],
                "source": "material_folder",
                "material_dir": str(directory),
            }
        )


def seed_brand_assets() -> None:
    for item in SYSTEM_ASSET_SOURCES:
        target = SYSTEM_DIR / item["file_name"]
        copy_or_create_placeholder(
            ROOT_DIR / Path(item["source_path"]),
            target,
            item["name"],
            size=(1080, 320) if item["asset_type"] == "bottom_bar" else (800, 800),
        )
        upsert_asset(
            serialize_asset(
                asset_id=item["id"],
                asset_type=item["asset_type"],
                name=item["name"],
                file_path=target,
                source=item["source"],
                tags=item.get("tags", []),
            )
        )

    for item in BACKGROUND_SOURCES:
        target = SYSTEM_DIR / item["file_name"]
        copy_or_create_placeholder(
            ROOT_DIR / Path(item["source_path"]),
            target,
            item["name"],
            size=(1080, 1920),
            colors=("#F4FBF8", "#0F766E"),
        )
        upsert_asset(
            serialize_asset(
                asset_id=item["id"],
                asset_type="background",
                name=item["name"],
                file_path=target,
                source="system",
                tags=item.get("tags", []),
            )
        )


def seed_product_material_assets() -> None:
    product_root = ROOT_DIR / "素材" / "产品资料"
    if not product_root.exists():
        return

    for directory in sorted((path for path in product_root.iterdir() if path.is_dir()), key=lambda item: item.name):
        product_id = product_id_from_name(directory.name)
        candidates = select_product_images(directory)
        for index, image_path in enumerate(candidates, start=1):
            asset_id = asset_id_for_path(image_path)
            upsert_asset(
                serialize_existing_file_asset(
                    asset_id=asset_id,
                    asset_type="product_image",
                    name=f"{directory.name} 产品图 {index}",
                    file_path=image_path,
                    source="product_material",
                    product_id=product_id,
                    tags=[directory.name, "系统产品素材"],
                )
            )


def select_product_images(directory: Path, limit: int = 4) -> list[Path]:
    image_paths = [
        path
        for path in directory.rglob("*")
        if is_indexable_product_image(path)
    ]
    ranked = sorted(image_paths, key=image_rank)
    return ranked[:limit]


def is_indexable_product_image(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() not in PRODUCT_IMAGE_SUFFIXES:
        return False
    if path.name.startswith("._") or path.name == ".DS_Store":
        return False
    text = str(path).lower()
    if any(term.lower() in text for term in DETAIL_IMAGE_TERMS):
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if not 60 * 1024 <= size <= MAX_INDEX_IMAGE_BYTES:
        return False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                width, height = image.size
    except Exception:
        return False
    if width <= 0 or height <= 0:
        return False
    if width * height > MAX_INDEX_IMAGE_PIXELS:
        return False
    if max(width, height) > MAX_INDEX_IMAGE_EDGE:
        return False
    aspect_ratio = max(width, height) / max(min(width, height), 1)
    return aspect_ratio <= 4.5


def image_rank(path: Path) -> tuple[int, int, str]:
    text = str(path).lower()
    score = 100
    positive_terms = ("高清", "产品高清图", "主图", "头图", "正视图", "侧视图", "产品")
    for term in positive_terms:
        if term.lower() in text:
            score -= 12
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return (score, -size, str(path))


def serialize_existing_file_asset(
    *,
    asset_id: str,
    asset_type: str,
    name: str,
    file_path: Path,
    source: str,
    product_id: str,
    tags: list[str],
) -> dict[str, Any]:
    width, height, mime_type = image_metadata(file_path)
    stamp = now_iso()
    return {
        "id": asset_id,
        "asset_type": asset_type,
        "name": name,
        "file_name": file_path.name,
        "public_url": f"/api/v1/assets/{asset_id}/file",
        "mime_type": mime_type,
        "size_bytes": file_path.stat().st_size if file_path.exists() else 0,
        "width": width,
        "height": height,
        "tags": tags,
        "source": source,
        "product_id": product_id,
        "created_at": stamp,
        "updated_at": stamp,
        "_file_path": str(file_path),
    }


def product_id_from_name(name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    return f"product_material_{digest}"


def asset_id_for_path(path: Path) -> str:
    try:
        relative = path.resolve().relative_to(ROOT_DIR.resolve())
    except ValueError:
        relative = path.resolve()
    digest = hashlib.sha1(str(relative).encode("utf-8")).hexdigest()[:16]
    return f"asset_product_{digest}"
