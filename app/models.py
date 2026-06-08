from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CopyPreference(BaseModel):
    mode: Literal["ai", "manual"] = "ai"
    title: str = ""
    subtitle: str = ""


class PosterTaskCreate(BaseModel):
    node_id: str
    product_id: str
    creation_mode: Literal["manual", "one_click"] = "manual"
    template_id: str = "template_v1_vertical_standard"
    custom_node_name: str = ""
    custom_node_date: str = ""
    custom_node_type: str = "custom"
    custom_node_keywords: list[str] = Field(default_factory=list)
    custom_node_colors: list[str] = Field(default_factory=list)
    custom_node_visual_direction: str = ""
    custom_node_copy_direction: str = ""
    scene_asset_id: str | None = None
    product_asset_ids: list[str] = Field(default_factory=list)
    logo_asset_id: str | None = None
    qrcode_asset_id: str | None = None
    bottom_bar_asset_id: str | None = None
    contact_text: str = ""
    scene_prompt: str = ""
    custom_requirement: str = ""
    raw_instruction: str = ""
    resolved_intent: dict[str, Any] = Field(default_factory=dict)
    render_mode: Literal["standard", "full_fusion"] = "standard"
    qrcode_policy: Literal["optional_overlay", "preserve_fusion"] = "optional_overlay"
    copy_preference: CopyPreference = Field(default_factory=CopyPreference)


class ComplianceCheckRequest(BaseModel):
    title: str = ""
    subtitle: str = ""
    product_id: str | None = None
    node_id: str | None = None


class PosterTaskRerenderRequest(BaseModel):
    title: str = ""
    subtitle: str = ""


class OneClickIntentRequest(BaseModel):
    raw_instruction: str = Field(..., min_length=1, max_length=1000)


class OneClickPosterOverrides(BaseModel):
    node_id: str | None = None
    product_id: str | None = None
    render_mode: Literal["standard", "full_fusion"] | None = None
    qrcode_policy: Literal["optional_overlay", "preserve_fusion"] | None = None
    template_id: str | None = None
    scene_asset_id: str | None = None
    product_asset_ids: list[str] | None = None
    logo_asset_id: str | None = None
    qrcode_asset_id: str | None = None
    bottom_bar_asset_id: str | None = None
    contact_text: str | None = None
    scene_prompt: str | None = None
    custom_requirement: str | None = None
    copy_preference: CopyPreference | None = None


class OneClickPosterTaskCreate(BaseModel):
    raw_instruction: str = Field(..., min_length=1, max_length=1000)
    overrides: OneClickPosterOverrides = Field(default_factory=OneClickPosterOverrides)
