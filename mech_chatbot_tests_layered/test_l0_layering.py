"""L0 - Architecture guard tests (P0 refactor).

Muc dich: khoa lai cac ranh gioi tang ma P0 vua thiet lap, tranh bi pha ve sau.
Cac test nay CHI doc/parse source (AST) nen KHONG can DB / Qdrant / streamlit /
cac thu vien nang -> chay duoc o moi noi (ke ca CI toi gian).

Co the chay bang pytest HOAC truc tiep:  python3 test_l0_layering.py
"""
import ast
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_src(start):
    d = start
    for _ in range(12):
        cand = os.path.join(d, "src", "mech_chatbot")
        if os.path.isdir(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


SRC = os.environ.get("MECH_SRC")
SRC = os.path.join(SRC, "mech_chatbot") if SRC else _find_src(_HERE)
assert SRC and os.path.isdir(SRC), f"Khong tim thay src/mech_chatbot (SRC={SRC})"

# Cac tang LOI khong duoc phu thuoc Streamlit hay UI.
CORE_DIRS = ["db", "llm", "config", "rag", "ingestion", "api", "workers"]
# auth: chi core.py / security_policy.py / rate_limit.py la core; service.py la bien gioi UI.
CORE_AUTH_FILES = ["core.py", "security_policy.py", "rate_limit.py"]


def _iter_py(path):
    for root, _dirs, files in os.walk(path):
        if "__pycache__" in root:
            continue
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(root, fn)


def _imports(pyfile):
    """Tra ve tap cac module duoc import (chi xet lenh import, khong xet docstring/comment)."""
    with open(pyfile, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=pyfile)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module)
    return mods


def _core_files():
    files = []
    for d in CORE_DIRS:
        p = os.path.join(SRC, d)
        if os.path.isdir(p):
            files.extend(_iter_py(p))
    for fn in CORE_AUTH_FILES:
        p = os.path.join(SRC, "auth", fn)
        if os.path.isfile(p):
            files.append(p)
    return files


def test_core_layers_do_not_import_streamlit():
    offenders = []
    for f in _core_files():
        for m in _imports(f):
            if m == "streamlit" or m.startswith("streamlit."):
                offenders.append((os.path.relpath(f, SRC), m))
    assert not offenders, f"Tang loi khong duoc import streamlit: {offenders}"


def test_core_layers_do_not_import_ui():
    offenders = []
    for f in _core_files():
        for m in _imports(f):
            if m == "mech_chatbot.ui" or m.startswith("mech_chatbot.ui."):
                offenders.append((os.path.relpath(f, SRC), m))
    assert not offenders, f"Tang loi khong duoc import mech_chatbot.ui: {offenders}"


def test_auth_core_is_pure():
    mods = _imports(os.path.join(SRC, "auth", "core.py"))
    assert not any(m == "streamlit" or m.startswith("streamlit.") for m in mods)
    assert not any(m.startswith("mech_chatbot.ui") for m in mods)


def test_engine_extracted_and_reexported():
    # engine.py ton tai va dinh nghia `engine` + `_ensure_engine`
    eng = os.path.join(SRC, "db", "engine.py")
    assert os.path.isfile(eng), "Thieu db/engine.py"
    src = open(eng, encoding="utf-8").read()
    assert "engine = create_db_engine()" in src
    assert "def _ensure_engine" in src
    # repository.py re-export tu db.engine (tuong thich nguoc)
    repo = open(os.path.join(SRC, "db", "repository.py"), encoding="utf-8").read()
    assert "from mech_chatbot.db.engine import" in repo


def test_service_reexports_authenticate_user():
    svc = _imports(os.path.join(SRC, "auth", "service.py"))
    assert "mech_chatbot.auth.core" in svc, "service.py phai re-export authenticate_user tu core"


def test_ui_does_not_import_repository_directly():
    """P2.3: UI phai di qua tang service (mech_chatbot.services), KHONG import
    truc tiep tang truy cap du lieu (db.repository / db.repositories)."""
    ui_dir = os.path.join(SRC, "ui")
    offenders = []
    for f in _iter_py(ui_dir):
        for m in _imports(f):
            if m == "mech_chatbot.db.repository" or m.startswith(
                "mech_chatbot.db.repositories"
            ):
                offenders.append((os.path.relpath(f, SRC), m))
    assert not offenders, f"UI phai import qua mech_chatbot.services, khong dung db.repository: {offenders}"


def test_service_layer_is_pure():
    """P2.3: tang services (L6) khong duoc import streamlit hay mech_chatbot.ui."""
    svc_dir = os.path.join(SRC, "services")
    assert os.path.isdir(svc_dir), "Thieu goi mech_chatbot/services"
    offenders = []
    for f in _iter_py(svc_dir):
        for m in _imports(f):
            if (
                m == "streamlit"
                or m.startswith("streamlit.")
                or m == "mech_chatbot.ui"
                or m.startswith("mech_chatbot.ui.")
            ):
                offenders.append((os.path.relpath(f, SRC), m))
    assert not offenders, f"Tang services khong duoc import UI/streamlit: {offenders}"


def test_ui_has_no_raw_sql():
    """P2.4: UI khong duoc chua SQL tho / truy cap engine truc tiep.
    Moi truy van phai di qua tang service (mech_chatbot.services)."""
    ui_dir = os.path.join(SRC, "ui")
    offenders = []
    for f in _iter_py(ui_dir):
        rel = os.path.relpath(f, SRC)
        src = open(f, encoding="utf-8").read()
        tree = ast.parse(src, filename=f)
        # 1) Khong import sqlalchemy (text/Connection...) trong UI
        for m in _imports(f):
            if m == "sqlalchemy" or m.startswith("sqlalchemy."):
                offenders.append((rel, f"import {m}"))
        # 2) Khong import ten `engine` tu tang service
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("mech_chatbot.services"):
                for a in node.names:
                    if a.name == "engine":
                        offenders.append((rel, "from mech_chatbot.services import engine"))
        # 3) Khong goi engine.connect()/engine.begin() truc tiep trong UI
        if "engine.connect(" in src or "engine.begin(" in src:
            offenders.append((rel, "engine.connect/engine.begin"))
    assert not offenders, f"UI con SQL tho / truy cap engine truc tiep: {offenders}"


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
            passed += 1
    print(f"\n{passed} architecture-guard tests passed.")
