# Query: gói chuẩn bị hậu-pilot ngày 2026-09-05

Trạng thái pre-freeze ngày 08/09: **đang chốt checkpoint source offline**, chưa
release freeze hoặc hoàn tất toàn roadmap. Base trước commit là `96c78e3`;
exact commit cuối, hashes và kiểm binding được ghi bên ngoài tracked source tại
`.local/offline-freeze-20260908/`. Không dùng base làm binding cho code mới.
Các kết quả và mô tả
“chưa có” trong các mục bên dưới là lịch sử từng bước, không thay cho tóm tắt
hiện tại này.

Đã có trong source hiện tại:

- Query gate wiring, CRAG isolation, Scheduled Host/Job và proof của checkpoint
  trước; review Standards/Spec checkpoint đó không có finding chặn.
- Matrix declaration/approval/source guards, exclusive run-root claim và
  dispatcher tuần tự sáu arm với môi trường child explicit, zero provider retry,
  dừng window khi arm lỗi, receipt/result hashes và terminal receipt.
- Worker observation ledger, đối soát report/quality binding và loader nối
  execution receipts với trace/evaluation evidence của ba row.

Checklist xác minh trước khi chốt delta này:

1. Full unit trên code đứng yên sau sửa authority đã đạt ngày 08/09:
   **3556 passed, 2 skipped, 0 failures/errors**, exit 0, 611.78 giây theo pytest.
   Một warning deprecation của Starlette/httpx; không đổi dependency trong scope.
   JUnit tại `C:/Users/bao.nguyen/AppData/Local/Temp/query-matrix-final-20260908.xml`,
   SHA-256 `c1002336ff5215a33a59f7c3975e0d06333bfc1c80fd8d475df75ce4411fa888`.
2. Integration qua production process bootstrap và real worker đã đạt với runtime
   offline: child chạy hai request và đóng runtime; khi request đầu lỗi thì không
   chạy request hai, không ghi eval report và vẫn đóng runtime. Fixture chỉ thay
   runtime/settings/preflight/logging/intent trong package initializer của bản
   source tạm; worker/evaluator/bootstrap giữ source production. Có network audit
   guard trong child. Nhóm process/observer/quality đạt **47/47**, 43.70 giây sau
   khi bổ sung hai ca này; production code không đổi so với full-unit phía trên.
3. Coverage branch+statement cùng lượt full unit cho 5 module matrix đạt
   **82.77%** tổng, exit 0 với ngưỡng 80%:
   dispatcher 74%, evidence 86%, matrix 96%, quality 97%, worker 84%.
   JSON tại `C:/Users/bao.nguyen/AppData/Local/Temp/query-matrix-final-20260908-coverage.json`,
   SHA-256 `6acca4b34c4345619f54ebf6655d9dbe4a448c8ecbb7d9a6b1cd5f8358cf83f3`.
   Báo cáo đo tiến trình pytest chính, không gộp source tạm trong subprocess;
   không được diễn giải 83% tổng thành mỗi module đều đạt 80%.
4. Hai lượt review độc lập sau đó đã hoàn tất: Spec tìm P1 (exit 2 của baseline
   chất lượng âm bị coi là lỗi thực thi), Standards tìm P3 (coordinator quá dài).
   Bản sửa nhận strict integer 0/2 ở dispatcher và reconciliation, vẫn giữ
   observation binding/completeness và cấm authority claims; lỗi worker vẫn
   dừng window. Receipt/result và terminal được tách ra, giữ exclusive create,
   flush/fsync và append kết quả chỉ sau ghi thành công. Coordinator còn 46 dòng.
   Regression declaration/process/evidence đạt **83/83**, 114.79 giây, gồm
   baseline exit2 vẫn hoàn thành sáu arm và worker thật với quality âm.
   Full unit/coverage sau sửa đã đạt **3557 passed, 2 skipped, 1 warning**, exit 0,
   601.37 giây; coverage **82.74%** trên năm module (ngưỡng 80%). JUnit và coverage
   JSON có prefix `query-findings-20260908` tại thư mục Temp. Hai reviewer đã xác
   nhận P1/P3 resolved, không có finding mới trong phạm vi sửa; đây là source
   review, không chạy test/live resource. Các số full unit/coverage ở mục 1/3
   là lịch sử trước hai bản sửa này, được thay thế bởi kết quả tại đây.
   Hai finding đã đóng; không suy ra quyền pilot, provider traffic hoặc rollout.

Kiểm tra tại chỗ sau coverage phát hiện dispatcher chỉ chặn authority ở hai
cờ top-level, còn reconciliation chặn cả nested quality/default rollout.
Regression mới RED 4/6: dispatcher nay dùng cùng quy tắc false-only cho ba
cờ ở cả result và quality, từ chối ngay trước arm kế tiếp; quality sai type
cũng bị reject. Declaration/evidence **75/75 passed**, 73.28 giây. Full-unit
và coverage ở checklist phía trên đã được chạy lại sau delta này.

Fixture quality/trace là bằng chứng regression offline, không phải kết quả RAG
thực hoặc owner review. `quality_acceptance_verified`, `matrix_accepted` và các
cờ authority vẫn false. Không có CLI run hoặc tự động phát sinh quyền dispatch.
Dependency đã được đánh giá lại: 12 advisory/5 package đều `assessed_open`,
`no_direct_application_sink_found`, **security-green=false**; đây không phải
`not_affected` hoặc owner risk acceptance. Kết luận và upstream references tại
[CRAG/Query dependency assessment](crag-offline-readiness-20260905.md).
Không có quyền mới để chạy pilot/matrix,
không push/merge/default activation. Commit chỉ lưu code/doc/test offline,
không chứa `.local` runtime, approval thật hoặc ciphertext của run cũ.

## Hồ sơ chuyển tiếp sau source freeze ngày 08/09

Lượt verification `query-offline-freeze-20260908` giữ nguyên 663 source/test/
requirements files nhưng có 8 setup errors, 3549 pass/2 skip: Python tạo
`scripts/ops/__pycache__` trong synthetic Git root, guard clean-source reject.
Không sửa validator hoặc source để bỏ guard. Lượt cuối dùng
`PYTHONDONTWRITEBYTECODE=1`, `RUN_QUERY_TASK_PROOF=0` và live-test opt-ins OFF;
chỉ receipt có exit 0 mới được dùng làm freeze verification. Giữ JUnit lượt lỗi
để phân biệt harness invocation failure với product regression.

Lượt `query-offline-freeze-final-20260908` đã đạt **3557 pass, 2 skip,
1 warning**, exit 0, 587,80 giây; coverage tổng năm module **82,74%**.
Hai skip là quyền tạo symlink và Scheduled Task opt-in; không đăng ký task.
Source/test/requirements inventory 663 file giữ nguyên. JUnit/coverage JSON
được lưu trong gói ignored cùng receipt theo exact commit sau freeze.

- Gói ignored `.local/offline-freeze-20260908/` lưu inventory source, kết quả
  test/coverage, dependency audit, disposition proposal và binding theo commit
  thực tế. Các file này không phải release decision hoặc owner approval.
- Dùng validator hiện có kiểm owner decision/formal evidence/review từ evidence
  worktree sạch. Phân biệt `evidence_source_commit` lịch sử với source RC; nếu
  validator reject thì dừng trước activation draft, ghi rõ evidence phải tái tạo.
  Không sửa artifact lịch sử hoặc ép validator pass.
- Chỉ tạo activation/consolidated launch **draft** nếu đủ đầu vào; không gọi
  finalize, register/start hoặc dispatch. Schedule plan đóng băng 100 card/hash
  và offset tối thiểu 24 giờ; absolute start/expiry chỉ khóa khi có future exact
  approval, không tạo lịch đã hết hạn trong lúc chờ.
- Hồ sơ gate phải phân biệt hash source/interpreter/host đã đo offline với
  provider identity, snapshot và live preflight chưa xác minh. Không gọi database,
  provider hoặc runtime để biến trường pending thành green trong scope này.
- Incident có proposal riêng khóa sáu capture; owner retention/deletion vẫn
  pending. Pilot đạt mới mở review/deletion/final gate và matrix evidence thực.
- CRAG có binding source/manifest/runner riêng trong gói freeze; recovery,
  declaration, smoke và diagnostic mới vẫn phải có authorization riêng.

Các mục đánh số bên dưới là lịch sử triển khai và runbook prospective; không
thay thế checkpoint/freeze receipt hoặc cấp quyền mở window.

Phạm vi: chuẩn bị offline trong worktree riêng, dựa trên source
`38620eb02806278fb689446c34f2d99e1f6e0746`. Đây là checklist thực thi và các
khoảng trống đã kiểm tra từ source; không phải owner review, authorization,
release decision, terminal receipt hoặc bằng chứng chạy matrix.

Checkpoint triển khai offline tiếp theo ngày 05/09: đã sửa CLI gate trong
worktree chuẩn bị, chưa commit/freeze/activate. `gate` nhận và truyền đủ
`--trace`, `--deletion-journal`, `--source-root` tới validator hiện có; không
thay điều kiện acceptance. Regression dùng bộ 100 card/review/deletion giả lập
đã RED vì parser từ chối ba option, rồi GREEN sau bản sửa. Thiếu input, sai
path hoặc đổi bytes trace/journal đều giữ `human_review_passed=false`;
automated-only CLI vẫn dùng được nhưng không tự accepted/default rollout.
Các mô tả baseline `38620eb` bên dưới vẫn là lịch sử của source chưa sửa.

## 1. Trạng thái đầu vào và ranh giới áp dụng

Read-only snapshot lúc khoảng 09:48 +07 ngày 2026-09-05:

- Launch root: `.local/worktrees/query-pilot-fail-closed/.local/query-pilot-launch-38620eb-20260905-01` tính từ repo chính.
- Artifacts thực nằm trong thư mục con `run/`: `pilot.wal.jsonl` có đúng 6 dòng,
  6 card ID khác nhau; `review-captures/` có 6 file.
- First attempt `2026-09-05T01:11:43Z`, last completion
  `2026-09-05T02:24:42Z`. Snapshot listing chưa có terminal/result/stop receipt.
- Việc đối soát process, nguyên nhân gián đoạn và disposition được ghi riêng
  trong báo cáo incident cùng đợt; số WAL/capture ở đây không chứng minh process
  còn sống, pilot thành công hoặc owner đã xem answer.

Root đã consume không được dùng để chạy bù 94 card hoặc thay lịch. Bộ review
thông thường hiện yêu cầu 100 WAL hợp lệ và đúng 20 capture đã freeze; vì vậy
root 6 card không đủ điều kiện tạo success review pack. Sáu ciphertext cần được
xử lý theo disposition incident/retention riêng, giữ đúng hash/count thực tế;
không tạo receipt giả cho 20 capture, không sửa WAL để đạt điều kiện của tool.
Gói này không giải mã, xóa capture hay ghi bất kỳ artifact nào vào root đó.

## 2. Đã có và bằng chứng còn phải có

| Hạng mục | Source đã có | Evidence thực tế còn thiếu |
| --- | --- | --- |
| Sample/capture | Schedule freeze 20/100, hai card mỗi complex case; DPAPI CurrentUser; hash-bound answer và structured citations | Run đủ 100 card với đúng 20 ciphertext, cùng authorization/schedule/source |
| Review pack | `query_pilot_review_pack.py` kiểm root, clean exact commit, WAL/trace/tool hashes và sample | Pack metadata-only từ run đủ điều kiện |
| Owner review | `query_pilot_review_ui.py` hiển thị cục bộ question/answer/citation, nhãn answer/citation/safety/decision/reason | Owner thực sự xem từng item; không thể thay bằng agent gán accepted |
| Cleanup | UI ghi result trước khi xóa; lifecycle có journal, quarantine và resume pending deletion | Receipt/journal hash-bound và thư mục capture rỗng; nhãn rejected vẫn cần cleanup |
| Final gate | `build_pilot_gate()` kiểm 100 card, cadence, tối thiểu 24 giờ, identity, review và deletion | Automated + human review cùng pass cho cùng root; default rollout vẫn false |
| Math/Query matrix | Manifest Query-only, manifest 3 BOM interaction và bộ aggregate/integrity integrated | Runner/contract cho đúng ba tập feature, bằng chứng mới trên cùng release candidate |

Sources chính: `scripts/ops/query_decomposition_pilot_gate.py`,
`scripts/ops/query_pilot_review_artifacts.py`,
`scripts/ops/query_pilot_review_integrity.py`,
`scripts/ops/query_pilot_review_ui.py` và
`scripts/ops/query_pilot_capture_lifecycle.py`.

## 3. Lỗ hổng entrypoint final gate tại baseline 38620eb

