from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from app.config import GENERATED_DIR, STORAGE_DIR, get_ai_settings
from app.db import get_product as db_get_product
from app.db import get_task as db_get_task
from app.db import update_task as db_update_task
from app.db import upsert_task
from app.layout import get_template_layout
from app.models import PosterTaskCreate, PosterTaskRerenderRequest
from app.responses import ApiError
from app.services.ai_provider import BrandReferenceAsset, AiProviderError, CopyGenerationResult, default_safe_zones, get_ai_provider
from app.services.assets import get_asset, require_asset
from app.services.compliance import check_copy
from app.utils import load_font, new_id, now_iso, public_storage_url
from config.seed_data import MARKETING_NODES, PRODUCTS


CANVAS_SIZE = (1080, 1920)
DEFAULT_BOTTOM_BAR_QR_SLOTS = [
    {
        "id": "bottom_bar_public_account",
        "label": "公众号",
        "box_ratio": (0.690, 0.192, 0.794, 0.629),
    },
    {
        "id": "bottom_bar_video",
        "label": "视频号",
        "box_ratio": (0.818, 0.176, 0.926, 0.635),
    },
]
QRCODE_BOTTOM_MAX_SIDE_RATIO = 0.60
QRCODE_BOTTOM_LABEL_RESERVE_RATIO = 0.30
QRCODE_BOTTOM_LABEL_RESERVE_MIN = 44
QRCODE_BOTTOM_TOP_PADDING_RATIO = 0.04
QRCODE_BOTTOM_TOP_PADDING_MIN = 6
_TASK_LOCKS: dict[str, threading.Lock] = {}
_TASK_LOCKS_GUARD = threading.Lock()


def create_task(payload: PosterTaskCreate, background_tasks: BackgroundTasks) -> dict[str, str]:
    del background_tasks

    resolve_node(payload)
    find_product(payload.product_id)
    if payload.template_id != "template_v1_vertical_standard":
        raise ApiError(
            "BAD_REQUEST",
            "MVP only supports template_v1_vertical_standard",
            details={"template_id": payload.template_id},
        )

    asset_mode, product_assets, scene_asset = resolve_task_assets(payload)

    logo_asset_id = payload.logo_asset_id or "asset_logo_original"
    qrcode_asset_ids = normalize_qrcode_asset_ids(payload)
    qrcode_asset_id = qrcode_asset_ids[0] if qrcode_asset_ids else None
    bottom_bar_asset_id = payload.bottom_bar_asset_id or "asset_bottom_default"
    require_asset(logo_asset_id, "logo")
    for asset_id in qrcode_asset_ids:
        require_asset(asset_id, "qrcode")
    require_asset(bottom_bar_asset_id, "bottom_bar")

    validate_copy_preference(payload)

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
            "asset_mode": asset_mode,
            "product_asset_ids": [asset["id"] for asset in product_assets],
            "product_asset_id": product_assets[0]["id"] if product_assets else None,
            "scene_asset_id": scene_asset["id"] if scene_asset else None,
            "scene_reference_asset_id": scene_asset["id"] if scene_asset and asset_mode == "product_image" else None,
            "logo_asset_id": logo_asset_id,
            "qrcode_asset_id": qrcode_asset_id,
            "qrcode_asset_ids": qrcode_asset_ids,
            "bottom_bar_asset_id": bottom_bar_asset_id,
        },
        "copy": None,
        "fusion": None,
        "compliance": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    upsert_task(task)

    threading.Thread(target=run_task, args=(task_id,), daemon=True).start()
    return {
        "task_id": task_id,
        "status": "pending",
        "polling_url": f"/api/v1/poster-tasks/{task_id}",
    }


def resolve_task_assets(payload: PosterTaskCreate) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
    if payload.scene_asset_id:
        scene_asset = require_asset(payload.scene_asset_id, "scene_image")
        return "scene_image", [], scene_asset

    if not payload.product_asset_ids:
        raise ApiError("BAD_REQUEST", "请选择系统产品图，或上传产品图/整张场景图")
    if len(payload.product_asset_ids) > 5:
        raise ApiError("BAD_REQUEST", "MVP 单次最多支持 5 张产品图")
    product_assets = [require_asset(asset_id, "product_image") for asset_id in payload.product_asset_ids]
    return "product_image", product_assets, None


def get_task(task_id: str) -> dict[str, Any]:
    task = db_get_task(task_id)
    if not task:
        raise ApiError("NOT_FOUND", "Task not found", status_code=404, details={"task_id": task_id})
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


def get_task_composition(task_id: str) -> dict[str, Any]:
    task = db_get_task(task_id)
    if not task:
        raise ApiError("NOT_FOUND", "Task not found", status_code=404, details={"task_id": task_id})
    poster = task.get("poster") or {}
    poster_path = storage_url_to_path(poster.get("jpg_url"))
    if not poster_path:
        raise ApiError("TASK_NOT_READY", "Composition is not ready.", status_code=409, details={"task_id": task_id})
    composition_path = poster_path.with_name(poster_path.name.replace("poster", "composition", 1)).with_suffix(".json")
    try:
        composition_path.relative_to(STORAGE_DIR.resolve())
    except ValueError as exc:
        raise ApiError("FORBIDDEN", "Composition path is not allowed.", status_code=403) from exc
    if not composition_path.exists() or not composition_path.is_file():
        raise ApiError("NOT_FOUND", "Composition file not found.", status_code=404, details={"task_id": task_id})
    try:
        return json.loads(composition_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ApiError("BAD_RESPONSE", "Composition file is invalid.", status_code=500, details={"task_id": task_id}) from exc


def run_task(task_id: str) -> None:
    try:
        update_task(task_id, status="processing", progress=12, current_step="正在读取上传素材")
        time.sleep(0.05)
        update_task(task_id, progress=35, current_step="正在生成场景提示词和文案")
        prepare_task_copy(task_id)
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
    except ApiError as exc:
        update_task(
            task_id,
            status="failed",
            progress=100,
            current_step="生成失败",
            error={"code": exc.code, "message": exc.message, "details": exc.details},
        )
    except Exception as exc:
        update_task(
            task_id,
            status="failed",
            progress=100,
            current_step="生成失败",
            error={"code": "GENERATION_FAILED", "message": "海报生成失败，请检查素材后重试", "details": {"error": str(exc)}},
        )


def update_task(task_id: str, **changes: Any) -> None:
    db_update_task(task_id, **changes)


def prepare_task_copy(task_id: str) -> None:
    task = db_get_task(task_id)
    if not task:
        raise ApiError("NOT_FOUND", "Task not found", status_code=404, details={"task_id": task_id})
    payload = PosterTaskCreate(**task["request"])
    node = resolve_node(payload)
    product = find_product(payload.product_id)
    copy_result, copy_source = generate_copy_or_fallback(node=node, product=product, payload=payload)
    compliance = check_copy(copy_result.title, copy_result.subtitle)
    if compliance["status"] != "passed":
        raise ApiError(
            "COMPLIANCE_BLOCKED",
            "文案合规检查未通过，已停止生成海报",
            status_code=400,
            details=compliance,
        )
    update_task(
        task_id,
        copy={
            **copy_result.to_public_dict(),
            "source": copy_source,
        },
        compliance=compliance,
    )


def get_task_lock(task_id: str) -> threading.Lock:
    with _TASK_LOCKS_GUARD:
        if task_id not in _TASK_LOCKS:
            _TASK_LOCKS[task_id] = threading.Lock()
        return _TASK_LOCKS[task_id]


def rerender_task(task_id: str, payload: PosterTaskRerenderRequest) -> dict[str, Any]:
    title = payload.title.strip()
    subtitle = payload.subtitle.strip()
    if not title or not subtitle:
        raise ApiError("BAD_REQUEST", "主标题和副标题不能为空")

    compliance = check_copy(title, subtitle)
    if compliance["status"] != "passed":
        raise ApiError(
            "COMPLIANCE_BLOCKED",
            "文案合规检查未通过，已禁止重新渲染和下载",
            status_code=400,
            details=compliance,
        )

    with get_task_lock(task_id):
        task = db_get_task(task_id)
        if not task:
            raise ApiError("NOT_FOUND", "Task not found", status_code=404, details={"task_id": task_id})
        if task["status"] != "success":
            raise ApiError("BAD_REQUEST", "只有生成成功的任务可以重新渲染文案", details={"task_id": task_id})

        copy = {
            **(task.get("copy") or {}),
            "title": title,
            "subtitle": subtitle,
            "source": "user_preview_edit",
            "revision": new_id("copyrev"),
        }
        poster, fusion = compose_poster(
            task_id,
            output_variant=f"edit_{datetime.now().strftime('%H%M%S_%f')}_{copy['revision']}",
            copy_override=copy,
            compliance_override=compliance,
            reuse_existing_scene=True,
        )
        db_update_task(
            task_id,
            copy=copy,
            poster=poster,
            fusion=fusion,
            compliance=compliance,
            current_step="预览文案已重新合成",
        )
    return get_task(task_id)


def compose_poster(
    task_id: str,
    output_variant: str = "",
    *,
    copy_override: dict[str, Any] | None = None,
    compliance_override: dict[str, Any] | None = None,
    reuse_existing_scene: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    task = db_get_task(task_id)
    if not task:
        raise ApiError("NOT_FOUND", "Task not found", status_code=404, details={"task_id": task_id})

    payload = PosterTaskCreate(**task["request"])
    copy = copy_override or task["copy"]
    node = resolve_node(payload)
    product = find_product(payload.product_id)
    resolved = task["resolved_asset_ids"]
    layout = get_template_layout(payload.template_id)
    asset_mode = resolved.get("asset_mode") or ("scene_image" if resolved.get("scene_asset_id") else "product_image")

    product_asset = require_asset(resolved["product_asset_id"], "product_image") if resolved.get("product_asset_id") else None
    scene_reference_asset_id = resolved.get("scene_reference_asset_id")
    scene_reference_asset = require_asset(scene_reference_asset_id, "scene_image") if scene_reference_asset_id else None
    scene_asset = require_asset(resolved["scene_asset_id"], "scene_image") if resolved.get("scene_asset_id") else None
    logo_asset = require_asset(resolved["logo_asset_id"], "logo")
    qrcode_assets = [
        require_asset(asset_id, "qrcode")
        for asset_id in (resolved.get("qrcode_asset_ids") or ([resolved["qrcode_asset_id"]] if resolved.get("qrcode_asset_id") else []))
    ]
    qrcode_asset = qrcode_assets[0] if qrcode_assets else None
    bottom_bar_asset = require_asset(resolved["bottom_bar_asset_id"], "bottom_bar")

    output_dir = GENERATED_DIR / datetime.now().strftime("%Y%m%d") / task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_{output_variant}" if output_variant else ""
    poster_path = output_dir / f"poster{suffix}.jpg"
    thumbnail_path = output_dir / f"thumbnail{suffix}.jpg"
    composition_path = output_dir / f"composition{suffix}.json"

    if reuse_existing_scene:
        base_canvas, fusion_meta, local_product_overlay = build_reused_scene_canvas(
            task=task,
            payload=payload,
            node=node,
            product_asset=product_asset,
        )
    elif asset_mode == "scene_image":
        if not scene_asset:
            raise ApiError("BAD_REQUEST", "场景图素材不存在")
        base_canvas, fusion_meta, local_product_overlay = build_scene_canvas(scene_asset=scene_asset)
    else:
        if not product_asset:
            raise ApiError("BAD_REQUEST", "产品图素材不存在")
        base_canvas, fusion_meta, local_product_overlay = build_ai_scene_canvas(
            node=node,
            product=product,
            payload=payload,
            copy=copy,
            product_asset=product_asset,
            scene_reference_asset=scene_reference_asset,
            logo_asset=logo_asset,
            bottom_bar_asset=bottom_bar_asset,
            output_dir=output_dir,
        )

    if not reuse_existing_scene:
        scene_base_path = output_dir / f"scene_base{suffix}.jpg"
        base_canvas.convert("RGB").save(scene_base_path, "JPEG", quality=92, optimize=True)
        fusion_meta["base_scene_url"] = public_storage_url(scene_base_path)
        fusion_meta["rerender_product_overlay"] = local_product_overlay

    ai_handles_copy = bool(fusion_meta.get("ai_handles_copy"))
    if qrcode_assets and ai_handles_copy:
        fusion_meta["ai_receives_brand_assets"] = True
        fusion_meta["brand_protection_mode"] = "ai_fusion_with_exact_final_qrcode_overlay"
    ai_generated_full_design = payload.creation_mode == "one_click" and ai_handles_copy
    ai_handles_brand_assets = fusion_meta.get("brand_protection_mode") == "ai_fusion_with_exact_final_overlay"
    canvas = base_canvas.convert("RGBA") if ai_handles_copy else apply_layout_panels(base_canvas, layout)
    draw = ImageDraw.Draw(canvas)

    if local_product_overlay and product_asset:
        paste_product_local(canvas, Path(product_asset["_file_path"]), layout)
    if not ai_generated_full_design:
        paste_logo(canvas, Path(logo_asset["_file_path"]), layout, harmonized=ai_handles_brand_assets)
    if not ai_handles_copy:
        draw_text_block(draw, copy["title"], copy["subtitle"], layout)
    if not ai_generated_full_design:
        paste_bottom_bar(
            canvas,
            Path(bottom_bar_asset["_file_path"]),
            layout,
            harmonized=ai_handles_brand_assets,
        )
    if not ai_handles_copy:
        draw_footer_text(
            draw,
            layout=layout,
            contact_text=payload.contact_text,
            custom_requirement=payload.custom_requirement,
        )
    if qrcode_assets and ai_generated_full_design:
        qrcode_overlay_meta = apply_exact_qrcode_overlays(
            canvas,
            qrcode_assets=qrcode_assets,
            bottom_bar_asset=bottom_bar_asset,
            layout=layout,
        )
        fusion_meta["exact_qrcode_overlay"] = qrcode_overlay_meta
        if qrcode_overlay_meta.get("warnings"):
            fusion_meta.setdefault("warnings", []).extend(qrcode_overlay_meta["warnings"])
    elif qrcode_assets:
        paste_qrcode(canvas, Path(qrcode_assets[0]["_file_path"]), layout, harmonized=ai_handles_brand_assets)
        fusion_meta["exact_qrcode_overlay"] = {
            "enabled": True,
            "mode": "legacy_template_qrcode_overlay",
            "placements": [],
            "warnings": [],
        }
    else:
        fusion_meta["exact_qrcode_overlay"] = {"enabled": False, "placements": [], "warnings": []}

    poster_copy = {
        "title": (fusion_meta.get("on_image_title") or copy["title"]) if ai_handles_copy else copy["title"],
        "subtitle": (fusion_meta.get("on_image_subtitle") or copy["subtitle"]) if ai_handles_copy else copy["subtitle"],
    }

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
            "product_asset_id": product_asset["id"] if product_asset else None,
            "scene_asset_id": scene_asset["id"] if scene_asset else None,
            "scene_reference_asset_id": scene_reference_asset["id"] if scene_reference_asset else None,
            **resolved,
            "background_asset_id": background_asset["id"] if background_asset else None,
        },
        "copy": {
            **copy,
            **poster_copy,
            "source": "ai_image_prompt" if ai_handles_copy else copy.get("source"),
        },
        "compliance": compliance_override or task.get("compliance"),
        "fusion": build_composition_fusion(
            payload=payload,
            product_asset=product_asset,
            scene_asset=scene_asset or scene_reference_asset,
            qrcode_asset=qrcode_asset,
            fusion_meta=fusion_meta,
            layout=layout,
        ),
    }
    composition_path.write_text(json.dumps(composition, ensure_ascii=False, indent=2), encoding="utf-8")

    poster = {
        "id": new_id("poster"),
        "title": f"{node['name']}_poster_{datetime.now().strftime('%Y%m%d')}",
        "jpg_url": public_storage_url(poster_path),
        "thumbnail_url": public_storage_url(thumbnail_path),
        "width": CANVAS_SIZE[0],
        "height": CANVAS_SIZE[1],
        "file_size_bytes": poster_path.stat().st_size,
        "composition_json_url": f"/api/v1/poster-tasks/{task_id}/composition",
        "copy": poster_copy,
    }
    return poster, composition["fusion"]


