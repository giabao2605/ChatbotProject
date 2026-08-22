# Query Decomposition và CRAG: chuẩn bị offline cho window kế tiếp

## Trạng thái

Tài liệu này chỉ chuẩn bị trước quy trình. Nó không authorize provider traffic,
formal window, pilot, controlled-demo activation hoặc default rollout.

Predeclaration nguồn là
`data/integrated_hardening_v1/evidence/query-crag-offline-preparation.json`.
Artifact có trạng thái `predeclared_unexecuted`: manifest, runner, exact feature
flags, threshold và Math-only release hashes đã được bind. Final RC chưa freeze
nên execution `source_commit` cùng mọi evidence phải recapture vẫn là `null`.
Không sửa artifact này tại chỗ thành authorization. Khi đủ điều kiện chạy,
derive một execution declaration mới, giữ nguyên binding tĩnh, điền binding
động và lấy owner signature riêng. Nếu binding tĩnh đổi, tạo predeclaration mới
và review lại.

Trong khi Grounded Math campaign `19aacefbe67b1aa3907a490c` chưa terminal:

- không gọi provider smoke;
- không chạy baseline/candidate hoặc diagnostic;
- không sửa `.env`, runtime Math, Scheduled Task, manifest, WAL hoặc trace Math;
- không reuse request, trace, elapsed time, pair hoặc gate lịch sử.

## Những gì đã chuẩn bị được ngay

- Query-only manifest: `data/decomposition_eval_v1/eval_manifest.jsonl`, 13 case,
  SHA-256 `6976cbbe4c9500b7c0755c5944775e326106a780bb2910bfa71167787a1d0bf8`.
- CRAG manifest: `data/crag_eval_v1/eval_manifest.jsonl`, 9 case, SHA-256
  `beac3aac28b59ac57930b2c7099997efa7bdfda2a76bf65e3f1620d4b0fb897b`.
- Exact baseline/candidate feature flags, thresholds, reviewer placeholders,
  stop conditions và fresh-binding checklist nằm trong packet JSON.
- Test offline xác nhận packet vẫn fail-closed:

```powershell
& 'C:\Users\bao.nguyen\Documents\ChatBotProject\chat_env\Scripts\python.exe' `
  -m pytest tests\unit\test_query_crag_offline_preparation.py -q
```

Lệnh trên không gọi provider và không cần token.

## Gate chung trước mọi lần chạy tương lai

Chỉ tiếp tục khi tất cả điều kiện sau đúng:

1. Math campaign đã terminal; Scheduled Task không còn phát traffic.
2. Có final RC trong clean worktree riêng; HEAD không đổi trong toàn window.
3. Manifest hash khớp declaration và output/trace path hoàn toàn mới.
4. Fixture preflight và rollback evidence pass trên cùng source commit.
5. Provider configuration được hash-bind; fresh smoke đạt `5/5`, zero retry
   và cùng provider configuration. Query yêu cầu smoke không quá 30 phút lúc
   baseline bắt đầu; CRAG revalidate giới hạn 30 phút trước mỗi arm.
6. Signed Math-only ledger/bundle hiện hành vẫn chỉ accept Grounded Math và
   giữ Query, CRAG cùng Claim Repair OFF.
7. Owner declaration mới bind source, manifest, fixture, provider, smoke,
   rollback, governance, runner và release-decision hash.
8. Không có credential, raw prompt, raw answer hoặc private trace ID trong
   declaration/evidence công khai.

Nếu thiếu một binding, dừng trước provider traffic. Không điền hash giả, không
dùng artifact lịch sử và không nới threshold sau khi xem kết quả.

### Guard cơ học trước provider smoke

Chạy định nghĩa guard dưới đây trong cùng PowerShell process. Nó chỉ đọc
artifact/Task Scheduler và không gọi provider:

```powershell
function Assert-MathCampaignTerminal {
    param(
        [Parameter(Mandatory = $true)][string]$CampaignRoot,
        [Parameter(Mandatory = $true)][string]$TaskName
    )

    $public = Get-Content -Raw -LiteralPath "$CampaignRoot\campaign-public.json" |
        ConvertFrom-Json
    if ($public.campaign_id -ne '19aacefbe67b1aa3907a490c') {
        throw 'unexpected_math_campaign_id'
    }

    $wal = Get-Content -LiteralPath "$CampaignRoot\campaign.wal.jsonl" |
        ForEach-Object { $_ | ConvertFrom-Json }
    if (@($wal | Where-Object event -eq 'attempt_completed').Count -ne 100) {
        throw 'math_campaign_not_100_completed'
    }

    $baseGate = Get-Content -Raw -LiteralPath "$CampaignRoot\base-gate.json" |
        ConvertFrom-Json
    $operatorGate = Get-Content -Raw -LiteralPath "$CampaignRoot\operator-gate.json" |
        ConvertFrom-Json
    if ($baseGate.passed -ne $true -or $operatorGate.passed -ne $true) {
        throw 'math_campaign_final_gates_not_passed'
    }
    if (!(Test-Path -LiteralPath "$CampaignRoot\stop.marker")) {
        throw 'math_campaign_completion_marker_missing'
    }

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    if ([string]$task.State -ne 'Disabled') {
        throw 'math_campaign_task_not_disabled'
    }
}

