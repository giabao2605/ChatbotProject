# Tiến độ và roadmap hoàn thành mục tiêu `doichieukientruc.docx`

Ngày cập nhật: 2026-07-16

Nhánh: `codex/p1-retrieval-intelligence`

Commit đối chiếu runtime Query Decomposition: `c7e7b86`

Commit đối chiếu Community summaries offline control plane: `27c13ce`

Tài liệu gốc: [`../doichieukientruc.docx`](../doichieukientruc.docx)
Bản review ban đầu: [`doichieukientruc-review.md`](doichieukientruc-review.md)

## Phạm vi và định nghĩa hoàn tất

Tài liệu gốc đề xuất năm lớp nâng cấp chính:

1. Telemetry và labeled evaluation làm nền tảng.
2. Corrective RAG và claim repair.
3. Grounded math có provenance.
4. Late Interaction, query decomposition và GraphRAG có kiểm soát.
5. Rollout theo feature flag, benchmark và governance gate.

Trong trang này, mục tiêu đang thực hiện là hoàn tất 100% **phạm vi controlled demo cho 5–10 người dùng**. Điều đó không đồng nghĩa sẵn sàng rollout mặc định. Một hạng mục demo chỉ được đóng khi đáp ứng các tầng chung và một trong các nhánh quyết định có bằng chứng sau:

1. **Code:** implementation đã nối vào pipeline thật, có feature flag mặc định tắt.
2. **Verification:** unit test, integration test cần thiết và full test suite đều xanh.
3. **Evaluation:** có manifest hợp lệ, baseline/candidate chạy trên cùng snapshot và có artifact tái lập.
4. **Gate:** gate đã được chạy, có artifact hợp lệ và quality, leakage, latency, cost, retry đã được đối chiếu với tiêu chí khóa; kết quả pass/fail quyết định nhánh tiếp theo.
5. **Operations:** có rollback, cleanup, audit và runbook phù hợp với trạng thái triển khai.
6. **Decision:** dùng `milestone-decision-v2`, scope là `controlled_demo`, và kết thúc bằng `accepted`, `rejected` hoặc `inconclusive`. Quyết định demo không tự hoàn tất prerequisite của `default_rollout`.

Vì vậy, “100% controlled demo” không đồng nghĩa phải bật mọi công nghệ. Một quyết định không triển khai có bằng chứng vẫn hoàn thành mục tiêu nghiên cứu; không ép bật tính năng kém hiệu quả. Các yêu cầu 7–14 ngày, tối thiểu 100 matched pairs và độ tin cậy cao hơn vẫn được giữ nguyên cho `default_rollout` và hiện ở trạng thái deferred.

## 1. Hiện trạng: đã có, chưa có, đã làm và chưa làm

### 1.1 Bảng tổng quan

| Hạng mục                       | Code                                          | Test offline               | Artifact/gate live                                                    | Controlled demo / default decision | Trạng thái thực tế                                                                               |
| -------------------------------- | --------------------------------------------- | -------------------------- | --------------------------------------------------------------------- | ---------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Telemetry và labeled evaluation | Có                                           | Có                        | Có                                                                   | Áp dụng cho evaluation           | Foundation v4 hoàn tất; pilot labels thuộc Milestone A–F                                         |
| CRAG và claim repair            | Có                                           | Có                        | Ba gate đạt                                                         | Inconclusive | Thiếu 20 matched pairs, hai người dùng và reviewer sign-off; flag giữ tắt |
| Grounded math                    | Có                                           | Có, gồm integration live | 3/3 pair đạt; series bị chặn bởi CRAG                            | Inconclusive | Thiếu 10 truy vấn demo được review thủ công; flag giữ tắt |
| Late Interaction                 | Có                                           | Có                        | Có readiness và 3 pair clean-commit gate fail-closed                | Rejected cho controlled demo mặc định | `late-v2` được giữ làm research path; flag bị pin tắt trong demo matrix |
| Query decomposition              | Có                                           | Có, full suite xanh       | Có provider-stable clean pair tại `937ec52`; quality gate không đạt | Rejected cho controlled demo | Không có harness/provider error; quality gain và branch accuracy/citation không đạt, flag giữ tắt |
| Governed GraphRAG                | Có schema/API/retrieval                      | Có, gồm integration live | Có clean-commit pair; gate fail-closed vì thiếu independent review | Inconclusive | Thiếu 20 edge do reviewer độc lập gán nhãn; flag giữ tắt |
| Community summaries              | Có offline control plane, chưa nối serving | Có; full suite xanh       | Readiness fail-closed tại `27c13ce` | Inconclusive | Bị khóa hợp lệ bởi GraphRAG; chưa generation/review/global-query run |

Không gán một phần trăm tổng hợp cho bảng này vì các hạng mục có trọng số và rủi ro khác nhau. Tất cả workstream trong bảng đã có ít nhất foundation hoặc offline control plane; trạng thái live vẫn được quyết định riêng bằng artifact và gate, không suy ra từ số lượng code. Checklist tại mục 2.11 là denominator chính thức để tiến tới 100%.

### 1.2 Telemetry có thể tái lập

#### Đã có và đã làm

- `rag_end` dùng `refusal_reason`; đường tương thích legacy được normalize tại logging boundary.
- Trace có `execution_context` gồm `production`, `evaluation` và `test`.
- Snapshot nhận timestamp range và context, mặc định loại `client_cancelled`, reason rỗng và test trace.
- Snapshot ghi source path, commit SHA, filter, denominator, P50/P95, cost, correction, repair và retry.
- External-AI call được ghi metadata-only theo provider, model, surface, status và latency; không ghi raw prompt, response, endpoint hoặc secret.
- Report external-AI phân biệt `success`, `error`, `cancelled` và `unknown`.
- `trace_id` đã được truyền qua generation, reranking, interaction routing, intent routing, query disambiguation, history summary, correction, repair và evidence verification.
- Semantic cache namespace chứa RBAC scope, trạng thái feature flags, planner version, Late Interaction index version và graph serving epoch.
- CRAG rollout runner từ chối chạy khi tracked worktree còn dirty và kiểm tra lại trước baseline, giữa baseline/candidate và sau candidate.

Các implementation chính:

- [`scripts/eval/rag_trace_snapshot.py`](../scripts/eval/rag_trace_snapshot.py)
- [`src/mech_chatbot/config/logging.py`](../src/mech_chatbot/config/logging.py)
- [`src/mech_chatbot/llm/external_ai.py`](../src/mech_chatbot/llm/external_ai.py)
- [`src/mech_chatbot/rag/semantic_cache.py`](../src/mech_chatbot/rag/semantic_cache.py)

#### Chưa có hoặc chưa hoàn tất

- Đã có risk–coverage curve theo operating point; chưa có dashboard production hiển thị curve này.
- Chưa có dashboard chuẩn tổng hợp theo pipeline namespace và theo feature combination.
- Chưa có SLO/alert chính thức cho provider latency, external reranker error và feature-specific fallback rate.
- Voyage 429 đã có policy pilot: không retry trong user request, fallback local ngay và abort nếu error rate >5% trong một cửa sổ đủ 50 rerank call. Chưa có SLO/alert production tổng quát ngoài pilot.

### 1.3 Labeled evaluation và rollout foundation

#### Đã có và đã làm

- Manifest hỗ trợ `expected_outcome`: `full_answer`, `partial_answer`, `clarification_required`, `insufficient_evidence`, `access_denied`.
- Manifest cũ có `should_refuse` vẫn được ánh xạ tương thích.
- Evaluator báo correct/wrong refusal, wrong refusal type, wrong answer, leakage và admin exception.
- Có Recall@5/10/20, nDCG@5/10 và MRR; từng case giữ rank list và expected source identity.
- Có deterministic claim evaluator cho claim precision, expected-claim recall và faithfulness.
- Có citation evaluator cho SourceID/page/version/rendered citation và inaccessible source.
- Có risk–coverage report không tự chọn threshold khi safety chưa đạt.
- Manifest v2 và evaluator artifact v4 được version; manifest cũ được ánh xạ thành legacy.
- Có protocol hai reviewer độc lập và reviewer thứ ba khi bất đồng, bắt buộc reason code.
- Report có P50/P95, token, estimated cost, provider retry, correction, repair, calculation, planner và graph traversal count.
- Fixture CRAG có identity, RBAC, site, clearance, publication, lifecycle, current version và provenance thật trong SQL/Qdrant staging.
- Preflight kiểm tra document/page/version và fixture fingerprint trước khi gửi request tới LLM.
- Baseline và candidate giữ cùng manifest, commit, fixture, provider configuration, concurrency và timestamp window.

Các implementation chính:

- [`scripts/eval/run_eval.py`](../scripts/eval/run_eval.py)
- [`src/mech_chatbot/evaluation/outcomes.py`](../src/mech_chatbot/evaluation/outcomes.py)
- [`src/mech_chatbot/evaluation/metrics.py`](../src/mech_chatbot/evaluation/metrics.py)
- [`src/mech_chatbot/evaluation/grounding.py`](../src/mech_chatbot/evaluation/grounding.py)
- [`src/mech_chatbot/evaluation/risk_coverage.py`](../src/mech_chatbot/evaluation/risk_coverage.py)
- [`src/mech_chatbot/evaluation/adjudication.py`](../src/mech_chatbot/evaluation/adjudication.py)
- [`docs/evaluation-foundation.md`](evaluation-foundation.md)
- [`scripts/crag_eval/`](../scripts/crag_eval/)
- [`data/crag_eval_v1/eval_manifest.jsonl`](../data/crag_eval_v1/eval_manifest.jsonl)

#### Chưa có hoặc chưa hoàn tất

- Chưa có một manifest chung đủ rộng cho exact code, near-code, OCR noise, multi-intent, multi-hop, relational query và grounded calculation.
- Chưa có workflow định kỳ để đóng băng và version evaluation snapshot qua nhiều milestone.

### 1.4 Corrective RAG và claim repair

#### Đã có và đã làm

- Evidence evaluator trả `SUFFICIENT`, `AMBIGUOUS` hoặc `INSUFFICIENT` cùng reason, stage và evidence quotes.
- `AMBIGUOUS` chạy đúng một correction pass bằng rewrite/lexical expansion, retrieval lại và fuse kết quả.
- Correction giữ nguyên department, site, clearance, publication, lifecycle và current-version policy.
- `INSUFFICIENT` chỉ từ chối sau fallback/correction hợp lệ không tìm được đủ evidence.
- Number checker trả violation có normalized value thay vì chỉ boolean.
- Number format tương đương như `1,500` và `1500`, hoặc decimal separator, được normalize.
- Khi number post-check fail, pipeline có thể chạy đúng một claim-repair pass.
- Sau repair, materials, codes, units, numbers và citation được kiểm tra lại trước khi stream.
- Bản nháp vi phạm không được gửi ra trước khi repair hoàn tất.
- Access denial được phân biệt với empty retrieval; restricted probe không materialize nội dung bị chặn.
- Legacy admin bypass có audit và được báo riêng trong evaluation.
- Retry đã bao phủ transient 502/503/429/no-capacity theo budget có telemetry.

Các implementation chính:

- [`src/mech_chatbot/rag/evidence_gate.py`](../src/mech_chatbot/rag/evidence_gate.py)
- [`src/mech_chatbot/rag/claim_repair.py`](../src/mech_chatbot/rag/claim_repair.py)
- [`src/mech_chatbot/rag/pipeline.py`](../src/mech_chatbot/rag/pipeline.py)
- [`src/mech_chatbot/rag/pipeline_steps.py`](../src/mech_chatbot/rag/pipeline_steps.py)
- [`scripts/eval/crag_rollout_gate.py`](../scripts/eval/crag_rollout_gate.py)

#### Bằng chứng đã đạt staging gate

Ba cặp benchmark hợp lệ trên commit `adb8060` đều đạt gate, candidate đạt 9/9. `reports/` là thư mục local bị gitignore, vì vậy bảng dưới lưu kết quả và digest ngay trong tài liệu tracked thay vì tạo link không tái lập được:

| Run local                    | Gate | Candidate | Baseline P95 | Candidate P95 | Ratio | Candidate trace SHA-256                                              |
| ---------------------------- | ---- | --------: | -----------: | ------------: | ----: | -------------------------------------------------------------------- |
| `20260714-latency-pair-01` | Pass |       9/9 |    54,377 ms |     24,164 ms | 0.444 | `a99616a38b187f50f0e20b29afbc5b20ff948793c251b83d93d49e200f13a311` |
| `20260714-latency-pair-02` | Pass |       9/9 |    16,176 ms |     16,530 ms | 1.022 | `d7a9ac1886d7f2d85ac8b9e139bc321b9234a785ff08a68b8b7ba8f4dd3867da` |
| `20260714-latency-pair-03` | Pass |       9/9 |    13,515 ms |     15,740 ms | 1.165 | `e06d99961b8e5c5ddc42d2ee647f4f96a5e69a031fc2528e9277575d62bf179c` |

Gate JSON của ba run có SHA-256 `3a6721a266c36ac59b321bd8af59cd00934da01037dc4eead4f639c02199619c`. Artifact có thể được tái tạo bằng runner tracked:

```powershell
$runId = 'crag-' + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$trace = "logs\rag_trace_$runId.jsonl"
$output = "reports\crag-rollout\$runId"
$env:QDRANT_COLLECTION = 'MechChatbot_CRAG_Eval_v1'
$env:RUN_CRAG_EVAL_FIXTURE = '1'
New-Item -ItemType Directory -Force logs | Out-Null
if (-not (Test-Path -LiteralPath $trace)) {
  New-Item -ItemType File -Path $trace | Out-Null
}
.\chat_env\Scripts\python.exe -m scripts.crag_eval.generate_fixture
.\chat_env\Scripts\python.exe -m scripts.crag_eval.ingest_fixture
.\chat_env\Scripts\python.exe -m scripts.crag_eval.preflight `
  --manifest data\crag_eval_v1\eval_manifest.jsonl
.\chat_env\Scripts\python.exe -m scripts.crag_eval.run_rollout `
  --manifest data\crag_eval_v1\eval_manifest.jsonl `
  --output-dir $output `
  --trace $trace `
  --router-mode offline