def normalize_qrcode_asset_ids(payload: PosterTaskCreate) -> list[str]:
    ids: list[str] = []
    for asset_id in payload.qrcode_asset_ids:
        clean = str(asset_id).strip()
        if clean and clean not in ids:
            ids.append(clean)
    if payload.qrcode_asset_id:
        clean = payload.qrcode_asset_id.strip()
        if clean and clean not in ids:
            ids.insert(0, clean)
    return ids[:4]


def build_scene_canvas(*, scene_asset: dict[str, Any]) -> tuple[Image.Image, dict[str, Any], bool]:
    image_path = Path(scene_asset["_file_path"])
    return (
        load_canvas_image(image_path, blur_radius=0, tint_color=None, tint_alpha=0),
        {
            "mode": "prebuilt_scene_image",
            "status": "provided",
            "provider": "user_or_material_upload",
            "model": "none",
            "prompt_provider": "none",
            "positive_prompt": "",
            "negative_prompt": "",
            "preserve_product_pixels": True,
            "ai_receives_brand_assets": False,
            "brand_asset_roles": [],
            "brand_protection_mode": "none",
            "protected_brand_asset_ids": {},
            "source_size": image_size(image_path),
            "fit": scene_fit_meta(image_path),
            "warnings": [],
            "fallback": {"used": False, "type": "none", "reason": None},
            "background_asset": None,
        },
        False,
    )


def build_ai_scene_canvas(
    *,
    node: dict[str, Any],
    product: dict[str, Any],
    payload: PosterTaskCreate,
    copy: dict[str, Any] | None,
    product_asset: dict[str, Any],
    scene_reference_asset: dict[str, Any] | None,
    logo_asset: dict[str, Any],
    bottom_bar_asset: dict[str, Any],
    output_dir: Path,
) -> tuple[Image.Image, dict[str, Any], bool]:
    ai_settings = get_ai_settings()
    if ai_settings.require_image_fusion and not ai_settings.has_image_credentials:
        raise ApiError(
            "AI_IMAGE_FUSION_FAILED",
            "图片模型未成功调用：未检测到可用的图片生成 Key，已按配置禁止本地堆叠兜底。",
            status_code=502,
            details={
                "reason": "image_api_key_not_configured",
                "fusion_policy": "AI_REQUIRE_IMAGE_FUSION=true",
                "local_fallback_used": False,
                "safe_context": ai_settings.safe_log_context(),
            },
        )

    product_path = Path(product_asset["_file_path"])
    scene_reference_path = Path(scene_reference_asset["_file_path"]) if scene_reference_asset else None
    brand_reference_assets = build_brand_reference_assets(
        logo_asset=logo_asset,
        bottom_bar_asset=bottom_bar_asset,
        output_dir=output_dir,
    )
    try:
        product_reference_path = prepare_product_reference_png(product_path, output_dir / "product_reference.png")
        fusion = get_ai_provider().generate_scene_with_product(
            node=node,
            product=product,
            payload=payload,
            transparent_product_png=product_reference_path,
            product_asset_id=product_asset["id"],
            output_dir=output_dir,
            scene_reference_path=scene_reference_path,
            copy=copy,
            canvas_size=CANVAS_SIZE,
            safe_zones=default_safe_zones(),
            brand_reference_assets=brand_reference_assets,
        )
        return (
            load_canvas_image(fusion.image_path, blur_radius=0, tint_color=None, tint_alpha=0),
            {
                "mode": fusion.mode,
                "status": "ai_generated",
                "provider": fusion.provider,
                "model": fusion.model,
                "prompt_provider": fusion.prompt_provider,
                "positive_prompt": fusion.prompt,
                "negative_prompt": fusion.negative_prompt,
                "preserve_product_pixels": fusion.preserve_product_pixels,
                "generated_image_contains_product": True,
                "ai_handles_copy": True,
                "on_image_title": fusion.on_image_title,
                "on_image_subtitle": fusion.on_image_subtitle,
                "reference_prompt_source": fusion.reference_prompt_source,
                "base_style_prompt": fusion.base_style_prompt,
                "node_style_prompt": fusion.node_style_prompt,
                "final_image_prompt": fusion.final_image_prompt or fusion.prompt,
                "asset_roles": fusion.asset_roles,
                "qrcode_policy": fusion.qrcode_policy,
                "reference_analysis_fallback_used": fusion.reference_analysis_fallback_used,
                "ai_receives_brand_assets": fusion.ai_receives_brand_assets,
                "brand_asset_roles": fusion.brand_asset_roles,
                "brand_protection_mode": fusion.brand_protection_mode,
                "protected_brand_asset_ids": fusion.protected_brand_asset_ids,
                "source_size": image_size(fusion.image_path),
                "fit": scene_fit_meta(fusion.image_path),
                "warnings": fusion.warnings,
                "fallback": {"used": False, "type": "none", "reason": None},
                "background_asset": None,
                "ai_scene_path": public_storage_url(fusion.image_path),
            },
            False,
        )
    except AiProviderError as exc:
        if ai_settings.require_image_fusion:
            raise ApiError(
                "AI_IMAGE_FUSION_FAILED",
                "图片模型调用失败或超时，已按配置禁止本地堆叠兜底。",
                status_code=502,
                details={
                    "reason": str(exc),
                    "fusion_policy": "AI_REQUIRE_IMAGE_FUSION=true",
                    "local_fallback_used": False,
                    "safe_context": ai_settings.safe_log_context(),
                },
            ) from exc
        return build_local_fallback_scene_canvas(
            node=node,
            payload=payload,
            scene_reference_asset=scene_reference_asset,
            fallback_reason=str(exc),
        )


