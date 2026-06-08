from __future__ import annotations

import re
from typing import Any

from app.db import list_products
from app.services.assets import list_assets
from app.services.one_click_guardrails import evaluate_one_click_guardrails
from config.seed_data import MARKETING_NODES


NODE_ALIASES: dict[str, list[str]] = {
    "node_spring_festival": ["春节", "新年", "过年", "除夕", "新春", "spring festival", "chinese new year"],
    "node_lantern_festival": ["元宵", "灯会", "汤圆", "lantern festival"],
    "node_lichun": ["立春", "春天", "春日", "start of spring"],
    "node_lidong": ["立冬", "入冬", "冬日", "start of winter"],
    "node_dongzhi": ["冬至", "饺子", "汤圆", "冬天", "winter solstice"],
    "node_mid_autumn": ["中秋", "中秋节", "月饼", "月亮", "圆月", "团圆", "mid autumn", "mid-autumn"],
    "node_national_day": ["国庆", "十一", "祖国", "长假", "national day"],
    "node_double_11": ["双11", "双十一", "11.11", "促销"],
    "node_618": ["618", "年中", "大促"],
    "node_anniversary": ["周年", "周年庆", "庆典", "答谢"],
}

STYLE_KEYWORDS = [
    "温馨",
    "团圆",
    "高级",
    "科技",
    "清爽",
    "简约",
    "国潮",
    "暖色",
    "冷色",
    "喜庆",
    "自然",
    "环保",
    "促销",
    "商务",
    "家庭",
]

VISUAL_ELEMENTS = [
    "月饼",
    "月亮",
    "圆月",
    "灯笼",
    "烟花",
    "桂花",
    "团圆饭",
    "客厅",
    "厨房",
    "餐桌",
    "水杯",
    "礼盒",
    "祥云",
    "花瓣",
    "雪",
    "绿植",
]

ACTIVITY_KEYWORDS = [
    "春节",
    "spring festival",
    "chinese new year",
    "除夕",
    "新年",
    "元宵",
    "lantern festival",
    "清明",
    "五一",
    "劳动节",
    "端午",
    "端午节",
    "dragon boat festival",
    "中秋",
    "中秋节",
    "mid autumn",
    "mid-autumn",
    "国庆",
    "national day",
    "重阳",
    "冬至",
    "winter solstice",
    "立春",
    "start of spring",
    "立夏",
    "立秋",
    "立冬",
    "start of winter",
    "小满",
    "芒种",
    "小暑",
    "大暑",
    "处暑",
    "白露",
    "寒露",
    "小雪",
    "大雪",
    "小寒",
    "大寒",
    "618",
    "双11",
    "双十一",
    "开业",
    "门店开业",
    "周年庆",
    "客户答谢",
    "答谢日",
    "发布会",
    "新品发布",
]


