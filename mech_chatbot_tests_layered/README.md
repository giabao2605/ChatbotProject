# Bo test theo tang (L1 -> L7) - Mech Chatbot

Muc tiec: kiem thu **tu duoi len**, moi tang co cong (gate) dinh luong. Bo test nay
la lop **tu dong hoa**; checklist P0->P2 cu la tang L7 (nghiem thu thu cong).

## 1. Cai dat

```powershell
# 1) Copy CA thu muc nay vao repo, dat cung cap voi src\
#    Vi du: C:\Users\bao.nguyen\Documents\ChatBotProject\mech_chatbot_tests_layered\
# 2) Cai pytest (mtruong app da co sqlalchemy/qdrant-client/dotenv)
pip install -r mech_chatbot_tests_layered\requirements-test.txt
```

> conftest.py tu dong tim `src\mech_chatbot`. Neu de o cho khac, set `MECH_SRC` tro toi thu muc `src`.

## 2. Chay

```powershell
cd mech_chatbot_tests_layered

# A) Cac tang KHONG can ha tang - chay duoc NGAY:
pytest -m "unit or security" -v

# B) Cac tang CAN ha tang (chi tro toi STAGING CLONE!):
$env:RUN_DB_TESTS="1"; $env:RUN_QDRANT_TESTS="1"
$env:QDRANT_URL="http://localhost:6333"; $env:QDRANT_API_KEY="<staging-key>"
$env:RAG_SERVER_URL="http://localhost:8100"
pytest -m "integration" -v

# C) Do coverage de lo cho chua test:
pytest -m "unit or security" --cov=mech_chatbot --cov-report=term-missing

# Chay tung tang:  pytest -m l4 -v   (l1..l7)
```

Hoac dung script: `run_tests.ps1` (Windows) / `run_tests.sh` (Linux/macOS).

## 3. Yeu cau moi truong theo tung tang

| Tang                    | File                      | Chay duoc ngay?               | Can gi de bat cac test con lai                                                   |
| ----------------------- | ------------------------- | ----------------------------- | -------------------------------------------------------------------------------- |
| **L1** SQL        | `test_l1_sql.py`        | ✅ phan sanitize (thuan)      | `RUN_DB_TESTS=1` + **DB staging clone** cho test ket noi / ham pha huy   |
| **L2** Qdrant     | `test_l2_qdrant.py`     | ❌                            | `RUN_QDRANT_TESTS=1` + `QDRANT_URL` (+API key) + **da ingest du lieu** |
| **L3** Ingest     | `test_l3_ingest.py`     | ✅ domain + sensitive (thuan) | file mau + LLM/DB cho`classify_document` (MAU)                                 |
| **L4** RBAC       | `test_l4_rbac.py`       | ✅ (chi can`qdrant-client`) | khong                                                                            |
| **L5** Guardrail  | `test_l5_guardrails.py` | ✅ (best-effort, xem muc 4)   | khong                                                                            |
| **L6** API/Worker | `test_l6_api_worker.py` | ❌                            | **khoi dong** `run_server.py` (+`run_worker.py`) + `RAG_SERVER_URL`  |
| **L7** E2E        | `test_l7_e2e.py`        | ❌ (thu cong)                 | staging clone + server chay; nghiem thu THU CONG                                 |

**Tra loi cau hoi “can chuan bi gi?”:**

- **L1/L2**: can DB (SQL Server) va Qdrant. L2 can **du lieu da ingest tu truoc** (collection khong rong) va da chay `create_qdrant_indexes.py`.
- **L6/L7**: can **khoi dong server** (`run_server.py`, cong 8100) va **worker** (`run_worker.py`). L6-2/6-3 can DB de kiem hang doi.
- **L4/L5**: **KHONG** can ingest / server / worker - la logic thuan, chay offline.
- **Tuyet doi** chi tro integration/eval vao **staging clone**, khong dung DB/Qdrant that.

## 4. Ghi chu ky thuat quan trong (L5)

`rag/service.py` khoi tao model + Qdrant + underthesea **ngay khi import**
(`_VISION_MODEL = build_vision_model()`), nen khong the unit-test truc tiep cac ham
guardrail. Bo test dung `_bootstrap_service.py` de **stub** cac phu thuoc nang roi
nap ham guardrail. Cach nay best-effort; neu moi truong thieu gi do, test L5 se
**SKIP** (khong lam do ca bo).

**Khuyen nghi lau dai:** tach cac ham guardrail (`has_unsupported_numbers`,
`has_unsupported_units_symbols`, `has_unsupported_materials`, `has_unsupported_codes`,
`requires_source_citation`, `has_required_source_citation`,
`make_insufficient_evidence_message`...) ra module thuan `rag/guardrails.py`
(dung nhu da lam voi `rag/rbac.py`). Khi do `_bootstrap_service` se import truc tiep,
khong can stub, va test chay on dinh 100%.

## 5. Cac MAU (TEMPLATE) can ban dien logic

Mot so test danh dau `skip` voi hau to `_TEMPLATE` vi can hieu biet sau ve schema
va DB that de viet dung (khong the tu tao mu). Da co khung + goi y buoc trong docstring:

- **L1-3** rollback cho `delete_document_completely`, `publish_as_*`, `rollback_to_*`...
- **L2-3** doi soat SQL <-> Qdrant (0 orphan, 0 doc ket `deleting`).
- **L3-2** phan loai file theo noi dung, khong bi ten file danh lua.
- **L6-2/6-3** hang doi dong thoi + race condition 2 worker.
- **L7** nghiem thu thu cong Checklist P0->P2.

> Neu muon, gui minh **ma muc** (vd L1-3) + doan schema/ham lien quan, minh viet code test day du cho muc do.
