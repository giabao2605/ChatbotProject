# Tiến độ và roadmap hoàn thành mục tiêu `doichieukientruc.docx`

Ngày cập nhật: 2026-07-14

Nhánh: `codex/p1-retrieval-intelligence`

Commit đối chiếu: `adb8060`

Tài liệu gốc: [`../doichieukientruc.docx`](../doichieukientruc.docx)
Bản review ban đầu: [`doichieukientruc-review.md`](doichieukientruc-review.md)

## Phạm vi và định nghĩa hoàn tất

Tài liệu gốc đề xuất năm lớp nâng cấp chính:

1. Telemetry và labeled evaluation làm nền tảng.
2. Corrective RAG và claim repair.
3. Grounded math có provenance.
4. Late Interaction, query decomposition và GraphRAG có kiểm soát.
5. Rollout theo feature flag, benchmark và governance gate.

Trong trang này, một hạng mục chỉ được coi là hoàn tất 100% khi đáp ứng các tầng chung và một trong hai nhánh quyết định sau:

1. **Code:** implementation đã nối vào pipeline thật, có feature flag mặc định tắt.
2. **Verification:** unit test, integration test cần thiết và full test suite đều xanh.
3. **Evaluation:** có manifest hợp lệ, baseline/candidate chạy trên cùng snapshot và có artifact tái lập.
4. **Gate:** gate đã được chạy, có artifact hợp lệ và quality, leakage, latency, cost, retry đã được đối chiếu với tiêu chí khóa; kết quả pass/fail quyết định nhánh tiếp theo.
5. **Operations:** có rollback, cleanup, audit và runbook phù hợp với trạng thái triển khai.
6. **Decision:** kết thúc bằng một trong hai nhánh rõ ràng:
   - **Nhánh chấp nhận:** Gate đạt, production pilot có giới hạn đạt và telemetry không xuất hiện regression.
   - **Nhánh bác bỏ:** Gate không đạt hoặc pilot bị dừng theo abort rule; có artifact ghi kết quả, nguyên nhân và quyết định giữ đường hiện tại/tắt feature. Nhánh này không bắt buộc tiếp tục pilot.

Vì vậy, “100% mục đích” không đồng nghĩa phải bật mọi công nghệ. Một quyết định không triển khai có bằng chứng vẫn hoàn thành mục tiêu nghiên cứu; không ép bật tính năng kém hiệu quả.

## 1. Hiện trạng: đã có, chưa có, đã làm và chưa làm

### 1.1 Bảng tổng quan

| Hạng mục | Code | Test offline | Artifact/gate live | Production pilot | Trạng thái thực tế |
|---|---|---|---|---|---|
| Telemetry và labeled evaluation | Có | Có | Có | Áp dụng cho evaluation | Foundation v4 hoàn tất; pilot labels thuộc Milestone A–F |
| CRAG và claim repair | Có | Có | Ba gate đạt | Chưa | Hoàn tất staging và pilot control plane; live pilot chưa bắt đầu |
| Grounded math | Có | Có | Chưa có baseline/candidate riêng | Chưa | Offline-ready |
| Late Interaction | Có | Có | Có readiness offline; chưa có quality gate | Chưa | Serving-ready về hạ tầng, chưa production-ready |
| Query decomposition | Có | Có | Chưa | Chưa | Implementation sớm, chưa được chứng minh chất lượng |
| Governed GraphRAG | Có schema/API/retrieval | Có | Chưa có staging gate | Chưa | Implementation sớm, chưa được nghiệm thu live |
| Community summaries | Chưa | Chưa | Chưa | Chưa | Chủ động hoãn theo điều kiện trong tài liệu gốc |

Không gán một phần trăm tổng hợp cho bảng này vì các hạng mục có trọng số và rủi ro khác nhau. Chỉ số có thể kiểm chứng hiện tại là: 6/6 workstream chính đã có implementation, 2/6 có artifact live hoặc readiness artifact, 1/6 đã vượt quality gate staging, và 0/6 đã hoàn tất production pilot. Checklist tại mục 2.11 là denominator chính thức để tiến tới 100%.

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

| Run local | Gate | Candidate | Baseline P95 | Candidate P95 | Ratio | Candidate trace SHA-256 |
|---|---|---:|---:|---:|---:|---|
| `20260714-latency-pair-01` | Pass | 9/9 | 54,377 ms | 24,164 ms | 0.444 | `a99616a38b187f50f0e20b29afbc5b20ff948793c251b83d93d49e200f13a311` |
| `20260714-latency-pair-02` | Pass | 9/9 | 16,176 ms | 16,530 ms | 1.022 | `d7a9ac1886d7f2d85ac8b9e139bc321b9234a785ff08a68b8b7ba8f4dd3867da` |
| `20260714-latency-pair-03` | Pass | 9/9 | 13,515 ms | 15,740 ms | 1.165 | `e06d99961b8e5c5ddc42d2ee647f4f96a5e69a031fc2528e9277575d62bf179c` |

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

Implementation chính:

- [`src/mech_chatbot/rag/grounded_math.py`](../src/mech_chatbot/rag/grounded_math.py)
- [`tests/unit/test_grounded_math.py`](../tests/unit/test_grounded_math.py)
- [`src/mech_chatbot/evaluation/grounded_math.py`](../src/mech_chatbot/evaluation/grounded_math.py)
- [`scripts/grounded_math_eval/`](../scripts/grounded_math_eval/)
- [`docs/grounded-math-rollout.md`](grounded-math-rollout.md)

#### Chưa có hoặc chưa hoàn tất

- Fixture và harness đã có trong code nhưng chưa được coi là bằng chứng live cho đến khi ingest/preflight staging chạy xanh trên commit sạch.
- Chưa có baseline/candidate artifact cho grounded math.
- Gate đã triển khai nhưng chưa có artifact live chứng minh calculation/citation accuracy, latency và cost.
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

- Chưa có clean-migration artifact trên staging database hiện tại.
- Chưa có seed report đo node/edge coverage theo Technical, Production và Maintenance.
- Chưa nghiệm thu reviewer workflow với role thật trên staging.
- Chưa có labeled relational/multi-hop manifest và baseline/candidate.
- Chưa đo reviewed-edge precision tối thiểu 95% hoặc relational-answer accuracy tăng 10 điểm phần trăm.
- Chưa production pilot.
- Community summaries chưa triển khai; đúng với điều kiện chỉ làm sau structured coverage >=80% và reviewed-edge precision >=95%.

### 1.9 Các kiểm thử và trạng thái vận hành chung

#### Đã có và đã làm

- Full pytest suite đang xanh; các integration test cần SQL/Qdrant/live provider vẫn là opt-in.
- Targeted tests cho grounded math, Late Interaction, decomposition, graph và retrieval intelligence gate đều xanh.
- Tất cả tính năng mới có feature flag mặc định tắt.
- HTTP/SSE chat contract không bị thay đổi bởi các milestone này.
- Branch hiện tại là `codex/p1-retrieval-intelligence`; thay đổi chưa được push/merge vào `main` trong milestone mới nhất.

#### Rủi ro còn mở

- ProxyLLM không còn 503 trong ba CRAG benchmark gần nhất nhưng generation latency vẫn có variance lớn.
- Ba cặp CRAG benchmark ghi nhận 19/48 external reranking call ở trạng thái error; log run cho thấy Voyage 429 là lỗi đã quan sát. Cần quyết định retry, rate-limit, fallback-only hoặc thay bằng Late Interaction trước production pilot rộng hơn.
- Các integration test opt-in chưa thể được coi là bằng chứng live cho grounded math, decomposition và GraphRAG.
- Nhiều feature cùng bật có thể tạo interaction về cache namespace, timeout, cost và shared correction budget; chưa có integrated combination matrix.

## 2. Kế hoạch chi tiết để đạt 100% mục đích tài liệu

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

Trạng thái cập nhật 2026-07-14: phần code readiness đã hoàn tất trên nhánh `codex/p1-retrieval-intelligence`. Đã có stable HMAC assignment theo identity, hai deployment cô lập, replay bất đồng bộ sang arm đối diện, semantic-cache/side-effect isolation, sampling bắt buộc, deployment preflight, Voyage fallback policy, telemetry, abort rules, artifact JSON/Markdown và runbook rollback. Tất cả flag vẫn mặc định tắt. Milestone này chưa đạt vì chưa chạy pilot thật 7–14 ngày, chưa có tối thiểu 100 matched pair đã adjudicate và chưa có reviewer sign-off/final artifact từ traffic thật.

#### Mục tiêu

Chuyển CRAG từ “staging gate passed” sang “production pilot passed”.

#### Công việc

1. Chốt policy cho Voyage 429:
   - Đo error/fallback rate theo cửa sổ thời gian.
   - Quyết định retry có backoff, fallback local ngay, hoặc tạm dùng local-only trong pilot.
   - Ghi quyết định vào ADR và telemetry threshold.
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

Trạng thái hiện tại: **code/harness hoàn tất, live gate chưa đóng**. Fixture, evaluator, isolated ingest/preflight/cleanup, baseline/candidate runner, rollback verifier và gate đã được triển khai. Các bước 5–7 vẫn cần artifact live trên commit sạch; production pilot còn bị chặn bởi quyết định milestone CRAG theo dependency guardrail.

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
5. Chạy baseline với flag tắt và candidate với `RAG_GROUNDED_MATH_ENABLED=true`.
6. Chạy `retrieval_intelligence_gate.py grounded_math` và bổ sung latency/cost/citation checks nếu còn thiếu.
7. Nếu gate đạt, pilot production nhỏ rồi đo calculation failure/refusal/latency.

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

1. Tạo labeled retrieval manifest từ corpus có provenance thật:
   - Exact code.
   - Near-code và code family.
   - Tên vật liệu/tiêu chuẩn hiếm.
   - Alias/thuật ngữ lệch.
   - OCR noise.
   - Hai chunk gần nghĩa cần phân biệt.
   - RBAC/site denial và lifecycle negative cases.