def analyze_one_click_intent(raw_instruction: str) -> dict[str, Any]:
    instruction = normalize_instruction(raw_instruction)
    products = list_products()
    product_candidates = rank_products(raw_instruction, products)
    node_candidates = rank_nodes(raw_instruction)
    style_keywords = find_terms(raw_instruction, STYLE_KEYWORDS)
    visual_elements = find_terms(raw_instruction, VISUAL_ELEMENTS)
    product_hint = extract_product_hint(raw_instruction, node_candidates)
    node = node_candidates[0]["node"] if node_candidates else None
    product = product_candidates[0]["product"] if product_candidates else None
    activity_hint = extract_activity_hint(raw_instruction, node_candidates)

    warnings: list[str] = []
    if not product:
        warnings.append("未在产品库中匹配到明确产品，请选择或上传真实产品素材后再生成。")
    if not node:
        warnings.append("未识别到标准节日节点；如果是自定义活动，请明确写出活动名称。")
    if not style_keywords:
        style_keywords = fallback_style_keywords(node)
    if not visual_elements:
        visual_elements = fallback_visual_elements(node)

    product_assets = list_product_assets(product["id"]) if product else []
    if product and not product_assets:
        warnings.append("已匹配到产品，但没有找到可用产品图素材，请先上传或补充产品图。")

    resolved_node = node or build_custom_node(activity_hint or raw_instruction)
    scene_prompt = build_scene_prompt(
        instruction=instruction,
        node=resolved_node,
        product=product,
        product_hint=product_hint,
        style_keywords=style_keywords,
        visual_elements=visual_elements,
    )
    custom_requirement = build_custom_requirement(
        raw_instruction=raw_instruction,
        style_keywords=style_keywords,
        visual_elements=visual_elements,
    )

    return {
        "raw_instruction": raw_instruction.strip(),
        "intent": {
            "product_hint": product_hint,
            "activity_hint": activity_hint,
            "node_hint": resolved_node.get("name", ""),
            "style_keywords": style_keywords,
            "visual_elements": visual_elements,
            "copy_direction": resolved_node.get("copy_direction", ""),
            "visual_direction": resolved_node.get("visual_direction", ""),
            "scene_prompt": scene_prompt,
            "custom_requirement": custom_requirement,
        },
        "matches": {
            "product": product,
            "product_assets": product_assets[:5],
            "node": resolved_node,
            "product_candidates": [
                {"product": item["product"], "score": item["score"], "reason": item["reason"]}
                for item in product_candidates[:5]
            ],
            "node_candidates": [
                {"node": item["node"], "score": item["score"], "reason": item["reason"]}
                for item in node_candidates[:5]
            ],
            "brand_assets": default_brand_assets(),
        },
        "confidence": {
            "product": score_to_confidence(product_candidates[0]["score"] if product_candidates else 0),
            "node": score_to_confidence(node_candidates[0]["score"] if node_candidates else 0),
            "overall": score_to_confidence(
                min(
                    product_candidates[0]["score"] if product_candidates else 0,
                    node_candidates[0]["score"] if node_candidates else 55,
                )
            ),
        },
        "requires": {
            "product_selection": product is None,
            "product_asset": not bool(product_assets),
        },
        "warnings": warnings,
    }


def normalize_instruction(value: str) -> str:
    return re.sub(r"\s+", "", value or "").strip()


def normalize_match_text(value: str) -> str:
    return re.sub(r"[\s\-_，。、“”‘’（）()【】\[\]:：/\\]+", "", value or "").casefold()


def find_terms(text: str, terms: list[str]) -> list[str]:
    normalized = normalize_match_text(text)
    found: list[str] = []
    for term in terms:
        if normalize_match_text(term) in normalized and term not in found:
            found.append(term)
    return found[:8]


