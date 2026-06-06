from __future__ import annotations

from typing import Any

from app.services.ai_provider import AiProviderError, get_ai_provider
from config.seed_data import COMPLIANCE_RULES


def check_copy(title: str, subtitle: str) -> dict[str, Any]:
    text = f"{title or ''}\n{subtitle or ''}"
    issues: list[dict[str, Any]] = []
    for term in COMPLIANCE_RULES["blocked_terms"]:
        if term and term in text:
            issues.append(
                {
                    "term": term,
                    "risk_level": "high",
                    "message": f"文案命中高风险表达“{term}”",
                    "suggestion": COMPLIANCE_RULES["suggestions"].get(term, "请修改为可验证、非绝对化表达"),
                }
            )

    if issues:
        return {
            "status": "blocked",
            "risk_level": "high",
            "issues": issues,
            "suggested_title": _sanitize(title),
            "suggested_subtitle": _sanitize(subtitle),
        }

    return {
        "status": "passed",
        "risk_level": "low",
        "issues": [],
        "suggested_title": title,
        "suggested_subtitle": subtitle,
    }


def check_copy_with_rewrite(title: str, subtitle: str) -> dict[str, Any]:
    result = check_copy(title, subtitle)
    if result["status"] == "passed":
        return result

    try:
        suggested_title, suggested_subtitle = get_ai_provider().rewrite_copy(
            title=title,
            subtitle=subtitle,
            issues=result["issues"],
        )
        result["suggested_title"] = suggested_title
        result["suggested_subtitle"] = suggested_subtitle
        result["rewrite_provider"] = "ai_provider"
    except AiProviderError:
        result["rewrite_provider"] = "local_fallback"
    return result


def _sanitize(text: str) -> str:
    safe = text or ""
    for term in COMPLIANCE_RULES["blocked_terms"]:
        safe = safe.replace(term, "")
    return safe.strip()
