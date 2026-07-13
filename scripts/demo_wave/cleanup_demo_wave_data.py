"""Delete only records created by the tagged Wave demo bootstrap."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from scripts.demo_wave.generate_demo_assets import DEFAULT_OUTPUT, DEMO_BATCH
from mech_chatbot.db.engine import _ensure_engine, engine
from mech_chatbot.db.repositories.document import delete_document_completely


def cleanup() -> dict:
    _ensure_engine()
    with engine.connect() as conn:
        doc_ids = [int(row[0]) for row in conn.execute(text(
            "SELECT DocID FROM dbo.TaiLieu WHERE SourceSystem=:batch ORDER BY DocID DESC"
        ), {"batch": DEMO_BATCH}).fetchall()]
    deleted_docs = sum(bool(delete_document_completely(doc_id, reviewer="demo-wave-cleanup")) for doc_id in doc_ids)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM dbo.DepartmentEvaluationGate WHERE Notes LIKE :tag"), {"tag": f"%{DEMO_BATCH}%"})
        conn.execute(text("""
            DELETE rr FROM dbo.RegressionRun rr
            JOIN dbo.RegressionQuestion rq ON rq.RegQID=rr.RegQID WHERE rq.DemoBatchID=:batch
        """), {"batch": DEMO_BATCH})
        deleted_cases = conn.execute(text("DELETE FROM dbo.RegressionQuestion WHERE DemoBatchID=:batch"), {"batch": DEMO_BATCH}).rowcount or 0
        conn.execute(text("""
            UPDATE dbo.DepartmentKnowledgeGovernance SET KnowledgeOwnerUserID=NULL, KnowledgeApproverUserID=NULL,
                UpdatedAt=GETDATE(), UpdatedBy=:batch
            WHERE UpdatedBy=:batch OR KnowledgeOwnerUserID IN (SELECT UserID FROM dbo.Users WHERE Username LIKE 'demo[_]%')
        """), {"batch": DEMO_BATCH})
        demo_user_ids = [int(row[0]) for row in conn.execute(text(
            "SELECT UserID FROM dbo.Users WHERE Username LIKE 'demo[_]%'"
        )).fetchall()]
        for user_id in demo_user_ids:
            for table in ("UserRoles", "UserDepartments", "UserSites", "UserSecurityClearance"):
                conn.execute(text(f"DELETE FROM dbo.{table} WHERE UserID=:user_id"), {"user_id": user_id})
            conn.execute(text("DELETE FROM dbo.Users WHERE UserID=:user_id"), {"user_id": user_id})
    return {"documents": deleted_docs, "evaluation_cases": int(deleted_cases), "users": len(demo_user_ids)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.confirm != DEMO_BATCH:
        parser.error(f"--confirm must equal {DEMO_BATCH}")
    print(cleanup())
    credentials = ROOT / ".local" / "demo-wave-credentials.json"
    credentials.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
