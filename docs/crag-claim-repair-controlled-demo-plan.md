# Kế hoạch đưa CRAG + Claim Repair vào controlled demo

## Mục tiêu

Chỉ thử nghiệm hai flag `RAG_CRAG_ENABLED` và `RAG_CLAIM_REPAIR_ENABLED` trên
collection `TaiLieuKyThuat_v2`, phòng `Technical`, site `HQ`, 2–10 người nội
bộ. Control và candidate chạy thành hai process riêng trên ports 8101/8102;
ứng dụng browser chạy port 8080 và chia arm ổn định theo người dùng đã xác thực.

Grounded Math, Late Interaction, Query Decomposition, GraphRAG và Community
Summaries tiếp tục tắt trong lần này. Mốc 20 matched pair là checkpoint demo;
sau đó dừng gateway. Pilot 100 matched pair/7–14 ngày là kế hoạch riêng.

Nếu chỉ có `bao.nguyen` review, artifact phải ghi `single_owner`,
`owner_review` và `risk_accepted=true`; tuyệt đối không ghi thành independent
review. Kết quả controlled demo không tự cho phép bật mặc định. Default rollout
chỉ được xét sau artifact pilot 100 pair và các checklist tương ứng.

## Trạng thái thực hiện

| Việc | Ai làm | Trạng thái | Điều kiện hoàn tất |
| --- | --- | --- | --- |
| Tối ưu parent-context bằng worker pool giới hạn | Codex | Đã làm ở code | Targeted test và full suite đạt; worker `1` giữ đường rollback tuần tự |
| Bỏ lượt gọi LLM khi có explicit negative evidence | Codex | Đã làm ở code | Chỉ dùng deterministic khi resolve được SourceID; nếu không thì fallback generation an toàn |
| CLI phân tích latency từ raw trace | Codex | Đã làm ở code | Artifact `crag-latency-breakdown-v1` chỉ có trace ID đã hash và số latency |
| CLI tạo cohort hash | Codex | Đã làm ở code | Input user ID chỉ đọc local; output không chứa raw identity hoặc secret |
| Script start/enable/status/stop control, candidate và gateway | Codex | Đã làm ở code | Start chỉ mở hai arm và tạo preflight; enable gateway là lệnh riêng sau phê duyệt |
| Regression/targeted/full tests | Codex | Runtime commit `041aec4` có focused freshness suite `75 passed` và GitHub Actions `unit`/`frontend` đều `success`. Sau khi window dừng, commit `3b884a3` khóa cùng một timestamp cho freshness check và baseline artifact; focused suite đạt `78 passed` | Evidence CI của runtime commit: run `30418136033`; không chạy lại evaluation sau khi đã thấy pair 02 fail |
| Self-review hai trục Standards/Spec | Codex + sub-agent | Standards và Security review không còn finding chặn; Spec review xác nhận staging pass chỉ cho phép chuẩn bị authorization, không thay thế human gate. Chưa có run-local review artifact nên các review trong task không được tính là release evidence | Capture hoặc chạy lại review artifact trước bất kỳ gateway approval nào |
| Rollback CRAG + Claim Repair | Codex | Đã đạt trên runtime commit `041aec4`: hai flag false và hai rollback test đạt | Evidence: `reports/controlled-demo/20260729-crag-window-02-041aec4/rollback.json` |
| Provider smoke cho từng pair | Codex chạy lệnh; provider phải sẵn sàng | Pair 01 và pair 02 đều đạt 5/5, 0 retry, provider không blocked và cùng provider hash; pair 03 không chạy do stop rule. Process evaluation pin `MAX_CONCURRENT_RAG=4` mà không sửa `.env` | Evidence: `reports/controlled-demo/20260729-crag-window-02-041aec4/pair-01/provider-smoke.json`, `pair-02/provider-smoke.json` |
| Ba staging baseline/candidate pairs | Codex chạy lệnh; SQL/Qdrant/provider phải sẵn sàng | Window mới được khai báo trước trên commit `041aec4`. Pair 01 đạt toàn bộ gate. Pair 02 candidate đạt 9/9, P50 và cost tốt hơn, retry 0 nhưng fail duy nhất latency: P95 `13380.82/8532.47 = 1.568 > 1.25`. Outlier `crag-alias-correction` có corrective retrieval `2110 ms` và generation provider `7145 ms` so với baseline `3024 ms`; Voyage cùng fallback không retry. Pair 03 không chạy, không tạo series/authorization | Evidence: `reports/controlled-demo/20260729-crag-window-02-041aec4/evaluation-window-declaration.json`, `window-outcome.json`, `pair-01/run.json`, `pair-02/run.json` |
| Main-collection evaluation và CRAG high-risk review pack | Codex tạo pack; con người điền review | Bị hoãn đúng fail-closed vì staging series hiện tại chưa đạt. Pack 6 case dưới run `20260718-crag-controlled-demo-readiness` chỉ là evidence lịch sử của commit `3e297cb` | Chỉ chạy lại main-collection và tạo pack mới sau khi một series mới trên cùng runtime commit đạt; manifest vẫn cần đủ 20 case và human review |
| Chọn 2–10 tài khoản Technical/HQ và xác nhận được phép tham gia | Con người | Chưa làm | Có cohort hash; không ghi username vào artifact |
| Xác nhận snapshot `TaiLieuKyThuat_v2` không đổi trong cửa sổ tối đa ba ngày | Con người vận hành | Chưa làm | Điền snapshot fingerprint vào config trước khi start |
| Phê duyệt mở browser gateway sau deployment preflight | Con người | Chưa làm | Người vận hành đọc `deployment-preflight.json` rồi chạy script enable riêng |
| Gửi câu hỏi thật để tạo 20 matched pairs | Cohort 2–10 tài khoản | Chưa làm | Review toàn bộ 20 pair rồi dừng controlled demo; pilot 100 pair là đợt riêng |
| Review câu trả lời/citation/refusal | `bao.nguyen` | Chưa làm | Điền toàn bộ review pack; không sửa field ngoài `human_review` |
| Theo dõi leakage, wrong-answer, latency và cost | Con người + artifact tự động | Chưa làm | Abort ngay khi chạm điều kiện dừng bên dưới |
| Ký quyết định controlled demo | `bao.nguyen` | Chưa làm | Ghi `single_owner` nếu review một người; accepted/rejected theo gate thật, không tự bật default rollout |

