"""Environment-backed settings used by the worker process adapter."""

from __future__ import annotations

import os


def worker_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


__all__ = ["worker_int"]
