# Kế hoạch cải thiện chatbot theo họ lỗi

## 1. Mục tiêu và phạm vi

Tài liệu này chuyển các lỗi quan sát được trong controlled demo thành các họ lỗi có thể tái tạo, đo lường và sửa ở một seam chung. Mục tiêu không phải sửa riêng từng câu hỏi hoặc xóa một tính năng sau một lần benchmark chưa đạt, mà là làm cho cùng một bản sửa bảo vệ được nhiều biến thể phổ biến trong tương lai.

Phạm vi hiện tại là controlled demo 5–10 người. Kết quả trong tài liệu này không chứng minh default rollout hoặc production-ready. Mọi tính năng thử nghiệm tiếp tục mặc định tắt cho đến khi vượt gate tương ứng; rollback luôn là tắt feature flag và không được yêu cầu migration ngược.

Các invariant chung:

- Không nới role, department, site, clearance, publication, lifecycle hoặc current-version policy để tăng recall.
- Leakage phải bằng 0; admin exception được báo riêng và không bypass publication/lifecycle/current.
- Wrong-answer không được tăng so với baseline trên cùng snapshot.
- Provider failure tạo kết luận `inconclusive`, không bị tính thành quality failure.
- Không ghi raw prompt, raw response, raw document hoặc secret vào audit artifact.
- Không dùng kết quả offline để tuyên bố production-ready.

## 2. Snapshot xuất phát và vấn đề tổng quát

Nguồn bằng chứng chính là `reports/controlled-demo/20260716-post-ingest-v3/` trên collection `TaiLieuKyThuat_v2`. Preflight đạt 44/44 case, 18 tài liệu, 0 failure; provider smoke đạt 5/5 request, không có `503/no_capacity` hoặc retry trong cửa sổ đo.

| Tính năng | Kết quả đã quan sát | Vấn đề tổng quát cần giải quyết |
| --- | --- | --- |
| CRAG | Baseline và candidate cùng đạt 3/18; wrong-answer 10, wrong-refusal 4, leakage 0 | Chưa phân loại ổn định khi nào phải trả lời, trả lời một phần, clarification, correction hoặc refusal |
| Grounded Math | 10 case nhưng candidate không tạo calculation plan; wrong-answer tăng 5 lên 7 | Bảng Markdown đã vào Qdrant nhưng chưa tạo structured BOM có row provenance trong SQL |
| Query Decomposition | Planner gọi 8 lần nhưng chỉ tạo tổng cộng 8 subquery cho 9 case; branch accuracy/citation accuracy bằng 0 | Planner không bị kiểm tra xem đã bao phủ đủ các ý trong câu hỏi hay chưa |
| GraphRAG | Historical queue có 21 edge; AI source audit đánh giá 20 edge hợp lý và một edge mơ hồ/trùng | Cần ngăn relation mơ hồ, trùng lặp và vẫn cần independent human review trước serving |
| Late Interaction | Shadow index an toàn, coverage 100%, nhưng `late-v2` không chứng minh nDCG gain | Cần cải thiện theo query family và hard negative, không chọn làm default chỉ vì index đã sẵn sàng |
| Community Summaries | Chưa generation/review/global-query run | Bị khóa hợp lệ cho đến khi GraphRAG đạt human-review gate |

## 3. Taxonomy họ lỗi và hợp đồng đánh giá

### 3.1 Danh mục chuẩn

| Mã | Ý nghĩa |
| --- | --- |
| `ACCESS_POLICY_ERROR` | Nhầm access denied với không tìm thấy dữ liệu hoặc làm lộ nguồn bị chặn |
| `EVIDENCE_POLICY_ERROR` | Chọn sai full answer, partial answer, clarification hoặc refusal |
| `CORRECTION_ROUTING_ERROR` | Không correction khi còn khả năng truy xuất, hoặc correction khi chắc chắn không giúp được |
| `UNSUPPORTED_CLAIM` | Câu trả lời chứa claim, số hoặc citation không được evidence hỗ trợ |
| `STRUCTURED_FACT_MISSING` | Tài liệu có bảng nhưng không tạo được fact có row/source provenance |
| `CALCULATION_PLAN_ERROR` | Thiếu/sai operand, operation, unit, formula hoặc version |
| `MULTI_INTENT_COVERAGE_ERROR` | Planner bỏ sót hoặc gộp sai một hay nhiều ý độc lập |
| `BRANCH_CITATION_ERROR` | Nội dung nhánh đúng nhưng outcome/citation của nhánh sai hoặc thiếu |
| `GRAPH_RELATION_ERROR` | Edge sai, mơ hồ, trùng, chưa review hoặc không còn servable |
| `RERANK_REGRESSION` | Reranker làm thứ tự kết quả kém hơn baseline hợp lệ |
| `PROVIDER_FAILURE` | Provider hết capacity, retry quá budget hoặc trả lỗi không phản ánh chất lượng tính năng |
| `LATENCY_BUDGET_ERROR` | Kết quả đúng nhưng vượt latency/cost budget của milestone |