function Assert-MathDefaultRollout {
    param([Parameter(Mandatory = $true)][string]$MathReleaseRoot)

    $ledgerPath = Join-Path $MathReleaseRoot 'release-decisions.json'
    $bundlePath = Join-Path $MathReleaseRoot 'activation-bundle.json'
    $expectedLedgerSha = '0e41b33f87b0f82be66453f105bd956380cfd67c89927aa9914539dfda971208'
    $expectedBundleSha = 'd2b146bb36ba66e3ec6319391fccf3228776f34287b18a0ff490befd588ba660'
    $sourceCommit = '67265a0bd6135f9f205521e99bd51870a955b014'
    if (
        (Get-FileHash -Algorithm SHA256 -LiteralPath $ledgerPath).Hash.ToLowerInvariant() -ne
            $expectedLedgerSha -or
        (Get-FileHash -Algorithm SHA256 -LiteralPath $bundlePath).Hash.ToLowerInvariant() -ne
            $expectedBundleSha
    ) {
        throw 'math_default_rollout_binding_drift'
    }

    $ledger = Get-Content -Raw -LiteralPath $ledgerPath | ConvertFrom-Json
    $bundle = Get-Content -Raw -LiteralPath $bundlePath | ConvertFrom-Json
    if (
        $ledger.status -ne 'complete' -or
        $ledger.source_commit -ne $sourceCommit -or
        $ledger.decisions.RAG_GROUNDED_MATH_ENABLED.decision -ne 'accepted' -or
        $ledger.decisions.RAG_QUERY_DECOMPOSITION_ENABLED.decision -ne 'rejected' -or
        $ledger.decisions.RAG_CRAG_ENABLED.decision -ne 'rejected' -or
        $ledger.decisions.RAG_CLAIM_REPAIR_ENABLED.decision -ne 'rejected'
    ) {
        throw 'math_default_rollout_ledger_invalid'
    }
    if (
        $bundle.scope -ne 'default_rollout' -or
        $bundle.source_commit -ne $sourceCommit -or
        $bundle.activation_profile -ne 'selective' -or
        $bundle.feature_flags.RAG_GROUNDED_MATH_ENABLED -ne $true -or
        $bundle.feature_flags.RAG_QUERY_DECOMPOSITION_ENABLED -ne $false -or
        $bundle.feature_flags.RAG_CRAG_ENABLED -ne $false -or
        $bundle.feature_flags.RAG_CLAIM_REPAIR_ENABLED -ne $false
    ) {
        throw 'math_default_rollout_bundle_invalid'
    }
}
```

Final `base-gate.json` là runtime-identity reconciliation cho Math window.
Không thay guard này bằng kiểm tra port/PID đơn thuần.

## Query Decomposition

### Scope đã khóa

- Baseline: mọi governed RAG feature OFF.
- Candidate: chỉ `RAG_QUERY_DECOMPOSITION_ENABLED=true`.
- Grounded Math và ba interaction case không thuộc formal Query-only window.
- Ngưỡng: complex-answer gain tối thiểu `0.10`; latency/cost ratio tối đa
  `1.5`; branch accuracy và branch citation accuracy đều `1.0`; tối đa ba
  subquery, một correction, một final generation và zero terminal violation.

### Trình tự chỉ chạy sau khi Math terminal

Từ clean final-RC worktree:

```powershell
$env:PYTHONPATH = "$PWD;$PWD\src"
$env:RUN_DECOMPOSITION_EVAL_FIXTURE = '1'
$env:QDRANT_COLLECTION = 'MechChatbot_CRAG_Eval_v1'
$env:RAG_EVAL_EXPECTED_COLLECTION = 'MechChatbot_CRAG_Eval_v1'
$python = 'C:\Users\bao.nguyen\Documents\ChatBotProject\chat_env\Scripts\python.exe'
$runRoot = '<new-absolute-query-window-path>'
$expectedSourceCommit = '<exact-40-character-final-rc-commit>'
$ownerAuthorization = '<absolute-owner-authorization-json-path>'
$mathCampaignRoot = 'C:\Users\bao.nguyen\Documents\ChatBotProject\.local\worktrees\advanced-rag-post-burst-disposition\.local\math-pilot-7b9d575-operator-window-13-campaign'
$mathTaskName = 'ChatBotProject-GroundedMath-Operator-Window13'
$mathReleaseRoot = 'C:\Users\bao.nguyen\Documents\ChatBotProject\.local\worktrees\advanced-rag-post-burst-disposition\.local\grounded-math-interaction-matrix-20260818-02\release-candidate'

