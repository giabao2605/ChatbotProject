# Kế hoạch triển khai Advanced RAG theo hướng value-first

## Tóm tắt

- Giữ default rollout ở `all_off`; RC `7b9d575` chỉ được phép chạy controlled-demo Math-only pilot, chưa được phép bật mặc định.
- Tách Grounded Math, Query Decomposition, Graph Retrieval và CRAG + Claim Repair thành các capability được đánh giá, pilot và quyết định độc lập.
- Phát hành dần: tính năng đạt không phải chờ tính năng khác; tính năng chưa đạt tiếp tục OFF.
- Mỗi pilot chạy trên Windows/LAN riêng trong tối thiểu 7 ngày và đủ 100 request đúng nhóm.
- Theo đến cùng bốn tính năng chính: Grounded Math, Query Decomposition, Graph Retrieval và CRAG + Claim Repair. Chỉ dừng khi `accepted` hoặc chứng minh kỹ thuật rằng muốn tiến xa hơn phải phá ngưỡng đã khóa.
- Community Summaries chỉ bắt đầu sau Graph accepted. Late Interaction giữ OFF vô thời hạn.

## Checkpoint thực thi — 2026-08-08

- Phase 0 và Phase 1 đã hoàn tất. Runtime pilot/control hiện bind exact commit `7b9d57562a669984b843d48d6d7ddf09048c472d`; pilot chỉ bật Grounded Math, control giữ `all_off`.
- Grounded Math đã đạt ba current-commit formal pair, series guardrail `production_eligible=true`, formal review 16/16 và rollback/restore reconciliation trên detached checkout sạch.
- Single-owner governance đã được `bao.nguyen` ký cho `scope=controlled_demo`, `risk_accepted=true` và đủ ba signoff `rag`, `security_qa`, `operations`. Quyền này không áp dụng cho default rollout.
- Proof 5/5 và owner declaration cho full campaign đã hoàn tất. Window dừng ở `30/100` đã được tombstone, không carry-forward request hoặc downtime. Window thay thế sạch bắt đầu `2026-08-10T04:33:45.6530163Z`, mốc tối thiểu `2026-08-17T04:33:45.6530163Z` và bắt đầu lại ở `0/100` eligible calculation request.
- Snapshot window thay thế xác nhận runtime identity và app health hợp lệ trên exact commit `7b9d57562a669984b843d48d6d7ddf09048c472d`; app/pilot/control đang listen ở `8180/8200/8210`, pilot chỉ bật Grounded Math trong `controlled_demo`, control giữ `all_off`. Gate false tại `0/100` là trạng thái collecting fail-closed dự kiến; không phát sinh synthetic production traffic.
- Reviewer contract của pilot được chốt bằng `.local/math-pilot-7b9d575/review-contract-resolution.json`: `bao.nguyen` là human reviewer duy nhất cho đủ 20 case dưới signed `single_owner`; Codex chỉ chuẩn bị metadata và hỗ trợ kỹ thuật, không được tính là independent human reviewer. `pilot-window.json` được giữ nguyên vì đã bind SHA.
- Query Decomposition đã có telemetry metadata-only tách planner, từng retrieval/correction branch, final context và final generation; rollup cost được đánh dấu để không cộng hai lần. Diagnostic không-formal sạch gần nhất tại `reports/decomposition/20260808-diagnostic-7b9d575-dirty-02/diagnostic.json` chạy đủ 13+13 case, không có provider failure nhưng không đạt: cost ratio `1.899837 > 1.35`, pass tổng `2/13 → 7/13`, decomposition `7/10`, branch accuracy `86.67%`, citation accuracy `70%`. Root fix đã chặn trước final generation đối với câu hỏi high-risk có partial coverage do nhánh `grounded_negative`; evaluation subprocess hiện được cấp `all_external` trong đúng `evaluation` scope nên không còn local `ExternalProcessingDenied`. Diagnostic `-04` đã tới ProxyLLM nhưng bị dừng sau `19/19` generation call trả HTTP 503 `no_capacity` trên 9 baseline case; chưa chạy candidate và không đánh giá cost/quality. Vì vậy chưa có post-fix cost ratio hợp lệ, chưa mở formal window và Query vẫn OFF; `-01`, `-03` và `-04` đều được giữ làm tombstone.
- Query WIP đã qua review Standards/Spec, security boundary review và được freeze tại commit `c123c399817236b63e8dcfc57ff608feb2853ff1`. Diagnostic runner fail-closed nếu commit/manifest/worktree/runner/fixture drift hoặc baseline gặp provider outage; default rollout và Query vẫn OFF.
- Checkpoint Query `2026-08-10`: diagnostic đầu trên `498257f` thiếu `RUN_DECOMPOSITION_EVAL_FIXTURE=1` nên chỉ giữ declaration làm tombstone; diagnostic kế tiếp chạy đủ hai arm nhưng dừng vì `baseline decomposition cost does not reconcile`. Root cause là evaluator ưu tiên top-level `decomposition_usage` được snapshot trước stream thay vì bản current trong `debug.generation_metrics`; root fix tối thiểu đổi precedence, có regression test, đã qua Standards/Spec/security review và được freeze tại `a106befec6fc5dfbf29ad34cf8e89a36952ff1e5`. Relevant Query/CRAG suite pass; full unit suite chỉ còn failure strict-stream đã tồn tại ngoài scope.
- Diagnostic sạch `reports/decomposition/20260810-diagnostic-a106bef-01/diagnostic.json` trên exact commit `a106bef` đã pass mục tiêu hỗ trợ: cost ratio `1.319309 <= 1.35`, baseline/candidate cost `0.0196675 → 0.0259475`, final-generation call giữ `9 → 9`, cả hai arm `cost_reconciled=true`, provider failure bằng `0`; pass tổng `3/13 → 5/13`, decomposition `6/10`, branch accuracy `86.67%`, citation accuracy `65%`, budget violation và simple planner call bằng `0`. Diagnostic SHA-256 `2b10a3703bf9543c9e48ec5eecdc42b7c060eca3a07e11e0b6b5810801c090e0`; declaration SHA-256 `5a58743719d3c4e4c7213d68463c387c238d1e8fcf57f1ddf58d44f2901798fe`.
- Query formal pair 01 trên detached checkout sạch `a106bef` có rollback `28/28` và provider smoke `5/5`, `0` retry, nhưng fail đúng hai check `branch_accuracy_complete` và `branch_citations_complete`; mọi provenance/safety/budget/latency/cost check khác pass. Candidate đạt `5/13`, decomposition `6/10`, branch accuracy `86.67%`, citation accuracy `65%`; latency ratio `1.115647`, cost ratio `1.251994`. Run SHA-256 `96f5db971489b4065a5a448655ea52c8ce0b2a0f3aad5eb0cc3a5651f4bba529`, gate SHA-256 `d9fb97ab4aa0ef6cd1ab3faaaed92a68bbdfb6765031c48742876a22482a42d4`; pair 02/03 không chạy để tránh chọn rerun đẹp.
- Owner `bao.nguyen` đã duyệt phương án khuyến nghị cho Query manifest. Canonical generator nay freeze hai scope: Query-only `13` case (`10` complex + `3` simple), SHA-256 `6976cbbe4c9500b7c0755c5944775e326106a780bb2910bfa71167787a1d0bf8`, không còn phụ thuộc Grounded Math; và Math+Query interaction `3` BOM case, SHA-256 `d21495e86faca22c455f745a7b9fd7f249643f31e76f36e086f8af4467b0932b`, giữ nguyên nhãn `full_answer` + phép `sum`. Branch contract tách exact retrieval provenance (`expected_citations`) khỏi exact rendered source (`expected_rendered_citations`). Retrieval so theo tập canonical source identity duy nhất: duplicate chunk cùng nguồn/trang được gộp, còn source identity khác expectation bị từ chối. Bốn Query-only high-risk case khóa overall `insufficient_evidence`, exact branch outcomes, terminal factual-claim count và rendered-source count đều bằng `0`, nhưng vẫn kiểm exact source đã retrieve; gate formal bắt buộc tổng terminal violation bằng `0`. Preflight từ chối scope thiếu/trộn, case ID interaction sai, Math contamination hoặc contract drift. Formal Query series mới chỉ được chạy sau freeze commit sạch; Query vẫn OFF trong lúc checkpoint này được commit.
- Formal pair 01 trên exact commit `5557715cdec1e8f78e22bd5d8a08e52de80b8632` được giữ làm tombstone, không chọn rerun đẹp: `branch_accuracy_complete` đã pass `1.0`, provider failure/retry bằng `0`, latency/cost/budget/safety đều pass, nhưng `branch_citations_complete` còn `0.783333` và complex pass giữ `4/10 → 4/10`. Run SHA-256 `c148f80435205c072c8d06427ae9c903813d80d076b69498653b160660028823`, gate SHA-256 `245badb65d9f2640139ac5a9608f33ca74370cebfa75b10d1904d525d0b7baf5`; pair 02/03 không chạy. Root cause là evaluator dùng cùng `expected_citations` cho source đã retrieve và source đã render, khiến terminal refusal không thể biểu diễn “retrieval đúng nhưng render rỗng”; root fix TDD thêm hai exact contract độc lập, không hạ gate hoặc nới citation oracle.
- Graph Retrieval current-commit cycle đã bắt đầu offline từ base `c123c39`. Fixture hiện có `17` case, trong đó đúng `10` relational case thuộc Technical/Production/Maintenance và bao phủ `HAS_VERSION`, `SUPERSEDES`, `CONTAINS_PART`, `USES_MATERIAL`, `APPLIES_TO`; manifest SHA-256 là `def156e8d30a9184fd7105ca5e799ddef311c98a5c88c7bc59001ad42ee8a136`. Claim oracle đã lên `deterministic-labeled-claims-v2`: mọi positive relational claim bắt buộc predicate + target trong cùng một mệnh đề khẳng định; polarity `không/chưa/chẳng/chả` chỉ chặn mệnh đề chứa relation đó, nên câu phủ định không pass giả và mệnh đề phụ phủ định relation khác không bị loại oan.
- Current fixture preflight chỉ đọc đã pass `17/17`, `0` failure, `21` approved edge, structured coverage/provenance đều `1.0`, pending serving edge bằng `0`; fingerprint `71edefd6023e72fdaee5acceac6a18a5e9dbb8a30a1f3a10036da05f49e178cf`. Queue mới `.local/graph-cycle-c123c39/review-queue.jsonl` có SHA-256 `a5cf221e549a37821deba9ec891b49e4822340352a8525f8831397221f9bdc2f`; semantic projection khác queue 21-edge cũ, nên không carry-forward review labels. Cần review queue mới trước formal graph-only pair; Graph vẫn OFF và chưa có provider/formal run.
- Checkpoint Graph `2026-08-10`: queue current-contract đã được review lại `21/21` edge bởi hai reviewer `bao.nguyen` và `tran.nghi`, precision `1.0`, SHA-256 `4a02676d496b216a7dfe94c588965aab00ab9ba991fc5d17e5cb9edc7c2a64b1`. Audit fail-closed đã sửa runner để baseline giữ `all_off`, candidate chỉ bật Graph trong `evaluation/all_external`; gate bắt buộc thêm non-relational no-decrease và cost `<=1.5`; preflight không còn chấp nhận Graph artifact chỉ `passed=true` khi chưa đủ production eligibility. Cycle đầu trên `262da71` đã pass nhưng bị vô hiệu hóa cho activation sau khi audit phát hiện bundle không thể biểu diễn đúng independent review và single-pair artifact có thể bị dùng thay series. Root fix TDD tại `4bc666c11b81e28cfcc83d81d7128522736accfe` buộc Graph controlled-demo decision dùng series được recompute từ ba pair hash-bound, bắt buộc đúng `multi_reviewer/independent`, từ chối exception `single_owner`, và vẫn giữ default rollout cần owner signature riêng.
- Trên detached checkout sạch của `4bc666c`, preflight pass với fingerprint `71edefd6023e72fdaee5acceac6a18a5e9dbb8a30a1f3a10036da05f49e178cf`, rollback Graph-only pass `70/70` test và ba provider smoke dùng cho formal evidence đều pass `5/5`, `0` retry. Ba matched pair `formal-pair-01..03` đều pass; baseline/candidate cùng `10/17`, relation accuracy ổn định `0 → 9/17` (`+52.94` điểm phần trăm), wrong-answer `7 → 7`; latency ratio lần lượt `1.020415`, `0.539582`, `0.978831`; cost ratio `1.371443`, `1.335126`, `1.342708`; mọi RBAC/provenance/review/pending/budget check đều pass. Series guardrail SHA-256 `8701c693cff4994b676d4f7a6281a56692e4b75f80c36e2672e1e67eb393264d` pass `9/9` check, bind `multi_reviewer/independent` và recompute validator trả `true`. Đây mới là technical eligibility để owner xét mở pilot, chưa phải pilot/live authorization: chưa tạo accepted controlled-demo decision hoặc feature-on bundle, Graph vẫn OFF, chưa có LAN pilot 7 ngày/100 routed relational request, interaction matrix, technical acceptance và owner release decision.
- Theo authorization tạo target nhưng chưa bật feature, restore drill đã tạo SQL database `Mech_Chatbot_DB_RestoreTest_RAGPilot_20260810_4bc666c` và Qdrant collection `TaiLieuKyThuat_v2_RestoreTest_RAGPilot_20260810_4bc666c` với `231/231` point; verifier exact `4bc666c` pass trên restore evidence SHA-256 `1f69e8bcdd87147f9d526bd0b3cf0738ad16c9576d4d10e2396b6258d5d7af27`. Exact snapshot fingerprint cần owner chấp nhận riêng là `b1a73fa7d90500e5c738154e30b64ade68a0b59e5de7066f0e7981e613209b11`; trước acceptance này không được tạo Graph controlled-demo decision/bundle hoặc bật Graph.
- Checkpoint `2026-08-10`: CRAG + Claim Repair interleaved diagnostic runner đã được freeze tại commit `0b98e12a75123e3fbb07c8bd5ade8867ad31d0f3`. Runner chỉ chấp nhận canonical manifest SHA-256 `beac3aac28b59ac57930b2c7099997efa7bdfda2a76bf65e3f1620d4b0fb897b`, kiểm input drift trước/sau egress, giữ `formal_evidence=false` và fail-closed với provider retry/error hoặc arm-order variance. Current fixture preflight pass `9/9`; provider smoke mới pass `5/5`, `0` retry.
- Diagnostic `.local/crag-cycle-0b98e12/diagnostic-01/outcome.json` chạy đủ hai pair đảo thứ tự arm nhưng kết luận `inconclusive`: pair 01 pass, pair 02 chỉ fail `latency_within_budget`; `0` provider failure/retry, candidate `9/9` ở cả hai pair, cost ratio gộp `1.000690`, latency ratio gộp `1.019142`. P95 ratio đổi `1.235804 → 1.370565` và dominant overhead đổi `correction → generation`, nên không được chọn pair đẹp hoặc mở formal window. CRAG và Claim Repair vẫn OFF; bước kế tiếp là một declaration mới trong capacity window khác, không sửa code từ lượt này.
- CRAG capacity window mới trên detached checkout sạch `a106bef` giữ manifest SHA-256 `beac3aac28b59ac57930b2c7099997efa7bdfda2a76bf65e3f1620d4b0fb897b`; fixture preflight pass `9/9`, fingerprint `9592deb0e747ac9a14d42bf3fe14471baa3b145ad752bdd53fdc491dde3dea7f`, provider smoke pass `5/5`, `0` retry. Interleaved diagnostic chạy đủ hai pair, candidate đều `9/9`, không provider failure/retry, nhưng vẫn `inconclusive`: p50 ratio ổn định `1.138060` và `1.131777`, cost ratio `1.001381` và `1.017774`, còn p95 gate đảo `1.750879` ở candidate-first (fail `latency_within_budget`) thành `0.748829` ở baseline-first (pass). Outcome gộp p50 latency `1.135002`, cost `1.009511`, dominant stage cùng là `correction`, nhưng `arm_order_consistent=false`; declaration SHA-256 `8a5ef11a7e5bf10d58e6218b48beaef929c3f8ecb76b01e11b33120d75940bdc`, outcome SHA-256 `5a769d6b85f130b885adb9c420695c8dea75c95ccd6fdc13a1cf2e08b98e7269`. Không mở formal window hoặc sửa code từ tail-latency variance này; CRAG và Claim Repair vẫn OFF.
- Owner-decision packet `.local/advanced-rag-owner-decision-packet-20260810.json` ghi `approved_recommended_all_three`: Query manifest adjudication, Graph named disposable target creation, và Math tombstone + clean restart. Authorization vẫn loại trừ Graph feature enablement trước exact fingerprint acceptance, mọi default rollout enablement và git push. Packet là local execution ledger và được hash lại sau khi Query formal artifact hoàn tất; không dùng SHA cũ trước authorization.
- Production preflight đã được harden fail-closed: chỉ `default_rollout` mới có `production_ready=true`; health-only chấp nhận đúng healthy `controlled_demo` nhưng trả `production_ready=false`; missing, `evaluation` hoặc scope lạ đều fail. Điều này không đổi Math controlled-demo authorization và không nới default rollout.
- Các attempt/window cũ và provider outage cũ tiếp tục được giữ làm tombstone; không chuyển request hoặc thời gian vào window hiện tại.
- `release_decisions.json` vẫn `incomplete`; chưa có Advanced RAG feature nào được phép bật trên default rollout.

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
  - [x] Proof 5/5 đã pass và owner declaration cho full campaign đã được ký.
  - [x] Tombstone window đã dừng ở `30/100`, khởi động lại exact RC/targets, recapture health/runtime identity và mở window thay thế sạch ở `0/100`; không carry-forward request hoặc downtime.
  - [ ] Thu đủ tối thiểu 7 ngày và `100` eligible calculation request thật trên window thay thế; không tạo synthetic production traffic.
  - [ ] Human review 20 case phân tầng theo signed `single_owner`: cả 20 primary labels bởi `bao.nguyen`; mọi failure/low-confidence case bắt buộc owner review. Codex chỉ hỗ trợ kỹ thuật, không phải independent human reviewer.
  - [ ] Nếu pilot pass, chạy interaction matrix Math-only trên final RC ở concurrency 1 và 5, technical review bởi `tran.nghi`, rồi mới tạo default-rollout ledger/bundle để owner quyết định release Math.
