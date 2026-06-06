from __future__ import annotations

from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import (
    API_PREFIX,
    API_ACCESS_TOKEN,
    API_ACCESS_TOKEN_REQUIRED,
    CORS_ALLOW_ORIGIN_REGEX,
    CORS_ORIGINS,
    GENERATED_DIR,
    ROOT_DIR,
    STORAGE_DIR,
    SYSTEM_DIR,
    UPLOAD_DIR,
    ensure_storage_dirs,
)
from app.db import init_db, list_products, mark_interrupted_tasks
from app.models import ComplianceCheckRequest, PosterTaskCreate, PosterTaskRerenderRequest
from app.responses import ApiError, api_error_handler, success_response, unhandled_error_handler
from app.services.assets import get_asset, list_assets, save_upload
from app.services.compliance import check_copy_with_rewrite
from app.services.poster import create_task, get_task, get_task_composition, rerender_task
from app.services.seed import initialize_seed_assets
from config.seed_data import MARKETING_NODES


app = FastAPI(title="朴道节日/节气海报宣传AI员工 MVP", version="0.1.0")
app.add_exception_handler(ApiError, api_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=CORS_ALLOW_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ensure_storage_dirs()

PUBLIC_STORAGE_ROOTS = {
    "generated": GENERATED_DIR,
    "uploads": UPLOAD_DIR,
    "system": SYSTEM_DIR,
}
PUBLIC_STORAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
BLOCKED_STORAGE_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".wal", ".shm", ".log", ".pid", ".json"}


def require_api_access(
    authorization: str | None = Header(default=None),
    x_api_token: str | None = Header(default=None),
) -> None:
    if not API_ACCESS_TOKEN_REQUIRED:
        return
    bearer_prefix = "Bearer "
    bearer_token = (
        authorization[len(bearer_prefix) :].strip()
        if authorization and authorization.startswith(bearer_prefix)
        else ""
    )
    if x_api_token == API_ACCESS_TOKEN or bearer_token == API_ACCESS_TOKEN:
        return
    raise ApiError("UNAUTHORIZED", "API access token is required.", status_code=401)


@app.on_event("startup")
async def startup() -> None:
    init_db()
    initialize_seed_assets()
    mark_interrupted_tasks()


@app.get("/health")
async def health() -> dict[str, object]:
    return success_response({"status": "ok"})


@app.get("/storage/{storage_path:path}")
async def read_public_storage_file(storage_path: str) -> FileResponse:
    candidate = Path(storage_path)
    parts = candidate.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ApiError("FORBIDDEN", "Storage path is not allowed", status_code=403)

    root = PUBLIC_STORAGE_ROOTS.get(parts[0])
    if not root:
        raise ApiError("FORBIDDEN", "Storage path is not public", status_code=403)

    suffix = candidate.suffix.lower()
    if suffix in BLOCKED_STORAGE_SUFFIXES or suffix not in PUBLIC_STORAGE_SUFFIXES:
        raise ApiError("FORBIDDEN", "Storage file type is not public", status_code=403)

    file_path = (STORAGE_DIR / candidate).resolve()
    try:
        file_path.relative_to(root.resolve())
        file_path.relative_to(STORAGE_DIR.resolve())
    except ValueError as exc:
        raise ApiError("FORBIDDEN", "Storage path is not allowed", status_code=403) from exc

    if not file_path.exists() or not file_path.is_file():
        raise ApiError("NOT_FOUND", "Storage file not found", status_code=404)
    return FileResponse(file_path)


@app.get(f"{API_PREFIX}/marketing-nodes")
async def get_marketing_nodes() -> dict[str, object]:
    return success_response({"items": MARKETING_NODES})


@app.get(f"{API_PREFIX}/products")
async def get_products() -> dict[str, object]:
    return success_response({"items": list_products()})


@app.get(f"{API_PREFIX}/assets")
async def get_assets(
    asset_type: str | None = Query(default=None),
    source: str | None = Query(default=None),
    product_id: str | None = Query(default=None),
) -> dict[str, object]:
    return success_response({"items": list_assets(asset_type=asset_type, source=source, product_id=product_id)})


@app.get(f"{API_PREFIX}/assets/{{asset_id}}/file")
async def read_asset_file(asset_id: str) -> FileResponse:
    asset = get_asset(asset_id)
    if not asset:
        raise ApiError("NOT_FOUND", "素材不存在", status_code=404, details={"asset_id": asset_id})
    file_path = Path(asset["_file_path"]).resolve()
    try:
        file_path.relative_to(ROOT_DIR.resolve())
    except ValueError as exc:
        raise ApiError("FORBIDDEN", "素材路径不允许访问", status_code=403) from exc
    if not file_path.exists() or not file_path.is_file():
        raise ApiError("NOT_FOUND", "素材文件不存在", status_code=404, details={"asset_id": asset_id})
    return FileResponse(file_path, media_type=asset.get("mime_type") or "application/octet-stream")


@app.post(f"{API_PREFIX}/assets")
async def upload_asset(
    _: None = Depends(require_api_access),
    file: UploadFile = File(...),
    asset_type: str = Form(...),
    name: str | None = Form(default=None),
    tags: str | None = Form(default=None),
    product_id: str | None = Form(default=None),
) -> dict[str, object]:
    return success_response(await save_upload(file=file, asset_type=asset_type, name=name, tags=tags, product_id=product_id))


@app.post(f"{API_PREFIX}/poster-tasks")
async def post_poster_task(
    payload: PosterTaskCreate,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_api_access),
) -> dict[str, object]:
    return success_response(create_task(payload, background_tasks))


@app.get(f"{API_PREFIX}/poster-tasks/{{task_id}}")
async def read_poster_task(task_id: str, _: None = Depends(require_api_access)) -> dict[str, object]:
    return success_response(get_task(task_id))


@app.get(f"{API_PREFIX}/poster-tasks/{{task_id}}/composition")
async def read_poster_task_composition(task_id: str, _: None = Depends(require_api_access)) -> dict[str, object]:
    return success_response(get_task_composition(task_id))


@app.post(f"{API_PREFIX}/poster-tasks/{{task_id}}/rerender")
async def post_poster_task_rerender(
    task_id: str,
    payload: PosterTaskRerenderRequest,
    _: None = Depends(require_api_access),
) -> dict[str, object]:
    return success_response(rerender_task(task_id, payload))


@app.post(f"{API_PREFIX}/compliance/check")
async def compliance_check(payload: ComplianceCheckRequest, _: None = Depends(require_api_access)) -> dict[str, object]:
    return success_response(check_copy_with_rewrite(payload.title, payload.subtitle))