2. Đóng băng source snapshot, shadow index version `late-v2`, commit và provider configuration.
3. Chạy ba pipeline:
   - Dense+BM25+RRF không external rerank.
   - Voyage rerank hiện tại.
   - Shadow MaxSim rerank.
4. Chạy nhiều cặp benchmark để giảm ảnh hưởng Voyage 429/provider variance.
5. Báo Recall@5/10, nDCG@5/10, wrong-answer, leakage, P50/P95, storage và fallback coverage.
6. Kiểm tra candidate thiếu shadow point luôn fallback, không thêm document ngoài governance-filtered input.
7. Nếu đạt, pilot với `RAG_LATE_ENCODER_READY=true` và `RAG_LATE_INTERACTION_ENABLED=true` trên scope nhỏ.
8. Nếu không đạt, giữ Voyage/local path và ghi báo cáo kết luận; shadow collection có thể giữ để nghiên cứu hoặc cleanup theo runbook.

#### Điều kiện hoàn tất

- Targeted nDCG@10 tăng >=5% tương đối so với Voyage.
- Recall@10 toàn bộ không giảm.
- Wrong-answer không tăng, leakage bằng 0.
- P95 <=1.25 baseline.
- Storage <=25x source dense storage; hiện tại là 23.3618x.
- Shadow coverage 100%, drift/orphan 0.
- Production pilot đạt hoặc có quyết định không dùng Late Interaction có bằng chứng.

### 2.6 Milestone D — Đóng Query Decomposition gate

#### Mục tiêu

Chứng minh decomposition chỉ giúp câu phức tạp, không làm câu đơn giản chậm hoặc tốn planner.

#### Công việc

1. Tạo manifest `decomposition-eval-v1` gồm:
   - Simple factual query.
   - Hai hoặc ba intent độc lập.
   - SQL BOM kết hợp tài liệu.
   - So sánh version/candidate.
   - Một nhánh đủ evidence, một nhánh thiếu evidence.
   - Một nhánh access denied.
   - Query chứa mã tài liệu để kiểm tra planner không tự tạo/nới mã.
2. Gắn expected branch outcomes và expected citations cho từng subquery.
3. Kiểm tra deterministic router: simple query có `planner_count=0`.
4. Chạy baseline flag tắt và candidate `RAG_QUERY_DECOMPOSITION_ENABLED=true`.
5. Kiểm tra tối đa ba subquery, một shared correction và một final generation.
6. Kiểm tra deadline propagation, parallel retrieval và partial-answer rendering.
7. Chạy gate accuracy, wrong-answer, leakage, P95 và cost.
8. Nếu đạt, pilot trên nhóm query phức tạp; router vẫn giữ feature tắt cho simple query.

#### Điều kiện hoàn tất

- Correct/partial-answer rate của complex set tăng >=10 điểm phần trăm.
- Simple set không có planner call.
- Wrong-answer không tăng, leakage bằng 0.
- P95 và cost <=1.5 baseline.
- Không query nào vượt ba subquery, một correction và một final generation.
- Production pilot đạt hoặc có quyết định không bật có bằng chứng.

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
9. Pilot `RAG_GRAPH_RETRIEVAL_ENABLED=true` chỉ cho relational/global router scope.
10. Sau pilot, quyết định có làm community summaries hay không.

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

#### Điều kiện hoàn tất

- Global-answer quality tăng theo target được khóa trước benchmark.
- Claim/citation precision không giảm.
- Không có cross-scope leakage.
- Index/update cost và stale-summary behavior nằm trong budget.
- Có rollback bằng serving epoch/feature flag.

### 2.9 Milestone G — Integrated evaluation và production hardening

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

1. **Evaluation foundation** để khóa evaluator, adjudication và metric definitions dùng chung.
2. **CRAG production pilot** vì staging gate đã đạt.
3. **Grounded Math live gate** vì code offline đã hoàn tất và phạm vi hẹp.
4. **Late Interaction quality gate** vì shadow index đã ready nhưng chưa chứng minh nDCG/Recall.
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
- [ ] Late Interaction có Voyage-vs-MaxSim quality artifact và pilot/decision.
- [ ] Query decomposition có complex-query gate và pilot/decision.
- [ ] GraphRAG migration, seed, reviewer flow, quality gate và pilot/decision đạt.
- [ ] Community summaries đã pilot hoặc có quyết định chính thức không triển khai do không đủ điều kiện/không tạo giá trị.
- [ ] Claim precision, citation accuracy và risk–coverage evaluation đã được bổ sung.
- [ ] Integrated feature-combination security/performance matrix xanh.
- [ ] Không có leakage ngoài admin exception được khai báo; không wrong-answer regression.
- [ ] Mọi enabled feature có flag rollback, cache namespace và operations runbook.
- [ ] Tài liệu kiến trúc cuối phản ánh implementation và artifact thực tế, không còn dùng log count thô làm ground truth.

Khi toàn bộ checklist trên được đóng bằng artifact hoặc quyết định bác bỏ có bằng chứng, mục đích của `doichieukientruc.docx` mới được coi là hoàn thành 100%.
