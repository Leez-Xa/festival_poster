from __future__ import annotations

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
import base64

from PIL import Image, ImageDraw


ROOT = pathlib.Path(__file__).resolve().parents[1]
WINDOWS_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
LINUX_PYTHON = ROOT / ".venv" / "bin" / "python"
PYTHON = WINDOWS_PYTHON if WINDOWS_PYTHON.exists() else LINUX_PYTHON if LINUX_PYTHON.exists() else pathlib.Path(sys.executable)
HOST = os.getenv("DEMO_CHECK_HOST", "127.0.0.1")
PORT = int(os.getenv("DEMO_CHECK_PORT", "18085"))
BASE_URL = os.getenv("DEMO_CHECK_BASE_URL", f"http://{HOST}:{PORT}").rstrip("/")
AUTH_USER = os.getenv("DEMO_CHECK_AUTH_USER") or os.getenv("BASIC_AUTH_USER", "")
AUTH_PASS = os.getenv("DEMO_CHECK_AUTH_PASS") or os.getenv("BASIC_AUTH_PASS", "")
API_TOKEN = os.getenv("DEMO_CHECK_API_TOKEN", "")


def default_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if AUTH_USER and AUTH_PASS:
        token = base64.b64encode(f"{AUTH_USER}:{AUTH_PASS}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    if API_TOKEN:
        headers["X-API-Token"] = API_TOKEN
    return headers


def request(method: str, path: str, body: object | bytes | None = None, headers: dict[str, str] | None = None):
    data = None
    request_headers = {**default_headers(), **(headers or {})}
    if isinstance(body, bytes):
        data = body
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json; charset=utf-8")

    req = urllib.request.Request(BASE_URL + path, data=data, method=method, headers=request_headers)
    with urllib.request.urlopen(req, timeout=45) as response:
        raw = response.read()
        content_type = response.headers.get("Content-Type", "")
        if "json" in content_type:
            return response.status, json.loads(raw.decode("utf-8"))
        return response.status, raw


def multipart_upload(path: str, fields: dict[str, str], file_field: str, filename: str, content: bytes):
    boundary = "----festival-demo-" + uuid.uuid4().hex
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
    return request("POST", path, b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) != 0


def wait_ready(process: subprocess.Popen[str] | None) -> None:
    for _ in range(80):
        if process and process.poll() is not None:
            raise RuntimeError("uvicorn exited before health check")
        try:
            _, payload = request("GET", "/health")
            if payload.get("success") and payload.get("data", {}).get("status") == "ok":
                return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("health check did not become ready")


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


def choose(items: list[dict[str, object]], fallback_name: str) -> dict[str, object]:
    if not items:
        raise RuntimeError(f"missing {fallback_name}")
    return items[0]


def main() -> None:
    started_process: subprocess.Popen[str] | None = None
    if "DEMO_CHECK_BASE_URL" not in os.environ:
        if not port_available(HOST, PORT):
            raise RuntimeError(f"demo check port {HOST}:{PORT} is already in use")
        env = os.environ.copy()
        env.update(
            {
                "APP_ENV": "demo",
                "AI_REQUIRE_IMAGE_FUSION": "false",
                "API_ACCESS_TOKEN": "",
                "AI_BASE_URL": "your-ai-base-url",
                "GPT_TEXT_API_KEY": "your_text_key",
                "GPT_IMAGE_API_KEY": "your_image_key",
            }
        )
        started_process = subprocess.Popen(
            [str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", HOST, "--port", str(PORT), "--log-level", "warning"],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )

    try:
        wait_ready(started_process)
        _, app_config = request("GET", "/api/v1/app-config")
        _, nodes = request("GET", "/api/v1/marketing-nodes")
        _, products = request("GET", "/api/v1/products")
        _, logos = request("GET", "/api/v1/assets?asset_type=logo&source=system")
        _, bottom_bars = request("GET", "/api/v1/assets?asset_type=bottom_bar&source=system")

        node_items = nodes["data"]["items"]
        product_items = products["data"]["items"]
        spring = next((item for item in node_items if item.get("id") == "spring_festival"), node_items[0])
        product = choose(product_items, "product")
        logo = choose(logos["data"]["items"], "logo")
        bottom_bar = choose(bottom_bars["data"]["items"], "bottom bar")

        _, upload = multipart_upload(
            "/api/v1/assets",
            {"asset_type": "product_image", "name": "demo purifier", "tags": '["demo-check"]'},
            "file",
            "demo-purifier.png",
            make_demo_product_png(),
        )
        asset_id = upload["data"]["id"]

        _, created = request(
            "POST",
            "/api/v1/poster-tasks",
            {
                "node_id": spring["id"],
                "product_id": product["id"],
                "template_id": "template_v1_vertical_standard",
                "scene_asset_id": None,
                "product_asset_ids": [asset_id],
                "logo_asset_id": logo["id"],
                "qrcode_asset_id": None,
                "bottom_bar_asset_id": bottom_bar["id"],
                "contact_text": "",
                "scene_prompt": "春节团圆饭，净水机自然放在餐边柜旁，中文艺术字节日氛围。",
                "custom_requirement": "答辩演示稳定生成，突出流程化、标准化、可复现。",
                "copy_preference": {"mode": "ai", "title": "", "subtitle": ""},
            },
        )
        task_id = created["data"]["task_id"]

        task = None
        for _ in range(90):
            _, task_payload = request("GET", f"/api/v1/poster-tasks/{task_id}")
            task = task_payload["data"]
            if task["status"] in {"success", "failed"}:
                break
            time.sleep(0.5)

        if not task or task["status"] != "success":
            raise RuntimeError("poster task did not finish successfully: " + json.dumps(task, ensure_ascii=False))

        jpg_url = task["poster"]["jpg_url"]
        _, jpg_bytes = request("GET", jpg_url)
        if not isinstance(jpg_bytes, bytes) or len(jpg_bytes) < 1000:
            raise RuntimeError("generated JPG URL is not accessible")

        _, composition = request("GET", task["poster"]["composition_json_url"])
        fusion = composition["data"]["fusion"]
        if fusion["safe_zones"]["qrcode"] is not None:
            raise RuntimeError("qrcode safe zone should be null when qrcode_asset_id is null")

        try:
            request("GET", "/storage/festival_poster.sqlite3")
            raise RuntimeError("storage database was unexpectedly public")
        except urllib.error.HTTPError as exc:
            if exc.code not in {403, 404}:
                raise

        print(
            json.dumps(
                {
                    "status": "ok",
                    "base_url": BASE_URL,
                    "app_env": app_config["data"]["app_env"],
                    "demo_fallback_enabled": app_config["data"]["demo_fallback_enabled"],
                    "nodes": len(node_items),
                    "products": len(product_items),
                    "task_id": task_id,
                    "jpg_url": jpg_url,
                    "jpg_size_bytes": len(jpg_bytes),
                    "qrcode_extra_overlay": False,
                    "fusion_provider": fusion.get("provider"),
                    "fusion_mode": fusion.get("mode"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        if started_process:
            started_process.terminate()
            try:
                started_process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                started_process.kill()
                started_process.wait(timeout=6)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        sys.exit(1)
