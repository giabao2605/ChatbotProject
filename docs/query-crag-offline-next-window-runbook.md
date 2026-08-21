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
$mathCampaignRoot = 'C:\Users\bao.nguyen\Documents\ChatBotProject\.local\worktrees\advanced-rag-post-burst-disposition\.local\math-pilot-7b9d575-operator-window-13-campaign'
$mathTaskName = 'ChatBotProject-GroundedMath-Operator-Window13'
$mathReleaseRoot = 'C:\Users\bao.nguyen\Documents\ChatBotProject\.local\worktrees\advanced-rag-post-burst-disposition\.local\grounded-math-interaction-matrix-20260818-02\release-candidate'

& .\scripts\ops\prepare_query_formal_window.ps1 `
  -RunRoot $runRoot `
  -ExpectedSourceCommit $expectedSourceCommit `
  -PythonPath $python `
  -CampaignRoot $mathCampaignRoot `
  -TaskName $mathTaskName `
  -MathReleaseRoot $mathReleaseRoot
if ($LASTEXITCODE -ne 0) { throw 'Query offline readiness failed.' }
```

Authorization boundary: stop here. The entrypoint above never calls a provider
and creates only `preflight.json` plus `rollback.json`. Do not run the next
block until the owner has approved provider traffic for this exact commit and
fresh run-root.

```powershell
$env:EXTERNAL_PROCESSING_POLICY = 'all_external'

& .\scripts\ops\prepare_query_formal_window.ps1 `
  -RunRoot $runRoot `
  -ExpectedSourceCommit $expectedSourceCommit `
  -PythonPath $python `
  -CampaignRoot $mathCampaignRoot `
  -TaskName $mathTaskName `
  -MathReleaseRoot $mathReleaseRoot `
  -RevalidateForProviderTraffic
if ($LASTEXITCODE -ne 0) { throw 'Query provider-boundary revalidation failed.' }

& $python -m scripts.eval.provider_smoke `
  --output "$runRoot\provider-smoke.json"
```

`prepare_query_formal_window.ps1` rejects missing/mismatched collection
bindings, an existing run-root, source/worktree drift, manifest/runner hash
drift, non-terminal Math campaign, active Scheduled Task, signed Math release
drift and failed offline preflight/rollback. A failed attempt leaves the
run-root non-reusable. Do not delete or repair it to continue the same window.
The `-RevalidateForProviderTraffic` call immediately before smoke rechecks all
bindings and reruns offline preflight/rollback into
`*-provider-boundary.json`; it refuses a root containing anything beyond the
two original offline artifacts. Any failure tombstones the root before
provider traffic.

Sau smoke, derive execution declaration mới và điền toàn bộ binding động bằng
hash thực tế. Owner ký và baseline phải bắt đầu trong 30 phút từ smoke;
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

Không chạy thêm standalone health smoke. Fresh Query formal window chỉ được mở
sau khi owner ký envelope bind exact clean commit và run-root chưa từng tồn
tại. Window đó phải chạy lại preparation, provider-boundary revalidation và
fresh formal-window smoke riêng; không copy health artifact/hash ở trên vào
declaration hoặc run-root mới.

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

## Sau formal window

- `passed` chỉ là technical evidence; không tự authorize controlled-demo hay
  default rollout.
- Reconcile gate, trace, review và rollback trên exact commit.
- Chỉ sau accepted owner decision mới tạo activation bundle mới.
- Nếu provider outage: disposition là `inconclusive_provider_outage`, giữ OFF
  và chỉ mở window khác bằng declaration + smoke hoàn toàn mới.
