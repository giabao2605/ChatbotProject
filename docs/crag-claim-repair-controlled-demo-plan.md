# Kế hoạch đưa CRAG + Claim Repair vào controlled demo

## Mục tiêu

Chỉ thử nghiệm hai flag `RAG_CRAG_ENABLED` và `RAG_CLAIM_REPAIR_ENABLED` trên
collection `TaiLieuKyThuat_v2`, phòng `Technical`, site `HQ`, tối đa 5–10 người
nội bộ. Control và candidate chạy thành hai process riêng trên ports 8101/8102;
ứng dụng browser chạy port 8080 và chia arm ổn định theo người dùng đã xác thực.

Grounded Math, Late Interaction, Query Decomposition, GraphRAG và Community
Summaries tiếp tục tắt trong lần này. Controlled demo dừng khi đủ 20 matched
pairs từ ít nhất hai tài khoản hoặc hết ba ngày, tùy điều kiện nào đến trước.

Do chỉ có `bao.nguyen` làm reviewer, kết luận cuối của lần chạy này bắt buộc là
`inconclusive`. Kết quả không được dùng để bật mặc định hoặc ghi
`release_decisions.json` thành `accepted`.

## Trạng thái thực hiện

| Việc | Ai làm | Trạng thái | Điều kiện hoàn tất |
| --- | --- | --- | --- |
| Tối ưu parent-context bằng worker pool giới hạn | Codex | Đã làm ở code | Targeted test và full suite đạt; worker `1` giữ đường rollback tuần tự |
| Bỏ lượt gọi LLM khi có explicit negative evidence | Codex | Đã làm ở code | Chỉ dùng deterministic khi resolve được SourceID; nếu không thì fallback generation an toàn |
| CLI phân tích latency từ raw trace | Codex | Đã làm ở code | Artifact `crag-latency-breakdown-v1` chỉ có trace ID đã hash và số latency |
| CLI tạo cohort hash | Codex | Đã làm ở code | Input user ID chỉ đọc local; output không chứa raw identity hoặc secret |
| Script start/enable/status/stop control, candidate và gateway | Codex | Đã làm ở code | Start chỉ mở hai arm và tạo preflight; enable gateway là lệnh riêng sau phê duyệt |
| Regression/targeted/full tests | Codex | Đã đạt sau typed-facade hardening ngày 2026-07-18 | Targeted và full pytest đạt trên code cuối; 22 integration/eval test cần live SQL/Qdrant/RAG/encoder được skip đúng opt-in |
| Self-review hai trục Standards/Spec | Codex | Đã đạt sau nhiều vòng sửa và re-review | Hai review độc lập đối chiếu từ `351d53d` đến code cuối; không còn finding actionable ảnh hưởng demo |
| Rollback CRAG + Claim Repair | Codex | Evidence `59d432d` chỉ còn là lịch sử | Artifact hợp lệ là `reports/controlled-demo/20260718-crag-controlled-demo-readiness/crag-rollback-latest.json` với `git_sha` đúng bằng clean HEAD và hai rollback test đạt |
| Provider smoke đầu/cuối | Codex chạy lệnh; provider phải sẵn sàng | Ba lần đều fail ngày 2026-07-18; diagnostic: 0/5, 15 retry, HTTP 503 capacity | Evidence đúng: `reports/controlled-demo/20260718-crag-controlled-demo-readiness/provider-smoke-diagnostic.json`; quyết định `inconclusive`, dừng staging/live eval |
| Ba staging baseline/candidate pairs | Codex chạy lệnh; SQL/Qdrant/provider phải sẵn sàng | Chờ provider smoke | Cùng commit/snapshot/manifest/config/concurrency; cả ba gate đạt |
| Chọn 2–10 tài khoản Technical/HQ và xác nhận được phép tham gia | Con người | Chưa làm | Có cohort hash; không ghi username vào artifact |
| Xác nhận snapshot `TaiLieuKyThuat_v2` không đổi trong ba ngày | Con người vận hành | Chưa làm | Điền snapshot fingerprint vào config trước khi start |
| Phê duyệt mở browser gateway sau deployment preflight | Con người | Chưa làm | Người vận hành đọc `deployment-preflight.json` rồi chạy script enable riêng |
| Gửi câu hỏi thật để tạo 20 matched pairs | Ít nhất hai người dùng | Chưa làm | Đủ 20 pairs hoặc hết ba ngày |
| Review câu trả lời/citation/refusal | `bao.nguyen` | Chưa làm | Điền toàn bộ review pack; không sửa field ngoài `human_review` |
| Theo dõi leakage, wrong-answer, latency và cost | Con người + artifact tự động | Chưa làm | Abort ngay khi chạm điều kiện dừng bên dưới |
| Ký quyết định cuối `inconclusive` | `bao.nguyen` | Chưa làm | Ghi rõ thiếu independent reviewer; tất cả feature flags trở về false |

## Các bước Codex có thể tự chạy sau khi code được commit

1. Chạy provider smoke lần một. Nếu không đạt 5/5 hoặc có retry, dừng tại đây và
   ghi kết quả `inconclusive`; không chạy eval tốn chi phí.
2. Tạo rollback evidence gắn với commit sạch mới.
3. Chạy ba baseline/candidate pair độc lập trên fixture `crag-eval-v1`, giữ cùng
   commit, fixture fingerprint, provider configuration và concurrency.
4. Kiểm tra từng gate: leakage bằng 0, wrong-answer không tăng, correction và
   repair không quá một, cost không quá 1.5x và P95 không quá 1.25x.
5. Chạy series guardrail trên cả ba pair. Chỉ khi tất cả đạt mới chuẩn bị config
   controlled demo.
6. Chạy main-collection evaluation read-only và sinh review pack. Manifest CRAG
   hiện có 18 case trong khi milestone yêu cầu 20, nên kết quả này vẫn có ceiling
   `inconclusive`; không tạo case giả để vượt gate.
7. Chạy provider smoke lần hai và xác nhận hash cấu hình giống lần một.

Provider smoke tooling đã được sửa để unwrap exception cuối từ Tenacity nhưng chỉ
lưu root exception type, HTTP status và error category. Nhờ đó lỗi capacity 503
không còn bị phân loại nhầm thành `non_capacity_failure`, đồng thời artifact vẫn
không chứa raw error message, prompt, response hoặc secret.

Sau typed-event-facade hardening, hai serving arm của controlled demo phải pin
runtime contract trong config: `execution_context=production`,
`evaluation_force_ambiguous=false` và `request_deadline_seconds=120`. Hai arm phải
báo đúng cùng contract qua `/health`; deployment preflight fail nếu thiếu, lệch
hoặc deadline chỉ là một giá trị dương khác 120. Staging baseline/candidate chạy
qua evaluator typed vẫn dùng `execution_context=evaluation`.

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
6. `bao.nguyen` review toàn bộ sample bắt buộc. Vì không có reviewer thứ hai,
   review này không được coi là independent review và không thể tạo quyết định
   `accepted`.

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

Khi đủ 20 pairs hoặc hết ba ngày: dừng gateway và hai arm, hoàn tất review pack,
tạo decision `inconclusive`, cập nhật roadmap/controlled-demo decision, nhưng giữ
mọi default release flag ở trạng thái chưa accepted.
