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

Mục này là snapshot ban đầu khi lập plan. Trạng thái authoritative mới hơn nằm
ở mục `Tiến độ thực thi cập nhật 2026-07-30` ngay bên dưới và trong
`data/integrated_hardening_v1/rag_production_decision_pack.json`.

- Branch `codex/codebase-layer-refactor` sạch và đang trước remote 7 commit.
- HEAD `c542eca` chứa 4.281 dòng thêm cho Jina/rerank evaluation; artifact full-RAG
  hiện `passed=false`, `technical_authorized=false`, `release_authorized=false`.
- `release_decisions.json` đang `incomplete`. Chỉ Late Interaction có quyết định
  `rejected`; các feature live khác chưa có quyết định.
- CRAG authoritative window `20260730-crag-window-06-0090639` là `inconclusive`; candidate
  đạt `9/9`, không provider failure, đã exercise correction/repair, nhưng P95
  `5863.22 -> 7921.84 ms`, ratio `1.351107 > 1.25`. Pair 02/03 không chạy và
  không tạo authorization/bundle. Window 05 là tombstone, không phải evidence.
- Grounded Math đã có series kỹ thuật tốt hơn nhưng chưa thể live vì CRAG và human
  decision còn thiếu.
- Query Decomposition có evidence chia đôi: Pair 01 đạt, Pair 02 fail cost,
  Pair 03 không chạy theo stop rule.
- GraphRAG chưa có tối thiểu 20 edge được independent review.
- Community Summaries phụ thuộc GraphRAG và corpus/eval hiện chưa đủ.
- Late Interaction giữ tắt theo quyết định rejected.
- Tại snapshot ban đầu, Jina chỉ dùng evaluation; pair 02 full-RAG vượt latency
  ratio `1.443 > 1.25`. Quyết định provider mới hơn được ghi ở checkpoint
  2026-07-31 bên dưới.
- LAN launcher chỉ migrate đến `V0032`, trong khi repo có migration đến `V0041`.
- Đường triển khai đã khóa là Windows LAN/local.
- App startup chưa có cùng fail-fast config validation như RAG/worker.
- Repo chưa có Playwright E2E chạy tự động.

## Tiến độ thực thi cập nhật 2026-07-31

### Mốc Git và phạm vi bằng chứng

- Branch thực thi: `codex/codebase-layer-refactor`.
- Commit feature-evaluation sạch:
  `fa8dc16c26eb13333d8ef978d75cba8d7101ebf0`.
- Integrated verification commit:
  `5f7b98b81565eb7c04db4f45be39e0f44ed85786`.
- Checkout HEAD tại lần reconcile 2026-07-31:
  `835648360f5fa996fe5ae52acaa69bcff0521066e`; commit này không thay thế
  evidence đã pin theo từng window.
- CRAG window 06 chạy tại
  `00906394bce762d0d3cfd7812ed944d53617cf16`; Grounded Math, Query
  Decomposition và Graph/Community chạy tại
  `fa8dc16c26eb13333d8ef978d75cba8d7101ebf0`.
- Grounded Math, Query Decomposition và Graph/Community disposition được chạy
  trong detached worktree sạch tại `fa8dc16`; integrated offline, full suite,
  coverage, frontend và architecture được chạy trên primary worktree sạch tại
  `5f7b98b`.
- Window Query Decomposition không khai báo ở `5f7b98b` được giữ làm tombstone
  nhưng bị loại khỏi mọi rollout decision; không trộn kết quả của window này
  với window hợp lệ ở `fa8dc16`.
- CRAG window `20260730-crag-window-05-0090639` bị operator dừng giữa chừng,
  được giữ nguyên làm tombstone và không được tính vào series hay rollout
  decision. Window 06 là CRAG evidence authoritative hiện hành.
- Đường triển khai đã chốt là Windows LAN/local, không dùng Docker. Không có
  Docker command, image, container hoặc Docker artifact nào được tạo/chỉnh sửa
  trong chuỗi thực thi này.

### Release-candidate freeze checkpoint

- Candidate xuất phát từ checkout HEAD `8356483`; sau khi owner duyệt, RC SHA
  được lấy từ Git HEAD chứa chính thay đổi này. RC commit vẫn chưa phải
  activation evidence.
- Owner đã đổi provider order ngày 2026-07-31: Jina là reranker mặc định,
  Voyage là fallback một lần, sau đó mới dùng deterministic local fusion.
  Thay đổi code này làm RC `40a11d8` hết hiệu lực; phải freeze RC mới và chạy
  lại formal chain. Mọi governed feature vẫn OFF.