& .\scripts\ops\prepare_query_formal_window.ps1 `
  -RunRoot $runRoot `
  -ExpectedSourceCommit $expectedSourceCommit `
  -PythonPath $python `
  -OwnerAuthorizationPath $ownerAuthorization `
  -CampaignRoot $mathCampaignRoot `
  -TaskName $mathTaskName `
  -MathReleaseRoot $mathReleaseRoot
if ($LASTEXITCODE -ne 0) { throw 'Query offline readiness failed.' }
```

Chỉ chạy block trên sau khi owner đã approve exact draft/commit/fresh root và
authorization vẫn còn hiệu lực. Entry point tự kiểm schema, draft hash,
commit/root, expiry tối đa 60 phút và toàn bộ allow/deny scope bằng parse không
phụ thuộc locale; block offline không gọi provider và chỉ tạo `preflight.json` cùng
`rollback.json`. Trước block tiếp theo, cùng authorization phải vẫn còn hiệu
lực; không suy diễn standing authority hoặc sửa expiry.

```powershell
$env:EXTERNAL_PROCESSING_POLICY = 'all_external'

& .\scripts\ops\prepare_query_formal_window.ps1 `
  -RunRoot $runRoot `
  -ExpectedSourceCommit $expectedSourceCommit `
  -PythonPath $python `
  -OwnerAuthorizationPath $ownerAuthorization `
  -CampaignRoot $mathCampaignRoot `
  -TaskName $mathTaskName `
  -MathReleaseRoot $mathReleaseRoot `
  -RevalidateForProviderTraffic
if ($LASTEXITCODE -ne 0) { throw 'Query provider-boundary revalidation failed.' }

& $python -m scripts.eval.provider_smoke `
  --output "$runRoot\provider-smoke.json"
if ($LASTEXITCODE -ne 0) { throw 'Query provider smoke failed.' }

$smokeBinding = & .\scripts\ops\resolve_query_formal_smoke_binding.ps1 `
  -ProviderSmokeArtifact "$runRoot\provider-smoke.json"
$smokeBinding | Format-List
```

`prepare_query_formal_window.ps1` rejects missing/mismatched collection
bindings, invalid/expired owner authorization, an existing run-root,
source/worktree drift, manifest/runner hash drift, non-terminal Math campaign,
active Scheduled Task, signed Math release drift and failed offline
preflight/rollback. Authorization failure tại provider boundary ghi
`owner-authorization-failure.json`, làm root không thể revalidate lại sau khi
sửa expiry. A failed attempt leaves the run-root non-reusable. Do not delete or
repair it to continue the same window.
Before presenting any future authorization draft, recompute and compare both
the manifest and runner hashes stored in
`data/integrated_hardening_v1/evidence/query-crag-offline-preparation.json`
against the exact proposed commit. A stale preparation-packet binding is a
terminal first failure even when it is detected before the script creates the
run-root; do not refresh the packet and continue under the same authorization.
The `-RevalidateForProviderTraffic` call immediately before smoke rechecks all
bindings and reruns offline preflight/rollback into
`*-provider-boundary.json`; it refuses a root containing anything beyond the
two original offline artifacts. Any failure tombstones the root before
provider traffic.

Sau smoke, chỉ lấy `completed_at`, deadline baseline, provider hash và artifact
hash từ `resolve_query_formal_smoke_binding.ps1`. Helper xử lý trực tiếp giá trị
`[datetime]` do `ConvertFrom-Json` materialize và chỉ parse invariant round-trip
khi input còn là string; không stringify timestamp theo locale rồi gọi
`DateTimeOffset.Parse`. Bất kỳ failure nào của helper cũng terminal, không sửa
lệnh rồi tiếp tục cùng window. Derive execution declaration mới bằng các binding
đã resolve. Owner ký và baseline phải bắt đầu trong 30 phút từ smoke;
`run_rollout` revalidate freshness trước baseline. Sau đó chỉ dispatch qua
entrypoint bind interpreter và chạy:

