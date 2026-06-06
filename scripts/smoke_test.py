from __future__ import annotations

import json
import mimetypes
import os
import pathlib
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid

from PIL import Image, ImageDraw


ROOT = pathlib.Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
HOST = "127.0.0.1"
PORT = int(os.getenv("SMOKE_TEST_PORT", "18083"))
BASE_URL = f"http://{HOST}:{PORT}"


def request(method: str, path: str, body: object | bytes | None = None, headers: dict[str, str] | None = None):
    data = None
    request_headers = headers or {}
    if isinstance(body, bytes):
        data = body
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json; charset=utf-8")

    req = urllib.request.Request(BASE_URL + path, data=data, method=method, headers=request_headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
        content_type = response.headers.get("Content-Type", "")
        if "json" in content_type:
            return response.status, json.loads(raw.decode("utf-8"))
        return response.status, raw


def multipart_upload(path: str, fields: dict[str, str], file_field: str, file_path: pathlib.Path):
    boundary = "----codex" + uuid.uuid4().hex
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )

    mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
            f'filename="{file_path.name}"\r\nContent-Type: {mime_type}\r\n\r\n'
        ).encode("utf-8")
    )
    parts.append(file_path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return request("POST", path, b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def wait_until_ready(process: subprocess.Popen[str]) -> None:
    for _ in range(40):
        try:
            if process.poll() is not None:
                raise RuntimeError("uvicorn exited before health check")
            _, payload = request("GET", "/health")
            if payload.get("success") and payload.get("data", {}).get("status") == "ok":
                return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("health check did not become ready")


def assert_port_available(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        if sock.connect_ex((host, port)) == 0:
            raise RuntimeError(f"smoke test port {host}:{port} is already in use")

def create_smoke_product_png() -> pathlib.Path:
    target_dir = ROOT / "storage" / "tmp"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / "smoke_product.png"

    image = Image.new("RGBA", (900, 900), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((280, 120, 620, 760), radius=54, fill=(242, 247, 250, 255))
    draw.rounded_rectangle((320, 170, 580, 350), radius=34, fill=(197, 228, 244, 255))
    draw.rounded_rectangle((350, 400, 550, 690), radius=26, fill=(228, 233, 238, 255))
    draw.ellipse((410, 220, 490, 300), fill=(255, 255, 255, 220))
    draw.ellipse((505, 220, 545, 260), fill=(84, 190, 255, 255))
    draw.rounded_rectangle((300, 760, 600, 820), radius=22, fill=(128, 145, 160, 160))
    image.save(target_path, "PNG")
    return target_path


def main() -> None:
    assert_port_available(HOST, PORT)
    env = os.environ.copy()
    env["AI_BASE_URL"] = ""
    env["GPT_TEXT_API_KEY"] = ""
    env["GPT_IMAGE_API_KEY"] = ""
    env["GPT_TEXT_MODEL"] = "gpt-mock"
    env["GPT_IMAGE_MODEL"] = "image2.0-mock"
    env["AI_REQUIRE_IMAGE_FUSION"] = "false"
    env["API_ACCESS_TOKEN"] = ""

    process = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", HOST, "--port", str(PORT)],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    try:
        wait_until_ready(process)

        _, nodes = request("GET", "/api/v1/marketing-nodes")
        _, products = request("GET", "/api/v1/products")
        _, logos = request("GET", "/api/v1/assets?asset_type=logo&source=system")
        _, qrcodes = request("GET", "/api/v1/assets?asset_type=qrcode&source=system")
        _, bottom_bars = request("GET", "/api/v1/assets?asset_type=bottom_bar&source=system")
        _, compliance_ok = request(
            "POST",
            "/api/v1/compliance/check",
            {
                "title": "立冬已至",
                "subtitle": "暖心健康水，陪伴每一天",
                "product_id": "product_k2",
                "node_id": "node_lidong",
            },
        )
        _, compliance_bad = request(
            "POST",
            "/api/v1/compliance/check",
            {
                "title": "行业第一",
                "subtitle": "100%治愈饮水问题",
                "product_id": "product_k2",
                "node_id": "node_lidong",
            },
        )

        product_png = create_smoke_product_png()
        _, upload = multipart_upload(
            "/api/v1/assets",
            {"asset_type": "product_image", "name": "冒烟透明产品PNG", "tags": '["smoke"]'},
            "file",
            product_png,
        )
        asset_id = upload["data"]["id"]

        _, task_create = request(
            "POST",
            "/api/v1/poster-tasks",
            {
                "node_id": "node_lidong",
                "product_id": "product_k2",
                "template_id": "template_v1_vertical_standard",
                "scene_asset_id": None,
                "product_asset_ids": [asset_id],
                "logo_asset_id": logos["data"]["items"][0]["id"],
                "qrcode_asset_id": qrcodes["data"]["items"][0]["id"],
                "bottom_bar_asset_id": bottom_bars["data"]["items"][0]["id"],
                "contact_text": "扫码咨询当地销售顾问",
                "scene_prompt": "请让产品融入立冬暖色营销场景，产品明显可见并与背景和谐",
                "custom_requirement": "暖色，有高级感，适合朋友圈传播",
                "copy_preference": {
                    "mode": "ai",
                    "title": "立冬已至",
                    "subtitle": "暖心健康水，陪伴每一天",
                },
            },
        )
        task_id = task_create["data"]["task_id"]

        task = None
        for _ in range(60):
            _, task = request("GET", f"/api/v1/poster-tasks/{task_id}")
            if task["data"]["status"] in {"success", "failed"}:
                break
            time.sleep(0.5)

        if not task or task["data"]["status"] != "success":
            raise RuntimeError("poster task did not finish successfully: " + json.dumps(task, ensure_ascii=False))

        jpg_url = task["data"]["poster"]["jpg_url"]
        _, jpg_bytes = request("GET", jpg_url)
        if not isinstance(jpg_bytes, bytes) or len(jpg_bytes) < 1000:
            raise RuntimeError("generated JPG URL is not accessible")

        composition_url = task["data"]["poster"]["composition_json_url"]
        _, composition = request("GET", composition_url)
        if composition["data"]["task_id"] != task_id:
            raise RuntimeError("composition endpoint returned unexpected task_id")

        _, rerender = request(
            "POST",
            f"/api/v1/poster-tasks/{task_id}/rerender",
            {
                "title": "立冬暖意",
                "subtitle": "清润好水，陪伴温暖时刻",
            },
        )
        if rerender["data"]["status"] != "success" or not rerender["data"]["fusion"].get("rerender_reused_scene"):
            raise RuntimeError("rerender did not reuse cached scene")

        try:
            request("GET", "/storage/festival_poster.sqlite3")
            raise RuntimeError("storage database was unexpectedly public")
        except urllib.error.HTTPError as exc:
            if exc.code not in {403, 404}:
                raise

        print(
            json.dumps(
                {
                    "nodes": len(nodes["data"]["items"]),
                    "products": len(products["data"]["items"]),
                    "logos": len(logos["data"]["items"]),
                    "compliance_pass": compliance_ok["data"]["status"],
                    "compliance_blocked": compliance_bad["data"]["status"],
                    "upload_asset_id": asset_id,
                    "task_id": task_id,
                    "task_status": task["data"]["status"],
                    "copy_source": task["data"]["copy"]["source"],
                    "jpg_url": jpg_url,
                    "jpg_size_bytes": len(jpg_bytes),
                    "composition_api": composition_url,
                    "rerender_reused_scene": rerender["data"]["fusion"].get("rerender_reused_scene"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if process.stdout:
            logs = process.stdout.read()
            if logs:
                print("--- uvicorn logs ---")
                print(logs[-1200:])


if __name__ == "__main__":
    main()