## Các bước Codex có thể tự chạy sau khi code được commit

1. Chạy provider smoke không quá 30 phút trước từng pair. Nếu bất kỳ smoke nào
   không đạt 5/5, có retry hoặc quá hạn, dừng tại đó và ghi `inconclusive`;
   không chạy eval tốn chi phí.
2. Tạo rollback evidence gắn với commit sạch mới.
3. Chạy ba baseline/candidate pair độc lập trên fixture `crag-eval-v1`, giữ cùng
   commit, fixture fingerprint, provider configuration và concurrency.
4. Kiểm tra từng gate: leakage bằng 0, wrong-answer không tăng, correction và
   repair không quá một, cost không quá 1.5x và P95 không quá 1.25x.
   Nếu một pair fail, dừng trước pair kế tiếp và giữ quyết định
   `inconclusive`; không retry để chọn kết quả đẹp hơn sau khi đã xem số.
5. Chạy series guardrail trên cả ba pair. Chỉ khi tất cả đạt mới tạo
   `crag-controlled-demo-authorization-v1` từ series và đúng ba provider smoke.
6. Tạo decision `controlled_demo=accepted` tham chiếu authorization trên cùng
   commit, rồi tạo activation bundle bất biến. Đây chỉ là quyền bắt đầu pilot,
   chưa phải kết quả pilot hoặc quyền bật mặc định.
7. Mở controlled demo tối đa ba ngày để lấy checkpoint 20 pair, sau đó dừng
   gateway và hai arm. Chỉ mở pilot 100 pair/7–14 ngày bằng kế hoạch riêng.

Provider smoke tooling đã được sửa để unwrap exception cuối từ Tenacity nhưng chỉ
lưu root exception type, HTTP status và error category. Nhờ đó lỗi capacity 503
không còn bị phân loại nhầm thành `non_capacity_failure`, đồng thời artifact vẫn
không chứa raw error message, prompt, response hoặc secret.

