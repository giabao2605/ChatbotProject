#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""empty_bag.py — Don sach \"chiec cap\" tri thuc (Knowledge Base) ve trang thai RONG.

Vi von: xoa het \"cuon vo\" (tai lieu da ingest + vector tren Qdrant) nhung GIU NGUYEN
\"chiec cap\" — tuc la bo khung he thong: schema, tai khoan, vai tro, phong ban,
site, phan quyen bao mat (clearance), tu dien vat tu. Sau khi chay, he thong san
sang nap lai tai lieu tu dau, khong mat cau hinh nguoi dung.

----------------------------------------------------------------------------------
NHOM DU LIEU
----------------------------------------------------------------------------------
[LUON XOA]  Tai lieu da ingest + dan xuat theo tai lieu (cac \"cuon vo\"):
    IngestionJobs, DocumentFamily, TaiLieu, TaiLieuKyThuat, BangKeVatTu,
    DocumentPages, TechnicalAttributes, DocumentAttributes, DocQualityScore
    + toan bo diem vector tren collection Qdrant.

[TUY CHON] (mac dinh GIU LAI, them co de xoa):
    --with-chat   -> LichSuChat (+ AnswerSource, FeedbackReview do FK cascade)
    --with-eval   -> GoldenAnswer, RegressionRun, RegressionQuestion
    --with-audit  -> AuditLog
    --with-files  -> xoa anh da tach trong data/processed (giu nguyen data/raw)

[KHONG BAO GIO XOA] (chinh la \"chiec cap\"):
    Users, Roles, UserRoles, UserDepartments, UserSecurityClearance,
    Departments, Sites, UserSites, MaterialDictionary, MaterialSynonym,
    _SchemaVersions.

----------------------------------------------------------------------------------
CACH DUNG
----------------------------------------------------------------------------------
    # Xem truoc se xoa bao nhieu (KHONG xoa that):
    python scripts/danger_ops/empty_bag.py --dry-run

    # Xoa that (co buoc go xac nhan):
    python scripts/danger_ops/empty_bag.py

    # Xoa khong hoi (CI / batch):
    python scripts/danger_ops/empty_bag.py --yes

    # Xoa sau hon:
    python scripts/danger_ops/empty_bag.py --with-chat --with-eval --with-audit --with-files

    # Xoa han collection Qdrant (thay vi chi xoa diem ben trong):
    python scripts/danger_ops/empty_bag.py --drop-collection
"""
import argparse
import os
import shutil
import sys

from dotenv import load_dotenv

# ----------------------------------------------------------------------------------
# Thiet lap duong dan de import duoc package mech_chatbot. Script nam o
# scripts/danger_ops/ -> goc du an la 3 cap tren, va package nam trong src/
# (giong cach cac launcher them 'src' vao sys.path).
# ----------------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_DIR = os.path.join(BASE_DIR, "src")
for _p in (SRC_DIR, BASE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

load_dotenv()

# ----------------------------------------------------------------------------------
# Danh sach bang, sap theo thu tu CON -> CHA de DELETE khong vuong khoa ngoai.
# (Du nhieu FK da co ON DELETE CASCADE, ta van xoa tuong minh cho chac chan va de
#  reseed IDENTITY ve 0.)
# ----------------------------------------------------------------------------------
CORE_DOC_TABLES = [
    "TechnicalAttributes",
    "DocumentPages",
    "BangKeVatTu",
    "DocQualityScore",
    "TaiLieuKyThuat",
    "DocumentAttributes",
    "TaiLieu",
    "DocumentFamily",
    "IngestionJobs",
]

CHAT_TABLES = [
    "AnswerSource",
    "FeedbackReview",
    "LichSuChat",
]

EVAL_TABLES = [
    "RegressionRun",
    "RegressionQuestion",
    "GoldenAnswer",
]

AUDIT_TABLES = [
    "AuditLog",
]

# Cac bang TUYET DOI khong dung toi (chi de in canh bao / tu kiem tra).
PROTECTED_TABLES = [
    "Users", "Roles", "UserRoles", "UserDepartments", "UserSecurityClearance",
    "Departments", "Sites", "UserSites", "MaterialDictionary", "MaterialSynonym",
    "_SchemaVersions",
]


def _hr():
    print("-" * 78)


def sql_count(conn, table):
    """Dem so dong; tra ve None neu bang khong ton tai (DB cu)."""
    from sqlalchemy import text
    try:
        return conn.execute(text(f"SELECT COUNT(*) FROM dbo.{table}")).scalar()
    except Exception:
        return None


def collect_tables(args):
    """Tra ve danh sach bang se xoa theo cac co tuy chon."""
    tables = list(CORE_DOC_TABLES)
    if args.with_chat:
        tables += CHAT_TABLES
    if args.with_eval:
        tables += EVAL_TABLES
    if args.with_audit:
        tables += AUDIT_TABLES
    return tables


def get_engine():
    from mech_chatbot.db.repository import engine
    if engine is None:
        raise RuntimeError(
            "SQLAlchemy Engine chua khoi tao duoc (kiem tra cau hinh DB / ODBC)."
        )
    return engine


def get_qdrant():
    """Tao client Qdrant + lay ten collection tu cau hinh (KHONG hardcode)."""
    from qdrant_client import QdrantClient, models  # noqa: F401
    from mech_chatbot.config.settings import QDRANT_COLLECTION

    qdrant_url = os.getenv("QDRANT_URL", "")
    qdrant_api_key = os.getenv("QDRANT_API_KEY", "")
    if not qdrant_url or not qdrant_api_key:
        raise ValueError("Thieu QDRANT_URL hoac QDRANT_API_KEY trong file .env")
    client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key, timeout=120)
    return client, QDRANT_COLLECTION


def qdrant_count(client, collection):
    try:
        if not client.collection_exists(collection):
            return None
        return client.count(collection_name=collection, exact=True).count
    except Exception as e:
        print(f"   [!] Khong dem duoc diem Qdrant: {e}")
        return None


# ----------------------------------------------------------------------------------
# DRY-RUN: chi in so luong, khong dong vao du lieu.
# ----------------------------------------------------------------------------------
def do_dry_run(args):
    tables = collect_tables(args)
    print("CHE DO XEM TRUOC (--dry-run): KHONG co gi bi xoa.\n")

    print("SQL Server — so dong se bi xoa:")
    total = 0
    try:
        engine = get_engine()
        with engine.connect() as conn:
            for t in tables:
                c = sql_count(conn, t)
                if c is None:
                    print(f"   - {t:<22} (bang khong ton tai, bo qua)")
                else:
                    total += c
                    print(f"   - {t:<22} {c:>10,} dong")
        print(f"   => Tong cong: {total:,} dong tren {len(tables)} bang.")
    except Exception as e:
        print(f"   [!] Loi truy van SQL: {e}")

    print("\nQdrant — so diem vector se bi xoa:")
    try:
        client, collection = get_qdrant()
        n = qdrant_count(client, collection)
        if n is None:
            print(f"   - Collection '{collection}' khong ton tai / khong dem duoc.")
        else:
            action = "xoa han collection" if args.drop_collection else "xoa toan bo diem"
            print(f"   - Collection '{collection}': {n:,} diem  ({action}).")
        client.close()
    except Exception as e:
        print(f"   [!] Loi Qdrant: {e}")

    if args.with_files:
        anh_dir = os.path.join(BASE_DIR, "data", "processed")
        n = len(os.listdir(anh_dir)) if os.path.isdir(anh_dir) else 0
        print(f"\nFile he thong: se don thu muc data/processed ({n} muc).")

    print("\nCac bang LUON DUOC GIU NGUYEN (chiec cap):")
    print("   " + ", ".join(PROTECTED_TABLES))


# ----------------------------------------------------------------------------------
# XOA THAT
# ----------------------------------------------------------------------------------
def wipe_sql(args):
    from sqlalchemy import text
    tables = collect_tables(args)
    engine = get_engine()
    print("1) Dang xoa du lieu SQL Server...")
    deleted = {}
    with engine.begin() as conn:
        for t in tables:
            try:
                c = sql_count(conn, t)
                if c is None:
                    print(f"   - {t:<22} (bang khong ton tai, bo qua)")
                    continue
                conn.execute(text(f"DELETE FROM dbo.{t}"))
                deleted[t] = c
                print(f"   - {t:<22} da xoa {c:,} dong")
            except Exception as e:
                print(f"   [!] Loi xoa {t}: {e}")
        # Reseed IDENTITY ve 0 (bang nao khong co IDENTITY se loi -> bo qua).
        for t in tables:
            try:
                conn.execute(text(f"DBCC CHECKIDENT ('dbo.{t}', RESEED, 0)"))
            except Exception:
                pass
    print(f"   => Xong SQL: {sum(deleted.values()):,} dong tren {len(deleted)} bang.")


def wipe_qdrant(args):
    from qdrant_client import models
    client, collection = get_qdrant()
    print("2) Dang xoa vector tren Qdrant...")
    try:
        if not client.collection_exists(collection):
            print(f"   - Collection '{collection}' chua ton tai, bo qua.")
            client.close()
            return
        before = qdrant_count(client, collection)
        if args.drop_collection:
            client.delete_collection(collection_name=collection)
            print(f"   - Da XOA HAN collection '{collection}' (truoc do {before:,} diem).")
            print("     Luu y: ung dung se tu tao lai collection khi khoi dong lan toi.")
            print("     Sau do nho chay: python scripts/create_qdrant_indexes.py")
        else:
            # Xoa TOAN BO diem bang filter rong (match-all) -> GIU collection +
            # payload index nguyen ven, dung lai duoc ngay.
            client.delete(
                collection_name=collection,
                points_selector=models.FilterSelector(filter=models.Filter()),
                wait=True,
            )
            after = qdrant_count(client, collection)
            print(f"   - Da xoa diem trong '{collection}': {before:,} -> {after:,}.")
    except Exception as e:
        print(f"   [!] Loi xoa Qdrant: {e}")
    finally:
        try:
            client.close()
        except Exception:
            pass


def wipe_files():
    print("3) Dang don thu muc anh da tach (data/processed)...")
    anh_dir = os.path.join(BASE_DIR, "data", "processed")
    try:
        if os.path.isdir(anh_dir):
            shutil.rmtree(anh_dir)
        os.makedirs(anh_dir, exist_ok=True)
        print(f"   - Da don sach: {anh_dir}")
    except Exception as e:
        print(f"   [!] Loi don thu muc: {e}")


def do_wipe(args):
    wipe_sql(args)
    wipe_qdrant(args)
    if args.with_files:
        wipe_files()
    print("\nHOAN TAT. Chiec cap gio da trong — san sang nap lai tai lieu tu dau.")


def build_parser():
    p = argparse.ArgumentParser(
        description="Don sach Knowledge Base ve trang thai rong (giu nguyen tai khoan & cau truc).",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Chi xem se xoa gi, KHONG xoa that.")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Bo qua buoc go xac nhan.")
    p.add_argument("--with-chat", action="store_true",
                   help="Xoa kem lich su chat (LichSuChat + AnswerSource + FeedbackReview).")
    p.add_argument("--with-eval", action="store_true",
                   help="Xoa kem du lieu eval (GoldenAnswer, RegressionRun/Question).")
    p.add_argument("--with-audit", action="store_true",
                   help="Xoa kem nhat ky kiem toan (AuditLog).")
    p.add_argument("--with-files", action="store_true",
                   help="Xoa kem anh da tach trong data/processed (giu nguyen data/raw).")
    p.add_argument("--drop-collection", action="store_true",
                   help="Xoa han collection Qdrant thay vi chi xoa diem ben trong.")
    return p


def main():
    args = build_parser().parse_args()

    _hr()
    print("EMPTY BAG — Don sach tri thuc ve trang thai rong")
    _hr()

    if args.dry_run:
        do_dry_run(args)
        return

    tables = collect_tables(args)
    print("SE XOA cac bang sau (+ vector Qdrant):")
    print("   " + ", ".join(tables))
    print("GIU NGUYEN: " + ", ".join(PROTECTED_TABLES))
    if not args.with_chat:
        print("   (lich su chat duoc giu — them --with-chat de xoa)")
    _hr()

    if not args.yes:
        print("CANH BAO: thao tac nay KHONG THE HOAN TAC.")
        confirm = input('Go chinh xac "XOA" de tiep tuc: ').strip()
        if confirm != "XOA":
            print("Da huy. Khong co gi bi xoa.")
            return

    do_wipe(args)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
