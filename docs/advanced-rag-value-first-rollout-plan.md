# Kế hoạch triển khai Advanced RAG theo hướng value-first

## Tóm tắt

- Giữ runtime LAN chính `d1d38dd` ở `all_off`; RC `b437b32` chỉ được phép chạy controlled-demo Math-only pilot, chưa được phép bật trên default rollout.
- Tách Grounded Math, Query Decomposition, Graph Retrieval và CRAG + Claim Repair thành các capability được đánh giá, pilot và quyết định độc lập.
- Phát hành dần: tính năng đạt không phải chờ tính năng khác; tính năng chưa đạt tiếp tục OFF.
- Mỗi pilot chạy trên Windows/LAN riêng trong tối thiểu 7 ngày và đủ 100 request đúng nhóm.
- Theo đến cùng bốn tính năng chính: Grounded Math, Query Decomposition, Graph Retrieval và CRAG + Claim Repair. Chỉ dừng khi `accepted` hoặc chứng minh kỹ thuật rằng muốn tiến xa hơn phải phá ngưỡng đã khóa.
- Community Summaries chỉ bắt đầu sau Graph accepted. Late Interaction giữ OFF vô thời hạn.

## Checkpoint thực thi — 2026-08-05

- Phase 0 đã hoàn tất; Phase 1 đã hoàn tất phần tạo/reconcile disposable target và chuyển web/app pilot sang RC sạch `b437b32`. Hai target được owner duyệt vẫn được giữ, không auto-cleanup.
- Grounded Math đã đạt ba math-only matched pair được giữ cho series (`pair-01`, `pair-03`, `pair-05`) và series guardrail `production_eligible=true` trên exact commit `b437b3240cbd5714e049b6e481e7553d9f342ed6`; candidate chỉ bật Grounded Math.
- Full review contract thay đổi do hai baseline-only case, nhưng candidate review surface của đúng 10 case không đổi; nhãn 10/10 của `bao.nguyen` được reuse fail-closed cho controlled-demo decision. Việc reuse này không thay thế human review 20 case của pilot.
- Giữ tombstone provider smoke đầu tiên 0/5 `ExternalProcessingDenied`. Recovery smoke được khai báo lúc `2026-08-05T03:20:44Z` vẫn 0/5 do ProxyLLM HTTP 503 `no_capacity`; proof attempt `20260805-2` sau đó có một generation thành công, 0 retry, nhưng chỉ là provider smoke và không có `grounded_math_generation`.
- Single-owner governance đã được `bao.nguyen` xác nhận với `risk_accepted=true`, ký ba vai trò `rag`, `security_qa`, `operations` cho controlled-demo Math-only pilot.
- Corpus-expanded restore reconciliation receipt đã pass; serving commit giữ `b437b32` để dùng exact controlled-demo bundle đã duyệt, ingestion RC là `ab25cec`. Post-publish snapshot fingerprint là `26284b5f75c101132e673afb10db9ff10fdf1b76f00d8d93bff35da2cbe66f3d`, restore evidence hash là `f4a55bdfd3f7efe393e6a024c02436a43376d54aa2052c5d8fb702560b33c59d`.
- RAG pilot `8200` đang `status=ok`, deployment `windows-lan-pilot-math-7pdf-b437b32`, selective bundle chỉ có `RAG_GROUNDED_MATH_ENABLED=true`; control `8210` đang `status=ok/all_off`, deployment `windows-lan-control-all-off-7pdf-b437b32`. Cả hai dùng SQL `Mech_Chatbot_DB_RestoreTest_RAGPilot_20260804_b437b32`, Qdrant `TaiLieuKyThuat_v2_RestoreTest_RAGPilot_20260804_b437b32` và khớp runtime identity `4a96bfc...` / `46cb2b72...`. App `8180` đang `status=ok` và vẫn bind `RAG_SERVER_URL=http://127.0.0.1:8200`. Default rollout không đổi.
- Window v3 bị gián đoạn khi pilot/control không còn listener; trace vẫn rỗng, `eligible_trace_count=0`. V3 đã được đóng fail-closed bằng `.local/math-pilot-b437b32-live-v3/window-interruption.json`, không chuyển trace hoặc thời gian sang attempt mới.
- Window v4 được owner đóng làm tombstone để chọn corpus-expanded pilot; trace rỗng, không chuyển thời gian hoặc request. Window 7 PDF mới bắt đầu lúc `2026-08-04T09:08:38.4917915Z`, mốc tối thiểu `2026-08-11T09:08:38.4917915Z`, artifact `.local/math-pilot-7pdf-b437b32-live-v1/`, trace `logs/math-pilot-7pdf-b437b32-20260804-0911-7pdf/pilot-trace.jsonl`.
- Proof declaration đã ký metadata-only và proof batch chọn 5 document-aggregate card hợp lệ. Request đầu tiên đi qua production UI nhưng ProxyLLM generation trả HTTP 503 `no_capacity` cả ba attempt nội bộ; Jina rerank thành công, request không eligible, gate vẫn `0/100`. Proof được dừng fail-closed, giữ tombstone `proof-run-20260804.json`; không retry hoặc gửi bốn card còn lại trong attempt này.
- Hai attempt ngày `2026-08-05` được tách riêng: attempt `-1` giữ tombstone 503; attempt `-2` xác nhận provider hồi phục bằng đúng một request UI, nhưng `eligible_trace_count=0`, `count_proof_toward_pilot=false` và `proof_batch_excluded=true`. Smoke này không thay thế proof batch 5 request và không được cộng vào pilot.
- Automation `math-pilot-daily-review` chạy hằng ngày lúc 09:00, đã chuyển sang window/health/state/trace 7 PDF; automation chỉ monitor metadata, không tạo traffic hoặc tự retry proof.
- Cả 7 PDF đã `approved/published/servable`, target giữ `281` Qdrant point; publication artifact SHA-256 `643ef9742ec03bcc5a532cf23ef761ae6fde83e053daca0b62eb0ef194a04e15`. Chưa publish release/bật Grounded Math trên default rollout vì window mới chưa đủ 7 ngày/100 eligible request/human review và `release_decisions.json` còn `incomplete`.
- `live_authorized=true` trong health hiện chỉ xác nhận bundle được phép ở `activation_scope=controlled_demo`; nó không authorize default rollout. `release_decisions.json` vẫn `incomplete`; chưa có Advanced RAG feature nào được phép bật trên default rollout.