CLI `python -m scripts.ops.query_decomposition_pilot gate` ở baseline chỉ nhận
và truyền review pack/result/deletion receipt/capture directory. Trong khi đó
`build_pilot_gate()` còn yêu cầu `trace_path`, `deletion_journal_path` và
`source_root`; `_review_valid()` trả false nếu một trong các đối số đó là None.
Vì CLI không nối ba đối số này, một bộ review hợp lệ vẫn không thể đạt
`human_review_passed` qua CLI baseline. Hàm Python đã có đầy đủ tham số.

Việc sửa cần nối parser tới hàm và regression test CLI trên fixture đủ 100
card/review/deletion; giữ các ca thiếu hoặc sai binding fail closed. Không hạ
điều kiện `_review_valid`. Tình trạng bản sửa offline, nếu có, phải đọc từ diff
và test ở worktree chuẩn bị; không suy ra rằng run 38620eb đã dùng source sửa.

`validate_authorized_source()` của pack/UI yêu cầu HEAD trùng exact commit
authorization và worktree sạch, kể cả untracked file không ignored. Do đó
worktree chuẩn bị đang có tài liệu/code sửa không phải source được phép dùng
để review root cũ. Sửa source không tự cập nhật authorization, review-tool
hashes hoặc quyền của một root đã consume.

## 4. Trình tự cho một pilot tương lai đủ điều kiện

Đây là trình tự prospective; không áp dụng như lệnh tiếp tục root 6 card.

1. Đối soát final operator/runtime disposition và immutable WAL/trace: đúng
   100 scheduled card, mỗi card một attempt, unique trace, không retry,
   replacement hoặc catch-up; cadence và thời lượng tối thiểu 24 giờ đạt.
2. Chạy automated reconciliation từ đúng authorization, schedule và runtime
   identity digest đã freeze. Chưa có owner review thì chỉ được kết luận
   `human_review_pending` khi automated gate thực sự pass.
3. Tại source sạch đúng commit authorization, tạo pack bằng
   `scripts.ops.query_pilot_review_pack`. Đối chiếu pack count: 20 encrypted
   sample cộng mọi deterministic refusal ngoài sample cần owner review;
   `review_item_count` có thể lớn hơn 20.
4. Owner chạy `scripts.ops.query_pilot_review_ui` dưới Windows user có quyền
   DPAPI CurrentUser tương ứng. Review từng item; không chụp/export plaintext,
   không auto-label. Đóng trước khi finalize thì review chưa hoàn tất.
5. UI lưu result metadata-only rồi xóa ciphertext bằng lifecycle có journal.
   Nếu receipt write thất bại, gate vẫn fail closed; dùng đúng resume path đã
   có để hoàn tất deletion, không tự viết receipt bằng tay.
6. Recompute final gate với toàn bộ artifacts: schedule, authorization, WAL,
   runtime identity, pack, result, receipt, capture directory, trace, deletion
   journal và source root. Kiểm cả `automated_gate_passed`,
   `human_review_passed`, `pilot_accepted`; `default_rollout_authorized` vẫn false.
7. Sau pilot/review accepted mới tới matrix, technical review, owner release
   decision và signed ledger/bundle riêng. Không dùng 6/100 hoặc unit test để
   bỏ qua bước này.

