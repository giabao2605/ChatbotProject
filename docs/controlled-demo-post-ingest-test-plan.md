# Kế hoạch kiểm thử từng tính năng sau ingest

## 1. Phạm vi đã khóa

Kế hoạch này chỉ áp dụng cho controlled demo 5–10 người, không phải default rollout.

- Commit khởi đầu: `8087fdd`.
- Collection: `TaiLieuKyThuat_v2`.
- Site live: `HQ` và `BRANCH-B`.
- Source system live: `upload`.
- Manifest nguồn: `controlled-demo-v2/evaluation_questions.jsonl` gồm 44 case.
- Mapping bắt buộc: `DEMO-HQ -> HQ`, `DEMO-BRANCH-B -> BRANCH-B`, `controlled-demo-v2 -> upload`.
- Preflight hiện tại: 44/44 case, 18/18 tài liệu được tham chiếu, 0 failure.
- Trong suốt một baseline/candidate pair, không ingest, sửa metadata, publish, xóa hoặc đổi current version.

`technical_demo_process_expired_v0.md` không được 44 case tham chiếu. Thiếu lifecycle transition thật cho version 0 được ghi là coverage gap riêng và không làm sai kết quả của 44 case hiện tại.

## 2. Bộ case và mức bằng chứng hiện có

| Nhóm | Số case | Milestone dùng |
| --- | ---: | --- |
| `factual` | 12 | CRAG |
| `insufficient_evidence` | 3 | CRAG/refusal |
| `access_denied` | 3 | CRAG/governance |
| `grounded_math` | 10 | Grounded Math |
| `complex` | 9 | Query Decomposition |
| `graphrag` | 6 | GraphRAG |
| `global` | 1 | Community Summaries |

Ngưỡng roadmap cao hơn số case hiện có ở CRAG, decomposition, GraphRAG và community. Vì vậy:

- Kết quả an toàn/chất lượng vẫn được đo và báo cáo.
- Milestone thiếu số lượng hoặc human review tối thiểu chỉ được kết luận `inconclusive`, không tự nâng thành `accepted`.
- `rejected` được dùng khi provider hoạt động nhưng chất lượng hoặc safety gate không đạt.
- `accepted` chỉ được dùng khi đủ cả metric, số lượng case và reviewer sign-off.

## 3. Trình tự chạy

### 3.1 Khóa snapshot

1. Lưu commit SHA, manifest SHA-256, fixture alias SHA-256 và preflight fingerprint.
2. Chia manifest thành các file theo `evaluation_group`; không sửa nội dung case nguồn.
3. Chạy lại preflight trên toàn bộ 44 case trước request LLM đầu tiên.
4. Chạy provider smoke đúng 5 request. Nếu có `503/no_capacity`, milestone cần provider được ghi `inconclusive` và không bị tính thành quality failure.

### 3.2 CRAG

- Baseline: `RAG_CRAG_ENABLED=false`, `RAG_CLAIM_REPAIR_ENABLED=false`.
- Candidate: bật đúng hai flag trên.
- Các feature P1 khác giữ tắt để đo đúng delta CRAG.
- Dùng nhóm factual, insufficient-evidence và access-denied.
- Gate: leakage bằng 0, wrong-answer không tăng, wrong-refusal giảm hoặc giữ 0, latency/cost/correction/repair/retry trong budget.
- Vì hiện có 18 thay vì 20 matched pair, kết quả tốt nhất trước human bổ sung là `inconclusive`.

### 3.3 Grounded Math

- Baseline/candidate dùng cùng snapshot và 10 case `grounded_math`.
- Candidate chỉ thêm `RAG_GROUNDED_MATH_ENABLED=true`.
- Kiểm tra Decimal, formula, unit, provenance, citation, partial answer và rollback.
- Tạo review pack cho toàn bộ 10 kết quả; chủ dự án review trước quyết định cuối.

### 3.4 Late Interaction

