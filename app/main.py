from __future__ import annotations

from fastapi import BackgroundTasks, FastAPI, File, Form, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import API_PREFIX, CORS_ORIGINS, STORAGE_DIR, ensure_storage_dirs
from app.models import ComplianceCheckRequest, PosterTaskCreate
from app.responses import ApiError, api_error_handler, success_response, unhandled_error_handler
from app.services.assets import list_assets, save_upload
from app.services.compliance import check_copy_with_rewrite
from app.services.poster import create_task, get_task
from app.services.seed import initialize_seed_assets
from config.seed_data import MARKETING_NODES, PRODUCTS


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
    initialize_seed_assets()


@app.get("/health")
async def health() -> dict[str, object]:
    return success_response({"status": "ok"})


@app.get(f"{API_PREFIX}/marketing-nodes")
async def get_marketing_nodes() -> dict[str, object]:
    return success_response({"items": MARKETING_NODES})


@app.get(f"{API_PREFIX}/products")
async def get_products() -> dict[str, object]:
    return success_response({"items": PRODUCTS})


@app.get(f"{API_PREFIX}/assets")
async def get_assets(
    asset_type: str | None = Query(default=None),
    source: str | None = Query(default=None),
) -> dict[str, object]:
    return success_response({"items": list_assets(asset_type=asset_type, source=source)})


@app.post(f"{API_PREFIX}/assets")
async def upload_asset(
    file: UploadFile = File(...),
    asset_type: str = Form(...),
    name: str | None = Form(default=None),
    tags: str | None = Form(default=None),
) -> dict[str, object]:
    return success_response(await save_upload(file=file, asset_type=asset_type, name=name, tags=tags))


@app.post(f"{API_PREFIX}/poster-tasks")
async def post_poster_task(payload: PosterTaskCreate, background_tasks: BackgroundTasks) -> dict[str, object]:
    return success_response(create_task(payload, background_tasks))


@app.get(f"{API_PREFIX}/poster-tasks/{{task_id}}")
async def read_poster_task(task_id: str) -> dict[str, object]:
    return success_response(get_task(task_id))


@app.post(f"{API_PREFIX}/compliance/check")
async def compliance_check(payload: ComplianceCheckRequest) -> dict[str, object]:
    return success_response(check_copy_with_rewrite(payload.title, payload.subtitle))
