from __future__ import annotations

from pydantic import BaseModel, Field


class CopyPreference(BaseModel):
    title: str = ""
    subtitle: str = ""


class PosterTaskCreate(BaseModel):
    node_id: str
    product_id: str
    template_id: str = "template_v1_vertical_standard"
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