### 3.2 Manifest và artifact

Manifest hiện tại tiếp tục được đọc tương thích. Case mới có thể bổ sung các trường tùy chọn:

- `failure_family`: một mã trong taxonomy.
- `seed_case_id`: case lỗi gốc tạo ra họ regression.
- `expected_policy`: outcome/evidence/correction policy mong đợi.
- `invariants`: các điều luôn phải đúng qua mọi biến thể.
- `mutation_axes`: các trục biến đổi được phép.
- `holdout`: đánh dấu case không được dùng để điều chỉnh implementation.

Evaluator sinh artifact `failure-family-eval-v1`, báo riêng kết quả từng case, từng seed và từng họ lỗi. Artifact phải ghi commit SHA, manifest hash, snapshot fingerprint, provider configuration, concurrency, feature flags và evaluator version.

Mỗi lỗi thật phải tạo:

1. Một regression seed bất biến.
2. Tối thiểu bốn biến thể dùng phát triển.
3. Tối thiểu hai biến thể holdout chưa dùng để điều chỉnh code.
4. Một invariant mô tả điều luôn phải đúng.

Biến thể deterministic là nguồn gate chính. Paraphrase do LLM sinh chỉ được tính vào gate sau khi người review xác nhận expected outcome; model generation không được tự làm ground truth cho chính nó.

## 4. Lộ trình cải thiện theo deep module

### 4.1 Giai đoạn A — Outcome Policy và Evidence Gate

Seam thống nhất:

```python
decide_answer_policy(
    question,
    evidence,
    access_context,
) -> AnswerDecision
```

`AnswerDecision` chứa:

- `outcome`: `full_answer`, `partial_answer`, `clarification_required`, `insufficient_evidence` hoặc `access_denied`.
- `evidence_state`: `SUFFICIENT`, `AMBIGUOUS` hoặc `INSUFFICIENT`.
- `reason`.
- `evidence_quotes`.
- `correction_allowed`.

Quy tắc:

- Access denied được quyết định trước retrieval quality và không tiết lộ tên/nội dung nguồn bị chặn.
- `AMBIGUOUS` chỉ correction khi dữ liệu có khả năng truy xuất thêm.
- Không correction cho RBAC denial, operand không tồn tại hoặc tài liệu xác nhận thông tin không được cung cấp.
- Negative evidence là bằng chứng hợp lệ để trả lời rằng tài liệu không quy định một thông tin.
- Giữ tối đa một correction pass cho toàn request và tái sử dụng nguyên governance filter.
- `evidence_gate` hiện tại trở thành adapter/implementation detail; pipeline chỉ phụ thuộc `AnswerDecision`.

Gate:

- 100% access-denied/high-risk case đúng outcome và leakage bằng 0.
- Wrong-refusal giảm hoặc giữ 0; wrong-answer không tăng.
- Correction chỉ xuất hiện khi `correction_allowed=true` và không vượt một lần/request.

### 4.2 Giai đoạn B — Structured Facts và Grounded Math

Seam thống nhất:

```python
solve_grounded_calculation(
    question,
    facts,
) -> CalculationResult
```

Đường dữ liệu:

```text
Markdown/PDF/DOCX table
    -> bảng hàng/cột chuẩn hóa
    -> BangKeVatTu có row identity
    -> GroundedFact
    -> CalculationPlan
    -> Decimal calculation
    -> provenance/citation post-check
    -> câu trả lời
```

Thay đổi:

- Parser bảng Markdown chuyển GitHub-style table thành định dạng dùng lại được bởi BOM extractor hiện có.
- Tái sử dụng `BangKeVatTu`; không tạo schema SQL mới chỉ cho Markdown.
- Point Qdrant vẫn giữ nội dung đọc được, nhưng Grounded Math chỉ chạy khi structured fact có provenance hợp lệ.
- Không cho LLM tự tính khi không có `CalculationPlan`.
- Chuẩn hóa unit không phân biệt hoa thường nhưng không tự quy đổi đơn vị.
- Không trộn version, không tự điền operand, không chia cho 0 và không dùng `float`.
- Thiếu một phần dữ liệu trả partial answer, chỉ rõ phần chưa tính được và không phát sinh số suy diễn.
- Telemetry ghi operation, status, operand count và provenance identity, không ghi raw document.