## Tiến độ theo phase

- Phase 0 — hoàn tất governance/selective activation và baseline foundation.
- Phase 1 — hoàn tất disposable target, restore reconciliation, Math-only pilot/control runtime và collector/gate metadata-only.
- Phase 2 — đang ở bước LAN pilot Grounded Math:
  - [x] Ba current-commit formal pair và series guardrail.
  - [x] So sánh review contract, reuse đúng 10 candidate labels không đổi và accepted controlled-demo decision.
  - [x] Chuyển web/app sang Math-only RC, xác minh runtime/rollback binding và traffic thật chỉ đếm `grounded_math_generation`.
  - [x] Loại trace/runtime drift cũ, restart pilot/control từ detached checkout sạch và mở collector v3 với exact runtime identity.
  - [x] Đóng v3/v4 làm tombstone, chuyển pilot sang exact target 7 PDF, xác minh health/runtime/fingerprint và mở window mới sạch.
  - [x] Ký proof declaration; chạy request đầu tiên qua production UI và dừng fail-closed khi ProxyLLM trả HTTP 503 `no_capacity`.
  - [x] Xác nhận provider hồi phục bằng một provider smoke riêng, không retry và không tính vào pilot.
  - [ ] Mở proof attempt mới có predeclaration và chạy đủ 5 request; chỉ khi proof pass mới ký owner declaration cho full campaign.
  - [ ] Giữ runtime ổn định tối thiểu đến `2026-08-11T09:08:38.4917915Z` và thu đủ 100 eligible calculation request mới trong window 7 PDF.
  - [ ] Human review 20 case phân tầng: 10 primary labels bởi `bao.nguyen`, 10 bởi `tran.nghi`; mọi failure/low-confidence case được cả hai review.
  - [ ] Nếu pilot pass, chạy interaction matrix Math-only trên final RC ở concurrency 1 và 5, technical review bởi `tran.nghi`, rồi mới tạo default-rollout ledger/bundle để owner quyết định release Math.
