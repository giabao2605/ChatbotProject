# CRAG: readiness offline ngày 05/09/2026

## Checkpoint bản sửa offline

Hai lỗi harness bên dưới đã được sửa trong worktree chuẩn bị, chưa
commit/freeze và không được áp vào source/run lịch sử `38620eb`:

- Arm environment ép năm feature ngoài scope OFF, dùng đúng tên canonical
  `RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED`; baseline all-off, candidate chỉ
  CRAG + Claim Repair. Không thay môi trường process cha.
- Lệnh evaluator bind `--maximum-provider-retries 0` trước dispatch qua seam
  đã có; không tăng timeout hoặc cho phép retry sau failure.
- Review phát hiện thêm provider map có thể ghi đè environment sau guard;
  nay provider settings được merge trước exact arm overrides. Test cả host
  lẫn provider map bị nhiễm, baseline/candidate và hai router mode (8 ca).
- Thêm `--stop-on-provider-failure` để evaluator không dispatch case kế tiếp
  sau provider failure; regression invocation RED trước bản sửa, GREEN sau.
- TDD: bốn ca host environment bị nhiễm RED rồi GREEN; test arm invocation
  RED vì thiếu retry option rồi GREEN. Các test downstream của evaluator và
  runtime fake transient provider xác nhận zero-budget dừng sau đúng một
  provider call. Suite liên quan pass 108/108 trong 17.34 giây.

Validation mở rộng: 144/144 focused tests pass, coverage branch+statement hai
module sửa 83% (CRAG 81%, Query 85%). Tám ca environment cũng pass riêng.
Full unit suite: 3236 pass, 1 fail ở
`test_query_preparation_runner_binding_matches_current_runner`; lỗi hash
`907ab3...` khác `c430b0...` tái hiện y hệt trên source sạch `38620eb`.
Không sửa historical packet/expected hash để che lỗi baseline. Full suite này
chạy trước hai chỉnh sửa cuối do review; focused suite đã chạy lại trên source
cuối. Không claim full-suite green hoặc coverage toàn repo.

Đây là bản sửa offline, chưa phải fresh measured evidence hoặc quyền mở
window. Các phần ghi baseline bên dưới giữ lại phát hiện trước bản sửa.

## Phạm vi và kết luận

Đã đọc lại artifact thật của diagnostic CRAG `crag-v3-window-02`, so sánh
source lịch sử với source Query mới và chạy test giả lập. CRAG có đủ harness
để tiếp tục chuẩn bị offline, nhưng chưa đủ điều kiện mở window mới. Audit
baseline tìm thấy hai khoảng trống feature isolation/zero retry; trạng thái
bản sửa mới được cập nhật riêng ở checkpoint phía trên.
Provider hiện tại chưa được kiểm tra trong công việc này.

Tài liệu là báo cáo và checklist, không phải approval, declaration hay evidence
cho activation. Không chạy provider, SQL, Qdrant; không thay runtime, `.env`,
credential, WAL, Scheduled Task hoặc artifact cũ. Không copy smoke/trace/pair
của Query hoặc CRAG sang window tương lai.

## Identity đã đối chiếu

- Source diagnostic lịch sử: `27e480962492ed119d613b26e7d6e25483ac4fdd`.
- Root lịch sử:
  `C:/Users/bao.nguyen/Documents/ChatBotProject/.local/worktrees/crag-27e4809/.local/crag-v3-window-02`.
- Source được audit offline: `38620eb02806278fb689446c34f2d99e1f6e0746`.
- Worktree chuẩn bị:
  `C:/Users/bao.nguyen/Documents/ChatBotProject/.local/worktrees/query-post-pilot-prep-20260905`.
- `38620eb` là baseline audit, chưa phải final RC được owner chấp nhận cho CRAG.
  Nếu sửa các thiếu sót dưới đây, phải lấy commit/hash mới sau review; không
  giữ binding `38620eb` cho code đã đổi.

Hash SHA-256 đọc từ bytes thật:

