"""P2-5 - Cache ket qua Vision (OCR/trich xuat) theo HASH anh trang.

Muc tieu: giam chi phi goi GPT-5.4 Vision. Cung 1 anh trang (hash sha256
giong nhau) -> tra ket qua da luu, KHONG goi lai API. Huu ich khi:
  - Re-ingest / re-embed lai cung tai lieu.
  - Retry sau loi.
  - Cac trang/anh trung lap (vd trang bia, mau title-block giong nhau).

Cache la file JSON tren dia (ben vung qua cac lan chay). Key = sha256(anh)+schema.
Tat bang env VISION_CACHE_ENABLED=false. Thu muc qua VISION_CACHE_DIR.
"""
import os
import json
import hashlib

from mech_chatbot.config.logging import logger

# Tang khi doi prompt/schema Vision de tu dong vo hieu cache cu.
SCHEMA_VERSION = "v2"
_DEFAULT_DIR = os.path.join("data", "cache", "vision")


def _enabled():
    return os.getenv("VISION_CACHE_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")


def _cache_dir():
    d = os.getenv("VISION_CACHE_DIR", _DEFAULT_DIR)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def hash_image_bytes(image_bytes):
    if not image_bytes:
        return None
    h = hashlib.sha256()
    h.update(SCHEMA_VERSION.encode("utf-8"))
    h.update(image_bytes)
    return h.hexdigest()


def hash_image_file(path):
    try:
        with open(path, "rb") as f:
            return hash_image_bytes(f.read())
    except Exception as e:
        logger.warning(f"vision_cache: khong hash duoc anh {path}: {e}")
        return None


def _path(key):
    return os.path.join(_cache_dir(), f"{key}.json")


def get(key):
    """Tra ve vision_data (dict) da cache, hoac None."""
    if not key or not _enabled():
        return None
    p = _path(key)
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"vision_cache get loi: {e}")
    return None


def put(key, data):
    """Luu vision_data (dict) vao cache."""
    if not key or not _enabled() or data is None:
        return False
    try:
        with open(_path(key), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return True
    except Exception as e:
        logger.warning(f"vision_cache put loi: {e}")
        return False