- Không chạy candidate `late-v2` lại trong vòng này vì đã có quyết định `rejected`.
- Integrated matrix phải giữ flag tắt và xác nhận Voyage/local fallback hoạt động.

### 3.5 Query Decomposition

- Dùng 9 case `complex` hiện có.
- Baseline tắt decomposition; candidate chỉ bật `RAG_QUERY_DECOMPOSITION_ENABLED`.
- Kiểm tra câu đơn giản không gọi planner, tối đa ba subquery, một correction budget chung, leakage và wrong-answer.
- Roadmap yêu cầu 10 câu phức hợp; với 9 case, quyết định tối đa là `inconclusive` cho đến khi bổ sung một case hợp lệ.

### 3.6 GraphRAG

1. Export review pack tối thiểu 20 edge/proposal kèm evidence cần thiết.
2. Người review độc lập gắn nhãn approve/reject; không dùng nhãn tự sinh làm ground truth.
3. Chỉ sau khi review precision đạt ít nhất 95% mới chạy 6 case `graphrag`.
4. Do roadmap yêu cầu 10 relational query, 6 case hiện tại chưa đủ cho `accepted`.

### 3.7 Community Summaries

- Chỉ chạy nếu GraphRAG đã có human review hợp lệ.
- Hiện chỉ có một case `global`, dưới ngưỡng 10 query và 5 summary; nếu chưa bổ sung thì ghi `inconclusive`, không bật serving.

### 3.8 Integrated matrix

- Dùng quyết định mới của từng milestone.
- Feature `rejected` hoặc `inconclusive` phải giữ flag tắt và được kiểm tra fallback/rollback.
- Chạy concurrency 1 và 5; concurrency 5 là gate controlled demo.
- Không đổi production/default-rollout ledger.

## 4. Phân công

### Codex thực hiện

- Sinh manifest theo nhóm, snapshot và hash.
- Chạy provider smoke, preflight, baseline/candidate, gate và rollback test.
- Không ghi raw prompt, raw document, credential hoặc secret vào tracked artifact.
- Tạo review pack và báo cáo `accepted`/`rejected`/`inconclusive` có lý do.
- Cập nhật roadmap, chạy full test, code-review và commit trên branch hiện tại.

### Chủ dự án thực hiện

- Không thay đổi corpus trong lúc một pair đang chạy.
- Review toàn bộ 10 kết quả Grounded Math.
- Review các CRAG case rủi ro cao và ký decision artifact.
- Cung cấp nhãn độc lập cho tối thiểu 20 GraphRAG edge/proposal.
- Review 5 community summary nếu GraphRAG đủ điều kiện.
- Nếu muốn đủ điều kiện hai người dùng thật, tổ chức hai tài khoản/người thử; không gửi mật khẩu vào artifact.

## 5. Điều kiện dừng và báo cáo

Dừng candidate của milestone nếu phát hiện leakage, governance escape, corpus thay đổi giữa hai arm hoặc artifact không khớp commit/hash. Provider capacity failure tạo quyết định `inconclusive`; provider ổn nhưng quality gate fail tạo `rejected`.

Báo cáo cuối phải chứa:

- Commit, timestamp, manifest/snapshot/provider hashes.
- Metric baseline và candidate theo từng milestone.
- Leakage, wrong-answer, wrong-refusal, citation/claim/branch accuracy, P95, cost và retry.
- Kết quả rollback/fallback.
- Human-review checkpoint còn thiếu.
- Decision artifact `milestone-decision-v2` cho 2.3–2.9 hoặc trạng thái phụ thuộc hợp lệ.
- Phân biệt rõ `ready_for_demo_matrix` với `ready_for_live_matrix`.

## 6. Lệnh thực thi chuẩn

Các lệnh dưới đây chạy từ root repo bằng `chat_env`. Mỗi run dùng một output directory mới; runner từ chối ghi đè artifact cũ.