| Artifact | Diagnostic lịch sử | Baseline offline `38620eb` |
| --- | --- | --- |
| `data/crag_eval_v1/eval_manifest.jsonl` | `beac3aac28b59ac57930b2c7099997efa7bdfda2a76bf65e3f1620d4b0fb897b` | giống lịch sử, 9 case |
| `scripts/crag_eval/run_diagnostic.py` | `a39f7685871b4ada587c367f5539582ebaf666a88bedc103660c4a16514a7d4c` | giống lịch sử |
| `scripts/crag_eval/run_rollout.py` | `62b333ae70e34b13b45c95049fad096327b457b779de4e928a7ffe45d3086955` | `15fe131bd84127f8e5daf303b12a7ac8edd257b10e4e1f42ce80f24d6456b84d` |
| `scripts/crag_eval/diagnostic_aggregation.py` | `5dde2d73cc46ed677466d4ed5e92b97cc1349ef1c2df1b8f23708c4fbf5172ec` | giống lịch sử |

Artifact lịch sử được bảo toàn:

- `preflight.json`: `5f58cffe22c59fc63b3fb4ee08ea91886f7540d8405084245d8066231af1588f`.
- `provider-smoke.json`: `e82534ea334f53b9d18f7873cb6d7f82162d55d4314b7120abc38850a73ef64d`.
- `diagnostic/declaration.json`: `58c0954fb9f1267b486c441ddf648edbe97515cbe6d9a33852f35c5c6df9332c`.
- `diagnostic/outcome.json`: `e3673c57012a343b3ff6b50cff0bc5a5d16cebdd8ed832692d8f7edcf04ffcf4`.

`query-crag-offline-preparation.json` trên baseline audit có hash
`cf2204c5309df5facdc470d416b6569b1ca6a15baacb048d811efa16cdf570e6`.
Packet này vẫn `predeclared_unexecuted`, được chuẩn bị từ Math commit
`67265a0bd6135f9f205521e99bd51870a955b014`; phần lịch sử Query đã cũ.
Đây là tài liệu nguồn để đối chiếu các invariant, không phải quyền chạy CRAG.

## Diagnostic cũ thực sự nói gì

Preflight lịch sử pass 9/9 trên collection `MechChatbot_CRAG_Eval_v1`, fingerprint
`9592deb0e747ac9a14d42bf3fe14471baa3b145ad752bdd53fdc491dde3dea7f`.
Smoke ngày 11/08/2026 pass 5/5, zero retry, P95 khoảng 5187 ms. Provider config
hash tại thời điểm đó là
`9d978ec3fb533f7316eb98928ec0f3cbbde9f6e52b33ae3b45aff15d1d61416f`.

Diagnostic sau smoke vẫn kết thúc `inconclusive`: 3 case pair, 8 arm run,
0 series hoàn tất, 2 provider error event và 1 retry (event count của cùng
retry episode, không phải hai sự cố độc lập); dừng tại
`series-01 / crag-version-citation`. Không có latency/cost ratio toàn series
để đánh giá, `diagnostic_target_met=false`, `formal_evidence=false` và tất cả
quyền formal/pilot/enablement/default đều false. Smoke xanh không ngăn provider
lỗi trong các arm sau đó. Không suy ra code mới đã chữa lỗi provider từ test
offline hoặc từ việc Query chạy được.

## Đã có, cần tái sử dụng

- Diagnostic v3 có mirrored case-paired interleaving, hai series đảo arm order,
  private trace riêng cho từng case/arm và dừng khi input binding drift hoặc
  provider error/retry. Runner/aggregation này chưa đổi từ diagnostic lịch sử.
- Rollout harness mới kiểm source sạch, commit ổn định, manifest/trace identity
  đầy đủ, không trùng case, appended trace đúng scope, gate exit khớp artifact;
  rollback evidence phải bind cùng commit. Dùng các helper hiện có, không xây
  harness mới.
- `scripts/crag_eval/preflight.py`, `verify_rollback.py`,
  `scripts/eval/crag_rollout_gate.py`, `crag_demo_authorization.py`,
  `crag_pilot_preflight.py`, `crag_pilot_gate.py` và runbook CRAG đã có. Chỉ dùng
  theo thứ tự gate khi có quyền tương ứng; hiện chưa cần tạo pilot tooling.