def prepare_product_reference_png(source_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as source:
        product = ImageOps.contain(source.convert("RGBA"), (900, 1200), method=Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (1024, 1536), (255, 255, 255, 0))
        x = (canvas.width - product.width) // 2
        y = (canvas.height - product.height) // 2
        canvas.alpha_composite(product, (x, y))
        canvas.save(output_path, "PNG")
    return output_path


def build_brand_reference_assets(
    *,
    logo_asset: dict[str, Any],
    bottom_bar_asset: dict[str, Any],
    output_dir: Path,
) -> list[BrandReferenceAsset]:
    bottom_reference = prepare_bottom_bar_reference_without_qrcodes(
        source_path=Path(bottom_bar_asset["_file_path"]),
        output_path=output_dir / "bottom_bar_qrcode_placeholders.png",
    )
    assets = [
        BrandReferenceAsset(role="logo", asset_id=logo_asset["id"], path=Path(logo_asset["_file_path"])),
        BrandReferenceAsset(role="bottom_bar", asset_id=bottom_bar_asset["id"], path=bottom_reference),
    ]
    return assets


def prepare_bottom_bar_reference_without_qrcodes(*, source_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source_path) as source:
            image = source.convert("RGBA")
            ratios = detect_qrcode_dark_ratios_in_bottom_asset(image.convert("RGB"), expected_count=len(DEFAULT_BOTTOM_BAR_QR_SLOTS))
            if len(ratios) < len(DEFAULT_BOTTOM_BAR_QR_SLOTS):
                ratios = [slot["box_ratio"] for slot in DEFAULT_BOTTOM_BAR_QR_SLOTS]
            for ratio in ratios[: len(DEFAULT_BOTTOM_BAR_QR_SLOTS)]:
                box = ratio_box_to_pixels(ratio, image.size)
                box = expand_box(box, max(4, int(min(image.size) * 0.015)), image.size)
                patch = build_bottom_bar_qrcode_removed_patch(image, box)
                image.alpha_composite(patch, (box[0], box[1]))
            image.save(output_path, "PNG")
            return output_path
    except Exception:
        return source_path


def build_bottom_bar_qrcode_removed_patch(
    image: Image.Image,
    box: tuple[int, int, int, int],
) -> Image.Image:
    x0, y0, x1, y1 = box
    width = max(1, x1 - x0)
    height = max(1, y1 - y0)
    fill = sample_light_background_color(image, expand_box(box, 12, image.size))
    return Image.new("RGBA", (width, height), fill)


def sample_light_background_color(
    image: Image.Image,
    box: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    pixels = image.load()
    values: list[tuple[int, int, int]] = []
    step = max(1, min(box_width(box), box_height(box)) // 18)
    for y in range(box[1], box[3], step):
        for x in range(box[0], box[2], step):
            r, g, b, *_ = pixels[x, y]
            if r >= 235 and g >= 235 and b >= 235:
                values.append((r, g, b))
    if not values:
        return (255, 255, 255, 255)
    values.sort()
    mid = values[len(values) // 2]
    return (mid[0], mid[1], mid[2], 255)


def ratio_box_to_pixels(box_ratio: tuple[float, float, float, float], size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = size
    return (
        int(round(box_ratio[0] * width)),
        int(round(box_ratio[1] * height)),
        int(round(box_ratio[2] * width)),
        int(round(box_ratio[3] * height)),
    )


def clamp_box(box: tuple[int, int, int, int], size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = size
    return (
        max(0, min(width, box[0])),
        max(0, min(height, box[1])),
        max(0, min(width, box[2])),
        max(0, min(height, box[3])),
    )


def build_local_fallback_scene_canvas(
    *,
    node: dict[str, Any],
    payload: PosterTaskCreate,
    scene_reference_asset: dict[str, Any] | None,
    fallback_reason: str,
) -> tuple[Image.Image, dict[str, Any], bool]:
    if scene_reference_asset:
        source_asset = scene_reference_asset
    else:
        source_asset = get_asset(node.get("background_asset_id")) or get_asset("asset_bg_anniversary")
    source_path = Path(source_asset["_file_path"]) if source_asset else None
    return (
        load_canvas_image(source_path, blur_radius=0, tint_color=None, tint_alpha=0),
        {
            "mode": "local_background_local_compose",
            "status": "local",
            "provider": "local_fallback",
            "model": "local_library",
            "prompt_provider": "none",
            "positive_prompt": payload.scene_prompt or payload.custom_requirement,
            "negative_prompt": "",
            "preserve_product_pixels": True,
            "generated_image_contains_product": False,
            "ai_handles_copy": False,
            "on_image_title": "",
            "on_image_subtitle": "",
            "reference_prompt_source": "local_fallback",
            "base_style_prompt": "",
            "node_style_prompt": "",
            "final_image_prompt": payload.scene_prompt or payload.custom_requirement,
            "reference_analysis_fallback_used": True,
            "ai_receives_brand_assets": False,
            "brand_asset_roles": [],
            "brand_protection_mode": "none",
            "protected_brand_asset_ids": {},
            "source_size": image_size(source_path),
            "fit": scene_fit_meta(source_path),
            "warnings": [fallback_reason],
            "fallback": {"used": True, "type": "local_background_product_overlay", "reason": fallback_reason},
            "background_asset": source_asset,
        },
        True,
    )


def build_reused_scene_canvas(
    *,
    task: dict[str, Any],
    payload: PosterTaskCreate,
    node: dict[str, Any],
    product_asset: dict[str, Any] | None,
) -> tuple[Image.Image, dict[str, Any], bool]:
    previous_fusion = dict(task.get("fusion") or {})
    base_scene_path = storage_url_to_path(previous_fusion.get("base_scene_url"))
    if not base_scene_path or not base_scene_path.exists():
        raise ApiError(
            "RERENDER_SCENE_CACHE_MISSING",
            "Preview rerender scene cache is missing; please create a new poster task.",
            details={"task_id": task.get("task_id")},
        )

    previous_fusion["warnings"] = [
        *(previous_fusion.get("warnings") or []),
        "Reused cached scene for copy-only rerender; AI image fusion was not called again.",
    ]
    previous_fusion["rerender_reused_scene"] = True
    previous_fusion["positive_prompt"] = previous_fusion.get("positive_prompt") or payload.scene_prompt or payload.custom_requirement
    previous_fusion["negative_prompt"] = previous_fusion.get("negative_prompt") or ""
    previous_fusion["fallback"] = previous_fusion.get("fallback") or {"used": False, "type": "none", "reason": None}
    previous_fusion["fit"] = previous_fusion.get("scene_fit") or scene_fit_meta(base_scene_path)
    previous_fusion["source_size"] = tuple(previous_fusion.get("source_size") or image_size(base_scene_path) or CANVAS_SIZE)
    previous_fusion["background_asset"] = None
    previous_fusion["base_scene_url"] = previous_fusion.get("base_scene_url")
    previous_fusion["ai_receives_brand_assets"] = bool(previous_fusion.get("ai_receives_brand_assets"))
    previous_fusion["brand_asset_roles"] = previous_fusion.get("brand_asset_roles") or []
    previous_fusion["brand_protection_mode"] = previous_fusion.get("brand_protection_mode") or "none"
    previous_fusion["protected_brand_asset_ids"] = previous_fusion.get("protected_brand_asset_ids") or {}
    local_product_overlay = bool(previous_fusion.get("rerender_product_overlay"))

    if local_product_overlay and not product_asset:
        raise ApiError("BAD_REQUEST", "Product asset is required for copy rerender.")

    return (
        load_canvas_image(base_scene_path, blur_radius=0, tint_color=None, tint_alpha=0),
        previous_fusion,
        local_product_overlay,
    )


def storage_url_to_path(url: str | None) -> Path | None:
    if not url or not url.startswith("/storage/"):
        return None
    relative = Path(url.removeprefix("/storage/"))
    try:
        path = (STORAGE_DIR / relative).resolve()
        path.relative_to(STORAGE_DIR.resolve())
    except ValueError:
        return None
    return path


def build_composition_fusion(
    *,
    payload: PosterTaskCreate,
    product_asset: dict[str, Any] | None,
    scene_asset: dict[str, Any] | None,
    qrcode_asset: dict[str, Any] | None,
    fusion_meta: dict[str, Any],
    layout: dict[str, Any],
) -> dict[str, Any]:
    ai_safe_zones = default_safe_zones()
    return {
        "mode": fusion_meta["mode"],
        "status": fusion_meta["status"],
        "provider": fusion_meta["provider"],
        "model": fusion_meta["model"],
        "prompt_provider": fusion_meta["prompt_provider"],
        "used_product_asset_id": product_asset["id"] if product_asset else None,
        "product_asset_type": product_asset["asset_type"] if product_asset else None,
        "scene_asset_id": scene_asset["id"] if scene_asset else None,
        "scene_asset_type": scene_asset["asset_type"] if scene_asset else None,
        "scene_prompt_input": payload.scene_prompt,
        "custom_requirement": payload.custom_requirement,
        "positive_prompt": fusion_meta["positive_prompt"],
        "negative_prompt": fusion_meta["negative_prompt"],
        "reference_prompt_source": fusion_meta.get("reference_prompt_source"),
        "base_style_prompt": fusion_meta.get("base_style_prompt"),
        "node_style_prompt": fusion_meta.get("node_style_prompt"),
        "final_image_prompt": fusion_meta.get("final_image_prompt") or fusion_meta["positive_prompt"],
        "asset_roles": fusion_meta.get("asset_roles") or ["product"],
        "qrcode_policy": fusion_meta.get("qrcode_policy") or "reserve_clean_area_for_exact_overlay",
        "on_image_title": fusion_meta.get("on_image_title"),
        "on_image_subtitle": fusion_meta.get("on_image_subtitle"),
        "reference_analysis_fallback_used": bool(fusion_meta.get("reference_analysis_fallback_used", True)),
        "ai_handles_copy": bool(fusion_meta.get("ai_handles_copy")),
        "ai_receives_brand_assets": bool(fusion_meta.get("ai_receives_brand_assets")),
        "brand_asset_roles": fusion_meta.get("brand_asset_roles") or [],
        "brand_protection_mode": fusion_meta.get("brand_protection_mode") or "none",
        "protected_brand_asset_ids": fusion_meta.get("protected_brand_asset_ids") or {},
        "preserve_product_pixels": fusion_meta["preserve_product_pixels"],
        "generated_image_contains_product": fusion_meta.get(
            "generated_image_contains_product",
            fusion_meta["mode"] in {"scene_with_product", "prebuilt_scene_image", "local_mock_composite", "image_edit_json_reference"},
        ),
        "final_layout_engine": fusion_meta.get("final_layout_engine")
        or infer_final_layout_engine(fusion_meta),
        "safe_zones": {
            "ai_top_logo": list(ai_safe_zones["top_logo"]),
            "ai_bottom_copy_qrcode": list(ai_safe_zones["bottom_copy_qrcode"]),
            "logo": list(layout["logo"]["box"]),
            "title": list(layout["title"]["box"]),
            "subtitle": list(layout["subtitle"]["box"]),
            "contact": list(layout["contact"]["box"]),
            "qrcode": list(layout["qrcode"]["card_box"]) if qrcode_asset else None,
            "bottom_bar": list(layout["bottom_bar"]["box"]),
        },
        "scene_fit": fusion_meta["fit"],
        "source_size": list(fusion_meta["source_size"]) if fusion_meta.get("source_size") else None,
        "fallback": fusion_meta["fallback"],
        "warnings": fusion_meta.get("warnings", []),
        "base_scene_url": fusion_meta.get("base_scene_url"),
        "exact_qrcode_overlay": fusion_meta.get("exact_qrcode_overlay") or {"enabled": False, "placements": [], "warnings": []},
        "rerender_product_overlay": bool(fusion_meta.get("rerender_product_overlay")),
        "rerender_reused_scene": bool(fusion_meta.get("rerender_reused_scene")),
    }


def infer_final_layout_engine(fusion_meta: dict[str, Any]) -> str:
    overlay = fusion_meta.get("exact_qrcode_overlay") or {}
    if overlay.get("enabled"):
        return "ai_scene_with_pillow_exact_qrcode_overlay"
    if fusion_meta.get("brand_protection_mode") == "ai_fusion_with_exact_final_overlay":
        return "ai_scene_with_exact_brand_protection"
    return "pillow_template_overlay"


def load_canvas_image(
    source: Path | None,
    *,
    blur_radius: float,
    tint_color: str | None,
    tint_alpha: float,
) -> Image.Image:
    if source:
        try:
            with Image.open(source) as image:
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


def image_size(path: Path | None) -> tuple[int, int] | None:
    if not path:
        return None
    try:
        with Image.open(path) as image:
            return image.width, image.height
    except Exception:
        return None


def scene_fit_meta(path: Path | None) -> dict[str, Any]:
    return {
        "mode": "cover",
        "canvas_size": list(CANVAS_SIZE),
        "source_size": list(image_size(path) or CANVAS_SIZE),
        "position": "center",
        "no_stretch": True,
    }


def apply_layout_panels(canvas: Image.Image, layout: dict[str, Any]) -> Image.Image:
    overlay = Image.new("RGBA", CANVAS_SIZE, (255, 255, 255, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_config = layout["overlay"]
    top_height = int(overlay_config["top_height"])
    top_alpha = int(overlay_config["top_alpha"])
    for y in range(top_height):
        alpha = int(top_alpha * (1 - y / max(top_height - 1, 1)))
        overlay_draw.line((0, y, CANVAS_SIZE[0], y), fill=(0, 0, 0, alpha))

    bottom_start_y = int(overlay_config["bottom_start_y"])
    bottom_alpha = int(overlay_config["bottom_alpha"])
    bottom_height = CANVAS_SIZE[1] - bottom_start_y
    for y in range(bottom_start_y, CANVAS_SIZE[1]):
        alpha = int(bottom_alpha * ((y - bottom_start_y) / max(bottom_height - 1, 1)))
        overlay_draw.line((0, y, CANVAS_SIZE[0], y), fill=(0, 0, 0, alpha))
    return Image.alpha_composite(canvas.convert("RGBA"), overlay)


def paste_product_local(canvas: Image.Image, product_path: Path, layout: dict[str, Any]) -> None:
    top_limit = int(layout["subtitle"]["box"][3]) + 70
    bottom_limit = int(layout["overlay"]["bottom_start_y"]) - 40
    zone = (120, top_limit, CANVAS_SIZE[0] - 120, bottom_limit)
    zone_width = zone[2] - zone[0]
    zone_height = max(zone[3] - zone[1], 1)

    with Image.open(product_path) as product_image:
        product = ImageOps.contain(
            product_image.convert("RGBA"),
            (zone_width, zone_height),
            method=Image.Resampling.LANCZOS,
        )
        x = zone[0] + (zone_width - product.width) // 2
        y = zone[1] + (zone_height - product.height) // 2

        shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        shadow_tile = Image.new("RGBA", product.size, (0, 0, 0, 0))
        shadow_tile.putalpha(product.getchannel("A").filter(ImageFilter.GaussianBlur(18)))
        shadow.alpha_composite(shadow_tile, (x + 18, y + 28))
        canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(10)))
        canvas.alpha_composite(product, (x, y))


def draw_text_block(draw: ImageDraw.ImageDraw, title: str, subtitle: str, layout: dict[str, Any]) -> None:
    draw_fitted_text(draw, title, layout["title"], bold=True, shadow_alpha=150)
    draw_fitted_text(draw, subtitle, layout["subtitle"], bold=False, shadow_alpha=132)


def paste_logo(canvas: Image.Image, logo_path: Path, layout: dict[str, Any], *, harmonized: bool = False) -> None:
    settings = layout["logo"]
    box = tuple(settings["box"])
    try:
        with Image.open(logo_path) as logo:
            logo_image = logo.convert("RGBA")
            logo_image.thumbnail(tuple(settings["max_size"]), Image.Resampling.LANCZOS)
            x = box[0]
            y = box[1] + (box_height(box) - logo_image.height) // 2
            draw = ImageDraw.Draw(canvas)
            plate_box = (x - 14, y - 10, x + logo_image.width + 14, y + logo_image.height + 10)
            plate = choose_harmonized_plate(canvas, plate_box) if harmonized else choose_logo_plate(logo_image)
            draw.rounded_rectangle(plate_box, radius=14 if harmonized else 16, fill=plate)
            shadow = Image.new("RGBA", logo_image.size, (0, 0, 0, 0))
            shadow.putalpha(logo_image.getchannel("A").filter(ImageFilter.GaussianBlur(5 if harmonized else 4)))
            canvas.alpha_composite(shadow, (x + 2, y + (4 if harmonized else 3)))
            canvas.alpha_composite(logo_image, (x, y))
    except Exception:
        draw = ImageDraw.Draw(canvas)
        draw.text((box[0], box[1]), "PUDOW", font=load_font(46, bold=True), fill="#FFFFFF")


def paste_qrcode(canvas: Image.Image, qrcode_path: Path, layout: dict[str, Any], *, harmonized: bool = False) -> None:
    settings = layout["qrcode"]
    card_box = tuple(settings["card_box"])
    image_box = tuple(settings["image_box"])
    target_size = max(int(settings["target_size"]), int(settings["min_size"]))
    target_size = min(target_size, box_width(image_box), box_height(image_box))
    try:
        with Image.open(qrcode_path) as qrcode_image:
            qrcode = qrcode_image.convert("RGB")
            qrcode = ImageOps.contain(qrcode, (target_size, target_size), Image.Resampling.LANCZOS).convert("RGBA")
            qrcode_square = Image.new("RGBA", (target_size, target_size), "#FFFFFF")
            qrcode_square.alpha_composite(qrcode, ((target_size - qrcode.width) // 2, (target_size - qrcode.height) // 2))
            draw = ImageDraw.Draw(canvas)
            if harmonized:
                shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
                shadow_draw = ImageDraw.Draw(shadow)
                shadow_draw.rounded_rectangle(card_box, radius=24, fill=(0, 0, 0, 62))
                canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(10)))
            outline = (255, 255, 255, 82) if harmonized else (15, 23, 42, 42)
            draw.rounded_rectangle(card_box, radius=24, fill="#FFFFFF", outline=outline, width=2)
            image_x = image_box[0] + (box_width(image_box) - target_size) // 2
            image_y = image_box[1] + (box_height(image_box) - target_size) // 2
            canvas.alpha_composite(qrcode_square, (image_x, image_y))
            caption_font = load_font(24, bold=True)
            draw_centered_text(draw, "扫码", (card_box[0], int(settings["caption_y"]), card_box[2], card_box[3] - 8), caption_font, "#12312E")
    except Exception:
        pass


def apply_exact_qrcode_overlays(
    canvas: Image.Image,
    *,
    qrcode_assets: list[dict[str, Any]],
    bottom_bar_asset: dict[str, Any],
    layout: dict[str, Any],
    force_template_bottom_bar: bool = False,
) -> dict[str, Any]:
    if not qrcode_assets:
        return {"enabled": False, "placements": [], "warnings": []}

    if force_template_bottom_bar:
        placement_plan = qrcode_overlay_boxes_from_template_bottom_bar(
            layout,
            expected_count=len(qrcode_assets),
            bottom_bar_asset=bottom_bar_asset,
            canvas_size=canvas.size,
        )
    else:
        placement_plan = locate_qrcode_overlay_boxes(
            canvas,
            layout,
            expected_count=len(qrcode_assets),
            bottom_bar_asset=bottom_bar_asset,
        )
    detected_boxes = placement_plan["boxes"]
    placement_source = placement_plan["source"]
    warnings: list[str] = list(placement_plan.get("warnings") or [])
    if len(detected_boxes) < len(qrcode_assets):
        warnings.append("可用二维码槽位少于已选二维码数量，剩余二维码未贴入。")

    placements: list[dict[str, Any]] = []
    for index, asset in enumerate(qrcode_assets[: len(detected_boxes)]):
        box = detected_boxes[index]
        try:
            paste_qrcode_into_box(canvas, Path(asset["_file_path"]), box)
            placements.append(
                {
                    "slot_id": DEFAULT_BOTTOM_BAR_QR_SLOTS[index]["id"] if index < len(DEFAULT_BOTTOM_BAR_QR_SLOTS) else f"slot_{index + 1}",
                    "slot_label": DEFAULT_BOTTOM_BAR_QR_SLOTS[index]["label"] if index < len(DEFAULT_BOTTOM_BAR_QR_SLOTS) else "二维码",
                    "asset_id": asset["id"],
                    "asset_name": asset.get("name", ""),
                    "box": list(box),
                    "source": placement_source,
                    "pasted": True,
                }
            )
        except Exception as exc:
            warnings.append(f"二维码 {asset.get('id', '')} 贴入失败：{exc}")

    return {
        "enabled": True,
        "mode": "ai_bottom_bar_placeholder_exact_qrcode_overlay",
        "bottom_bar_asset_id": bottom_bar_asset.get("id"),
        "placeholder_detection": placement_source,
        "detected_bottom_bar_box": placement_plan.get("bottom_band"),
        "raw_detected_bottom_bar_box": placement_plan.get("raw_bottom_band"),
        "source_qrcode_slot_ratios": placement_plan.get("slot_ratios"),
        "placements": placements,
        "warnings": warnings,
    }


def paste_qrcode_into_box(canvas: Image.Image, qrcode_path: Path, box: tuple[int, int, int, int]) -> None:
    target_w = box_width(box)
    target_h = box_height(box)
    target_size = max(1, min(target_w, target_h))
    with Image.open(qrcode_path) as qrcode_image:
        qrcode = ImageOps.contain(
            qrcode_image.convert("RGBA"),
            (target_size, target_size),
            Image.Resampling.LANCZOS,
        )
        x = box[0] + (target_w - target_size) // 2
        y = box[1] + (target_h - target_size) // 2
        canvas.alpha_composite(qrcode, (x + (target_size - qrcode.width) // 2, y + (target_size - qrcode.height) // 2))


def erase_qrcode_placeholder_regions(
    canvas: Image.Image,
    boxes: list[tuple[int, int, int, int]],
    bottom_band_value: Any,
    *,
    bottom_bar_asset: dict[str, Any] | None = None,
) -> None:
    if not boxes or not bottom_band_value:
        return
    try:
        bottom_band = tuple(int(value) for value in bottom_band_value)
    except Exception:
        return
    source = canvas.convert("RGBA")
    erase_limit = qrcode_placeholder_erase_limit(bottom_band, canvas.size)
    bottom_patch_source = load_bottom_bar_patch_source(bottom_bar_asset, bottom_band)
    for box in boxes:
        pad = max(2, min(box_width(box), box_height(box)) // 24)
        erase_box = expand_box(box, pad, canvas.size)
        erase_box = intersect_boxes(erase_box, erase_limit)
        if box_width(erase_box) <= 0 or box_height(erase_box) <= 0:
            continue
        patch = build_qrcode_placeholder_erase_patch(source, erase_box)
        if bottom_patch_source is not None:
            patch = overlay_bottom_bar_patch(patch, bottom_patch_source, erase_box, bottom_band)
        canvas.alpha_composite(patch, (erase_box[0], erase_box[1]))


def intersect_boxes(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def qrcode_placeholder_erase_limit(
    bottom_band: tuple[int, int, int, int],
    canvas_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    qr_band = qrcode_overlay_qr_safe_band(bottom_band)
    return (0, 0, canvas_size[0], qr_band[3])


def load_bottom_bar_patch_source(
    bottom_bar_asset: dict[str, Any] | None,
    bottom_band: tuple[int, int, int, int],
) -> Image.Image | None:
    path = Path(bottom_bar_asset.get("_file_path") or bottom_bar_asset.get("file_path") or "") if bottom_bar_asset else None
    if not path or not path.exists():
        return None
    try:
        with Image.open(path) as source:
            size = (box_width(bottom_band), box_height(bottom_band))
            return ImageOps.fit(source.convert("RGBA"), size, method=Image.Resampling.LANCZOS)
    except Exception:
        return None


def overlay_bottom_bar_patch(
    base_patch: Image.Image,
    bottom_patch_source: Image.Image,
    erase_box: tuple[int, int, int, int],
    bottom_band: tuple[int, int, int, int],
) -> Image.Image:
    x0, y0, x1, y1 = erase_box
    target = base_patch.copy()
    overlap = intersect_boxes(erase_box, bottom_band)
    if box_width(overlap) <= 0 or box_height(overlap) <= 0:
        return target
    source_box = (
        overlap[0] - bottom_band[0],
        overlap[1] - bottom_band[1],
        overlap[2] - bottom_band[0],
        overlap[3] - bottom_band[1],
    )
    patch = bottom_patch_source.crop(source_box)
    target.alpha_composite(patch, (overlap[0] - x0, overlap[1] - y0))
    return target


def build_qrcode_placeholder_erase_patch(
    source: Image.Image,
    box: tuple[int, int, int, int],
) -> Image.Image:
    x0, y0, x1, y1 = box
    width = max(1, x1 - x0)
    height = max(1, y1 - y0)
    pixels = source.load()
    patch = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    patch_pixels = patch.load()
    for local_y, y in enumerate(range(y0, y1)):
        left = find_non_qrcode_placeholder_pixel(pixels, source.size, x0 - 1, y, direction=-1)
        right = find_non_qrcode_placeholder_pixel(pixels, source.size, x1, y, direction=1)
        if not left and local_y > 0:
            left = patch_pixels[0, local_y - 1]
        if not right and local_y > 0:
            right = patch_pixels[width - 1, local_y - 1]
        left = left or right or (227, 241, 231, 255)
        right = right or left
        for local_x in range(width):
            ratio = local_x / max(width - 1, 1)
            patch_pixels[local_x, local_y] = tuple(
                int(left[channel] * (1 - ratio) + right[channel] * ratio)
                for channel in range(4)
            )
    return patch.filter(ImageFilter.GaussianBlur(radius=0.8))


def build_horizontal_background_patch(
    source: Image.Image,
    box: tuple[int, int, int, int],
) -> Image.Image:
    x0, y0, x1, y1 = box
    width = max(1, x1 - x0)
    height = max(1, y1 - y0)
    pixels = source.load()
    patch = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    patch_pixels = patch.load()
    for local_y, y in enumerate(range(y0, y1)):
        left = find_background_pixel(pixels, source.size, x0 - 1, y, direction=-1)
        right = find_background_pixel(pixels, source.size, x1, y, direction=1)
        if not left and local_y > 0:
            left = patch_pixels[0, local_y - 1]
        if not right and local_y > 0:
            right = patch_pixels[width - 1, local_y - 1]
        left = left or (226, 241, 229, 255)
        right = right or left
        for local_x in range(width):
            ratio = local_x / max(width - 1, 1)
            patch_pixels[local_x, local_y] = tuple(
                int(left[channel] * (1 - ratio) + right[channel] * ratio)
                for channel in range(4)
            )
    return patch.filter(ImageFilter.GaussianBlur(radius=1.4))


def find_background_pixel(
    pixels: Any,
    size: tuple[int, int],
    start_x: int,
    y: int,
    *,
    direction: int,
) -> tuple[int, int, int, int] | None:
    width, height = size
    if y < 0 or y >= height:
        return None
    x = max(0, min(start_x, width - 1))
    for _ in range(180):
        if x < 0 or x >= width:
            break
        pixel = pixels[x, y]
        if not is_qrcode_card_pixel(pixel[:3]):
            return pixel
        x += direction
    return None


def find_non_qrcode_placeholder_pixel(
    pixels: Any,
    size: tuple[int, int],
    start_x: int,
    y: int,
    *,
    direction: int,
) -> tuple[int, int, int, int] | None:
    width, height = size
    if y < 0 or y >= height:
        return None
    x = max(0, min(start_x, width - 1))
    for _ in range(220):
        if x < 0 or x >= width:
            break
        pixel = pixels[x, y]
        rgb = pixel[:3]
        if not is_qrcode_card_pixel(rgb) and not is_qr_dark_pixel(rgb):
            return pixel
        x += direction
    return None


def _legacy_locate_qrcode_overlay_boxes_unused(
    canvas: Image.Image,
    layout: dict[str, Any],
    *,
    expected_count: int,
) -> dict[str, Any]:
    bottom_band = detect_bottom_bar_band(canvas)
    if not bottom_band:
        return {
            "source": "template_fallback",
            "bottom_band": None,
            "boxes": fallback_qrcode_boxes_from_layout(layout)[:expected_count],
            "warnings": ["未能在 AI 生成图中稳定识别底部宣传条，已使用模板槽位兜底贴码。"],
        }

    ratio_boxes = qrcode_boxes_from_bottom_band(bottom_band)
    card_boxes = detect_qrcode_card_boxes_in_bottom_band(canvas, bottom_band, expected_count=expected_count)
    if card_boxes:
        return {
            "source": "detected_qrcode_card",
            "bottom_band": list(bottom_band),
            "boxes": normalize_qrcode_box_count(card_boxes, ratio_boxes, expected_count, canvas.size),
            "warnings": [],
        }

    qr_like_boxes = detect_qr_like_boxes_in_bottom_band(canvas, bottom_band, expected_count=expected_count)
    if qr_like_boxes:
        return {
            "source": "detected_qr_like_region",
            "bottom_band": list(bottom_band),
            "boxes": normalize_qrcode_box_count(qr_like_boxes, ratio_boxes, expected_count, canvas.size),
            "warnings": ["AI 生成图中出现疑似二维码纹理，已用真实二维码覆盖该区域。"],
        }

    return {
        "source": "detected_bottom_bar_ratio",
        "bottom_band": list(bottom_band),
        "boxes": ratio_boxes[:expected_count],
        "warnings": [],
    }


def detect_qrcode_placeholder_boxes(canvas: Image.Image, layout: dict[str, Any]) -> list[tuple[int, int, int, int]]:
    return locate_qrcode_overlay_boxes(canvas, layout, expected_count=len(DEFAULT_BOTTOM_BAR_QR_SLOTS))["boxes"]


def locate_qrcode_overlay_boxes(
    canvas: Image.Image,
    layout: dict[str, Any],
    *,
    expected_count: int,
    bottom_bar_asset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_bottom_band = detect_bottom_bar_band(canvas)
    allowed_bottom_band = qrcode_overlay_allowed_bottom_band(
        raw_bottom_band,
        layout,
        canvas.size,
        bottom_bar_asset=bottom_bar_asset,
    )
    placeholder_boxes = detect_bottom_qrcode_placeholder_boxes(canvas, expected_count=expected_count)
    if len(placeholder_boxes) >= expected_count:
        boxes = constrain_qrcode_boxes_to_bottom_band(
            equalize_qrcode_boxes(placeholder_boxes[:expected_count], canvas.size),
            allowed_bottom_band,
            canvas.size,
        )
        return {
            "source": "detected_bottom_placeholder_pair",
            "bottom_band": list(allowed_bottom_band),
            "raw_bottom_band": list(raw_bottom_band) if raw_bottom_band else None,
            "boxes": boxes,
            "erase_boxes": [list(box) for box in placeholder_boxes[:expected_count]],
            "slot_ratios": [],
            "warnings": [],
        }

    if not raw_bottom_band:
        fallback_boxes = constrain_qrcode_boxes_to_bottom_band(
            equalize_qrcode_boxes(
                fallback_qrcode_boxes_from_layout(layout)[:expected_count],
                canvas.size,
            ),
            allowed_bottom_band,
            canvas.size,
        )
        return {
            "source": "template_fallback",
            "bottom_band": list(allowed_bottom_band),
            "raw_bottom_band": None,
            "boxes": fallback_boxes,
            "erase_boxes": [],
            "slot_ratios": [list(slot["box_ratio"]) for slot in DEFAULT_BOTTOM_BAR_QR_SLOTS[:expected_count]],
            "warnings": ["Could not detect a stable bottom bar or QR placeholder pair; used template fallback QR slots."],
        }

    bottom_band = complete_bottom_bar_band(raw_bottom_band, canvas.size, bottom_bar_asset=bottom_bar_asset)
    slot_ratios = qrcode_slot_ratios_from_bottom_bar_asset(bottom_bar_asset, expected_count=expected_count)
    ratio_boxes = constrain_qrcode_boxes_to_bottom_band(
        equalize_qrcode_boxes(
            qrcode_boxes_from_bottom_band(bottom_band, slot_ratios=slot_ratios)[:expected_count],
            canvas.size,
        ),
        allowed_bottom_band,
        canvas.size,
    )
    card_boxes = detect_qrcode_card_boxes_in_bottom_band(canvas, bottom_band, expected_count=expected_count)
    if len(card_boxes) >= expected_count:
        return {
            "source": "detected_qrcode_card_pair",
            "bottom_band": list(allowed_bottom_band),
            "raw_bottom_band": list(raw_bottom_band),
            "boxes": constrain_qrcode_boxes_to_bottom_band(
                equalize_qrcode_boxes(card_boxes[:expected_count], canvas.size),
                allowed_bottom_band,
                canvas.size,
            ),
            "erase_boxes": [list(box) for box in card_boxes[:expected_count]],
            "slot_ratios": [list(ratio) for ratio in slot_ratios[:expected_count]],
            "warnings": [],
        }

    qr_like_boxes = detect_qr_like_boxes_in_bottom_band(canvas, bottom_band, expected_count=expected_count)
    if len(qr_like_boxes) >= expected_count:
        return {
            "source": "detected_qr_like_region_pair",
            "bottom_band": list(allowed_bottom_band),
            "raw_bottom_band": list(raw_bottom_band),
            "boxes": constrain_qrcode_boxes_to_bottom_band(
                equalize_qrcode_boxes(qr_like_boxes[:expected_count], canvas.size),
                allowed_bottom_band,
                canvas.size,
            ),
            "erase_boxes": [list(box) for box in qr_like_boxes[:expected_count]],
            "slot_ratios": [list(ratio) for ratio in slot_ratios[:expected_count]],
            "warnings": ["AI generated QR-like texture; replaced the paired regions with exact QR codes."],
        }

    warnings: list[str] = []
    if placeholder_boxes or card_boxes or qr_like_boxes:
        warnings.append("QR placeholder detection did not find a complete pair; used bottom-bar asset slot ratios for both QR codes.")
    return {
        "source": "bottom_bar_asset_slot_ratio",
        "bottom_band": list(allowed_bottom_band),
        "raw_bottom_band": list(raw_bottom_band),
        "boxes": ratio_boxes[:expected_count],
        "erase_boxes": [],
        "slot_ratios": [list(ratio) for ratio in slot_ratios[:expected_count]],
        "warnings": warnings,
    }


def qrcode_overlay_boxes_from_template_bottom_bar(
    layout: dict[str, Any],
    *,
    expected_count: int,
    bottom_bar_asset: dict[str, Any] | None,
    canvas_size: tuple[int, int],
) -> dict[str, Any]:
    bottom_band = clamp_box(tuple(layout["bottom_bar"]["box"]), canvas_size)
    slot_ratios = qrcode_slot_ratios_from_bottom_bar_asset(bottom_bar_asset, expected_count=expected_count)
    boxes = constrain_qrcode_boxes_to_bottom_band(
        equalize_qrcode_boxes(
            qrcode_boxes_from_bottom_band(bottom_band, slot_ratios=slot_ratios)[:expected_count],
            canvas_size,
        ),
        bottom_band,
        canvas_size,
    )
    return {
        "source": "template_bottom_bar_asset_slot_ratio",
        "bottom_band": list(bottom_band),
        "raw_bottom_band": None,
        "boxes": boxes,
        "erase_boxes": [],
        "slot_ratios": [list(ratio) for ratio in slot_ratios[:expected_count]],
        "warnings": [],
    }


def detect_bottom_bar_band(canvas: Image.Image) -> tuple[int, int, int, int] | None:
    image = canvas.convert("RGB")
    width, height = image.size
    pixels = image.load()
    row_scores: list[tuple[int, float]] = []
    start_y = int(height * 0.58)
    sample_step = 6
    for y in range(start_y, height, sample_step):
        light = 0
        total = 0
        for x in range(0, width, sample_step):
            total += 1
            if is_bottom_bar_pixel(pixels[x, y]):
                light += 1
        row_scores.append((y, light / max(total, 1)))

    strong_threshold = 0.68
    weak_threshold = 0.50
    strong_rows = [
        y
        for y, score in row_scores
        if y >= int(height * 0.78) and score >= strong_threshold
    ]
    if not strong_rows:
        return None

    if max(strong_rows) < height - max(48, int(height * 0.045)):
        return None

    score_by_y = {y: score for y, score in row_scores}
    top = min(strong_rows)
    bottom = min(height, max(strong_rows) + sample_step)
    while top - sample_step >= start_y and score_by_y.get(top - sample_step, 0) >= weak_threshold:
        top -= sample_step
    while bottom < height and score_by_y.get(bottom, 0) >= weak_threshold:
        bottom += sample_step
    bottom = min(height, bottom)
    for y, score in reversed(row_scores):
        if y < top:
            break
        if y < bottom - max(80, int(height * 0.08)) and score < weak_threshold:
            top = y + sample_step
            break

    band_height = bottom - top
    if band_height < 110:
        return None
    if band_height > 430:
        top = bottom - 430
    return (0, top, width, bottom)


def detect_qrcode_card_boxes_in_bottom_band(
    canvas: Image.Image,
    bottom_band: tuple[int, int, int, int],
    *,
    expected_count: int,
) -> list[tuple[int, int, int, int]]:
    if expected_count <= 0:
        return []
    image = canvas.convert("RGB")
    pixels = image.load()
    band_width = box_width(bottom_band)
    band_height = box_height(bottom_band)
    scan_box = (
        bottom_band[0] + int(band_width * 0.56),
        bottom_band[1] + int(band_height * 0.05),
        bottom_band[2] - int(band_width * 0.02),
        bottom_band[1] + int(band_height * 0.80),
    )
    step = 2
    light_boxes: list[tuple[int, int, int, int]] = []
    for y in range(scan_box[1], scan_box[3], step):
        for x in range(scan_box[0], scan_box[2], step):
            if is_qrcode_card_pixel(pixels[x, y]):
                light_boxes.append((x, y, min(x + step, scan_box[2]), min(y + step, scan_box[3])))
    if not light_boxes:
        return []

    merged = merge_nearby_boxes(light_boxes, padding=4)
    candidates: list[tuple[int, int, int, int]] = []
    for box in merged:
        width = box_width(box)
        height = box_height(box)
        if width < 95 or height < 95:
            continue
        if width > band_width * 0.22 or height > band_height * 0.74:
            continue
        aspect = width / max(height, 1)
        if aspect < 0.72 or aspect > 1.32:
            continue
        if light_pixel_ratio(image, box, step=5) < 0.18 or dark_pixel_ratio(image, box, step=5) < 0.04:
            continue
        candidates.append(qrcode_box_from_card_box(box, image.size, pad_ratio=0.07))

    return sorted(candidates, key=lambda item: item[0])[:expected_count]


def detect_qr_like_boxes_in_bottom_band(
    canvas: Image.Image,
    bottom_band: tuple[int, int, int, int],
    *,
    expected_count: int,
) -> list[tuple[int, int, int, int]]:
    if expected_count <= 0:
        return []
    image = canvas.convert("RGB")
    pixels = image.load()
    band_width = box_width(bottom_band)
    band_height = box_height(bottom_band)
    scan_left = bottom_band[0] + int(band_width * 0.56)
    scan_top = bottom_band[1] + int(band_height * 0.08)
    scan_bottom = bottom_band[1] + int(band_height * 0.82)
    step = 4
    dark_boxes: list[tuple[int, int, int, int]] = []
    for y in range(scan_top, scan_bottom, step):
        for x in range(scan_left, bottom_band[2], step):
            if is_qr_dark_pixel(pixels[x, y]):
                dark_boxes.append((x, y, min(x + step, bottom_band[2]), min(y + step, bottom_band[3])))

    if not dark_boxes:
        return []

    merged = merge_nearby_boxes(dark_boxes, padding=18)
    candidates: list[tuple[int, int, int, int]] = []
    for box in merged:
        width = box_width(box)
        height = box_height(box)
        if width < 72 or height < 72:
            continue
        if width > band_width * 0.28 or height > band_height * 0.90:
            continue
        aspect = width / max(height, 1)
        if aspect < 0.55 or aspect > 1.65:
            continue
        dark_ratio = dark_pixel_ratio(image, box, step=5)
        if dark_ratio < 0.08 or dark_ratio > 0.68:
            continue
        side = max(width, min(height, int(width * 1.18)))
        side = min(side, int(band_height * 0.70))
        square = clamp_box((box[0], box[1], box[0] + side, box[1] + side), image.size)
        candidates.append(square)

    return sorted(candidates, key=lambda item: item[0])[:expected_count]


def normalize_qrcode_box_count(
    detected_boxes: list[tuple[int, int, int, int]],
    ratio_boxes: list[tuple[int, int, int, int]],
    expected_count: int,
    canvas_size: tuple[int, int],
) -> list[tuple[int, int, int, int]]:
    boxes = [clamp_box(box, canvas_size) for box in sorted(detected_boxes, key=lambda item: item[0])]
    for ratio_box in ratio_boxes:
        if len(boxes) >= expected_count:
            break
        if not any(boxes_overlap_or_near(existing, ratio_box, padding=32) for existing in boxes):
            boxes.append(ratio_box)
    return sorted(boxes, key=lambda item: item[0])[:expected_count]


def is_placeholder_pixel(pixel: tuple[int, int, int]) -> bool:
    r, g, b = pixel
    return r >= 232 and g >= 232 and b >= 232 and max(r, g, b) - min(r, g, b) <= 18


def is_qrcode_card_pixel(pixel: tuple[int, int, int]) -> bool:
    r, g, b = pixel
    return r >= 242 and g >= 242 and b >= 238 and max(r, g, b) - min(r, g, b) <= 24


def is_qr_dark_pixel(pixel: tuple[int, int, int]) -> bool:
    r, g, b = pixel
    return r <= 118 and g <= 118 and b <= 118 and max(r, g, b) - min(r, g, b) <= 62


def is_bottom_bar_pixel(pixel: tuple[int, int, int]) -> bool:
    r, g, b = pixel
    return r >= 210 and g >= 210 and b >= 205 and max(r, g, b) - min(r, g, b) <= 34


def dark_pixel_ratio(image: Image.Image, box: tuple[int, int, int, int], *, step: int) -> float:
    pixels = image.load()
    dark = 0
    total = 0
    for y in range(box[1], box[3], step):
        for x in range(box[0], box[2], step):
            total += 1
            if is_qr_dark_pixel(pixels[x, y]):
                dark += 1
    return dark / max(total, 1)


def light_pixel_ratio(image: Image.Image, box: tuple[int, int, int, int], *, step: int) -> float:
    pixels = image.load()
    light = 0
    total = 0
    for y in range(box[1], box[3], step):
        for x in range(box[0], box[2], step):
            total += 1
            if is_qrcode_card_pixel(pixels[x, y]):
                light += 1
    return light / max(total, 1)


def expand_box(box: tuple[int, int, int, int], pad: int, size: tuple[int, int]) -> tuple[int, int, int, int]:
    return clamp_box((box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad), size)


def detect_bottom_qrcode_placeholder_boxes(
    canvas: Image.Image,
    *,
    expected_count: int,
) -> list[tuple[int, int, int, int]]:
    if expected_count <= 0:
        return []
    image = canvas.convert("RGB")
    width, height = image.size
    pixels = image.load()
    scan_box = (
        int(width * 0.55),
        int(height * 0.82),
        width,
        height - max(8, int(height * 0.01)),
    )
    step = 2
    light_boxes: list[tuple[int, int, int, int]] = []
    for y in range(scan_box[1], scan_box[3], step):
        for x in range(scan_box[0], scan_box[2], step):
            if is_qrcode_card_pixel(pixels[x, y]):
                light_boxes.append((x, y, min(x + step, scan_box[2]), min(y + step, scan_box[3])))
    if not light_boxes:
        return []

    candidates: list[tuple[int, int, int, int]] = []
    for box in merge_nearby_boxes(light_boxes, padding=4):
        width_px = box_width(box)
        height_px = box_height(box)
        if width_px < 72 or height_px < 72:
            continue
        if width_px > width * 0.18 or height_px > height * 0.11:
            continue
        aspect = width_px / max(height_px, 1)
        if aspect < 0.68 or aspect > 1.42:
            continue
        if box[0] < int(width * 0.55) or box[1] < int(height * 0.84):
            continue
        candidates.append(box)

    return sorted(candidates, key=lambda item: item[0])[:expected_count]


def equalize_qrcode_boxes(
    boxes: list[tuple[int, int, int, int]],
    canvas_size: tuple[int, int],
) -> list[tuple[int, int, int, int]]:
    if not boxes:
        return []
    side = min(min(box_width(box), box_height(box)) for box in boxes)
    side = max(1, side)
    equalized: list[tuple[int, int, int, int]] = []
    for box in sorted(boxes, key=lambda item: item[0]):
        center_x = (box[0] + box[2]) // 2
        center_y = (box[1] + box[3]) // 2
        half = side // 2
        candidate = (center_x - half, center_y - half, center_x - half + side, center_y - half + side)
        equalized.append(clamp_square_box(candidate, canvas_size, side))
    return equalized


def clamp_square_box(
    box: tuple[int, int, int, int],
    canvas_size: tuple[int, int],
    side: int,
) -> tuple[int, int, int, int]:
    width, height = canvas_size
    x = max(0, min(box[0], width - side))
    y = max(0, min(box[1], height - side))
    return (x, y, x + side, y + side)


def qrcode_overlay_allowed_bottom_band(
    raw_bottom_band: tuple[int, int, int, int] | None,
    layout: dict[str, Any],
    canvas_size: tuple[int, int],
    *,
    bottom_bar_asset: dict[str, Any] | None,
) -> tuple[int, int, int, int]:
    width, height = canvas_size
    template_band = tuple(layout.get("bottom_bar", {}).get("box", (0, int(height * 0.86), width, height)))
    template_band = clamp_box(template_band, canvas_size)
    if not raw_bottom_band:
        return template_band

    completed = complete_bottom_bar_band(raw_bottom_band, canvas_size, bottom_bar_asset=bottom_bar_asset)
    top = max(template_band[1], completed[1])
    bottom = min(template_band[3], completed[3])
    if bottom - top < 80:
        return template_band
    return (0, top, width, bottom)


def constrain_qrcode_boxes_to_bottom_band(
    boxes: list[tuple[int, int, int, int]],
    bottom_band: tuple[int, int, int, int],
    canvas_size: tuple[int, int],
) -> list[tuple[int, int, int, int]]:
    if not boxes:
        return []
    qr_band = qrcode_overlay_qr_safe_band(bottom_band)
    detected_side = min(min(box_width(box), box_height(box)) for box in boxes)
    max_safe_side = int(box_height(bottom_band) * QRCODE_BOTTOM_MAX_SIDE_RATIO)
    max_side = max(1, min(detected_side, box_height(qr_band), max_safe_side))
    constrained: list[tuple[int, int, int, int]] = []
    for box in sorted(boxes, key=lambda item: item[0]):
        center_x = (box[0] + box[2]) // 2
        center_y = (box[1] + box[3]) // 2
        half = max_side // 2
        x = center_x - half
        y = center_y - half
        x = max(qr_band[0], min(x, qr_band[2] - max_side))
        y = max(qr_band[1], min(y, qr_band[3] - max_side))
        constrained.append(clamp_square_box((x, y, x + max_side, y + max_side), canvas_size, max_side))
    return constrained


def qrcode_overlay_qr_safe_band(bottom_band: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    height = box_height(bottom_band)
    top_padding = max(QRCODE_BOTTOM_TOP_PADDING_MIN, int(height * QRCODE_BOTTOM_TOP_PADDING_RATIO))
    label_reserve = max(QRCODE_BOTTOM_LABEL_RESERVE_MIN, int(height * QRCODE_BOTTOM_LABEL_RESERVE_RATIO))
    top = min(bottom_band[3] - 1, bottom_band[1] + top_padding)
    bottom = max(top + 1, bottom_band[3] - label_reserve)
    return (bottom_band[0], top, bottom_band[2], bottom)


def bottom_band_from_boxes(
    boxes: list[tuple[int, int, int, int]],
    canvas_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    if not boxes:
        return (0, int(canvas_size[1] * 0.86), canvas_size[0], canvas_size[1])
    top = max(0, min(box[1] for box in boxes) - int(canvas_size[1] * 0.035))
    return (0, top, canvas_size[0], canvas_size[1])


def complete_bottom_bar_band(
    raw_bottom_band: tuple[int, int, int, int],
    canvas_size: tuple[int, int],
    *,
    bottom_bar_asset: dict[str, Any] | None,
) -> tuple[int, int, int, int]:
    width, height = canvas_size
    bottom = raw_bottom_band[3]
    source_ratio = bottom_bar_aspect_ratio(bottom_bar_asset) or 4.224
    expected_height = int(round(width / source_ratio))
    expected_height = max(box_height(raw_bottom_band), expected_height)
    expected_height = min(expected_height, int(height * 0.20))
    top = max(0, bottom - expected_height)
    return (0, top, width, bottom)


def bottom_bar_aspect_ratio(bottom_bar_asset: dict[str, Any] | None) -> float | None:
    path = Path(bottom_bar_asset.get("_file_path") or bottom_bar_asset.get("file_path") or "") if bottom_bar_asset else None
    if not path:
        return None
    try:
        with Image.open(path) as image:
            if image.height <= 0:
                return None
            return image.width / image.height
    except Exception:
        return None


def qrcode_slot_ratios_from_bottom_bar_asset(
    bottom_bar_asset: dict[str, Any] | None,
    *,
    expected_count: int,
) -> list[tuple[float, float, float, float]]:
    path = Path(bottom_bar_asset.get("_file_path") or bottom_bar_asset.get("file_path") or "") if bottom_bar_asset else None
    if not path or not path.exists():
        return [slot["box_ratio"] for slot in DEFAULT_BOTTOM_BAR_QR_SLOTS[:expected_count]]
    try:
        with Image.open(path) as source:
            image = source.convert("RGB")
        ratios = detect_qrcode_dark_ratios_in_bottom_asset(image, expected_count=expected_count)
        if len(ratios) >= expected_count:
            return ratios[:expected_count]
    except Exception:
        pass
    return [slot["box_ratio"] for slot in DEFAULT_BOTTOM_BAR_QR_SLOTS[:expected_count]]


def detect_qrcode_dark_ratios_in_bottom_asset(
    image: Image.Image,
    *,
    expected_count: int,
) -> list[tuple[float, float, float, float]]:
    width, height = image.size
    pixels = image.load()
    step = 2
    dark_boxes: list[tuple[int, int, int, int]] = []
    for y in range(0, int(height * 0.75), step):
        for x in range(int(width * 0.55), width, step):
            if is_qr_dark_pixel(pixels[x, y]):
                dark_boxes.append((x, y, min(x + step, width), min(y + step, height)))
    candidates: list[tuple[int, int, int, int]] = []
    for box in merge_nearby_boxes(dark_boxes, padding=14):
        width_px = box_width(box)
        height_px = box_height(box)
        if width_px < 80 or height_px < 80:
            continue
        aspect = width_px / max(height_px, 1)
        if aspect < 0.76 or aspect > 1.24:
            continue
        candidates.append(box)
    ratios = []
    for box in sorted(candidates, key=lambda item: item[0])[:expected_count]:
        ratios.append((box[0] / width, box[1] / height, box[2] / width, box[3] / height))
    return ratios


def qrcode_box_from_card_box(
    box: tuple[int, int, int, int],
    size: tuple[int, int],
    *,
    pad_ratio: float,
) -> tuple[int, int, int, int]:
    width = box_width(box)
    height = box_height(box)
    pad = max(4, int(round(min(width, height) * pad_ratio)))
    side = max(1, min(width - pad * 2, height - pad * 2))
    center_x = (box[0] + box[2]) // 2
    half = side // 2
    x = center_x - half
    y = box[1] + pad
    if y + side > box[3] - pad:
        y = (box[1] + box[3] - side) // 2
    return clamp_box((x, y, x + side, y + side), size)


def merge_nearby_boxes(boxes: list[tuple[int, int, int, int]], *, padding: int = 12) -> list[tuple[int, int, int, int]]:
    merged: list[tuple[int, int, int, int]] = []
    for box in sorted(boxes, key=lambda item: (item[0], item[1])):
        found = False
        for index, existing in enumerate(merged):
            if boxes_overlap_or_near(existing, box, padding=padding):
                merged[index] = (
                    min(existing[0], box[0]),
                    min(existing[1], box[1]),
                    max(existing[2], box[2]),
                    max(existing[3], box[3]),
                )
                found = True
                break
        if not found:
            merged.append(box)
    return merged


def boxes_overlap_or_near(a: tuple[int, int, int, int], b: tuple[int, int, int, int], *, padding: int) -> bool:
    return not (
        a[2] + padding < b[0]
        or b[2] + padding < a[0]
        or a[3] + padding < b[1]
        or b[3] + padding < a[1]
    )


def fallback_qrcode_boxes_from_layout(layout: dict[str, Any]) -> list[tuple[int, int, int, int]]:
    bottom_box = tuple(layout["bottom_bar"]["box"])
    return equalize_qrcode_boxes(qrcode_boxes_from_bottom_band(bottom_box), CANVAS_SIZE)


def qrcode_boxes_from_bottom_band(
    bottom_box: tuple[int, int, int, int],
    *,
    slot_ratios: list[tuple[float, float, float, float]] | None = None,
) -> list[tuple[int, int, int, int]]:
    bottom_size = (box_width(bottom_box), box_height(bottom_box))
    boxes: list[tuple[int, int, int, int]] = []
    ratios = slot_ratios or [slot["box_ratio"] for slot in DEFAULT_BOTTOM_BAR_QR_SLOTS]
    for ratio in ratios:
        local = ratio_box_to_pixels(ratio, bottom_size)
        boxes.append(
            (
                bottom_box[0] + local[0],
                bottom_box[1] + local[1],
                bottom_box[0] + local[2],
                bottom_box[1] + local[3],
            )
        )
    return boxes


def paste_bottom_bar(canvas: Image.Image, bottom_bar_path: Path, layout: dict[str, Any], *, harmonized: bool = False) -> None:
    box = tuple(layout["bottom_bar"]["box"])
    size = (box_width(box), box_height(box))
    draw = ImageDraw.Draw(canvas)
    try:
        with Image.open(bottom_bar_path) as bottom_image:
            bottom = ImageOps.fit(bottom_image.convert("RGB"), size, method=Image.Resampling.LANCZOS).convert("RGBA")
            if harmonized:
                bottom = apply_top_alpha_feather(bottom, feather_height=34)
            canvas.alpha_composite(bottom, (box[0], box[1]))
    except Exception:
        draw.rectangle(box, fill="#0F766E")
        draw.text((box[0] + 72, box[1] + 48), "PUDOW", font=load_font(42, bold=True), fill="#FFFFFF")
    draw.line((box[0], box[1], box[2], box[1]), fill=(255, 255, 255, 34 if harmonized else 56), width=2)


def draw_footer_text(
    draw: ImageDraw.ImageDraw,
    *,
    layout: dict[str, Any],
    contact_text: str,
    custom_requirement: str,
) -> None:
    del custom_requirement
    text = contact_text.strip() or "扫码咨询当地销售顾问"
    draw_fitted_text(draw, text, layout["contact"], bold=True, shadow_alpha=150)


def draw_fitted_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    settings: dict[str, Any],
    *,
    bold: bool,
    shadow_alpha: int,
) -> None:
    font, lines = fit_text(draw, text, settings, bold=bold)
    box = tuple(settings["box"])
    x, y = box[0], box[1]
    fill = settings["fill"]
    line_gap = int(settings["line_gap"])
    for line in lines:
        draw.text((x + 2, y + 3), line, font=font, fill=(0, 0, 0, shadow_alpha))
        draw.text((x, y), line, font=font, fill=fill)
        bbox = draw.textbbox((x, y), line, font=font)
        y += bbox[3] - bbox[1] + line_gap


def fit_text(draw: ImageDraw.ImageDraw, text: str, settings: dict[str, Any], *, bold: bool) -> tuple[Any, list[str]]:
    text = text.strip()
    if not text:
        return load_font(int(settings["min_font_size"]), bold=bold), []

    box = tuple(settings["box"])
    max_width = box_width(box)
    max_height = box_height(box)
    max_lines = int(settings["max_lines"])
    line_gap = int(settings["line_gap"])
    for size in range(int(settings["font_size"]), int(settings["min_font_size"]) - 1, -2):
        font = load_font(size, bold=bold)
        lines = wrap_text(draw, text, font, max_width)
        if len(lines) <= max_lines and text_height(draw, lines, font, line_gap) <= max_height:
            return font, lines

    font = load_font(int(settings["min_font_size"]), bold=bold)
    lines = wrap_text(draw, text, font, max_width)[:max_lines]
    if lines:
        lines[-1] = ellipsize_line(draw, lines[-1], font, max_width)
    return font, lines


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


def ellipsize_line(draw: ImageDraw.ImageDraw, line: str, font: Any, max_width: int) -> str:
    suffix = "..."
    while line and draw.textbbox((0, 0), line + suffix, font=font)[2] > max_width:
        line = line[:-1]
    return (line + suffix) if line else suffix


def text_height(draw: ImageDraw.ImageDraw, lines: list[str], font: Any, line_gap: int) -> int:
    if not lines:
        return 0
    height = 0
    for index, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        height += bbox[3] - bbox[1]
        if index < len(lines) - 1:
            height += line_gap
    return height


def choose_logo_plate(logo_image: Image.Image) -> tuple[int, int, int, int]:
    sample = logo_image.convert("RGBA").resize((1, 1), Image.Resampling.LANCZOS).getpixel((0, 0))
    brightness = (sample[0] * 0.299) + (sample[1] * 0.587) + (sample[2] * 0.114)
    if brightness > 190:
        return (0, 0, 0, 96)
    return (255, 255, 255, 204)


def choose_harmonized_plate(canvas: Image.Image, rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    left = max(0, min(canvas.width - 1, int(rect[0])))
    top = max(0, min(canvas.height - 1, int(rect[1])))
    right = max(left + 1, min(canvas.width, int(rect[2])))
    bottom = max(top + 1, min(canvas.height, int(rect[3])))
    sample = canvas.convert("RGB").crop((left, top, right, bottom)).resize((1, 1), Image.Resampling.LANCZOS).getpixel((0, 0))
    brightness = (sample[0] * 0.299) + (sample[1] * 0.587) + (sample[2] * 0.114)
    if brightness < 146:
        return (255, 255, 255, 156)
    return (0, 0, 0, 82)


def apply_top_alpha_feather(image: Image.Image, *, feather_height: int) -> Image.Image:
    result = image.copy()
    alpha = Image.new("L", result.size, 255)
    draw = ImageDraw.Draw(alpha)
    limit = min(max(feather_height, 0), result.height)
    for y in range(limit):
        value = int(255 * (y / max(limit - 1, 1)))
        draw.line((0, y, result.width, y), fill=value)
    result.putalpha(alpha)
    return result


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    font: Any,
    fill: str,
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    x = box[0] + (box_width(box) - (bbox[2] - bbox[0])) // 2
    y = box[1] + (box_height(box) - (bbox[3] - bbox[1])) // 2
    draw.text((x, y), text, font=font, fill=fill)


def box_width(box: tuple[int, int, int, int]) -> int:
    return box[2] - box[0]


def box_height(box: tuple[int, int, int, int]) -> int:
    return box[3] - box[1]


def make_copy(node: dict[str, Any], product: dict[str, Any], payload: PosterTaskCreate) -> tuple[str, str]:
    title = payload.copy_preference.title.strip()
    subtitle = payload.copy_preference.subtitle.strip()
    if not title:
        title = f"{node['name']}好水相伴"
    if not subtitle:
        subtitle = f"{product['name']}，让真实产品融入每一个营销场景"
    return title[:18], subtitle[:42]


def validate_copy_preference(payload: PosterTaskCreate) -> None:
    if payload.copy_preference.mode != "manual":
        return
    title = payload.copy_preference.title.strip()
    subtitle = payload.copy_preference.subtitle.strip()
    if not title or not subtitle:
        raise ApiError("BAD_REQUEST", "手动文案模式下必须填写主标题和副标题")
    if len(title) > 18 or len(subtitle) > 42:
        raise ApiError("BAD_REQUEST", "主标题不能超过18字，副标题不能超过42字")


def generate_copy_or_fallback(
    *,
    node: dict[str, Any],
    product: dict[str, Any],
    payload: PosterTaskCreate,
) -> tuple[CopyGenerationResult, str]:
    if payload.copy_preference.mode == "manual":
        title = payload.copy_preference.title.strip()
        subtitle = payload.copy_preference.subtitle.strip()
        if not title or not subtitle:
            raise ApiError("BAD_REQUEST", "手动文案模式下必须填写主标题和副标题")
        if len(title) > 18 or len(subtitle) > 42:
            raise ApiError("BAD_REQUEST", "主标题不能超过18字，副标题不能超过42字")
        return (
            CopyGenerationResult(
                title=title,
                subtitle=subtitle,
                alternatives=[],
                risk_level="low",
                provider="user_manual",
            ),
            "user_manual",
        )

    try:
        copy_result = get_ai_provider().generate_copy(node=node, product=product, payload=payload)
        if not copy_result.title or not copy_result.subtitle:
            raise AiProviderError("AI copy result is missing title or subtitle")
        if copy_result.risk_level not in {"low", "medium", "high"}:
            raise AiProviderError("AI copy result has invalid risk_level")
        if copy_result.risk_level == "high":
            raise AiProviderError("AI copy result was high risk")
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


def resolve_node(payload: PosterTaskCreate) -> dict[str, Any]:
    is_custom_node = payload.node_id.startswith("custom_") or bool(payload.custom_node_name.strip())
    if is_custom_node:
        name = payload.custom_node_name.strip() or "自定义活动"
        date = payload.custom_node_date.strip()
        keywords = normalize_node_keywords(payload.custom_node_keywords) or [name, "健康饮水"]
        if date and date not in keywords:
            keywords.append(date)
        colors = normalize_node_colors(payload.custom_node_colors) or ["#0F766E", "#DBEAFE"]
        visual_direction = payload.custom_node_visual_direction.strip() or f"{name}主题，结合品牌健康饮水传播"
        copy_direction = payload.custom_node_copy_direction.strip() or f"{name}主题传播、健康饮水"
        return {
            "id": payload.node_id or f"custom_{name}",
            "name": name,
            "type": payload.custom_node_type.strip() or "custom",
            "date": date,
            "keywords": keywords,
            "colors": colors,
            "copy_direction": copy_direction,
            "visual_direction": visual_direction,
        }

    node = dict(find_node(payload.node_id))
    keywords = normalize_node_keywords(payload.custom_node_keywords)
    colors = normalize_node_colors(payload.custom_node_colors)
    visual_direction = payload.custom_node_visual_direction.strip()
    copy_direction = payload.custom_node_copy_direction.strip()
    if keywords:
        node["keywords"] = keywords
    if colors:
        node["colors"] = colors
    if visual_direction:
        node["visual_direction"] = visual_direction
    if copy_direction:
        node["copy_direction"] = copy_direction
    return node


def normalize_node_keywords(keywords: list[str]) -> list[str]:
    normalized = []
    for keyword in keywords:
        value = str(keyword).strip()
        if value and value not in normalized:
            normalized.append(value[:24])
    return normalized[:8]


def normalize_node_colors(colors: list[str]) -> list[str]:
    normalized = []
    for color in colors:
        value = str(color).strip()
        if is_hex_color(value):
            normalized.append(value)
    return normalized[:4]


def is_hex_color(value: str) -> bool:
    return len(value) == 7 and value.startswith("#") and all(char in "0123456789abcdefABCDEF" for char in value[1:])


def find_node(node_id: str) -> dict[str, Any]:
    for node in MARKETING_NODES:
        if node["id"] == node_id:
            return node
    raise ApiError("NOT_FOUND", "Marketing node not found", status_code=404, details={"node_id": node_id})


def find_product(product_id: str) -> dict[str, Any]:
    product = db_get_product(product_id)
    if product:
        return product
    for product in PRODUCTS:
        if product["id"] == product_id:
            return product
    raise ApiError("NOT_FOUND", "Product not found", status_code=404, details={"product_id": product_id})
