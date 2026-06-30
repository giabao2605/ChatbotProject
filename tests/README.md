# Khung Test — Mech Chatbot

Bo test phan tang, thiet ke de chan REGRESSION khi audit/sua tung tang.

## Cai dat
```bash
pip install -r requirements-test.txt   # pytest, pytest-cov, requests
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
RAG_SERVER_URL=http://localhost:8100 pytest -m eval

# 6) Bao phu code
pytest -m unit --cov=mech_chatbot --cov-report=term-missing
```

## Cau truc
```
tests/
  conftest.py                 # them src/ vao path, fixtures, skip theo env
  _helpers/rbac_matrix.py      # ma tran phan quyen KY VONG (nguon su that)
  unit/                        # logic thuan, chay nhanh
    test_sensitive_scanner.py  #  CHAY NGAY (chi dung re)
    test_text_utils.py         #  CHAY NGAY (chi dung unicodedata)
    test_security_filter.py    #  importorskip qdrant_client
    test_rbac_filter.py        #  importorskip qdrant_client
  integration/                 # can SQL Server / Qdrant that
    test_db_repository.py
    test_sql_qdrant_consistency.py
  eval/                        # can RAG server
    test_golden_questions.py
  golden_questions.json        # (san co) bo cau hoi vang
```

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
