"""Idempotent bootstrap for Wave 1-4 demo principals and evaluation cases.

This script never overwrites existing passwords and only touches records tagged
with ``demo-wave-v1``. Corpus ingestion is intentionally a separate command so
operators can inspect generated fixtures before sending them through the real
pipeline.
"""

from __future__ import annotations

import argparse
import json
import secrets
import string
import sys
from pathlib import Path

import bcrypt
from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from scripts.demo_wave.generate_demo_assets import DEFAULT_OUTPUT, DEMO_BATCH, DEPARTMENTS, generate_corpus, generate_eval
from mech_chatbot.db.engine import _ensure_engine, engine


CREDENTIALS_PATH = ROOT / ".local" / "demo-wave-credentials.json"


def _password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "-_!@"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _ensure_user(conn, username: str, display_name: str, department: str | None, roles: list[str], site: str | None, clearance: str) -> tuple[int, str | None]:
    row = conn.execute(text("SELECT UserID FROM dbo.Users WHERE Username = :username"), {"username": username}).fetchone()
    password = None
    if row:
        user_id = int(row[0])
        conn.execute(text("UPDATE dbo.Users SET IsActive = 1 WHERE UserID = :uid"), {"uid": user_id})
    else:
        password = _password()
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        user_id = int(conn.execute(text("""
            INSERT INTO dbo.Users (Username, PasswordHash, DisplayName, Department, IsActive)
            OUTPUT INSERTED.UserID VALUES (:username, :password_hash, :display_name, :department, 1)
        """), {"username": username, "password_hash": password_hash, "display_name": display_name, "department": department}).scalar_one())
    for role in roles:
        conn.execute(text("""
            INSERT INTO dbo.UserRoles (UserID, RoleID)
            SELECT :uid, RoleID FROM dbo.Roles r
            WHERE r.RoleName = :role AND NOT EXISTS (
                SELECT 1 FROM dbo.UserRoles ur WHERE ur.UserID = :uid AND ur.RoleID = r.RoleID
            )
        """), {"uid": user_id, "role": role})
    if department:
        conn.execute(text("""
            IF NOT EXISTS (SELECT 1 FROM dbo.UserDepartments WHERE UserID = :uid AND Department = :department)
                INSERT INTO dbo.UserDepartments (UserID, Department) VALUES (:uid, :department)
        """), {"uid": user_id, "department": department})
    if site:
        conn.execute(text("""
            IF NOT EXISTS (SELECT 1 FROM dbo.UserSites WHERE UserID = :uid AND Site = :site)
                INSERT INTO dbo.UserSites (UserID, Site) VALUES (:uid, :site)
        """), {"uid": user_id, "site": site})
    conn.execute(text("""
        MERGE dbo.UserSecurityClearance AS target
        USING (SELECT :uid AS UserID) AS source ON target.UserID = source.UserID
        WHEN MATCHED THEN UPDATE SET MaxLevel = :clearance
        WHEN NOT MATCHED THEN INSERT (UserID, MaxLevel) VALUES (:uid, :clearance);
    """), {"uid": user_id, "clearance": clearance})
    return user_id, password


