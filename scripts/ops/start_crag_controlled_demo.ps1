param(
    [Parameter(Mandatory = $true)]
    [string]$Config
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot "crag_controlled_demo_common.ps1")
$pythonExe = Join-Path $projectRoot "chat_env\Scripts\python.exe"
$stateDir = Join-Path $projectRoot ".agents\state"
$statePath = Join-Path $stateDir "crag-controlled-demo.json"
$logsDir = Join-Path $projectRoot "logs\crag-controlled-demo"
$configPath = (Resolve-Path -LiteralPath $Config).Path

if (!(Test-Path -LiteralPath $pythonExe)) {
    throw "Khong tim thay chat_env\Scripts\python.exe."
}
if (Test-Path -LiteralPath $statePath) {
    throw "Controlled demo da co state. Chay status/stop truoc khi start lai."
}
if ([string]::IsNullOrWhiteSpace($env:CRAG_PILOT_ASSIGNMENT_SALT)) {
    throw "Thieu CRAG_PILOT_ASSIGNMENT_SALT trong process hien tai."
}

$demoConfig = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
$pinnedControlUrl = "http://127.0.0.1:8101"
$pinnedCandidateUrl = "http://127.0.0.1:8102"
$required = @(
    $demoConfig.git_sha,
    $demoConfig.snapshot_fingerprint,
    $demoConfig.collection,
    $demoConfig.max_concurrent_rag,
    $demoConfig.experiment_id,
    $demoConfig.eligible_cohort.department,
    $demoConfig.eligible_cohort.sha256,
    $demoConfig.runtime_contract.execution_context,
    $demoConfig.runtime_contract.request_deadline_seconds,
    $demoConfig.deployments.control.id,
    $demoConfig.deployments.candidate.id,
    $demoConfig.deployment_urls.control,
    $demoConfig.deployment_urls.candidate,
    $demoConfig.activation_bundle.path,
    $demoConfig.activation_bundle.sha256
)
if ($required | Where-Object { [string]::IsNullOrWhiteSpace([string]$_) -or [string]$_ -match "REPLACE_WITH" }) {
    throw "Config con thieu gia tri pinned hoac van chua placeholder."
}
if ([string]$demoConfig.runtime_contract.execution_context -ne "production") {
    throw "runtime_contract.execution_context phai la production."
}
if ($demoConfig.runtime_contract.evaluation_force_ambiguous -ne $false) {
    throw "runtime_contract.evaluation_force_ambiguous phai la false."
}
if ($demoConfig.collection -ne "TaiLieuKyThuat_v2") {
    throw "collection phai la TaiLieuKyThuat_v2."
}
if ($demoConfig.max_concurrent_rag -ne 4) {
    throw "max_concurrent_rag phai dung bang 4."
}
$configControlUrl = ([string]$demoConfig.deployment_urls.control).TrimEnd('/')
$configCandidateUrl = ([string]$demoConfig.deployment_urls.candidate).TrimEnd('/')
if ($configControlUrl -ne $pinnedControlUrl -or $configCandidateUrl -ne $pinnedCandidateUrl) {
    throw "deployment_urls phai pin vao http://127.0.0.1:8101 va http://127.0.0.1:8102."
}

$bundlePathValue = [string]$demoConfig.activation_bundle.path
if (![IO.Path]::IsPathRooted($bundlePathValue)) {
    $bundlePathValue = Join-Path (Split-Path -Parent $configPath) $bundlePathValue
}
$bundlePath = (Resolve-Path -LiteralPath $bundlePathValue).Path
$bundleSha256 = [string]$demoConfig.activation_bundle.sha256
$requestDeadline = 0.0
if (
    ![double]::TryParse(
        [string]$demoConfig.runtime_contract.request_deadline_seconds,
        [Globalization.NumberStyles]::Float,
        [Globalization.CultureInfo]::InvariantCulture,
        [ref]$requestDeadline
    ) -or $requestDeadline -ne 120.0
) {
    throw "runtime_contract.request_deadline_seconds phai dung bang 120."
}

Push-Location $projectRoot
try {
    $head = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $head -ne [string]$demoConfig.git_sha) {
        throw "Commit hien tai khong trung config.git_sha."
    }
    if (& git status --porcelain) {
        throw "Worktree phai sach truoc khi start controlled demo."
    }
}
finally {
    Pop-Location
}

foreach ($port in 8101, 8102) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw "Port $port dang duoc su dung."
    }
}

New-Item -ItemType Directory -Force -Path $stateDir, $logsDir | Out-Null

