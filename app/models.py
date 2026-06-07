from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CopyPreference(BaseModel):
    mode: Literal["ai", "manual"] = "ai"
    title: str = ""
    subtitle: str = ""


class PosterTaskCreate(BaseModel):
    node_id: str
    product_id: str
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
    copy_preference: CopyPreference = Field(default_factory=CopyPreference)


class ComplianceCheckRequest(BaseModel):
    title: str = ""
    subtitle: str = ""
    product_id: str | None = None
    node_id: str | None = None


class PosterTaskRerenderRequest(BaseModel):
    title: str = ""
    subtitle: str = ""