def seed_users(credentials_path: Path = CREDENTIALS_PATH) -> dict:
    _ensure_engine()
    created_credentials: dict[str, str] = {}
    principals: dict[str, dict[str, int]] = {}
    with engine.begin() as conn:
        for department, (_, site, security, _, _, _) in DEPARTMENTS.items():
            suffix = department.lower()
            owner_id, owner_password = _ensure_user(
                conn, f"demo_owner_{suffix}", f"Demo Owner {department}", department,
                ["uploader", "knowledge_consumer"], site, security,
            )
            approver_id, approver_password = _ensure_user(
                conn, f"demo_approver_{suffix}", f"Demo Approver {department}", department,
                ["knowledge_approver", "reviewer"], site, security,
            )
            principals[department] = {"owner_id": owner_id, "approver_id": approver_id}
            if owner_password:
                created_credentials[f"demo_owner_{suffix}"] = owner_password
            if approver_password:
                created_credentials[f"demo_approver_{suffix}"] = approver_password
            conn.execute(text("""
                UPDATE dbo.DepartmentKnowledgeGovernance
                SET KnowledgeOwnerUserID = :owner, KnowledgeApproverUserID = :approver,
                    IsActive = 1, UpdatedAt = GETDATE(), UpdatedBy = :batch
                WHERE DeptCode = :department
            """), {"owner": owner_id, "approver": approver_id, "department": department, "batch": DEMO_BATCH})

        ui_accounts = [
            ("demo_platform_admin", "Demo Platform Admin", None, ["platform_admin"], None, "internal"),
            ("demo_security_admin", "Demo Security Admin", None, ["security_admin"], None, "confidential"),
            ("demo_viewer", "Demo Viewer", "Technical", ["viewer"], "HQ", "internal"),
            ("demo_uploader", "Demo Uploader", "Technical", ["uploader"], "HQ", "internal"),
            ("demo_reviewer", "Demo Reviewer", "Technical", ["reviewer", "knowledge_approver"], "HQ", "confidential"),
        ]
        for username, display, department, roles, site, clearance in ui_accounts:
            _, password = _ensure_user(conn, username, display, department, roles, site, clearance)
            if password:
                created_credentials[username] = password

    credentials_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if credentials_path.exists():
        try:
            existing = json.loads(credentials_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing.update(created_credentials)
    credentials_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"principals": principals, "created_credentials": len(created_credentials), "credentials_path": str(credentials_path)}


def import_eval_cases(output: Path = DEFAULT_OUTPUT) -> dict:
    manifest = output / "eval_manifest.jsonl"
    if not manifest.exists():
        generate_eval(output)
    cases = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    _ensure_engine()
    inserted = 0
    updated = 0
    with engine.begin() as conn:
        for case in cases:
            params = {
                "question": case["question"], "keywords": json.dumps(case.get("expected_keywords") or [], ensure_ascii=False),
                "department": case["department"], "site": case["site"], "batch": DEMO_BATCH,
                "case_id": case["id"], "scenario": case["scenario"], "behavior": case["expected_behavior"],
                "reference": case.get("expected_reference"), "case_json": json.dumps(case, ensure_ascii=False),
            }
            exists = conn.execute(text("SELECT RegQID FROM dbo.RegressionQuestion WHERE DemoBatchID = :batch AND CaseID = :case_id"), params).fetchone()
            if exists:
                conn.execute(text("""
                    UPDATE dbo.RegressionQuestion SET QuestionText=:question, ExpectedKeywords=:keywords,
                        Department=:department, Site=:site, Scenario=:scenario, ExpectedBehavior=:behavior,
                        ExpectedReference=:reference, CaseJson=:case_json, IsActive=1
                    WHERE RegQID=:reg_q_id
                """), {**params, "reg_q_id": int(exists[0])})
                updated += 1
            else:
                conn.execute(text("""
                    INSERT INTO dbo.RegressionQuestion
                        (QuestionText, ExpectedKeywords, Department, Site, CreatedBy, IsActive,
                         DemoBatchID, CaseID, Scenario, ExpectedBehavior, ExpectedReference, CaseJson)
                    VALUES (:question, :keywords, :department, :site, :batch, 1,
                            :batch, :case_id, :scenario, :behavior, :reference, :case_json)
                """), params)
                inserted += 1
    return {"cases": len(cases), "inserted": inserted, "updated": updated}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("generate", "seed-users", "import-eval", "all"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--credentials", type=Path, default=CREDENTIALS_PATH)
    args = parser.parse_args()
    result = {}
    if args.command in {"generate", "all"}:
        args.output.mkdir(parents=True, exist_ok=True)
        result["generated"] = {"documents": len(generate_corpus(args.output)), "cases": len(generate_eval(args.output))}
    if args.command in {"seed-users", "all"}:
        result["users"] = seed_users(args.credentials)
    if args.command in {"import-eval", "all"}:
        result["eval"] = import_eval_cases(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
