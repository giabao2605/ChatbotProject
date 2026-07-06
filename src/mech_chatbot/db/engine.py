"""P0 refactor: tach SQLAlchemy engine + connection setup ra khoi repository.py.

Muc dich:
- `db/repository.py` chi con lo data-access (queries), viec khoi tao engine
  (connection string, connection pool) duoc tap trung tai day.
- De tai su dung (API / worker / test) va de mock trong unit test.
- Cac module cu van `import engine` / `_ensure_engine` tu `db.repository`
  (repository re-export lai tu day) nen KHONG pha vo import hien co.

KHONG import Streamlit / UI o tang nay (day la ha tang thuan).
"""
import os
import urllib.parse

from sqlalchemy import create_engine
from dotenv import load_dotenv

from mech_chatbot.config.logging import logger

load_dotenv()

SQL_SERVER = os.getenv("SQL_SERVER", r"localhost\SQLEXPRESS")
SQL_DATABASE = os.getenv("SQL_DATABASE", "Mech_Chatbot_DB")
SQL_DRIVER = os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server")
SQL_USERNAME = os.getenv("SQL_USERNAME")
SQL_PASSWORD = os.getenv("SQL_PASSWORD")
SQL_TRUSTED_CONNECTION = os.getenv("SQL_TRUSTED_CONNECTION", "yes").lower() in {"1", "true", "yes"}


def build_conn_str():
    """Dung chuoi ket noi ODBC. Uu tien SQL auth neu co username/password,
    nguoc lai dung Trusted Connection (Windows auth)."""
    if SQL_USERNAME and SQL_PASSWORD:
        return (
            f"DRIVER={SQL_DRIVER};"
            f"SERVER={SQL_SERVER};"
            f"DATABASE={SQL_DATABASE};"
            f"UID={SQL_USERNAME};"
            f"PWD={SQL_PASSWORD};"
            f"TrustServerCertificate=yes;"
        )
    return (
        f"DRIVER={SQL_DRIVER};"
        f"SERVER={SQL_SERVER};"
        f"DATABASE={SQL_DATABASE};"
        f"Trusted_Connection=yes;"
    )


def create_db_engine():
    """Khoi tao SQLAlchemy Engine. Tra ve None neu that bai (giu nguyen hanh vi cu:
    caller dung _ensure_engine() de bao loi ro rang thay vi NameError)."""
    params = urllib.parse.quote_plus(build_conn_str())
    try:
        eng = create_engine(
            f"mssql+pyodbc:///?odbc_connect={params}",
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        logger.info("Da khoi tao SQLAlchemy Engine thanh cong.")
        return eng
    except Exception as e:
        logger.error(f"Loi khoi tao SQLAlchemy Engine: {e}", exc_info=True)
        return None


# Engine dung chung toan ung dung (giu ten bien `engine` de tuong thich nguoc).
engine = create_db_engine()


def _ensure_engine():
    """Bao loi ro rang thay vi NameError khi engine khong khoi tao duoc."""
    if engine is None:
        raise RuntimeError(
            "SQLAlchemy Engine chua san sang. Kiem tra connection string / ODBC driver / SQL Server."
        )