function ConvertTo-EnvironmentTable {
    param([object]$Value)
    $table = @{}
    foreach ($property in $Value.PSObject.Properties) {
        $table[$property.Name] = [string]$property.Value
    }
    return $table
}

function Render-ActivationProfile {
    param([string]$Profile)
    $arguments = @(
        "-m", "scripts.ops.render_activation_profile",
        "--profile", $Profile,
        "--scope", "controlled_demo"
    )
    if ($Profile -ne "all_off") {
        $arguments += @(
            "--activation-bundle", $bundlePath,
            "--activation-bundle-sha256", $bundleSha256
        )
    }
    $output = & $pythonExe @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Khong render duoc activation profile $Profile."
    }
    return ConvertTo-EnvironmentTable (($output -join [Environment]::NewLine) | ConvertFrom-Json)
}

$controlEnv = Render-ActivationProfile "all_off"
$candidateEnv = Render-ActivationProfile "crag_claim"
if ($candidateEnv.RAG_DEPLOYMENT_GIT_SHA -ne [string]$demoConfig.git_sha) {
    throw "Activation bundle khong trung config.git_sha."
}

$common = @{
    RAG_DEPLOYMENT_GIT_SHA = [string]$demoConfig.git_sha
    RAG_SNAPSHOT_FINGERPRINT = [string]$demoConfig.snapshot_fingerprint
    CRAG_PILOT_ASSIGNMENT_SALT = [string]$env:CRAG_PILOT_ASSIGNMENT_SALT
    RAG_EXECUTION_CONTEXT = "production"
    RAG_EVAL_FORCE_AMBIGUOUS = "false"
    RAG_REQUEST_DEADLINE_SECONDS = $requestDeadline.ToString(
        [Globalization.CultureInfo]::InvariantCulture
    )
    QDRANT_COLLECTION = [string]$demoConfig.collection
    MAX_CONCURRENT_RAG = [string]$demoConfig.max_concurrent_rag
    PARENT_CONTEXT_MAX_WORKERS = "4"
}
$common.Keys | ForEach-Object {
    $controlEnv[$_] = $common[$_]
    $candidateEnv[$_] = $common[$_]
}
$started = @()
try {
    $controlEnv.RAG_SERVER_PORT = "8101"
    $controlEnv.RAG_DEPLOYMENT_ID = [string]$demoConfig.deployments.control.id
    $controlEnv.RAG_TRACE_LOG_FILE = Join-Path $logsDir "control-trace.jsonl"
    $started += Start-CragDemoProcess $pythonExe $projectRoot "control" $controlEnv `
        "mech_chatbot.api.rag_server" (Join-Path $logsDir "control.out.log") `
        (Join-Path $logsDir "control.err.log")

    $candidateEnv.RAG_SERVER_PORT = "8102"
    $candidateEnv.RAG_DEPLOYMENT_ID = [string]$demoConfig.deployments.candidate.id
    $candidateEnv.RAG_TRACE_LOG_FILE = Join-Path $logsDir "candidate-trace.jsonl"
    $started += Start-CragDemoProcess $pythonExe $projectRoot "candidate" $candidateEnv `
        "mech_chatbot.api.rag_server" (Join-Path $logsDir "candidate.out.log") `
        (Join-Path $logsDir "candidate.err.log")

    Wait-CragDemoHttpHealth "$pinnedControlUrl/health" 60 "Control RAG deployment khong healthy."
    Wait-CragDemoHttpHealth "$pinnedCandidateUrl/health" 60 "Candidate RAG deployment khong healthy."

    $preflightPath = Join-Path (Split-Path -Parent $configPath) "deployment-preflight.json"
    & $pythonExe -m scripts.eval.crag_pilot_preflight `
        --config $configPath --output $preflightPath
    if ($LASTEXITCODE -ne 0) {
        throw "Deployment preflight khong dat."
    }

    @{
        schema = "crag-controlled-demo-process-state-v1"
        config = $configPath
        config_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $configPath).Hash.ToLowerInvariant()
        preflight = $preflightPath
        preflight_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $preflightPath).Hash.ToLowerInvariant()
        activation_bundle = $bundlePath
        activation_bundle_sha256 = $bundleSha256.ToLowerInvariant()
        gateway_enabled = $false
        processes = $started
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding utf8
    Write-Output "Control/candidate va preflight da san sang. Hay review preflight truoc khi enable gateway."
}
catch {
    foreach ($item in ($started | Sort-Object { if ($_.name -eq "gateway") { 0 } else { 1 } })) {
        Stop-Process -Id $item.pid -ErrorAction SilentlyContinue
    }
    throw
}
