from __future__ import annotations

from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import API_PREFIX, CORS_ORIGINS, ROOT_DIR, STORAGE_DIR, ensure_storage_dirs
from app.db import init_db, list_products, mark_interrupted_tasks
from app.models import ComplianceCheckRequest, PosterTaskCreate, PosterTaskRerenderRequest
from app.responses import ApiError, api_error_handler, success_response, unhandled_error_handler
from app.services.assets import get_asset, list_assets, save_upload
from app.services.compliance import check_copy_with_rewrite
from app.services.poster import create_task, get_task, rerender_task
from app.services.seed import initialize_seed_assets
from config.seed_data import MARKETING_NODES


app = FastAPI(title="朴道节日/节气海报宣传AI员工 MVP", version="0.1.0")
app.add_exception_handler(ApiError, api_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):\d+$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ensure_storage_dirs()
app.mount("/storage", StaticFiles(directory=str(STORAGE_DIR)), name="storage")


@app.on_event("startup")
async def startup() -> None:
    init_db()
    initialize_seed_assets()
    mark_interrupted_tasks()


@app.get("/health")
async def health() -> dict[str, object]:
    return success_response({"status": "ok"})


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
    file: UploadFile = File(...),
    asset_type: str = Form(...),
    name: str | None = Form(default=None),
    tags: str | None = Form(default=None),
    product_id: str | None = Form(default=None),
) -> dict[str, object]:
    return success_response(await save_upload(file=file, asset_type=asset_type, name=name, tags=tags, product_id=product_id))


@app.post(f"{API_PREFIX}/poster-tasks")
async def post_poster_task(payload: PosterTaskCreate, background_tasks: BackgroundTasks) -> dict[str, object]:
    return success_response(create_task(payload, background_tasks))


@app.get(f"{API_PREFIX}/poster-tasks/{{task_id}}")
async def read_poster_task(task_id: str) -> dict[str, object]:
    return success_response(get_task(task_id))


@app.post(f"{API_PREFIX}/poster-tasks/{{task_id}}/rerender")
async def post_poster_task_rerender(task_id: str, payload: PosterTaskRerenderRequest) -> dict[str, object]:
    return success_response(rerender_task(task_id, payload))


@app.post(f"{API_PREFIX}/compliance/check")
async def compliance_check(payload: ComplianceCheckRequest) -> dict[str, object]:
    return success_response(check_copy_with_rewrite(payload.title, payload.subtitle))
