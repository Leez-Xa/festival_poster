from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from app.config import GENERATED_DIR
from app.models import PosterTaskCreate
from app.responses import ApiError
from app.services.ai_provider import (
    AiProviderError,
    BackgroundGenerationResult,
    CopyGenerationResult,
    SceneFusionResult,
    default_safe_zones,
    get_ai_provider,
)
from app.services.assets import get_asset, require_asset
from app.services.compliance import check_copy
from app.state import TASKS, tasks_lock
from app.utils import load_font, new_id, now_iso, public_storage_url
from config.seed_data import MARKETING_NODES, PRODUCTS


CANVAS_SIZE = (1080, 1920)


def create_task(payload: PosterTaskCreate, background_tasks: BackgroundTasks) -> dict[str, str]:
    del background_tasks

    node = find_node(payload.node_id)
    product = find_product(payload.product_id)
    if payload.template_id != "template_v1_vertical_standard":
        raise ApiError(
            "BAD_REQUEST",
            "MVP仅支持 template_v1_vertical_standard",
            details={"template_id": payload.template_id},
        )
    if not payload.product_asset_ids:
        raise ApiError("BAD_REQUEST", "请至少上传1张产品图")
    if len(payload.product_asset_ids) > 5:
        raise ApiError("BAD_REQUEST", "产品图最多支持5张")

    for asset_id in payload.product_asset_ids:
        require_asset(asset_id, "product_image")
    if payload.scene_asset_id:
        require_asset(payload.scene_asset_id, "scene_image")

    logo_asset_id = payload.logo_asset_id or "asset_logo_original"
    qrcode_asset_id = payload.qrcode_asset_id or "asset_qrcode_wechat"
    bottom_bar_asset_id = payload.bottom_bar_asset_id or "asset_bottom_default"
    require_asset(logo_asset_id, "logo")
    require_asset(qrcode_asset_id, "qrcode")
    require_asset(bottom_bar_asset_id, "bottom_bar")

    copy_result, copy_source = generate_copy_or_fallback(node=node, product=product, payload=payload)
    compliance = check_copy(copy_result.title, copy_result.subtitle)
    if compliance["status"] != "passed":
        raise ApiError(
            "COMPLIANCE_BLOCKED",
            "文案合规不通过，请修改后重新生成",
            status_code=400,
            details=compliance,
        )

    task_id = new_id("task")
    task = {
        "task_id": task_id,
        "status": "pending",
        "progress": 0,
        "current_step": "等待生成",
        "poster": None,
        "error": None,
        "request": payload.model_dump(),
        "resolved_asset_ids": {
            "logo_asset_id": logo_asset_id,
            "qrcode_asset_id": qrcode_asset_id,
            "bottom_bar_asset_id": bottom_bar_asset_id,
        },
        "copy": {
            **copy_result.to_public_dict(),
            "source": copy_source,
        },
        "fusion": None,
        "compliance": compliance,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    with tasks_lock:
        TASKS[task_id] = task

    threading.Thread(target=run_task, args=(task_id,), daemon=True).start()
    return {
        "task_id": task_id,
        "status": "pending",
        "polling_url": f"/api/v1/poster-tasks/{task_id}",
    }


def get_task(task_id: str) -> dict[str, Any]:
    with tasks_lock:
        task = TASKS.get(task_id)
        if not task:
            raise ApiError("NOT_FOUND", "任务不存在", status_code=404, details={"task_id": task_id})
        return {
            "task_id": task["task_id"],
            "status": task["status"],
            "progress": task["progress"],
            "current_step": task["current_step"],
            "poster": task["poster"],
            "error": task["error"],
            "copy": task.get("copy"),
            "fusion": task.get("fusion"),
            "compliance": task.get("compliance"),
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
        }


def run_task(task_id: str) -> None:
    try:
        update_task(task_id, status="processing", progress=12, current_step="正在读取素材")
        time.sleep(0.05)
        update_task(task_id, progress=35, current_step="正在生成文案与场景提示词")
        time.sleep(0.05)
        update_task(task_id, progress=62, current_step="正在融合产品与节日场景")
        poster, fusion = compose_poster(task_id)
        update_task(
            task_id,
            status="success",
            progress=100,
            current_step="生成完成",
            poster=poster,
            fusion=fusion,
            error=None,
        )
    except Exception as exc:
        update_task(
            task_id,
            status="failed",
            progress=100,
            current_step="生成失败",
            error={"code": "GENERATION_FAILED", "message": str(exc), "details": {}},
        )


def update_task(task_id: str, **changes: Any) -> None:
    with tasks_lock:
        task = TASKS[task_id]
        task.update(changes)
        task["updated_at"] = now_iso()


def compose_poster(task_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    with tasks_lock:
        task = dict(TASKS[task_id])

    request = task["request"]
    payload = PosterTaskCreate(**request)
    copy = task["copy"]
    node = find_node(payload.node_id)
    product = find_product(payload.product_id)
    resolved = task["resolved_asset_ids"]

    product_asset = require_asset(payload.product_asset_ids[0], "product_image")
    scene_asset = require_asset(payload.scene_asset_id, "scene_image") if payload.scene_asset_id else None
    logo_asset = require_asset(resolved["logo_asset_id"], "logo")
    qrcode_asset = require_asset(resolved["qrcode_asset_id"], "qrcode")
    bottom_bar_asset = require_asset(resolved["bottom_bar_asset_id"], "bottom_bar")

    output_dir = GENERATED_DIR / datetime.now().strftime("%Y%m%d") / task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    poster_path = output_dir / "poster.jpg"
    thumbnail_path = output_dir / "thumbnail.jpg"
    composition_path = output_dir / "composition.json"

    base_canvas, fusion_meta, local_product_overlay = build_scene_canvas(
        node=node,
        product=product,
        payload=payload,
        product_asset=product_asset,
        scene_asset=scene_asset,
        output_dir=output_dir,
    )

    canvas = apply_layout_panels(base_canvas)
    draw = ImageDraw.Draw(canvas)

    paste_logo(canvas, Path(logo_asset["_file_path"]))
    draw_text_block(draw, copy["title"], copy["subtitle"], node, product)
    if local_product_overlay:
        paste_product(canvas, Path(product_asset["_file_path"]))
    paste_qrcode(canvas, Path(qrcode_asset["_file_path"]))
    paste_bottom_bar(canvas, Path(bottom_bar_asset["_file_path"]))
    draw_footer_text(
        draw,
        contact_text=payload.contact_text,
        custom_requirement=payload.custom_requirement,
    )

    rgb = canvas.convert("RGB")
    rgb.save(poster_path, "JPEG", quality=92, optimize=True)
    thumb = ImageOps.contain(rgb.copy(), (360, 640))
    thumb.save(thumbnail_path, "JPEG", quality=88, optimize=True)

    background_asset = fusion_meta.get("background_asset")
    composition = {
        "task_id": task_id,
        "template_id": payload.template_id,
        "node_id": payload.node_id,
        "product_id": payload.product_id,
        "asset_ids": {
            "product_asset_ids": payload.product_asset_ids,
            "scene_asset_id": payload.scene_asset_id,
            **resolved,
            "background_asset_id": background_asset["id"] if background_asset else None,
        },
        "copy": copy,
        "compliance": task.get("compliance"),
        "fusion": build_composition_fusion(
            payload=payload,
            product_asset=product_asset,
            scene_asset=scene_asset,
            fusion_meta=fusion_meta,
        ),
    }
    composition_path.write_text(json.dumps(composition, ensure_ascii=False, indent=2), encoding="utf-8")

    poster = {
        "id": new_id("poster"),
        "title": f"{node['name']}海报_{datetime.now().strftime('%Y%m%d')}",
        "jpg_url": public_storage_url(poster_path),
        "thumbnail_url": public_storage_url(thumbnail_path),
        "width": CANVAS_SIZE[0],
        "height": CANVAS_SIZE[1],
        "file_size_bytes": poster_path.stat().st_size,
        "composition_json_url": public_storage_url(composition_path),
        "copy": {
            "title": copy["title"],
            "subtitle": copy["subtitle"],
        },
    }
    return poster, composition["fusion"]


def build_scene_canvas(
    *,
    node: dict[str, Any],
    product: dict[str, Any],
    payload: PosterTaskCreate,
    product_asset: dict[str, Any],
    scene_asset: dict[str, Any] | None,
    output_dir: Path,
) -> tuple[Image.Image, dict[str, Any], bool]:
    provider = get_ai_provider()
    scene_reference_path = Path(scene_asset["_file_path"]) if scene_asset else None
    safe_zones = default_safe_zones()

    try:
        scene_result = provider.generate_scene_with_product(
            node=node,
            product=product,
            payload=payload,
            transparent_product_png=Path(product_asset["_file_path"]),
            product_asset_id=product_asset["id"],
            output_dir=output_dir,
            scene_reference_path=scene_reference_path,
            canvas_size=CANVAS_SIZE,
            safe_zones=safe_zones,
        )
        return (
            load_canvas_image(scene_result.image_path, blur_radius=0.0, tint_color=None, tint_alpha=0.0),
            {
                "mode": "scene_with_product",
                "status": "success",
                "provider": scene_result.provider,
                "model": scene_result.model,
                "prompt_provider": scene_result.prompt_provider,
                "positive_prompt": scene_result.prompt,
                "negative_prompt": scene_result.negative_prompt,
                "preserve_product_pixels": scene_result.preserve_product_pixels,
                "warnings": scene_result.warnings,
                "fallback": {"used": False, "type": "none", "reason": None},
                "background_asset": None,
            },
            False,
        )
    except AiProviderError as fusion_exc:
        try:
            background_result = provider.generate_background_only(node=node, payload=payload, output_dir=output_dir)
            return (
                load_canvas_image(
                    background_result.image_path,
                    blur_radius=1.4,
                    tint_color="#F7FBFA",
                    tint_alpha=0.14,
                ),
                {
                    "mode": "background_only_local_compose",
                    "status": "fallback",
                    "provider": background_result.provider,
                    "model": background_result.model,
                    "prompt_provider": "n/a",
                    "positive_prompt": background_result.prompt,
                    "negative_prompt": background_result.negative_prompt,
                    "preserve_product_pixels": True,
                    "warnings": [],
                    "fallback": {
                        "used": True,
                        "type": "background_generation_plus_local_layout",
                        "reason": str(fusion_exc),
                    },
                    "background_asset": None,
                },
                True,
            )
        except AiProviderError as background_exc:
            background_asset = get_asset(node.get("background_asset_id")) or get_asset("asset_bg_anniversary")
            return (
                load_canvas_image(
                    Path(background_asset["_file_path"]) if background_asset else None,
                    blur_radius=2.2,
                    tint_color="#F7FBFA",
                    tint_alpha=0.18,
                ),
                {
                    "mode": "local_background_local_compose",
                    "status": "fallback",
                    "provider": "local_fallback",
                    "model": "local_library",
                    "prompt_provider": "n/a",
                    "positive_prompt": "",
                    "negative_prompt": "",
                    "preserve_product_pixels": True,
                    "warnings": [],
                    "fallback": {
                        "used": True,
                        "type": "local_background_plus_local_layout",
                        "reason": f"scene_fusion_failed={fusion_exc}; background_generation_failed={background_exc}",
                    },
                    "background_asset": background_asset,
                },
                True,
            )


def build_composition_fusion(
    *,
    payload: PosterTaskCreate,
    product_asset: dict[str, Any],
    scene_asset: dict[str, Any] | None,
    fusion_meta: dict[str, Any],
) -> dict[str, Any]:
    return {
        "mode": fusion_meta["mode"],
        "status": fusion_meta["status"],
        "provider": fusion_meta["provider"],
        "model": fusion_meta["model"],
        "prompt_provider": fusion_meta["prompt_provider"],
        "used_product_asset_id": product_asset["id"],
        "product_asset_type": product_asset["asset_type"],
        "scene_asset_id": scene_asset["id"] if scene_asset else None,
        "scene_prompt_input": payload.scene_prompt,
        "custom_requirement": payload.custom_requirement,
        "positive_prompt": fusion_meta["positive_prompt"],
        "negative_prompt": fusion_meta["negative_prompt"],
        "preserve_product_pixels": fusion_meta["preserve_product_pixels"],
        "safe_zones": {
            "top_logo": list(default_safe_zones()["top_logo"]),
            "bottom_copy_qrcode": list(default_safe_zones()["bottom_copy_qrcode"]),
        },
        "fallback": fusion_meta["fallback"],
        "warnings": fusion_meta.get("warnings", []),
    }


def load_canvas_image(
    source: dict[str, Any] | Path | None,
    *,
    blur_radius: float,
    tint_color: str | None,
    tint_alpha: float,
) -> Image.Image:
    if source:
        try:
            image_path = source if isinstance(source, Path) else Path(source["_file_path"])
            with Image.open(image_path) as image:
                fitted = ImageOps.fit(image.convert("RGB"), CANVAS_SIZE, method=Image.Resampling.LANCZOS)
                if blur_radius > 0:
                    fitted = fitted.filter(ImageFilter.GaussianBlur(radius=blur_radius))
                if tint_color and tint_alpha > 0:
                    tint = Image.new("RGB", CANVAS_SIZE, tint_color)
                    fitted = Image.blend(fitted, tint, tint_alpha)
                return fitted.convert("RGBA")
        except Exception:
            pass
    return Image.new("RGBA", CANVAS_SIZE, "#F2F8F7")


def apply_layout_panels(canvas: Image.Image) -> Image.Image:
    overlay = Image.new("RGBA", CANVAS_SIZE, (255, 255, 255, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle((72, 190, 1008, 775), radius=42, fill=(255, 255, 255, 214))
    overlay_draw.rounded_rectangle((72, 1320, 1008, 1825), radius=34, fill=(255, 255, 255, 228))
    return Image.alpha_composite(canvas.convert("RGBA"), overlay)


def draw_text_block(draw: ImageDraw.ImageDraw, title: str, subtitle: str, node: dict[str, Any], product: dict[str, Any]) -> None:
    title_font = load_font(92, bold=True)
    subtitle_font = load_font(42)
    meta_font = load_font(30)
    body_font = load_font(34)
    accent = (node.get("colors") or ["#0F766E"])[0]

    draw.text((108, 260), node["name"], font=meta_font, fill=accent)
    draw_multiline(draw, title, (108, 330), title_font, "#12312E", max_width=780, line_gap=12)
    draw_multiline(draw, subtitle, (110, 555), subtitle_font, "#335C57", max_width=820, line_gap=10)
    draw.rounded_rectangle((108, 705, 520, 755), radius=25, fill=accent)
    draw.text((132, 714), str(product.get("short_description", ""))[:20], font=body_font, fill="#FFFFFF")


def paste_logo(canvas: Image.Image, logo_path: Path) -> None:
    try:
        with Image.open(logo_path) as logo:
            logo_image = logo.convert("RGBA")
            logo_image.thumbnail((240, 110), Image.Resampling.LANCZOS)
            canvas.alpha_composite(logo_image, (92, 82))
    except Exception:
        draw = ImageDraw.Draw(canvas)
        draw.text((92, 92), "PUDOW", font=load_font(46, bold=True), fill="#0F766E")


def paste_product(canvas: Image.Image, product_path: Path) -> None:
    with Image.open(product_path) as product_image:
        product = product_image.convert("RGBA")
        product.thumbnail((760, 620), Image.Resampling.LANCZOS)
        shadow = Image.new("RGBA", product.size, (0, 0, 0, 0))
        alpha = product.getchannel("A")
        shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(16)))
        x = (CANVAS_SIZE[0] - product.width) // 2
        y = 805
        canvas.alpha_composite(shadow, (x + 18, y + 28))
        canvas.alpha_composite(product, (x, y))


def paste_qrcode(canvas: Image.Image, qrcode_path: Path) -> None:
    try:
        with Image.open(qrcode_path) as qrcode_image:
            qrcode = qrcode_image.convert("RGB")
            qrcode = ImageOps.contain(qrcode, (230, 230), Image.Resampling.LANCZOS).convert("RGBA")
            draw = ImageDraw.Draw(canvas)
            draw.rounded_rectangle((744, 1480, 1000, 1736), radius=26, fill="#FFFFFF")
            canvas.alpha_composite(qrcode, (757, 1493))
            draw.text((766, 1748), "扫码咨询", font=load_font(30, bold=True), fill="#12312E")
    except Exception:
        pass


def paste_bottom_bar(canvas: Image.Image, bottom_bar_path: Path) -> None:
    draw = ImageDraw.Draw(canvas)
    try:
        with Image.open(bottom_bar_path) as bottom_image:
            bottom = ImageOps.fit(bottom_image.convert("RGB"), (936, 170), method=Image.Resampling.LANCZOS).convert("RGBA")
            canvas.alpha_composite(bottom, (72, 1650))
    except Exception:
        draw.rounded_rectangle((72, 1650, 1008, 1820), radius=28, fill="#0F766E")
        draw.text((112, 1702), "朴道 PUDOW 健康水专家", font=load_font(46, bold=True), fill="#FFFFFF")


def draw_footer_text(draw: ImageDraw.ImageDraw, *, contact_text: str, custom_requirement: str) -> None:
    font = load_font(34)
    small = load_font(26)
    primary_line = contact_text.strip() or "健康饮水方案，适配家庭与商务场景"
    draw.text((108, 1394), primary_line[:28], font=font, fill="#12312E")
    secondary = custom_requirement.strip()
    if secondary:
        draw_multiline(draw, secondary[:60], (108, 1450), small, "#54716D", max_width=580, line_gap=6)


def draw_multiline(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[int, int],
    font: Any,
    fill: str,
    max_width: int,
    line_gap: int,
) -> None:
    x, y = xy
    for line in wrap_text(draw, text, font, max_width):
        draw.text((x, y), line, font=font, fill=fill)
        bbox = draw.textbbox((x, y), line, font=font)
        y += bbox[3] - bbox[1] + line_gap


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: Any, max_width: int) -> list[str]:
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for char in text:
        trial = current + char
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def make_copy(node: dict[str, Any], product: dict[str, Any], payload: PosterTaskCreate) -> tuple[str, str]:
    title = payload.copy_preference.title.strip()
    subtitle = payload.copy_preference.subtitle.strip()
    if not title:
        title = f"{node['name']}好水相伴"
    if not subtitle:
        subtitle = f"{product['name']}，让真实产品融入每一个营销场景"
    return title[:18], subtitle[:42]


def generate_copy_or_fallback(
    *,
    node: dict[str, Any],
    product: dict[str, Any],
    payload: PosterTaskCreate,
) -> tuple[CopyGenerationResult, str]:
    try:
        copy_result = get_ai_provider().generate_copy(node=node, product=product, payload=payload)
        if not copy_result.title or not copy_result.subtitle:
            raise AiProviderError("AI文案结构缺少 title 或 subtitle")
        if copy_result.risk_level not in {"low", "medium", "high"}:
            raise AiProviderError("AI文案 risk_level 字段异常")
        if copy_result.risk_level == "high":
            raise AiProviderError("AI文案返回高风险")
        return copy_result, "ai_provider"
    except AiProviderError:
        title, subtitle = make_copy(node, product, payload)
        return (
            CopyGenerationResult(
                title=title,
                subtitle=subtitle,
                alternatives=[],
                risk_level="low",
                provider="local_fallback",
            ),
            "local_fallback",
        )


def find_node(node_id: str) -> dict[str, Any]:
    for node in MARKETING_NODES:
        if node["id"] == node_id:
            return node
    raise ApiError("NOT_FOUND", "营销节点不存在", status_code=404, details={"node_id": node_id})


def find_product(product_id: str) -> dict[str, Any]:
    for product in PRODUCTS:
        if product["id"] == product_id:
            return product
    raise ApiError("NOT_FOUND", "产品不存在", status_code=404, details={"product_id": product_id})
