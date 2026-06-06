from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse

LOGGER = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


def make_request_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")[:-3]
    return f"req_{stamp}_{uuid4().hex[:8]}"


def success_response(data: Any) -> dict[str, Any]:
    return {
        "success": True,
        "data": data,
        "error": None,
        "request_id": make_request_id(),
    }


def error_response(
    code: str,
    message: str,
    status_code: int = 400,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    request_id = request_id or make_request_id()
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "data": None,
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
            },
            "request_id": request_id,
        },
    )


async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return error_response(
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
        details=exc.details,
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = make_request_id()
    LOGGER.exception("Unhandled API error request_id=%s path=%s", request_id, request.url.path)
    return error_response(
        code="INTERNAL_ERROR",
        message="服务内部错误，请稍后重试",
        status_code=500,
        details={"request_id": request_id},
        request_id=request_id,
    )