CLI đã kiểm từ source để đọc giao diện mà không mở UI, ghi pack hoặc gọi live:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location).Path 'src')
python -m scripts.ops.query_pilot_review_pack --help
python -m scripts.ops.query_pilot_review_ui --help
python -m scripts.ops.query_decomposition_pilot gate --help
```

Khi triển khai thật cần chọn interpreter tuyệt đối đã probe từ launch packet;
ba dòng trên chỉ là help inspection. Không cung cấp một lệnh post-pilot chạy
thẳng vào root hiện tại vì root đó chưa đủ điều kiện. `gate --output` có ghi
file; review UI có xóa ciphertext sau finalize, không phải lệnh read-only.

## 5. Checklist Math-only, Query-only, Math+Query

Manifest hiện tại được hash trực tiếp ở worktree chuẩn bị:

| Scope | Manifest | Case count | SHA-256 |
| --- | --- | --- | --- |
| Query-only | `data/decomposition_eval_v1/eval_manifest.jsonl` | 13: 10 complex + 3 simple | `6976cbbe4c9500b7c0755c5944775e326106a780bb2910bfa71167787a1d0bf8` |
| Math+Query interaction | `data/decomposition_eval_v1/math_query_interaction_manifest.jsonl` | 3 BOM complex | `d21495e86faca22c455f745a7b9fd7f249643f31e76f36e086f8af4467b0932b` |

Ba BOM case giữ `requires_grounded_math=true`, `full_answer` và phép `sum`;
không đổi nhãn để ép pass. Query-only và interaction cần preflight riêng,
không trộn vào một artifact.

Khoảng trống runner đã xác minh:

- `scripts.decomposition_eval.run_rollout` yêu cầu đúng 13 Query case và ép
  Grounded Math OFF ở cả hai arm. Không dùng runner này cho manifest 3 case.
- `data/integrated_hardening_v1/matrix.json` hiện có năm combination đơn/phụ
  thuộc: CRAG/Claim, Math, Query, Graph, Community; chưa có Math+Query.
- `scripts/integrated_eval/` có preflight, offline verifier, result/load
  aggregation và metadata composer. Những phần này không tự tạo một runner
  phát request có contract riêng cho bộ ba Math/Query/Math+Query.
- Runbook integrated hiện nói đúng rằng interaction manifest chờ một matrix
  runner được declare. Không sửa matrix toàn cục hoặc mở Graph/Community để
  dùng một workflow tổng quát khi mục tiêu chỉ là Math/Query.

Checklist chuẩn bị trước khi freeze runner/matrix window:

- [ ] Chốt versioned contract cho ba tập `{Math}`, `{Query}`, `{Math, Query}`,
  baseline tương ứng, manifest mỗi row và concurrency được phép; đặt các feature
  khác OFF trong từng row. Concurrency 1/5 là đề xuất để đối chiếu lịch sử Math,
  chưa phải quyền phát traffic cho window mới.
- [ ] Chốt exact source commit và tool hashes của runner mới; khai báo đủ
  manifest, SQL/Qdrant snapshot, collection, provider identity, governance scope,
  bundle/flags và cache namespace. Cùng baseline/candidate pair phải cùng
  identity và concurrency. Math release cũ không tự làm evidence cho commit mới.
- [ ] Unit tests dùng fake data: reject mixed scope, Math flag OFF trong
  interaction, extra feature ON, stale/wrong commit, drift manifest/snapshot,
  missing raw trace, missing security/load reference; verify branch citations,
  calculation provenance và budgets đúng contract.
- [ ] Fresh preflight/rollback/smoke và authorization cụ thể khi được phép
  chuyển sang live. `scripts.decomposition_eval.preflight` CLI gọi SQL/Qdrant
  thật; tên preflight không có nghĩa là offline.
- [ ] Evidence từng row: baseline/candidate eval + immutable raw trace/snapshot,
  benchmark/load cùng concurrency, security results, budgets, cost, retries,
  fallback, first-token/completion P50/P95 và exact flag configuration.
- [ ] Recompute integrated gate, technical review và owner decision riêng;
  phát hành signed ledger/bundle chỉ sau các gate này. Nếu một window fail,
  giữ disposition và tạo window mới theo governance hiện hành.

## 6. Verification của gói chuẩn bị

Source inspection và manifest hashes ở trên được kiểm trực tiếp từ baseline.
Suite offline đã pass 100/100 test, exit code 0; collect-only xác minh count:
`test_query_pilot_review_integrity.py` (2), `test_query_pilot_review_pack.py` (4),
`test_query_pilot_review_ui.py` (5), `test_query_pilot_review_artifacts.py` (3),
`test_query_decomposition_pilot.py` (23), `test_decomposition_eval_fixture.py`
(63), đều trong `tests/unit/`. Ba live-test opt-in được đặt `0` trong process
chạy test, execution context là `test`; interpreter sử dụng
`C:\Users\bao.nguyen\Documents\ChatBotProject\chat_env\Scripts\python.exe`.
Không chạy DB/Qdrant/provider preflight, không mở UI review, không claim pilot
acceptance hoặc coverage toàn repo từ suite này. Suite green baseline vẫn chưa
bao phủ entrypoint final gate thiếu ba đối số nêu ở mục 3. Lượt triển khai tiếp
theo đã bổ sung regression CLI vào
`tests/unit/test_query_decomposition_pilot.py`; suite liên quan Query/CRAG và
runtime zero-retry pass 108/108 trong 17.34 giây. Đây là synthetic offline
evidence, không phải human review hoặc pilot evidence cho root thật.

Validation mở rộng: focused suite 144/144 pass; Query module branch+statement
coverage 85%, combined với CRAG 83%. Full unit suite 3236 pass/1 baseline
failure do hash runner trong preparation packet cũ; đã tái hiện trên source
sạch 38620eb. Không sửa binding lịch sử để đạt green. Matrix/launcher và
human incident disposition chưa triển khai, không được gọi là hoàn tất chỉ
vì CLI gate đã có đủ input.

## 7. Contract chuẩn bị matrix `math-query-isolation-v1-draft`

Đây là specification offline cho runner riêng, không thay
`data/integrated_hardening_v1/matrix.json`, không cấp quyền dispatch và không
kết luận runner đã tồn tại. Mục tiêu là so sánh mỗi cấu hình với all-off trên
cùng manifest; không suy ra tương tác nhân quả hay đóng góp riêng từng feature
từ chênh lệch giữa các row có bộ câu hỏi khác nhau.

| Row | Baseline | Candidate | Manifest | Số case |
| --- | --- | --- | --- | --- |
| Math-only | All seven flags OFF | Grounded Math only | `data/grounded_math_eval_v1/eval_manifest.jsonl` | 16 |
| Query-only | All seven flags OFF | Query Decomposition only | `data/decomposition_eval_v1/eval_manifest.jsonl` | 13 |
| Math+Query | All seven flags OFF | Grounded Math + Query Decomposition | `data/decomposition_eval_v1/math_query_interaction_manifest.jsonl` | 3 |

Math manifest SHA-256 được kiểm lại:
`650af531b2b2f349c8fef85dc8a6681592c3d4af523f3060f66259cc469af0c3`.
Hai manifest Query giữ SHA ở mục 5. Không đổi ba BOM case để ép vừa runner
Query-only đang yêu cầu 13 case và Math OFF.

Contract phải được runner kiểm trước mọi dispatch:

- Concurrency của evaluation là 1, một attempt/case/arm; provider retries,
  replacement và catch-up đều 0. Dừng toàn window khi provider failure;
  không chọn chạy lại riêng row lỗi. Load concurrency 5 là window riêng chưa
  được chuẩn bị/authorize, không trộn vào kết quả evaluation này.
- Cả bảy canonical feature flag phải được ghi rõ ở mỗi arm; CRAG, Claim
  Repair, Late Interaction, Graph và Community luôn OFF. Merge provider/host
  environment trước khi áp exact flags; cache namespace riêng cho mỗi cấu hình.
- Cùng pair phải bind cùng clean source commit, tool hashes, manifest bytes,
  SQL/Qdrant snapshot, collection, provider/model identity và concurrency.
  Không dùng evidence Math release cũ thay cho baseline của commit mới.
- Raw trace và evidence phải bind row/arm/case, cấu hình đã thực thi, budgets,
  retry/fallback/cost, citations và calculation provenance. Gate không chỉ tin
  metadata do runner tự công bố; thiếu trace hoặc drift identity thì reject.
- Latency/cost và quality được tổng hợp riêng từng row; không gộp ba BOM case
  vào floor complex/simple của Query-only, không gọi ba case là độ phủ đầy đủ.
- CLI chuẩn bị mặc định chỉ xuất draft. Live path cần fresh declaration,
  authorization, preflight/rollback/smoke và never-used root. Không bật default
  flags, không tạo release ledger từ kết quả offline.

Tiêu chí bàn giao runner còn mở: regression reject extra feature, mixed row,
Math OFF trong interaction, stale commit/manifest/snapshot, missing trace và
missing security/load evidence; test budgets/provenance qua entrypoint với
transport giả; review Standards/Spec/security trước freeze. Chưa có traffic
contract được thực thi, benchmark hay acceptance từ specification này.

## 8. Kiểm tra dependency trước freeze

Interpreter dùng chung `chat_env/Scripts/python.exe`: `pip check` pass, nhưng
`pip_audit --local --progress-spinner off` exit 1, báo 12 advisory ở 5 package.
Đây là kết quả scanner tại lượt chuẩn bị 05/09, chưa phải phân tích mức độ
khai thác/reachability của từng advisory trong ứng dụng.

| Package hiện tại | Số advisory | Fix version scanner đề xuất |
| --- | --- | --- |
| GitPython 3.1.58 | 4 | 3.1.59 |
| pip 26.1.2 | 1 | 26.2 |
| pypdf 6.15.0 | 3 | 6.16.1 cho toàn bộ 3 advisory |
| tornado 6.5.7 | 3 | 6.5.8 |
| unstructured 0.22.32 | 1 | 0.24.0 |

Không nâng cấp môi trường dùng chung, không sửa lockfile để che kết quả.
Security-green/release readiness vẫn chưa đạt. Cần đánh giá dependency và
kiểm compatibility trong môi trường tách biệt trước khi cân nhắc rollout;
local code freeze, nếu có, không phải release authorization hay audit-clean.

Verification mới: preparation 36/36 pass; Query/CRAG/operator suite 123/123
pass (83.57 giây) trước thay đổi defer-capture; Windows Job suite sau sửa lỗi
thread-handle close pass 14/14. Host + job suite 29/29 pass (1.39 giây), gồm
terminal cleanup chỉ sau job-empty và port-release proof. Full unit suite
**3273/3273 pass trong 401.26 giây**, exit 0; một cảnh báo deprecation từ
Starlette test client/httpx, không tự thay dependency để tắt warning.
Ba test bổ sung sau collect của full suite (success giữ capture, journal drift,
delayed port release) pass trong host suite 18/18. Scheduled Task thử nghiệm
chạy cả host/job suite 32/32, không skip, đã cleanup task và proof PIDs.
Fixture bổ sung đã materialize activation/pilot authorization rồi prepare và
validate host packet trên clean temporary Git repo, dùng nguyên validator
thật; cùng fixture reject executable hashes lệch, host root đã consume và run
root đã tồn tại. Host/job suite mới **33/33 pass**, chưa chạy lại full suite
sau test bổ sung này. Không dùng artifact/approval của run thật trong fixture.
Coverage command hiện chỉ đo host ở source path gốc (42%), không cộng đường
validate chạy từ bản copy trong fixture; vì vậy chưa công bố coverage host
đạt 80%. Còn kiểm run_packet qua process giả và review cuối; chưa có task
governed dùng operator thật hoặc quyền rollout từ synthetic evidence.

Lượt kiểm tiếp theo: fixture được mở rộng gọi `run_packet` thật với Win32
CreateProcess được thay tại OS boundary bằng Python process giả hữu hạn.
Không mock validator hoặc hàm host. Hai nhánh exit 0/7 pass: job-empty và
port-release được kiểm thật; success giữ capture, failure xóa tập capture
tổng hợp qua journal hiện có; receipt không chứa token test; packet đã consume
không thể dispatch lần hai. Host/job suite **34/34 pass trong 21.84 giây**.
Coverage đo riêng từng bản copy source trong repo fixture, chưa hợp nhất
source-path aliases nên không dùng aggregate 71% đó làm coverage production.

Test subprocess gọi wrapper PowerShell mặc định (không `-Register`/`-Start`)
đã bắt lỗi module discovery của Windows PowerShell khi kế thừa PSModulePath
từ phiên cha: `CommandNotFoundException` trước đọc packet. Bản sửa nạp hai
module Management/Utility từ chính `$PSHOME`; cùng test chuyển RED sang GREEN,
xác nhận `task_registered=false` và run root chưa tạo. Lỗi trả mã allowlist,
phase và tên exception type, không xuất nội dung exception/packet/credentials.

Coverage kiểm lại sau thêm CLI prepare/validate success và reject: host **85%**,
WindowsJob **92%**, combined branch+statement **87%** (283 statements,
96 branches). Bộ **34/34 pass trong 24.79 giây**. CoverageData từ source gốc
và các temporary copies được map về cùng path chỉ sau kiểm bytes bằng nhau;
không cộng test code vào tỷ lệ. Kết quả này thay cho báo cáo source-path
chưa hợp nhất ở trên; không phải coverage toàn repository hay provider proof.

## 9. Runbook launcher chuẩn bị, chưa thực thi pilot

- `scripts/ops/query_pilot_scheduled_host.py prepare`: nhận exact source commit
  và operator CLI arguments sau `--`; kiểm authorization đã materialize, source
  sạch, never-used root; ghi packet dưới `.local` ngoài run root.
- Host `validate`: kiểm packet SHA, interpreter, host/job/task-script hashes,
  input bytes, authorization và root freshness; không khởi động runtime.
- `scripts/ops/query_pilot_scheduled_task.ps1 -Packet ... -PacketSha256 ...`:
  mặc định chỉ kiểm tra, trả `task_registered=false`. `-Register` tạo task
  on-demand; `-Register -Start` yêu cầu chạy. Không trigger/restart/catch-up.
  Chỉ dùng các nhánh ghi/chạy trong future exact-authorized window.
- Task gọi host `run`; host kiểm lại authorization, interpreter, token và port,
  consume sibling `<run>-scheduled-host` một lần rồi tạo operator trong job.
  Host receipt không thay final gate, operator evidence hoặc human review.

Task user phải có `RAG_SERVICE_TOKEN` trong environment của scheduled process.
Environment launcher shell không tự truyền sang Task Scheduler; không ghi
token vào packet/XML/arguments để khắc phục. Thiếu token thì reject trước
consume/spawn. Provider/config identity vẫn cần declaration và frozen health.

Current-user Interactive/Limited yêu cầu user giữ đăng nhập, không cam kết qua
logout/reboot. Host chết cứng khiến job handle đóng và dừng CreateProcess
descendants, nhưng không thể bảo đảm ghi receipt. Sibling consumed root cấm
chạy lại; reconcile read-only và disposition riêng, không restart task cũ.

Operator dưới host hoãn capture deletion; host chỉ terminal-cleanup khi
job-empty và port-release đều xác nhận. Success giữ capture cho human review.
Cleanup lỗi/thiếu proof ghi failed/deferred, không xóa WAL hay giả runtime-stop.

Review: primitive có independent review, hai finding đã xử lý. Standards/Spec
cuối đã trả kết quả: thiếu proof nhánh đăng ký task thật; Standards còn phát
hiện ambient environment có thể yêu cầu operator hoãn cleanup, và test quá
lớn cần tách lifecycle. Proof task đã bổ sung ở checkpoint dưới; binding
cleanup đang được sửa, chưa có signoff/freeze toàn launcher.

## 10. Gói window kế tiếp: đầu vào còn phải freeze

Không tạo authorization có thời hạn khi source còn dirty hoặc tests/review
chưa đóng. Gói chuẩn bị này không điền commit/root/schedule giả để trông sẵn
sàng. Sau local freeze, thứ tự materialization giữ nguyên workflow hiện có:

1. Bind clean candidate commit và hash operator/host/job/task script; kiểm
   interpreter và provider configuration mà scheduled user thực sự đọc được.
2. Dùng owner-decision/evidence references còn hợp lệ qua validator; nếu
   evidence thay đổi thì không mang acceptance 39/39 cũ sang mặc định.
3. Tạo activation draft và consolidated launch draft dưới never-used launch
   root. Draft khai báo Query-only, 100 card tối thiểu 24 giờ, concurrency 1,
   zero retry/replacement/catch-up, đúng SQL/Qdrant snapshot và port mới xác minh.
4. Fresh exact authorization phải bind chính draft bytes, source/root và
   lịch tương lai; không reuse approval ngày 05/09 đã consume. Fresh live
   preflight/rollback/smoke chỉ thực hiện trong traffic scope được phép riêng.
5. Materialize activation/pilot contract, prepare host packet, kiểm wrapper ở
   default mode; sau đó mới xét register/start. Missing token, busy port,
   stale code/hash, expired authorization hoặc root tồn tại đều phải reject.
6. Sau terminal, giữ disposition riêng và kiểm host receipt cùng operator
   artifacts/WAL/trace. Completed operator chưa phải accepted pilot; review và
   deletion/final gate vẫn là các bước riêng. Không tự bật default Query.

Hiện chỉ hoàn tất checklist/offline harness; fresh live window chưa được
materialize, register hoặc dispatch. Các bước trên không phải lệnh chạy lại
run cũ hay authorization mới do agent tự cấp.

## 11. Full-suite checkpoint cuối

Full unit suite mới **3278/3278 pass**, exit 0, **404.94 giây**, một cảnh báo
Starlette test client/httpx deprecation. JUnit local:
`C:/Users/bao.nguyen/AppData/Local/Temp/query-prep-unit-20260905-final.xml`.
Ba live opt-in DB/Qdrant/eval đều 0; execution context test. Không có live
provider, DB hoặc RAG server được gọi bởi bộ fixture launcher đã khai báo.

Thay đổi PowerShell cuối trong lúc suite chạy chỉ nạp ScheduledTasks từ đường
dẫn Windows chuẩn; đã probe read-only module/cmdlets và AST. Nhánh register
governed task chưa được chạy trên packet thật. Full-suite xanh không xóa
dependency advisory hay thay thế independent review/fresh authorization.

## 12. Proof wrapper sau review

Opt-in `RUN_QUERY_TASK_PROOF=1`, test
`test_real_wrapper_registers_exact_synthetic_task` đã pass **1/1 trong 14.56
giây**, JUnit `C:/Users/bao.nguyen/AppData/Local/Temp/query-wrapper-registration-20260905.xml`.
Test gọi đúng production wrapper `-Register -Start` với authorization tổng
hợp trong temporary clean Git repo. Trước khi commit fixture, chỉ entrypoint
`run` của bản host tạm được thay bằng stub stdlib hữu hạn, ghi marker rồi
exit 0; nhánh validate giữ nguyên. Không gọi operator thật, không đọc/sửa
credential environment của user, không dùng authorization hoặc root pilot cũ.

Proof kiểm task XML/CIM: no triggers, Interactive/Limited, IgnoreNew,
RestartCount 0, giới hạn 26 giờ, đúng executable/arguments/working directory
và không chứa service token. Marker kiểm arguments và cwd mà task thực sự
nhận; LastTaskResult bằng 0. `finally` dọn đúng digest-derived task; read-only
listing sau test xác nhận không còn task `ChatBotProject-Query-Governed-*`.
Windows có thể bỏ RunLevel mặc định trong XML và chuẩn hóa 26 giờ thành
`P1DT2H`; test kiểm cả giá trị CIM thay vì coi cách serialize khác là lỗi.

Đây là proof transport của wrapper, không phải end-to-end production host
dưới Scheduler hoặc xác nhận token/provider của scheduled user. Host
`run_packet` và cleanup dùng validator thật, Windows Job thật và process giả
ở OS boundary trong test riêng. Test lớn đã tách prepare/validate, drift/root,
registration/handoff và run outcome; full suite chưa chạy lại sau thay đổi
test/binding này. Không suy ra live readiness từ hai lớp synthetic proof.

Suite host/job sau tách fixture/lifecycle pass **37/37 trong 49.46 giây**,
bao gồm opt-in registration; JUnit
`C:/Users/bao.nguyen/AppData/Local/Temp/query-host-post-review-20260905.xml`.
Lượt lifecycle proof mới phát hiện race đọc `started.json` trước khi writer
đóng file (`144bcfda40284a47b22e2b0d34f656c6`, failed, task đã dọn).
Script proof nay ghi file tạm độc quyền rồi publish bằng rename không ghi đè.
Fresh proof `31e331e30730471fb695ab6c57efd8c5` **passed**: launcher thoát,
payload vẫn sống và hoàn tất tests rồi exit 0; task đã được dọn. Receipt tại
`.local/query-scheduler-proof/31e331e30730471fb695ab6c57efd8c5/result.json`.
Nhánh proof có Python suite dùng giới hạn 180 giây/wait 150 giây vì fixture
authorization thật mất nhiều thời gian hơn; proof sleep-only vẫn 45/30 giây.
Nested task-registration opt-in bị đặt 0 trong payload proof, không tự tạo
thêm task từ ambient test environment. Đây chỉ là script kiểm chứng tổng hợp,
không nới timeout hoặc retry của pilot thật.

## 13. Cleanup delegation sau review

Ambient `RAG_QUERY_PILOT_HOST_CONTAINED=1` không còn đủ để hoãn cleanup.
Operator đối chiếu packet bytes/SHA, exact arguments, clean source commit,
host/job/task/interpreter hashes và input hashes với consumed marker. Host
identity bind PID, process creation time và exact command; OS probe yêu cầu
process con ở Windows Job. Lớp Python venv redirector chỉ được đi qua khi
executable và toàn bộ argument tail khớp; không duyệt ancestor tùy ý.
Requested packet không hợp lệ dừng CLI trước supervisor, không âm thầm chuyển
sang cleanup sớm. Không có requested packet thì standalone giữ cleanup cũ.

Regression ban đầu bắt host/job hash và source commit drift vẫn được cho qua,
cộng parent matching lỗi với venv redirector; sau sửa, suite CLI **25/25 pass
trong 36.82 giây**, branch+statement coverage **88%**. Một subprocess probe
Windows Job thật cũng pass: finite child nhận đúng host cha qua venv và đang
ở job, không chạy operator/provider. Nếu host creation time không đọc được,
host reject trước consume; test riêng RED/GREEN, **1/1 pass**.

Host/job/binding suite trước bổ sung malformed-input cases pass **44**, skip
**1** (task-registration opt-in). Full unit suite đang chạy từ trước các test
bổ sung cuối; phải đối chiếu JUnit và focused results, không gọi nó là bằng
chứng bao phủ những test chưa được collect. Independent review cho bản binding
mới bị quota trước kết quả; signoff/freeze vẫn chưa hoàn tất.

Full unit checkpoint binding: **3294 pass, 1 skip, 1 warning**, **449.86 giây**,
JUnit `C:/Users/bao.nguyen/AppData/Local/Temp/query-prep-unit-post-binding-20260905.xml`.
Lượt focused có opt-in chạy song song: **62 pass, 1 failure** trong 109.86 giây,
failure là `proof_handoff_timeout` tại task transport, không phải binding test.
Task đã cleanup; không nâng timeout để đạt green. Proof được bổ sung diagnostic
state/result/LastRunTime và đang kiểm riêng với fixture mới. Chưa kết luận
nguyên nhân hoặc coi toàn bộ xác minh cuối đã pass.

Diagnostic tiếp theo xác minh false timeout: task `Ready`, LastTaskResult `0`,
nhưng Scheduler LastRunTime `15:06:06+07` sớm hơn mốc driver
`15:06:12.4669348+07`. Chưa xác định nguyên nhân lệch đồng hồ giữa hai nguồn.
Proof không còn dùng wall-clock ordering làm điều kiện handoff; nó yêu cầu
marker chưa tồn tại trước register, do stub ghi độc quyền, task đã thoát,
exit 0 và nội dung marker bind exact packet/arguments/cwd. Timeout giữ 20 giây.
Fresh singleton proof sau sửa **1/1 pass trong 12.84 giây**. Đây là sửa test
observer, không đổi authorization timestamps, cadence hoặc clock guard pilot.

## 14. Bước matrix offline đã triển khai

`python -m scripts.integrated_eval.math_query_matrix prepare --source-root ROOT
--output OUTPUT` tạo draft metadata-only: canonical manifest hashes cho đúng
ba row 16/13/3 case, exact bảy flags, all-off baseline, concurrency 1 và zero
retry/replacement/catch-up. Input manifest đổi bytes bị reject trước output;
output dùng exclusive create, không overwrite. Source commit/dirty status
được ghi là observed identity, không giả định dirty source đã freeze.
Draft luôn `execution_ready=false`, `dispatch_authorized=false` và
`default_rollout_authorized=false`. Không có command `run`, không gọi preflight
SQL/Qdrant, evaluator hoặc provider; không load credential config.

Public CLI regression **6/6 pass trong 0.84 giây**, coverage branch+statement
**95%** cho module mới. Đây chỉ là bước chuẩn bị có thể thực thi, chưa phải
matrix runner. Các phần còn mở: fresh clean source/tool bindings, declared
snapshot/provider identity, transport zero-retry với stop toàn window,
trace/provenance/budget validation, security/load references và reviewed gates.
Không dùng `integrated_hardening.REQUEST_LIMITS.provider_retries=2` của
contract cũ thay cho retry 0 của matrix này. Full unit checkpoint 3294 phía
trên chưa bao gồm sáu test matrix mới.

Subprocess help inspection sau đó bắt lỗi import khi không có PYTHONPATH;
module entrypoint nay bootstrap đúng thư mục `src` của checkout. Regression
help không PYTHONPATH được thêm: **7/7 pass trong 0.93 giây**. Independent
read-only planning xác nhận draft-only là bước phù hợp; không gọi review này
là code signoff hoặc bằng chứng runner hoàn tất.

Draft validator đã triển khai qua CLI `math_query_matrix validate --source-root
ROOT --draft DRAFT`: so toàn bộ contract với draft dựng lại từ manifest/source
hiện tại, giữ so sánh type-strict và không ghi file. Reject extra feature,
Math OFF trong interaction, manifest/commit/tool drift, dispatch flag hoặc
retry bị sửa. Bộ **15/15 pass trong 2.65 giây**, coverage **93%**. Kết quả là
`validated_draft`, vẫn `execution_ready=false` và `dispatch_authorized=false`.
Chưa có input snapshot/provider được freeze nên validation này không thay
runtime identity hoặc fresh authorization gate.

Inspection evaluator xác nhận `scripts.decomposition_eval.preflight` đã biết
scope `math_query_interaction` và exact 3 case; không cần viết lại resolver.
Điểm cần runner mới là isolation/dispatch/evidence orchestration: runner Math
hiện ép Query OFF, runner Query ép Math OFF. Không dùng các runner đó cho
interaction bằng cách đổi nhãn manifest hoặc nới validator 13 Query case.

Independent read-only binding review không tìm thấy security bypass hoặc
cleanup race thành success. Có lưu ý availability nếu host được gọi bằng
argv tương đương nhưng khác spelling/extra interpreter flags/`-m`; contract
hiện chỉ hỗ trợ exact action do wrapper hash-bound dựng ra (script path,
`run --packet ... --packet-sha256 ...`). Các cách launch thủ công khác phải
reject, không được normalize tùy ý để bỏ qua parent identity. Actual fixed
Windows Job/venv command shape đã có subprocess probe pass; review đang
đối chiếu xem lưu ý này có xảy ra trong supported wrapper path hay chỉ là
giới hạn ngoài contract. Không dùng ghi chú này làm signoff toàn runner matrix.

Reviewer đã đối chiếu và rút finding argv: không có generated-command mismatch
trong supported wrapper path; alternate launch spelling là giới hạn có chủ ý.
Kết luận review binding: không còn finding security/correctness cho điểm này.
Review Standards/Spec delta toàn gói thử tiếp sau đó đều bị quota trước kết
quả, nên không ghi signoff giả hoặc coi local freeze đã xong.

Proof timeout 20 giây nay dùng `Diagnostics.Stopwatch`, không dùng wall-clock
ordering/deadline. Singleton registration proof sau thay đổi **1/1 pass trong
12.98 giây**. Authorization timestamps và cadence pilot không thay đổi.

Final focused checkpoint: **78/78 pass**, không skip, **83.88 giây**,
JUnit `C:/Users/bao.nguyen/AppData/Local/Temp/query-prep-final-focused-20260905.xml`.
Bao gồm host/job/cleanup-binding/matrix draft và opt-in task registration.
Stopwatch proof change được kiểm thêm singleton ở trên. Không còn task
governed thử nghiệm sau cleanup; `git diff --check` pass. Chưa commit vì
review delta còn thiếu; full goal vẫn còn runner matrix/evidence validation,
không được thay bằng kết quả draft-only.

## 15. Kế hoạch sáu arm cho runner

`build_arm_plan(source_root, run_root)` đã dựng được đúng thứ tự
Math baseline/candidate, Query baseline/candidate, Math+Query baseline/candidate.
Mỗi arm có exact flags và trace sibling `row/rag-traces/arm.jsonl`, evaluator
arguments `--maximum-provider-retries 0 --stop-on-provider-failure`, không
capture raw review content. Semantic cache tắt trên cả sáu arm. Collection
theo fixture hiện hữu: Math dùng GroundedMath fixture; hai row Query dùng CRAG
fixture, không suy ra hai fixture là cùng snapshot hoặc so trực tiếp giữa row.

Hàm không tạo run root hoặc chạy subprocess evaluator. Nó trả controlled
environment overlay, không sao chép credentials từ ambient environment;
dispatcher tương lai phải merge provider settings trước overlay này. Existing
root bị reject. Bộ matrix **17/17 pass trong 2.46 giây**, coverage **95%**.
Đây là command planning, chưa phải dispatcher hoặc smoke/quality evidence.
Các gate còn thiếu giữ nguyên: clean candidate, frozen identity/authorization,
preflight/rollback/smoke, stop toàn window sau arm lỗi và raw trace/evidence
validation trước acceptance. Plan luôn `execution_ready=false` và
`dispatch_authorized=false`; không có lệnh CLI run được thêm.

## 16. Ranh giới tái sử dụng dispatcher/gate đã kiểm

Không tìm thấy matrix-specific authorization entrypoint trong tracked
`scripts/ops` hoặc `scripts`; consolidated Query authorization hiện chỉ có
activation/pilot. Không tái sử dụng nó cho interaction matrix. Runbook
integrated yêu cầu hai capability pass gate độc lập trước matrix đã declare.

`evaluate_combination_evidence` có thể tái sử dụng cho kiểm trace/load/quality
của một row nhưng yêu cầu benchmark đủ concurrency **1 và 5**, không chỉ
evaluation concurrency 1. Inventory có chín reference bắt buộc:
baseline/candidate eval, trace, benchmark, load và results. Loader hiện hữu
còn kiểm raw-trace/security/bindings trước composer, nên không gọi composer
trực tiếp trên metadata tự khai báo rồi coi là acceptance. Retry budget
integrated cũ là 2; matrix mới phải thêm kiểm zero retry, không sửa gate cũ
thành một gate nhẹ hơn để khớp draft. Chưa triển khai dispatcher/acceptance
validator mới tại checkpoint này.

Host/CLI hiện dùng chung hàm canonical arguments SHA-256 thay vì hai bản
copy; mục tiêu là không để binding drift do sửa một bên. Không thay schema,
authorization hoặc giá trị hash được tính.

## 17. Delta offline ngày 07/09: budget theo arm và bộ đọc evidence dùng chung

`evaluate_arm_budgets` kiểm ba row Math-only, Query-only, Math+Query:
baseline không được tiêu budget feature nào; candidate chỉ dùng feature đúng
row. Interaction cho phép planner/subquery và calculation cùng hoạt động.
Provider retry phải là integer 0 (không nhận boolean/string/float/missing).
Shared evaluator giữ mặc định năm combination cũ và ceiling 2; contract mới
chỉ được siết ceiling, không nâng. Standards và Spec review budget delta đều
không có finding. Biến môi trường HOST_SCRIPT không được đọc đã bỏ; parent
identity vẫn lấy trusted path từ source root.

`load_row_evidence` tách từ loader matrix cũ, giữ kiểm hash/schema của chín
artifact, security JSONL binding, raw trace recomputation và derived load/results
recomputation. Loader năm combination cũ gọi lại public seam này. Regression
bao gồm thiếu/đổi bytes, sửa derived report rồi rehash, lệch raw trace và lệch
security binding. Fixture candidate sửa start time để nằm trong trace window;
security fixture dùng manifest chuẩn, với observed outcomes giả lập rõ ràng.
Đây là kiểm validator offline, không phải bằng chứng security của runtime.

Kiểm trực tiếp delta: **111 passed, 1 skipped trong 47.73 giây**, gồm host
run outcomes. Skip là OS task registration chưa opt-in ở lượt này. Coverage
riêng **88 passed trong 7.62 giây**: matrix module 96%, integrated budget
module 93%; compose module 65% do tập test không phủ mọi CLI/matrix path.
Không suy ra coverage toàn repo. JSON coverage ở
`C:/Users/bao.nguyen/AppData/Local/Temp/query-matrix-budget-path-20260907.json`.
Lượt coverage module-name trước đó lỗi native import lúc collection; lượt
path-based sau đó thành công, không đổi dependency hoặc bootstrap runtime.

Delta chưa commit. Independent Standards/Spec review riêng extraction bị
quota trước kết quả; parent đã kiểm diff và chạy regression, chưa thay thế
independent signoff. Chưa chạy full unit trên delta (3323/1 là của 96c78e3).

Còn phải nối budget contract mới xuyên qua row evidence/derived results,
kiểm exact manifest cases và frozen conditions của ba row, rồi hoàn thiện
dispatcher cùng fresh authorization/preflight/rollback/smoke gates. Không dùng
`load_row_evidence` mặc định để chấp nhận Math+Query: legacy budget contract
vẫn cố ý không biết combination mới. Không có provider traffic hay lệnh run
matrix mới từ delta này. Draft đã bind hash cũ cần tạo mới sau freeze, không
ghi đè draft lịch sử hoặc coi nó còn current sau thay đổi preparer.

### Nối contract xuyên row evidence (delta tiếp theo, chưa freeze)

Shared `load_row_evidence`/`evaluate_combination_evidence` nhận contract baseline
và candidate riêng cùng ceiling retry; trace reconciliation dùng giới hạn từ
report của contract đó. `build_results` và derived-results recomputation nhận
cùng candidate contract. Mặc định các caller cũ vẫn giữ nguyên năm combination
và ceiling 2; CLI cũ không nhận tùy chọn đổi contract từ artifact.

Regression **93/93 pass trong 6.27 giây** gồm fixture Math+Query có planner=1,
subquery=2, calculation=1 và raw events tương ứng. Baseline có planner hoặc
calculation, baseline/candidate có retry đều bị reject ngay cả khi tất cả hash
và derived artifacts đã được tái tạo. Legacy contract vẫn reject combination
Math+Query. Đây là nối plumbing nội bộ; matrix-specific entrypoint vẫn phải
chọn contract từ code, không từ metadata đầu vào, và còn phải khóa manifest
cases/identity/versions trước acceptance. Chưa có dispatcher, traffic, full
unit mới hay independent review của delta nối contract này.

### Cửa kiểm ba-row evidence (delta tiếp theo, chưa freeze)

`scripts/integrated_eval/math_query_evidence.py::load_math_query_evidence`
đã chọn exact flags và baseline/candidate budget từ code, không từ evidence.
Nó yêu cầu đúng ba row duy nhất, manifest hash khóa và exact 16/13/3 case IDs,
không trùng/thay case; điều kiện mỗi arm phải khớp expected conditions do
caller cung cấp độc lập. Kiểm commit, manifest, snapshot, provider/governance
hash, collection, evaluation context và concurrency 1; provider/commit dùng
chung giữa các row. Governance hash khóa riêng theo manifest (đã sửa lỗi
ép global trong regression tiếp theo dưới đây). Benchmark gate cũ vẫn đòi
cả concurrency 1/5.
Versions đủ canonical fields, flags có kiểu boolean chính xác (0 không được
đóng vai false), retry tổng và retry events đều zero. Chạy lại row loader
để đối soát trace/budget/security/derived reports như trên.

Regression matrix + shared gates **111/111 pass trong 13.87 giây**. Fixture
thành công có đủ 16/13/3 case và 30 references; fixture là dữ liệu test, không
phải provider evidence. Negative cases gồm inventory thiếu/trùng/thừa, đổi
frozen identity, thay case, kiểu count/flag sai và contract hash/version lỗi.
Kết quả luôn `dispatch_authorized=false`, `default_rollout_authorized=false`.

Caller vẫn phải xác thực nguồn của frozen contract; hàm này không xác minh
owner approval hoặc phát hành release decision. Chưa có dispatcher/CLI run,
review độc lập mới hoặc commit. Full unit delta đã được khởi chạy với JUnit
`C:/Users/bao.nguyen/AppData/Local/Temp/query-matrix-evidence-20260907-unit.xml`;
chưa ghi nhận kết quả cuối tại thời điểm cập nhật này.

### Đối soát request identity và failure telemetry

Đã thêm binding `case.id -> eval:<label>:<id> -> raw rag_end` bằng exact
multiset trong snapshot time window và evaluation context, đồng thời kiểm
lại raw SHA khi đọc. Regression thay trace_id hoặc lặp request để bù case
thiếu vẫn bị reject dù query_count, raw hash và rebuilt snapshot hợp lệ.
Provider failure phải có tổng integer zero và từng case khai báo false;
snapshot error events phải zero. Nếu artifact có `case_count`, số này phải
khớp manifest để không đổi mẫu số cost/retry trong load report.

RED đã xác nhận thiếu checks identity/provider failure và chấp nhận sai
case_count; GREEN tập matrix/shared gates **116/116 pass trong 18.62 giây**.
Full unit session khởi chạy trước các sửa cuối này vẫn đang chạy; vì source
đã đổi khi session còn sống, kết quả của nó chỉ là regression tham khảo,
không được dùng làm full-suite signoff cho delta cuối. Cần chạy full suite
lại trên snapshot đứng yên sau khi hoàn tất review/fix.

### Sửa binding governance theo manifest thực tế

Đối chiếu runner hiện hữu cho thấy `governance_scope_sha256` bao gồm từng
case ID và governance fields, không chỉ một ACL chung. Ba manifest cho ba
hash khác nhau; điều kiện ép governance hash bằng nhau giữa row là sai.
Regression dùng hash tính trực tiếp từ ba manifest đã RED vì global-conditions
rejection; nay GREEN khi hash được khóa riêng theo manifest, còn commit và
provider identity vẫn chung. Collection cũng khóa từ constants fixture Math
hoặc CRAG/Query, không chấp nhận collection tùy ý trong frozen input.

Tập matrix/shared regression hiện **118/118 pass trong 19.66 giây**.
Full-unit process cũ vẫn sống và đang tiến triển; không restart hoặc suy ra
stalled chỉ vì một số test chậm. Phần dispatcher chưa triển khai; cần tiếp tục
kiểm binding quality/provenance và declaration trước freeze/live path.

### Phân biệt integrity với quality acceptance

Đã đối chiếu `run_eval`: artifact giữ `answer_metadata` và kết quả chấm
calculation/decomposition, không giữ đủ observed calculation records, branch
debug và answer để chạy lại các evaluator độc lập. `evaluate_grounded_calculation`
cần expected contract đã resolve, actual records và answer; evaluator Query
cần branch debug cùng answer cho terminal-answer contract. Không thể suy ra
independent provenance/unsupported-number acceptance chỉ từ aggregate `passed`.

Kết quả matrix integrity nay ghi rõ `quality_acceptance_verified=false` và
`matrix_accepted=false`, bên cạnh hai authority flags false. Regression cho
ranh giới này đã RED rồi GREEN (1 passed). Đây không phải thay mục tiêu bằng
integrity: quality evidence contract và dispatcher vẫn là phần bắt buộc chưa
xong. Không bật raw capture hoặc giữ nội dung review của run thật để lấp chỗ
thiếu trong lượt offline này.

Coverage matrix-evidence trước thay đổi hai output flags: **25/25 pass trong
37.11 giây**, module mới **93% branch+statement**; coverage các module khác
không đại diện full suite. JSON tại
`C:/Users/bao.nguyen/AppData/Local/Temp/query-matrix-evidence-20260907-coverage.json`.
Full-unit session cũ vẫn sống, đã qua 74%, chưa có kết quả cuối.

### Full-unit kết thúc và recomputation quality trong bộ nhớ

Session full-unit đã kết thúc **3381 passed, 1 skipped, 1 warning trong
631.38 giây**. JUnit đã nêu phía trên, SHA-256
`5fdf49958f4ea8abedd76f57e7530903b519e23f36329868f1c1f6846cee82da`.
Đây là regression tham khảo của session có source thay đổi trong lúc chạy,
không phải signoff full delta hiện tại.

`math_query_quality.recompute_quality_case` nhận resolved case, reported
evaluation và observation trong bộ nhớ; đối chiếu case/trace/answer SHA và
char count rồi chạy lại evaluator calculation/decomposition hiện hữu. Không
ghi/đọc file, không trả answer/debug, không cấp quyền capture. Caller vẫn
phải bind resolved case với manifest/preflight và observation với request đã
chạy; helper chưa tự làm được binding này hoặc acceptance toàn matrix.

Regression phủ thay answer, đổi provenance/version, đổi rendered branch
citations, sai case/trace, malformed observation và truthful failure. Đã RED
trường hợp calculation ngoài expected contract bị bỏ qua do applicable=false;
nay giữ kết quả fail của calculation evaluator dù không applicable. Quality
pass khác với recomputed_matches: báo cáo trung thực về lỗi vẫn không đạt.
Tập focused trước fix cuối **128 passed trong 23.43 giây**; quality sau fix
**12/12 pass trong 0.91 giây, coverage module 100% branch+statement**.
Coverage JSON `C:/Users/bao.nguyen/AppData/Local/Temp/query-quality-20260907-coverage.json`.

Chưa nối observation vào evaluator/dispatcher hoặc bật raw capture. Matrix
integrity tiếp tục `quality_acceptance_verified=false`, `matrix_accepted=false`.
Independent review delta, dispatcher, binding nguồn observation và full suite
trên snapshot đứng yên vẫn còn phải làm; các tests trên không thay chúng.

### Observation hook tại evaluator (offline opt-in bằng Python API)

`run_evaluation(..., quality_observer=...)` nay gửi resolved case, reported
case và observation answer/debug của request vừa hoàn thành cho callback trong
bộ nhớ. Không có CLI flag mới, không đổi mặc định và không bật raw capture.
Callback nhận deep copies để không thể sửa case/report đang được dùng tiếp.
Lỗi callback được sanitize thành `quality_observer_failed` và thoát ngoài
request exception handler, trước case kế tiếp và trước eval artifact cuối.
Request không có observation cũng dừng với `quality_observation_unavailable`.

Regression qua typed fake executor đã RED vì thiếu argument rồi GREEN:
observer dùng chính quality recomputation helper; mutation report không ảnh
hưởng output, exception không dispatch case thứ hai, không tạo review-content.
Tập observer/quality/existing evaluator **77 passed trong 8.67 giây**.
Hook chưa được dispatcher gọi; callback return không phải acceptance tự động.
Matrix integrity vẫn giữ quality/acceptance false đến khi complete observation
coverage và frozen preflight/authorization binding được nối và review.

Observer regression mở rộng: invalid callback reject trước đọc manifest/mkdir;
request lỗi không có observation dừng trước case thứ hai (79/79 test nhóm
observer/quality/evaluator). Arm planning nay ép `RAG_EVAL_FIXTURE_BATCH`
theo constants Math hoặc CRAG và `RAG_EVAL_GOVERNANCE_SCOPE_SHA256` tính từ
manifest từng row; regression ambient contamination đã RED rồi GREEN.
Nhóm matrix/quality/observer/shared evidence mới nhất **135/135 pass trong
17.82 giây**. Chưa thêm provider hash hoặc versions từ ambient vào plan;
dispatcher phải lấy chúng từ verified frozen contract, vẫn chưa triển khai.

### Stable full-unit checkpoint và row identity

Full unit trên source giữ cố định đã kết thúc **3405 passed, 1 skipped,
1 warning trong 530.00 giây**. Bảy module implementation theo dõi có SHA
trước/sau trùng nhau. JUnit
`C:/Users/bao.nguyen/AppData/Local/Temp/query-matrix-stable-20260907-unit.xml`,
SHA-256 `7589db26167b55d399303a909a554dc1ea1d9587819f445d9df92079ff276e0f`.
Skip là OS task registration opt-in; warning Starlette/httpx đã có trước.

Sau khi session kết thúc mới thêm regression ambient wrong-row và sửa arm
plan ép `RAG_EVAL_COMBINATION_ID` đúng tên row. RED là missing key; bản sửa
không đổi schema/flags/authority. Full-suite trên đây là trước sửa một dòng
binding này, không suy ra whole-goal complete. Dispatcher và review delta
vẫn còn mở; chưa commit, push/merge hoặc provider traffic.

Review Standards của delta trước sửa symlink đã trả kết quả không có finding
chặn; review Spec đang đối chiếu. Parent phát hiện original run root symlink
bị mất sau resolve: test filesystem-boundary mock RED vì không reject rồi
GREEN sau kiểm is_symlink trên path gốc. Draft suite **19 pass, 1 skip trong
2.41 giây**. Real symlink test bị OS từ chối tạo link nên skip, không coi
mock là chứng minh hành vi native Windows. Sửa này vẫn chỉ ở arm planning;
dispatcher phải kiểm freshness/containment tại thời điểm tạo root thực tế.

### Kết luận review delta hiện tại

Standards đã kiểm observer/deep-copy/sanitized termination, shared rowloader,
budget defaults và evidence integrity: không có finding chặn. Spec đã kiểm
toàn delta offline, bao gồm original-symlink guard cuối: không có finding
cụ thể. Các ghi chú review pending/quota trước đây là lịch sử, không phải
trạng thái review hiện tại. Standards review trước sửa symlink; Spec đã xem
bản sửa. Cả hai đều read-only, không tự chạy lại suite.

Kết luận chỉ áp dụng nền tảng offline (arm plan, integrity, budget, observation
hook/recomputation). Không bao gồm dispatcher chưa viết, xác thực authority
của frozen contract, complete quality observation coverage, provider run hay
matrix acceptance. Giữ goal mở. Mốc full unit source cố định vẫn 3405/1
trước hai delta nhỏ row-ID và symlink; focused row-ID 47/47, draft sau symlink
19/1. Không suy diễn các mốc này thành full-suite signoff cho dispatcher tương lai.

### Managed evaluator observation entrypoint

`run_eval.main(..., quality_observer=...)` đã nối Python API callback vào
`run_evaluation` bên trong runtime/trace context hiện hữu. Invalid callback
bị reject trước parse args, đọc manifest hoặc load settings. Không thêm CLI
flag, raw capture, provider traffic hay authority. Runtime đóng trong finally
trên cả thành công và lỗi observer.

Regression RED: main chưa nhận keyword mới (3 failed, 2 passed); GREEN sau
forwarding và guard. Kiểm tra tích hợp chạy main và evaluator thật qua fake
runtime executor: callback nhận observation thực, recomputation khớp; lỗi
dừng trước case thứ hai, không có eval artifact cuối, không có review-content
và runtime luôn đóng. Nhóm evaluator/quality/matrix/shared rowloader:
**169 passed, 1 skipped trong 28.36 giây**. Skip là native symlink creation
không được OS cho phép, không phải provider test. `git diff --check` pass.

Standards review delta entrypoint không có finding mới, read-only không chạy
test. Spec reviewer bị quota, chưa có independent Spec signoff cho entrypoint
mới; review Spec nền tảng trước đó không bao gồm delta này. Chưa chạy lại full
unit cho delta entrypoint, chưa commit hoặc freeze. Dispatcher, verified frozen
authorization và complete resolved-case/observation coverage vẫn còn mở.

### Immutable observation ledger và binding báo cáo cuối

`QualityObservationLedger.create` chụp canonical resolved-case contracts trong
bộ nhớ. `record` trả instance mới, yêu cầu đúng case/order/type và recomputation
khớp; không thay đổi input hoặc ledger trước. Duplicate, extra, changed case,
answer/report mismatch đều reject. Summary tách observation coverage với chất
lượng: truthful quality failure vẫn có thể đủ observation nhưng không đạt.
`finalize` yêu cầu đủ inventory và SHA-256 từng final reported case khớp chính
xác, đúng thứ tự/count. Receipt chỉ giữ checks/hash, không giữ generated
answer/debug. Input resolved cases vẫn nằm trong bộ nhớ, không có file capture.

Đã RED ở API ledger và finalize còn thiếu, sau đó GREEN. Integration test nối
ledger vào callback của main/evaluator thật với fake runtime executor, rồi
finalize trên case rows trong eval artifact thật. Nhóm regression hiện
**180 passed, 1 skipped trong 29.19 giây**; quality **23 passed trong 0.84 giây**,
module `math_query_quality.py` 100% branch+statement. Coverage chỉ áp dụng
module này, không phải toàn thư mục integrated_eval. `git diff --check` pass.

Ledger là object Python công khai, không phải signed/trusted evidence hay
authorization. Worker còn phải xác thực nguồn frozen manifest/preflight,
kiểm preflight drift TRƯỚC tạo runtime/request, propagate mọi lỗi, bind run
identity và report metadata. Callback hiện chỉ kiểm case sau request, không
thay thế pre-dispatch guard. Matrix acceptance/dispatch flags vẫn false.
Standards reviewer delta ledger cũng bị quota; chưa có independent review
cho ledger, chưa full-unit/freeze/commit. Dispatcher vẫn chưa triển khai.

### Pre-request frozen-preflight validation seam

`run_eval.main(..., preflight_validator=...)` bổ sung Python-only validator:
chạy trước logging/runtime composition, rồi chạy lại trong cached preflight
sau khi evaluator reload manifest, trước request đầu tiên. Callback phải trả
đúng boolean True; False/None/1/exception đều fail-closed với lỗi đã sanitize.
Callback nhận deep copies, không thể thay nội dung cases hoặc cached preflight.
Không có validator thì đường mặc định và preflight failure behavior giữ nguyên.

`validate_frozen_preflight` đối chiếu canonical cases và toàn preflight report
với expected inputs độc lập; chỉ chấp nhận preflight passed đúng boolean True.
Fixture fingerprint, resolutions, type hoặc manifest drift đều làm mismatch.
Helper chưa xác thực chữ ký/nguồn expected inputs; dispatcher phải lấy chúng
từ verified frozen contract, không được lấy actual vừa đọc làm expected.

TDD đã RED vì thiếu main API/helper và vì false-like callback chưa bị chặn,
rồi GREEN. Nhóm regression **185 passed, 1 skipped trong 27.34 giây**. Sau đó
thêm hai integration cases đổi manifest tại fake runtime construction: real
main/evaluator reject ở reload, executor chưa nhận request nào, không có final
eval artifact và runtime được close. Observer suite mới **8 passed trong
7.55 giây**. `git diff --check` pass. Không có provider traffic hoặc CLI flag
mới. Independent review/full-unit của delta này vẫn chưa có; quota reviewers
trước đó chưa được xem là hết. Chưa commit/freeze/dispatch hay matrix acceptance.

### Per-arm quality worker

`scripts/integrated_eval/math_query_worker.evaluate_quality_arm` nối frozen
inputs -> preflight validator -> managed evaluator -> immutable observation
ledger -> final reported-case binding. Input JSON được chụp riêng trước run;
resolutions chỉ cho phép expected calculation/citations/branches/claims,
không cho đổi case ID, access scope hoặc thêm case lạ. Lệnh evaluator nội bộ
ép retry 0 và stop-on-provider-failure, không bật raw review capture. Worker
không có CLI, không sửa environment và không tạo/verify authorization.

Worker chỉ được parent dispatcher gọi sau khi xác thực quyền, source, run-root
mới và environment từng arm. Source authenticity của expected inputs chưa do
worker chứng minh. Cơ chế Ed25519 hiện có ký release decision ledger; không
được diễn giải lại thành quyền chạy matrix. Kết quả worker là metadata, các
matrix acceptance/dispatch authority flags vẫn false.

Integration RED vì worker chưa tồn tại, sau đó GREEN qua real main/evaluator
với fake runtime executor. Kiểm tra frozen resolutions được áp dụng trước
quality observation, manifest drift khi reload chặn trước request, runtime
đóng, không raw capture. Invalid/extra resolution và failed frozen preflight
bị reject trước đọc manifest hoặc tạo runtime. Nhóm evaluator/quality/worker
**109 passed trong 10.09 giây**; sau thêm resolved-case fixture, observer/worker
**14 passed trong 7.75 giây**. `git diff --check` pass. Chưa có independent
review/full-unit cho worker; dispatcher và authenticated frozen contract vẫn
chưa xong. Không traffic thật, commit, freeze hoặc activation trong bước này.

### Stable full-unit checkpoint sau per-arm worker

Full unit chạy trên source giữ nguyên đã kết thúc **3436 passed, 2 skipped,
1 warning trong 514.27 giây**. JUnit:
`C:/Users/bao.nguyen/AppData/Local/Temp/query-worker-stable-20260907-unit.xml`,
SHA-256 `5ce1543a1e7b67ee57ca86fd59390a85901b4928f9dc3c8cac7f3a1cbc0c7d26`.
Hai skip được đọc từ JUnit: Windows không cho tạo symlink và temporary
Scheduled Task registration cần explicit opt-in. Warning Starlette/httpx
deprecation đã có trước; không sửa dependency hoặc chạy OS proof trong suite.

Ba implementation hashes theo dõi trùng trước/sau:

- `scripts/eval/run_eval.py`: `c849dc203190352fda14909c32be6554e53e0b67872108b923c28831c0771947`.
- `scripts/integrated_eval/math_query_quality.py`: `4db5194fe94955a81397ea35861f6378b847de8a9779ae48555dbe0e5c89f904`.
- `scripts/integrated_eval/math_query_worker.py`: `190066a73a6569b4741045067224eab6479f812ed8db0ea46e8f4559fdfe976b`.

Đối chiếu read-only trong lúc suite chạy: `validate_authorized_source` có
exact clean commit check, `validate_provider_smoke_for_arms` có per-arm smoke
checks để tái sử dụng. Query pilot authorization khóa capability Query-only,
approval/draft/schedule/bundle và run-root; không được dùng nguyên cho matrix.
Release signature ký decision ledger, không chứng minh matrix traffic approval.
Dispatcher vẫn phải triển khai contract riêng và kiểm source/tool/identity,
fresh root, owner authorization và guards trước từng arm.

Kết quả full unit chứng minh regression snapshot hiện tại, không phải completion
goal, independent review, release freeze hay permission cho provider traffic.
Review delta ledger/preflight/worker vẫn mở do quota; chưa commit/push/merge.

### Dispatcher source guard

`math_query_dispatch.validate_matrix_source` kiểm exact SHA-1 commit, Git
toplevel đúng source root, worktree sạch (kể cả untracked) và exact inventory
SHA-256 của 10 tool files điều khiển matrix/evaluator. Kiểm source/tool paths
trước resolve/read, reject symlink hoặc Windows reparse points ở mọi ancestor.
Không có dispatch entrypoint hoặc authorization mới trong module này.

TDD dùng Git repo tạm chứa synthetic tool files: RED thiếu module, RED nested
root chưa bị chặn, rồi GREEN. Repo hỗ trợ Python 3.11 (README và Dockerfile),
nên không dùng `Path.is_junction()` của Python 3.12; regression xóa API này
đã RED rồi GREEN sau chuyển sang `lstat`/reparse attributes. Mock filesystem
boundary kiểm source/tool reparse rejection, không thay native Windows proof.
Temporary commits tắt signing và hooks, không commit source worktree thật.

Nhóm source guard/matrix draft/worker **37 passed, 1 skipped trong 10.26 giây**;
skip native symlink creation như trước. `git diff --check` pass. Full unit
3436/2 phía trên là trước source guard, không dùng làm full delta signoff.
Source validation chưa chứng minh executing module/interpreter identity,
owner authorization, input/runtime bindings hoặc fresh root consumption;
dispatcher phải nối các gate này trước worker. Review delta vẫn pending quota,
không gọi lặp trước mốc reset đã được báo. Chưa freeze/commit/live traffic.

### Read-only fresh run-root guard

`validate_fresh_matrix_root` chỉ trả path mới dưới source `.local`, không tạo
thư mục. Reject traversal, empty/noncanonical segments, alternate data stream,
Windows reserved names, trailing dot, existing root và reparse ở target hoặc
ancestor trước resolve. Regression RED thiếu API rồi GREEN; mock lstat kiểm
reparse rejection, không tuyên bố native junction proof. Đây chưa phải atomic
claim hay quyền tạo root: dispatcher vẫn phải xác thực authorization, tạo
exclusive và xử lý filesystem races trước khi gọi worker. Không đụng root cũ.

### Approval envelope binding (chưa phải dispatcher authorization)

`validate_matrix_approval_binding` nhận approval bytes và SHA-256/owner từ
kênh phê duyệt độc lập. Reject hash mismatch, owner/scope mismatch, draft byte
drift, duplicate JSON keys, nonfinite JSON, naive clock, future/expired window,
thời hạn hơn 60 phút và mọi quyền retry/replacement/catch-up/default rollout.
Envelope dùng schema matrix riêng, không tái sử dụng Query-only approval.
Không tạo file approval, không ký hoặc chứng nhận owner từ nội dung tự khai.

Return giữ `declaration_validated=false`, `dispatch_authorized=false`: binding
không đủ để chứng minh schema/nội dung declaration đúng, source/tool identity,
runtime/preflight/smoke/rollback hoặc single-use claim. Caller tuyệt đối không
tự hash approval file rồi coi đó là expected trust anchor. Deadline 60 phút
là giới hạn prospective của envelope offline, chưa có cửa sổ thật được cấp.

Regression RED missing API rồi GREEN. Session trước ngắt đã kết thúc hợp lệ
**69 passed, 1 skipped trong 10.23 giây**; đã lấy kết quả cùng session, không
restart. Thêm nonfinite draft regression RED vì JSON NaN được nhận, sửa parser
GREEN; approval/source group **37 passed trong 0.98 giây**. Chưa review delta,
full unit sau thay đổi, freeze hay provider traffic. Nội dung declaration và
dispatch orchestration vẫn còn phải triển khai và kiểm chứng.

### Code-owned traffic contract trong approval binding

Declaration `traffic` nay được đối chiếu type-strict với ROWS code-owned:
Math 16, Query 13, interaction 3; exact manifest path/hash, all-off baseline,
candidate đúng feature set; baseline rồi candidate, concurrency 1, retry/
replacement/catch-up 0. Không nhận extra row/field hoặc boolean thay integer.
Approval binding gọi guard này, nên một approval rehashed cho traffic sai
không thể vượt qua chỉ nhờ hash khớp. TDD RED missing validator và RED approval
chưa reject concurrency 5, sau đó GREEN: **50 passed trong 3.04 giây**.
`git diff --check` pass. Chưa xác thực toàn declaration (runtime identity,
frozen preflight/reference/rollback/smoke), chưa dispatch hoặc tạo root;
`declaration_validated` và `dispatch_authorized` tiếp tục false. Chưa review
hoặc full-unit cho delta này, không freeze/commit/provider traffic.

### Same-executor ON/OFF characterization

Public pipeline test nay chạy ON rồi OFF trên cùng executor/retrieval adapter,
trace riêng. ON có 2 decomposition subqueries; request OFF tiếp theo vẫn tới
generation, không còn decomposition route/branches. Toàn file public pipeline
characterization **9 passed trong 10.70 giây**. Chỉ đổi mutable fake adapter,
không đổi environment hoặc runtime thật. Đây kiểm request-state carryover,
không chứng minh deployment reload. Actual BOM calculation/provenance hợp lệ
vẫn thiếu, nên chưa đăng ký combined rollback profile hoặc mở interaction gate.

### Declaration consistency validator

`validate_matrix_declaration` nối exact schema, traffic contract, clean source
commit/tool hashes, fresh run-root, actual manifest hashes và row conditions.
Shared `validate_matrix_conditions` được dùng chung với evidence loader, không
nhân đôi điều kiện. Frozen preflight từng row phải đúng schema/batch/collection,
passed boolean True, no failures, exact count, fingerprint khớp conditions;
resolutions chỉ được tác động expected fields và case IDs đã khai báo.

Test dùng Git repo tạm chứa bản sao tool/manifest, không import/execute source
tạm, không provider/runtime access. RED missing validator rồi GREEN. Nhóm
declaration/approval/source **57 passed trong 7.75 giây**; evidence/declaration/
worker **58 passed trong 24.36 giây**; `git diff --check` pass.

`declaration_validated=true` chỉ là consistency check của nội dung frozen:
không chứng minh preflight live hiện tại, rollback/smoke, owner trust anchor,
executing interpreter/module identity hoặc exclusive root claim. Dispatch vẫn
false. Các bước này vẫn phải nối trong orchestrator trước worker; không có
CLI dispatch, review/full-unit/freeze hoặc traffic thật từ delta này.

### Unified launch-input validation

`validate_matrix_launch_inputs` nhận immutable bytes cho draft/approval,
kiểm trust-anchor/owner/time/traffic binding trước rồi parse chính draft bytes
đó để kiểm declaration/source/preflight consistency. Không nhận mutable
bytearray; không ghép kết quả từ hai declaration khác nhau. Output gồm hash
đã bind và run-root đã kiểm, nhưng `dispatch_authorized=false` vì live guards,
executing identity và exclusive root claim chưa nối. Không file/runtime writes.

RED thiếu unified API rồi GREEN. Nhóm declaration/approval/source **59 passed
trong 9.28 giây**; thêm mutable-input regression, focused launch-input **3 passed
trong 2.02 giây**. Test đổi run-root sau approval reject trước tạo `.local`.
`git diff --check` pass. Chưa full-unit/review cho delta này; goal còn mở.

### Per-arm smoke evidence guard

`validate_matrix_arm_smoke` kiểm exact bytes/hash và provider SHA, sau đó dùng
`provider_smoke_artifact_valid` và `provider_smoke_fresh_for_arms` hiện hữu:
5/5 successful, zero retry, not blocked, completed trước arm start và tối đa
30 phút. Matrix boundary bổ sung strict integer counts và strict JSON parser,
không nhận bool/string thay số. Không probe, không đọc credentials, không nới
freshness hoặc thay đổi validator mặc định của workflow khác.

RED missing API rồi GREEN; smoke mới và existing CLI tests **19 passed trong
6.67 giây**, `git diff --check` pass. Fixture chỉ synthetic bytes. Hash smoke
còn phải được bind vào declaration/arm packet; guard chưa được orchestration
gọi trước mỗi arm. Rollback, executing identity, exclusive root consumption,
review và full-unit cuối vẫn còn mở. Không live smoke hoặc dispatch.

### Declared smoke inventory

Declaration bắt buộc `smoke_sha256s` cho đúng ba row và hai arm mỗi row;
reject missing/extra arm/row hoặc digest không phải lowercase SHA-256.
Vì nằm trong draft bytes được approval bind, dispatcher không được thay hash
smoke ngoài declaration đã duyệt. Cùng artifact có thể phục vụ nhiều arm chỉ
khi per-arm freshness check đều đạt; hash hợp lệ không chứng minh smoke pass.
TDD RED valid declaration chưa nhận field mới rồi GREEN. Chưa thêm quyền
probe, refresh/retry hay live dispatch; vẫn phải gọi guard ở mỗi arm start.

### Rollback evidence boundary

`validate_matrix_rollback` bind exact bytes/hash và source commit rồi tái dùng
`validate_rollback_evidence`: exact test profile/command, flags, disabled state,
successful exit và evidence fields. Matrix wrapper reject bool exit code hoặc
integer thay false. Không chạy command từ artifact. RED missing API rồi GREEN:
rollback/smoke **14 passed trong 0.46 giây**.

Shared profiles chưa có tổ hợp Math+Query. Guard giữ interaction rejected,
không coi proof Math-only là combined rollback và không tự ghép hai proof riêng.
Cần triển khai/test profile rollback kết hợp tại execution seam rồi bind hashes
vào declaration trước khi hoàn thiện dispatcher. Chưa có rollback thật, live
traffic, review/full-unit hoặc freeze cho delta này.

### Public pipeline rollback characterization in progress

Đã thêm test public DefaultRagExecutor qua fake stores/model và no-network audit
boundary. OFF đi tới retrieval/generation hoàn tất; đối chứng ON có trace
query_decomposition với 2 subqueries. Test ban đầu lộ fixture thiếu generation
client/settings và policy boundary; chỉ bổ sung fake adapter/audit, không sửa
runtime sản phẩm. Diagnostics không expose decomposition_intents nên assertion
dùng structured pilot_request_evidence trace hiện hữu. Focused **2 passed trong
10.70 giây**.

Đây chưa phải rollback combined proof: hai test case độc lập chưa thể hiện
ON->OFF cùng lifecycle, fixture chưa exercise actual BOM calculation và ON
trace quality/security vẫn invalid do synthetic citations. Không đăng ký
ROLLBACK_TEST_PROFILES cho Math+Query hoặc hạ gate từ characterization này.
Cần fixture có nguồn BOM/provenance hợp lệ và chuyển trạng thái thực trước khi
công nhận profile. Không provider traffic hoặc thay đổi cờ runtime thật.

### Combined request transition characterization refreshed

Test hiện tại đã thay hai case độc lập bằng ON rồi OFF trên cùng executor.
Fixture BOM có 2 + 3 = 5 cái, source/version provenance và HR/HCM ACL đúng
request. ON có 2 subqueries, calculation valid, provenance/security passed và
no leakage; OFF tới generation nhưng không có query_decomposition hoặc
grounded_math_generation trace, không calculation route hoặc kết quả 5 cái.
Nguyên nhân security failure trước là thiếu phong_ban_quyen/site trong fixture,
không phải lý do để bypass RBAC. Chỉ sửa fake metadata, không đổi policy.

Toàn public pipeline characterization **9 passed trong 10.44 giây**. Đây là
request-runtime transition với injected adapters/no-network audit, không phải
deployment reload hoặc live rollback proof. Combined profile chưa đăng ký;
independent review và command-bound proof tại clean commit vẫn còn mở.

### Combined rollback profile registration

Registered the combined ON-to-OFF characterization as an additional
`MATH_QUERY_ROLLBACK_PROFILE`, accepted by the shared evidence validator.
Kept the existing `ROLLBACK_TEST_PROFILES` inventory unchanged so the
failure-family aggregate gate does not acquire a new mandatory profile.
Matrix validation still rejects Math-only proof for the combined row.
The missing-profile test was RED before implementation; rollback, aggregate
gate and public pipeline characterization tests then passed 24/24 (11.14s).
These are offline tests, not a clean-commit rollback artifact or live proof.
Independent review, declared rollback hashes and dispatcher wiring remain open.

### Declared rollback inventory

Declaration now requires `rollback_sha256s` with exactly math_only, query_only
and math_query, each a lowercase SHA-256 digest. These values are inside the
exact draft bytes bound by approval; changing the combined rollback digest
after approval is rejected before filesystem writes. This is hash binding,
not a digital signature or proof that a rollback command ran successfully.
RED: the valid declaration with the new inventory was rejected before the
validator update. GREEN: declaration/approval/rollback 55 passed (12.54s),
then launch-input tests including rollback hash substitution 4 passed (2.37s).
Existing aggregate rollback and matrix rollback tests also passed 15/15 (0.51s).
No live proof, root claim, dispatch, provider traffic or release freeze was
performed. Actual proof validation must still be called by the orchestrator.

### Launch proof validation connected

`validate_matrix_launch_inputs` now requires the three rollback byte artifacts
and exactly baseline/candidate smoke bytes for each row. After approval and
declaration validation it calls the existing rollback and smoke validators
using only the hashes, commit and provider identity in the approved draft.
The result reports `proofs_validated=true`, still `dispatch_authorized=false`.
Smoke is checked at launch time; it must be checked again at every actual arm
start, since a long-running matrix can outlive the smoke freshness limit.

RED: the public launch seam did not accept proof inputs. GREEN: declaration,
smoke and rollback tests 46 passed (14.82s). Additional launch checks then
passed 10/10 (7.27s), including approved-but-failed rollback, approved-but-stale
smoke, substituted bytes, missing row and extra arm. All use synthetic bytes
and temporary repositories; invalid proofs leave the run-root uncreated.
Exclusive root claim, executing identity, actual worker orchestration and
final independent review/full-suite/freeze remain outstanding.

### Root claim contention checks

Revalidated the existing exclusive-mkdir claim implementation without replacing
it. Eight concurrent thread callers produce exactly one successful claim and
seven rejected claims. A deterministic filesystem-boundary race creates a
competing owner's directory after freshness validation; the claimant rejects
it and preserves the competing owner's marker. Source/root tests pass 22/22
(7.12s), and diff whitespace validation passes.
The initial concurrency test overconstrained the loser error to not_fresh;
Windows path resolution during creation can instead fail closed as invalid.
The test now accepts those two rejection codes while retaining the exact
one-winner assertion. This does not prove resistance to adversarial parent
directory replacement, Windows ACL isolation, or cross-process orchestration.
The combined-profile reviewer terminated on quota, with no review conclusion;
independent review remains pending. No runtime or provider actions occurred.

### Loaded entry-point identity guard

Added `validate_matrix_process_identity`: the current Python executable must
resolve to the expected interpreter, and loaded dispatcher, worker and the
worker's evaluator module must resolve to their exact paths under the declared
checkout without redirected module paths. This complements, not replaces,
clean-commit/tool-hash validation. It does not attest loaded bytecode, every
dependency, interpreter binary hashes, or resistance to a hostile same-user
process. The eventual orchestrator must invoke it before claiming a root.
RED missing API then GREEN: source/root/identity tests 23 passed (4.42s),
including rejection of a different checkout and nonexistent Python executable.
No subprocess worker, provider traffic or activation was started.

### Prepare-run guard ordering

`prepare_matrix_run` now connects launch-input/proof validation, loaded process
identity validation, then exclusive root claim. It returns claim metadata but
keeps dispatch authorization false and does not invoke a worker. A public-seam
regression supplies valid approved artifacts for a clean temporary source but
executes from the actual preparation checkout: identity fails and no `.local`
directory is created. RED missing API then GREEN; declaration/source tests
59 passed (19.32s), diff check passed.
The positive combined prepare path still needs an isolated subprocess running
from its own clean source; individual validator/claim tests are not sufficient
proof of that path. Durable consumption receipt, worker orchestration and
per-arm revalidation also remain pending. No real run root was claimed.

### Isolated positive prepare and consumption receipt

An isolated Python subprocess now tests the positive prepare path from a clean
temporary Git repository containing current Python source and frozen manifests.
It uses synthetic approvals/rollback/smoke bytes, claims one temporary root,
and rejects a second prepare attempt. It never invokes the evaluation worker.
The test first passed the positive claim path (15.66s), then failed because the
durable consumption receipt was absent. Prepare now exclusively writes and
fsyncs `consumed.json`, binding source commit and draft/approval hashes while
keeping dispatch authorization false. Write failure leaves the directory
consumed; no cleanup/retry is performed. Source/declaration tests then passed
60/60 (35.42s). This is test evidence, not a formal rollback or release artifact.
Filesystem race hardening against hostile directory replacement and receipt
write-failure injection remain unverified; worker execution and per-arm guards
are still outstanding, as is independent review of this delta.

### Receipt durability failure regression

The isolated prepare subprocess test now also injects an OSError at os.fsync,
after the receipt write/flush. Prepare propagates failure rather than reporting
success, leaves the claimed root consumed, and rejects a subsequent attempt
after the injected fault is removed. Both normal and failed-fsync cases passed
(2 tests, 19.96s). The readable receipt after flush is not evidence that fsync
succeeded or that power-loss durability was established. No worker is invoked.
This verifies the existing fail-closed behavior without changing product code;
other write failures and adversarial parent replacement remain unverified.

### Prepared arm input revalidation

Added `validate_matrix_arm_start`, which rechecks approval time/binding, clean
source/tool hashes, loaded entry points, contained nonredirected root/receipt,
receipt commit/draft/approval identity, and the selected row's rollback and
arm's smoke. It does not authorize dispatch or prove sequencing. Shared path
containment is reused by fresh-root validation and prepared-root validation.
An isolated subprocess first failed for the missing API, then passed prepare
plus arm validation and rejected smoke at minute 31 after a valid minute-30
prepare. Both subprocess cases passed (22.76s), including the prior fsync fault.
The orchestrator must still stop on prepare failure and enforce arm order and
exactly-once execution: a consumed receipt alone is not successful preparation
or worker authorization. No evaluation worker or provider request was run.

### Six-arm coordinator connected through explicit transport

`execute_matrix_arms` validates inputs, builds the six-arm plan before claiming
the root, prepares it, rechecks each arm, loads its frozen manifest and invokes
an explicitly supplied worker transport sequentially. Worker exceptions,
nonzero/invalid exit codes or incomplete observation binding stop the loop.
An isolated subprocess verified the exact six-arm order and an exception in
arm two stopping before arm three. Together with fsync regression, 2 subprocess
tests passed (21.85s). Worker calls in this test are synthetic, not evaluations.

The coordinator has NO default process transport yet. Environment isolation,
actual evaluate_quality_arm invocation and persisted terminal/result artifacts
still need implementation and verification. Returned execution coverage is not
matrix acceptance. Provider identity/environment attestation and final evidence
acceptance remain required before real execution. No provider traffic occurred.
Separately, observer/worker regressions passed 15/15 (8.03s), including a timeout
on case one leaving no eval report and dispatching no second case.

### Coordinator terminal persistence

The six-arm loop now writes an exclusive, flushed/fsynced metadata-only
terminal.json on normal completion or loop failure, including draft/commit,
last row/arm and completed-arm count. Worker/guard exceptions stop the loop;
raw exception details are not persisted. Synthetic subprocess tests first
failed on the missing terminal, then passed both normal completion (6 arms)
and worker failure (1 completed arm, no third dispatch), 2 tests in 21.43s.
Terminal completed denotes orchestration completion only, never matrix quality
acceptance. Prepare failures remain represented by consumed-root state rather
than this loop terminal. Terminal-write failure itself propagates and retains
the consumed root. Process transport, result artifact persistence and final
evidence verification/review remain outstanding. No provider traffic occurred.

### Explicit worker process transport

Added `run_quality_arm_process`: fixed isolated Python bootstrap, explicit env
mapping (no automatic ambient env merge), JSON stdin frozen inputs, bounded
subprocess timeout, hidden Windows process, and schema-checked JSON output.
It invokes evaluate_quality_arm in the child; raw child output/errors are not
included in raised failures. RED missing API then process/observer tests passed
16/16 (9.45s). The new real-child negative test supplies failed preflight and
observes sanitized rejection/no output directory. It does not independently
prove the child reached the preflight boundary rather than failing earlier.
Successful fake-provider child evaluation and binding this transport to the
coordinator's provider/environment contract remain unverified and required.
No actual evaluation/provider traffic was authorized or attempted.

### Process transport success and timeout checks

Real child processes against a synthetic temporary worker now verify exact
JSON input transfer, explicit environment visibility, absence of a synthetic
ambient secret, and separation of printed worker logs from the JSON response.
A sleeping synthetic worker is terminated by the subprocess timeout and the
caller receives only the sanitized process failure. Process/observer tests
passed 18/18 (10.01s). These test the transport boundary, not a successful
full RAG evaluation in a child; the latter remains outstanding along with
provider/environment binding and coordinator integration.

### Planned-arm process adapter

Added run_planned_quality_arm to map a coordinator-owned arm's manifest,
output, label and controlled environment overlay into run_quality_arm_process.
Provider settings are parsed from explicit mappings and checked with the
existing governance fingerprint; the supplied command is parsed as plan data,
not executed. Label mismatch is rejected. A provider-drift regression was RED
before the adapter existed, then environment/process tests passed 6/6 (8.62s).
The process transport also rejects discoverable .env paths without reading
their contents, preventing implicit dotenv additions to its explicit settings.
The adapter still needs a fully bound coordinator entry point and positive
end-to-end verification; these checks are not authorization for provider use.

### Process-backed coordinator entry point

execute_matrix_processes now binds the explicit environment against the
approved provider fingerprint for all six planned arms before claiming a root,
then supplies run_planned_quality_arm as the sequential coordinator transport.
The existing approval/source/proof/identity/arm guards remain in the path.
A missing-provider test failed before this entry point existed, then passed
with no root created. Environment/process/declaration regressions passed 45/45
(50.98s). This does not yet prove successful full process-backed evaluation:
only its negative boundary and the prior synthetic transport/coordinator paths
have been exercised. End-to-end fake-provider verification and independent
review/full-unit/final artifact acceptance remain outstanding. No live traffic.

### Provider fingerprint report binding

The environment builder now overwrites RAG_EVAL_PROVIDER_CONFIGURATION_SHA256
with the verified provider digest. Previously a supplied stale value could
flow into run_eval's report despite actual settings matching the approved hash.
Regression was RED with an untrusted supplied digest; the fix uses the verified
digest without modifying the caller's mapping. A broader matrix suite completed
234 passed/1 skipped (68.90s), but overlapped this edit and is not a frozen
post-change signoff. Focused environment/process verification was rerun after
the fix. Final stable full-suite and independent review remain required.

### Process-backed coordinator integration verified with synthetic leaf

The isolated clean-repository test now retains the actual coordinator, adapter,
and process transport, while replacing only evaluate_quality_arm in that
temporary repository with a synthetic leaf. Its exact modified tool hash and
commit are bound to the synthetic declaration. Six real child processes
successfully verify per-row/arm flags, collection, evaluation context and
provider-fingerprint presence. The process-backed entry point completes all
six and retains matrix_accepted=false (1 integration test, 26.44s).
This proves transport wiring and settings propagation, not actual RAG quality
or observed-answer evidence. Real worker successful child execution and final
evidence persistence/acceptance still need validation. No provider was called.

### Per-arm receipt and result persistence

Successful arms now persist the worker's metadata result before its receipt,
both exclusive writes with flush/fsync. The receipt binds row/arm, draft,
commit and the exact stored result digest/name. Completed-arm count advances
only after both writes succeed. Initial receipt integration passed 2/2
(34.83s); the subsequent stored-result regression was RED before result
persistence, then both subprocess cases passed (58.42s). Tests recompute the
stored result hash and verify six receipts for success versus one when the
second worker fails. Persisted results are still execution/observation metadata,
not independent evidence acceptance. Read-only receipt reconciliation and
final report/trace matrix integration remain pending. Independent review was
redispatched after the earlier quota reset; no conclusion has been received.

### Read-only execution reconciliation

reconcile_matrix_execution checks the completed six-arm terminal against
independently supplied draft/commit identity, exact receipt/result inventory,
each receipt's row/arm and result hash, successful exit and observation binding.
Redirected artifact paths and unexpected files are rejected. It returns only
execution_complete, never matrix acceptance. The isolated process-backed test
was RED for the missing reconciler, then passed full reconciliation and rejected
a one-byte result change (28.74s). This is integrity/coverage evidence, not
independent proof of quality, provider authorization or actual RAG execution.
Quality/trace row evidence integration and review/full-suite remain pending.

### Worker report-byte binding

The worker now reads eval.json once as bytes, validates stored quality metadata
against that report and the independently frozen resolved cases, and returns
eval_sha256 with its result. This makes the persisted result/receipt chain able
to identify the exact report bytes rather than only its case projection.
The regression was RED for missing eval_sha256; observer/quality tests then
passed 40/40 (8.32s). Post-run reconciliation still needs to compare the stored
digest with the actual report and feed verified observations into the row
evidence result. No quality or release acceptance is claimed by this change.

### Quality aggregate and worker authority regressions

Stored quality now rejects a summary inconsistent with per-case boolean quality
outcomes. RED contradictory case/aggregate then observer/quality 40 passed
(8.07s). The broader matrix run found another regression: worker results could
assert matrix_accepted=true while retaining a matching receipt hash. That run
finished 237 passed/1 failed/1 skipped (84.64s), not green. Reconciliation now
rejects matrix/dispatch/default-rollout claims in worker results or nested
quality; both subprocess regressions passed after the fix (38.24s). It also
checks actual eval.json bytes against the stored worker eval_sha256, rejecting
report edits independently of worker-result edits. Full stable rerun remains
pending. Independent reviewer again hit quota and delivered no conclusion.

### Matrix regression rerun, September 8

After the aggregate/authority fixes, the broad matrix, observer, row loader and
failure-family rollback group passed 238 tests with 1 skip (83.64s). No code
edits were made during this run. JUnit:
`C:/Users/bao.nguyen/AppData/Local/Temp/query-matrix-20260908-regression.xml`.
This supersedes the earlier 237/1-failure run for that test selection only.
It is not full-unit coverage, independent review, a clean commit freeze, or
formal evidence. Successful actual-worker child integration and final quality/
trace evidence wiring remain open; runtime/provider state was not changed.

### Actual worker/evaluator in isolated Python

A new subprocess regression runs the existing successful worker boundary test
under isolated Python with an explicit environment and external tests disabled.
Unlike the six-process synthetic-leaf test, this uses the real
evaluate_quality_arm, run_eval.main, observation ledger and report-byte hash.
The fixture injects a fake runtime executor/preflight/settings; it is not a live
DefaultRagExecutor/provider/DB test and does not traverse run_quality_arm_process.
The child passed (12.25s), then process/observer/quality tests passed 45/45
(19.16s). This confirms the actual worker/evaluator lifecycle independently of
the process transport test; combined production bootstrap plus fake external
dependencies and final quality/trace acceptance still need verification.

### Six-arm stored quality binding integration

The execution reconciler optionally requires independently supplied resolved
cases for all three rows, and invokes stored-quality binding on every arm's
actual stored eval report and worker quality summary. Without those inputs it
explicitly reports quality_binding_verified=false; an empty mapping is rejected.
The six-process synthetic leaf now uses the real observation ledger and real
calculation/decomposition evaluators on empty synthetic answers, rather than
returning hardcoded observation booleans. Reconciliation with manifest-loaded
independent cases passes quality binding for all six arms while keeping matrix
acceptance false (integration test passed, 27.43s). Truthful quality failure is
not confused with mismatched observations. This remains synthetic-answer
evidence, not a real RAG quality result or approval to activate any feature.

### Row evidence linked to execution reports

load_math_query_evidence optionally takes execution root, independent draft
digest and resolved cases together. It first reconciles receipts and quality
bindings, then requires each baseline/candidate eval reference to point to
the corresponding report under that execution root. Missing execution receipts
are rejected rather than falling back to self-reported row evidence.
Observed candidate quality outcomes are reported separately as
observed_candidate_quality_passed. quality_acceptance_verified and
matrix_accepted remain false: automated observations are not independent owner
review. Evidence/quality/declaration tests passed 93/93 (67.54s), including
existing integrity-only callers. Positive combined trace-and-execution fixture
coverage, full-unit validation and independent review remain outstanding.

### Combined trace/execution fixture

Added a synthetic stored-metadata integration fixture over the existing
three-row trace/evaluation fixture. All six reports are linked to matching
execution result/receipt hashes and independently supplied case projections.
The combined loader reports evidence passed and quality binding verified, but
keeps quality acceptance and matrix acceptance false. Referencing an identical
report copied to another path is rejected as execution-report mismatch.
Focused integration passed (1.22s); evidence/quality tests passed 52/52
(12.13s), diff check passed. The manually constructed stored metadata validates
integrity wiring only, not authenticity or actual provider-answer correctness.
Independent review and stable full-unit validation remain required.

### Historical checkpoint: exclusive matrix run-root claim

Added `claim_matrix_run_root` as the first write-capable dispatcher guard. It
reuses the source-relative `.local/...` path validator, creates `.local` and
the final run-root directory with an exclusive `mkdir`, rejects existing roots,
and rechecks reparse/symlink markers around the created root. The declaration
and launch validators remain read-only; invalid proof inputs still leave the
run root uncreated.

RED: source/root guard tests failed because no claim helper existed. GREEN:
source/root guard tests 20 passed (0.90s). Broader focused launch contract,
declaration, approval, rollback and public transition tests passed 83/83
(30.76s). No worker orchestration, provider traffic, runtime flag change,
old-root reuse, proof generation or release freeze was performed. The next
missing dispatcher step at that checkpoint was executing identity plus wiring
the approved launch inputs into a single-use orchestrator that claims the root
exactly once. Those steps have since been implemented as described above; this
paragraph retains the historical test result, not the current remaining work.
