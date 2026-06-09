from __future__ import annotations

from typing import Any

from fastapi import BackgroundTasks

from app.db import get_product
from app.models import CopyPreference, OneClickPosterOverrides, OneClickPosterTaskCreate, PosterTaskCreate
from app.responses import ApiError
from app.services.assets import list_assets
from app.services.intent_parser import parse_one_click_intent
from app.services.one_click_guardrails import ensure_one_click_not_blocked
from app.services.poster import create_task
from app.services.prompt_builder import build_one_click_prompt_diagnostics


def create_one_click_poster_task(payload: OneClickPosterTaskCreate, background_tasks: BackgroundTasks) -> dict[str, Any]:
    parsed = parse_one_click_intent(payload.raw_instruction)
    ensure_one_click_not_blocked(parsed)
    task_payload, diagnostics = build_task_payload(parsed, payload.overrides)
    result = create_task(task_payload, background_tasks)
    return {
        **result,
        "intent": parsed["intent"],
        "prompt_diagnostics": diagnostics,
    }


def build_task_payload(parsed: dict[str, Any], overrides: OneClickPosterOverrides) -> tuple[PosterTaskCreate, dict[str, Any]]:
    intent = parsed["intent"]
    selected_node = intent.get("node")
    selected_product = intent.get("product")

    product_id = overrides.product_id or (selected_product or {}).get("id")
    if not product_id:
        raise ApiError(
            "ONE_CLICK_PRODUCT_NOT_FOUND",
            "未从产品库或产品素材中匹配到产品，请补充产品名称或先上传/索引产品素材。",
            status_code=422,
            details={"warnings": parsed["warnings"], "candidates": parsed["candidates"].get("products", [])},
        )
    product = get_product(product_id)
    if not product:
        raise ApiError(
            "ONE_CLICK_PRODUCT_NOT_FOUND",
            "指定产品不存在，请先确认产品库或素材索引。",
            status_code=422,
            details={"product_id": product_id},
        )

    product_asset_ids = choose_product_asset_ids(product_id, overrides)
    scene_asset_id = overrides.scene_asset_id
    if not scene_asset_id and not product_asset_ids:
        raise ApiError(
            "ONE_CLICK_PRODUCT_ASSET_REQUIRED",
            "已匹配产品，但没有找到可用于生成的产品图素材。",
            status_code=422,
            details={"product_id": product_id, "product": product},
        )

    node_id = overrides.node_id or (selected_node or {}).get("id") or "custom_one_click"
    node_name = (selected_node or {}).get("name") or "一键生成活动"
    style_keywords = intent.get("style_keywords") or []
    visual_elements = intent.get("visual_elements") or []
    scene_prompt = overrides.scene_prompt or intent.get("scene_prompt") or parsed["raw_instruction"]
    custom_requirement = overrides.custom_requirement or build_custom_requirement(
        parsed["raw_instruction"],
        style_keywords,
        visual_elements,
    )

    qrcode_asset_ids = choose_qrcode_asset_ids(overrides)
    bottom_bar_asset_id = overrides.bottom_bar_asset_id or choose_first_asset_id("bottom_bar") or "asset_bottom_default"
    task_payload = PosterTaskCreate(
        node_id=node_id,
        product_id=product_id,
        creation_mode="one_click",
        template_id=overrides.template_id or "template_v1_vertical_standard",
        custom_node_name=node_name if node_id.startswith("custom_") else "",
        custom_node_keywords=[*style_keywords, *visual_elements][:8],
        custom_node_visual_direction=scene_prompt,
        custom_node_copy_direction=intent.get("copy_direction") or "",
        scene_asset_id=scene_asset_id,
        product_asset_ids=product_asset_ids,
        logo_asset_id=overrides.logo_asset_id,
        qrcode_asset_id=qrcode_asset_ids[0] if qrcode_asset_ids else None,
        qrcode_asset_ids=qrcode_asset_ids,
        bottom_bar_asset_id=bottom_bar_asset_id,
        contact_text=overrides.contact_text or "",
        scene_prompt=scene_prompt,
        custom_requirement=custom_requirement,
        raw_instruction=parsed["raw_instruction"],
        resolved_intent=parsed,
        render_mode=overrides.render_mode or "full_fusion",
        qrcode_policy=overrides.qrcode_policy or "optional_overlay",
        copy_preference=overrides.copy_preference or CopyPreference(mode="ai"),
    )

    selected_assets = {
        "node_id": task_payload.node_id,
        "product_id": task_payload.product_id,
        "product_asset_ids": task_payload.product_asset_ids,
        "scene_asset_id": task_payload.scene_asset_id,
        "logo_asset_id": task_payload.logo_asset_id or "asset_logo_original",
        "qrcode_asset_id": task_payload.qrcode_asset_id,
        "qrcode_asset_ids": task_payload.qrcode_asset_ids,
        "bottom_bar_asset_id": task_payload.bottom_bar_asset_id,
    }
    prompt_diagnostics = build_one_click_prompt_diagnostics(
        raw_instruction=parsed["raw_instruction"],
        node_name=node_name,
        product=product,
        scene_prompt=scene_prompt,
        custom_requirement=custom_requirement,
        style_keywords=style_keywords,
        visual_elements=visual_elements,
        selected_assets=selected_assets,
    ).to_public_dict()
    diagnostics = {
        "raw_instruction": parsed["raw_instruction"],
        "scene_prompt": scene_prompt,
        "custom_requirement": custom_requirement,
        **prompt_diagnostics,
        "style_keywords": style_keywords,
        "visual_elements": visual_elements,
        "selected": selected_assets,
        "render_mode": task_payload.render_mode,
        "task_qrcode_policy": task_payload.qrcode_policy,
        "confidence": parsed["confidence"],
        "warnings": parsed["warnings"],
    }
    return task_payload, diagnostics


def choose_product_asset_ids(product_id: str, overrides: OneClickPosterOverrides) -> list[str]:
    if overrides.product_asset_ids is not None:
        return overrides.product_asset_ids[:5]
    return [asset["id"] for asset in list_assets(asset_type="product_image", product_id=product_id)[:3]]


def choose_first_asset_id(asset_type: str) -> str | None:
    assets = list_assets(asset_type=asset_type)
    return assets[0]["id"] if assets else None


def choose_qrcode_asset_ids(overrides: OneClickPosterOverrides) -> list[str]:
    if overrides.qrcode_asset_ids is not None:
        return overrides.qrcode_asset_ids[:4]
    if overrides.qrcode_asset_id is not None:
        return [overrides.qrcode_asset_id] if overrides.qrcode_asset_id else []
    qrcodes = list_assets(asset_type="qrcode")
    preferred_names = ("朴道公众号", "朴道官方视频号", "公众号", "视频号")
    selected: list[str] = []
    for name in preferred_names:
        for asset in qrcodes:
            asset_name = f"{asset.get('name', '')} {asset.get('file_name', '')}"
            if name in asset_name and asset["id"] not in selected:
                selected.append(asset["id"])
                break
    for asset in qrcodes:
        if len(selected) >= 2:
            break
        if asset["id"] not in selected:
            selected.append(asset["id"])
    return selected[:2]


def build_custom_requirement(raw_instruction: str, style_keywords: list[str], visual_elements: list[str]) -> str:
    parts = [f"一键生图需求：{raw_instruction}"]
    if style_keywords:
        parts.append(f"风格关键词：{'、'.join(style_keywords)}")
    if visual_elements:
        parts.append(f"画面元素：{'、'.join(visual_elements)}")
    parts.append("产品与节日氛围需要自然融合，避免机械拼贴。")
    return "；".join(parts)