- Owner đã cấp production authorization cho Jina reranking. Migration `V0042`
  chỉ promote đúng profile `V0041` sang
  `risk-accepted-v1-jina-production`, giữ surface `reranking`, secret reference
  và retention contract; review hết hạn sau 90 ngày. Jina là primary trong
  production, Voyage chỉ fallback khi Jina không khả dụng/lỗi.
- Diagnostic Jina trên RC cũ chỉ là supporting evidence: candidate đạt `9/9`,
  Jina `8/8` call thành công, fallback/retry/provider error bằng `0`; P95 tổng
  `21237 ms` bị chi phối bởi generation P95 `18455 ms`, không được dùng làm
  formal activation evidence cho RC mới.
- Query Decomposition chỉ rút gọn instruction nội bộ: base `67` ký tự, biến
  thể đồng thời thiếu nguồn và access denied `126` ký tự; không đổi model,
  classifier, planner, số subquery hoặc gate.
- Graph seed chỉ tạo/refresh deterministic edge từ source current, servable,
  approved, published và effective; edge cũ sai source, quote hoặc endpoint bị
  disable trong đúng department/source-system scope. Row LLM/human-reviewed
  cùng natural key không bị seed ghi đè.
- Graph proposal, preflight và runtime serving dùng cùng contract
  relation-specific. `APPLIES_TO` chỉ nhận document -> part cùng source doc,
  quote phải có cả mã document, mã part, nội dung bổ sung và tồn tại trong đúng
  page/BOM row. Proposal provenance sai vẫn reject được; `REQUIRES_TOOL` chưa có
  structured validator nên fail-closed khi approve/serve.
- Verification local sau mọi review fix: targeted `197/197`; backend fast
  `2478 passed, 1 skipped, 21 deselected`; coverage line `92.286356%`, branch
  `85.008666%`; architecture `18/18`; security `137 passed, 9 skipped`;
  frontend `32/32` và production build đạt. Frontend coverage vẫn là baseline
  cũ `21.94%` line, `14.66%` branch và repo chưa cấu hình threshold frontend
  `80%`; backend là gate coverage `80/80` hiện hành.
- Verification mới cho Jina production candidate: rerank/profile targeted xanh;
  backend fast `2484 passed, 1 skipped`, coverage line `92.373853%`, branch
  `85.051903%`; architecture `18/18`, security marker suite, frontend `32/32`
  và production build đều đạt. Hai review độc lập không còn blocker.
- Production metadata smoke gọi trực tiếp Jina `jina-reranker-v3` qua policy
  `risk-accepted-v1-jina-production`, trả đúng top document trong `871.03 ms`;
  Voyage không được gọi. RAG server đã restart, health `ok`, execution context
  `production`, activation profile vẫn `all_off`.
- Các số local trên chưa được dùng để sửa release decision. Chưa gọi provider,
  chưa ingest/re-seed SQL/Qdrant, chưa chạy formal window và chưa làm human
  review.

### Tổng quan theo ticket