Gate:

- 100% fixture calculation đúng exact `Decimal`, formula, unit và row provenance.
- Không unsupported number, wrong unit, mixed version hoặc duplicate-row double count.
- Citation/provenance accuracy 100%, leakage 0 và tối đa một calculation plan/query.

### 4.3 Giai đoạn C — CRAG và claim repair

Sau khi Outcome Policy ổn định:

- Sinh biến thể alias, typo, định dạng số, citation/version, negative evidence và empty retrieval.
- Correction phải tạo candidate/evidence mới; nếu không cải thiện thì fallback an toàn.
- Claim repair chỉ sửa claim vi phạm, giữ citation trong tập evidence được phép và chạy tối đa một lần.
- Không stream bản nháp trước khi post-check và repair hoàn tất.
- Các case wrong-refusal và correction không kích hoạt trong snapshot hiện tại trở thành regression seed.

Gate giữ nguyên ADR 0001: wrong-answer không tăng, leakage 0, wrong-refusal giảm/giữ 0, latency/cost trong budget và rollback chỉ bằng hai feature flag.

### 4.4 Giai đoạn D — Query Decomposition

Seam thống nhất:

```python
compile_query_plan(
    question,
    access_context,
) -> BranchPlan
```

Thay đổi:

- Router deterministic giữ câu đơn giản ở `planner_count=0`.
- Câu phức hợp được tách sơ bộ theo clause trước khi gọi planner.
- Sau planner, kiểm tra coverage giữa các ý gốc và tối đa ba subquery.
- Nếu planner chỉ lặp lại câu gốc hoặc bỏ ý, dùng deterministic split làm fallback.
- Planner không được tự tạo mã tài liệu hoặc thay đổi access context.
- Mỗi nhánh có outcome, evidence state, evidence và citation riêng.
- Một correction budget dùng chung cho toàn request.
- Final generation chỉ nhận evidence từ nhánh được phép trả lời; access-denied branch không tiết lộ nguồn.

Gate:

- Simple query không gọi planner.
- Mọi expected intent được ánh xạ đúng một nhánh hoặc có lý do an toàn vì sao không thể tạo nhánh.
- Tối đa ba subquery, một correction và một final generation.
- Branch accuracy/citation tăng, wrong-answer không tăng và leakage 0.

### 4.5 Giai đoạn E — GraphRAG và Community Summaries

Seam thống nhất:

```python
retrieve_governed_graph_evidence(
    seeds,
    access_context,
) -> GraphEvidence
```

Quy tắc:

- Duy trì whitelist relation ontology; deterministic edge và LLM-proposed edge được đo riêng.
- Edge LLM luôn `pending` cho đến khi reviewer độc lập duyệt.
- Từ chối edge mơ hồ, trùng nghĩa hoặc thừa so với deterministic edge.
- Historical edge 48 `RELATED_COMPONENT` là regression seed cho relation mơ hồ/trùng `CONTAINS_PART`.
- Mọi edge phải có source quote, DocID, page, version và governance metadata.
- Trước serving, kiểm tra lại current, reviewed, published, effective, department, site và clearance.
- Community summary chỉ được tạo từ serving edge đã duyệt và phải giữ provenance tới edge/node nguồn.

Gate:

- Tối thiểu 20 edge được người độc lập review và reviewed-edge precision đạt ít nhất 95%.
- Không edge pending/draft/superseded/unpublished/trái RBAC-site được serving.
- Relational-answer accuracy tăng, leakage 0 và traversal nằm trong hai hop/50 edge.
- Chỉ mở Community Summaries sau khi GraphRAG được accepted; review thủ công toàn bộ năm summary demo.

### 4.6 Giai đoạn F — Late Interaction

Không xóa shadow index hiện tại:

- Giữ `late-v2` bất biến làm baseline nghiên cứu và tiếp tục mặc định tắt.
- Nhóm benchmark theo query family thay vì chỉ đo aggregate.
- Bổ sung hard negative: mã gần giống, alias, OCR noise, thuật ngữ hiếm và hai chunk gần nghĩa.
- Chỉ tạo `late-v3` khi encoder/index contract thực sự thay đổi.
- Shadow reranker không được bổ sung document ngoài candidate đã qua governance.
- Partial coverage hoặc encoder/Qdrant failure trả nguyên candidate cho fallback hiện tại; không trộn score.

