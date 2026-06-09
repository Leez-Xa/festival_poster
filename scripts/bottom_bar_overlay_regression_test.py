from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import OneClickPosterOverrides
from app.services.one_click import build_task_payload
from app.services.poster import qrcode_overlay_boxes_from_template_bottom_bar


SCENARIOS = [
    "端午节给名士K2饮水机做一张朋友圈竖版海报，活动是扫码咨询端午饮水方案，清爽绿色风格，有粽叶、水波纹、健康饮水场景，突出产品陪伴和扫码咨询。",
    "中秋节给商用净水设备做一张高级感海报，活动是企业团购咨询，现代极简、银白蓝色、留白多，画面有月光、水杯、办公室茶水间，必须有产品主体和扫码关注。",
    "春节给名士K2饮水机做家庭年夜饭场景海报，活动是新年健康饮水焕新季，温暖喜庆但不要俗气，红金点缀，包含团圆餐桌、水汽、灯笼、产品和扫码了解优惠。",
    "夏至节气给净水产品做一张清凉插画风海报，活动是扫码预约试饮，波浪形底部留白、蓝绿色渐变、水花和植物元素，整体轻盈通透。",
    "给名士K2做一张节日活动海报，清爽高级，突出健康饮水和扫码咨询。",
]

REQUIRED_PROMPT_SNIPPETS = (
    "去掉二维码后的底部宣传条会作为模型参考",
    "底部宣传条必须只融合在海报底部",
    "顶部禁止出现扫码关注、公众号、视频号、公司信息、二维码槽位或底部宣传条内容",
    "真实二维码会由本地 Pillow 贴回",
)


class RegressionFailure(Exception):
    pass


def main() -> int:
    results: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        parsed = build_parsed_intent(scenario)
        task_payload, diagnostics = build_task_payload(parsed, OneClickPosterOverrides())
        assert_prompt_diagnostics(diagnostics)
        assert_task_uses_local_bottom_bar(task_payload)
        results.append(
            {
                "scenario": scenario,
                "asset_roles": diagnostics["asset_roles"],
                "qrcode_policy": diagnostics["qrcode_policy"],
                "bottom_bar_asset_id": diagnostics["selected"]["bottom_bar_asset_id"],
            }
        )

    overlay_checks = assert_bottom_bar_qrcode_geometry()
    output = {
        "status": "ok",
        "scenario_count": len(results),
        "results": results,
        "overlay_checks": overlay_checks,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def build_parsed_intent(raw_instruction: str) -> dict[str, Any]:
    return {
        "raw_instruction": raw_instruction,
        "intent": {
            "node": {"id": "node_dragon_boat", "name": "端午"},
            "product": {"id": "product_k2"},
            "scene_prompt": raw_instruction,
            "copy_direction": "",
            "style_keywords": ["清爽", "高级", "节日活动"],
            "visual_elements": ["产品", "活动", "扫码咨询", "节日元素"],
        },
        "warnings": [],
        "candidates": {"products": []},
        "confidence": {"product": 1.0, "node": 1.0},
    }


def assert_prompt_diagnostics(diagnostics: dict[str, Any]) -> None:
    asset_roles = diagnostics.get("asset_roles") or []
    if "bottom_bar" not in asset_roles:
        raise RegressionFailure(f"prompt diagnostics should send QR-removed bottom_bar as image model role: {asset_roles}")
    if "logo" not in asset_roles:
        raise RegressionFailure(f"prompt diagnostics should keep logo as image model role: {asset_roles}")

    prompt = diagnostics.get("final_image_prompt") or ""
    missing = [snippet for snippet in REQUIRED_PROMPT_SNIPPETS if snippet not in prompt]
    if missing:
        raise RegressionFailure(f"final_image_prompt missing required snippets: {missing}")


def assert_task_uses_local_bottom_bar(task_payload: Any) -> None:
    if not task_payload.bottom_bar_asset_id:
        raise RegressionFailure("one-click task payload should retain a bottom_bar_asset_id for local composition")
    if not task_payload.qrcode_asset_ids:
        raise RegressionFailure("one-click task payload should retain QR assets for exact local overlay")


def assert_bottom_bar_qrcode_geometry() -> dict[str, Any]:
    bottom_bar = (0, 1760, 1080, 1920)
    layout = {"bottom_bar": {"box": bottom_bar}}
    plan = qrcode_overlay_boxes_from_template_bottom_bar(
        layout,
        expected_count=2,
        bottom_bar_asset=None,
        canvas_size=(1080, 1920),
    )
    boxes = plan["boxes"]
    if len(boxes) != 2:
        raise RegressionFailure(f"expected 2 QR boxes, got {len(boxes)}")
    for box in boxes:
        if not box_inside(box, bottom_bar):
            raise RegressionFailure(f"QR box {box} is outside bottom bar {bottom_bar}")
    if boxes_overlap(boxes[0], boxes[1]):
        raise RegressionFailure(f"QR boxes overlap: {boxes}")
    return {"bottom_bar": list(bottom_bar), "source": plan["source"], "qrcode_boxes": [list(box) for box in boxes]}


def box_inside(inner: tuple[int, int, int, int], outer: tuple[int, int, int, int]) -> bool:
    return inner[0] >= outer[0] and inner[1] >= outer[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def boxes_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


if __name__ == "__main__":
    raise SystemExit(main())
