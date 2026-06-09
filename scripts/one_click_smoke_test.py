from __future__ import annotations

import base64
import io
import json
import mimetypes
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw


ROOT = pathlib.Path(__file__).resolve().parents[1]
WINDOWS_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
LINUX_PYTHON = ROOT / ".venv" / "bin" / "python"
PYTHON = WINDOWS_PYTHON if WINDOWS_PYTHON.exists() else LINUX_PYTHON if LINUX_PYTHON.exists() else pathlib.Path(sys.executable)
HOST = os.getenv("ONE_CLICK_SMOKE_HOST", "127.0.0.1")
PORT = int(os.getenv("ONE_CLICK_SMOKE_PORT", "18086"))
BASE_URL = os.getenv("ONE_CLICK_SMOKE_BASE_URL", f"http://{HOST}:{PORT}").rstrip("/")
API_PREFIX = "/api/v1"
AUTH_USER = os.getenv("ONE_CLICK_SMOKE_AUTH_USER") or os.getenv("BASIC_AUTH_USER", "")
AUTH_PASS = os.getenv("ONE_CLICK_SMOKE_AUTH_PASS") or os.getenv("BASIC_AUTH_PASS", "")
API_TOKEN = os.getenv("ONE_CLICK_SMOKE_API_TOKEN", "")


@dataclass
class CheckResult:
    name: str
    status: str
    details: dict[str, Any] = field(default_factory=dict)


class SmokeFailure(Exception):
    pass


class EndpointMissing(SmokeFailure):
    pass


