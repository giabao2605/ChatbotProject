# Query pilot 05/09/2026: đối soát incident

## Kết luận và phạm vi

Đối soát offline 08/09: run vẫn có 6 WAL, thiếu terminal/result/runtime-stop.
Đề xuất disposition nằm ngoài run tại
`.local/offline-freeze-20260908/incident-disposition-proposal.json`, khóa hash
WAL/trace/consumed và sáu capture, không chứa plaintext/ciphertext.
Trạng thái là `proposed_not_owner_approved`: giữ nguyên evidence chờ owner
chọn retention hoặc terminal-cleanup cho đúng sáu hash. Không tự giải mã/xóa,
không ghi successful pilot receipt, không resume/carry-forward. Các proof và
mô tả triển khai dưới đây là lịch sử; trạng thái source mới xem Query readiness.

Snapshot read-only lúc `2026-09-05T09:50:43+07:00` xác nhận pilot gián đoạn ở
6/100: supervisor/runtime không còn tồn tại và port 8302 trống. Không phải
completed pilot hoặc clean shutdown. Root đã consume, không được resume,
retry/replacement/catch-up hoặc chuyển sáu card/thời lượng sang window khác.

Đây là observation/disposition đề xuất bên ngoài run, không phải terminal
receipt do operator tạo, signed owner disposition hoặc quyền mở cửa sổ mới.
Không ghi thêm/sửa marker trong run. Sáu encrypted capture được giữ nguyên;
chưa giải mã hoặc xóa. Incident retention/deletion cần disposition riêng,
không dùng full-pilot success workflow đòi 100 WAL/20 capture.

## Identity và timeline đã kiểm

- Source: `38620eb02806278fb689446c34f2d99e1f6e0746`.
- Worktree nguồn sạch:
  `C:/Users/bao.nguyen/Documents/ChatBotProject/.local/worktrees/query-pilot-fail-closed`.
- Launch root (tương đối worktree nguồn):
  `.local/query-pilot-launch-38620eb-20260905-01`; evidence nằm trong `run/`.
- Deployment: `query-pilot-38620eb-candidate-02`, Query-only controlled demo.
- Không phải root ngày 29/08 đã dừng ở 17/100.

| Mốc (giờ Việt Nam, UTC+07) | Evidence |
| --- | --- |
| 08:09:14–08:09:16 | Task launch dùng `Start-Process ... -WindowStyle Hidden -PassThru`; wrapper đầu PID 7368. State ghi supervisor 28512, runtime wrapper 324, port 8302. |
| 08:11:43 | Attempt card 001 bắt đầu; lịch 100 card đã freeze, tối thiểu 24 giờ. |
| 09:24:42 | WAL card 006 hoàn tất; sáu card duy nhất, không có card 007 trong snapshot. |
| 09:25:20.8277192 | Windows Application Hang 1002 ghi Codex bị treo và đóng. |
| 09:27–09:50 | Các lần đối soát độc lập không thấy supervisor/runtime/listener; WAL vẫn 6. |
| 09:38:59 | Mốc scheduled card 007; tại snapshot 09:50 chưa có claim. Không cần đợi hết dispatch interval để kết luận process đã mất; không suy ra operator đã phát terminal vì không có marker. |

Kiểm trực tiếp PID 28512, 324 và server PID 23372: đều vắng mặt. Listener 8302:
0. State còn `runtime_stopped=false` là dữ liệu khởi động cũ, không phải liveness
probe. `terminal.json`, `result.json`, `runtime-stop.json` đều chưa tồn tại.
`consumed.json` ghi `retry_authorized=false`.

WAL/trace đã đối chiếu: 6 claim, 6 WAL, 6 unique card/trace, 6 start, 6 complete,
6 `pilot_request_evidence`; mỗi card attempt 1, không provider retry/error được
ghi trong sáu request này. Card 003/005 là deterministic safe refusal có
`owner_review_required=true`; không biến chúng thành quality acceptance.
Không có raw question, answer, tài liệu hoặc credentials trong báo cáo này.

## Chẩn đoán: bằng chứng và giới hạn

Windows Application log, Event ID `1002`, Record ID `25773`:

- TimeCreated: `2026-09-05T09:25:20.8277192+07:00`.
- Program: `ChatGPT.exe`, version `151.0.7922.174`.
- Package: `OpenAI.Codex_26.825.4187.0_x64__2p2nqsd0c76g0`.
- Process ID (hex): `53dc`; Report ID:
  `eda70364-174c-409d-a454-2078d2a15bb0`.
- Event nói chương trình ngừng tương tác với Windows và đã bị đóng.

Giả thuyết dẫn đầu: process campaign bị kết thúc đột ngột từ bên ngoài, có
tương quan thời gian với Codex hang/close. `Start-Process -WindowStyle Hidden`
chỉ chứng minh cách launch/ẩn cửa sổ, không chứng minh process sống độc lập với
host hoặc Windows job object.

