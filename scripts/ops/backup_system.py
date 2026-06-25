r"""P1.8 — Backup tu dong: SQL Server (full + log) + snapshot Qdrant.

Chuc nang:
  - SQL: chay BACKUP DATABASE (full) va BACKUP LOG (neu recovery model = FULL).
  - Qdrant: tao snapshot cho collection (client.create_snapshot).
  - Ghi log ket qua + don backup cu hon --keep-days ngay.

LUU Y QUAN TRONG:
  - SQL Server ghi file backup len MAY CHU SQL (duong dan local cua dich vu SQL),
    KHONG phai may chay script. Mac dinh: thu muc DATA cua instance.
    Truyen --sql-dir de chi dinh (vd: D:\\Backups). Thu muc phai ton tai & SQL co quyen ghi.
  - Snapshot Qdrant nam tren server Qdrant (hoac Qdrant Cloud). Dung Qdrant API/Console de tai ve.

Cach chay (Windows PowerShell):
    $env:PYTHONPATH="src"; python scripts\ops\backup_system.py
    $env:PYTHONPATH="src"; python scripts\ops\backup_system.py --sql-dir "D:\\Backups" --keep-days 14
    $env:PYTHONPATH="src"; python scripts\ops\backup_system.py --skip-qdrant

Lich dinh ky: dung Windows Task Scheduler chay hang ngay (full + log nhieu lan/ngay).

--- KIEM THU KHOI PHUC (lam dinh ky, BAT BUOC truoc go-live) ---
  1) Khoi phuc SQL ra DB tam de kiem tra file backup doc duoc:
     RESTORE DATABASE Mech_Chatbot_DB_TEST
       FROM DISK = N'<duong_dan>\\Mech_Chatbot_DB_full_YYYYMMDD_HHMMSS.bak'
       WITH MOVE 'Mech_Chatbot_DB' TO N'D:\\Data\\Mech_Chatbot_DB_TEST.mdf',
            MOVE 'Mech_Chatbot_DB_log' TO N'D:\\Data\\Mech_Chatbot_DB_TEST_log.ldf',
            RECOVERY, REPLACE;
  2) Qdrant: tao collection moi tu snapshot (recover_snapshot) roi so sanh so points.
"""
from __future__ import annotations

import argparse
import os
import sys
import datetime as _dt

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from sqlalchemy import text  # noqa: E402

from mech_chatbot.db import repository as repo  # noqa: E402

COLLECTION = "TaiLieuKyThuat_v2"


def _timestamp():
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def backup_sql(sql_dir=None):
    """BACKUP DATABASE (full) + BACKUP LOG (neu FULL recovery). Tra ve list file da tao."""
    repo._ensure_engine()
    db = repo.SQL_DATABASE
    ts = _timestamp()
    created = []

    # Neu khong chi dinh thu muc -> hoi SQL Server thu muc backup mac dinh cua instance
    if not sql_dir:
        with repo.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT CAST(SERVERPROPERTY('InstanceDefaultBackupPath') AS NVARCHAR(4000))"
            )).fetchone()
            sql_dir = (row[0] if row and row[0] else None)
    if not sql_dir:
        raise RuntimeError("Khong xac dinh duoc thu muc backup. Truyen --sql-dir.")

    full_path = os.path.join(sql_dir, f"{db}_full_{ts}.bak")
    # autocommit: BACKUP khong chay trong transaction
    with repo.engine.connect() as conn:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(text(
            f"BACKUP DATABASE [{db}] TO DISK = :p WITH INIT, COMPRESSION, "
            f"NAME = :nm, STATS = 10"
        ), {"p": full_path, "nm": f"{db} full {ts}"})
        print(f"[SQL] full backup OK -> {full_path}")
        created.append(full_path)

        # Recovery model
        rm = conn.execute(text(
            "SELECT CAST(DATABASEPROPERTYEX(:db,'Recovery') AS NVARCHAR(20))"
        ), {"db": db}).scalar()
        if rm and str(rm).upper() == "FULL":
            log_path = os.path.join(sql_dir, f"{db}_log_{ts}.trn")
            conn.execute(text(
                f"BACKUP LOG [{db}] TO DISK = :p WITH INIT, NAME = :nm, STATS = 10"
            ), {"p": log_path, "nm": f"{db} log {ts}"})
            print(f"[SQL] log backup OK -> {log_path}")
            created.append(log_path)
        else:
            print(f"[SQL] Recovery model = {rm} -> bo qua BACKUP LOG (chi FULL moi can).")
    return created


def backup_qdrant():
    """Tao snapshot collection tren server Qdrant. Tra ve ten snapshot."""
    client = repo._get_qdrant_client()
    snap = client.create_snapshot(collection_name=COLLECTION, wait=True)
    name = getattr(snap, "name", None) or str(snap)
    print(f"[Qdrant] snapshot OK -> collection={COLLECTION} snapshot={name}")
    return name


def cleanup_old(sql_dir, keep_days):
    """Xoa file backup .bak/.trn cu hon keep_days (chay tren may co the truy cap sql_dir)."""
    if not sql_dir or not os.path.isdir(sql_dir):
        return
    cutoff = _dt.datetime.now() - _dt.timedelta(days=keep_days)
    for f in os.listdir(sql_dir):
        if not (f.endswith(".bak") or f.endswith(".trn")):
            continue
        fp = os.path.join(sql_dir, f)
        try:
            if _dt.datetime.fromtimestamp(os.path.getmtime(fp)) < cutoff:
                os.remove(fp)
                print(f"[cleanup] da xoa backup cu: {fp}")
        except Exception as e:
            print(f"[cleanup] bo qua {fp}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Backup SQL + Qdrant (P1.8)")
    parser.add_argument("--sql-dir", default=None, help="Thu muc backup tren MAY CHU SQL (vd D:\\Backups).")
    parser.add_argument("--skip-sql", action="store_true")
    parser.add_argument("--skip-qdrant", action="store_true")
    parser.add_argument("--keep-days", type=int, default=14, help="Don file backup cu hon N ngay (chi khi sql_dir truy cap duoc cuc bo).")
    args = parser.parse_args()

    print(f"=== Backup he thong @ {_timestamp()} ===")
    errors = []

    if not args.skip_sql:
        try:
            backup_sql(args.sql_dir)
            if args.sql_dir:
                cleanup_old(args.sql_dir, args.keep_days)
        except Exception as e:
            errors.append(f"SQL backup loi: {e}")
            print(f"[SQL] LOI: {e}")

    if not args.skip_qdrant:
        try:
            backup_qdrant()
        except Exception as e:
            errors.append(f"Qdrant snapshot loi: {e}")
            print(f"[Qdrant] LOI: {e}")

    if errors:
        print("\n=== KET THUC voi LOI ===")
        for e in errors:
            print(" - " + e)
        sys.exit(1)
    print("\n=== Backup hoan tat ===")


if __name__ == "__main__":
    main()