| Ticket | Trạng thái | Đã chứng minh | Còn thiếu để đóng ticket |
| --- | --- | --- | --- |
| 1. Release candidate và provenance | Đã hoàn tất nền tảng | Git/evidence được pin theo commit; provider manifest và runtime artifact có hash; clean worktree verifier hoạt động | Khi mở cửa sổ eval mới vẫn phải pin lại commit/snapshot/provider theo đúng window |
| 2. Deployment và security fail-fast | Đã hoàn tất code; live recapture chưa chạy | Windows launcher fail nếu worktree bẩn, restore evidence sai, migration/preflight lỗi hoặc runtime-state drift; health có deployment/runtime identity | Chạy launcher thật trên restore evidence hợp lệ và thu hardened live preflight mới |
| 3. Cohort tài khoản test | Đã hoàn tất, không tạo/xóa account | Reuse 33 account `demo_...`; viewer/uploader/reviewer login và profile đúng; credential không vào Git/report | Chỉ bổ sung account nếu một future matrix thiếu actor; cần phê duyệt riêng |
| 4. Browser E2E và baseline all-off | Đã đo baseline | Browser `3/3`, golden `5/5`, frontend `32/32`, load c1/c5 không lỗi; mọi governed flag OFF | Chạy lại health/browser smoke sau hardened launcher để thay evidence pre-hardening |
| 5. CRAG + Claim Repair | Dừng đúng stop rule, disposition `inconclusive` | Window 06 Pair 01 candidate `9/9`; diagnostic Voyage gặp `5` HTTP 429; diagnostic Jina đạt candidate `9/9`, Jina `8/8` thành công nhưng có generation outlier | Freeze RC Jina-primary mới rồi chạy clean interleaved diagnostic; không nới latency gate hoặc dùng evidence từ RC cũ |
| 6. Grounded Math | Ba pair kỹ thuật đạt; disposition vẫn `inconclusive` | Fixture `16/16`, rollback `2/2`, ba pair candidate đều `16/16`; quality/safety/latency/cost/rollback xanh | CRAG phải accepted và owner review đủ `10/10`; không chạy lại window đã hoàn tất |
| 7. Query Decomposition | RC code candidate đã được owner duyệt; disposition vẫn `inconclusive` | Instruction đạt `67/126` ký tự và unit contracts xanh; formal evidence cũ vẫn fail Pair 02 cost `1.554088 > 1.5` | Chờ provider/reranker ổn định, chạy diagnostic/formal window mới và owner review; không dùng rerun không khai báo |
| 8. GraphRAG và Community Summaries | Provenance kỹ thuật đạt; human gate vẫn blocked | Batch `graph-eval-v1` đã re-seed, stale edge bị disable, quote thật được approve; preflight đạt `21/21`, workflow approve/reject đạt | Đồng nghiệp review độc lập toàn bộ `21` edge hợp lệ; chỉ sau Graph accepted mới chạy Community |
| 9. Integrated matrix, rollback và production audit | Offline capability hoàn tất; live matrix/restore thật chưa chạy | Integrated verification commit `5f7b98b`: offline `63/63`, security `15/15`, leakage 0, backend `2467` pass, coverage line/branch `92.29%/84.99%`, frontend/build và architecture xanh | Restore drill trên disposable targets, hardened all-off runtime, live matrix c1/c5 và browser rollback smoke |
| 10. Decision pack cuối | Deliverable đã có; chưa được chủ dự án ký | JSON decision pack máy đọc được, fail-closed; từng feature có disposition/next gate; hash evidence khớp | Owner signature và release decisions đầy đủ; chỉ sau đó mới tạo feature-on activation bundle |

### Những thay đổi đã implement và commit

| Commit | Nội dung đã hoàn thành |
| --- | --- |
| `7e240cc` | Fixture Query Decomposition chỉ thêm row thiếu; loại đường delete/shared destructive ingest |
| `b929920` | Khớp decomposition manifest với source row/provenance thật |
| `3fb13f2` | Ghi disposition hiện hành cho từng retrieval/RAG feature |
| `b7e8c11` | Tạo production decision pack fail-closed |
| `573cfb7` | Bắt buộc Graph edge có source provenance |
| `a1413c7` | Bind integrated gate với release flags/decisions thay vì chỉ capability |
| `912fa59` | Bắt buộc load evidence ở concurrency 1 và 5 |
| `a94a812` | Bổ sung rollback contract cho Community Summaries |
| `a886a8f` | Pin Windows runtime bằng deployment ID, Git SHA và snapshot fingerprint |
| `f1c65a3` | Backup cleanup chuyển thành opt-in và fail-closed |
| `24d35c4` | Thêm guarded SQL/Qdrant restore drill, không `REPLACE`, không auto-cleanup |
| `ace0944` | Canonicalize line ending khi verify provider manifest; runtime artifact vẫn raw-hash |
| `5b1f4bb` | Bind benchmark/load report với runtime identity; kiểm identity trước/sau từng concurrency level |
| `3065b98` | Bind Graph source quote với current document/page/BOM, semantic endpoints và governance |
| `2bd0a34` | Bind launcher với restore receipt và fingerprint trạng thái SQL/Qdrant thực sau migration |
| `c7ad3e8` | Refresh decision pack và bằng chứng production hiện hành |
| `0090639` | Chuẩn hóa Part ID và đọc đúng quantity/unit trong bảng Markdown |
| `fa8dc16` | Giữ Grounded Math deterministic và sửa typed partial outcome/cross-document scope |
| `5f7b98b` | Cho phép dirty worktree chỉ khi demo launcher được gọi explicit; production mặc định vẫn fail-closed |

### Chi tiết hardening đã hoàn thành

1. Runtime và benchmark provenance:
   - RAG `/health` trả `deployment_id`, `git_sha`, `snapshot_fingerprint`,
     `provider_configuration_sha256`, collection, execution context, flags và
     versions.
   - Benchmark đọc identity trước và sau từng level; runtime restart/config drift
     giữa c1/c5 làm window fail thay vì gắn identity cũ.
   - Integrated load report chỉ nhận benchmark/eval có cùng Git SHA, snapshot,
     provider hash, governance scope, collection, execution context và pipeline
     configuration.