Sau typed-event-facade hardening, hai serving arm của controlled demo phải pin
runtime contract trong config: `execution_context=production`,
`evaluation_force_ambiguous=false`, `request_deadline_seconds=120`,
`collection=TaiLieuKyThuat_v2` và `max_concurrent_rag=4`. Hai arm phải báo đúng
cùng contract, collection và concurrency qua `/health`; deployment preflight
fail nếu thiếu hoặc lệch. Staging baseline/candidate chạy qua evaluator typed
vẫn dùng `execution_context=evaluation`.

## Các bước bắt buộc con người làm

1. Chọn cohort thật, tối thiểu hai tài khoản thuộc `Technical/HQ`, rồi tạo cohort
   hash và actor HMAC-SHA256 bằng đúng experiment ID/assignment salt. Codex không
   tự chọn hoặc giả danh người tham gia. Chỉ actor hash được ghi vào config.
2. Xác nhận snapshot fingerprint và cửa sổ UTC tối đa ba ngày trong bản copy của
   `docs/examples/crag-controlled-demo-config.example.json`.
3. Đặt `CRAG_PILOT_ASSIGNMENT_SALT` bằng secret store hoặc biến môi trường. Không
   ghi secret này vào config, log, chat hoặc artifact.
4. Sau khi đọc và đồng ý với `deployment-preflight.json`, chủ động cho phép mở
   gateway bằng `scripts/ops/enable_crag_controlled_demo.ps1`.
5. Sử dụng chatbot thật để thu thập matched pairs. Codex không thể tạo bằng chứng
   người dùng độc lập thay cho traffic thật.
6. `bao.nguyen` review toàn bộ sample bắt buộc. Nếu dùng chế độ một người,
   review được ghi là `owner_review`/`single_owner`, không phải independent.

## Lệnh vận hành

Tạo file tạm ngoài repo, mỗi dòng là một user ID được duyệt, rồi sinh actor hash
và cohort hash. Artifact kết quả không chứa raw user ID hoặc salt:

```powershell
$env:CRAG_PILOT_ASSIGNMENT_SALT = '<secret-store-value>'
chat_env\Scripts\python.exe -m scripts.eval.crag_cohort_hash `
  --user-ids-file C:\temp\crag-demo-users.txt `
  --experiment-id crag-controlled-demo-v1 `
  --output reports\controlled-demo\<run-id>\cohort-hashes.json
```

Copy `actor_hashes` và `cohort_sha256` sang config run-specific. Không đưa file
raw user ID vào repo hoặc artifact.

Sau khi ba pair và ba smoke tương ứng đều đạt, tạo authorization kỹ thuật. Thứ
tự `--provider-smoke` phải khớp thứ tự ba pair trong series:

```powershell
chat_env\Scripts\python.exe -m scripts.eval.crag_demo_authorization `
  --series reports\controlled-demo\<run-id>\crag-series-guardrail.json `
  --provider-smoke reports\controlled-demo\<run-id>\provider-smoke-01.json `
  --provider-smoke reports\controlled-demo\<run-id>\provider-smoke-02.json `
  --provider-smoke reports\controlled-demo\<run-id>\provider-smoke-03.json `
  --review-mode single_owner `
  --output reports\controlled-demo\<run-id>\crag-demo-authorization.json
```

Decision `controlled_demo` của CRAG phải là `accepted` và tham chiếu đúng
authorization trên clean commit. Nếu decision vẫn là `inconclusive`, launcher
sẽ fail-closed; không đổi flag thủ công để bỏ qua bước này. Tạo decision bằng
CLI, sau đó tạo bản copy run-local của controlled-demo ledger dưới
`reports/controlled-demo/<run-id>` và cập nhật reference/hash của mục `crag`
trong bản copy đó:

```powershell
chat_env\Scripts\python.exe -m scripts.eval.milestone_decision build `
  --milestone crag `
  --scope controlled_demo `
  --decision accepted `
  --source-commit (git rev-parse HEAD) `
  --evidence reports\controlled-demo\<run-id>\crag-demo-authorization.json `
  --reason "Ba pair va ba provider smoke dat gate ky thuat" `
  --reviewer bao.nguyen `
  --output reports\controlled-demo\<run-id>\crag-controlled-demo-decision.json