def rank_products(raw_instruction: str, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = normalize_match_text(raw_instruction)
    ranked: list[dict[str, Any]] = []
    for product in products:
        score = 0
        reasons: list[str] = []
        for field, weight in (("name", 90), ("model", 80), ("id", 30), ("category", 15)):
            value = normalize_match_text(str(product.get(field, "")))
            if value and value in normalized:
                score += weight
                reasons.append(f"matched_{field}")
        for point in product.get("selling_points", []) or []:
            value = normalize_match_text(str(point))
            if value and value in normalized:
                score += 8
                reasons.append("matched_selling_point")
        model = str(product.get("model", "")).strip()
        if model and re.search(rf"(?<![A-Za-z0-9]){re.escape(model)}(?![A-Za-z0-9])", raw_instruction, re.I):
            score += 40
            reasons.append("exact_model_token")
        if score >= 25:
            ranked.append({"product": product, "score": min(score, 100), "reason": ",".join(sorted(set(reasons)))})
    return sorted(ranked, key=lambda item: item["score"], reverse=True)


def rank_nodes(raw_instruction: str) -> list[dict[str, Any]]:
    normalized = normalize_match_text(raw_instruction)
    ranked: list[dict[str, Any]] = []
    for node in MARKETING_NODES:
        score = 0
        reasons: list[str] = []
        node_id = node["id"]
        node_name = normalize_match_text(node["name"])
        if node_name and node_name in normalized:
            score += 90
            reasons.append("matched_node_name")
        for alias in NODE_ALIASES.get(node_id, []):
            if normalize_match_text(alias) in normalized:
                score += 36 if alias != node.get("name") else 60
                reasons.append(f"matched_alias:{alias}")
        for keyword in node.get("keywords", []):
            if normalize_match_text(keyword) in normalized:
                score += 15
                reasons.append(f"matched_keyword:{keyword}")
        if score:
            ranked.append({"node": node, "score": min(score, 100), "reason": ",".join(sorted(set(reasons)))})
    return sorted(ranked, key=lambda item: item["score"], reverse=True)


def extract_product_hint(raw_instruction: str, node_candidates: list[dict[str, Any]]) -> str:
    node_names = [item["node"]["name"] for item in node_candidates[:3]]
    node_names.extend(["宣传图", "海报", "节日", "活动", "中秋节", "中秋"])
    stop_pattern = "|".join(re.escape(name) for name in node_names if name)
    patterns = [
        r"(?:给|为)(?:朴道|PUDOW)?(.{1,28}?)(?:做|制作|生成|出|设计)(?:一张|一个)?(?:宣传图|海报)",
        r"(?:宣传|推广|推荐)(.{1,28}?)(?:产品|设备)?(?:，|。|,|$)",
        rf"(?:制作|做|生成|出|要|想要|我要)(?:一个|一张)?(.{{2,28}}?)(?:的)?(?:{stop_pattern})",
        rf"(.{{2,28}}?)(?:的)?(?:{stop_pattern})(?:宣传图|海报)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_instruction)
        if match:
            hint = cleanup_product_hint(match.group(1))
            if hint:
                return hint
    return ""


def extract_activity_hint(raw_instruction: str, node_candidates: list[dict[str, Any]]) -> str:
    if node_candidates:
        return str(node_candidates[0]["node"].get("name", ""))
    found = find_terms(raw_instruction, ACTIVITY_KEYWORDS)
    if found:
        return found[0]
    patterns = [
        r"(?:节日|节气|活动|主题|节点)[是为:：\s]*([\u4e00-\u9fa5A-Za-z0-9]{2,20})",
        r"([\u4e00-\u9fa5A-Za-z0-9]{2,20})(?:活动|开业|庆典|答谢日|发布会|周年庆)",
        r"(?:做|生成|制作|出)(?:一张|一个)?.{0,18}?([\u4e00-\u9fa5A-Za-z0-9]{2,20})(?:海报|宣传图)",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_instruction)
        if match:
            hint = cleanup_activity_hint(match.group(1))
            if hint:
                return hint
    return ""


def cleanup_activity_hint(value: str) -> str:
    cleaned = re.sub(r"(我要|我想要|制作|生成|做|一个|一张|宣传图|海报|给|为)", "", value or "")
    cleaned = cleaned.strip(" ，。、“”‘’的")
    return cleaned[:24]


def cleanup_product_hint(value: str) -> str:
    cleaned = re.sub(r"(我要|我想要|制作|生成|做|一个|一张|宣传图|海报|给|为|朴道|PUDOW)", "", value)
    for activity in ACTIVITY_KEYWORDS:
        cleaned = cleaned.replace(activity, "")
    cleaned = cleaned.strip(" ，。、“”‘’的")
    return cleaned[:40]


def list_product_assets(product_id: str) -> list[dict[str, Any]]:
    product_assets = list_assets(asset_type="product_image", source="product_material", product_id=product_id)
    if product_assets:
        return product_assets
    return list_assets(asset_type="product_image", product_id=product_id)


def default_brand_assets() -> dict[str, Any]:
    logos = list_assets(asset_type="logo", source="system")
    qrcodes = list_assets(asset_type="qrcode", source="system")
    bottom_bars = list_assets(asset_type="bottom_bar", source="system")
    return {
        "logo": logos[0] if logos else None,
        "qrcode": qrcodes[0] if qrcodes else None,
        "bottom_bar": bottom_bars[0] if bottom_bars else None,
    }


def fallback_style_keywords(node: dict[str, Any] | None) -> list[str]:
    if not node:
        return ["自然", "高级"]
    text = f"{node.get('visual_direction', '')} {' '.join(node.get('keywords', []))}"
    return find_terms(text, STYLE_KEYWORDS) or [str(node.get("visual_direction") or "品牌统一")]


def fallback_visual_elements(node: dict[str, Any] | None) -> list[str]:
    if not node:
        return []
    node_id = node.get("id", "")
    if node_id == "node_mid_autumn":
        return ["月亮", "月饼", "团圆"]
    if node_id == "node_spring_festival":
        return ["灯笼", "团圆饭"]
    if node_id == "node_lantern_festival":
        return ["灯笼", "汤圆"]
    return []


def build_custom_node(raw_instruction: str) -> dict[str, Any]:
    name = cleanup_activity_hint(raw_instruction) or "自定义活动"
    return {
        "id": "custom_one_click",
        "name": name[:20],
        "type": "custom",
        "keywords": [name[:20], "品牌宣传", "健康饮水"],
        "colors": ["#0F766E", "#DBEAFE"],
        "copy_direction": f"{name[:20]}主题传播、健康饮水",
        "visual_direction": f"{name[:20]}主题，结合品牌健康饮水传播",
    }


def build_scene_prompt(
    *,
    instruction: str,
    node: dict[str, Any],
    product: dict[str, Any] | None,
    product_hint: str,
    style_keywords: list[str],
    visual_elements: list[str],
) -> str:
    product_name = product.get("name") if product else product_hint or "指定产品"
    product_points = "、".join(product.get("selling_points", [])[:4]) if product else ""
    pieces = [
        f"整张竖版中文节日宣传海报，主题为{node.get('name', '营销活动')}。",
        f"真实参考产品是{product_name}，必须保持产品外观、比例、材质和结构可信。",
        f"整体风格：{'、'.join(style_keywords) if style_keywords else node.get('visual_direction', '')}。",
        f"画面元素：{'、'.join(visual_elements) if visual_elements else '结合活动氛围自然设计'}。",
        "产品、Logo、底部宣传条与场景色调、光影、透视和版式要统一，不能像后贴上去。",
        "二维码区域要保持高对比、清晰、可扫码，不要生成额外二维码或错误二维码。",
        "中文标题清晰可读，不要英文、乱码、水印、价格标签或夸大医疗功效表达。",
    ]
    if product_points:
        pieces.append(f"产品卖点只能参考：{product_points}，不要编造未经提供的数据或功效。")
    if instruction:
        pieces.append(f"用户原始要求：{instruction}")
    return "\n".join(pieces)


def build_custom_requirement(*, raw_instruction: str, style_keywords: list[str], visual_elements: list[str]) -> str:
    return "；".join(
        item
        for item in [
            f"用户指令：{raw_instruction.strip()}",
            f"风格关键词：{'、'.join(style_keywords)}" if style_keywords else "",
            f"画面元素：{'、'.join(visual_elements)}" if visual_elements else "",
            "一键整图融合模式，避免产品、Logo、底部条与背景割裂",
            "二维码采用保真融合策略，优先确保可扫码",
        ]
        if item
    )


def score_to_confidence(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 45:
        return "medium"
    if score > 0:
        return "low"
    return "missing"


def parse_one_click_intent(raw_instruction: str) -> dict[str, Any]:
    analyzed = analyze_one_click_intent(raw_instruction)
    matches = analyzed.get("matches", {})
    product = matches.get("product")
    product_assets = matches.get("product_assets") or []
    product_candidates = matches.get("product_candidates") or []

    if not product:
        product, product_assets, product_candidates = match_product_from_assets(raw_instruction, product_candidates)

    node = matches.get("node")
    node_candidates = matches.get("node_candidates") or []
    brand_assets = matches.get("brand_assets") or {}
    warnings = list(analyzed.get("warnings") or [])
    if product and not product_assets:
        warning = "\u5df2\u5339\u914d\u4ea7\u54c1\uff0c\u4f46\u6ca1\u6709\u627e\u5230\u53ef\u7528\u4e8e\u751f\u6210\u7684\u4ea7\u54c1\u56fe\u7d20\u6750\u3002"
        if warning not in warnings:
            warnings.append(warning)

    intent = {
        **(analyzed.get("intent") or {}),
        "node": public_node(node) if node else None,
        "product": public_product(product) if product else None,
    }
    candidates = {
        "nodes": [public_scored_node(item) for item in node_candidates[:5]],
        "products": [public_scored_product(item) for item in product_candidates[:5]],
        "assets": {
            "product_images": product_assets[:5],
            "logos": compact_asset_list(brand_assets.get("logo")),
            "qrcodes": compact_asset_list(brand_assets.get("qrcode")),
            "bottom_bars": compact_asset_list(brand_assets.get("bottom_bar")),
        },
    }
    confidence = analyzed.get("confidence") or {}
    result = {
        **analyzed,
        "intent": intent,
        "candidates": candidates,
        "confidence": {
            **confidence,
            "score": confidence_to_score(str(confidence.get("overall") or "")),
        },
        "requires": {
            **(analyzed.get("requires") or {}),
            "product_selection": product is None,
            "product_asset": not bool(product_assets),
        },
        "warnings": warnings,
    }
    result.update(evaluate_one_click_guardrails(raw_instruction, result))
    return result


def match_product_from_assets(
    raw_instruction: str,
    product_candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    normalized = normalize_match_text(raw_instruction)
    products_by_id = {product["id"]: product for product in list_products()}
    best: dict[str, Any] | None = None
    for asset in list_assets(asset_type="product_image"):
        product_id = str(asset.get("product_id") or "")
        product = products_by_id.get(product_id)
        if not product:
            continue
        terms = [
            product_id,
            str(product.get("name") or ""),
            str(product.get("model") or ""),
            str(asset.get("name") or ""),
            str(asset.get("file_name") or ""),
            " ".join(str(tag) for tag in asset.get("tags", []) or []),
        ]
        score = 0
        for term in terms:
            value = normalize_match_text(term)
            if value and value in normalized:
                score += 35 if term == product_id else 25
        model = str(product.get("model") or "").strip()
        if model and re.search(rf"(?<![A-Za-z0-9]){re.escape(model)}(?![A-Za-z0-9])", raw_instruction, re.I):
            score += 40
        if score and (not best or score > best["score"]):
            best = {"product": product, "asset": asset, "score": min(score, 100)}

    if not best:
        return None, [], product_candidates

    product = best["product"]
    product_assets = list_product_assets(product["id"])
    product_candidates = [
        {"product": product, "score": best["score"], "reason": "matched_product_asset"},
        *product_candidates,
    ]
    return product, product_assets, product_candidates


def public_product(product: dict[str, Any] | None) -> dict[str, Any] | None:
    if not product:
        return None
    return {
        "id": product.get("id", ""),
        "name": product.get("name", ""),
        "model": product.get("model", ""),
        "category": product.get("category", ""),
        "short_description": product.get("short_description", ""),
        "selling_points": product.get("selling_points", []),
        "source": product.get("source", ""),
    }


def public_node(node: dict[str, Any] | None) -> dict[str, Any] | None:
    if not node:
        return None
    return {
        "id": node.get("id", ""),
        "name": node.get("name", ""),
        "type": node.get("type", ""),
        "keywords": node.get("keywords", []),
        "colors": node.get("colors", []),
        "copy_direction": node.get("copy_direction", ""),
        "visual_direction": node.get("visual_direction", ""),
    }


def public_scored_product(item: dict[str, Any]) -> dict[str, Any]:
    return {
        **(public_product(item.get("product")) or {}),
        "score": item.get("score", 0),
        "reason": item.get("reason", ""),
    }


def public_scored_node(item: dict[str, Any]) -> dict[str, Any]:
    return {
        **(public_node(item.get("node")) or {}),
        "score": item.get("score", 0),
        "reason": item.get("reason", ""),
    }


def compact_asset_list(asset: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [asset] if asset else []


def confidence_to_score(value: str) -> float:
    return {
        "high": 0.9,
        "medium": 0.65,
        "low": 0.35,
        "missing": 0.0,
    }.get(value, 0.0)
