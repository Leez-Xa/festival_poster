from __future__ import annotations

import re
from typing import Any


GENERIC_PRODUCT_TERMS = {
    "产品",
    "产品图",
    "产品资料",
    "产品高清图",
    "高清图",
    "详情页",
    "详情图",
    "主图",
    "头图",
    "正视图",
    "侧视图",
    "系统产品素材",
    "素材库",
    "页面",
    "长图",
    "折页",
}


def normalize_match_text(value: str) -> str:
    return re.sub(r"[\s\-_，。、“”‘’（）()【】\[\]:：/\\.&＋+]+", "", value or "").casefold()


def expand_product_terms(*values: str) -> list[str]:
    expanded: list[str] = []
    for value in values:
        raw = str(value or "").strip()
        if not raw:
            continue
        for part in split_product_text(raw):
            add_product_term(expanded, part)
            cleaned = strip_generic_product_words(part)
            add_product_term(expanded, cleaned)
            for alias in split_combined_model_terms(cleaned):
                add_product_term(expanded, alias)
            for token in re.findall(r"[A-Za-z]+\d+[A-Za-z0-9-]*|\d+[A-Za-z]+[A-Za-z0-9-]*", part):
                add_product_term(expanded, token)
    return expanded


def split_product_text(value: str) -> list[str]:
    stem = re.sub(r"\.(?:jpg|jpeg|png|webp|pdf|psd|mp4)$", "", value.strip(), flags=re.I)
    parts = [
        part.strip(" ，。、“”‘’（）()【】[]")
        for part in re.split(r"[\\/|,，;；\n\r\t]+", stem)
        if part.strip(" ，。、“”‘’（）()【】[]")
    ]
    return parts or [stem]


def strip_generic_product_words(value: str) -> str:
    cleaned = value.strip()
    cleaned = re.sub(
        r"(?:产品资料|产品高清图|产品详情页&头图|产品详情页|详情页&头图|详情页|详情图|高清图|正视图|侧视图|主图|头图|产品图|产品|页面|长图|折页|看稿)",
        "",
        cleaned,
    )
    cleaned = re.sub(r"(?:白色|合集版|非轻创家|冰机|温机)$", "", cleaned)
    cleaned = cleaned.strip(" -_—（）()【】[]，。、“”‘’")
    return cleaned


