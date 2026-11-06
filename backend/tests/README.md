# Khung Test — Mech Chatbot

Bo test phan tang, thiet ke de chan REGRESSION khi audit/sua tung tang.

## Cai dat
```bash
pip install -r backend/requirements/requirements-test.txt   # pytest, pytest-cov, requests
```

## Cach chay
```bash
# 1) Test logic thuan (nhanh, khong can DB/Qdrant/LLM) — chay trong CI moi commit
pytest -m unit

# 2) Tat ca tru integration + eval (CI mac dinh)
pytest -m "not integration and not eval"

# 3) Test integration tang DB (tro vao DB STAGING, KHONG production!)
RUN_DB_TESTS=1 pytest -m integration

# 4) Test nhat quan SQL <-> Qdrant
RUN_DB_TESTS=1 RUN_QDRANT_TESTS=1 pytest -m integration

# 5) Danh gia chat luong RAG (can server dang chay)
RUN_EVAL_TESTS=1 RAG_SERVER_URL=http://localhost:8100 pytest -m eval

# 6) Bao phu code
pytest -m unit --cov=mech_chatbot --cov-report=term-missing
```

## Cau truc
```
backend/tests/
  conftest.py            # them repo, backend va backend/src vao path, fixtures, skip theo env
  _helpers/              # ma tran phan quyen KY VONG (nguon su that)
  architecture/          # kiem quy tac phu thuoc giua cac tang
  fixtures/              # stub va script ho tro dung chung
  diagnostics/           # cong cu do baseline information-first (khong phai test)
  unit/                  # logic thuan, chay nhanh, chia theo module duoc test
    rag/                 #  mech_chatbot.rag, llm: truy hoi, sinh, xac minh
    db/                  #  repository, SQL, lich su chat
    api/                 #  app/rag server, auth, file duoc bao ve
    ingestion/           #  PDF, xlsx, chunking, worker nap file
    services/            #  domain, application, adapters, services
    governance/          #  kich hoat tinh nang, rollout theo dot phong ban
    evaluation/          #  bo danh gia, gate pilot, scripts/eval
    ops/                 #  scripts/ops, Windows service, backup/restore, migration
    runtime/             #  config, composition, worker runtime
  integration/           # can SQL Server / Qdrant that
  eval/                  # can RAG server
  golden_questions.json  # bo cau hoi vang
```

Them test moi vao thu muc con cua module ma no kiem. Dat ten theo tinh nang,
khong theo dot lam viec (khong dung tien to `wave`/`gd`/`phase`). Import helper
tu file test khac bang duong dan tuyet doi, vd `from tests.unit.db.test_x import y`.

## Quy uoc marker
| Marker | Y nghia | Khi nao chay |
|---|---|---|
| `unit` | Logic thuan, khong I/O ngoai | Moi commit (CI) |
| `security` | RBAC / phan quyen / ro ri du lieu | Uu tien cao nhat |
| `integration` | Can SQL/Qdrant that | Truoc release / nightly |
| `eval` | Chat luong RAG, can server | Truoc release |
| `slow` | Test cham | Nightly |

## Nguyen tac mo rong
1. Moi BUG tim thay khi audit -> viet 1 test tai hien (do truoc, xanh sau khi fix).
2. Test `xfail(strict=False)` = rui ro DA BIET nhung chua fix; khi fix xong, bo xfail.
3. Uu tien viet test `security` truoc (sai = ro ri tai lieu mat).
4. Test integration luon tro DB/Qdrant STAGING, khong cham production.
