# CRAG: readiness offline ngày 05/09/2026

## Checkpoint offline 09/09/2026: source mới, chưa có CRAG window mới

Static review trong worktree task riêng tại source
`9f9776148f08545a0ad29cfd1a8d4e8bb1c4759b` xác nhận các sửa chữa isolation,
zero retry và stop-on-failure đã có trong source. Không còn coi hai lỗi trên
baseline `38620eb` bên dưới là việc implementation chưa làm. Lượt này không
chạy test/import, audit dependencies hoặc truy cập runtime; những số kiểm chứng
sau là kết quả đã ghi nhận trước đó, không phải test chạy lại ngày 09/09.
Isolated venv đã ghi nhận **3562 pass / 2 skip**, audit **0 known vulnerabilities**;
shared `chat_env` vẫn có advisory/disposition riêng ở checkpoint 08/09, không
được đổi thành security-green nhờ kết quả của venv khác.

| Invariant đã có tại `9f97761` | Source và regression để đối chiếu |
| --- | --- |
| Exact arm isolation, không mutate process cha | `scripts/crag_eval/run_rollout.py:162` merge provider map trước overrides; baseline all-off, candidate chỉ CRAG + Claim Repair; `tests/unit/test_crag_arm_isolation.py:13` có 8 tổ hợp host/provider nhiễm flag và hai router mode. |
| Zero retry trước dispatch và dừng case tiếp theo | `run_rollout.py:301` truyền `--maximum-provider-retries 0` và `--stop-on-provider-failure`; `test_crag_eval_harness.py:1138` assert invocation, `:645` assert chỉ case đầu được gọi khi provider fail, `:687` kiểm source timeout. |
| Source và artifact binding | `run_rollout.py:141`, `:154`, `:429` kiểm source sạch, commit/manifest không drift sau arm; regression `test_crag_eval_harness.py:1604`, `:1619`, `:1631` bao phủ dirty source, commit drift và rollback cùng commit; `:1534` kiểm smoke age trước mỗi arm. |
| Inconclusive không thành acceptance | `scripts/crag_eval/diagnostic_aggregation.py:192`, `:520` yêu cầu series đầy đủ, không failure/retry, giữ gate và authorization false; `tests/unit/test_crag_diagnostic.py:345`, `:606`, `:628`, `:659`, `:687` kiểm stop, failure/retry và provider variance. |

Không tìm thấy bounded implementation gap mới trong phạm vi static review này;
không sửa runner/aggregator hoặc thêm harness trùng lặp. Đây không phải chứng
minh toàn bộ CRAG không còn lỗi. Khoảng thiếu còn thực là **fresh measured
evidence và owner authorization cho CRAG trên final RC**. Diagnostic lịch sử
vẫn **3/9 case pair, 0 series hoàn tất, inconclusive**; giữ nguyên mọi hashes và
tombstones bên dưới. Query pilot/smoke không phải CRAG evidence; chưa đủ dữ liệu
để tối ưu code/performance từ provider variance.

### Draft diagnostic/recovery prerequisites, không phải quyền chạy

1. Chờ kết thúc cạnh tranh tài nguyên với Query; kiểm tra final RC và interpreter
   thực sự được chọn sau hậu-pilot. Không dùng authorization Math cũ hoặc Query
   làm CRAG authorization. Graph giữ `keep_off_technical_limit`; Community/Late
   OFF; CRAG baseline/candidate giữ exact isolation như bảng trên.
2. Chốt scope supporting diagnostic hoặc formal trong draft riêng: never-used
   absolute root, source/manifest/runner/aggregation hashes mới, provider config,
   fixture collection và fingerprint, concurrency 1, zero retry, budget/expiry,
   stop conditions và rollback binding cùng RC. Các trường execution cuối tài
   liệu vẫn chưa điền; approval/traffic/activation đều false.
3. Owner phải duyệt exact scope/bindings trước preflight có traffic hoặc smoke.
   Khi được duyệt mới thu preflight/rollback và fresh CRAG smoke 5 request,
   một attempt/request; bind declaration mới, kiểm smoke age 30 phút mỗi arm.
   Recovery phải được xác nhận cho CRAG; không refresh/resume/retry window đã
   terminal, không dùng artifact hoặc root cũ để tiếp tục.
