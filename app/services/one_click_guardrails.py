from __future__ import annotations

import re
from typing import Any

from app.responses import ApiError
from config.seed_data import COMPLIANCE_RULES


PROFANITY_TERMS = [
    "傻逼",
    "傻比",
    "煞笔",
    "sb",
    "脑残",
    "弱智",
    "妈的",
    "操你",
    "草泥马",
]

COMPETITOR_TERMS = [
    "美的",
    "沁园",
    "安吉尔",
    "史密斯",
    "ao史密斯",
    "A.O.史密斯",
    "海尔",
    "小米净水器",
    "3M净水器",
    "飞利浦净水器",
    "碧云泉",
    "九阳净水器",
    "苏泊尔净水器",
]

EXTRA_MARKETING_RISK_TERMS = [
    "全网第一",
    "行业第一",
    "销量第一",
    "销量冠军",
    "遥遥领先",
    "包过",
    "稳赚",
    "零风险",
    "立刻见效",
    "药到病除",
    "改善百病",
    "延年益寿",
]


def evaluate_one_click_guardrails(raw_instruction: str, parsed: dict[str, Any]) -> dict[str, Any]:
    safety_issues = [
        *find_profanity_issues(raw_instruction),
        *find_marketing_compliance_issues(raw_instruction),
        *find_competitor_issues(raw_instruction),
    ]
    validation_issues = find_validation_issues(parsed)
    blocked = bool(safety_issues or validation_issues)
    return {
        "safety": {
            "status": "blocked" if safety_issues else "passed",
            "blocked": bool(safety_issues),
            "issues": safety_issues,
        },
        "validation": {
            "status": "blocked" if validation_issues else "passed",
            "blocked": bool(validation_issues),
            "missing_fields": [issue["field"] for issue in validation_issues if issue.get("type") == "missing"],
            "issues": validation_issues,
        },
        "blocked": blocked,
        "block_reasons": [issue["message"] for issue in [*safety_issues, *validation_issues]],
        "user_message": build_user_message(safety_issues, validation_issues),
    }


def ensure_one_click_not_blocked(parsed: dict[str, Any]) -> None:
    if not parsed.get("blocked"):
        return
    raise ApiError(
        "ONE_CLICK_INPUT_BLOCKED",
        parsed.get("user_message") or "一键生图指令未通过生成前检测，请修改后再提交。",
        status_code=422,
        details={
            "blocked": True,
            "block_reasons": parsed.get("block_reasons", []),
            "safety": parsed.get("safety", {}),
            "validation": parsed.get("validation", {}),
            "intent": parsed.get("intent", {}),
            "candidates": parsed.get("candidates", {}),
        },
    )


def find_profanity_issues(raw_instruction: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "blocked_term",
            "category": "civility",
            "severity": "high",
            "term": term,
            "message": f"生图指令包含辱骂或不文明表达“{term}”，不能进入生图流程。",
            "suggestion": "请删除辱骂、不文明或攻击性表达，改为正常的海报需求描述。",
        }
        for term in find_terms(raw_instruction, PROFANITY_TERMS)
    ]


def find_marketing_compliance_issues(raw_instruction: str) -> list[dict[str, Any]]:
    terms = [
        *(COMPLIANCE_RULES.get("blocked_terms") or []),
        *EXTRA_MARKETING_RISK_TERMS,
    ]
    suggestions = COMPLIANCE_RULES.get("suggestions") or {}
    issues: list[dict[str, Any]] = []
    for term in find_terms(raw_instruction, terms):
        issues.append(
            {
                "type": "blocked_term",
                "category": "marketing_compliance",
                "severity": "high",
                "term": term,
                "message": f"生图指令命中广告法或营销合规高风险表达“{term}”。",
                "suggestion": suggestions.get(term, "请改为真实、可验证、非绝对化的表达。"),
            }
        )
    return issues


def find_competitor_issues(raw_instruction: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "competitor_brand",
            "category": "competitor",
            "severity": "high",
            "term": term,
            "message": f"生图指令包含竞品品牌或竞品产品“{term}”，不能用于本品牌海报生成。",
            "suggestion": "请改为朴道品牌和产品库中的已有产品，或上传本品牌/本产品图片。",
        }
        for term in find_terms(raw_instruction, COMPETITOR_TERMS)
    ]


def find_validation_issues(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    intent = parsed.get("intent") or {}
    matches = parsed.get("matches") or {}
    node_candidates = matches.get("node_candidates") or []
    product_candidates = matches.get("product_candidates") or []
    product = intent.get("product")
    activity_hint = str(intent.get("activity_hint") or "").strip()
    product_hint = str(intent.get("product_hint") or "").strip()

    issues: list[dict[str, Any]] = []
    if not node_candidates and not has_explicit_activity_hint(activity_hint):
        issues.append(
            {
                "type": "missing",
                "category": "required_field",
                "field": "festival_activity",
                "severity": "high",
                "message": "缺少明确的节日、节气或活动主题。",
                "suggestion": "请补充节日/节气/活动，例如“春节”“端午节”“门店开业”“客户答谢日”。",
            }
        )

    if not product:
        issue_type = "unknown" if product_hint else "missing"
        issues.append(
            {
                "type": issue_type,
                "category": "required_field",
                "field": "product",
                "severity": "high",
                "message": (
                    f"产品库中没有匹配到“{product_hint}”。"
                    if product_hint
                    else "缺少明确的产品名称或型号。"
                ),
                "suggestion": "请选择产品库已有产品，例如 K2、H5、P2，或先上传/索引本产品图片后再生成。",
                "candidates": product_candidates[:5],
            }
        )
    return issues


def has_explicit_activity_hint(value: str) -> bool:
    hint = value.strip()
    if not hint:
        return False
    generic_values = {"自定义活动", "活动", "节日", "节气", "海报", "宣传图", "品牌宣传"}
    return hint not in generic_values


def build_user_message(safety_issues: list[dict[str, Any]], validation_issues: list[dict[str, Any]]) -> str:
    issues = [*safety_issues, *validation_issues]
    if not issues:
        return "一键生图指令已通过生成前检测。"
    first = issues[0]["message"]
    if len(issues) == 1:
        return f"一键生图指令已被阻断：{first}"
    return f"一键生图指令已被阻断：{first} 等 {len(issues)} 项问题。"


def find_terms(text: str, terms: list[str]) -> list[str]:
    normalized = normalize_text(text)
    found: list[str] = []
    for term in terms:
        if is_short_ascii_token(term):
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text or "", re.I):
                found.append(term)
            continue
        value = normalize_text(term)
        if value and value in normalized and term not in found:
            found.append(term)
    return found


def is_short_ascii_token(value: str) -> bool:
    return 1 <= len(value) <= 3 and bool(re.fullmatch(r"[A-Za-z0-9]+", value))


def normalize_text(value: str) -> str:
    return re.sub(r"[\s\-_，。、“”‘’（）()【】\[\]:：/\\.!！?？]+", "", value or "").casefold()
