from __future__ import annotations

import mimetypes
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont

from app.config import ROOT_DIR


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4()}"


def public_storage_url(path: Path) -> str:
    relative = path.resolve().relative_to((ROOT_DIR / "storage").resolve())
    return "/storage/" + relative.as_posix()


def safe_filename(filename: str, fallback_ext: str = ".jpg") -> str:
    ext = Path(filename or "").suffix.lower() or fallback_ext
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        ext = fallback_ext
    return f"{uuid4().hex}{ext}"


def image_metadata(path: Path) -> tuple[int | None, int | None, str]:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    try:
        with Image.open(path) as image:
            return image.width, image.height, mime_type
    except Exception:
        return None, None, mime_type


def copy_or_create_placeholder(
    source_path: Path,
    target_path: Path,
    placeholder_text: str,
    size: tuple[int, int] = (1080, 1920),
    colors: tuple[str, str] = ("#E7F5F2", "#0F766E"),
) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        return
    if source_path.exists():
        shutil.copyfile(source_path, target_path)
        return
    create_placeholder_image(target_path, placeholder_text, size=size, colors=colors)


def create_placeholder_image(
    path: Path,
    text: str,
    size: tuple[int, int],
    colors: tuple[str, str] = ("#E7F5F2", "#0F766E"),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, colors[0])
    draw = ImageDraw.Draw(image)
    for y in range(size[1]):
        ratio = y / max(size[1] - 1, 1)
        base = _hex_to_rgb(colors[0])
        accent = _hex_to_rgb(colors[1])
        mixed = tuple(int(base[i] * (1 - ratio * 0.28) + accent[i] * ratio * 0.28) for i in range(3))
        draw.line([(0, y), (size[0], y)], fill=mixed)

    font = load_font(72)
    sub_font = load_font(34)
    text_bbox = draw.textbbox((0, 0), text, font=font)
    x = (size[0] - (text_bbox[2] - text_bbox[0])) // 2
    y = size[1] // 2 - 70
    draw.rounded_rectangle((110, y - 70, size[0] - 110, y + 170), radius=36, fill=(255, 255, 255))
    draw.text((x, y), text, font=font, fill=colors[1])
    sub = "PUDOW Festival Poster MVP"
    sub_bbox = draw.textbbox((0, 0), sub, font=sub_font)
    draw.text(((size[0] - (sub_bbox[2] - sub_bbox[0])) // 2, y + 96), sub, font=sub_font, fill="#334155")
    image.save(path, quality=92)


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    ]
    for font_path in candidates:
        if font_path.exists():
            return ImageFont.truetype(str(font_path), size=size)
    return ImageFont.load_default()


def serialize_asset(
    *,
    asset_id: str,
    asset_type: str,
    name: str,
    file_path: Path,
    source: str,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    width, height, mime_type = image_metadata(file_path)
    return {
        "id": asset_id,
        "asset_type": asset_type,
        "name": name,
        "file_name": file_path.name,
        "public_url": public_storage_url(file_path),
        "mime_type": mime_type,
        "size_bytes": file_path.stat().st_size if file_path.exists() else 0,
        "width": width,
        "height": height,
        "tags": tags or [],
        "source": source,
        "created_at": now_iso(),
        "_file_path": str(file_path),
    }


def public_asset(asset: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in asset.items() if not key.startswith("_")}


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))