4. Giữ nguyên mirrored series và hard gates đã ghi bên dưới. Failure/retry,
   binding drift hoặc expiry làm dừng; ghi tombstone, giữ evidence và chuẩn bị
   draft mới. Không biến diagnostic pass thành formal/pilot/default approval.

Smallest focused regression set đề nghị sau pilot, **chưa chạy trong lượt này**:

```powershell
# Dùng interpreter đã được chọn cho final RC, tại worktree offline riêng.
# Giữ RUN_DB_TESTS/RUN_QDRANT_TESTS/RUN_EVAL_TESTS=0 và RAG_EXECUTION_CONTEXT=test.
& $FinalRcPython -m pytest -q -p no:cacheprovider tests/unit/test_crag_arm_isolation.py tests/unit/test_crag_eval_harness.py tests/unit/test_crag_diagnostic.py -k 'exact_feature_set or arm_binds_trace_log_file or stops_before_next_case or stops_after_qdrant_source_timeout or dirty_worktree or source_commit_drift or commit_pinned_rollback or stale_provider_smoke or stops_and_counts_retry or inconclusive_and_fail_closed or arm_order_changes_result or material_effect_size_variance'
```

Lệnh này là offline regression template, không gọi diagnostic/rollout CLI thực.
Full suite/coverage, model imports và dependency audit không nằm trong lượt này.

## Candidate interpreter 08/09: audit sạch, compatibility đang kiểm

Venv riêng `.local/live-readiness-20260908/venv` (Python 3.12.3,
include-system-site-packages=false) đã cài thành công và `pip check` pass.
Fresh `pip_audit --local` exit 0, không có vulnerability đã biết. So với 279
package của shared inventory, chỉ đổi GitPython 3.1.58→3.1.59, pip 26.1.2→26.2,
pypdf 6.15.0→6.16.1, tornado 6.5.7→6.5.8, unstructured 0.22.32→0.24.0.
Report cuối và hashes ở `.local/live-readiness-20260908/dependency-compatibility-verified.json`;
`dependency-remediation.json` là snapshot trung gian trước full unit.
Full unit trên interpreter mới đã đạt 3562 pass, 2 skip, 1 warning (607,48 giây,
exit 0); 12/12 isolated imports pass với network audit guard. Candidate pins
được lưu trong requirements-security.txt; exact 279-package inventory vẫn ở
gói local để không gọi lock lịch sử là runtime hiện tại. Chưa chạy runtime live, chưa
freeze/bind lại. Shared chat_env giữ nguyên và vẫn có disposition bên dưới.
Audit sạch là kết quả scanner, không thay compatibility, owner approval hoặc
fresh preflight/smoke; không tự xác nhận upstream advisory discrepancy đã giải quyết.

## Checkpoint dependency hiện hành ngày 08/09/2026

Đánh giá này áp dụng cho interpreter dùng chung của Query và CRAG:
`C:/Users/bao.nguyen/Documents/ChatBotProject/chat_env/Scripts/python.exe`.
Chạy lại `-m pip check` trả exit 0 (`No broken requirements found`);
`-m pip_audit --local --progress-spinner off --format json` trả exit 1,
**12 advisory ở 5 package**. Inventory được đối chiếu thêm bằng `pip show`.
Không cài đặt, nâng cấp hoặc thay đổi môi trường dùng chung.

| Package cài hiện tại | Advisory hiện hành | Fix candidate từ scanner |
| --- | --- | --- |
| GitPython 3.1.58 | PYSEC-2026-3785 / CVE-2026-78675 / GHSA-7833-fr7j-v32q; PYSEC-2026-3786 / CVE-2026-78676 / GHSA-284h-m62q-gf8w; PYSEC-2026-3787 / CVE-2026-78677 / GHSA-8mcc-hrx5-hvxc; PYSEC-2026-3788 / CVE-2026-78678 / GHSA-5xxx-qhh7-9287 | 3.1.59 |
| pip 26.1.2 | PYSEC-2026-3721 / CVE-2026-13346 / GHSA-qwm4-qh6w-59xr | 26.2 |
| pypdf 6.15.0 | CVE-2026-84309 / GHSA-jp53-mhqp-8xcg; CVE-2026-84310 / GHSA-23w6-3w8w-8484; CVE-2026-84311 / GHSA-763m-79hh-57f2 | 6.16.1 cho cả ba |
| tornado 6.5.7 | GHSA-wwv5-g3v4-889x; GHSA-8423-8fgw-73vq; CVE-2026-82397 / GHSA-mpf4-983q-p7j4 | 6.5.8, cần xác minh discrepancy bên dưới |
| unstructured 0.22.32 | CVE-2026-71428 / GHSA-4mvj-m6j5-pmf7 | 0.24.0 |