```

Các lệnh live yêu cầu SQL/Qdrant staging đã cấu hình và fixture phải ingest, review, publish, current và preflight thành công trước rollout. Không chạy các lệnh này với collection production.

Trong cả ba run:

- Candidate đạt 9/9.
- Gate không ghi nhận wrong-answer hoặc leakage regression.
- Provider retry rate bằng 0.
- Mỗi query không vượt một correction và một repair.
- Cost ratio nằm dưới giới hạn 1.5.

#### Chưa có hoặc chưa hoàn tất

- Chưa bật `RAG_CRAG_ENABLED` và `RAG_CLAIM_REPAIR_ENABLED` trong production pilot.
- Chưa có artifact production pilot chứng minh không regression trong traffic thật.
- Trong ba cặp CRAG benchmark, reranking external có 19 error status trên 48 call; log trực tiếp cho thấy Voyage 429 là lỗi đã quan sát. ADR 0002 đã khóa policy fallback local ngay, không retry trong request và ngưỡng abort >5%/50 call cho pilot.
- Tài liệu gốc cho phép tối đa hai correction pass; implementation và pilot gate chủ động giới hạn một pass để khóa latency/cost. Quyết định này đã được ghi trong rollout contract; kiến trúc cuối vẫn cần phản ánh giới hạn đã chọn.

### 1.5 Grounded math có provenance

#### Đã có và đã làm

- Có `GroundedFact`, `CalculationPlan` và `DerivedClaim`.
- Dùng `Decimal`, không dùng `float` cho phép tính nghiệp vụ.
- Hỗ trợ tổng BOM, cộng/trừ cùng đơn vị, tỷ lệ/phần trăm từ cùng đơn vị và nhân/chia khi một vế không có đơn vị.
- Dedupe BOM theo row/source identity.
- Không tự điền operand thiếu, không trộn version, không tự unit conversion.
- Chia cho 0, unit mismatch, provenance mơ hồ hoặc thiếu operand trả partial/insufficient thay vì suy diễn.
- LLM chỉ diễn đạt kết quả đã tính; công thức, value, unit và citation được post-check trước stream.
- Feature flag `RAG_GROUNDED_MATH_ENABLED` mặc định `false`.
- Unit tests và strict stream tests đã tồn tại và xanh.
- Đã có evaluator `grounded-calculation-evaluation-v1` kiểm tra Decimal, status, operation, display, formula, unit, provenance và số không được phép.
- Đã có fixture staging riêng `grounded-math-eval-v1`, collection `MechChatbot_GroundedMath_Eval_v1`, 15 case positive/negative và source-row key cố định.
- Preflight ánh xạ source-row key sang DocID/BOM ID thật và fail-closed khi lifecycle, governance, version, value, unit hoặc Qdrant page drift.
- Đã có baseline/candidate runner chỉ toggle Grounded Math, gate latency/cost/citation/provenance, rollback evidence và cleanup giới hạn phạm vi.
- Fixture đã đi qua lifecycle ingest, review, publish, current trên staging; preflight và opt-in integration test ingest → retrieval → deterministic calculation → cleanup đều xanh.
- Ba rollout pair dưới `reports/grounded-math/d5ec3e1/` trên cùng commit, manifest, fixture fingerprint, provider configuration và concurrency đều đạt pair gate: candidate đúng 45/45 case, toàn bộ Decimal/status/operation/formula/unit/provenance đều chính xác, citation accuracy/precision đạt 51/51 và không có unsupported number.
- P95 candidate của ba pair lần lượt là 5.467,14 ms, 4.445,26 ms và 4.458,95 ms, đều thấp hơn giới hạn 1,25 lần baseline; cost candidate bằng 0 vì deterministic generation không gọi LLM để tính.
- Evaluator đã được sửa để giữ token, cost, retry và toàn bộ budget count khi generation stream lỗi. Artifact mới ghi đúng 27–28 provider retry trên mỗi baseline thay vì báo sai 0.
- Series guardrail xác nhận pair contract, independence, benchmark conditions, safety gate, rollback và production-collection isolation đều đạt; chỉ `prior_milestones_completed=false` vì CRAG chưa có quyết định cuối.

Implementation chính:

- [`src/mech_chatbot/rag/grounded_math.py`](../src/mech_chatbot/rag/grounded_math.py)
- [`tests/unit/test_grounded_math.py`](../tests/unit/test_grounded_math.py)
- [`src/mech_chatbot/evaluation/grounded_math.py`](../src/mech_chatbot/evaluation/grounded_math.py)
- [`scripts/grounded_math_eval/`](../scripts/grounded_math_eval/)
- [`docs/grounded-math-rollout.md`](grounded-math-rollout.md)

#### Chưa có hoặc chưa hoàn tất

- ProxyLLM vẫn có `503 no_capacity`: ba baseline ghi 27–28 retry/pair. Voyage fallback rate của candidate là 80% do `429`; kết quả deterministic vẫn đúng nhưng môi trường provider chưa phù hợp cho production pilot.
- Dependency CRAG chưa có quyết định milestone cuối, nên guardrail vẫn chặn production pilot Grounded Math.
- Chưa production pilot.
- Unit conversion có provenance vẫn nằm ngoài phạm vi và cần spec riêng nếu muốn bổ sung.

### 1.6 Late Interaction bằng shadow index

#### Đã có và đã làm

- Có seam `attempt_shadow_rerank(...)` trả documents, coverage, latency, used-shadow và fallback reason.
- Shadow rerank chỉ nhận candidate đã qua dense+sparse+RRF và governance; không thể thêm document ngoài input.
- Chỉ dùng MaxSim khi coverage đầy đủ; partial coverage/schema/encoder/Qdrant error fallback nguyên candidate.
- Không trộn MaxSim score với Voyage/local score.
- Candidate identity dùng `SHA256(doc_id|page|canonical_chunk_index|content_hash)`; chunk index thiếu dùng sentinel rỗng ổn định.
- Shadow payload giữ provenance, governance fingerprint, index version và token-vector count.
- Collection `MechChatbot_LateInteraction_v1` dùng named vector `late`, 1024 chiều, FLOAT16, MaxSim và HNSW `m=0`.
- Backfill idempotent, resumable, audit drift và không xóa orphan mặc định.
- Encoder chạy trong môi trường riêng, offline từ local cache.
- Preflight phân biệt `capability_passed` và `ready_for_serving`.
- Feature flags mặc định tắt; cache namespace chứa `RAG_LATE_INDEX_VERSION`.

Implementation chính:

- [`src/mech_chatbot/rag/late_interaction.py`](../src/mech_chatbot/rag/late_interaction.py)
- [`scripts/late_interaction/backfill_shadow.py`](../scripts/late_interaction/backfill_shadow.py)
- [`requirements-late-interaction.txt`](../requirements-late-interaction.txt)

#### Bằng chứng offline readiness

Artifact local `reports/late-interaction/fb07b27-final/readiness.json` có SHA-256 `fad1250001f6cbeaac72fa9478238b7cadda703a0baae5b555c539e1c352a89b` và ghi nhận:

- Qdrant server `1.18.2`, multivector/MaxSim tương thích.
- `capability_passed=true` và `ready_for_serving=true`.
- 170/170 source point eligible, coverage `1.0`.
- 169 unique shadow point do một source candidate trùng identity; backfill vẫn xác nhận 170 source hợp lệ.
- Governance drift `0`, provenance drift `0`, orphan `0`.
- Shadow storage ratio `23.3618`, dưới budget `25x`.
- Encode P95 `463.51 ms`, MaxSim query P95 `517.92 ms` trong offline benchmark.

#### Chưa có hoặc chưa hoàn tất

- Chưa có labeled retrieval manifest trên corpus phù hợp cho exact/near-code, rare term và OCR noise.
- Chưa chạy baseline Voyage so với candidate MaxSim trên cùng snapshot.
- Chưa chứng minh targeted nDCG@10 tăng ít nhất 5% tương đối và Recall@10 toàn bộ không giảm.
- Chưa có wrong-answer/leakage/P95 quality gate live.
- Chưa production pilot; `RAG_LATE_INTERACTION_ENABLED=false` và `RAG_LATE_ENCODER_READY=false` vẫn là mặc định.

### 1.7 Query decomposition có ngân sách cố định

#### Đã có và đã làm

- Có deterministic router phân biệt simple/complex query.
- Simple query không cần planner.
- Complex query có planner JSON schema, tối đa ba subquery.
- Subquery kế thừa RBAC, site, clearance, lifecycle, publication và version policy.
- Có shared correction budget tối đa một correction cho toàn request.
- Retrieval nhánh có thể chạy song song; mỗi nhánh giữ evidence/evaluator state riêng.
- Nhánh đủ evidence có thể trả lời, nhánh thiếu evidence được đánh dấu, access denied không tiết lộ nguồn bị chặn.
- Feature flag `RAG_QUERY_DECOMPOSITION_ENABLED=false` mặc định.
- Unit tests budget/governance đã tồn tại và xanh.

Implementation chính:

- [`src/mech_chatbot/rag/query_decomposition.py`](../src/mech_chatbot/rag/query_decomposition.py)
- [`tests/unit/test_query_decomposition.py`](../tests/unit/test_query_decomposition.py)

#### Chưa có hoặc chưa hoàn tất

- Chưa có manifest câu hỏi multi-intent/multi-source có đáp án gán nhãn.
- Chưa có baseline/candidate chứng minh correct/partial-answer rate tăng ít nhất 10 điểm phần trăm.
- Chưa đo simple-query planner call bằng 0 trên live run.
- Chưa có cost/P95/leakage gate live.
- Chưa production pilot.

### 1.8 Governed GraphRAG trên SQL Server

#### Đã có và đã làm

- Có migration additive cho `KnowledgeGraphNode`, `KnowledgeGraphEdge` và `GraphExtractionProposal`.
- Có migration hardening provenance và vô hiệu hóa edge không hợp lệ.
- Có deterministic seed script cho document family/version, supersedes, page, BOM part và material.
- Edge/proposal giữ source document, page, version, department, site, security, status và serving metadata.
- LLM proposal ở trạng thái pending; producer không ghi thẳng approved edge.
- Có admin API list/approve/reject proposal.
- Có recursive SQL graph traversal tối đa hai hop và edge budget.
- Serving kiểm tra lại current/reviewed/published/effective và user access trước khi hydrate evidence.
- Graph evidence quay lại Evidence Gate, claim check và citation check của pipeline chính.
- Feature flag `RAG_GRAPH_RETRIEVAL_ENABLED=false` mặc định.
- Unit tests kiểm tra migration, proposal governance, RBAC/site và pending edge không serving.

Implementation chính:

- [`database/migrations/V0033__governed_knowledge_graph.sql`](../database/migrations/V0033__governed_knowledge_graph.sql)
- [`database/migrations/V0034__graph_provenance_hardening.sql`](../database/migrations/V0034__graph_provenance_hardening.sql)
- [`scripts/graph/seed_deterministic.py`](../scripts/graph/seed_deterministic.py)
- [`src/mech_chatbot/rag/graph_retrieval.py`](../src/mech_chatbot/rag/graph_retrieval.py)
- [`src/mech_chatbot/db/repositories/graph.py`](../src/mech_chatbot/db/repositories/graph.py)
- [`src/mech_chatbot/services/graph_service.py`](../src/mech_chatbot/services/graph_service.py)

#### Chưa có hoặc chưa hoàn tất

- Clean migration, seed coverage, reviewer workflow, labeled relational manifest và clean-commit baseline/candidate đã hoàn tất; bằng chứng chi tiết nằm tại mục 2.7.
- Relational-answer accuracy đã vượt target, nhưng reviewed-edge precision độc lập chưa đo được vì queue 21 edge chưa được human reviewer gán nhãn.
- Graph production pilot chưa chạy vì gate đang fail-closed ở independent review.
- Community summaries đã có offline control plane và readiness fail-closed; chưa generation/serving vì reviewed-edge precision độc lập chưa đạt 95%.

### 1.9 Các kiểm thử và trạng thái vận hành chung

#### Đã có và đã làm

- Full pytest suite đang xanh; các integration test cần SQL/Qdrant/live provider vẫn là opt-in.
- Targeted tests cho grounded math, Late Interaction, decomposition, graph và retrieval intelligence gate đều xanh.
- Tất cả tính năng mới có feature flag mặc định tắt.
- HTTP/SSE chat contract không bị thay đổi bởi các milestone này.
- Branch hiện tại là `codex/p1-retrieval-intelligence`; thay đổi chưa được push/merge vào `main` trong milestone mới nhất.

#### Rủi ro còn mở

- ProxyLLM không còn 503 trong ba CRAG benchmark gần nhất nhưng generation latency vẫn có variance lớn.
- Ba cặp CRAG benchmark ghi nhận 19/48 external reranking call ở trạng thái error; log run cho thấy Voyage 429 là lỗi đã quan sát. Policy đã chốt không retry, fallback local ngay và abort theo threshold; phần còn lại là quan sát/xác nhận error và fallback rate trong controlled demo.
- Integration test opt-in Grounded Math đã chạy xanh với fixture live và cleanup giới hạn phạm vi; decomposition và GraphRAG vẫn chưa có bằng chứng integration live tương đương.
- Nhiều feature cùng bật có thể tạo interaction về cache namespace, timeout, cost và shared correction budget; offline matrix bảy combination đã có, nhưng chưa có live pair/load evidence cho từng combination.

## 2. Kế hoạch chi tiết để đạt 100% mục đích tài liệu

### Cách hiểu trạng thái trong dự án demo

Dự án hiện ở giai đoạn phát triển và demo có kiểm soát, chưa phải hệ thống đã được triển khai rộng rãi. Vì vậy roadmap tách ba mức nghiệm thu:

1. **Offline/staging ready**: code, test, fixture, artifact và rollback đã có.
2. **Controlled demo**: bật opt-in cho nhóm nhỏ đã biết trước rủi ro để quan sát, so sánh và cải tiến; feature vẫn mặc định tắt.
3. **Default rollout**: đủ bằng chứng để trở thành đường xử lý mặc định cho phạm vi người dùng rộng hơn.

Không đạt default-rollout gate không đồng nghĩa phải xóa tính năng hoặc cấm demo. Tính năng vẫn có thể vào controlled demo khi leakage bằng 0, governance không bị nới, có fallback/rollback và người thử được thông báo rõ đây là nhánh thử nghiệm.

### Hai bảng tiến độ độc lập

| Controlled demo 5–10 người | Trạng thái 2026-07-16 |
| --- | --- |
| 2.1–2.2 foundation | Hoàn tất implementation; 112 targeted tests xanh tại `937ec52` và có tracked technical evidence |
| 2.3 CRAG | `inconclusive`; chưa có 20 matched pairs và reviewer sign-off |
| 2.4 Grounded Math | `inconclusive`; staging đạt nhưng chưa có 10 truy vấn demo được review |
| 2.5 Late Interaction | `rejected`; immutable evidence và decision v2 đã được thêm |
| 2.6 Query Decomposition | `rejected`; smoke 5/5 và clean pair có 0 error/0 retry nhưng quality gate không đạt |
| 2.7 GraphRAG | `inconclusive`; thiếu independent review tối thiểu 20 edge |
| 2.8 Community summaries | `inconclusive`; bị khóa bởi 2.7, chưa generation/review/global-query run |
| 2.9 Integrated matrix | Demo ledger đầy đủ, `ready_for_demo_matrix=true` và fallback load observation concurrency 1/5 đạt; bảy pair độc lập chưa chạy |

| Default rollout | Trạng thái 2026-07-16 |
| --- | --- |
| Bằng chứng 7–14 ngày, tối thiểu 100 matched pairs | Deferred |
| Production/live prerequisites | Giữ fail-closed |
| `ready_for_live_matrix` | `false`; không được thay đổi bởi decision demo |
| Production feature flags | Mặc định `false` |

### Tổng hợp phần chưa hoàn tất từ 2.1 đến 2.5

| Mục                      | Phần đã hoàn tất                                                                                                   | Phần chưa hoàn tất                                                                                                                                                                                            | Trạng thái đối với demo                    |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| 2.1 Rollout guardrails    | Policy, CLI và fail-closed checks đã có                                                                             | Không còn implementation riêng; phải tiếp tục áp dụng guardrail cho từng run mới                                                                                                                        | Sẵn sàng                                      |
| 2.2 Evaluation foundation | Schema v4, manifest v2, claim/citation/risk-coverage evaluator, adjudication protocol, worked examples và tracked 112-test artifact đã có | Sample controlled-demo đủ reviewer chưa được thu; đây là evidence gap của demo, không phải implementation gap | Hoàn tất kỹ thuật |
| 2.3 CRAG/repair           | Code readiness, staging gates, canary isolation, replay, telemetry, abort, Voyage fallback policy và rollback đã có | Chưa có 20 matched pairs/tối thiểu hai user và reviewer sign-off | `inconclusive`, flag tắt |
| 2.4 Grounded Math         | Fixture live, 3/3 pair, exact Decimal/provenance/citation gate và rollback đều đạt | Chưa có 10 truy vấn demo thật được review toàn bộ | `inconclusive`, flag tắt |
| 2.5 Late Interaction      | Shadow `late-v2`, coverage/governance, three-arm benchmark, tracked immutable evidence và `milestone-decision-v2` đã có | Candidate không chứng minh nDCG gain; đã kết thúc nhánh evidence-first bằng quyết định `rejected` cho controlled demo mặc định | Hoàn tất quyết định; flag giữ tắt |

Đối với controlled demo, các yêu cầu 7–14 ngày, tối thiểu 100 matched pairs và quality threshold đầy đủ vẫn là mục tiêu tham chiếu cho default rollout, không phải điều kiện bắt buộc để bắt đầu một demo nhỏ. Demo vẫn phải dừng ngay khi có leakage, governance escape hoặc rollback không hoạt động.

### 2.1 Nguyên tắc triển khai còn lại

- Không mở rộng governance để tăng recall.
- Không thay collection production tại chỗ; dùng staging/shadow/blue-green.
- Mỗi milestone dùng cùng manifest, snapshot, commit, provider configuration và concurrency cho baseline/candidate.
- Không coi unit test là rollout evidence.
- Không coi một run thuận lợi là đủ; latency/provider variance phải được đánh giá qua nhiều cặp run.
- Không bật production nếu wrong-answer tăng hoặc leakage khác 0.
- Rollback luôn phải là tắt feature flag, không yêu cầu data migration ngược.
- Chỉ chuyển milestone tiếp theo sang production sau khi milestone trước có artifact và gate rõ ràng.

Trạng thái triển khai: đã hoàn tất bằng [`rollout_guardrails.py`](../src/mech_chatbot/evaluation/rollout_guardrails.py) và CLI [`scripts/eval/rollout_guardrails.py`](../scripts/eval/rollout_guardrails.py). Guardrail fail-closed với unit-only evidence, benchmark drift, production mutation, safety regression, rollback gap, ít hơn ba pair hoặc milestone dependency chưa đóng.

### 2.2 Milestone 0 — Hoàn thiện evaluation foundation dùng chung

#### Mục tiêu

Đóng các khoảng trống đo lường còn lại trước khi dùng production traffic để kết luận chất lượng.

#### Công việc

1. Mở rộng retrieval evaluator:
   - Thêm Recall@20 và MRR bên cạnh Recall/nDCG@5/10.
   - Giữ rank list và expected source identity để audit từng case.
2. Thêm claim evaluator:
   - Tách answer thành claim có source support.
   - Đo claim precision và faithfulness.
   - Không dùng cùng prompt/model generation làm nguồn ground truth duy nhất.
3. Thêm citation evaluator:
   - Kiểm tra SourceID, DocID, page, version và rendered citation.
   - Báo riêng missing citation, wrong page, wrong version và inaccessible source.
4. Thêm risk–coverage report:
   - Sắp case theo confidence/evidence state.
   - Báo coverage, wrong-refusal, wrong-answer và leakage tại từng operating point.
   - Không tự động chọn threshold nếu leakage khác 0 hoặc wrong-answer tăng.
5. Version schema evaluator và manifest; ghi evaluator version/model vào mọi artifact.
6. Xây adjudication protocol dùng chung:
   - Hai reviewer độc lập cho answer/refusal/citation label.
   - Reviewer thứ ba xử lý bất đồng.
   - Lưu reason code và không dùng log count thô thay cho label.
7. Bổ sung unit test bằng worked example và integration test artifact generation.

#### Target cố định

- Fixture claim precision: 100% trên các case high-risk được gán nhãn.
- Fixture citation accuracy: 100% cho SourceID/page/version.
- Pilot sample claim precision, faithfulness và citation accuracy: >=99%.
- Leakage: 0 tại mọi operating point được phép serving.
- Wrong-answer không tăng so với baseline.
- Retrieval report luôn có Recall@5/10/20, nDCG@5/10 và MRR; milestone-specific target vẫn được khóa trước mỗi benchmark.

#### Điều kiện hoàn tất

- Evaluator schema và manifest version được commit.
- Test evaluator xanh và artifact mẫu tái lập được từ clean commit.
- Mọi milestone A–G sử dụng cùng metric definitions và adjudication protocol này.

Trạng thái triển khai: evaluator foundation v4, manifest v2, worked-example artifact và protocol dùng chung đã hoàn tất. Milestone A–G bắt buộc đi qua `run_eval.py` và rollout guardrail này; live target >=99% vẫn phải được chứng minh trong pilot tương ứng, không được suy ra từ unit test.

### 2.3 Milestone A — Đóng CRAG production pilot

Trạng thái cập nhật 2026-07-15: phần code readiness đã hoàn tất trên nhánh `codex/p1-retrieval-intelligence`. Đã có stable HMAC assignment theo identity, hai deployment cô lập, replay bất đồng bộ sang arm đối diện, semantic-cache/side-effect isolation, sampling bắt buộc, deployment preflight, Voyage fallback policy, telemetry, abort rules, artifact JSON/Markdown và runbook rollback. Tất cả flag vẫn mặc định tắt. Controlled demo nhỏ chưa chạy; mốc 7–14 ngày, tối thiểu 100 matched pair đã adjudicate và reviewer sign-off vẫn là điều kiện cho default rollout rộng hơn.

#### Mục tiêu

Chuyển CRAG từ “staging gate passed” sang “production pilot passed”.

#### Công việc

1. [Đã chốt policy] Xử lý Voyage 429:
   - Không retry trong pilot path; fallback local ngay.
   - Abort khi external rerank error rate vượt threshold đã khóa.
   - ADR, telemetry threshold và runbook đã có; phần còn lại là đo error/fallback rate trong controlled demo để xác nhận policy hoạt động như thiết kế.
2. Tạo canary isolation vì feature flags hiện là process-global:
   - Control deployment/process chạy cùng commit với hai flag tắt.
   - Candidate deployment/process chạy cùng commit với hai flag bật.
   - Chốt một eligible cohort chung thuộc một department trước khi pilot; không đổi cohort sau khi xem kết quả.
   - Gateway dùng salted stable hash của `experiment_id|authenticated_user_id` để chia 50/50 control/candidate; không dựa vào nội dung prompt.
   - Theo dõi cân bằng role/site/query type giữa hai arm; không thay đổi RBAC/admin policy.
   - Với mọi query được đưa vào adjudication, replay bất đồng bộ sang arm còn lại trên cùng pinned snapshot; chỉ response của arm được assign mới trả cho user.
   - Replay không ghi semantic cache, không tạo side effect và giữ nguyên RBAC identity; mỗi cặp có `matched_pair_id`.
3. Khóa pilot window:
   - Tối thiểu 7 ngày và tối thiểu 100 matched query pair đã adjudicate, tùy điều kiện nào đến sau.
   - Tối đa 14 ngày; nếu chưa đủ mẫu thì kết luận pilot inconclusive thay vì tự hạ sample size.
4. Phân công owner:
   - RAG owner chịu trách nhiệm config, artifact và rollback.
   - Security/QA owner xét leakage, wrong-answer và admin exception.
   - Operations owner theo dõi latency/provider/fallback và thực hiện abort.
5. Ghi các event `evidence_gate`, `corrective_retrieval`, `claim_repair`, `external_ai_call`, `llm_retry` và `rag_end`.
6. Lấy mẫu để gán nhãn:
   - 100% refusal, correction, repair, access-denied và provider-error case.
   - Tối thiểu 20% answer thường được lấy ngẫu nhiên theo ngày.
   - Hai reviewer độc lập; reviewer thứ ba phân xử bất đồng theo protocol ở Milestone 0.
7. Artifact pilot tối thiểu phải có:
   - Schema version, run ID, experiment ID, commit, flags, start/end UTC, control/candidate deployment ID.
   - Eligible cohort definition, stable-hash assignment version, arm counts và matched-pair IDs nhưng không ghi raw credential.
   - Labeled outcome confusion, claim/citation metrics, latency/cost/retry/fallback.
   - Abort events, admin exception, reviewer sign-off và final decision.
8. So sánh candidate với control cùng cửa sổ theo wrong-refusal, wrong-answer, leakage, P50/P95, cost, retry và fallback rate.
9. Abort ngay nếu:
   - Có bất kỳ leakage ngoài admin exception hoặc confirmed wrong-answer nghiêm trọng.
   - P95 >1.25 control hoặc cost >1.5 control trong hai cửa sổ không chồng lấn liên tiếp; mỗi cửa sổ gồm 50 eligible query và chỉ đóng sau tối thiểu 30 phút.
   - Correction/repair vượt budget.
   - Nếu tiếp tục dùng Voyage, external rerank error rate >5% trong một cửa sổ 50 rerank calls đã hoàn tất.
10. Tắt candidate flags và route toàn bộ traffic về control khi abort hoặc kết thúc pilot.

Implementation phục vụ pilot:

- [`src/mech_chatbot/evaluation/crag_pilot.py`](../src/mech_chatbot/evaluation/crag_pilot.py)
- [`scripts/eval/crag_pilot_preflight.py`](../scripts/eval/crag_pilot_preflight.py)
- [`scripts/eval/crag_pilot_gate.py`](../scripts/eval/crag_pilot_gate.py)
- [`docs/adr/0002-crag-pilot-isolation-and-voyage-fallback.md`](adr/0002-crag-pilot-isolation-and-voyage-fallback.md)
- [`docs/crag-production-pilot.md`](crag-production-pilot.md)

#### Điều kiện hoàn tất

- Wrong-refusal giảm hoặc giữ 0 nếu baseline bằng 0.
- Wrong-answer không tăng.
- Leakage bằng 0 ngoài admin exception đã khai báo.
- P95 <=1.25 baseline, cost <=1.5 baseline.
- Correction và repair tối đa một lần/query.
- Claim precision và citation accuracy >=99% trên adjudicated pilot sample.
- Có ít nhất 100 matched query pair được adjudicate và đủ reviewer sign-off.
- Pilot kết thúc không có incident và có runbook rollback.

Protocol canary, labeling, artifact và abort này được tái sử dụng cho các production pilot ở Milestone B–F; mỗi milestone chỉ bổ sung metric riêng của tính năng.

### 2.4 Milestone B — Đóng Grounded Math live gate

Trạng thái hiện tại: **staging evidence đã hoàn tất, controlled demo chưa chạy**. Fixture, evaluator, isolated ingest/preflight/cleanup, baseline/candidate runner, rollback verifier và gate đã được triển khai. Ba pair trên commit `d5ec3e1` đạt toàn bộ pair gate và series conditions. Dependency CRAG và provider stability vẫn chặn default rollout, nhưng không cấm một demo Grounded Math cô lập, opt-in và có rollback.

#### Mục tiêu

Chứng minh deterministic calculation có provenance tốt hơn baseline mà không tạo số hoặc citation sai.

#### Công việc

1. Tạo fixture `grounded-math-eval-v1` riêng, có source row identity và version cố định.
2. Thêm positive cases:
   - Tổng BOM nhiều dòng.
   - Cộng/trừ cùng đơn vị.
   - Tỷ lệ/phần trăm từ cùng đơn vị.
   - Nhân/chia khi một operand không có đơn vị.
   - Dedupe duplicate row/source.
3. Thêm negative cases:
   - Thiếu operand.
   - Chia cho 0.
   - Unit mismatch.
   - Trộn version.
   - Provenance mơ hồ.
   - Số hoặc công thức không có nguồn.
4. Bổ sung evaluator kiểm tra exact Decimal result, formula, unit, DocID/page/version/row citation.
5. [Đã hoàn tất 3/3 pair] Chạy baseline với flag tắt và candidate với `RAG_GROUNDED_MATH_ENABLED=true` trên cùng snapshot và commit.
6. [Đã đạt] Chạy `retrieval_intelligence_gate.py grounded_math`; latency/cost/citation/provenance, retry telemetry và rollback contract đều đạt ở cả ba pair.
7. [Đã chạy, dependency-blocked] Series guardrail đạt mọi điều kiện nội tại của Grounded Math nhưng fail-closed vì milestone CRAG chưa hoàn tất.
8. Khi dependency CRAG đã có quyết định hoàn tất và provider ổn định, chạy lại series dependency check, sau đó pilot production nhỏ rồi đo calculation failure/refusal/latency; nếu không đạt thì lưu artifact quyết định giữ flag tắt.

#### Điều kiện hoàn tất

- 100% grounded-math fixture case đúng outcome.
- Không có wrong calculation, wrong unit hoặc unsupported number.
- Citation/provenance accuracy 100% trên fixture.
- Tối đa một calculation plan/query.
- Leakage bằng 0, P95 <=1.25 baseline và cost <=1.5 baseline.
- Production pilot đạt hoặc có quyết định không bật kèm artifact.

### 2.5 Milestone C — Đóng Late Interaction quality gate

#### Mục tiêu

Biến trạng thái “shadow index ready” thành quyết định production dựa trên chất lượng thực tế.

#### Công việc

1. [Đã hoàn tất] Tạo labeled retrieval manifest từ corpus có provenance thật:
   - Exact code.
   - Near-code và code family.
   - Tên vật liệu/tiêu chuẩn hiếm.
   - Alias/thuật ngữ lệch.
   - OCR noise.
   - Hai chunk gần nghĩa cần phân biệt.
   - RBAC/site denial và lifecycle negative cases.
2. [Đã hoàn tất] Đóng băng source snapshot 170 point, shadow index version `late-v2`, commit và provider configuration.
3. [Đã hoàn tất] Chạy ba pipeline:
   - Dense+BM25+RRF không external rerank.
   - Voyage rerank hiện tại.
   - Shadow MaxSim rerank.
4. [Đã chạy 3 pair, gate fail-closed] Voyage fallback lần lượt 62,5%, 100% và 100%; dữ liệu này chứng minh provider variance nhưng không đủ điều kiện làm baseline hợp lệ.
5. [Đã hoàn tất] Report có Recall@5/10, nDCG@5/10, wrong-answer, leakage, P50/P95, storage và fallback coverage.
6. [Đã hoàn tất] Unit/integration seam xác nhận partial shadow luôn fallback nguyên candidate và reranker không thể thêm document ngoài governance-filtered input.
7. [Chưa controlled demo] Clean-commit aggregate cho MaxSim: Recall@10=1, nDCG@10=0,5221, leakage=0, coverage=100% và không fallback. RRF đạt nDCG@10=0,8908; Voyage/fallback đạt 0,8899 nhưng baseline Voyage không hợp lệ vì fallback 87,5%. Aggregate latency MaxSim đạt budget; pair cold-start đầu không đạt.
8. [Không dùng mặc định; cho phép opt-in demo] Giữ `RAG_LATE_ENCODER_READY=false` và `RAG_LATE_INTERACTION_ENABLED=false` ở cấu hình mặc định; tiếp tục dùng Voyage/local path. Có thể bật `late-v2` trong controlled demo giới hạn để quan sát ranking/cold start và thu thập feedback. Muốn trở thành default path phải có index/encoder revision mới và rerun toàn bộ gate.

#### Điều kiện hoàn tất

- Targeted nDCG@10 tăng >=5% tương đối so với Voyage.
- Recall@10 toàn bộ không giảm.
- Wrong-answer không tăng, leakage bằng 0.
- P95 <=1.25 baseline.
- Storage <=25x source dense storage; hiện tại là 23.3618x.
- Shadow coverage 100%, drift/orphan 0.
- Controlled demo là bằng chứng quan sát tùy chọn. Default rollout chỉ đạt khi quality gate đạt; một quyết định không dùng phiên bản index hiện tại làm default, kèm clean-commit artifact, cũng đóng được milestone.

#### Kết quả hiện tại

- Artifact: `reports/late-interaction/quality-gate/20260715-757b939-series-v1/`, chạy từ commit `757b939` với cùng manifest, snapshot và provider-config fingerprint cho cả ba arm.
- Ba pair và aggregate đều fail-closed. Voyage baseline fallback vượt 10%; pair 1 không đạt latency do cold start, pair 2-3 và aggregate đạt latency.
- MaxSim giảm khoảng 41,4% nDCG@10 so với RRF hợp lệ, Recall@10 không giảm, wrong-answer và leakage bằng 0, coverage 100%, drift/orphan bằng 0 và storage 23,3618x.
- Đây là quyết định không dùng `late-v2` làm default path. Nó vẫn là demo candidate có kiểm soát vì leakage bằng 0, governance được giữ nguyên, coverage 100% và có rollback. Một revision muốn default rollout phải dùng index version mới và được nghiệm thu lại từ đầu.

### 2.6 Milestone D — Đóng Query Decomposition gate

#### Mục tiêu

Chứng minh decomposition chỉ giúp câu phức tạp, không làm câu đơn giản chậm hoặc tốn planner.

#### Công việc

1. [Đã hoàn tất] Tạo manifest `decomposition-eval-v1` gồm:
   - Simple factual query.
   - Hai hoặc ba intent độc lập.
   - SQL BOM kết hợp tài liệu.
   - So sánh version/candidate.
   - Một nhánh đủ evidence, một nhánh thiếu evidence.
   - Một nhánh access denied.
   - Query chứa mã tài liệu để kiểm tra planner không tự tạo/nới mã.
2. [Đã hoàn tất offline và preflight] Gắn expected branch outcomes, expected citations và SourceID được render cho từng subquery.
3. [Đã hoàn tất] Deterministic router giữ simple query ở `planner_count=0`; planner chỉ nhận tối đa ba subquery và không được tự tạo mã.
4. [Đã chạy nhưng artifact không hợp lệ để rollout] Baseline flag tắt và candidate `RAG_QUERY_DECOMPOSITION_ENABLED=true` đã chạy cùng commit/snapshot. ProxyLLM trả `503 no_capacity` trên phần lớn request nên pair bị fail-closed.
5. [Đã hoàn tất offline] Enforce tối đa ba subquery, một shared correction và một final generation; evaluator và gate báo budget violation.
6. [Đã hoàn tất offline] Deadline được truyền dưới dạng monotonic deadline, retrieval chạy song song và trả về khi hết hạn; chỉ document từ nhánh `full_answer` đi vào generation; missing/access-denied chỉ tạo notice an toàn.
7. [Đã hoàn tất decision] Gate đo accuracy, wrong-answer, leakage, provider retry, P95 và cost. Pair provider-stable tại `937ec52` có 0 harness error và 0 provider retry ở cả hai arm; latency/cost, leakage, wrong-answer và request budget đều đạt nhưng complex-answer gain, branch accuracy và citation không đạt.
8. [Đã reject cho controlled demo] `milestone-decision-v2` đã khóa kết luận không bật Query Decomposition trong demo hiện tại. Flag mặc định tiếp tục tắt; fallback path sẽ được kiểm tra trong integrated matrix.

#### Điều kiện hoàn tất

- Correct/partial-answer rate của complex set tăng >=10 điểm phần trăm.
- Simple set không có planner call.
- Wrong-answer không tăng, leakage bằng 0.
- P95 và cost <=1.5 baseline.
- Không query nào vượt ba subquery, một correction và một final generation.
- Production pilot đạt hoặc có quyết định không bật có bằng chứng.

#### Kết quả hiện tại

- Commit implementation: `c7e7b86`; code-review hai trục không còn blocker và full pytest xanh.
- Fixture dùng batch `crag-eval-v1`, collection staging `MechChatbot_CRAG_Eval_v1`; preflight đạt 8/8 case, SQL BOM có hai row provenance cố định và integration SQL/Qdrant xanh.
- Rollback evidence cho `RAG_QUERY_DECOMPOSITION_ENABLED` đạt; flag mặc định vẫn `false`.
- Artifact lần chạy đầu: `reports/decomposition/quality-gate/20260715-101519-c7e7b86/`. Baseline và candidate dùng cùng commit, manifest SHA, fixture fingerprint và concurrency nhưng bị ProxyLLM 503 nên chỉ là fail-closed evidence.
- Gate trả `passed=false`: complex gain, branch accuracy/citation, retry, P95 và cost không đạt. Trace xác nhận ProxyLLM `gpt-5.4` trả nhiều `503 service_unavailable/no_capacity`; baseline có 10 retry, candidate 12 retry. Vì vậy artifact này là bằng chứng fail-closed/provider-invalid, chưa phải quyết định bác bỏ decomposition.
- Provider smoke ngày 2026-07-16 đạt 5/5, 0 retry. Pair đầu tiên sau smoke phát hiện lỗi local `extract_source_ids` chưa import; lỗi đã có regression test và được sửa tại `937ec52`, artifact lỗi không được dùng làm quality decision.
- Pair sạch sau fix: `reports/decomposition/quality-gate/20260716-fixed-937ec52/`, baseline/candidate 0 error và 0 provider retry. Gate vẫn `passed=false`: complex-answer gain, branch accuracy và branch citation không đạt; latency/cost/budget/safety đạt.
- Kết luận evidence-first: Query Decomposition bị `rejected` cho controlled demo hiện tại, không tiếp tục tuning trong milestone này và giữ `RAG_QUERY_DECOMPOSITION_ENABLED=false`.

### 2.7 Milestone E — Đóng Governed GraphRAG gate

#### Mục tiêu

Nghiệm thu graph route trên staging thật trước khi dùng cho relational/global query.

#### Công việc

1. Chạy clean migration test từ database rỗng đến migration mới nhất.
2. Apply V0033/V0034 trên SQL staging và lưu migration artifact.
3. Chạy deterministic seed cho Technical, Production và Maintenance.
4. Xuất report node/edge theo type, department, site, security, version và provenance completeness.
5. Nghiệm thu reviewer workflow:
   - `knowledge_approver`, `reviewer`, `admin` có quyền phù hợp.
   - User khác không approve/reject được.
   - Pending proposal không bao giờ serving.
   - Approve tạo approved edge có audit; reject giữ history.
6. Tạo relational manifest:
   - Document supersedes document.
   - Assembly contains part.
   - Part uses material.
   - Page/document relationships.
   - Cross-department/site/security negative cases.
   - Draft, unpublished, superseded và expired source không serving.
7. Chạy baseline regular retrieval và candidate graph route trên cùng evidence snapshot.
8. Đo edge precision qua reviewer sample, graph coverage và relational-answer accuracy.
9. Pilot `RAG_GRAPH_RETRIEVAL_ENABLED=true` chỉ cho relational router scope; global sensemaking thuộc mục 2.8.
10. Sau pilot, quyết định có làm community summaries hay không.

#### Tiến độ hiện tại (2026-07-15)

- [X] Clean migration từ database rỗng đến V0035 chạy hai lần idempotent; V0033/V0034 có trong ledger. Artifact: `reports/graph/20260715-104107/migration.json` (local, reports được ignore).
- [X] Fixture staging độc lập `graph-eval-v1` đã ingest 10 tài liệu vào `MechChatbot_Graph_Eval_v1`; BOM fixture có 6 dòng để tạo đủ tập edge review, source collection production không bị sửa.
- [X] Deterministic seed được giới hạn bằng `SourceSystem`, tạo family/version, supersedes, page, part và material edges cho Technical, Production và Maintenance.
- [X] Preflight 13/13 case đạt; graph hiện có 25 node, 21 approved edge, structured coverage 6/6 = 100%, provenance completeness 100%, pending serving edge = 0 và cả ba pilot domain có node/approved edge.
- [X] Reviewer workflow staging đạt: viewer bị chặn; pending proposal không serving; approve/reject giữ reviewer, note, timestamp; hai audit event không chứa raw prompt. Hai proposal scripted chỉ là workflow fixture, không được tính làm quality sample.
- [X] Graph router chỉ gọi graph seam cho relational/global wording; simple query giữ regular retrieval. Traversal hai chiều vẫn giữ hướng edge gốc, fail-closed theo governance và bị chặn cứng ở 2 hop/50 edge.
- [X] Graph evidence sống sót qua rerank/parent hydration được giữ riêng để evaluator audit và được đưa thật vào generation context; evidence bị loại khỏi serving context không được tính relation match.
- [X] Evaluator và rollout gate đo riêng relation accuracy và fully-grounded relational-answer accuracy, cùng coverage, reviewer precision, provenance, pending-serving, router scope và traversal budget; rollback test xanh và chỉ cần tắt `RAG_GRAPH_RETRIEVAL_ENABLED`.
- [X] Clean-commit baseline/candidate tại `48e5fc6` đã chạy cùng manifest, fixture fingerprint và provider config. Artifact local: `reports/graph/quality-gate/20260715-133845-48e5fc6/`.
  - Relation accuracy: `0% -> 100%`.
  - Fully-grounded relational-answer accuracy: `0% -> 16,67%`, vượt yêu cầu tăng 10 điểm phần trăm.
  - Wrong-answer: `7 -> 6`; leakage: `0 -> 0`; provider retry: `0 -> 0`.
  - P95: `74.639,75 ms -> 45.077,56 ms`; estimated cost: `0,017305 -> 0,02052`.
  - Các check answer quality, governance, provenance, domain, pending edge, router, traversal, rollback và latency đều đạt.
- [X] Đã xuất queue 21 approved edge có provenance tại `reports/graph/20260715-130721/review-queue.jsonl`; queue cố ý để trống `reviewer`, `expected_correct` và `review_note`, không tự tạo nhãn giả.
- [ ] Chưa có reviewer độc lập điền tối thiểu 20 edge. Vì vậy `review_sample_count=0`, `reviewed_edge_precision` chưa đo được và ba check `reviewed_edge_precision`, `review_sample_is_independent`, `review_sample_size_sufficient` fail-closed.
- [X] Gate hiện có reject/fail-closed decision bằng artifact: không chạy controlled demo pilot, giữ `RAG_GRAPH_RETRIEVAL_ENABLED=false` cho đến khi human review hoàn tất và pair được chạy lại.
- [X] Community summaries đã bắt đầu ở phạm vi offline control plane; generation, serving và pilot vẫn bị khóa cho đến khi gate mục 2.7 đạt.

#### Điều kiện hoàn tất

- Structured graph coverage >=80% trong ba pilot domain.
- Reviewed-edge precision >=95%.
- Relational-answer accuracy tăng >=10 điểm phần trăm.
- Leakage bằng 0; P95 <=1.5 baseline.
- Hai-hop/50-edge budget được tuân thủ.
- Pending/unreviewed edge không serving.
- Production pilot đạt hoặc có quyết định không dùng graph route có bằng chứng.

### 2.8 Milestone F — Community summaries, chỉ khi đủ điều kiện

#### Mục tiêu

Hoàn tất phần GraphRAG global sensemaking trong tài liệu gốc mà không đưa LLM-generated graph data chưa review vào serving.

#### Điều kiện bắt đầu

- Milestone E đã đạt.
- Structured coverage >=80%.
- Reviewed-edge precision >=95%.
- Có tập global questions mà regular/graph local retrieval chưa giải quyết tốt.

#### Công việc

1. Version community detection và serving epoch.
2. Community summary luôn giữ source node/edge/doc/page/version provenance.
3. Summary generated bởi LLM ở trạng thái pending cho đến khi review hoặc qua policy được phê duyệt.
4. Tách local, global và relational evaluation set.
5. Đo global-answer accuracy, claim precision, citation accuracy, cost và indexing latency.
6. Nếu không tạo cải thiện đáng kể, ghi quyết định không serving community summaries.

#### Tiến độ hiện tại (2026-07-15)

- [X] Migration additive V0036 tạo community version, membership và summary; summary mặc định `pending`. Community version chỉ được `approved` khi có attestation graph gate đạt, structured coverage >=80%, reviewed-edge precision >=95% và target global đã khóa. Clean migration từ database rỗng qua V0036 chạy hai lần idempotent và đạt.
- [X] Community detection deterministic đã được version bằng `detection_version`, graph fingerprint và serving epoch. Run read-only tại commit `27c13ce` đọc 21 approved edge, tạo 5 community trong 24,096 ms, provenance completeness 100%, không persist và không sinh summary.
- [X] Summary contract ánh xạ chính xác community node/edge tới doc/page/version/department/site/security. Approval kiểm tra transactionally membership, edge còn approved và document còn current/published/reviewed/servable; serving kiểm tra lại version readiness, epoch, fingerprint, membership, governance và RBAC.
- [X] LLM-generated summary chỉ có thể được persist ở trạng thái `pending`; review API giới hạn cho `knowledge_approver`, `reviewer`, `admin` và audit không ghi raw prompt. Không có đường generation hoặc serving được nối vào chat pipeline khi prerequisite chưa đạt.
- [X] Semantic cache namespace gồm feature flag `RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED` và `RAG_COMMUNITY_SERVING_EPOCH`; cả feature flag và rollback test đều mặc định tắt/xanh.
- [X] Manifest `rag-eval-manifest-v2` đã tách 2 global, 2 local và 2 relational case, có identity RBAC, expected outcome, claim và citation labels. Graph fixture preflight hỗ trợ resolve nhiều citation cho global case.
- [X] Community rollout gate fail-closed nếu thiếu claim/citation metric, graph prerequisite, provenance, epoch, stale behavior, latency/cost budget hoặc global gain target 10 điểm phần trăm.
- [X] Artifact clean-commit local: `reports/community-summaries/20260715-073433-27c13ce/` (reports được ignore). `capability_passed=true`, structured coverage 100%, provenance 100%, rollback đạt.
- [ ] `ready_for_generation=false` và `ready_for_serving=false` vì graph gate mục 2.7 vẫn `passed=false`, reviewed-edge precision độc lập hiện là 0 do chưa có nhãn reviewer. Preflight exit code 2 là quyết định fail-closed đúng thiết kế.
- [ ] Chưa gọi LLM để tạo summary; chưa persist/approve summary staging; chưa chạy baseline/candidate, community quality gate hoặc controlled demo pilot.
- [ ] Bước mở khóa tiếp theo là hoàn tất independent review tối thiểu 20 edge ở mục 2.7, chạy lại graph pair/gate, sau đó mới chạy generation → review → baseline/candidate của 2.8 trên cùng snapshot.

#### Điều kiện hoàn tất

- Global-answer quality tăng theo target được khóa trước benchmark.
- Claim/citation precision không giảm.
- Không có cross-scope leakage.
- Index/update cost và stale-summary behavior nằm trong budget.
- Có rollback bằng serving epoch/feature flag.

### 2.9 Milestone G — Integrated evaluation và production hardening

#### Trạng thái cập nhật 2026-07-15

- [X] Đã hoàn thiện control plane offline cho đúng bảy combination bắt buộc.
  Mỗi row khai báo tường minh bảy feature flag, bốn version/serving namespace
  và prerequisite; cache namespace của các row không trùng nhau.
- [X] Đã tách `milestone-decision-v2` và controlled-demo ledger khỏi default-rollout ledger. Artifact lịch sử được xác minh theo source commit/hash gốc, không theo HEAD hiện tại; feature bị reject/inconclusive được pin tắt trong effective demo matrix để kiểm tra fallback.
- [X] Preflight commit `e686db1` đạt offline capability, 15/15 security case, leakage 0, cache/strict-stream/rollback và `ready_for_demo_matrix=true`; `ready_for_live_matrix=false` đúng thiết kế.
- [X] Đã sửa concurrency benchmark để giữ identity server-side từ manifest hoặc `--username` nhưng chỉ lưu question hash. Fallback all-off observation đạt 8/8 request ở concurrency 1 và 8/8 ở concurrency 5; completion P95 lần lượt khoảng 20,9 giây và 47,1 giây.
- [ ] Fallback observation chưa thay thế bảy baseline/candidate pair độc lập và chưa chứng minh wrong-answer regression/cost cho từng row. Vì mọi feature đang `rejected` hoặc `inconclusive`, không bật flag chỉ để tạo candidate matrix.
- [X] Đã thêm request-budget gate fail-closed. Mọi case phải ghi đủ counter
  kiểu integer cho planner, subquery, correction, repair, calculation, graph
  edge, provider retry và final generation; thiếu telemetry cũng bị fail.
- [X] Đã thêm 15 security case phủ allow/deny theo role, department, site,
  clearance, lifecycle, publication và current version. Legacy admin exception
  chỉ hợp lệ cho đúng role `admin` và tài liệu published/current/approved/
  effective/servable; admin vẫn không qua draft, unpublished hoặc non-current.
- [X] Đã thêm load adapter đọc đúng artifact nhiều concurrency của
  `benchmark_rag_concurrency.py`, buộc chọn một mức concurrency và ghép P50/P95
  first-token/completion với cost/query, retry và fallback của labeled eval.
- [X] Đã xác nhận bằng test rằng strict factual streaming vẫn buffer trước
  post-check/repair, feature flags mặc định tắt và rollback không cần migration.
- [X] Eval artifact lưu đúng bảy flag và bốn version; baseline phải all-off,
  candidate phải khớp chính xác matrix row. Trace snapshot được rebuild từ raw
  JSONL bất biến và các budget maxima phải khớp eval telemetry, nên nhãn
  combination hoặc snapshot tổng hợp giả không thể tự làm gate xanh.
- [X] Đã thêm composer và integrated gate kiểm lại SHA-256/schema của artifact,
  cùng commit, manifest, snapshot, provider configuration, governance scope,
  collection và concurrency cho từng pair trong bảy pair. Eval, trace, load và
  results của mỗi pair được bind end-to-end; một combination regression làm
  toàn bộ gate fail. Gate không nhận boolean tự khai báo thay evidence.
- [X] Đã viết ADR và operations runbook cho flag isolation, abort, rollback,
  cleanup có scope, baseline/candidate, load report và release decision.
- [ ] Chưa chạy live bảy combination, load pair hoặc controlled demo pilot.
  Đây là trạng thái chủ động fail-closed: CRAG, Grounded Math, Late Interaction,
  decomposition và graph vẫn chưa đồng thời có decision artifact đã xác minh.
- [ ] Chưa có release decision cuối cho toàn bộ matrix. Late Interaction và Query Decomposition đã có scoped controlled-demo reject decision; các feature
  phải có quyết định accepted/rejected và immutable evidence hợp lệ trước gate.

Kết luận hiện tại: phần triển khai có thể làm hoàn toàn offline của 2.9 đã hoàn
tất; `capability_passed` có thể đạt, nhưng `ready_for_live_matrix` phải tiếp tục
ở `false` cho đến khi các milestone 2.3–2.8 đóng prerequisite tương ứng. Không
bật feature flag demo chỉ để làm cho integrated gate xanh.

#### Mục tiêu

Chứng minh các tính năng khi kết hợp không phá governance, cache, latency hoặc budget.

#### Công việc

1. Tạo feature-combination matrix tối thiểu:
   - CRAG + repair.
   - CRAG + grounded math.
   - CRAG + Late Interaction.
   - CRAG + decomposition.
   - CRAG + graph route.
   - Decomposition + graph/Late Interaction trên các nhánh được phép.
2. Kiểm tra semantic cache isolation cho mọi combination và version namespace.
3. Kiểm tra request budget tổng:
   - Planner count.
   - Subquery count.
   - Shared correction.
   - Repair.
   - Calculation.
   - Graph traversal.
   - Provider retry.
4. Chạy security regression matrix theo role, department, site, clearance, lifecycle, publication và current version.
5. Chạy load test ở concurrency mục tiêu; báo P50/P95, first-token, completion, cost/query, retry/fallback rate.
6. Xác nhận strict streaming không phát bản nháp vi phạm trước post-check/repair.
7. Viết operations runbook:
   - Cách bật từng flag.
   - Điều kiện abort.
   - Cách rollback.
   - Cách cleanup staging/shadow/graph proposal.
   - Cách tái chạy baseline/candidate.
8. Cập nhật `doichieukientruc.docx` hoặc tài liệu kế nhiệm bằng kết quả thật, bỏ các giả thuyết đã được xác minh hoặc bác bỏ.

#### Điều kiện hoàn tất

- Full unit/integration/evaluation matrix xanh.
- Tất cả enabled production feature đều có gate và pilot artifact.
- Wrong-answer không regression, leakage bằng 0 ngoài admin exception được khai báo.
- Latency/cost/retry nằm trong budget của từng milestone và budget tổng.
- Cache namespace/rollback tests xanh.
- Runbook và ADR đầy đủ.
- Working tree sạch, changes được review và commit; release decision được ghi rõ.

### 2.10 Thứ tự thực hiện đề xuất

Thứ tự còn lại nên là:

1. [Đã hoàn tất implementation] **Evaluation foundation** đã khóa evaluator, adjudication và metric definitions dùng chung; live reviewer evidence tiếp tục được thu trong các demo.
2. **CRAG controlled demo** vì staging gate và pilot control plane đã sẵn sàng.
3. **Grounded Math controlled demo** vì code, fixture live và offline gate đã hoàn tất, phạm vi hẹp.
4. [Đã hoàn tất default decision] **Late Interaction quality gate** đã đo Recall/nDCG và loại `late-v2` khỏi default path; A/B controlled demo chỉ là bước quan sát tùy chọn.
5. **Query Decomposition gate** sau khi retrieval/rerank đã ổn định.
6. **GraphRAG staging và pilot** cho relational query.
7. **Community summaries** chỉ khi GraphRAG đạt điều kiện.
8. **Integrated hardening** và cập nhật tài liệu cuối.

Quan hệ phụ thuộc:

```text
Telemetry + labeled evaluation
        |
        +--> CRAG/repair pilot
        |         |
        |         +--> Grounded Math gate
        |
        +--> Late Interaction quality gate
        |         |
        |         +--> Query Decomposition gate
        |
        +--> Governed GraphRAG gate
                  |
                  +--> Community summaries (conditional)