Source `scripts/ops/query_decomposition_pilot_operator.py:supervise_pilot`
ghi state sau `Popen`, chạy pilot trong try/finally, rồi đóng logs, terminate/
wait/kill runtime và ghi stop receipt. CLI cũng có terminal-failure path cho
Exception. Việc cả process và final receipts đều mất phù hợp với việc cleanup
không chạy tới cuối; chưa đủ để phân biệt hard termination với mọi lỗi cleanup
có thể xảy ra. Không kết luận provider outage hay bug ở card 007 từ evidence này.

Application log trong khoảng incident không ghi Python crash liên quan;
máy không reboot (last boot 28/07). Đây là bằng chứng âm có giới hạn, không
loại trừ mọi native/process failure. Security process-termination event 4689
không đọc được do quyền hiện tại, nên chưa biết exact terminating actor hoặc
job-object policy. Không nâng quyền, không restart campaign để tái hiện lỗi.

## Fingerprints bảo toàn

SHA-256 đọc trực tiếp từ artifact; không sao chép raw logs hoặc payload:

| Artifact | SHA-256 |
| --- | --- |
| Authorization | `76510cc2d9e61758db773f1ee072faba6256a0aa714bc7b2043129cccdfbdf13` |
| Consolidated launch draft | `7606df6744f952af206e58f8090b36cbefd1d110f0661512c2f5b22e3ab59deb` |
| Schedule | `7d830fb5f4e4e9c47bcfd05902c3ef075e4b8cc7b59a24d984254b9884e694d8` |
| Activation bundle | `64b7a44655b9446b7e34dbebab09961a12c0de6477c86ba06a50a6c1e6e7bc62` |
| `run/consumed.json` | `52944d9224788f434701a54c82f5a18b214042f4d47e8a5eebdc4c1ce76ceb89` |
| `run/runtime-state.json` | `732d186e078d146e75069eca73417b88bbee9cc9fad3378632b7bcd1173cd33e` |
| `run/frozen-health.json` | `3b35eeb513b0968adbe04b4c6458ec6c154bda91dfd64db5f6d6c22657c0b3bb` |
| `run/pilot.wal.jsonl` | `4b51f661907bc10c1fac3bc5ef65447d6cbd7474b63918dca37d36a3342d1c16` |
| `run/trace.jsonl` | `f01ab9b8fb44906010076e58cb84f8ee7ed1b70b356dd6b72560fe7f1291147c` |
| `run/runtime.out.log` | `a4f304872adbd4e4a1423985764abc89f25b681eadd4a49f380364d33bf14996` |
| `run/runtime.err.log` | `ab28f1fb48fb90c3e8530786cc17a2c79cfb17578ce0d9e3ee950a492e09cf91` |

Frozen runtime identity digest:
`f9d38d7826c4116c9ec86756857e5614389bb109cde279db32f4d63624944e71`.
Frozen health is historical, not a current health claim.

## Bước tiếp theo, không thực thi trong incident audit

1. Owner chốt incident disposition và retention/deletion cho đúng 6 capture;
   không phát hành receipt đầy đủ 20, không sửa root để vượt gate.
2. Trong worktree offline, chứng minh lifecycle host/supervisor bằng process
   giả, bao gồm host đóng/crash, supervisor mất, child orphan, port chưa nhả
   và cleanup exception. Không dùng provider hoặc card thật để debug.
3. Chọn launcher có lifetime độc lập đã được chứng minh, fail-closed về
   exactly-once và observable terminal state. Mọi hardening phải được review,
   kiểm thử rồi freeze commit mới, không patch nóng source 38620eb.
4. Hoàn tất các thiếu sót post-pilot và CRAG theo hai packet cùng thư mục.
   Đây là các prerequisite được audit, không phải claim code đã sửa.
5. Chỉ sau đó trình draft/authorization mới bind exact source, never-used root,
   schedule, runtime/provider/target identities và traffic contract. Fresh
   preflight/rollback/smoke tuân theo quyền mới. Không reuse authorization cũ,
   không default enablement/push/merge.

## Verification phạm vi công việc

Đã đối soát live process/port và artifact read-only; kiểm Windows Application
event; so source cleanup path và launch command. Không thay runtime, WAL,
Scheduled Task, `.env`, DB/Qdrant, credentials hoặc activation. Báo cáo cố ý
giữ giới hạn causal certainty vì không có process-termination audit khả dụng.

Kiểm tra bàn giao: 194/194 test offline từ 10 file liệt kê trong hai readiness
packet được chạy lại cùng một invocation, exit code 0. Markdown links tới ba
packet đều tồn tại, code fences cân bằng, không trailing whitespace;
`git diff --check` pass. Source runtime vẫn sạch, các run fingerprints trên
không đổi. Test xanh này chỉ xác nhận baseline trong phạm vi đã chạy, không
chứng minh lỗi launcher/CLI/CRAG được sửa hoặc pilot đã đạt acceptance.

