"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
from sqlalchemy import text
from ..engine import engine
from mech_chatbot.config.logging import logger

__all__ = [
    '_APP_SETTINGS_DEFAULTS',
    '_APP_SETTINGS_TTL',
    '_app_settings_cache',
    'get_all_app_settings',
    'get_app_setting',
    'get_app_setting_int',
    'set_app_setting',
]

# ============================================================================
# P1: Cau hinh ung dung (AppSettings) + metadata tong quat cho RAG
# ============================================================================
_APP_SETTINGS_DEFAULTS = {
    "expiry_warning_days": "30",
    "rag_general_top_k": "30",
}
_app_settings_cache = {"data": None, "ts": 0.0}
_APP_SETTINGS_TTL = 30  # giay


def get_all_app_settings(use_cache=True):
    """Doc toan bo cau hinh tu bang AppSettings (co cache ngan). Luon tra ve day du
    cac key mac dinh ke ca khi DB chua co dong tuong ung."""
    import time
    if use_cache and _app_settings_cache["data"] is not None and (time.time() - _app_settings_cache["ts"]) < _APP_SETTINGS_TTL:
        return dict(_app_settings_cache["data"])
    result = dict(_APP_SETTINGS_DEFAULTS)
    if engine is not None:
        try:
            with engine.connect() as conn:
                rows = conn.execute(text("SELECT SettingKey, SettingValue FROM dbo.AppSettings")).fetchall()
            for k, v in rows:
                if v is not None:
                    result[k] = v
        except Exception as e:
            logger.warning(f"get_all_app_settings loi: {e}")
    _app_settings_cache["data"] = dict(result)
    _app_settings_cache["ts"] = time.time()
    return result


def get_app_setting(key, default=None):
    val = get_all_app_settings().get(key)
    if val is None or val == "":
        if default is not None:
            return default
        return _APP_SETTINGS_DEFAULTS.get(key)
    return val


def get_app_setting_int(key, default=0):
    try:
        return int(str(get_app_setting(key, default)).strip())
    except Exception:
        return default


def set_app_setting(key, value, updated_by="System"):
    """Upsert mot cau hinh va xoa cache."""
    if engine is None:
        return False
    with engine.begin() as conn:
        conn.execute(text("""
            MERGE dbo.AppSettings AS tgt
            USING (SELECT :k AS SettingKey) AS src
            ON tgt.SettingKey = src.SettingKey
            WHEN MATCHED THEN
                UPDATE SET SettingValue = :v, UpdatedAt = GETDATE(), UpdatedBy = :by
            WHEN NOT MATCHED THEN
                INSERT (SettingKey, SettingValue, UpdatedAt, UpdatedBy)
                VALUES (:k, :v, GETDATE(), :by);
        """), {"k": key, "v": str(value), "by": updated_by})
    _app_settings_cache["data"] = None
    return True
