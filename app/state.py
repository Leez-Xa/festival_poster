from __future__ import annotations

from threading import Lock
from typing import Any


assets_lock = Lock()
tasks_lock = Lock()

ASSETS: dict[str, dict[str, Any]] = {}
TASKS: dict[str, dict[str, Any]] = {}