- `scripts/eval/run_eval.py` và composition đã nhận
  `--maximum-provider-retries`; đây là seam có sẵn để cưỡng chế retry budget.

## Thay đổi Query ảnh hưởng đến CRAG

1. Query branch batch và parent batch chỉ dành cho retrieval thực sự
   `decomposed_*`. CRAG-only không tự nhận lợi ích batch đó. Baseline/candidate
   CRAG phải giữ Query OFF để đo đúng CRAG + Claim Repair.
2. Deadline chung nay truyền xuống dense/sparse retrieval, restricted-access
   probe, parent hydration, corrective metadata scroll và provider generation.
   Timeout/RequestDeadlineExceeded phải được propagate; không dùng fallback để
   biến lỗi deadline thành thành công. Cần đo lại latency trên source mới.
3. `corrective.py:load_metadata_corrected_documents` thay timeout scroll cố định
   3 giây bằng timeout Qdrant được cấu hình (default 10 giây), giới hạn bởi
   remaining request deadline. Đây là thay đổi thực về time budget, không được
   tuyên bố toàn bộ timeout đều chỉ giảm so với CRAG lịch sử.
4. Retry callback hiện tiêu thụ request budget trước lần retry và có telemetry;
   generation dùng timeout còn lại. Cơ chế này chỉ bảo đảm zero retry nếu
   runner thực sự bind budget bằng 0.

## Thiếu trước khi freeze RC CRAG

| Thiếu trên baseline `38620eb` | Bằng chứng và việc offline cần hoàn tất |
| --- | --- |
| Exact feature isolation | `build_evaluation_environment()` trong `run_rollout.py` kế thừa `os.environ`, chỉ gán CRAG/Claim Repair. Probe với 5 flag không liên quan đặt true cho thấy Math, Query, Graph, Community và Late Interaction vẫn true ở baseline. Thêm regression môi trường bị nhiễm và ép toàn bộ governed flags theo exact arm contract. |
| Zero retry trước dispatch | `_invoke_evaluation()` không truyền `--maximum-provider-retries`; môi trường không ép `RAG_PROVIDER_RETRY_LIMIT=0`, trong khi settings mặc định là 2. Artifact guard có thể bác retry sau khi đã xảy ra. Bind 0 qua seam có sẵn, test fake transient provider để chứng minh một attempt và không fallback/repair retry ngoài contract. |
| Fresh measured evidence | Chưa có preflight/rollback/provider binding/smoke/declaration/series cho RC CRAG tương lai. Không điền bằng hash lịch sử, không coi 94 unit tests dưới đây là measured quality/latency. |
| Scope và budget cửa sổ kế tiếp | Chọn rõ supporting diagnostic hay formal window trong draft mới; không dùng diagnostic declaration làm formal authorization. Review lại tương tác với campaign Query đang có và resource isolation trước bất kỳ egress nào. |

## Kiểm chứng offline trong lượt này

Ngày 05/09/2026, trên source `38620eb`, bốn file hiện có pass
**94 tests trong 13.81 giây**:

- `tests/unit/test_crag_diagnostic.py`;
- `tests/unit/test_crag_eval_harness.py`;
- `tests/unit/test_corrective_retrieval.py`;
- `tests/unit/test_llm_retry_telemetry.py`.

Chạy bằng Python của `ChatBotProject/chat_env`, `PYTHONDONTWRITEBYTECODE=1`,
`RUN_DB_TESTS=0`, `RUN_QDRANT_TESTS=0`, `RUN_EVAL_TESTS=0`,
`RAG_EXECUTION_CONTEXT=test`, `-p no:cacheprovider`; basetemp là
`C:/Users/bao.nguyen/AppData/Local/Temp/crag-offline-readiness-20260905-tests`.
Các test dùng fake/mocked dependencies. Không chạy integration/live eval hay
full unit suite. Test hiện tại xanh chưa bao phủ hai thiếu sót ở trên; probe
feature isolation riêng đã tái tạo lỗi mà không kết nối dịch vụ.

## Checklist cho một window mới, sau công việc offline

