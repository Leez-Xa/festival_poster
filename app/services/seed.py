from __future__ import annotations

from pathlib import Path

from app.config import ROOT_DIR, SYSTEM_DIR, ensure_storage_dirs
from app.state import ASSETS, assets_lock
from app.utils import copy_or_create_placeholder, serialize_asset
from config.seed_data import BACKGROUND_SOURCES, SYSTEM_ASSET_SOURCES


def initialize_seed_assets() -> None:
    ensure_storage_dirs()
    with assets_lock:
        ASSETS.clear()
        for item in SYSTEM_ASSET_SOURCES:
            target = SYSTEM_DIR / item["file_name"]
            copy_or_create_placeholder(
                ROOT_DIR / Path(item["source_path"]),
                target,
                item["name"],
                size=(1080, 320) if item["asset_type"] == "bottom_bar" else (800, 800),
            )
            ASSETS[item["id"]] = serialize_asset(
                asset_id=item["id"],
                asset_type=item["asset_type"],
                name=item["name"],
                file_path=target,
                source=item["source"],
                tags=item.get("tags", []),
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
            ASSETS[item["id"]] = serialize_asset(
                asset_id=item["id"],
                asset_type="background",
                name=item["name"],
                file_path=target,
                source="system",
                tags=item.get("tags", []),
            )