- Phase 3 — chưa bắt đầu Query Decomposition diagnostic trên RC mới; có thể triển khai telemetry metadata-only song song trong khi chờ đủ thời gian/request của Math pilot, nhưng chưa mở formal window.
- Phase 4 — chưa bắt đầu Graph Retrieval current-commit cycle.
- Phase 5 — chưa bắt đầu CRAG + Claim Repair current-commit diagnostic.
- Phase 6 — Community Summaries tiếp tục OFF; Late Interaction tiếp tục `rejected/OFF`.

## Thay đổi activation contract

### Selective activation

Bổ sung chế độ `selective` vào contract hiện tại thay vì tạo mọi tổ hợp profile:

- Bundle vẫn dùng schema `rag-activation-bundle-v1`; trường `feature_flags` là tập flag chính xác được yêu cầu.
- CLI build bundle nhận `--profile selective` và `--enable-feature <RAG_*_ENABLED>` lặp lại.
- Renderer đọc flag trực tiếp từ bundle đã hash, không tin flag nhập thủ công.
- Runtime nhận `RAG_ACTIVATION_PROFILE=selective`; bundle, ledger, commit và chữ ký phải khớp chính xác.
- `all_off` giữ nguyên: không cần bundle hoặc chữ ký.
- Các profile cũ được giữ để tương thích, nhưng rollout mới dùng `selective`.

Các invariant fail-closed:

- CRAG và Claim Repair luôn cùng ON hoặc cùng OFF.
- Late Interaction luôn bị từ chối.
- Community Summaries chỉ được ON khi Graph Retrieval ON.
- Grounded Math, Query Decomposition, Graph Retrieval và cặp CRAG/Claim Repair không còn phụ thuộc nhau.
- Mọi flag ON phải có decision `accepted`; accepted set trong ledger phải bằng đúng enabled set trong bundle.
- Default rollout có feature ON vẫn bắt buộc chữ ký Ed25519 trên exact ledger bytes.

Cập nhật ba nguồn contract chính:

- [feature_activation.py](C:/Users/bao.nguyen/Documents/ChatBotProject/src/mech_chatbot/governance/feature_activation.py)
- [milestones.py](C:/Users/bao.nguyen/Documents/ChatBotProject/scripts/controlled_demo_eval/milestones.py)
- [matrix.json](C:/Users/bao.nguyen/Documents/ChatBotProject/data/integrated_hardening_v1/matrix.json)

Evaluation dependencies mới:

- Grounded Math, Query Decomposition, Graph Retrieval và CRAG chỉ phụ thuộc evaluation foundation.
- Community Summaries phụ thuộc Graph Retrieval.
- Query-only không được dùng CRAG branch correction; đây là một variant mới và phải có evidence riêng.
- Matrix chuyển thành `integrated-v3-selective`, sinh accepted stack từ release decisions thay vì chuỗi cumulative cố định.

Không làm admin UI để bật/tắt. Mọi thay đổi live vẫn qua bundle được duyệt, restart process và health verification.

## Các phase triển khai

### Phase 0 — Khóa governance và baseline mới

1. Viết test RED cho selective activation:
   - Math-only, Query-only, Graph-only và CRAG/Claim-only hợp lệ khi đủ evidence.
   - CRAG lệch Claim Repair, Late ON, Community không có Graph đều fail.
   - Flag rejected/keep_off không thể xuất hiện trong bundle.
   - Sai commit, schema, hash, signature hoặc bundle flags đều fail.
   - `all_off` vẫn hoạt động không cần bundle.
2. Implement thay đổi tối thiểu ở shared activation path.
3. Chuyển runners sang baseline độc lập:
   - Math: `all_off → math-only`.
   - Query: `all_off → query-only`.
   - Graph: `all_off → graph-only`.
   - CRAG: `all_off → CRAG + Claim Repair`.
   - Community: `graph-only → graph + community`.
4. Chạy targeted tests, full backend/frontend/build/security và coverage tối thiểu 80%.
5. Freeze commit governance mới.
6. Recapture all-off runtime, browser smoke, c1/c5 và rollback. Mọi evidence cũ chỉ còn giá trị lịch sử.

Không feature nào được bật trong phase này.

### Phase 1 — LAN pilot riêng

Tạo một cặp disposable target cho release train sau khi owner duyệt exact names:

- SQL: `Mech_Chatbot_DB_RestoreTest_RAGPilot_<YYYYMMDD>_<sha7>`.
- Qdrant: `TaiLieuKyThuat_v2_RestoreTest_RAGPilot_<YYYYMMDD>_<sha7>`.