def split_combined_model_terms(value: str) -> list[str]:
    normalized = normalize_match_text(value)
    aliases: list[str] = []
    patterns = [
        r"^(.+?)(\d+[a-z])(\d+[a-z])$",
        r"^(.+?)([a-z]\d+)([a-z]\d+)$",
        r"^(.+?)([a-z]\d+)([a-z]\d+[a-z]?)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, normalized, flags=re.I)
        if not match:
            continue
        prefix, first, second = match.groups()
        aliases.extend([f"{prefix}{first}", f"{prefix}{second}", first, second])
    return aliases


def add_product_term(target: list[str], value: str) -> None:
    clean = str(value or "").strip()
    normalized = normalize_match_text(clean)
    if not is_useful_product_term(normalized):
        return
    if all(normalize_match_text(item) != normalized for item in target):
        target.append(clean)


def is_useful_product_term(normalized: str) -> bool:
    if len(normalized) < 2:
        return False
    if normalized in {normalize_match_text(item) for item in GENERIC_PRODUCT_TERMS}:
        return False
    if normalized.isdigit():
        return False
    if re.fullmatch(r"(?:主图|头图|详情页)?\d+", normalized):
        return False
    return True


def product_match_terms(product: dict[str, Any]) -> list[tuple[str, int, str]]:
    terms: list[tuple[str, int, str]] = []
    field_weights = (
        ("name", 95),
        ("model", 90),
        ("material_dir", 75),
        ("category", 15),
    )
    for field, weight in field_weights:
        value = str(product.get(field) or "")
        if field == "material_dir":
            value = last_path_part(value)
        for index, term in enumerate(expand_product_terms(value)):
            term_weight = weight if index == 0 else max(35, weight - 25)
            reason = f"matched_{field}" if index == 0 else f"matched_{field}_alias"
            terms.append((term, term_weight, reason))
    for point in product.get("selling_points", []) or []:
        for term in expand_product_terms(str(point)):
            terms.append((term, 8, "matched_selling_point"))
    return dedupe_weighted_terms(terms)


def asset_match_terms(asset: dict[str, Any]) -> list[tuple[str, int, str]]:
    terms: list[tuple[str, int, str]] = []
    for field, weight in (("name", 70), ("file_name", 85), ("product_id", 35)):
        for index, term in enumerate(expand_product_terms(str(asset.get(field) or ""))):
            terms.append((term, weight if index == 0 else max(25, weight - 25), f"matched_asset_{field}"))
    for tag in asset.get("tags", []) or []:
        for term in expand_product_terms(str(tag)):
            terms.append((term, 45, "matched_asset_tag"))
    return dedupe_weighted_terms(terms)


def dedupe_weighted_terms(terms: list[tuple[str, int, str]]) -> list[tuple[str, int, str]]:
    best: dict[str, tuple[str, int, str]] = {}
    for term, weight, reason in terms:
        normalized = normalize_match_text(term)
        if not is_useful_product_term(normalized):
            continue
        existing = best.get(normalized)
        if existing is None or weight > existing[1]:
            best[normalized] = (term, weight, reason)
    return list(best.values())


def last_path_part(value: str) -> str:
    parts = [part for part in re.split(r"[\\/]+", value or "") if part]
    return parts[-1] if parts else value


def score_weighted_terms(raw_instruction: str, terms: list[tuple[str, int, str]]) -> tuple[int, list[str]]:
    normalized = normalize_match_text(raw_instruction)
    score = 0
    reasons: list[str] = []
    matched_norms: set[str] = set()
    for term, weight, reason in terms:
        value = normalize_match_text(term)
        if not value or value in matched_norms:
            continue
        if value in normalized:
            score += weight + min(len(value), 24)
            reasons.append(reason)
            matched_norms.add(value)
        elif len(value) >= 4 and normalized and normalized in value:
            score += max(20, weight // 3)
            reasons.append(f"partial_{reason}")
            matched_norms.add(value)
    return score, reasons


def exact_model_token_matched(raw_instruction: str, term: str) -> bool:
    clean = str(term or "").strip()
    if not clean:
        return False
    return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(clean)}(?![A-Za-z0-9])", raw_instruction, re.I))


def longest_matched_term_length(raw_instruction: str, terms: list[tuple[str, int, str]]) -> int:
    normalized = normalize_match_text(raw_instruction)
    longest = 0
    for term, _weight, _reason in terms:
        value = normalize_match_text(term)
        if value and value in normalized:
            longest = max(longest, len(value))
    return longest


def sort_product_assets_for_instruction(assets: list[dict[str, Any]], raw_instruction: str) -> list[dict[str, Any]]:
    if not raw_instruction or not assets:
        return assets

    scored: list[tuple[int, int, int, str, dict[str, Any]]] = []
    for asset in assets:
        terms = asset_match_terms(asset)
        score, _reasons = score_weighted_terms(raw_instruction, terms)
        longest_match = longest_matched_term_length(raw_instruction, terms)
        file_rank = product_asset_file_rank(asset)
        scored.append((score, longest_match, -file_rank, str(asset.get("name") or ""), asset))
    scored.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
    return [item[4] for item in scored]


def product_asset_file_rank(asset: dict[str, Any]) -> int:
    text = f"{asset.get('name', '')} {asset.get('file_name', '')} {' '.join(str(tag) for tag in asset.get('tags', []) or [])}"
    rank = 100
    for term in ("高清", "正视图", "侧视图", "主图", "头图", "产品"):
        if term in text:
            rank -= 10
    return rank