Default gate chỉ đạt khi nDCG@10 tăng tối thiểu 5% tương đối so với baseline hợp lệ, Recall@10 không giảm, wrong-answer không tăng, leakage 0 và P95/storage nằm trong budget.

### 4.7 Giai đoạn G — Mutation matrix tích hợp

Sinh biến thể theo các trục:

- Paraphrase, đảo thứ tự câu và phủ định.
- `1,500`, `1500`, `1.500`, `12,50`, `12.50`.
- Unit hoa/thường, unit thiếu hoặc khác nhau.
- Operand thiếu, duplicate row, division by zero và mixed version.
- Một, hai và ba intent; thay đổi thứ tự intent.
- RBAC, department, site, clearance và admin exception.
- Current, superseded, draft, unpublished và expired.
- Graph edge đúng, sai, mơ hồ, pending và trùng lặp.
- Retrieval exact-code, near-code, alias, OCR noise và empty retrieval.

Mỗi tổ hợp chạy cùng manifest, snapshot, commit, provider configuration và concurrency. Tính năng `rejected` hoặc `inconclusive` vẫn được chạy với flag tắt để xác nhận fallback/rollback ổn định. Cache namespace phải chứa toàn bộ feature flags và planner/index/graph versions.

## 5. Điều kiện hoàn tất

Một họ lỗi được coi là đã cải thiện khi:

- 100% security, governance và high-risk regression seed đạt.
- Leakage bằng 0.
- Wrong-answer không tăng so với baseline.
- Ít nhất 90% biến thể phổ biến đạt.
- Tối thiểu hai holdout variant mỗi họ lỗi đạt mà chưa được dùng để chỉnh implementation.
- Citation và provenance đúng 100% cho Grounded Math và GraphRAG.
- Correction, repair, planner, calculation và graph traversal không vượt budget.
- Rollback/fallback tests xanh.
- Provider failure được ghi `inconclusive`, không bị tính thành lỗi chất lượng.

Thứ tự triển khai:

1. Failure taxonomy, mutation contract và evaluator artifact.
2. Outcome Policy.
3. Markdown structured facts và Grounded Math.
4. CRAG correction/repair.
5. Query Decomposition intent coverage.
6. GraphRAG ontology/human review và Community Summaries.
7. Late Interaction revision theo query family.
8. Mutation matrix và controlled-demo rerun.
9. Cập nhật decision artifacts và roadmap.

Mỗi giai đoạn phải có targeted tests, integration test phù hợp, artifact trước/sau và quyết định `accepted`, `rejected` hoặc `inconclusive`. Full test suite và code-review hai trục được chạy trước commit.

## 6. Tương thích và theo dõi

- Không đổi HTTP/SSE chat contract.
- Không migration collection production hoặc ingestion schema ngoài thay đổi additive đã được gate riêng.
- Artifact lịch sử được giữ bất biến; evaluator mới đọc tương thích manifest hiện tại.
- `doichieukientruc-progress-roadmap.md` tiếp tục là ledger nghiệm thu 2.1–2.9.
- Tài liệu này là nguồn theo dõi họ lỗi, regression seed, mutation coverage và thứ tự cải thiện.
- Khi một tracer hoàn tất, cập nhật bảng snapshot, evidence path, gate result và feature-flag state trong cả hai tài liệu; không đánh dấu default rollout từ evidence controlled demo.

## 7. Trạng thái triển khai ngày 2026-07-17

### 7.1 Đã hoàn thành ở mức code và fixture deterministic