```

Sau khi ledger đã trỏ đúng decision mới, tạo bundle bất biến rồi copy `path` và
`sha256` được in ra vào trường `activation_bundle` của config:

```powershell
chat_env\Scripts\python.exe -m scripts.ops.build_activation_bundle `
  --scope controlled_demo `
  --profile crag_claim `
  --source-commit (git rev-parse HEAD) `
  --decision-ledger reports\controlled-demo\<run-id>\demo-decisions.json `
  --review-governance reports\controlled-demo\<run-id>\review-governance.json `
  --output reports\controlled-demo\<run-id>\activation-bundle.json
```

Tạo config run-specific dưới `reports/controlled-demo/<run-id>/config.json`, sau
đó đặt secret chỉ trong process PowerShell hiện tại. Chỉ dùng lệnh start khi
staging series đã đạt. Lệnh đầu chỉ start control/candidate và tạo preflight:

```powershell
$env:CRAG_PILOT_ASSIGNMENT_SALT = '<secret-store-value>'
scripts\ops\start_crag_controlled_demo.ps1 `
  -Config reports\controlled-demo\<run-id>\config.json
```

Sau khi con người đã đọc `deployment-preflight.json` và thấy `passed=true`, mở
gateway bằng lệnh riêng:

```powershell
scripts\ops\enable_crag_controlled_demo.ps1
```

Kiểm tra và dừng:

```powershell
scripts\ops\status_crag_controlled_demo.ps1
scripts\ops\stop_crag_controlled_demo.ps1
```

Mỗi arm ghi raw trace riêng để không trộn control/candidate. Trace snapshot dùng
cho traffic người dùng chỉ lọc `production`. Với từng performance window 50
matched pairs, tạo một latency artifact cho mỗi arm từ cả request chính
(`production`) và lượt matched-pair đối diện (`pilot_replay`):

```powershell
chat_env\Scripts\python.exe -m scripts.eval.crag_latency_breakdown `
  logs\crag-controlled-demo\candidate-trace.jsonl `
  --start <WINDOW_UTC_START> --end <WINDOW_UTC_END> `
  --context production --context pilot_replay `
  --output reports\controlled-demo\<run-id>\candidate-latency-<window>.json

chat_env\Scripts\python.exe -m scripts.eval.crag_latency_breakdown `
  logs\crag-controlled-demo\control-trace.jsonl `
  --start <WINDOW_UTC_START> --end <WINDOW_UTC_END> `
  --context production --context pilot_replay `
  --output reports\controlled-demo\<run-id>\control-latency-<window>.json
```

Khi tạo final pilot artifact, truyền raw trace của hai arm và lặp lại tham số
`--control-latency-breakdown`/`--candidate-latency-breakdown` cho mọi performance
window. Gate tự hash lại file trace và latency artifact, gắn từng file hash với
canonical content digest, bắt buộc hai arm dùng evidence độc lập và context đúng
`production + pilot_replay`, rồi tự đối chiếu P95/cost với từng monitoring
window và query count/P50/P95/cost với matched-pair aggregate. Thiếu một
arm/window hoặc sửa số bằng tay đều fail-closed.

## Điều kiện abort và kết thúc

Abort ngay khi có leakage, wrong-answer nghiêm trọng, correction/repair vượt một
lần, hoặc hai cửa sổ liên tiếp vi phạm latency/cost. Thứ tự dừng là gateway,
candidate, rồi control; script stop dùng đúng PID đã lưu và từ chối dừng PID đã
bị process khác tái sử dụng.

Khi đủ 20 pair hoặc hết ba ngày: dừng gateway và hai arm, hoàn tất review toàn
bộ 20 pair rồi ghi `checkpoint_go`, `aborted`, `rejected` hoặc `inconclusive`
theo gate thật. `checkpoint_go` chỉ cho phép lập kế hoạch pilot 100 pair/7–14
ngày; mọi default release flag vẫn giữ OFF cho đến khi default-rollout ledger và
activation bundle riêng được chấp nhận.
