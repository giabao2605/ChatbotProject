# Query: gói chuẩn bị hậu-pilot ngày 2026-09-05

Trạng thái bàn giao: lưu **local implementation checkpoint**, không phải
release freeze hoặc hoàn tất toàn scope. Các checkpoint “chưa commit” bên
dưới là lịch sử trước snapshot này. Query gate wiring, CRAG isolation,
Scheduled Host/Job và proof đã được kiểm; matrix mới có draft validator và
arm command planning, chưa có dispatcher hoặc evidence acceptance validator.
Independent review binding đã đóng; review delta toàn gói còn thiếu do quota.
Dependency advisories còn mở. Không có quyền mới để chạy pilot/matrix,
không push/merge/default activation. Commit chỉ lưu code/doc/test offline,
không chứa `.local` runtime, approval thật hoặc ciphertext của run cũ.

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