2. Windows launcher provenance:
   - Từ chối primary worktree bẩn hoặc commit đổi trong lúc chuẩn bị.
   - Không nhận `RAG_SNAPSHOT_FINGERPRINT` tự khai báo.
   - Bắt buộc restore evidence nằm trong `.local/restore-drill`, đúng SHA-256,
     đúng current commit, source database/collection và disposable target.
   - Sau migration, Qdrant index/backfill và production preflight, launcher mới
     hash trạng thái live SQL/Qdrant ngay trước `Start-Process`.
   - SQL serving tables được đọc hai lượt trong transaction `SERIALIZABLE`.
     Qdrant payload, vectors và collection config được hash hai lượt; same-count
     mutation hoặc digest drift đều làm startup fail.

3. Restore/backup safety:
   - SQL restore bắt buộc target có dạng source-prefixed `RestoreTest`, target
     chưa tồn tại, backup đúng source database và có BackupSetGUID/LSN đầy đủ.
   - Không dùng `WITH REPLACE`, không `DROP DATABASE`, không cleanup tự động.
   - SQL UNC/traversal path bị từ chối; partial restore ghi rõ
     `target_may_exist=true`.
   - Qdrant restore chỉ nhận exact configured origin/path, snapshot thuộc source
     collection và checksum khớp; target phải chưa tồn tại.
   - Backup cleanup chỉ xóa đúng filename do current database tạo theo mẫu
     timestamp; prefix collision của database khác không còn bị xóa nhầm.

4. Graph provenance:
   - Approved edge phải có source doc/page/version/quote và
     `source_evidence_matches=true`.
   - Quote phải khớp exact current `DocumentPages` hoặc `BangKeVatTu.RawRowJson`.
   - `HAS_VERSION`, `SUPERSEDES`, `HAS_PAGE`, `CONTAINS_PART` và
     `USES_MATERIAL` bind canonical source/target endpoints.
   - `APPLIES_TO` chỉ nhận document-to-part trong cùng source document và quote
     phải chứa đúng mã tài liệu, mã part cùng câu giải thích có thật.
   - Department, site và security level của edge phải khớp `TaiLieu` hiện hành.
   - Missing migration, stale quote, stale version, wrong endpoint hoặc stale
     governance đều fail provenance completeness.

5. Restore/runtime fingerprint:
   - SQL backup receipt dùng BackupSetGUID, First/Last/Checkpoint/DatabaseBackup
     LSN, BackupType và Position; không dùng hash chuỗi path làm identity.
   - Runtime fingerprint bao phủ core document/BOM/attribute/material/graph/
     community tables, RBAC serving metadata và toàn bộ Qdrant point payload,
     vectors, payload schema và collection configuration.

### Bằng chứng kiểm thử authoritative hiện hành

- Clean backend offline tại `5f7b98b`:
  `2467 passed, 1 skipped, 0 failed`; SQL integration bị skip đúng vì
  `RUN_DB_TESTS` chưa bật, live integration và eval bị loại đúng marker.
- Coverage canonical theo CI tại cùng commit: line `92.286356%`, branch
  `84.991334%`; cả hai vượt ngưỡng `80%`.
- Frontend tại cùng commit: `32/32` unit test và production build đạt;
  architecture `18/18`.
- Integrated offline artifact:
  `.local/integrated-hardening/5f7b98b/offline.json`,
  SHA-256
  `c30da40c23d881dd39a9f131b6bfcfb2d9edc8fe06fbd4763cc9f0d0773c5b02`.
  Kết quả: `63/63`, flags default OFF, cache isolation/strict stream/rollback đạt.
- Integrated preflight artifact:
  `.local/integrated-hardening/5f7b98b/preflight.json`,
  SHA-256
  `8bb633c1bf8cdc34bce48b00c45948bd916e938a009e48cfaa154df639025908`.
  Kết quả: capability và controlled-demo fallback đạt, security `15/15`,
  leakage `0`; mọi effective release flag OFF và
  `ready_for_live_matrix=false`.
- Local demo health vẫn `status=ok` và cả 7 governed flag OFF, nhưng deployment
  là `windows-lan-dirty-00906394bce7`, Git SHA `0090639`, không khớp integrated
  verification commit `5f7b98b` hoặc checkout HEAD hiện tại `8356483`; quan sát
  này không phải release evidence và không được dùng thay hardened launcher
  preflight.
- `npm audit --omit=dev` và `pip-audit` trên active environment không tìm thấy
  advisory. `requirements.lock.txt` là snapshot local không canonical, đã được
  loại khỏi install/CI path từ `6ac5557`; audit của file này có 27 advisory/5
  package nhưng không được dùng làm rollout gate hoặc nguồn cài production.