```powershell
$env:PYTHONPATH = "src"
$env:CONTROLLED_DEMO_LIVE_OPT_IN = "1"
$env:CONTROLLED_DEMO_FIXTURE_ALIASES = "C:\path\to\controlled-demo-v2\fixture_aliases.json"
$manifest = "C:\path\to\controlled-demo-v2\evaluation_questions.jsonl"
$run = "reports\controlled-demo\<run-id>"

.\chat_env\Scripts\python.exe -m scripts.controlled_demo_eval.prepare `
  --manifest $manifest `
  --output-dir "$run\manifests"

.\chat_env\Scripts\python.exe -m scripts.controlled_demo_eval.preflight `
  --manifest $manifest `
  --output "$run\preflight.json"

.\chat_env\Scripts\python.exe -m scripts.eval.provider_smoke `
  --output "$run\provider-smoke.json"

.\chat_env\Scripts\python.exe -m scripts.controlled_demo_eval.run_feature_pair crag `
  --manifest "$run\manifests\crag.jsonl" `
  --output-dir "$run\crag" `
  --full-preflight-artifact "$run\preflight.json" `
  --provider-smoke-artifact "$run\provider-smoke.json" `
  --manifest-inventory-artifact "$run\manifests\inventory.json"

.\chat_env\Scripts\python.exe -m scripts.controlled_demo_eval.run_feature_pair grounded_math `
  --manifest "$run\manifests\grounded_math.jsonl" `
  --output-dir "$run\grounded-math" `
  --full-preflight-artifact "$run\preflight.json" `
  --provider-smoke-artifact "$run\provider-smoke.json" `
  --manifest-inventory-artifact "$run\manifests\inventory.json"

.\chat_env\Scripts\python.exe -m scripts.controlled_demo_eval.run_feature_pair query_decomposition `
  --manifest "$run\manifests\query_decomposition.jsonl" `
  --output-dir "$run\query-decomposition" `
  --full-preflight-artifact "$run\preflight.json" `
  --provider-smoke-artifact "$run\provider-smoke.json" `
  --manifest-inventory-artifact "$run\manifests\inventory.json"
```

Sau khi Codex đã tạo hai review pack và Graph review queue, chủ dự án chỉ sửa các
trường review được hướng dẫn trong từng file. Lệnh dưới đây kiểm tra toàn bộ nhãn,
khóa nội dung gốc bằng SHA-256 và xuất một báo cáo chỉ chứa metadata:

```powershell
.\chat_env\Scripts\python.exe -m scripts.controlled_demo_eval.finalize_reviews `
  --crag-pack "$run\review\crag-high-risk\pack.json" `
  --crag-review "$run\review\crag-high-risk\review.jsonl" `
  --grounded-math-pack "$run\review\grounded-math\pack.json" `
  --grounded-math-review "$run\review\grounded-math\review.jsonl" `
  --graph-source "reports\graph\20260715-130721\review-queue.jsonl" `
  --graph-review "$run\graph\review-queue.jsonl" `
  --review-anchor "data\integrated_hardening_v1\evidence\post-ingest-human-review-anchor-20260716.json" `
  --output "$run\review\finalization.json"
```

Exit code `2` nghĩa là review chưa đủ hoặc không hợp lệ, không phải lỗi runner.
Anchor được track trong repo khóa semantic hash của hai pack và raw SHA-256 của
Graph queue lịch sử; vì vậy không thể sửa đồng thời pack/source và review để vượt
validator. CLI từ chối ghi đè output cũ và không ghi raw question, answer, document, reviewer
name hoặc review note vào báo cáo. Kể cả khi Graph review đủ 20 edge và precision
đạt 95%, Community Summaries vẫn chưa tự mở; bước kế tiếp là chạy lại Graph quality
gate trên cùng evidence.

GraphRAG, Community Summaries và integrated matrix chưa tự chạy tiếp từ runner này
vì chúng phải đi qua human-review checkpoint và decision ledger trước. Không được bỏ
qua checkpoint bằng cách bật flag trực tiếp.