### Reachability và disposition

- GitPython và Tornado được `pip show` ghi là dependency của Streamlit.
  Không tìm thấy import/call trực tiếp các thư viện này trong Python source
  của repo; server hiện dùng Uvicorn/FastAPI, còn auth Streamlit là removed
  compatibility shim. Chưa thấy đường ứng dụng đến GitPython config/clone/
  blame/submodule hoặc Tornado cookie/form parser bị ảnh hưởng.
  [GitPython upstream](https://github.com/gitpython-developers/GitPython/security/advisories/GHSA-7833-fr7j-v32q),
  [Tornado cookie](https://github.com/tornadoweb/tornado/security/advisories/GHSA-wwv5-g3v4-889x),
  [multipart](https://github.com/tornadoweb/tornado/security/advisories/GHSA-8423-8fgw-73vq),
  [urlencoded](https://github.com/tornadoweb/tornado/security/advisories/GHSA-mpf4-983q-p7j4).
- pypdf được cài cho unstructured-client; unstructured không có `Required-by`.
  Không tìm thấy import pypdf, Unstructured loader hoặc URL partition trong
  source; ingestion PDF hiện gọi `fitz`/`pdfplumber`. Chưa thấy đường trực tiếp
  đến pypdf outline/text/writer hoặc unstructured URL SSRF. Đây là static
  assessment, chưa chứng minh mọi dynamic/optional dependency đều unreachable.
  [pypdf upstream](https://github.com/py-pdf/pypdf/security/advisories/GHSA-763m-79hh-57f2),
  [unstructured upstream](https://github.com/Unstructured-IO/unstructured/security/advisories/GHSA-4mvj-m6j5-pmf7).
- pip: scanner mô tả path traversal khi dùng malicious package index;
  không tìm thấy runtime package installation trong source được kiểm.
  Không tải/cài từ untrusted index. URL
  [pip advisory](https://github.com/pypa/pip/security/advisories/GHSA-qwm4-qh6w-59xr)
  chưa fetch được; ID/description/fix candidate hiện dựa trên scanner,
  không claim đã xác minh riêng upstream.
- Upstream Tornado cookie advisory đang ghi `Patched versions: None`, trong
  khi scanner đề xuất 6.5.8. Cần đối chiếu release/source và re-audit; chưa
  xem candidate này là remediation đã chứng minh.

Disposition của **cả 12 advisory**: `assessed_open`,
`no_direct_application_sink_found`; **security-green = false**. Không phải
`not_affected` hoặc owner risk acceptance. Có thể lưu kết luận này trong
local source freeze không phát hành; dependency gate vẫn chặn tuyên bố
release-ready/security-clean và không cấp quyền launch Query/CRAG.

### Điều kiện kiểm compatibility tách biệt

1. Tạo venv riêng cùng Python version, không dùng `--system-site-packages`;
   không đổi `chat_env`. Đối soát intended runtime với inventory trước:
   lock hiện ghi cryptography 49.0.0, shared runtime là 50.0.0, nên lock
   không được coi là bản sao môi trường đang kiểm.
2. Kiểm fix candidate trên môi trường riêng. Nếu chọn bỏ dependency
   Streamlit/unstructured không dùng, cần review riêng phạm vi runtime;
   không xóa package dùng chung trong lượt này.
3. Chạy `pip check`, fresh audit, offline imports, PDF ingestion local
   fixtures, API/auth regression và full unit/matrix coverage. Đối chiếu
   upstream cho Tornado và pip trước khi tuyên bố khắc phục đủ 12 advisory.
4. Chỉ đóng gate khi inventory mới, compatibility và advisory disposition
   được review; nếu còn advisory thì giữ gate mở và security-green false.
   Mọi interpreter/package thay đổi cần binding mới và preflight mới cho
   future window được owner duyệt; không tái sử dụng binding lịch sử.

Các checkpoint, số test và câu `chưa commit/freeze` dưới đây là **lịch sử
ngày 05/09**, không xác định trạng thái freeze hiện hành. Final commit/binding
phải đọc từ gói freeze mới; không sửa source/run/hash lịch sử.

## Checkpoint bản sửa offline (lịch sử 05/09)

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
