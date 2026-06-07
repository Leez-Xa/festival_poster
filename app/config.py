from __future__ import annotations

from pathlib import Path
import os


ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT_DIR / ".env"
STORAGE_DIR = ROOT_DIR / "storage"
UPLOAD_DIR = STORAGE_DIR / "uploads"
GENERATED_DIR = STORAGE_DIR / "generated"
SYSTEM_DIR = STORAGE_DIR / "system"
PROMPT_CACHE_DIR = STORAGE_DIR / "prompt_cache"
DB_PATH = STORAGE_DIR / "festival_poster.sqlite3"

API_PREFIX = "/api/v1"
MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_MIME_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
]


def load_env_file(path: Path = ENV_FILE) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#") or "=" not in clean:
            continue
        key, value = clean.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env_file()


class AISettings:
    def __init__(self) -> None:
        self.base_url = os.getenv("AI_BASE_URL", "").strip().rstrip("/")
        self.text_api_key = os.getenv("GPT_TEXT_API_KEY", "").strip()
        self.text_model = os.getenv("GPT_TEXT_MODEL", "gpt-mock").strip()
        self.scene_prompt_model = os.getenv("GPT_SCENE_PROMPT_MODEL", self.text_model).strip()
        self.image_api_key = os.getenv("GPT_IMAGE_API_KEY", "").strip()
        self.image_model = os.getenv("GPT_IMAGE_MODEL", "image2.0-mock").strip()
        self.image_generation_endpoint = os.getenv("AI_IMAGE_GENERATION_ENDPOINT", "/images/generations").strip()
        self.image_fusion_endpoint = os.getenv("AI_IMAGE_FUSION_ENDPOINT", "/images/edits").strip()
        self.image_fusion_request_mode = os.getenv("AI_IMAGE_FUSION_REQUEST_MODE", "multipart").strip().lower()
        self.image_file_field = os.getenv("AI_IMAGE_FILE_FIELD", "image[]").strip() or "image[]"
        self.image_output_size = os.getenv("AI_IMAGE_OUTPUT_SIZE", "1024x1792").strip() or "1024x1792"
        self.image_quality = os.getenv("AI_IMAGE_QUALITY", "auto").strip() or "auto"
        self.image_output_format = os.getenv("AI_IMAGE_OUTPUT_FORMAT", "png").strip() or "png"
        self.image_response_format = os.getenv("AI_IMAGE_RESPONSE_FORMAT", "b64_json").strip()
        self.text_timeout_seconds = _read_timeout(os.getenv("AI_TEXT_TIMEOUT_SECONDS", "30"), default=30)
        self.image_timeout_seconds = _read_timeout(
            os.getenv("AI_IMAGE_TIMEOUT_SECONDS", "120"),
            default=120,
            minimum=120,
            maximum=120,
        )
        self.require_image_fusion = _read_bool(os.getenv("AI_REQUIRE_IMAGE_FUSION", "true"), default=True)

    @property
    def has_text_credentials(self) -> bool:
        return bool(_is_real_base_url(self.base_url) and _is_real_secret(self.text_api_key))

    @property
    def has_image_credentials(self) -> bool:
        return bool(_is_real_base_url(self.base_url) and _is_real_secret(self.image_api_key))

    def safe_log_context(self) -> dict[str, object]:
        return {
            "base_url_configured": bool(self.base_url),
            "text_api_key_configured": bool(self.text_api_key),
            "image_api_key_configured": bool(self.image_api_key),
            "text_model": self.text_model,
            "scene_prompt_model": self.scene_prompt_model,
            "image_model": self.image_model,
            "image_generation_endpoint": self.image_generation_endpoint,
            "image_fusion_endpoint": self.image_fusion_endpoint,
            "image_fusion_request_mode": self.image_fusion_request_mode,
            "image_file_field": self.image_file_field,
            "image_output_size": self.image_output_size,
            "image_quality": self.image_quality,
            "image_output_format": self.image_output_format,
            "image_response_format": self.image_response_format,
            "text_timeout_seconds": self.text_timeout_seconds,
            "image_timeout_seconds": self.image_timeout_seconds,
            "require_image_fusion": self.require_image_fusion,
        }


def get_ai_settings() -> AISettings:
    return AISettings()


def _read_csv(value: str, default: list[str]) -> list[str]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or default


def _read_timeout(value: str, default: int, *, minimum: int = 1, maximum: int = 180) -> int:
    try:
        timeout = int(value)
    except ValueError:
        return default
    return max(minimum, min(timeout, maximum))


def _read_bool(value: str, default: bool) -> bool:
    clean = value.strip().lower()
    if clean in {"1", "true", "yes", "y", "on"}:
        return True
    if clean in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _is_real_secret(value: str) -> bool:
    if not value:
        return False
    lowered = value.lower()
    placeholders = ("your_", "replace_with", "填写", "你的", "<")
    return not any(marker in lowered for marker in placeholders)


def _is_real_base_url(value: str) -> bool:
    if not value:
        return False
    lowered = value.lower()
    placeholders = ("your-", "your_", "填写", "你的", "<")
    return not any(marker in lowered for marker in placeholders)


CORS_ORIGINS = _read_csv(os.getenv("CORS_ORIGINS", ""), DEFAULT_CORS_ORIGINS)
CORS_ALLOW_LOCALHOST = _read_bool(os.getenv("CORS_ALLOW_LOCALHOST", "true"), default=True)
CORS_ALLOW_ORIGIN_REGEX = (
    os.getenv("CORS_ALLOW_ORIGIN_REGEX", "").strip()
    or (r"^http://(localhost|127\.0\.0\.1):\d+$" if CORS_ALLOW_LOCALHOST else None)
)
API_ACCESS_TOKEN = os.getenv("API_ACCESS_TOKEN", "").strip()
API_ACCESS_TOKEN_REQUIRED = _is_real_secret(API_ACCESS_TOKEN)


def ensure_storage_dirs() -> None:
    for path in (STORAGE_DIR, UPLOAD_DIR, GENERATED_DIR, SYSTEM_DIR, PROMPT_CACHE_DIR):
        path.mkdir(parents=True, exist_ok=True)
