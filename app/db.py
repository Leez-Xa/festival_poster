from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.config import DB_PATH, ensure_storage_dirs
from app.utils import now_iso


SCHEMA_VERSION = "1"


def get_connection() -> sqlite3.Connection:
    ensure_storage_dirs()
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def init_db() -> None:
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS products (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              model TEXT NOT NULL DEFAULT '',
              category TEXT NOT NULL DEFAULT '',
              short_description TEXT NOT NULL DEFAULT '',
              selling_points_json TEXT NOT NULL DEFAULT '[]',
              source TEXT NOT NULL DEFAULT 'seed',
              material_dir TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS assets (
              id TEXT PRIMARY KEY,
              asset_type TEXT NOT NULL,
              name TEXT NOT NULL,
              file_name TEXT NOT NULL,
              public_url TEXT NOT NULL,
              mime_type TEXT NOT NULL,
              size_bytes INTEGER NOT NULL DEFAULT 0,
              width INTEGER,
              height INTEGER,
              tags_json TEXT NOT NULL DEFAULT '[]',
              source TEXT NOT NULL,
              product_id TEXT NOT NULL DEFAULT '',
              file_path TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_assets_type_source
              ON assets(asset_type, source);

            CREATE INDEX IF NOT EXISTS idx_assets_product
              ON assets(product_id);

            CREATE TABLE IF NOT EXISTS poster_tasks (
              task_id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              progress INTEGER NOT NULL DEFAULT 0,
              current_step TEXT NOT NULL,
              poster_json TEXT,
              error_json TEXT,
              request_json TEXT NOT NULL,
              resolved_asset_ids_json TEXT NOT NULL,
              copy_json TEXT,
              fusion_json TEXT,
              compliance_json TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_poster_tasks_status
              ON poster_tasks(status);
            """
        )
        connection.execute(
            """
            INSERT INTO schema_meta(key, value)
            VALUES('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (SCHEMA_VERSION,),
        )


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str | None, default: Any) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def upsert_product(product: dict[str, Any]) -> None:
    stamp = now_iso()
    created_at = product.get("created_at") or stamp
    updated_at = stamp
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO products(
              id, name, model, category, short_description, selling_points_json,
              source, material_dir, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name = excluded.name,
              model = excluded.model,
              category = excluded.category,
              short_description = excluded.short_description,
              selling_points_json = excluded.selling_points_json,
              source = excluded.source,
              material_dir = excluded.material_dir,
              updated_at = excluded.updated_at
            """,
            (
                product["id"],
                product.get("name", ""),
                product.get("model", ""),
                product.get("category", ""),
                product.get("short_description", ""),
                json_dumps(product.get("selling_points", [])),
                product.get("source", "seed"),
                product.get("material_dir", ""),
                created_at,
                updated_at,
            ),
        )


def list_products() -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute("SELECT * FROM products ORDER BY name COLLATE NOCASE").fetchall()
    return [row_to_product(row) for row in rows]


def get_product(product_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    return row_to_product(row) if row else None


def row_to_product(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "model": row["model"],
        "category": row["category"],
        "short_description": row["short_description"],
        "selling_points": json_loads(row["selling_points_json"], []),
        "source": row["source"],
        "material_dir": row["material_dir"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def upsert_asset(asset: dict[str, Any]) -> None:
    stamp = now_iso()
    created_at = asset.get("created_at") or stamp
    updated_at = stamp
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO assets(
              id, asset_type, name, file_name, public_url, mime_type, size_bytes,
              width, height, tags_json, source, product_id, file_path, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              asset_type = excluded.asset_type,
              name = excluded.name,
              file_name = excluded.file_name,
              public_url = excluded.public_url,
              mime_type = excluded.mime_type,
              size_bytes = excluded.size_bytes,
              width = excluded.width,
              height = excluded.height,
              tags_json = excluded.tags_json,
              source = excluded.source,
              product_id = excluded.product_id,
              file_path = excluded.file_path,
              updated_at = excluded.updated_at
            """,
            (
                asset["id"],
                asset["asset_type"],
                asset.get("name", ""),
                asset.get("file_name", ""),
                asset.get("public_url", ""),
                asset.get("mime_type", ""),
                int(asset.get("size_bytes") or 0),
                asset.get("width"),
                asset.get("height"),
                json_dumps(asset.get("tags", [])),
                asset.get("source", "user"),
                asset.get("product_id", ""),
                str(asset.get("_file_path") or asset.get("file_path") or ""),
                created_at,
                updated_at,
            ),
        )


def list_assets(
    *,
    asset_type: str | None = None,
    source: str | None = None,
    product_id: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[str] = []
    if asset_type:
        clauses.append("asset_type = ?")
        params.append(asset_type)
    if source:
        clauses.append("source = ?")
        params.append(source)
    if product_id:
        clauses.append("product_id = ?")
        params.append(product_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT * FROM assets {where} ORDER BY asset_type, name COLLATE NOCASE",
            params,
        ).fetchall()
    return [row_to_asset(row) for row in rows]


def get_asset(asset_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return row_to_asset(row) if row else None


def row_to_asset(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "asset_type": row["asset_type"],
        "name": row["name"],
        "file_name": row["file_name"],
        "public_url": row["public_url"],
        "mime_type": row["mime_type"],
        "size_bytes": row["size_bytes"],
        "width": row["width"],
        "height": row["height"],
        "tags": json_loads(row["tags_json"], []),
        "source": row["source"],
        "product_id": row["product_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "_file_path": row["file_path"],
    }


def upsert_task(task: dict[str, Any]) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO poster_tasks(
              task_id, status, progress, current_step, poster_json, error_json,
              request_json, resolved_asset_ids_json, copy_json, fusion_json,
              compliance_json, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
              status = excluded.status,
              progress = excluded.progress,
              current_step = excluded.current_step,
              poster_json = excluded.poster_json,
              error_json = excluded.error_json,
              request_json = excluded.request_json,
              resolved_asset_ids_json = excluded.resolved_asset_ids_json,
              copy_json = excluded.copy_json,
              fusion_json = excluded.fusion_json,
              compliance_json = excluded.compliance_json,
              updated_at = excluded.updated_at
            """,
            (
                task["task_id"],
                task["status"],
                int(task.get("progress") or 0),
                task.get("current_step", ""),
                json_dumps(task.get("poster")) if task.get("poster") is not None else None,
                json_dumps(task.get("error")) if task.get("error") is not None else None,
                json_dumps(task.get("request", {})),
                json_dumps(task.get("resolved_asset_ids", {})),
                json_dumps(task.get("copy")) if task.get("copy") is not None else None,
                json_dumps(task.get("fusion")) if task.get("fusion") is not None else None,
                json_dumps(task.get("compliance")) if task.get("compliance") is not None else None,
                task.get("created_at") or now_iso(),
                task.get("updated_at") or now_iso(),
            ),
        )


def get_task(task_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM poster_tasks WHERE task_id = ?", (task_id,)).fetchone()
    return row_to_task(row) if row else None


def update_task(task_id: str, **changes: Any) -> dict[str, Any]:
    task = get_task(task_id)
    if not task:
        raise KeyError(task_id)
    task.update(changes)
    task["updated_at"] = now_iso()
    upsert_task(task)
    return task


def row_to_task(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "task_id": row["task_id"],
        "status": row["status"],
        "progress": row["progress"],
        "current_step": row["current_step"],
        "poster": json_loads(row["poster_json"], None),
        "error": json_loads(row["error_json"], None),
        "request": json_loads(row["request_json"], {}),
        "resolved_asset_ids": json_loads(row["resolved_asset_ids_json"], {}),
        "copy": json_loads(row["copy_json"], None),
        "fusion": json_loads(row["fusion_json"], None),
        "compliance": json_loads(row["compliance_json"], None),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def mark_interrupted_tasks() -> None:
    stamp = now_iso()
    error = json_dumps(
        {
            "code": "GENERATION_INTERRUPTED",
            "message": "服务重启后任务已中断，请重新创建生成任务。",
            "details": {},
        }
    )
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE poster_tasks
            SET status = 'failed',
                progress = 100,
                current_step = '服务重启，任务中断',
                error_json = ?,
                updated_at = ?
            WHERE status IN ('pending', 'processing')
            """,
            (error, stamp),
        )