- Phase 3 — owner đã adjudicate current-contract drift. Query-only manifest mới giữ floor `10+3`, không phụ thuộc Math và khóa terminal no-render contract; ba nhãn BOM Math-coupled được tách nguyên vẹn sang interaction manifest. Generator/preflight/regression suite đã GREEN; formal series mới phải chạy trên exact clean freeze commit trước mọi pilot decision. Query vẫn OFF.
- Phase 4 — formal Graph gate đã hoàn tất trên `4bc666c`: queue current-contract `21/21` được hai reviewer duyệt; preflight, rollback, ba provider smoke, ba graph-only matched pair và recomputed series guardrail đều pass. Disposable SQL/Qdrant targets đã được restore và verifier pass với exact fingerprint `b1a73fa7d90500e5c738154e30b64ade68a0b59e5de7066f0e7981e613209b11`. Graph mới đạt `implemented / measured / reviewed / technically eligible for pilot`; vẫn OFF và chưa `pilot-authorized` hay `live-authorized`. Bước gated kế tiếp cần owner chấp nhận fingerprint này; sau đó mới tạo controlled-demo decision/bundle, xác minh runtime/restore receipt và mở LAN pilot graph-only đủ 7 ngày/100 routed relational request.
- Phase 5 — diagnostic capacity window thứ hai trên `a106bef` vẫn `inconclusive`: quality/cost/provider checks ổn định nhưng p95 latency gate đổi theo arm order. Chưa có code-controlled root-fix signal hợp lệ, chưa mở formal window; lượt kế tiếp chỉ được mở bằng declaration/smoke mới trong capacity window khác hoặc sau khi có giả thuyết tail-latency mới đo được.
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
   - Diagnostic final `20260808-diagnostic-7b9d575-dirty-02` đã chạy đủ `13+13` case, provider failure bằng 0 và cost reconciliation hợp lệ; `-01` là tombstone của implementation diff trước review.
   - Kết quả chưa đạt: cost ratio `1.899837 > 1.35`; pass tổng `2/13 → 7/13`, decomposition pass `7/10`, branch accuracy `86.67%`, citation accuracy `70%`.
   - Final generation là overhead trội: baseline gọi `9` lần với cost `0.0198925`, query-only gọi `13` lần với cost `0.0377925`; chênh lệch `0.0179`.
   - Root fix hiện tại chuyển high-risk partial coverage có nhánh `grounded_negative` thành `insufficient_evidence` trước final generation; partial do `insufficient_evidence` hoặc `access_denied` không-grounded vẫn được phép sinh câu trả lời.
   - Diagnostic `20260808-diagnostic-7b9d575-dirty-03` là tombstone inconclusive: baseline và candidate đều có `9` provider failure, cost ratio `null`. Provider smoke riêng sau đó fail `0/5` với `ExternalProcessingDenied`, nên không chạy thêm 13+13 case và không dùng `-03` để đánh giá cost/quality.