Runtime pilot:

- Web: `0.0.0.0:8180`.
- RAG: `127.0.0.1:8200`.
- Runtime LAN chính tiếp tục dùng profile đã release trước đó; ban đầu là `all_off`.
- Không `WITH REPLACE`, không drop, không auto-cleanup.
- Có thể reuse hai target trong cùng release train nếu fingerprint dữ liệu không đổi; mỗi RC phải có read-only reconciliation receipt mới.
- Nếu dữ liệu hoặc Graph fingerprint thay đổi, cần owner duyệt target mới.

Mỗi pilot chỉ chạy một feature độc lập trước khi chạy accepted stack.

### Phase 2 — Grounded Math

Trạng thái ban đầu: lợi ích đã rõ; chưa cần tối ưu thuật toán.

1. Chạy lại math-only trên current commit:
   - Ba matched pair độc lập.
   - Candidate thêm duy nhất Grounded Math.
   - Exact calculation, formula, unit, citation và provenance đạt 100%.
   - Không unsupported number; tối đa một calculation.
   - Leakage bằng 0; latency ratio `<=1.25`; cost ratio `<=1.5`.
2. So sánh review contract với pack 10/10 hiện có:
   - Chỉ reuse nhãn khi immutable case và `review_contract_sha256` không đổi.
   - Case hoặc candidate output thay đổi phải review lại.
3. Chạy LAN pilot math-only:
   - Tối thiểu 7 ngày và 100 request có calculation route.
   - Automated safety/citation/provenance check đủ 100.
   - Human review 20 case phân tầng và mọi failure.
4. Nếu đạt, tạo release Math:
   - Main stack chuyển `all_off → {Grounded Math}`.
   - Rollback thông thường về `all_off`.

Chỉ sửa code nếu math-only recapture phát hiện lỗi deterministic ở shared calculation/provenance path. Không tối ưu thêm khi các gate vẫn xanh.

### Phase 3 — Query Decomposition

Trạng thái ban đầu: chất lượng tốt nhưng cost từng đạt `1.554088 > 1.5`.

1. Thêm telemetry metadata-only để chia token/cost thành:
   - planner;
   - từng branch retrieval/correction;
   - context cuối;
   - final generation.
2. Chạy diagnostic, không dùng làm formal evidence.
3. Áp dụng root fix theo thứ tự:
   - Nếu duplicate source/context chiếm phần lớn overhead: dedupe theo canonical source identity trước final context, nhưng giữ đủ citation cho từng branch.
   - Nếu shared instruction bị lặp: đưa phần chung ra khỏi từng branch.
   - Planner chỉ được gọi khi deterministic splitter không bao phủ đủ intent; câu đơn giản không gọi planner.
   - Nếu ba nguyên nhân trên không giải thích overhead, mở design investigation cho split-generation/merge; không sửa ngưỡng.
4. Diagnostic target là cost `<=1.35` để có margin; formal gate vẫn giữ `<=1.5`.
5. Freeze commit và chạy ba formal pair query-only:
   - Complex-answer gain `>=10%`.
   - Simple planner call bằng 0.
   - Branch và branch-citation accuracy 100%.
   - Tối đa ba subquery, một correction và một final generation.
   - Latency/cost `<=1.5`; không leakage, retry hoặc wrong-answer regression.
6. Human review 10-case current-contract pack.
7. LAN pilot query-only đủ 7 ngày/100 eligible requests.
8. Nếu đạt:
   - Chạy interaction matrix Math-only, Query-only và Math+Query.
   - Release accepted stack `{Math, Query}`; nếu Math chưa đạt thì release `{Query}`.
   - Rollback Query về stack accepted trước đó.

Mỗi formal failure được giữ làm tombstone. Vòng tiếp theo phải có design delta cụ thể, test RED và declaration mới.

### Phase 4 — Graph Retrieval

Trạng thái ban đầu: edge/provenance tốt nhưng khả năng tạo câu trả lời relational chưa đủ thuyết phục.

1. Giữ lại 21/21 review labels chỉ khi source queue hash và immutable edge content không đổi.
2. Khóa tối thiểu 10 relational cases thuộc ba domain Technical, Production và Maintenance, bao phủ:
   - version;
   - supersedes;
   - contains part;
   - uses material;
   - applies to.