def default_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if AUTH_USER and AUTH_PASS:
        token = base64.b64encode(f"{AUTH_USER}:{AUTH_PASS}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    if API_TOKEN:
        headers["X-API-Token"] = API_TOKEN
    return headers


def request(
    method: str,
    path: str,
    body: object | bytes | None = None,
    headers: dict[str, str] | None = None,
    *,
    timeout: int = 45,
):
    data = None
    request_headers = {**default_headers(), **(headers or {})}
    if isinstance(body, bytes):
        data = body
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json; charset=utf-8")

    req = urllib.request.Request(BASE_URL + path, data=data, method=method, headers=request_headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        content_type = response.headers.get("Content-Type", "")
        if "json" in content_type:
            return response.status, json.loads(raw.decode("utf-8"))
        return response.status, raw


def expect_json(
    method: str,
    path: str,
    body: object | bytes | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        _, payload = request(method, path, body, headers)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise EndpointMissing(f"{method} {path} returned 404") from exc
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = ""
        raise SmokeFailure(f"{method} {path} returned HTTP {exc.code}: {error_body[:500]}") from exc
    if not isinstance(payload, dict):
        raise SmokeFailure(f"{method} {path} did not return JSON")
    if payload.get("success") is not True:
        raise SmokeFailure(f"{method} {path} returned success=false: {json.dumps(payload, ensure_ascii=False)[:500]}")
    return payload["data"]


def multipart_upload(path: str, fields: dict[str, str], file_field: str, filename: str, content: bytes):
    boundary = "----festival-one-click-" + uuid.uuid4().hex
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
            f'filename="{filename}"\r\nContent-Type: {mime_type}\r\n\r\n'
        ).encode("utf-8")
    )
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return expect_json("POST", path, b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) != 0


def wait_ready(process: subprocess.Popen[str] | None) -> None:
    for _ in range(80):
        if process and process.poll() is not None:
            raise SmokeFailure("uvicorn exited before health check")
        try:
            data = expect_json("GET", "/health")
            if data.get("status") == "ok":
                return
        except Exception:
            time.sleep(0.25)
    raise SmokeFailure("health check did not become ready")


def make_demo_product_png() -> bytes:
    image = Image.new("RGBA", (640, 780), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((185, 75, 455, 700), radius=42, fill=(244, 249, 248, 255), outline=(18, 89, 83, 255), width=7)
    draw.rounded_rectangle((225, 145, 415, 305), radius=24, fill=(207, 233, 230, 255))
    draw.rounded_rectangle((245, 360, 395, 610), radius=22, fill=(226, 236, 236, 255))
    draw.ellipse((285, 190, 355, 260), fill=(255, 255, 255, 230))
    draw.text((252, 650), "PUDAO", fill=(18, 89, 83, 255))
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def choose(items: list[dict[str, Any]], label: str) -> dict[str, Any]:
    if not items:
        raise SmokeFailure(f"missing {label}")
    return items[0]


def record(results: list[CheckResult], name: str, status: str, **details: Any) -> None:
    results.append(CheckResult(name=name, status=status, details=details))


def run_common_checks(results: list[CheckResult]) -> dict[str, Any]:
    health = expect_json("GET", "/health")
    if health.get("status") != "ok":
        raise SmokeFailure("health status is not ok")
    record(results, "health", "passed", health_status=health.get("status"))

    app_config = expect_json("GET", f"{API_PREFIX}/app-config")
    nodes = expect_json("GET", f"{API_PREFIX}/marketing-nodes")["items"]
    products = expect_json("GET", f"{API_PREFIX}/products")["items"]
    product_materials = expect_json("GET", f"{API_PREFIX}/assets?asset_type=product_image&source=product_material")["items"]
    logos = expect_json("GET", f"{API_PREFIX}/assets?asset_type=logo&source=system")["items"]
    qrcodes = expect_json("GET", f"{API_PREFIX}/assets?asset_type=qrcode&source=system")["items"]
    bottom_bars = expect_json("GET", f"{API_PREFIX}/assets?asset_type=bottom_bar&source=system")["items"]

    if not nodes or not products or not logos or not bottom_bars:
        raise SmokeFailure("required seed data is incomplete")
    record(
        results,
        "catalog_assets",
        "passed",
        nodes=len(nodes),
        products=len(products),
        product_material_assets=len(product_materials),
        logos=len(logos),
        qrcodes=len(qrcodes),
        bottom_bars=len(bottom_bars),
        demo_fallback_enabled=app_config.get("demo_fallback_enabled"),
    )
    return {
        "nodes": nodes,
        "products": products,
        "product_materials": product_materials,
        "logos": logos,
        "qrcodes": qrcodes,
        "bottom_bars": bottom_bars,
    }


def poll_task(task_id: str, *, max_attempts: int = 90) -> dict[str, Any]:
    task = None
    for _ in range(max_attempts):
        task = expect_json("GET", f"{API_PREFIX}/poster-tasks/{task_id}")
        if task["status"] in {"success", "failed"}:
            break
        time.sleep(0.5)
    if not task:
        raise SmokeFailure("task polling returned no task")
    if task["status"] != "success":
        raise SmokeFailure("poster task did not finish successfully: " + json.dumps(task, ensure_ascii=False)[:1000])
    return task


def assert_jpg_accessible(jpg_url: str) -> int:
    _, jpg_bytes = request("GET", jpg_url, timeout=45)
    if not isinstance(jpg_bytes, bytes) or len(jpg_bytes) < 1000:
        raise SmokeFailure(f"JPG URL is not accessible or too small: {jpg_url}")
    return len(jpg_bytes)


def run_legacy_manual_flow(results: list[CheckResult], fixtures: dict[str, Any]) -> dict[str, Any]:
    upload = multipart_upload(
        f"{API_PREFIX}/assets",
        {"asset_type": "product_image", "name": "one click smoke legacy product", "tags": '["one-click-smoke","legacy"]'},
        "file",
        "one-click-smoke-product.png",
        make_demo_product_png(),
    )
    asset_id = upload["id"]

    node = next((item for item in fixtures["nodes"] if item.get("id") == "node_spring_festival"), fixtures["nodes"][0])
    product = choose(fixtures["products"], "product")
    logo = choose(fixtures["logos"], "logo")
    bottom_bar = choose(fixtures["bottom_bars"], "bottom bar")
    qrcode = fixtures["qrcodes"][0] if fixtures["qrcodes"] else None

    created = expect_json(
        "POST",
        f"{API_PREFIX}/poster-tasks",
        {
            "node_id": node["id"],
            "product_id": product["id"],
            "template_id": "template_v1_vertical_standard",
            "scene_asset_id": None,
            "product_asset_ids": [asset_id],
            "logo_asset_id": logo["id"],
            "qrcode_asset_id": qrcode["id"] if qrcode else None,
            "bottom_bar_asset_id": bottom_bar["id"],
            "contact_text": "scan to contact local sales",
            "scene_prompt": "Spring Festival family dinner scene, product naturally placed, warm and premium.",
            "custom_requirement": "legacy manual regression from one-click smoke test",
            "copy_preference": {"mode": "manual", "title": "Spring Water", "subtitle": "Warm clean water for every day"},
        },
    )
    task_id = created["task_id"]
    task = poll_task(task_id)
    jpg_size = assert_jpg_accessible(task["poster"]["jpg_url"])
    composition = expect_json("GET", task["poster"]["composition_json_url"])
    fusion = composition["fusion"]
    if qrcode and fusion["safe_zones"]["qrcode"] is None:
        raise SmokeFailure("legacy manual flow did not preserve qrcode safe zone")
    record(
        results,
        "legacy_manual_flow",
        "passed",
        task_id=task_id,
        jpg_url=task["poster"]["jpg_url"],
        jpg_size_bytes=jpg_size,
        qrcode_safe_zone=bool(fusion["safe_zones"]["qrcode"]),
    )
    return {"uploaded_product_asset_id": asset_id, "task": task}


def validate_intent_payload(intent: dict[str, Any]) -> None:
    for key in ("intent", "matches", "confidence", "requires", "warnings"):
        if key not in intent:
            raise SmokeFailure(f"one-click intent payload missing `{key}`")
    planning = intent["intent"]
    matches = intent["matches"]
    if not isinstance(planning, dict) or not isinstance(matches, dict):
        raise SmokeFailure("one-click intent planning fields must be objects")
    for key in ("scene_prompt", "custom_requirement", "style_keywords", "visual_elements"):
        if key not in planning:
            raise SmokeFailure(f"one-click intent planning missing `{key}`")
    if "brand_assets" not in matches:
        raise SmokeFailure("one-click intent matches missing `brand_assets`")


def run_one_click_flow(results: list[CheckResult], fixtures: dict[str, Any], legacy: dict[str, Any]) -> None:
    instruction = (
        "春节给名士K2饮水机做家庭年夜饭场景海报，活动是新年健康饮水焕新季，"
        "温暖喜庆但不要俗气，红金点缀，包含团圆餐桌、水汽、灯笼、产品和扫码了解优惠。"
    )
    intent = expect_json("POST", f"{API_PREFIX}/one-click-intent", {"raw_instruction": instruction})
    validate_intent_payload(intent)
    record(
        results,
        "one_click_intent",
        "passed",
        fields=sorted(intent.keys()),
        intent_fields=sorted(intent["intent"].keys()),
        product_confidence=intent["confidence"].get("product"),
        node_confidence=intent["confidence"].get("node"),
        requires=intent["requires"],
    )

    node = next((item for item in fixtures["nodes"] if item.get("id") == "node_spring_festival"), fixtures["nodes"][0])
    product = next((item for item in fixtures["products"] if item.get("id") == "product_k2"), fixtures["products"][0])
    overrides = {
        "node_id": node["id"],
        "product_id": product["id"],
        "product_asset_ids": [legacy["uploaded_product_asset_id"]],
        "logo_asset_id": choose(fixtures["logos"], "logo")["id"],
        "bottom_bar_asset_id": choose(fixtures["bottom_bars"], "bottom bar")["id"],
        "qrcode_asset_ids": [asset["id"] for asset in fixtures["qrcodes"][:2]],
    }
    created = expect_json(
        "POST",
        f"{API_PREFIX}/one-click-poster-tasks",
        {
            "raw_instruction": instruction,
            "overrides": overrides,
        },
    )
    task_id = created["task_id"]
    diagnostics = created.get("prompt_diagnostics") or {}
    selected = diagnostics.get("selected") or {}
    for field in ("positive_prompt", "negative_prompt", "final_image_prompt", "asset_roles", "qrcode_policy"):
        if field not in diagnostics:
            raise SmokeFailure(f"one-click prompt diagnostics missing `{field}`")
    if "bottom_bar" not in (diagnostics.get("asset_roles") or []):
        raise SmokeFailure("one-click prompt diagnostics did not include QR-removed bottom_bar as image model asset role")
    for snippet in (
        "去掉二维码后的底部宣传条会作为模型参考",
        "底部宣传条必须只融合在海报底部",
        "顶部禁止出现扫码关注、公众号、视频号、公司信息、二维码槽位或底部宣传条内容",
        "真实二维码会由本地 Pillow 贴回",
    ):
        if snippet not in (diagnostics.get("final_image_prompt") or ""):
            raise SmokeFailure(f"one-click prompt diagnostics missing bottom-bar guardrail: {snippet}")
    if selected.get("product_id") != product["id"]:
        raise SmokeFailure("one-click task did not use override product_id")
    if selected.get("node_id") != node["id"]:
        raise SmokeFailure("one-click task did not use override node_id")
    if legacy["uploaded_product_asset_id"] not in selected.get("product_asset_ids", []):
        raise SmokeFailure("one-click task did not use override product_asset_ids")
    task = poll_task(task_id)
    jpg_size = assert_jpg_accessible(task["poster"]["jpg_url"])
    composition = expect_json("GET", task["poster"]["composition_json_url"])
    fusion = composition["fusion"]
    asset_ids = composition["asset_ids"]
    if asset_ids.get("product_id") and asset_ids["product_id"] != product["id"]:
        raise SmokeFailure("one-click composition returned unexpected product_id")
    if legacy["uploaded_product_asset_id"] not in asset_ids.get("product_asset_ids", []):
        raise SmokeFailure("one-click composition did not include override product asset")
    if fusion.get("final_layout_engine") != "ai_scene_with_pillow_exact_qrcode_overlay":
        raise SmokeFailure("one-click flow did not use exact qrcode overlay mode")
    overlay = fusion.get("exact_qrcode_overlay") or {}
    if fixtures["qrcodes"] and not overlay.get("enabled"):
        raise SmokeFailure("one-click flow did not enable exact qrcode overlay")
    if "bottom_bar" not in (fusion.get("asset_roles") or []):
        raise SmokeFailure("runtime image fusion did not include QR-removed bottom_bar as image model asset role")
    if "bottom_bar" not in (fusion.get("brand_asset_roles") or []):
        raise SmokeFailure("runtime image fusion did not include bottom_bar in brand reference roles")
    assert_qrcode_placements_inside_bottom_bar(fusion)
    record(
        results,
        "one_click_poster_task",
        "passed",
        task_id=task_id,
        jpg_url=task["poster"]["jpg_url"],
        jpg_size_bytes=jpg_size,
        selected=selected,
        prompt_diagnostic_fields=sorted(diagnostics.keys()),
        qrcode_policy=diagnostics.get("qrcode_policy"),
        brand_asset_roles=fusion.get("brand_asset_roles"),
        final_layout_engine=fusion.get("final_layout_engine"),
    )


def assert_qrcode_placements_inside_bottom_bar(fusion: dict[str, Any]) -> None:
    safe_zones = fusion.get("safe_zones") or {}
    bottom_bar = safe_zones.get("bottom_bar")
    overlay = fusion.get("exact_qrcode_overlay") or {}
    placements = overlay.get("placements") or []
    if not bottom_bar or not placements:
        raise SmokeFailure("one-click qrcode overlay missing bottom bar or placement metadata")
    boxes = [placement.get("box") for placement in placements if placement.get("pasted")]
    if len(boxes) < 2:
        raise SmokeFailure(f"expected two pasted QR placements, got {len(boxes)}")
    for box in boxes:
        if not box_inside(box, bottom_bar):
            raise SmokeFailure(f"QR placement {box} is outside bottom bar {bottom_bar}")
    for index, first in enumerate(boxes):
        for second in boxes[index + 1 :]:
            if boxes_overlap(first, second):
                raise SmokeFailure(f"QR placements overlap: {boxes}")


def box_inside(inner: list[int], outer: list[int]) -> bool:
    return inner[0] >= outer[0] and inner[1] >= outer[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def boxes_overlap(a: list[int], b: list[int]) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def start_server_if_needed() -> subprocess.Popen[str] | None:
    if "ONE_CLICK_SMOKE_BASE_URL" in os.environ:
        return None
    if not port_available(HOST, PORT):
        raise SmokeFailure(f"one-click smoke test port {HOST}:{PORT} is already in use")
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "demo",
            "AI_REQUIRE_IMAGE_FUSION": "false",
            "API_ACCESS_TOKEN": "",
            "AI_BASE_URL": "your-ai-base-url",
            "GPT_TEXT_API_KEY": "your_text_key",
            "GPT_IMAGE_API_KEY": "your_image_key",
            "GPT_TEXT_MODEL": "gpt-mock",
            "GPT_IMAGE_MODEL": "image2.0-mock",
        }
    )
    return subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", HOST, "--port", str(PORT), "--log-level", "warning"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def main() -> int:
    results: list[CheckResult] = []
    started_process = start_server_if_needed()
    try:
        wait_ready(started_process)
        fixtures = run_common_checks(results)
        legacy = run_legacy_manual_flow(results, fixtures)
        record(results, "legacy_uploaded_asset", "passed", asset_id=legacy["uploaded_product_asset_id"])
        run_one_click_flow(results, fixtures, legacy)
    except EndpointMissing as exc:
        record(
            results,
            "one_click_endpoints",
            "failed",
            code="ENDPOINT_MISSING",
            error=str(exc),
            expected=[
                f"POST {API_PREFIX}/one-click-intent",
                f"POST {API_PREFIX}/one-click-poster-tasks",
                f"GET {API_PREFIX}/poster-tasks/{{task_id}}",
            ],
        )
    except Exception as exc:
        record(results, "smoke_test", "failed", error=str(exc))
    finally:
        if started_process:
            started_process.terminate()
            try:
                started_process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                started_process.kill()
                started_process.wait(timeout=6)

    output = {
        "status": "ok" if all(item.status == "passed" for item in results) else "failed",
        "base_url": BASE_URL,
        "results": [item.__dict__ for item in results],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
