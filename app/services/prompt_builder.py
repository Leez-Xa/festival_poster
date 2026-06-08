from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


DEFAULT_NEGATIVE_PROMPT = (
    "不要改变产品真实外观、颜色、结构、比例、材质和细节；"
    "不要生成额外Logo、额外二维码、伪造品牌标识、水印、价格牌、按钮或贴纸；"
    "不要英文文案、乱码、错别字、错误汉字、硬广口号、绝对化承诺或医疗功效暗示；"
    "不要机械贴片、硬边抠图、孤立漂浮素材、光影割裂或风格割裂。"
)


@dataclass
class PromptDiagnostics:
    positive_prompt: str
    negative_prompt: str
    final_image_prompt: str
    asset_roles: list[str] = field(default_factory=lambda: ["product"])
    qrcode_policy: str = "reserve_clean_area_for_exact_overlay"
    fusion_strategy: str = "ai_full_poster_fusion_with_exact_brand_overlay"
    notes: list[str] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "positive_prompt": self.positive_prompt,
            "negative_prompt": self.negative_prompt,
            "final_image_prompt": self.final_image_prompt,
            "asset_roles": self.asset_roles,
            "qrcode_policy": self.qrcode_policy,
            "fusion_strategy": self.fusion_strategy,
            "notes": self.notes,
        }


def build_one_click_prompt_diagnostics(
    *,
    raw_instruction: str,
    node_name: str,
    product: dict[str, Any],
    scene_prompt: str,
    custom_requirement: str,
    style_keywords: list[str],
    visual_elements: list[str],
    selected_assets: dict[str, Any],
) -> PromptDiagnostics:
    asset_roles = infer_asset_roles(selected_assets)
    qrcode_policy = build_qrcode_policy(asset_roles)
    product_points = "、".join(str(item) for item in product.get("selling_points", []) if item)
    style_text = "、".join([*style_keywords, *visual_elements]) or "节日氛围、真实生活空间、干净高级"
    positive_prompt = (
        f"一键生图需求：{raw_instruction}。"
        f"生成一张1080x1920竖版中文节日产品海报，节点为“{node_name}”。"
        f"产品参考：{product.get('name', '产品')}，品类：{product.get('category', '')}，生活化卖点参考：{product_points}。"
        f"风格和画面元素：{style_text}。"
        f"具体场景：{scene_prompt}。"
        f"补充要求：{custom_requirement}。"
        "产品、Logo、底部宣传条、二维码预留区、节日元素、中文标题和整体色调必须像一张完整设计稿，"
        "通过统一光影、透视、材质、留白、层级和色彩自然融合，避免机械拼贴。"
        "产品必须保持真实外观、结构、颜色、比例和材质，像原本就在餐边柜、厨房台面、客厅边柜或茶水间里一样。"
        "中文主标题和副标题要清晰可读，先表达节日氛围，再自然带出产品陪伴。"
        "Logo、底部宣传条和二维码只使用已选参考素材作为视觉参考，不生成额外品牌元素或额外二维码。"
        f"二维码策略：{qrcode_policy}。"
    )
    final_image_prompt = (
        f"{positive_prompt}"
        "最终图片模型应优先理解并融合整张海报视觉系统；输出图将直接作为最终海报使用，不再进行本地二次拼贴。"
    )
    notes = [
        "one-click planned diagnostics; runtime AI provider may refine copy and final prompt",
        "one-click output uses the AI-generated final poster directly without local overlay",
    ]
    return PromptDiagnostics(
        positive_prompt=positive_prompt,
        negative_prompt=DEFAULT_NEGATIVE_PROMPT,
        final_image_prompt=final_image_prompt,
        asset_roles=asset_roles,
        qrcode_policy=qrcode_policy,
        notes=notes,
    )


def infer_asset_roles(selected_assets: dict[str, Any]) -> list[str]:
    roles = ["product"]
    if selected_assets.get("logo_asset_id"):
        roles.append("logo")
    if selected_assets.get("bottom_bar_asset_id"):
        roles.append("bottom_bar")
    if selected_assets.get("qrcode_asset_id"):
        roles.append("qrcode")
    return roles


def build_qrcode_policy(asset_roles: list[str]) -> str:
    if "qrcode" in asset_roles:
        return (
            "use supplied qrcode as the only qrcode-like visual; keep one crisp high-contrast code area and do not add extra qrcodes"
        )
    return (
        "do not generate or hallucinate any qrcode pattern; leave one clean placeholder/contact area only if needed"
    )