3. Instrument từng seam: route → seed → traversal → hydration → final answer.
4. TDD sửa seam đầu tiên làm relation đúng nhưng answer/claim/citation sai; không thêm edge giả hoặc nới provenance.
5. Chạy ba graph-only matched pair:
   - Relational answer gain `>=10%`.
   - Reviewed precision `>=95%`.
   - Provenance/citation 100%.
   - Structured coverage `>=80%`.
   - Pending/stale serving bằng 0.
   - Tối đa hai hop và 50 edge.
   - Local/non-relational quality không giảm.
   - Latency và cost `<=1.5`.
6. LAN pilot graph-only đủ 7 ngày/100 routed relational requests.
7. Nếu đạt:
   - Chạy single, pairwise và full-stack matrix với Math/Query đã accepted.
   - Thêm Graph vào accepted stack.
   - Rollback Graph về stack accepted trước đó.

Tiếp tục vòng diagnose → TDD root fix → RC mới → formal window cho đến khi accepted hoặc feasibility review chứng minh không thể đạt answer gain mà vẫn giữ provenance/RBAC/budget.

### Phase 5 — CRAG + Claim Repair

CRAG không còn chặn Math, Query hoặc Graph.

1. Chạy interleaved diagnostic tách riêng:
   - retrieval;
   - rerank;
   - correction;
   - generation;
   - claim repair.
2. Không sửa code khi delta vẫn do provider variance.
3. Root-fix decision:
   - Rerank overhead: reuse kết quả không đổi và giảm candidate duplication, không giảm recall.
   - Generation/context overhead: dedupe corrected context, vẫn chỉ một final generation.
   - Correction overhead: tối ưu shared correction path nếu phần CRAG-controlled thực sự chi phối.
   - Provider variance: tiếp tục diagnostic với declaration mới; không chọn rerun đẹp.
4. Chỉ mở formal window khi diagnostic dự báo latency ratio `<=1.25`.
5. Ba formal pair phải đạt:
   - Candidate vượt baseline trên failure case và tất cả case đạt.
   - Correction và repair đều được exercise.
   - Mỗi loại tối đa một lần.
   - Wrong-refusal giảm; wrong-answer không tăng.
   - Leakage/provider error ngoài contract bằng 0.
   - Latency `<=1.25`, cost `<=1.5`.
6. LAN pilot chỉ đếm request thực sự đi vào correction/repair route; đủ 7 ngày/100 eligible requests.
7. Khi accepted, thêm cả hai flag vào accepted stack và chạy lại pairwise/full-stack matrix.

CRAG được tiếp tục qua nhiều design iteration, nhưng mỗi iteration phải có nguyên nhân mới hoặc thay đổi kỹ thuật đo được.

### Phase 6 — Community Summaries và Late Interaction

Community Summaries:

- Giữ OFF cho đến khi Graph accepted.
- Sau đó khóa graph fingerprint, generate/review summary và chạy ít nhất 10 global cases.
- Global gain `>=10%`; local/relational quality không giảm; citation/provenance đầy đủ; không stale/pending serving; latency/cost `<=1.5`.
- Nếu technical gate đạt, chạy pilot 7 ngày/100 global requests rồi mới thêm vào stack.
- Nếu lần đánh giá chuẩn đầu tiên fail, giữ OFF và đưa lại owner ưu tiên; Community không nằm trong nhóm “theo đến cùng”.

Late Interaction:

- Giữ `rejected` và OFF.
- Không sửa/tối ưu implementation hiện tại.
- Chỉ mở lại bằng design mới có giả thuyết retrieval cụ thể và predeclared NDCG window; không nới NDCG/recall/latency/storage gate.

## Progressive release và bật/tắt

Mỗi feature đi qua cùng lifecycle:

1. Diagnostic.
2. TDD root fix nếu có lỗi code-controlled.
3. Freeze một RC sạch.
4. Provider smoke 5/5, không lỗi/retry/fallback ngoài contract.
5. Ba formal pair và stop-on-first-failure.
6. Human review pack.
7. Controlled-demo bundle cho LAN pilot riêng.
8. Pilot đủ 7 ngày và 100 eligible requests.
9. Integrated interaction matrix.
10. `tran.nghi` review kỹ thuật.
11. `bao.nguyen` chấp nhận hoặc giữ OFF.
12. Default-rollout ledger được ký và tạo selective activation bundle.
13. Restart runtime LAN chính và xác minh health/browser/c1/c5.