- Evaluation runner hiện ép `EXTERNAL_PROCESSING_POLICY=all_external` cùng `RAG_EXECUTION_CONTEXT=evaluation` trong bản sao môi trường của subprocess; process cha, `.env`, runtime LAN và default rollout không đổi. Diagnostic `20260808-diagnostic-7b9d575-dirty-04` đã vượt local policy gate nhưng bị dừng sau 19 ProxyLLM error call, 13 retry event và 9 baseline trace; mọi provider call đều trả HTTP 503 `service_unavailable/no_capacity`, candidate chưa bắt đầu.
- Diagnostic runner hiện kiểm tra lại source commit, manifest, tracked worktree, runner hash và fixture fingerprint giữa hai arm. Nếu baseline đã có provider failure, runner ghi tombstone `inconclusive` rồi dừng trước candidate; recovery chỉ được xác nhận bên ngoài diagnostic, không bắt buộc thêm smoke.
- Root fix evaluator tại `a106bef` ưu tiên `debug.generation_metrics.decomposition_usage` current sau stream trước top-level snapshot cũ. Diagnostic sạch sau fix đạt cost ratio `1.319309`, final-generation call không tăng (`9 → 9`) và reconciliation pass ở cả hai arm.
- Formal pair 01 dừng ở branch accuracy/citation; pair 02/03 không chạy. Ba BOM case đang ghép kỳ vọng Grounded Math vào Query-only, còn `decomp-sufficient-missing` đang ghép partial-answer expectation vào terminal grounded-negative safety contract. Owner phải adjudicate/relabel và freeze manifest mới trước formal series; không sửa runtime/evaluator để làm yếu refusal hoặc citation oracle.
3. Áp dụng root fix theo thứ tự:
   - Nếu duplicate source/context chiếm phần lớn overhead: dedupe theo canonical source identity trước final context, nhưng giữ đủ citation cho từng branch.
   - Nếu shared instruction bị lặp: đưa phần chung ra khỏi từng branch.
   - Planner chỉ được gọi khi deterministic splitter không bao phủ đủ intent; câu đơn giản không gọi planner.
   - Sau root fix, theo owner không chạy thêm provider smoke cho diagnostic không-formal; chỉ mở một diagnostic mới khi provider được xác nhận hồi phục ngoài attempt này. Nếu diagnostic hợp lệ vẫn vượt ngưỡng, đo lại final-generation calls và per-case context trước khi tối ưu context; không giảm gate hoặc bỏ refusal/post-check để lấy số đẹp.
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
- Human review 20 case phân tầng theo governance được ký trước pilot:
  - Math controlled-demo hiện tại dùng `single_owner`: đủ 20 primary labels bởi `bao.nguyen`.
  - Codex chỉ chuẩn bị metadata và hỗ trợ kỹ thuật; không được ghi là independent human reviewer.
  - Mọi failure, access-denied bất thường hoặc low-confidence case bắt buộc owner review.
  - Pilot khác hoặc default rollout không được kế thừa exception này nếu không có governance artifact đúng scope/commit.
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
- `bao.nguyen` là release owner; `tran.nghi` vẫn là technical reviewer độc lập trước quyết định default rollout. Math controlled-demo hiện tại dùng exception `single_owner` riêng, không thay thế gate default rollout.
- Không có UI toggle cho người dùng hoặc admin.
- Threshold hiện hành không được nới.
- Không tạo/xóa account; reuse cohort nội bộ hiện có.
- Không cleanup disposable targets tự động.
- Không push hoặc publish tracker/PR nếu chưa có phê duyệt riêng.
