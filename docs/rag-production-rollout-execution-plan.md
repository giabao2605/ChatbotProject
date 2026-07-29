# Kế hoạch thực thi đưa các tính năng RAG vào sử dụng

## Destination

Mọi tính năng RAG được kiểm tra trên cùng một release candidate bằng test, runtime
smoke, baseline/candidate gate, rollback và review artifact tương ứng. Kết quả cuối
cùng là một bảng khuyến nghị bật/tắt có bằng chứng để chủ dự án phê duyệt; không
feature thử nghiệm nào được bật mặc định trước phê duyệt đó.

## Notes

- Kế hoạch này mang cả phần quyết định lẫn thực thi; không dừng ở mức nghiên cứu.
- Không xóa file, project, report, tài khoản hoặc dữ liệu nếu chưa có sự cho phép
  rõ ràng của chủ dự án.
- Không sửa gate, ngưỡng hoặc oracle chỉ để làm kết quả đạt.
- Không trộn artifact giữa các commit, snapshot, collection, provider profile,
  concurrency hoặc execution context.
- Không ghi credential, raw prompt, raw response, raw document hoặc secret vào
  report được commit.
- Tài khoản test dùng namespace hiện có `demo_...`; credential chỉ nằm trong
  `.local/`, vốn đã được gitignore. Không chạy cleanup nếu chưa được phép.
- GitHub Issues là tracker chính thức của repo, nhưng chưa được phép đăng issue ra
  ngoài. File này là bản đồ cục bộ canonical cho đến khi chủ dự án cho phép publish.
- Skills áp dụng khi thực thi: `implement`, `tdd`, `security-review`,
  `e2e-testing`, `code-review`, `production-audit`.

## Phạm vi kiểm thử đã khóa

Người dùng đã giao quyền tự tổ chức việc triển khai. Các public seam sau được dùng
làm seam TDD và acceptance:

1. Migration CLI và release preflight.
2. RAG `/health`, `/chat`, `/chat/stream`.
3. App `/api/health`, `/api/auth/login`, `/api/chat/message`, `/api/users`.
4. Browser login → chat SSE → citation → RBAC/access-denied.
5. Feature activation ledger, activation bundle và runtime health contract.
6. Runner/gate/rollback chính thức của từng feature.
7. Integrated matrix ở concurrency 1 và 5.

Test chỉ mock tại system boundary: SQL/Qdrant/provider/time/filesystem. Test hành vi
qua public interface, không khóa private implementation.

## Trạng thái hiện tại tại lúc lập kế hoạch

- Branch `codex/codebase-layer-refactor` sạch và đang trước remote 7 commit.
- HEAD `c542eca` chứa 4.281 dòng thêm cho Jina/rerank evaluation; artifact full-RAG
  hiện `passed=false`, `technical_authorized=false`, `release_authorized=false`.
- `release_decisions.json` đang `incomplete`. Chỉ Late Interaction có quyết định
  `rejected`; các feature live khác chưa có quyết định.
- CRAG window mới nhất `20260729-crag-window-03-7d8cfa2` là `inconclusive`; pair
  đầu thất bại quality/provider checks và không tạo authorization/bundle.
- Grounded Math đã có series kỹ thuật tốt hơn nhưng chưa thể live vì CRAG và human
  decision còn thiếu.
- Query Decomposition có evidence chia đôi: một pair đạt, hai pair không đạt.
- GraphRAG chưa có tối thiểu 20 edge được independent review.
- Community Summaries phụ thuộc GraphRAG và corpus/eval hiện chưa đủ.
- Late Interaction giữ tắt theo quyết định rejected.
- Jina giữ evaluation-only; pair 02 full-RAG vượt latency ratio `1.443 > 1.25`.
- LAN launcher chỉ migrate đến `V0032`, trong khi repo có migration đến `V0041`.
- Đường triển khai đã khóa là Windows LAN/local.
- App startup chưa có cùng fail-fast config validation như RAG/worker.
- Repo chưa có Playwright E2E chạy tự động.

## Bản đồ phụ thuộc

```text
Release candidate và provenance
  -> Deployment/security foundation
    -> Test cohort
      -> Baseline browser E2E + load
        -> CRAG + Claim Repair
          -> Grounded Math
            -> Query Decomposition
              -> GraphRAG
                -> Community Summaries
                  -> Integrated matrix + rollback drill
                    -> Final enable/disable decision pack

Late Interaction: giữ OFF theo evidence hiện tại.
Jina rerank: giữ evaluation-only theo evidence hiện tại.
```

