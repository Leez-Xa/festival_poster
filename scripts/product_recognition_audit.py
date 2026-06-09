from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import ROOT_DIR
from app.db import init_db, list_assets
from app.services.intent_parser import parse_one_click_intent
from app.services.seed import initialize_seed_assets, product_id_from_name, select_product_images


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def count_material_images(directory: Path) -> int:
    return sum(
        1
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES and not path.name.startswith("._")
    )


def audit_instruction(name: str, expected_product_id: str, instruction: str) -> dict[str, Any]:
    parsed = parse_one_click_intent(instruction)
    product = (parsed.get("intent") or {}).get("product") or {}
    product_assets = ((parsed.get("candidates") or {}).get("assets") or {}).get("product_images") or []
    matched_product_id = str(product.get("id") or "")
    first_asset = product_assets[0] if product_assets else {}
    problems: list[str] = []
    if matched_product_id != expected_product_id:
        problems.append("product_mismatch")
    if not product_assets:
        problems.append("missing_product_assets")
    return {
        "name": name,
        "instruction": instruction,
        "expected_product_id": expected_product_id,
        "matched_product_id": matched_product_id,
        "matched_product_name": product.get("name", ""),
        "asset_count": len(product_assets),
        "first_asset_file": first_asset.get("file_name", ""),
        "first_asset_tags": first_asset.get("tags", [])[:10],
        "confidence": parsed.get("confidence", {}),
        "warnings": parsed.get("warnings", []),
        "status": "passed" if not problems else "failed",
        "problems": problems,
    }


def audit_all_products() -> dict[str, Any]:
    init_db()
    initialize_seed_assets()

    product_root = ROOT_DIR / "素材" / "产品资料"
    if not product_root.exists():
        return {
            "status": "failed",
            "error": f"missing product material directory: {product_root}",
            "results": [],
        }

    results: list[dict[str, Any]] = []
    for directory in sorted((path for path in product_root.iterdir() if path.is_dir()), key=lambda item: item.name):
        expected_product_id = product_id_from_name(directory.name)
        result = audit_instruction(
            directory.name,
            expected_product_id,
            f"给{directory.name}做一张中秋海报",
        )
        result["material_image_count"] = count_material_images(directory)
        result["indexed_candidate_count"] = len(select_product_images(directory))
        result["db_product_asset_count"] = len(
            list_assets(asset_type="product_image", source="product_material", product_id=expected_product_id)
        )
        results.append(result)

    special_cases = [
        ("小白鲸2.0", product_id_from_name("小白鲸2.0"), "给小白鲸2.0做一张中秋海报"),
        ("爵士H5", product_id_from_name("爵士H5"), "给爵士H5做一张中秋海报"),
        ("小蓝鲸3F", product_id_from_name("小蓝鲸3F4F"), "给小蓝鲸3F做一张中秋海报"),
        ("小蓝鲸4F", product_id_from_name("小蓝鲸3F4F"), "给小蓝鲸4F做一张中秋海报"),
        ("小蓝鲸3F4F", product_id_from_name("小蓝鲸3F4F"), "给小蓝鲸3F4F做一张中秋海报"),
    ]
    special_results = []
    for name, expected_product_id, instruction in special_cases:
        result = audit_instruction(name, expected_product_id, instruction)
        first_file = str(result.get("first_asset_file") or "").casefold()
        if name == "小蓝鲸3F" and "3f" not in first_file:
            result["status"] = "failed"
            result["problems"].append("first_asset_not_3f")
        if name == "小蓝鲸4F" and "4f" not in first_file:
            result["status"] = "failed"
            result["problems"].append("first_asset_not_4f")
        special_results.append(result)

    failures = [item for item in [*results, *special_results] if item["status"] != "passed"]
    return {
        "status": "passed" if not failures else "failed",
        "product_root": str(product_root),
        "product_directory_count": len(results),
        "passed_count": len(results) - len([item for item in results if item["status"] != "passed"]),
        "failed_count": len([item for item in results if item["status"] != "passed"]),
        "special_passed_count": len(special_results) - len([item for item in special_results if item["status"] != "passed"]),
        "special_failed_count": len([item for item in special_results if item["status"] != "passed"]),
        "results": results,
        "special_results": special_results,
        "failures": failures,
    }


def main() -> int:
    report = audit_all_products()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