```powershell
& .\scripts\ops\start_query_formal_pair.ps1 `
  -PythonPath $python `
  -Manifest data/decomposition_eval_v1/eval_manifest.jsonl `
  -OutputDir "$runRoot\formal-pair-01" `
  -Trace "$runRoot\rag-trace.jsonl" `
  -ProviderSmokeArtifact "$runRoot\provider-smoke.json" `
  -RollbackTestArtifact "$runRoot\rollback-provider-boundary.json"
```

`start_query_formal_pair.ps1` từ chối relative/missing interpreter, resolve và
probe exact absolute executable, kiểm tra input cùng fresh trace/output, rồi
mới atomically tạo zero-byte trace và gọi runner bằng chính executable đã
probe. Probe/input failure không tạo trace hoặc output. Bất kỳ failure nào sau
khi trace được tạo vẫn là first failure và làm window terminal; không sửa lệnh
rồi tiếp tục cùng root.

Dừng và tombstone toàn window khi provider failure/retry, binding drift, dirty
worktree, output reuse hoặc bất kỳ governed flag ngoài Query được bật. Không
chạy pair 02/03 sau một pair hỏng để tìm kết quả đẹp.

## CRAG + Claim Repair

### Scope đã khóa

- Baseline: mọi governed RAG feature OFF.
- Candidate: chỉ `RAG_CRAG_ENABLED=true` và
  `RAG_CLAIM_REPAIR_ENABLED=true`.
- Router mode `offline` cô lập CRAG khỏi router-model variance; generation vẫn
  là provider traffic nên vẫn phải chờ Math terminal và fresh smoke.
- Ngưỡng: latency ratio tối đa `1.25`, cost ratio tối đa `1.5`, tối đa một
  correction và một repair mỗi query, zero provider retry, zero leakage, mọi
  candidate case pass và wrong-answer không tăng.

### Trình tự chỉ chạy sau khi Math terminal

```powershell
$env:PYTHONPATH = "$PWD;$PWD\src"
$env:RUN_CRAG_EVAL_FIXTURE = '1'
$python = 'C:\Users\bao.nguyen\Documents\ChatBotProject\chat_env\Scripts\python.exe'
$runRoot = '<new-empty-crag-window>'
New-Item -ItemType Directory -Path $runRoot -ErrorAction Stop
$mathCampaignRoot = 'C:\Users\bao.nguyen\Documents\ChatBotProject\.local\worktrees\advanced-rag-post-burst-disposition\.local\math-pilot-7b9d575-operator-window-13-campaign'
$mathTaskName = 'ChatBotProject-GroundedMath-Operator-Window13'
$mathReleaseRoot = 'C:\Users\bao.nguyen\Documents\ChatBotProject\.local\worktrees\advanced-rag-post-burst-disposition\.local\grounded-math-interaction-matrix-20260818-02\release-candidate'

Assert-MathCampaignTerminal -CampaignRoot $mathCampaignRoot -TaskName $mathTaskName
Assert-MathDefaultRollout -MathReleaseRoot $mathReleaseRoot

& $python -m scripts.crag_eval.preflight `
  --manifest data/crag_eval_v1/eval_manifest.jsonl `
  --output "$runRoot\preflight.json"

& $python -m scripts.crag_eval.verify_rollback `
  --output "$runRoot\rollback.json"

$env:EXTERNAL_PROCESSING_POLICY = 'all_external'

& $python -m scripts.eval.provider_smoke `
  --output "$runRoot\provider-smoke.json"
```

Sau smoke, derive và ký execution declaration mới với mọi binding động;
baseline phải bắt đầu trong 30 phút và mỗi arm đều bị runner revalidate. Sau đó
mới chạy:

```powershell
New-Item -ItemType File -Path "$runRoot\rag-trace.jsonl" -ErrorAction Stop

& $python -m scripts.crag_eval.run_rollout `
  --manifest data/crag_eval_v1/eval_manifest.jsonl `
  --output-dir "$runRoot\formal-pair-01" `
  --trace "$runRoot\rag-trace.jsonl" `
  --provider-smoke-artifact "$runRoot\provider-smoke.json" `
  --rollback-test-artifact "$runRoot\rollback.json" `
  --router-mode offline `
  --arm-order baseline-first