## Ticket 1: Khóa release candidate và provenance

### Mục tiêu

Chọn đúng commit để toàn bộ test/eval về sau cùng tham chiếu và không khái quát
evidence cũ sang HEAD mới.

### Việc làm

1. Giữ nguyên 7 commit hiện tại; không reset, drop hoặc xóa report.
2. Chạy targeted gate cho Jina/rerank và full fast suite trên HEAD.
3. Ghi rõ Jina là evaluation-only; không đổi default Voyage.
4. Chạy backend coverage, architecture, frontend coverage/build.
5. Sau khi code review sạch, commit các sửa đổi theo conventional commit.
6. Không push hoặc mở PR nếu chưa có phê duyệt external action riêng.

### Evidence hoàn tất

- Git SHA sạch và cố định.
- Backend line/branch coverage tối thiểu 80%.
- Architecture, frontend test/build xanh.
- Current-HEAD verification report nêu rõ rejected evidence vẫn là rejected.

## Ticket 2: Làm deployment và security fail-fast

### Mục tiêu

Không service nào được báo ready khi schema, Qdrant, activation hoặc secret/cookie
production chưa hợp lệ.

### TDD theo vertical slice

1. RED: launcher không được pin cứng `V0032` khi release target là migration mới
   nhất được discover.
2. GREEN: dùng migration CLI canonical, không tạo source-of-truth thứ hai.
3. RED: readiness phải fail với `status=degraded` dù HTTP endpoint còn sống.
4. GREEN: dùng checker JSON strict cho Windows LAN.
5. RED: app production startup từ chối thiếu/reuse `APP_SESSION_SECRET`, cookie
   không secure, external-processing policy không explicit.
6. GREEN: gọi config validation tại app lifespan; error phải mask secret.
7. RED: production preflight từ chối seeded dev credential đang active.
8. GREEN: thêm read-only preflight check; không tự xóa hoặc vô hiệu hóa account.
9. RED: upload vượt per-file hoặc aggregate cap bị chặn trước khi giữ toàn bộ body.
10. GREEN: một capped-read helper dùng lại cho document và chat image upload.
11. RED/GREEN: thêm rate limit tối thiểu cho chat/RAG/upload trước provider work.
12. RED/GREEN: thêm security headers và allowlist trusted host cho production.

### Windows LAN readiness

- Healthcheck phải xác nhận `status=ok`, `rag_loaded=true`,
  `activation_valid=true`, `live_authorized` phù hợp scope.
- LAN launcher không được tự dừng process ngoài đúng PID/port ownership contract.

### Evidence hoàn tất

- Clean migration chạy hai lần trên disposable DB.
- App/RAG/worker config tests xanh.
- PowerShell parser và strict LAN readiness test xanh.
- Security regression suite, dependency audit và secret scan sạch.

### Trạng thái thực thi 2026-07-29

- Clean database `Mech_Chatbot_Test_RAG_20260729_T2A` bootstrap V0001-V0041 và
  chạy migration lần hai thành công; database test được giữ lại, không cleanup.
- 177 test tập trung đạt; full fast backend suite đạt với line coverage
  `92.234594%` và branch coverage `84.905989%`.
- Frontend 32 test đạt, production build đạt; architecture 18 test đạt.
- `npm audit`, `pip-audit`, high-confidence secret scan và security review không
  còn finding chưa xử lý.
- Preflight thật đạt migration, Qdrant và activation profile `all_off`; production
  vẫn fail-closed vì account seed `admin` còn active. Không tự vô hiệu hóa account
  này khi chưa có phê duyệt tác động tài khoản.

## Ticket 3: Tạo cohort tài khoản test an toàn

### Mục tiêu

Có đủ actor để kiểm tra viewer/uploader/reviewer/admin, department/site/clearance
và controlled-demo assignment mà không dùng credential production.

### Việc làm

1. Chạy unit contract của user creation/access profile trước.
2. Kiểm tra account `demo_...` hiện có bằng query chỉ trả count/role/site, không
   trả password hash hoặc credential.
3. Nếu thiếu, dùng `scripts/demo_wave/bootstrap_demo_wave_data.py seed-users`.
4. Credential sinh ngẫu nhiên chỉ ghi `.local/demo-wave-credentials.json`.
5. Xác minh login và profile qua `/api/auth/login`; không verify bằng đọc trực
   tiếp password hash.