Tất cả milestone đã đạt
        |
        +--> Integrated security/performance evaluation
        +--> Production hardening, runbook và final decision
```

### 2.11 Checklist cuối để tuyên bố đạt 100%

- [ ] CRAG/repair production pilot đạt hoặc có reject decision/artifact theo nhánh bác bỏ.
- [ ] Grounded math có live baseline/candidate, gate và pilot/decision.
- [X] Late Interaction có clean-commit three-arm artifact và quyết định không dùng `late-v2` làm default; controlled demo vẫn là hạng mục quan sát tùy chọn, flags mặc định giữ tắt.
- [ ] Query decomposition có complex-query gate và pilot/decision.
- [ ] GraphRAG migration, seed, reviewer flow, quality gate và pilot/decision đạt.
- [ ] Community summaries đã pilot hoặc có quyết định chính thức không triển khai do không đủ điều kiện/không tạo giá trị.
- [X] Claim precision, citation accuracy và risk–coverage evaluator đã được bổ sung; live reviewer sample vẫn thuộc gate của từng controlled demo.
- [ ] Integrated feature-combination security/performance matrix xanh.
- [ ] Không có leakage ngoài admin exception được khai báo; không wrong-answer regression.
- [ ] Mọi enabled feature có flag rollback, cache namespace và operations runbook.
- [ ] Tài liệu kiến trúc cuối phản ánh implementation và artifact thực tế, không còn dùng log count thô làm ground truth.

Khi toàn bộ checklist trên được đóng bằng artifact hoặc quyết định bác bỏ có bằng chứng, mục đích của `doichieukientruc.docx` mới được coi là hoàn thành 100%.
