"""P1.1: db/repository.py da duoc che thanh package db/repositories/.
File nay giu lai lam SHIM re-export de moi import cu van hoat dong:
  from mech_chatbot.db.repository import <bat_ky_ham_nao>
  from mech_chatbot.db import repository as repo; repo.<ham>()
Engine compatibility name van duoc re-export tu db/engine.py. Phase 5 khong
con tao engine hay doc environment khi import facade nay.
"""
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

from mech_chatbot.db.repositories._shared import *  # noqa: F401,F403
from mech_chatbot.db.repositories.access import *  # noqa: F401,F403
from mech_chatbot.db.repositories.analytics import *  # noqa: F401,F403
from mech_chatbot.db.repositories.audit import *  # noqa: F401,F403
from mech_chatbot.db.repositories.bom import *  # noqa: F401,F403
from mech_chatbot.db.repositories.catalog import *  # noqa: F401,F403
from mech_chatbot.db.repositories.chat import *  # noqa: F401,F403
from mech_chatbot.db.repositories.community_summaries import *  # noqa: F401,F403
from mech_chatbot.db.repositories.doc_metadata import *  # noqa: F401,F403
from mech_chatbot.db.repositories.document import *  # noqa: F401,F403
from mech_chatbot.db.repositories.document_pages import *  # noqa: F401,F403
from mech_chatbot.db.repositories.external_ai import *  # noqa: F401,F403
from mech_chatbot.db.repositories.feedback import *  # noqa: F401,F403
from mech_chatbot.db.repositories.glossary import *  # noqa: F401,F403
from mech_chatbot.db.repositories.graph import *  # noqa: F401,F403
from mech_chatbot.db.repositories.jobs import *  # noqa: F401,F403
from mech_chatbot.db.repositories.knowledge_governance import *  # noqa: F401,F403
from mech_chatbot.db.repositories.lifecycle import *  # noqa: F401,F403
from mech_chatbot.db.repositories.material import *  # noqa: F401,F403
from mech_chatbot.db.repositories.publication import *  # noqa: F401,F403
from mech_chatbot.db.repositories.qdrant import *  # noqa: F401,F403
from mech_chatbot.db.repositories.rollout import *  # noqa: F401,F403
from mech_chatbot.db.repositories.semantic_cache import *  # noqa: F401,F403
from mech_chatbot.db.repositories.settings import *  # noqa: F401,F403
from mech_chatbot.db.repositories.version import *  # noqa: F401,F403
from mech_chatbot.db.repositories import _LEGACY_EXPORTS

__all__ = list(_LEGACY_EXPORTS) + [
    'engine', '_ensure_engine', 'SQL_SERVER', 'SQL_DATABASE', 'SQL_DRIVER',
    'SQL_USERNAME', 'SQL_PASSWORD', 'SQL_TRUSTED_CONNECTION',
]