- Security subagent đã chạy negative probes cho runtime drift, same-count
  Qdrant mutation, `TaiLieuKyThuat`, SQL backup identity, path/checksum và Graph
  endpoint/governance; kết luận không còn blocker HIGH/MEDIUM trong phạm vi.
- PowerShell parser, Python compile và `git diff --check` đều đạt.

### Trạng thái fail-closed hiện tại

- `authoritative_for_live_activation=false`.
- `ready_for_live_matrix=false`.
- `feature_activation_authorized=false`.
- `release_decisions.json` vẫn `status=incomplete`.
- Late Interaction là feature duy nhất có quyết định `rejected`.
- CRAG, Claim Repair, Grounded Math, Query Decomposition, Graph Retrieval và
  Community Summaries chưa có accepted/rejected owner decision.
- Activation profile phải giữ `all_off`; không tạo feature-on bundle và không
  bật live flag từ các kết quả offline/capability.

### Phần chưa chạy và thứ tự gated tiếp theo

Chủ dự án đã chọn hoãn backup/restore khi SQL/Qdrant hiện chỉ chứa corpus demo.
Quyết định này tránh tạo bản sao không cần thiết nhưng không làm gate restore
thành đạt; trước khi ingest dữ liệu thật vẫn phải chốt disposable targets và thu
restore evidence mới.

1. Chủ dự án xác nhận cho phép tạo disposable restore targets và chốt:
   SQL backup path, SQL data directory, target database, Qdrant snapshot
   URL/name/checksum, expected point count từ receipt gốc và target collection.
2. Chạy `scripts/ops/restore_drill.py --execute` đúng một lần trên các target đã
   xác nhận. Giữ nguyên target/artifact để review; không cleanup tự động.
3. Xác minh restore artifact hash và chạy Windows launcher trên clean commit.
   Bước này có migration/backfill live nên cần quyền vận hành rõ ràng.
4. Thu hardened all-off `/health`, production preflight và browser smoke mới;
   bằng chứng pre-hardening `5/5` không được dùng thay thế.
5. Giữ nguyên Grounded Math window đã hoàn tất; chẩn đoán CRAG latency và Query
   Decomposition cost trước khi predeclare bất kỳ window mới nào.
6. Dừng ngay ở failed pair/gate đầu tiên; không rerun window để chọn số đẹp và
   không nới threshold.
7. Thu independent Graph labels và owner review cho các pack bắt buộc.
8. Chỉ khi prerequisites, human decisions, restore/rollback và matrix c1/c5 đều
   đạt mới hoàn tất `release_decisions.json`, ký decision pack và tạo activation
   bundle.

### Những hành động chưa được thực hiện

- Chưa chạy restore thật hoặc tạo disposable SQL/Qdrant target.
- Chưa chạy hardened Windows launcher trên current HEAD sau provenance hardening.
- Đã chạy provider smoke/window mới cho Grounded Math và Query Decomposition;
  kết quả vẫn fail-closed theo gate nêu dưới.