Accepted stack sau mỗi release là hợp của mọi feature đã accepted. Feature chưa đạt không xuất hiện trong bundle.

Rollback có hai mức:

- Feature rollback: tạo bundle mới bằng accepted stack trước release, restart và smoke.
- Emergency rollback: xóa toàn bộ governed flags, chạy `all_off` không cần bundle.

Bất kỳ lỗi security, cross-scope leakage, bundle/commit drift hoặc rollback failure nào đều chuyển thẳng về `all_off`.

## Pilot review và acceptance

Mỗi pilot:

- Chạy đủ 7 ngày và 100 request đúng route, lấy điều kiện hoàn thành sau.
- Automated checks đủ 100 request: runtime identity, security, citation structure, provenance, budgets, provider errors và leakage.
- Quality gain vẫn lấy từ matched formal evaluation, không suy diễn từ organic traffic không có oracle.
- Human review 20 case phân tầng:
  - 10 primary labels bởi `bao.nguyen`.
  - 10 primary labels bởi `tran.nghi`.
  - Mọi failure, access-denied bất thường hoặc low-confidence case được cả hai review.
- Không ghi raw document, credential hoặc raw private response vào artifact.

Pilot bị dừng ngay khi:

- Có leakage hoặc RBAC/site/security violation.
- Citation trỏ sai nguồn hoặc stale/pending data được serve.
- Runtime SHA/snapshot/provider/bundle drift.
- Provider fallback ngoài declaration.
- Rollback không đưa runtime về previous stack hoặc `all_off`.

Quality/budget failure làm design hiện tại `rejected`; provider outage làm window `inconclusive`.

## Interaction matrix và kiểm thử

Trên mỗi final RC, chạy tại concurrency 1 và 5:

- `all_off`;
- từng accepted feature riêng;
- mọi pair có liên quan tới feature mới;
- full accepted stack;
- Graph+Community khi Community được xét.

Kiểm tra:

- Backend unit/integration/eval và coverage line/branch tối thiểu 80%.
- Frontend unit, production build và browser E2E.
- Login, SSE, citation, access denied và CSRF/session failures.
- Strict streaming, cache namespace isolation và request budgets.
- SQL/Qdrant fingerprint, restore receipt và rollback.
- Zero leakage; không severe wrong-answer.
- Commit, deployment ID, snapshot, provider hash, collection và bundle hash ổn định trước/sau c1/c5.
- Không migration/data cleanup ngoài disposable scope.

Sau bất kỳ code change nào:

- Viết regression test trước.
- Freeze commit mới.
- Vô hiệu hóa formal evidence khác commit.
- Không overwrite/rerun tombstone để chọn kết quả đẹp.

## Điều kiện hoàn tất roadmap

Bốn tính năng chính chỉ kết thúc ở một trong hai trạng thái:

- `accepted`: formal gate, pilot, review, security, interaction matrix và rollback đều đạt.
- `keep_off_technical_limit`: có feasibility report chứng minh muốn tiếp tục phải phá ngưỡng latency/cost/safety/RBAC đã khóa hoặc thay đổi provider/corpus/product scope; `tran.nghi` review và `bao.nguyen` chấp nhận disposition.

Decision pack cuối phải phân biệt `implemented / measured / reviewed / pilot-authorized / live-authorized`, có disposition cho cả sáu tính năng và chỉ accepted stack được ký để bật.

## Giả định đã khóa

- Mục tiêu là Windows/LAN nội bộ, không Docker và không public deploy.
- Jina vẫn là reranker chính; Voyage fallback một lần rồi deterministic local fusion.
- Cả ba nhóm BOM/math, multi-intent và relational query đều có nhu cầu thực tế.
- `bao.nguyen` là release owner; `tran.nghi` là technical reviewer độc lập.
- Không có UI toggle cho người dùng hoặc admin.
- Threshold hiện hành không được nới.
- Không tạo/xóa account; reuse cohort nội bộ hiện có.
- Không cleanup disposable targets tự động.
- Không push hoặc publish tracker/PR nếu chưa có phê duyệt riêng.
