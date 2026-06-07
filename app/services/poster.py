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
from app.services.ai_provider import AiProviderError, CopyGenerationResult, default_safe_zones, get_ai_provider
from app.services.assets import get_asset, require_asset
from app.services.compliance import check_copy
from app.utils import load_font, new_id, now_iso, public_storage_url
from config.seed_data import MARKETING_NODES, PRODUCTS


CANVAS_SIZE = (1080, 1920)
_TASK_LOCKS: dict[str, threading.Lock] = {}
_TASK_LOCKS_GUARD = threading.Lock()


def create_task(payload: PosterTaskCreate, background_tasks: BackgroundTasks) -> dict[str, str]:
    del background_tasks

    find_node(payload.node_id)
    find_product(payload.product_id)
    if payload.template_id != "template_v1_vertical_standard":
        raise ApiError(
            "BAD_REQUEST",
            "MVP only supports template_v1_vertical_standard",
            details={"template_id": payload.template_id},
        )

    asset_mode, product_assets, scene_asset = resolve_task_assets(payload)

    logo_asset_id = payload.logo_asset_id or "asset_logo_original"
    qrcode_asset_id = payload.qrcode_asset_id
    bottom_bar_asset_id = payload.bottom_bar_asset_id or "asset_bottom_default"
    require_asset(logo_asset_id, "logo")
    if qrcode_asset_id:
        require_asset(qrcode_asset_id, "qrcode")
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
    node = find_node(payload.node_id)
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
    node = find_node(payload.node_id)
    product = find_product(payload.product_id)
    resolved = task["resolved_asset_ids"]
    layout = get_template_layout(payload.template_id)
    asset_mode = resolved.get("asset_mode") or ("scene_image" if resolved.get("scene_asset_id") else "product_image")

    product_asset = require_asset(resolved["product_asset_id"], "product_image") if resolved.get("product_asset_id") else None
    scene_reference_asset_id = resolved.get("scene_reference_asset_id")
    scene_reference_asset = require_asset(scene_reference_asset_id, "scene_image") if scene_reference_asset_id else None
    scene_asset = require_asset(resolved["scene_asset_id"], "scene_image") if resolved.get("scene_asset_id") else None
    logo_asset = require_asset(resolved["logo_asset_id"], "logo")
    qrcode_asset = require_asset(resolved["qrcode_asset_id"], "qrcode") if resolved.get("qrcode_asset_id") else None
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
            product_asset=product_asset,
            scene_reference_asset=scene_reference_asset,
            output_dir=output_dir,
        )

    if not reuse_existing_scene:
        scene_base_path = output_dir / f"scene_base{suffix}.jpg"
        base_canvas.convert("RGB").save(scene_base_path, "JPEG", quality=92, optimize=True)
        fusion_meta["base_scene_url"] = public_storage_url(scene_base_path)
        fusion_meta["rerender_product_overlay"] = local_product_overlay

    canvas = apply_layout_panels(base_canvas, layout)
    draw = ImageDraw.Draw(canvas)

    if local_product_overlay and product_asset:
        paste_product_local(canvas, Path(product_asset["_file_path"]), layout)
    paste_logo(canvas, Path(logo_asset["_file_path"]), layout)
    draw_text_block(draw, copy["title"], copy["subtitle"], layout)
    paste_bottom_bar(canvas, Path(bottom_bar_asset["_file_path"]), layout)
    draw_footer_text(
        draw,
        layout=layout,
        contact_text=payload.contact_text,
        custom_requirement=payload.custom_requirement,
    )
    if qrcode_asset:
        paste_qrcode(canvas, Path(qrcode_asset["_file_path"]), layout)

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
        "copy": copy,
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
        "copy": {
            "title": copy["title"],
            "subtitle": copy["subtitle"],
        },
    }
    return poster, composition["fusion"]


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
    product_asset: dict[str, Any],
    scene_reference_asset: dict[str, Any] | None,
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
            canvas_size=CANVAS_SIZE,
            safe_zones=default_safe_zones(),
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
        load_canvas_image(source_path, blur_radius=2.2, tint_color="#F7FBFA", tint_alpha=0.18),
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
        "preserve_product_pixels": fusion_meta["preserve_product_pixels"],
        "generated_image_contains_product": fusion_meta.get(
            "generated_image_contains_product",
            fusion_meta["mode"] in {"scene_with_product", "prebuilt_scene_image", "local_mock_composite", "image_edit_json_reference"},
        ),
        "final_layout_engine": "pillow_template_overlay",
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
        "rerender_product_overlay": bool(fusion_meta.get("rerender_product_overlay")),
        "rerender_reused_scene": bool(fusion_meta.get("rerender_reused_scene")),
    }


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