| Tracer | Implementation evidence | Trạng thái |
| --- | --- | --- |
| Failure contract | `evaluation/failure_families.py`, `evaluation/failure_mutations.py`, `scripts/eval/generate_failure_mutations.py` | Hoàn thành validator, compiler 1 seed + 4 dev + 2 holdout và artifact có hash/commit |
| Outcome Policy | `rag/answer_policy.py`, adapter trong `rag/pipeline.py` và policy evaluation trong `run_eval.py` | Hoàn thành năm outcome, ba evidence state và correction policy fail-closed |
| Structured facts | `ingestion/pdf/bom.py`, Markdown path trong `ingestion/pdf/pipeline.py`, exact Decimal/source row ở BOM repository | Hoàn thành code cho lần ingest mới; corpus SQL hiện hữu vẫn cần re-ingest hoặc repair có kiểm soát |
| Grounded Math | `solve_grounded_calculation()` và post-check/evaluator hiện hữu | Matched rollout commit `085f3f3` đạt 16/16, rollback và guardrail đạt; vẫn chờ review 10 truy vấn demo thật |
| Query Decomposition | `compile_query_plan()` với deterministic intent split/coverage/fallback | Preflight/rollback đạt; matched 8-case candidate chỉ 1/8 và gate fail-closed, flag vẫn tắt |
| GraphRAG | `rag/graph_ontology.py`, proposal validator và duplicate-serving-edge check | Fixture 27 node/21 edge, source evidence 21/21 và preflight đạt; quality gate vẫn fail vì relational gain/review độc lập, flag vẫn tắt |
| Community Summaries | Gate hiện hữu tiếp tục yêu cầu GraphRAG accepted, coverage, precision và summary review | Không mở serving; trạng thái đúng là chờ human review GraphRAG |
| Late Interaction | Artifact có `query_families`, hard-negative coverage và gate không cho giảm Recall@10 theo family | Giữ `late-v2`, không tạo index mới, flag mặc định tắt |
| Controlled-demo gate | `scripts/eval/failure_family_gate.py`, `scripts/eval/verify_failure_family_rollback.py` | Hoàn thành phân biệt accepted/rejected/inconclusive, pair provenance, budget và composer rollback commit-pinned |

Các regression seed đã được đóng gói trong `data/failure_family_eval_v1/` cho bốn họ lỗi quan sát trực tiếp:

- `EVIDENCE_POLICY_ERROR` từ `demo-case-037`.
- `CALCULATION_PLAN_ERROR` từ `demo-case-013`.
- `MULTI_INTENT_COVERAGE_ERROR` từ `demo-case-023`.
- `GRAPH_RELATION_ERROR` từ `demo-case-032`, cùng unit regression cho relation lịch sử `RELATED_COMPONENT`.

Mỗi bộ có đúng bốn development variant và hai holdout variant. Recipe deterministic chỉ biến đổi câu hỏi/format đã được khai báo. Các mutation có thể đổi policy như RBAC, lifecycle, graph relation, empty retrieval hoặc negation bắt buộc người viết cung cấp `expected_policy` mới; compiler không tự suy ground truth.

### 7.2 Chưa được phép đánh dấu accepted

Các thay đổi trên mới chứng minh implementation và fixture contract, chưa chứng minh chất lượng live trên collection chính. Còn phải:

1. Chạy `failure_family_gate.py` trên bốn mutation pack đã compile.
2. Lấy independent human review tối thiểu 20 graph edge; chỉ khi precision ít nhất 95% mới chạy Community Summary generation/review.
3. Bổ sung và review đủ 10 câu hỏi phức hợp cho Query Decomposition.
4. Chạy lại CRAG khi có matched samples/provider window ổn định để xử lý latency gate.
5. Tạo `milestone-decision-v2` theo kết quả thật. Provider failure phải ghi `inconclusive`; quality/safety failure phải ghi `rejected`; không bật flag chỉ vì unit test xanh.

`late-v2` tiếp tục là `rejected` cho default reranker trong controlled demo hiện tại cho đến khi benchmark mới chứng minh đủ hard-negative coverage, nDCG@10 gain, Recall@10 không giảm theo family và leakage bằng 0.

### 7.3 Tóm tắt công việc đã làm và phần chờ review

Đã làm: đóng AnswerDecision fail-closed; bổ sung exact Decimal và document-scoped Grounded Math; thêm source evidence thật cho Graph edge qua migration V0038; khôi phục/ingest/preflight fixture cho CRAG, Grounded Math, Decomposition và GraphRAG; rollback CRAG giờ ép runtime flags về false; compile đủ bốn mutation pack; chạy lại Grounded Math matched gate trên commit `085f3f3` đạt 16/16 sau disambiguation/citation merge; xuất Graph review queue 20 edge có evidence; sửa generator Graph để governance contract hợp lệ và deterministic seed không còn synthetic provenance.

Chưa làm hoặc chưa được phép bật: CRAG còn fail latency gate; Query Decomposition còn fail quality gate 1/8; GraphRAG còn thiếu independent review và relational gain; Community Summaries chưa mở; Late Interaction vẫn rejected; chưa chạy failure-family gate và integration matrix cuối; chưa có quyết định accepted cho controlled demo.

Chờ bạn review: file review queue Graph 20 edge, kết quả Grounded Math 16/16, các mutation pack và các artifact rollout trong `reports/controlled-demo/`. Sau khi bạn chốt nhãn Graph và 10 câu hỏi Decomposition, mình sẽ chạy lại gate và cập nhật decision artifact.
