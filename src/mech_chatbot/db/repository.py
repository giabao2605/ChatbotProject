"""P1.1: db/repository.py da duoc che thanh package db/repositories/.
File nay giu lai lam SHIM re-export de moi import cu van hoat dong:
  from mech_chatbot.db.repository import <bat_ky_ham_nao>
  from mech_chatbot.db import repository as repo; repo.<ham>()
Engine van duoc re-export tu db/engine.py (tuong thich nguoc tu P0).
"""
from dotenv import load_dotenv
from mech_chatbot.db.engine import (
    engine,
    _ensure_engine,
    SQL_SERVER,
    SQL_DATABASE,
    SQL_DRIVER,
    SQL_USERNAME,
    SQL_PASSWORD,
    SQL_TRUSTED_CONNECTION,
)

load_dotenv()

from mech_chatbot.db.repositories import *  # noqa: F401,F403 re-export toan bo
from mech_chatbot.db.repositories import __all__ as _repos_all

__all__ = list(_repos_all) + [
    'engine', '_ensure_engine', 'SQL_SERVER', 'SQL_DATABASE', 'SQL_DRIVER',
    'SQL_USERNAME', 'SQL_PASSWORD', 'SQL_TRUSTED_CONNECTION',
]