```

Dừng và tombstone khi có provider failure/retry, correction error, binding
drift, output reuse hoặc feature flag ngoài CRAG + Claim Repair. Không resume
V3 `3/9`, không carry-forward pair và không rerun cùng design để chọn kết quả.

### Phân tích offline tombstone CRAG V3 window-02

Kết luận từ metadata đã đóng băng: trigger dừng là provider-side, còn evidence
không đủ để kết luận toàn bộ CRAG là provider-only hay đã đạt gate kỹ thuật.

- Preflight đã pass đủ `9/9`; provider smoke đã pass `5/5`, timeout 30 giây,
  zero retry.
- Diagnostic chạy tuần tự, hoàn thành 3 case pair và 8 arm. Ở
  `series-01/crag-version-citation`, baseline ghi một ProxyLLM `RuntimeError`
  rồi một `llm_retry`; vì vậy `provider_failure_count=2` là event count của
  cùng episode, không phải hai outage độc lập. `execution_failure_count=0`
  không cho thấy lỗi runner ở các arm đã hoàn thành.
- Outcome đúng là `inconclusive`, không có formal/feature/pilot/default
  authorization và yêu cầu window + declaration mới sau provider recovery.
- Vì mới dừng ở `3/9`, evidence không chứng minh được full quality,
  latency/cost hoặc loại trừ lỗi code-controlled ở các case chưa chạy. Không có
  căn cứ để sửa code CRAG hay retry diagnostic từ tombstone này.

## Checklist Query cho window kế tiếp

Checklist này chỉ chuẩn bị offline; không phải authorization:

- [ ] Clean final-RC worktree, HEAD đúng exact 40-character commit đã review.
- [ ] `QDRANT_COLLECTION` và `RAG_EVAL_EXPECTED_COLLECTION` cùng bằng
  `MechChatbot_CRAG_Eval_v1` trước khi tạo run-root.
- [ ] Run-root chưa từng tồn tại; không reuse trace, output, smoke, declaration
  hoặc pair từ bất kỳ window/smoke tombstone trước.
- [ ] Health smoke `5/5` gần nhất chỉ là consumed recovery signal; không copy
  artifact hoặc hash đó vào fresh formal run-root/declaration.
- [ ] Query packet, manifest 13 case và runner hash khớp byte-for-byte.
- [ ] Math campaign terminal `100/100`, stop marker tồn tại, base/operator gate
  pass, Scheduled Task `Disabled`.
- [ ] Signed Math release ledger/bundle khớp và chỉ Math đang ON.
- [ ] Offline Query preflight và rollback pass trên exact commit.
- [ ] Owner authorization envelope đã được điền và ký cho exact commit,
  run-root, provider configuration và giới hạn traffic.
- [ ] Fresh smoke đúng 5 request, một attempt/request, timeout 30 giây, zero
  retry; baseline bắt đầu trong 30 phút.
- [ ] Chỉ dispatch Query pair qua `start_query_formal_pair.ps1`; entrypoint phải
  resolve và probe exact absolute Python interpreter trước khi tạo zero-byte
  trace, không dùng relative executable path.
- [ ] Formal pair chạy tuần tự, tối đa 3 pair; dừng và tombstone ngay khi formal
  gate trả false (gồm latency/cost), runner không launch, có provider
  failure/retry, drift, duplicate/mismatched trace ID hoặc fallback ngoài
  deterministic local split contract. Local pre-run failure sau khi tạo trace
  vẫn là first failure; không sửa command rồi tiếp tục cùng window.

### Authorization envelope chưa ký

Mẫu dưới đây cố ý không phải declaration/evidence và không authorize traffic:

```text
status: NOT_AUTHORIZED_TEMPLATE
scope: query_decomposition_formal_window_only
source_commit: <exact-40-character-reviewed-commit>
run_root: <new-absolute-never-used-path>
manifest_sha256: <verified-query-13-case-manifest-sha256>
runner_sha256: <verified-query-rollout-runner-sha256>
provider_configuration_sha256: <verified-provider-configuration-sha256>
collection: MechChatbot_CRAG_Eval_v1
max_provider_smoke_requests: 5
max_attempts_per_smoke_request: 1
smoke_timeout_seconds: 30
max_formal_pairs: 3
concurrency: 1
authorization_excludes: pilot,feature_activation,default_rollout,push,merge
owner_authorization_id: <required>
owner_signature_or_approval_reference: <required>
authorized_at: <required>
expires_at: <required>
```

Nếu một placeholder còn trống, exact binding đã drift hoặc owner chưa ký thì
giữ `status: NOT_AUTHORIZED_TEMPLATE` và không tạo smoke/declaration.

### Recovery/cooldown gate sau các health smoke

Run-root `query-provider-smoke-58dbb08-20260820-141914` đã tombstone sau
offline/provider-boundary guards xanh nhưng smoke `0/5`, năm HTTP `502`, zero
retry. Health root `provider-health-smoke-d28be06-20260820T083329Z` sau đó
cũng tombstone ở `0/5` với năm HTTP `502`, zero retry. Health root
`provider-health-smoke-d28be06-20260821T004535Z` kế tiếp tombstone ở `4/5`
vì một `APITimeoutError`, zero retry.

Recovery signal mới nhất tại
`provider-health-smoke-d28be06-20260821T011357Z` đạt `5/5`, zero retry, P95
`3.13s`, provider configuration SHA-256
`9d978ec3fb533f7316eb98928ec0f3cbbde9f6e52b33ae3b45aff15d1d61416f`.
Provider-smoke artifact SHA-256 là
`01f4f2dd39502762ea0585518123a23e879f19c47f173f8b58affe78fd6b03a4`.
Disposition khóa lượt này thành `health_proof_only`, `formal_evidence=false`,
`reuse_authorized=false` và `consumed=true`.

Formal window `query-formal-6a566e1-20260821-01` sau đó tombstone tại fresh
smoke `0/5` với năm HTTP `502`, zero retry. Theo authorization mới chỉ để kiểm
recovery, independent root `provider-health-smoke-e14b02f-20260822T004512Z`
trên clean commit `e14b02ff24d7ebe16e05f938f50d79f47101f985` đạt `5/5`,
zero retry, một attempt/request, P50 `1.54s`, P95 `16.45s`. Provider
configuration SHA-256 vẫn là
`9d978ec3fb533f7316eb98928ec0f3cbbde9f6e52b33ae3b45aff15d1d61416f`;
artifact SHA-256 là
`b4a994186b51a85b710780ced854a8fd133b6914dafcb24e73dfe304119e9fe0`.
Disposition khóa kết quả thành `health_proof_only`, `consumed=true`, không
đánh giá Query quality và không cho reuse/carry-forward.

Không chạy thêm standalone health smoke sau signal này. Fresh Query formal
window chỉ được mở sau khi owner ký envelope bind exact clean commit và
run-root chưa từng tồn tại. Window đó phải chạy lại preparation,
provider-boundary revalidation và fresh formal-window smoke riêng; không copy
health artifact/hash ở trên vào declaration hoặc run-root mới.

### Formal window `d76ad9c` đã terminal

Window `query-formal-d76ad9c-20260821-01` bind exact commit
`d76ad9c64ab684f138f1c589e04b2e1f17707a2d`, pass offline/provider-boundary
preflight `13/13`, rollback `33/33` và fresh smoke `5/5`, zero retry. Pair 01
pass toàn bộ gate với latency P95 ratio `1.035668005`; kết quả này chỉ là một
technical pair, không authorize pilot/activation/default rollout.

Pre-run dispatch cho pair 02 sau đó fail vì PowerShell không resolve được
relative Python interpreter path. Python chưa khởi động, output chưa được tạo,
không có provider traffic và trace còn `0` byte; tuy nhiên contract
`stop_on_first_failure` không có ngoại lệ. Canonical terminal vì vậy là local
dispatch failure, authorization/window bị consumed và chỉ pair 01 là formal
evidence hợp lệ.

Corrected launch sau terminal là out-of-contract. Raw pair-02 output được giữ
nguyên chỉ làm non-formal diagnostic residue, không phải formal/rollout evidence
và không được reuse/carry-forward. Observation residue có latency P95 ratio
`1.511066968 > 1.5`, zero provider failure/retry; pair 03 không tồn tại.
Disposition SHA-256
`8c22265d301e37d7169ad8c28ad84a2b21bfdb5eaa00aac9004c8fbbc054c64d`
được bảo toàn; canonical adjudication SHA-256
`3313e5dc4e3dec91b3c31d12976aca99cc6545e074bd6b42ceac8e3c767ec08e`
sửa terminal reason. Không rerun smoke/pair, không nới threshold, không
catch-up/carry-forward/reuse artifact. Query tiếp tục OFF. Absolute interpreter
binding nay phải được enforce bởi `start_query_formal_pair.ps1`; formal attempt
tương lai vẫn cần owner authorization, never-used root, evidence, smoke,
declaration, trace và series hoàn toàn mới.

### Formal window `0eaddfa` đã terminal

Owner đã approve exact draft SHA-256
`4146fdb19e5d507a964c9d79f16fc2e3b06960345cc98897c45726b72c8895b9`
cho `query-formal-0eaddfa-20260821-01` trên clean commit
`0eaddfa3998ce4ee5cc2d17aa4c3b8e1e605bd90`. Exact Python, runtime snapshot,
tracked lock, manifest, runner và operator hashes đều khớp draft; root chưa
từng tồn tại tại authorization boundary.

Offline preparation đầu tiên fail với
`query_window_preparation_binding_drift` trước khi tạo preparation artifact.
Manifest binding vẫn khớp, nhưng tracked packet
`query-crag-offline-preparation.json` bind runner SHA-256 cũ
`5bfdbaa23c8dd7902dcc4042d2c5c371683c1fe79a4662018373460716418e4a` thay vì
exact approved runner SHA-256
`4d970dbf0d8ff3aea68a56c808ce70239f958e6cd1930781ea16fe350d71e5f0`.

Contract `stop_on_first_failure` làm authorization/window consumed và
tombstone tại offline pre-root validation. Root sau adjudication chỉ chứa
`window-disposition.json`; không có boundary, smoke, declaration, trace hoặc
formal-pair output. Provider traffic bằng `0`, provider health và Query quality
chưa được evaluate, Query tiếp tục OFF. Disposition SHA-256
`9a3a9ded829e95605e09267404c1c77cc3b9f55612483047c533ee549ecaeda3`.

Không refresh packet rồi chạy lại cùng authorization/root. Bước kế tiếp là
design delta + regression cho preparation binding trên commit mới; sau đó phải
có draft/owner authorization/never-used root/preparation/provider-boundary/
fresh smoke/declaration/trace/series hoàn toàn mới. Không reuse hoặc
carry-forward artifact từ `d76ad9c` hay `0eaddfa`.

### Formal window `d424628` đã terminal

Preparation-packet binding được sửa test-first trên exact clean commit
`d42462866b9d5905642bc518d34e29c18d7b7802`. Window mới
`query-formal-d424628-20260821-01` pass offline/provider-boundary preflight
`13/13`, rollback và fresh provider smoke `5/5`, zero retry, một
attempt/request. Declaration bind exact draft, authorization, source, manifest,
packet, runner, launcher, Python/runtime, provider configuration và Math release
hashes; không reuse evidence từ window trước.

Pair 01 pass toàn bộ gate với latency P95 `16748.04 → 11720.63` ms, ratio
`0.699820994`, cost ratio `1.249319684`; đây chỉ là một technical pair,
`production_eligible=false` và không authorize pilot/activation/default.

Pair 02 fail duy nhất `latency_within_budget`: latency P95
`8414.83 → 21364.61` ms, ratio `2.538923543 > 1.5`. Cost ratio
`1.211159875`, mọi non-latency gate pass, provider failure/retry và prohibited
trace event đều bằng `0`. Đây là terminal formal failure theo
`stop_on_first_failure`; pair 03 không được tạo. Disposition SHA-256
`b66c871b67e43b0f017472743fb455d99d8f5a90d18c9621a98090e924c22419`.

Authorization/window đã consumed và tombstone, Query tiếp tục OFF. Không rerun
pair 02/03, không chọn pair đẹp, không nới threshold và không reuse/carry-forward
smoke, declaration, trace hay output. Chẩn đoán latency chỉ dùng evidence bất
biến.

Offline exact-gate replay tái tạo byte-for-byte gate SHA-256
`363c7038b8682c07d568c51eda32312c3afa2b17ed859662e343a52c19705730`.
Minimized tail case có branch retrieval tuần tự `2354 + 2467 + 2532 ms`, parent
context `6089 ms` và generation `7736 ms`. Counterfactual lý tưởng chỉ thay tổng
branch bằng branch max vẫn cho P95 `16543.61 ms`, ratio
`1.966006443 > 1.5`; do đó hoàn tác serialization vừa không đủ đạt gate vừa mở
lại regression shared-client `ResponseHandlingException`. Bỏ parent hydration
hoặc cấp client riêng cho từng branch là architecture/evidence-semantics change,
không có public deterministic seam chứng minh an toàn trong scope này.

Diagnosis SHA-256
`8589ec36d4d3728755d79c8823715dbe2cad0df17b6367a25b8480c57bb8b25a`
đặt disposition `keep_off_technical_limit_current_design`. Không tạo speculative
code fix/test và Query giữ OFF. Future attempt chỉ được xem xét sau một design
scope riêng cho batched/isolated branch retrieval + parent context; sau đó vẫn
cần clean commit, authorization, never-used root, preparation, provider-boundary,
fresh smoke, declaration, trace và series hoàn toàn mới.

### Batched retrieval design delta `9e48ddc`

Owner đã mở riêng architecture scope sau disposition trên. Exact design commit
`9e48ddcf6e9533958c241ac1ae68e2fa31507070` gom tối đa ba branch thành một dense
batch và một sparse batch có thứ tự; strict miss hoặc BOM chỉ mở broad batch cho
những branch cần thiết. Không có call chồng lấn trên shared Qdrant client, không
tạo thêm client/credential copy và không đổi project-owned RRF, query embedding,
strict/broad/RBAC/lifecycle filters, base-k, retrieval mode hoặc direct/simple
retrieval contract.

Parent hydration chỉ batch khi request thực tế đã tạo retrieval mode
`decomposed_*`. Mỗi `QueryRequest(query=None)` bind exact parent key và lặp lại
site, department, security/clearance, published/approved/current, serving epoch
và publication version. Baseline và simple/direct request tiếp tục dùng path cũ.
Branch/parent batch failure là terminal; không serial/scroll fallback, retry,
replacement hoặc catch-up. Mỗi Qdrant batch nhận remaining request deadline và
không được tăng timeout cũ.

Offline validation: full unit `3015/3015`, compile, `pip-audit --local` và ba
review correctness/security/complexity đều pass; provider traffic bằng `0`.
Telemetry mới đánh dấu per-branch latency là `shared_batch` và ghi một
`retrieval_batch` aggregate. Đây chỉ là design/offline safety evidence, không
phải formal latency/quality evidence và không authorize Query traffic.

Planning target từ tombstone pair 02: baseline cũ cho phép candidate tối đa
`12622.245 ms`; branch-only counterfactual còn `16543.61 ms`. Nếu các stage khác
không đổi, parent tail cũ `6089 ms` phải giảm xuống khoảng `2167.635 ms` hoặc
thấp hơn. Không được dùng phép tính này thay measured gate, nới ratio `1.5` hoặc
chọn case đẹp.

Formal attempt kế tiếp phải bắt đầu từ exact clean tracked commit chứa design và
docs này, draft/owner authorization còn hiệu lực, never-used root, offline
preparation, provider-boundary revalidation, fresh `5/5` smoke, signed
declaration, empty trace và tối đa ba pair tuần tự mới. Không reuse bất kỳ
authorization, root, smoke, declaration, trace hoặc output của `d424628`.

### Formal window `6a566e1` đã terminal ở fresh smoke

Sau authorization-gate hardening `802aa90`, window
`query-formal-6a566e1-20260821-01` bind exact clean commit
`6a566e17729022ea060d3a61ef65986b6b8562da`, draft SHA-256
`721f34befa9011513c1c7d95069d4164523f3e1dfde8c2aa341ee075bcf15da8` và
authorization lifetime đúng 60 phút. Offline/provider-boundary preflight pass
`13/13`; rollback pass trên exact commit và xác nhận Query flag OFF.

Fresh formal-window smoke duy nhất fail `0/5`: cả năm request trả HTTP `502
InternalServerError`, zero retry, một attempt/request, timeout 30 giây. Smoke
SHA-256 `5d5cc9994790b8d03e0e9ed9eead6fb835592bb9f1fcd97269cbe37181f1ec66`;
provider configuration SHA-256 vẫn là
`9d978ec3fb533f7316eb98928ec0f3cbbde9f6e52b33ae3b45aff15d1d61416f`.
Đây là provider availability failure, không phải Query quality result.

Contract dừng ngay trước declaration/trace/formal pair. Disposition SHA-256
`ba3b3e9a4129bbcd8385619240a1eae6f46c6e967bffbcffcfc38eadf5de1f9b` khóa
authorization/window ở consumed+tombstoned; Query tiếp tục OFF. Không rerun
smoke, không reuse preparation/boundary artifact và không mở formal pair trên
root này. Future attempt chỉ được xem xét sau independent provider recovery
signal, rồi phải dùng fresh draft/authorization/never-used root/preparation/
boundary/smoke/declaration/trace/pair artifacts hoàn toàn mới.

## Sau formal window

- `passed` chỉ là technical evidence; không tự authorize controlled-demo hay
  default rollout.
- Reconcile gate, trace, review và rollback trên exact commit.
- Chỉ sau accepted owner decision mới tạo activation bundle mới.
- Nếu provider outage: disposition là `inconclusive_provider_outage`, giữ OFF
  và chỉ mở window khác bằng declaration + smoke hoàn toàn mới.
