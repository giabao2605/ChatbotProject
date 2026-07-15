# Grounded Math staging và production pilot

Runbook này triển khai Milestone 2.4 mà không thay collection production. Grounded Math chỉ được xét production sau khi CRAG đã có quyết định milestone hoàn tất.

## Phạm vi và fail-safe

- SQL fixture chỉ dùng `SourceSystem=grounded-math-eval-v1`.
- Qdrant fixture chỉ dùng `MechChatbot_GroundedMath_Eval_v1`.
- Asset chỉ nằm tại `data/grounded_math_eval_v1`.
- Baseline và candidate giữ CRAG/claim repair bật giống nhau; chỉ đổi `RAG_GROUNDED_MATH_ENABLED`.
- Production mặc định vẫn `RAG_GROUNDED_MATH_ENABLED=false`.
- Không unit conversion, không tự điền operand và không dùng LLM để tính.

## Chuẩn bị fixture staging

Chạy từ root bằng Python của `chat_env` và đặt collection staging trước khi import ứng dụng:

```powershell
$env:RUN_GROUNDED_MATH_EVAL_FIXTURE='1'
$env:QDRANT_COLLECTION='MechChatbot_GroundedMath_Eval_v1'
.\chat_env\Scripts\python.exe -m scripts.grounded_math_eval.generate_fixture
.\chat_env\Scripts\python.exe -m scripts.grounded_math_eval.ingest_fixture
.\chat_env\Scripts\python.exe -m scripts.grounded_math_eval.preflight --manifest data/grounded_math_eval_v1/eval_manifest.jsonl --output reports/grounded-math/preflight.json
```

Preflight phải có `passed=true`. Nó xác minh lifecycle, version, department/site/security, Qdrant page, BOM value/unit và ánh xạ source row key sang `BOM-{ID}` thật trước khi gọi model.

## Baseline và candidate

Runner yêu cầu working tree sạch để commit SHA mô tả đúng code đã chạy. Tạo rollback evidence trên cùng commit, rồi chạy một output directory mới:

```powershell
.\chat_env\Scripts\python.exe -m scripts.grounded_math_eval.verify_rollback --output reports/grounded-math/rollback.json
.\chat_env\Scripts\python.exe -m scripts.grounded_math_eval.run_rollout `
  --manifest data/grounded_math_eval_v1/eval_manifest.jsonl `
  --output-dir reports/grounded-math/<run-id> `
  --trace logs/rag_trace.jsonl `
  --router-mode offline `
  --rollback-test-artifact reports/grounded-math/rollback.json
```

Mỗi arm ghi `eval.json`, `eval.md`, `trace.json`, `trace.md` và `preflight.json`. Runner từ chối ghi đè, kiểm tra manifest/snapshot/commit không đổi và gọi `retrieval_intelligence_gate.py grounded_math`.

Gate chỉ đạt khi 100% case đúng, Decimal/công thức/đơn vị/provenance/citation đều chính xác, không có số không được phép, tối đa một plan/query, leakage bằng 0, wrong-answer không tăng, P95 không quá 1,25 lần và cost không quá 1,5 lần baseline.

## Production pilot nhỏ

Chỉ bắt đầu khi có ít nhất ba rollout pair staging hợp lệ, quyết định CRAG dependency đã hoàn tất và series guardrail cho Grounded Math đạt. Pilot dùng protocol canary chung trong `docs/crag-production-pilot.md`, nhưng chỉ bật thêm:

```text
RAG_GROUNDED_MATH_ENABLED=true
```

Theo dõi calculation failure, partial/refusal, wrong number/unit, citation/provenance, P50/P95 và cost. Bất kỳ leakage, wrong calculation hoặc wrong-answer regression nào đều dừng pilot.

Rollback không cần migration:

```text
RAG_GROUNDED_MATH_ENABLED=false
```

Nếu gate/pilot không đạt, lưu `run.json`, `gate.json`, `rollout_pair.json` và quyết định không bật; không sửa artifact để biến failed thành passed.

## Cleanup staging

```powershell
$env:RUN_GROUNDED_MATH_EVAL_FIXTURE='1'
$env:QDRANT_COLLECTION='MechChatbot_GroundedMath_Eval_v1'
.\chat_env\Scripts\python.exe -m scripts.grounded_math_eval.cleanup_fixture --execute
```

Cleanup fail-closed nếu asset root hoặc collection không đúng fixture. Nó không được chạm collection production hay tài liệu có `SourceSystem` khác.