6. Tạo cohort hash cho 2–10 actor Technical/HQ nếu CRAG controlled demo cần.
7. Không chạy cleanup script khi chưa có sự cho phép riêng.

### Evidence hoàn tất

- Tối thiểu các vai trò viewer, uploader, reviewer/admin đăng nhập được.
- RBAC, department, site và clearance phản ánh đúng qua public API.
- Không credential nào xuất hiện trong git diff, report hoặc console transcript.

## Ticket 4: Thêm browser E2E và chứng minh baseline all-off

### Mục tiêu

RAG nền usable trước khi đánh giá feature nâng cao.

### Việc làm

1. Thêm Playwright tối thiểu, dùng browser Chromium và không tạo abstraction thừa.
2. E2E: login, mở chat, gửi câu hỏi được phép, nhận SSE hoàn tất và citation.
3. E2E: actor khác site/clearance nhận access-denied, không leakage.
4. E2E: session/CSRF lỗi bị chặn đúng status.
5. E2E: health báo activation profile `all_off`, mọi governed flag false.
6. Chạy golden eval và load test concurrency 1/5 với cache identity được ghi rõ.
7. Capture screenshot/trace chỉ khi fail; artifact không chứa credential.

### Evidence hoàn tất

- Browser E2E chạy lại được bằng một command.
- Allowed và denied flow đều đạt.
- Không wrong-answer/leakage regression trên baseline corpus.
- P95, provider error, retry và fallback được ghi metadata-only.

## Ticket 5: Hoàn tất CRAG + Claim Repair

### Mục tiêu

Tạo đủ evidence để kết luận accepted/rejected/inconclusive cho controlled demo,
không bật default rollout.

### Việc làm

1. Chẩn đoán pair-01 window 03 theo root cause:
   generation timeout/connection, Voyage HTTP error, missing repair exercise,
   wrong-refusal không giảm.
2. Viết test đỏ tại public RAG/evaluation seam cho lỗi deterministic tìm được.
3. Sửa tối thiểu ở shared root cause; giữ budget, threshold và fallback contract.
4. Fresh provider smoke tối đa 30 phút trước mỗi pair.
5. Preflight fixture, rollback evidence và ba pair độc lập trên cùng commit,
   snapshot, manifest, collection, provider hash và concurrency.
6. Stop on first failed pair; không rerun để chọn số đẹp.
7. Khi ba pair đạt, tạo series guardrail và technical authorization.
8. Tạo review pack high-risk; dùng owner-review đúng nhãn nếu chỉ một reviewer.
9. Controlled demo tối đa ba ngày, 20 matched pairs; sau đó dừng gateway.
10. Nếu checkpoint cho phép, chuẩn bị pilot 100 adjudicated pairs/7–14 ngày.

### Evidence hoàn tất

- Gate cả ba pair đạt hoặc có rejection artifact hợp lệ.
- Leakage bằng 0, wrong-answer không tăng, correction/repair không quá một.
- P95 không quá 1.25x, cost không quá 1.5x, retry đúng policy.
- Rollback chỉ cần tắt hai flag và đã được test trên cùng commit.

## Ticket 6: Hoàn tất Grounded Math

### Mục tiêu

Đánh giá structured calculation sau khi CRAG có disposition hợp lệ.

### Việc làm

1. Reconcile series hiện có với HEAD và không reuse artifact khác commit.
2. Xác minh BOM provenance/Decimal/formula/unit/citation từ SQL + Qdrant fixture.
3. Chạy baseline/candidate pairs, candidate chỉ thêm Grounded Math.
4. Xuất và hoàn tất review pack 10 truy vấn.
5. Ghi accepted/rejected/inconclusive decision theo gate thật.

### Evidence hoàn tất

- Calculation plan được exercise; không unsupported number.
- Formula, unit, provenance và citation đúng.
- Wrong-answer/leakage không tăng; rollback flag xanh.

## Ticket 7: Hoàn tất Query Decomposition

### Mục tiêu

Giải quyết evidence chia đôi và chứng minh gain trên truy vấn phức hợp.

### Việc làm

