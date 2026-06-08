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
HOST = os.getenv("ONE_CLICK_GUARDRAILS_HOST", "127.0.0.1")
PORT = int(os.getenv("ONE_CLICK_GUARDRAILS_PORT", "18087"))
BASE_URL = os.getenv("ONE_CLICK_GUARDRAILS_BASE_URL", f"http://{HOST}:{PORT}").rstrip("/")
API_PREFIX = "/api/v1"
API_TOKEN = os.getenv("ONE_CLICK_GUARDRAILS_API_TOKEN", "")


@dataclass
class CheckResult:
    name: str
    status: str
    details: dict[str, Any] = field(default_factory=dict)


class GuardrailFailure(Exception):
    pass


def headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    auth = {"X-API-Token": API_TOKEN} if API_TOKEN else {}
    return {"X-Demo-User-Id": "demo_sales_user", **auth, **(extra or {})}


def request(method: str, path: str, body: object | bytes | None = None, extra_headers: dict[str, str] | None = None):
    data = None
    request_headers = headers(extra_headers)
    if isinstance(body, bytes):
        data = body
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json; charset=utf-8")
    req = urllib.request.Request(BASE_URL + path, data=data, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            raw = response.read()
            content_type = response.headers.get("Content-Type", "")
            payload = json.loads(raw.decode("utf-8")) if "json" in content_type else raw
            return response.status, payload
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = raw.decode("utf-8", errors="replace")
        return exc.code, payload


def expect_success(method: str, path: str, body: object | bytes | None = None, extra_headers: dict[str, str] | None = None):
    status, payload = request(method, path, body, extra_headers)
    if status >= 400 or not isinstance(payload, dict) or payload.get("success") is not True:
        raise GuardrailFailure(f"{method} {path} failed: HTTP {status} {json.dumps(payload, ensure_ascii=False)[:500]}")
    return payload["data"]


def expect_api_error(method: str, path: str, body: object, *, status_code: int, code: str) -> dict[str, Any]:
    status, payload = request(method, path, body)
    if status != status_code:
        raise GuardrailFailure(f"{method} {path} expected HTTP {status_code}, got {status}: {payload}")
    if not isinstance(payload, dict) or payload.get("success") is not False:
        raise GuardrailFailure(f"{method} {path} did not return ApiError payload")
    error = payload.get("error") or {}
    if error.get("code") != code:
        raise GuardrailFailure(f"{method} {path} expected {code}, got {error}")
    return error


def multipart_upload(path: str, fields: dict[str, str], file_field: str, filename: str, content: bytes):
    boundary = "----festival-guardrails-" + uuid.uuid4().hex
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
    return expect_success("POST", path, b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def make_demo_product_png() -> bytes:
    image = Image.new("RGBA", (420, 520), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((110, 45, 310, 485), radius=34, fill=(246, 250, 249, 255), outline=(10, 95, 91, 255), width=6)
    draw.rounded_rectangle((145, 110, 275, 230), radius=22, fill=(208, 233, 231, 255))
    draw.rounded_rectangle((160, 270, 260, 420), radius=18, fill=(226, 236, 236, 255))
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) != 0


def wait_ready(process: subprocess.Popen[str] | None) -> None:
    for _ in range(80):
        if process and process.poll() is not None:
            raise GuardrailFailure("uvicorn exited before health check")
        try:
            data = expect_success("GET", "/health")
            if data.get("status") == "ok":
                return
        except Exception:
            time.sleep(0.25)
    raise GuardrailFailure("health check did not become ready")


def start_server_if_needed() -> subprocess.Popen[str] | None:
    if "ONE_CLICK_GUARDRAILS_BASE_URL" in os.environ:
        return None
    if not port_available(HOST, PORT):
        raise GuardrailFailure(f"guardrails test port {HOST}:{PORT} is already in use")
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


def record(results: list[CheckResult], name: str, status: str, **details: Any) -> None:
    results.append(CheckResult(name=name, status=status, details=details))


def assert_intent_blocked(results: list[CheckResult], name: str, instruction: str, expected: str) -> None:
    data = expect_success("POST", f"{API_PREFIX}/one-click-intent", {"raw_instruction": instruction})
    reasons = "；".join(data.get("block_reasons") or [])
    if data.get("blocked") is not True or expected not in reasons:
        raise GuardrailFailure(f"{name} did not block as expected: {json.dumps(data, ensure_ascii=False)[:800]}")
    error = expect_api_error(
        "POST",
        f"{API_PREFIX}/one-click-poster-tasks",
        {"raw_instruction": instruction},
        status_code=422,
        code="ONE_CLICK_INPUT_BLOCKED",
    )
    record(results, name, "passed", blocked=True, api_error_code=error.get("code"), reasons=data.get("block_reasons"))


def run_checks(results: list[CheckResult]) -> None:
    expect_success("GET", "/health")
    products = expect_success("GET", f"{API_PREFIX}/products")["items"]
    logos = expect_success("GET", f"{API_PREFIX}/assets?asset_type=logo&source=system")["items"]
    bottom_bars = expect_success("GET", f"{API_PREFIX}/assets?asset_type=bottom_bar&source=system")["items"]
    if not products or not logos or not bottom_bars:
        raise GuardrailFailure("seed products or brand assets are missing")
    record(results, "seed_catalog", "passed", products=len(products), logos=len(logos), bottom_bars=len(bottom_bars))

    assert_intent_blocked(results, "profanity_blocked", "端午节给傻逼K2做海报", "不文明表达")
    assert_intent_blocked(results, "missing_activity_blocked", "给K2做一张海报，清爽高级", "缺少明确的节日")
    assert_intent_blocked(results, "unknown_product_blocked", "春节给火星净水器做海报", "产品库中没有匹配到")
    assert_intent_blocked(results, "competitor_blocked", "春节给美的净水器做海报", "竞品品牌")

    product = next((item for item in products if item.get("id") == "product_k2"), products[0])
    upload = multipart_upload(
        f"{API_PREFIX}/assets",
        {"asset_type": "product_image", "name": "guardrails product", "tags": '["guardrails"]', "product_id": product["id"]},
        "file",
        "guardrails-product.png",
        make_demo_product_png(),
    )
    instruction = "春节给K2做一张清爽高级的朋友圈竖版海报，突出健康饮水和扫码咨询。"
    intent = expect_success("POST", f"{API_PREFIX}/one-click-intent", {"raw_instruction": instruction})
    if intent.get("blocked"):
        raise GuardrailFailure(f"legal intent unexpectedly blocked: {json.dumps(intent, ensure_ascii=False)[:800]}")
    created = expect_success(
        "POST",
        f"{API_PREFIX}/one-click-poster-tasks",
        {
            "raw_instruction": instruction,
            "overrides": {
                "node_id": "node_spring_festival",
                "product_id": product["id"],
                "product_asset_ids": [upload["id"]],
                "logo_asset_id": logos[0]["id"],
                "bottom_bar_asset_id": bottom_bars[0]["id"],
            },
        },
    )
    if not created.get("task_id"):
        raise GuardrailFailure("legal one-click task response missing task_id")
    record(
        results,
        "legal_input_created_task",
        "passed",
        task_id=created["task_id"],
        product_id=product["id"],
        product_asset_id=upload["id"],
        blocked=intent.get("blocked"),
    )


def main() -> int:
    results: list[CheckResult] = []
    started_process = start_server_if_needed()
    try:
        wait_ready(started_process)
        run_checks(results)
    except Exception as exc:
        record(results, "guardrails_test", "failed", error=str(exc))
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