def paste_logo(canvas: Image.Image, logo_path: Path, layout: dict[str, Any]) -> None:
    settings = layout["logo"]
    box = tuple(settings["box"])
    try:
        with Image.open(logo_path) as logo:
            logo_image = logo.convert("RGBA")
            logo_image.thumbnail(tuple(settings["max_size"]), Image.Resampling.LANCZOS)
            x = box[0]
            y = box[1] + (box_height(box) - logo_image.height) // 2
            draw = ImageDraw.Draw(canvas)
            plate = choose_logo_plate(logo_image)
            draw.rounded_rectangle((x - 14, y - 10, x + logo_image.width + 14, y + logo_image.height + 10), radius=16, fill=plate)
            shadow = Image.new("RGBA", logo_image.size, (0, 0, 0, 0))
            shadow.putalpha(logo_image.getchannel("A").filter(ImageFilter.GaussianBlur(4)))
            canvas.alpha_composite(shadow, (x + 2, y + 3))
            canvas.alpha_composite(logo_image, (x, y))
    except Exception:
        draw = ImageDraw.Draw(canvas)
        draw.text((box[0], box[1]), "PUDOW", font=load_font(46, bold=True), fill="#FFFFFF")


def paste_qrcode(canvas: Image.Image, qrcode_path: Path, layout: dict[str, Any]) -> None:
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
            draw.rounded_rectangle(card_box, radius=24, fill="#FFFFFF", outline=(15, 23, 42, 42), width=2)
            image_x = image_box[0] + (box_width(image_box) - target_size) // 2
            image_y = image_box[1] + (box_height(image_box) - target_size) // 2
            canvas.alpha_composite(qrcode_square, (image_x, image_y))
            caption_font = load_font(24, bold=True)
            draw_centered_text(draw, "Scan", (card_box[0], int(settings["caption_y"]), card_box[2], card_box[3] - 8), caption_font, "#12312E")
    except Exception:
        pass


def paste_bottom_bar(canvas: Image.Image, bottom_bar_path: Path, layout: dict[str, Any]) -> None:
    box = tuple(layout["bottom_bar"]["box"])
    size = (box_width(box), box_height(box))
    draw = ImageDraw.Draw(canvas)
    try:
        with Image.open(bottom_bar_path) as bottom_image:
            bottom = ImageOps.fit(bottom_image.convert("RGB"), size, method=Image.Resampling.LANCZOS).convert("RGBA")
            canvas.alpha_composite(bottom, (box[0], box[1]))
    except Exception:
        draw.rectangle(box, fill="#0F766E")
        draw.text((box[0] + 72, box[1] + 48), "PUDOW", font=load_font(42, bold=True), fill="#FFFFFF")
    draw.line((box[0], box[1], box[2], box[1]), fill=(255, 255, 255, 56), width=2)


def draw_footer_text(
    draw: ImageDraw.ImageDraw,
    *,
    layout: dict[str, Any],
    contact_text: str,
    custom_requirement: str,
) -> None:
    del custom_requirement
    text = contact_text.strip() or "Contact local sales consultant"
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