1. Chạy lại đủ ba pair trên cùng current commit sau evaluator fixes.
2. Bổ sung/khóa bộ 10 complex questions theo contract hiện có.
3. Simple query không gọi planner; complex query có branch/subquery bounded.
4. Đo branch accuracy, branch citation, wrong-answer, budget, latency và cost.
5. Owner review toàn bộ pack; không dùng một rerun xanh thay cho formal review.

### Evidence hoàn tất

- Ba pair có disposition thống nhất.
- Planner tối đa ba subquery, một correction, một final generation.
- Branch/citation accuracy đạt gate; rollback và leakage xanh.

## Ticket 8: Hoàn tất GraphRAG và Community Summaries

### Mục tiêu

Chỉ mở Graph/Community khi provenance và independent review đạt.

### Việc làm

1. Export queue tối thiểu 20 edge có source document/page/version/quote.
2. Thu independent labels; không để agent tự giả làm người review.
3. Validator yêu cầu reviewed precision tối thiểu 95%.
4. Chạy Graph baseline/candidate gate và relational router scope.
5. Chỉ sau Graph accepted mới generate/review community summaries.
6. Mở rộng global-query corpus đủ ngưỡng; đo global gain và citation grounding.
7. Ghi decision riêng cho Graph và Community.

### Evidence hoàn tất

- Graph edge review đủ count/precision và provenance.
- Relational answer gain đạt, không leakage/pending-serving escape.
- Community summaries có reviewer approval, global gain và rollback evidence.

## Ticket 9: Integrated matrix, rollback và production audit

### Mục tiêu

Chứng minh tổ hợp cuối không phá security/performance và có thể khôi phục.

### Việc làm

1. Build effective matrix từ accepted/rejected decisions; rejected/inconclusive
   feature bắt buộc chạy fallback OFF.
2. Chạy từng row concurrency 1 và 5 trên cùng commit/snapshot/provider hash.
3. Chạy security matrix, strict streaming, cache namespace, request budget.
4. Backup SQL/Qdrant; restore drill chỉ trên disposable target.
5. Rollback drill: flags/profile về all-off, restart, health + browser smoke.
6. Full backend/frontend/architecture/integration/eval/E2E suite.
7. Code review hai trục Standards/Spec và security review cuối.
8. Production audit chấm điểm theo evidence hiện hành.

### Evidence hoàn tất

- `ready_for_live_matrix=true` chỉ khi prerequisites thật sự complete.
- Không leakage ngoài admin exception đã khai báo.
- Không severe wrong-answer; P95/cost trong budget.
- Restore/rollback drill có artifact và không tác động dữ liệu production.

## Ticket 10: Bảng quyết định cuối cho chủ dự án

### Deliverable

Một Markdown/JSON decision pack, mỗi feature có:

- commit, snapshot, manifest, provider profile và collection;
- implemented/measured/reviewed status;
- gate metrics và failed checks;
- security/performance/rollback evidence;
- recommendation `enable`, `keep_off`, hoặc `re-evaluate`;
- residual risk và lệnh rollback;
- link/hash tới artifact authoritative.

Chỉ chủ dự án ký quyết định cuối. Sau chữ ký, mới cập nhật
`release_decisions.json`, tạo activation bundle cùng commit và bật đúng activation
profile. Không tự suy diễn controlled-demo acceptance thành default rollout.

## Decisions so far

- Giữ Voyage `rerank-2.5-lite` làm default; Jina vẫn evaluation-only.
- Giữ Late Interaction OFF theo quyết định rejected hiện tại.
- Chưa bật bất kỳ governed feature nào; live ledger vẫn fail-closed.
- Dùng cohort `demo_...` hiện có thay vì viết account system mới.
- Ưu tiên sửa deployment/security/E2E trước khi chạy lại eval tốn chi phí.

## Not yet specified

- Deployment target cuối là LAN nội bộ hay server HTTPS; plan hỗ trợ cả hai, nhưng
  production hardening cuối sẽ pin một target sau khi baseline E2E đạt.
- Người review độc lập cho Graph edge ngoài owner hiện chưa được chỉ định.
- Production pilot traffic thật chỉ được bắt đầu sau khi technical gates và owner
  approval hoàn tất.

## Out of scope

- Không rewrite toàn project.
- Không thay kiến trúc retrieval đang có nếu gate chưa chứng minh cần thiết.
- Không xóa report/artifact cũ để làm tree “sạch”.
- Không đổi Vision model trong effort này.
- Không push, mở PR, deploy public hoặc bật live flags nếu chưa có phê duyệt external
  action tương ứng.