- Chưa chạy integrated live matrix c1/c5.
- Chưa thay đổi account, credential hoặc release decision.
- Chưa bật bất kỳ governed RAG feature nào.
- Chưa push branch, mở PR hoặc deploy ra hệ thống ngoài.

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
Rerank mặc định: Jina -> Voyage fallback -> deterministic local fusion.
```

## Ticket 1: Khóa release candidate và provenance

### Mục tiêu

Chọn đúng commit để toàn bộ test/eval về sau cùng tham chiếu và không khái quát
evidence cũ sang HEAD mới.

### Việc làm

1. Giữ nguyên lịch sử commit hiện tại; không reset, drop hoặc xóa report.
2. Chạy targeted gate cho Jina/rerank và full fast suite trên HEAD.
3. Ghi rõ Jina là default, Voyage là fallback không retry; RC cũ hết hiệu lực.
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
  Evidence này có trước `V0042`; RC mới phải chạy lại clean migration qua
  `V0042`.
- Clean database `Mech_Chatbot_Test_JinaProd_20260731` đã bootstrap và apply
  V0001-V0042, chạy lần hai idempotent, ledger đủ; database test được giữ lại.
  `Mech_Chatbot_DB` cũng đã apply V0042 và đọc lại đúng production policy.
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

### Trạng thái thực thi 2026-07-29

- Reuse 33 account `demo_...` hiện có; không tạo account mới và không chạy
  cleanup.
- `demo_viewer`, `demo_uploader` và `demo_reviewer` đều login và đọc profile
  thành công qua `/api/auth/login` + `/api/auth/me`.
- Public profile xác nhận đúng Technical/HQ; clearance lần lượt là internal,
  internal và confidential; role lần lượt là viewer, uploader và
  knowledge_approver/reviewer.
- Browser flow xác nhận actor Technical không nhận nội dung IT, còn
  `demo_owner_it` nhận đúng claim SLA 4 giờ và citation IT tương ứng.
- Credential chỉ được load trong process test; không ghi vào diff, report hoặc
  console.

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

### Trạng thái thực thi 2026-07-29

- Playwright Chromium harness tối thiểu có 3 test và chạy lại đạt `3/3` bằng
  Chrome hệ Chromium có sẵn qua `E2E_BROWSER_CHANNEL=chrome`; bundled Chromium
  chưa dùng được vì download bị treo trên máy này.
- Browser test xác nhận login/profile thật, SSE hoàn tất với claim SLA 4 giờ và
  citation; anonymous/session/CSRF bị chặn; actor Technical không rò claim hoặc
  nguồn IT.
- RAG health đạt `status=ok`, `rag_loaded=true`, activation hợp lệ/live và toàn
  bộ governed feature OFF. Cache identity là
  `d=IT|lvl=internal|s=HQ|pipe=0ef99535644c00f41c51`.
- Golden public `/chat` ban đầu bắt được lỗi deterministic khi payload ngoài hệ
  thống có `calculation_provenance=null`; regression test đã RED/GREEN, 101 test
  liên quan đạt và golden chạy lại đạt `5/5`.
- Benchmark metadata-only dùng 5 câu, không lỗi: concurrency 1 có complete P95
  `211 ms`; concurrency 5 có complete P95 `554 ms`. Runtime log sau khi khóa
  provider chain đã pin không có provider error, retry hoặc fallback vượt gate.
- Frontend unit `32/32`, production build và `npm audit` đạt.

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

### Trạng thái thực thi authoritative 2026-07-30

- CRAG window `20260730-crag-window-06-0090639` chạy tại commit `0090639`;
  preflight fixture đạt `9/9`, rollback hai flag đạt và provider failure/retry
  bằng `0`.
- Pair 01 chạy `candidate-first`: baseline `8/9`, candidate `9/9`;
  wrong-refusal giảm `1 -> 0`, correction `1`, repair `1`, cost ratio
  `1.001384`.
- Gate chỉ fail `latency_within_budget`: P95 `5863.22 -> 7921.84 ms`, ratio
  `1.351107`. Diagnostic chưa cô lập được deterministic code defect; generation
  P95 `3676 -> 5216 ms` và rerank P95 `694 -> 1085 ms`, nên không cấp quyền sửa
  code hoặc mở formal window mới từ evidence này.
- Dừng pair 02/03 đúng predeclaration. Không tạo series, authorization,
  activation bundle và không bật live flag.
- Window `20260730-crag-window-05-0090639` được giữ làm tombstone do operator
  dừng trước khi có đủ hai arm/gate; không xóa, resume hoặc dùng làm evidence.
- Diagnostic RC `1add3d5` ngày 2026-07-31 dùng đúng manifest, fixture fingerprint
  và provider hash của window 06. Provider smoke đạt `5/5`, retry `0`; candidate
  đạt `9/9`, P95 `9054.55 ms`, nhưng có `5` Voyage 429/fallback. Tiến trình bị
  dừng trước baseline theo stop rule, nên không có ratio và không được dùng làm
  formal evidence hay lý do sửa code.

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

### Kết quả cửa sổ hiện tại

- Root fix ở `fa8dc16` giữ pure deterministic math không gọi generation LLM
  thừa, giữ cross-document operands và phát đúng typed partial outcome.
- Window hợp lệ `20260730-window-04-fa8dc16` dùng fixture hiện hữu, không ingest:
  preflight đạt `16/16`, rollback đạt `2/2`; ba provider smoke đều `5/5`.
- Cả ba immutable pair đều đạt toàn bộ 23 gate. Candidate đạt `16/16` ở mỗi
  pair; provider failure/retry/leakage đều bằng `0`; latency ratio lần lượt
  `0.164652`, `0.255974`, `0.150771`.
- Series vẫn `production_eligible=false` vì CRAG chưa accepted và owner review
  mới `0/10`. Outcome:
  `reports/grounded-math/20260730-window-04-fa8dc16/window-outcome.json`,
  SHA-256
  `7cd852717a68e2a3455f8c018bf4d116bde3049a80bada46193fe34a2e9b5a6a`.
- Không tạo authorization/bundle và không bật flag. Window đã hoàn tất được giữ
  nguyên; không rerun để thay số.

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

### Kết quả hiện tại

- Commit `7e240cc` loại đường `DELETE`/shared ingest khỏi fixture prepare:
  chỉ chèn row thật sự thiếu; row xung đột, duplicate hoặc mất identity đều
  fail-closed trước write.
- Commit `b929920` khớp manifest với nguồn thật: dùng
  `source_row_id=table-1-row-1/2`, không bịa đơn vị `cái`, và giữ calculation
  `2 + 3 = 5`. Prepare trên staging ghi `bom_rows_inserted=0`; không
  INSERT/UPDATE/DELETE dữ liệu.
- Preflight hiện tại đạt `13/13`, `0` failure, fingerprint
  `b4066ab6ce9005715192d312c4ffa10b73512a027c5cf88858dea6bf71d1f90e`;
  manifest hash là
  `1ab4ec403f6501858d25f40ea329d96246f677a63044692632178d1ee95eb24e`.
- Window hợp lệ `20260730-window-01-fa8dc16` được predeclare và pin cùng commit,
  fixture, provider profile và execution context. Preflight đạt `13/13`;
  rollback và hai provider smoke đạt.
- Pair 01 đạt mọi gate: candidate complex pass rate `0.7`, branch/citation
  accuracy `1.0`, simple planner call `0`, latency ratio `0.902172`, cost ratio
  `1.47852`.
- Pair 02 giữ nguyên quality, branch/citation, safety và latency nhưng fail đúng
  `cost_within_budget`: `1.554088 > 1.5`. Pair 03 không chạy theo stop rule.
  Human review hiện `0/10`; disposition `inconclusive`, flag giữ tắt.
- Outcome authoritative:
  `reports/decomposition/20260730-window-01-fa8dc16/window-outcome.json`,
  SHA-256
  `4efb93b6167448f78d8102f27d60636069983cf27b92f8617a933fc01adb5e22`.
- Window xanh về sau ở `5f7b98b` không có declaration, chạy sau khi đã thấy
  Pair 02 fail và commit không chứa Query Decomposition cost fix. Artifact được
  giữ nhưng tombstone
  `reports/decomposition/20260730-window-01-5f7b98b/evidence-invalidation.json`
  cấm dùng cho authorization/release decision; không xóa hoặc ghi đè.

## Ticket 8: Hoàn tất GraphRAG và Community Summaries

### Mục tiêu

Chỉ mở Graph/Community khi provenance và independent review đạt.

### Việc làm

1. Export toàn bộ `21` edge hợp lệ, vẫn vượt tối thiểu 20 edge, có source
   document/page/version/quote.
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

### Kết quả hiện tại

- Validator hiện hành ở `fa8dc16` kiểm tra read-only `13` case và fail đúng
  `approved_edge_provenance_incomplete`: chỉ `20/22` approved edge đủ current
  provenance. Edge 2 dùng document superseded/non-servable; edge 24 có relation
  `APPLIES_TO` chưa thuộc verified source-evidence contract.
- Checkpoint RC `1add3d5` đã reconcile đúng batch `graph-eval-v1`: edge
  deterministic từ version superseded bị disable, edge `APPLIES_TO` quote giả
  bị disable sau khi validator xác nhận, proposal quote thật được approve và
  proposal giả được reject. Preflight đạt `21/21`, provenance completeness
  `1.0`, pending serving edge `0`, workflow approve/reject đạt.
- Mẫu số đổi từ `22` xuống `21` vì root fix bắt buộc loại edge stale, không phải
  hạ threshold. Không tạo edge thay thế giả để giữ mẫu số `22`; independent
  reviewer phải review toàn bộ `21` edge hợp lệ và precision vẫn phải `>=95%`.
- Community detection, generation và serving readiness chưa chạy vì Graph còn
  `0/21` independent review.
- GraphRAG và Community Summaries tiếp tục `inconclusive`; cả hai flag giữ tắt.
  Disposition:
  `reports/graph/20260730-gate-fa8dc16/gate-disposition.json`, SHA-256
  `5b61eb4300efd4935d671d91758bca894de5057039259028664f37a326902951`.

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

### Kết quả hiện tại

- Baseline trước provenance hardening từng đạt full backend, frontend `32/32`,
  production build, architecture `18/18` và browser E2E `3/3`; security
  targeted `101/101` và rollback/cache/strict-stream/preflight `75/75`.
  Các số này chỉ là historical evidence, không thay kết quả integrated
  verification tại `5f7b98b` bên dưới và cũng không được suy diễn sang checkout
  HEAD hiện tại.
- Production preflight Windows trước provenance hardening đạt `5/5`: migration
  current, Qdrant ready, activation `all_off` hợp lệ, không có seeded dev
  account active và RAG health ready. Kết quả này không còn là live evidence
  hiện hành sau `a886a8f`.
- All-off hot-cache smoke đạt `8/8` ở concurrency 1 và `8/8` ở concurrency 5,
  không busy/error; completion P95 lần lượt khoảng `70 ms` và `100 ms`. Đây
  không phải cold-path benchmark và không chấm correctness.
- Launcher và health preflight Windows đã bắt buộc `deployment_id`, current
  `git_sha` và fingerprint từ trạng thái SQL/Qdrant sau migration. SQL được đọc
  trong transaction `SERIALIZABLE`; Qdrant payload/vector/config được hash hai
  lượt và fail nếu drift. Runtime chưa được restart và recapture provenance,
  nên hardened live preflight vẫn chưa đạt.
- Offline integrated runner đã chạy lại trên worktree sạch tại commit `5f7b98b`:
  `63/63` test cache/strict-stream/rollback đạt; security matrix `15/15`,
  leakage `0`. Capability đạt nhưng `ready_for_live_matrix=false` vì
  prerequisites và release decisions vẫn chưa hoàn tất.
- Effective release matrix giữ cả 7 governed flag OFF. Controlled-demo fallback
  đạt `ready_for_demo_matrix=true`; trạng thái này không cấp quyền live.
- Full backend offline đạt `2467 passed, 1 skipped, 0 failed`; coverage line
  `92.286356%`, branch `84.991334%`; frontend `32/32`, production build và
  architecture `18/18` đều đạt trên cùng commit.
- Active Python environment và frontend production dependencies audit sạch.
  `requirements.lock.txt` là snapshot local không canonical và vẫn bị loại khỏi
  install/CI path; không mở lại dependency-lock scope trong rollout này.
- Final review độc lập có `0` Standards finding, `0` Spec finding và `0`
  blocking security finding; reviewer không gọi provider hoặc mutate live data.
- Restore drill fail-closed đã bind SQL BackupSetGUID/LSN và Qdrant snapshot
  checksum, không `REPLACE`, không xóa và không auto-cleanup. Drill thật chưa
  chạy vì chưa có disposable target cùng backup/snapshot location được xác
  nhận; không dùng dữ liệu hiện hữu để thử.
- Production audit cho phạm vi bật governed RAG feature giữ ở `49/100`, trạng
  thái `blocked`. Baseline all-off và offline capability hoạt động, nhưng chưa
  đủ restore/runtime provenance, live matrix, quyết định và review để bật
  feature.
- Không dùng Docker, không sửa/xóa Docker artifact và không coi Docker là điều
  kiện triển khai trong đường Windows/LAN này.

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

### Decision pack hiện tại

JSON máy đọc được nằm tại
`data/integrated_hardening_v1/rag_production_decision_pack.json`.

| Feature | Disposition hiện tại | Khuyến nghị | Gate kế tiếp |
| --- | --- | --- | --- |
| CRAG + Claim Repair | Inconclusive; window 06 fail latency, diagnostic Voyage bị loại vì `5` HTTP 429; diagnostic Jina chỉ là supporting evidence trên RC cũ | `keep_off` | Freeze RC Jina-primary mới; chỉ mở cửa sổ formal sau clean interleaved diagnostic |
| Grounded Math | Inconclusive; 3/3 pair kỹ thuật đạt, CRAG và review `0/10` còn thiếu | `re_evaluate` | CRAG accepted và owner review đủ 10 case; không rerun window |
| Late Interaction | Rejected | `keep_off` | Chỉ mở lại khi thiết kế mới vượt quality gate |
| Query Decomposition | Inconclusive; Pair 02 fail cost `1.554088 > 1.5`, Pair 03 không chạy | `re_evaluate` | Chẩn đoán overhead, predeclare window mới và owner review |
| Graph Retrieval | Inconclusive; provenance `21/21`, review `0/21` | `re_evaluate` | Independent review toàn bộ 21 edge, precision tối thiểu 95% |
| Community Summaries | Inconclusive; không chạy vì Graph chưa accepted | `re_evaluate` | Hoàn tất Graph rồi mới detection/generation/review/global eval |

Kết luận kỹ thuật hiện tại: giữ activation profile `all_off`,
`ready_for_live_matrix=false`, không cập nhật release ledger và không tạo
feature-on activation bundle trước chữ ký của chủ dự án.

## Decisions so far

- Dùng Jina `jina-reranker-v3` làm default; Voyage `rerank-2.5-lite` là fallback
  một lần, sau đó mới về deterministic local fusion.
- Jina được owner cho phép dùng surface `reranking` trong production qua
  `V0042`; authorization vẫn fail-closed khi profile/key/review không hợp lệ.
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