## Checkpoint continuation: các sửa đổi offline và gate vận hành

Sau audit, đã sửa CLI final gate Query và harness CRAG trong worktree chuẩn bị
riêng (không phải source pilot 38620eb). Chi tiết RED/GREEN và giới hạn evidence
nằm trong hai readiness packet. Root incident/capture không bị sửa hoặc xóa.

Launcher chưa được sửa và chưa có bằng chứng survival qua host crash.
`start_rag_profile_pair.ps1` có kiểm listener/process identity đáng tái sử dụng,
nhưng helper `Start-CragDemoProcess` của nó cũng dùng `Start-Process`; không
được gọi đây là host-independent solution. Thêm `CREATE_NO_WINDOW` chỉ ẩn
window. Windows job có thể quản lý cả process con; breakaway còn phụ thuộc
policy của job cha. Không tự thêm breakaway flag hoặc khẳng định nó giải quyết
incident. Tham chiếu: [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
và [Process Creation Flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags).

Đề xuất cần owner chốt: dùng Windows Task Scheduler làm host cho một operator
duy nhất, dưới đúng Windows user cần DPAPI CurrentUser, không trigger lặp,
không restart-on-failure/catch-up. Trước campaign thật, tạo một task proof riêng
chỉ chạy process giả hữu hạn: parent shell thoát, fake supervisor bị kill,
orphan runtime/port-release và cleanup-error. Chỉ cleanup PID/port có ownership
đã xác minh, không quét/kill rộng. Đây là đề xuất chưa triển khai; nó cần quyền
tạo Scheduled Task, vốn bị loại khỏi scope hiện tại. Không đổi task Math cũ.

Owner cũng cần chốt retention/disposition đúng sáu encrypted capture. Không
suy diễn “làm tiếp” thành quyền xóa chúng. Matrix Math+Query cần chốt riêng
baseline/manifest/concurrency và versioned contract; chưa sửa matrix toàn cục
hoặc runner Query-only để lách scope. Các quyền task proof, capture disposal,
matrix contract và fresh pilot/provider window là độc lập với nhau.

## Scheduled Task proof đã được owner cho phép và thực hiện

Owner trả lời `ok mình cho phép` cho đúng phép thử Scheduled Task tạm chạy
process giả, không phải quyền chạy pilot/provider hoặc xóa capture.

Script thử nghiệm: `scripts/ops/test_query_scheduler_lifetime.ps1` (không phải
production launcher), SHA-256
`7f5915186e38e76fcf718d86f6cbc48098d3f3a06e5f98666fff7b613d6ce3af`.
Chỉ dùng Windows cmdlets, không thêm dependency; script payload chỉ ghi metadata
trong root proof riêng rồi sleep hữu hạn, không import/start RAG.

Run local:
`.local/query-scheduler-proof/325a10f3010f49b68c3e26d6025bedf2/result.json`,
SHA-256 `850c98ef3e675861baa600beb436894be0425bfcf4f240e655d70e60c3e1e3cc`.

| Evidence | Kết quả |
| --- | --- |
| Task | `ChatBotProject-Query-LifecycleProof-325a10f3010f49b68c3e26d6025bedf2` |
| Principal/settings | Current user, Interactive, Limited; 0 trigger, 0 restart; execution limit 45 giây |
| Launcher PID 24756 thoát | `2026-09-05T04:01:54.0483506Z` |
| Payload PID 14220 được thấy còn sống sau launcher exit | `2026-09-05T04:01:54.3405012Z` |
| Payload hoàn tất | `2026-09-05T04:02:06.3066306Z` |
| Verdict | `passed`, task result `0`, payload đã thoát |
| Cleanup kiểm lại độc lập | 0 temporary proof task, 0 process còn lại từ hai PID đã ghi |

Đã gỡ task tạm; giữ script và metadata proof để audit. Không xóa user data.
WAL/trace pilot giữ nguyên hash ở bảng trên; sáu ciphertext còn nguyên;
runtime source sạch; task Math Window13 vẫn Disabled.

Phép thử chứng minh Task Scheduler có thể thực thi payload sau khi launcher
thoát trong phiên user interactive hiện tại. Không cố tình crash Codex, kill
supervisor, đăng xuất/reboot, tạo child runtime/listener, kiểm DPAPI hoặc chạy
24 giờ. Vì vậy chưa chứng minh production supervisor recovery/cleanup,
orphan/port-release, khả năng qua logout/reboot hoặc nguyên nhân incident cũ.
Không lấy `passed` của proof này làm quyền triển khai launcher/pilot mới.

## Reconciliation bổ sung lúc 2026-09-05T05:57:26Z

Kiểm read-only lại: 6 WAL, 6 claim, không listener 8302. WAL và trace giữ
nguyên SHA trong bảng fingerprints. Không có terminal/result/runtime-stop
receipt do operator tạo. Operational disposition của gói chuẩn bị là
`interrupted_consumed_non_reusable`, `pilot_accepted=false`; không ghi
disposition này vào run dưới dạng terminal receipt giả hoặc owner signature.

Sáu ciphertext vẫn được giữ tại chỗ, không giải mã/sao chép/xóa. Inventory
hash-only cho bước retention/deletion riêng:

| Capture | SHA-256 |
| --- | --- |
| `query-pilot-001.capture.json` | `309295da1ae7e255b1c911cb1f185c14abcac8465df9e3df8b63c143ad5f633f` |
| `query-pilot-002.capture.json` | `c4de33140c672830b0958ea79bd57b213ca084f825aae38892aa0af7c33375bb` |
| `query-pilot-003.capture.json` | `b0f7c8687174a98f04b79a25dbedf6d2ddf0615a99080f16c7fdaca9bb1129be` |
| `query-pilot-004.capture.json` | `0f9640213e136cb9a46c165b806f026303c0dfcc36d2dcad2805a996b1435770` |
| `query-pilot-005.capture.json` | `36123ed7de60008fb5a3562dd4da22e1667f83f76a73e1e8456f54e65d6fdc6e` |
| `query-pilot-006.capture.json` | `bf8c62b64a4ecf9409ea300866b7c8d6d1b9397e7964d99d116671cb7b0b129e` |

Code đã có `cleanup_terminal_captures` và deletion journal cho partial run;
nếu chọn deletion, dùng đúng tập sáu hash và authorization hash lịch sử để
ghi terminal-cleanup-only receipt, không dùng success-review workflow 20
capture. Hàm này **chưa được gọi** trên run thật. Việc chuẩn bị launcher mới
không tự tạo quyền giải mã capture, xác nhận review hoặc thay evidence cũ.

## Process-tree hardening: offline verification

Trong worktree chuẩn bị, `scripts/ops/query_pilot_windows_job.py` bổ sung
Windows Job Object không truyền handle cho child, `KILL_ON_JOB_CLOSE`, và thứ
tự create suspended → assign job → resume. Cleanup dùng process/job handle
đang sở hữu, không lookup/kill theo PID lưu trong artifact lịch sử.

Parent chạy độc lập `tests/unit/test_query_pilot_windows_job.py`: **13/13
pass trong 0.86 giây**. Các test dùng Python/process giả kiểm argument quoting,
bounded wait, assignment failure trước resume, owner bị kill đột ngột,
venv child/grandchild dừng và process ngoài job vẫn sống. `wait_empty` kiểm
ActiveProcesses bằng 0 trước khi trả bằng chứng tree đã dừng. Worker báo
coverage branch+statement 92%; chưa có parent coverage rerun cho toàn launcher.

Đây là primitive lifecycle proof, không phải proof Scheduled Task với operator
thật, DPAPI, 24 giờ hoặc qua logout/reboot. Host/task binding và review vẫn
đang hoàn thiện, chưa freeze; không mở runtime hoặc dùng lại authorization.

## Scheduled Task chạy bộ lifecycle test mới

Proof `f27e132e5ee74734a26f258fbb731f2f`, hoàn tất
`2026-09-05T06:19:04Z`: task tạm chạy hai bộ host/WindowsJob test bằng interpreter
đã chỉ định; JUnit ghi **32 tests, 0 failure, 0 error, 0 skip**, 1.755 giây.
Launcher PID 23812 thoát trước khi payload PID 2932 hoàn tất; task result 0.
Parent kiểm lại task chính xác không còn và hai PID proof đều đã thoát.

Result tại `.local/query-scheduler-proof/f27e132e5ee74734a26f258fbb731f2f/result.json`,
SHA-256 `82f801ff4ece9e6b060d3764c34d9774b2c2ed9768e32e6f36f4bf85842794dc`.
Script proof thêm option `-PythonExe`; default proof cũ vẫn chỉ dùng payload
sleep. Bộ test được chọn cố định, chỉ có process/socket/ciphertext giả, không
gọi operator thật hoặc provider. Không nhầm task thử nghiệm này với task
`ChatBotProject-Query-Governed-*` của launcher đang chuẩn bị.

Review primitive đã sửa failure đóng thread handle bằng regression RED/GREEN;
WindowsJob suite mới 14/14. Job containment chỉ cam kết subprocess/CreateProcess
descendants; WMI/brokered process creation ngoài job không nằm trong contract
operator này. Vẫn chưa chứng minh happy-path packet authorization mới, hoạt
động 24 giờ, DPAPI trong task hoặc qua logout/reboot.