1. Hai regression/fix đã triển khai offline; hoàn tất review và validation RC;
   freeze clean RC ở worktree riêng. Tính mới manifest/runner/aggregation hashes,
   giữ đầy đủ 9 case và các policy/RBAC/lifecycle filters.
2. Kiểm tra campaign Math/Query có còn phát traffic hay giữ cổng/resource nào;
   hoàn tất các dependency gate có hiệu lực. Không tự dừng/chạy lại campaign để
   tạo chỗ cho CRAG. Refresh release ledger/bundle thực tế thay vì suy từ prose cũ.
3. Chuẩn bị draft scope cụ thể, never-used absolute root, exact RC/interpreter,
   collection, provider configuration, concurrency 1, zero retry, điều kiện
   dừng. Baseline all-off; candidate chỉ CRAG + Claim Repair. Mọi flag khác OFF.
4. Owner chấp nhận draft/bindings và giới hạn traffic trước provider/fixture
   traffic. Sau đó thu preflight và rollback đúng RC, chạy fresh smoke đúng
   5 request, 1 attempt/request, zero retry; mọi failure làm dừng window.
5. Bind fresh evidence vào declaration mới. CRAG revalidate smoke age tối đa
   30 phút trước mỗi arm; hết hạn thì dừng, không refresh smoke trong window.
   Không resume/rerun/carry-forward khi arm hoặc gate đã terminal.
6. Giữ hard gates: latency ratio tối đa 1.25, cost 1.5, correction/repair tối đa
   một, zero provider errors/retries, zero leakage, wrong-answer không tăng.
   Supporting diagnostic còn phải giữ spread latency/cost tối đa 0.1 và đủ
   mirrored series; không thay các gate sau khi thấy kết quả.
7. Kết luận từ artifact thật. Technical pass chỉ mở bước review/decision theo
   scope đã duyệt; pilot, controlled demo và default rollout có gate riêng.

Các field execution tương lai hiện vẫn **chưa có giá trị**:
`execution_source_commit`, `run_root`, `fixture_preflight_sha256`,
`provider_configuration_sha256`, `fresh_provider_smoke_sha256`,
`rollback_evidence_sha256`, `owner_declaration_sha256`,
`owner_approval_reference`, `authorized_at`, `expires_at`.
`provider_traffic_authorized`, `formal_window_authorized`, `pilot_authorized`,
`feature_enablement_authorized`, `default_rollout_authorized` đều **false** đối
với gói chuẩn bị này. Không serialize checklist này thành approval giả.

## Đối chiếu lại binding của packet lịch sử

Lỗi full-suite baseline nêu ở checkpoint được xác định là assertion dùng sai
ngữ nghĩa: packet `predeclared_unexecuted` giữ runner Query lịch sử `907ab3...`,
không phải cam kết runner đó vẫn trùng mọi checkout mới. Bản chuẩn bị thêm
`query_decomposition.binding_readiness` với `historical_stale`,
`current_execution_ready=false`, `fresh_binding_required=true`; giữ nguyên
hash runner lịch sử, prepared identity và tất cả authorization false.
Vì có annotation mới, hash toàn packet sau sửa không còn là hash baseline
`cf2204...` được ghi ở phần audit phía trên.

Regression mới yêu cầu annotation stale rõ ràng và đưa actual current runner
cùng historical hash vào fixture entrypoint: vẫn phải fail với
`query_window_preparation_binding_drift` trước khi tạo run root. Không sửa
runtime validator hay tự bind packet vào source hiện tại. Readiness cho một
window tương lai vẫn cần fresh bindings, review và owner authorization đúng
scope trước traffic; annotation không phải approval hoặc bằng chứng thực thi.

TDD: assertion annotation mới RED với `KeyError: binding_readiness` trước sửa.
Sau annotation, toàn bộ `test_query_crag_offline_preparation.py` pass 36/36
(exit 0, parent kiểm lại độc lập trong 135.13 giây), gồm regression runtime
historical hash và các binding drift guard.
`git diff --check` cho các file thay đổi pass; chưa chạy lại full unit suite
sau bản đối chiếu này. Đây không phải fresh execution binding, provider smoke
hoặc quyền mở window.
