"""Cache Vision results by image hash through explicit immutable config."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from mech_chatbot.config.logging import logger


SCHEMA_VERSION = "v2"
_DEFAULT_DIR = Path("data") / "cache" / "vision"


@dataclass(frozen=True, slots=True)
class VisionCacheConfig:
    enabled: bool = True
    directory: Path = _DEFAULT_DIR

    def __post_init__(self) -> None:
        object.__setattr__(self, "directory", Path(self.directory))


def _resolved_config(config: VisionCacheConfig | None) -> VisionCacheConfig:
    return config or VisionCacheConfig()


def _enabled(config: VisionCacheConfig | None = None) -> bool:
    return _resolved_config(config).enabled


def _cache_dir(config: VisionCacheConfig | None = None) -> Path:
    directory = _resolved_config(config).directory
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def hash_image_bytes(image_bytes):
    if not image_bytes:
        return None
    digest = hashlib.sha256()
    digest.update(SCHEMA_VERSION.encode("utf-8"))
    digest.update(image_bytes)
    return digest.hexdigest()


def hash_image_file(path):
    try:
        with open(path, "rb") as image_file:
            return hash_image_bytes(image_file.read())
    except Exception as error:
        logger.warning(f"vision_cache: khong hash duoc anh {path}: {error}")
        return None


def _path(key, config: VisionCacheConfig | None = None) -> Path:
    return _cache_dir(config) / f"{key}.json"


def get(key, *, config: VisionCacheConfig | None = None):
    """Return cached Vision data, or ``None`` when absent/disabled."""

    if not key or not _enabled(config):
        return None
    path = _path(key, config)
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as cache_file:
                return json.load(cache_file)
    except Exception as error:
        logger.warning(f"vision_cache get loi: {error}")
    return None


def put(key, data, *, config: VisionCacheConfig | None = None):
    """Persist Vision data using the explicitly selected cache directory."""

    if not key or not _enabled(config) or data is None:
        return False
    try:
        path = _path(key, config)
        with path.open("w", encoding="utf-8") as cache_file:
            json.dump(data, cache_file, ensure_ascii=False)
        return True
    except Exception as error:
        logger.warning(f"vision_cache put loi: {error}")
        return False


__all__ = [
    "SCHEMA_VERSION",
    "VisionCacheConfig",
    "get",
    "hash_image_bytes",
    "hash_image_file",
    "put",
]
