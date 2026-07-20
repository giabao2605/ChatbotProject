param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("crag_claim", "grounded_math", "query_decomposition", "graph_retrieval", "community_summaries")]
    [string]$Profile,

    [ValidateSet("evaluation", "controlled_demo", "default_rollout")]
    [string]$Scope = "evaluation",

    [string]$ActivationBundle,
    [string]$ActivationBundleSha256,

    [Parameter(Mandatory = $true)]
    [string]$SnapshotFingerprint,

    [int]$ControlPort = 8101,
    [int]$CandidatePort = 8102,
    [string]$ControlDeploymentId = "rag-profile-control",
    [string]$CandidateDeploymentId = "rag-profile-candidate"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot "crag_controlled_demo_common.ps1")
$pythonExe = Join-Path $projectRoot "chat_env\Scripts\python.exe"
$stateDir = Join-Path $projectRoot ".agents\state"
$statePath = Join-Path $stateDir "rag-profile-pair.json"
$logsDir = Join-Path $projectRoot "logs\rag-profile-pair"

if (!(Test-Path -LiteralPath $pythonExe)) {
    throw "Khong tim thay chat_env\Scripts\python.exe."
}
if (Test-Path -LiteralPath $statePath) {
    throw "RAG profile pair da co state. Chay stop truoc khi start lai."
}
if ([string]::IsNullOrWhiteSpace($SnapshotFingerprint)) {
    throw "SnapshotFingerprint khong duoc de trong."
}
if ($ControlPort -eq $CandidatePort) {
    throw "ControlPort va CandidatePort phai khac nhau."
}
if ($Scope -ne "evaluation") {
    if ([string]::IsNullOrWhiteSpace($ActivationBundle)) {
        throw "Live scope can ActivationBundle."
    }
    if ([string]::IsNullOrWhiteSpace($ActivationBundleSha256)) {
        throw "Live scope can ActivationBundleSha256."
    }
    $ActivationBundle = (Resolve-Path -LiteralPath $ActivationBundle).Path
}

Push-Location $projectRoot
try {
    $head = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Khong doc duoc git commit hien tai."
    }
    if (& git status --porcelain) {
        throw "Worktree phai sach truoc khi start profile pair."
    }
}
finally {
    Pop-Location
}

foreach ($port in $ControlPort, $CandidatePort) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw "Port $port dang duoc su dung."
    }
}

function ConvertTo-EnvironmentTable {
    param([object]$Value)
    $table = @{}
    foreach ($property in $Value.PSObject.Properties) {
        $table[$property.Name] = [string]$property.Value
    }
    return $table
}

function Render-ProfileEnvironment {
    param([string]$TargetProfile)
    $arguments = @(
        "-m", "scripts.ops.render_activation_profile",
        "--profile", $TargetProfile,
        "--scope", $Scope
    )
    if ($Scope -ne "evaluation" -and $TargetProfile -ne "all_off") {
        $arguments += @(
            "--activation-bundle", $ActivationBundle,
            "--activation-bundle-sha256", $ActivationBundleSha256
        )
    }
    $output = & $pythonExe @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Khong render duoc activation profile $TargetProfile."
    }
    return ConvertTo-EnvironmentTable (($output -join [Environment]::NewLine) | ConvertFrom-Json)
}

$controlEnv = Render-ProfileEnvironment "all_off"
$candidateEnv = Render-ProfileEnvironment $Profile
if ($Scope -ne "evaluation" -and $candidateEnv.RAG_DEPLOYMENT_GIT_SHA -ne $head) {
    throw "Activation bundle khong trung git commit hien tai."
}

$common = @{
    RAG_SNAPSHOT_FINGERPRINT = $SnapshotFingerprint
    RAG_EVAL_FORCE_AMBIGUOUS = "false"
    RAG_REQUEST_DEADLINE_SECONDS = "120"
    PARENT_CONTEXT_MAX_WORKERS = "4"
}
foreach ($key in $common.Keys) {
    $controlEnv[$key] = $common[$key]
    $candidateEnv[$key] = $common[$key]
}
$controlEnv.RAG_DEPLOYMENT_GIT_SHA = $head
$controlEnv.RAG_SERVER_PORT = [string]$ControlPort
$controlEnv.RAG_DEPLOYMENT_ID = $ControlDeploymentId
$controlEnv.RAG_TRACE_LOG_FILE = Join-Path $logsDir "control-trace.jsonl"
$candidateEnv.RAG_SERVER_PORT = [string]$CandidatePort
$candidateEnv.RAG_DEPLOYMENT_ID = $CandidateDeploymentId
$candidateEnv.RAG_TRACE_LOG_FILE = Join-Path $logsDir "candidate-trace.jsonl"

New-Item -ItemType Directory -Force -Path $stateDir, $logsDir | Out-Null
$started = @()
try {
    $started += Start-CragDemoProcess $pythonExe $projectRoot "control" $controlEnv `
        "mech_chatbot.api.rag_server" (Join-Path $logsDir "control.out.log") `
        (Join-Path $logsDir "control.err.log")
    $started += Start-CragDemoProcess $pythonExe $projectRoot "candidate" $candidateEnv `
        "mech_chatbot.api.rag_server" (Join-Path $logsDir "candidate.out.log") `
        (Join-Path $logsDir "candidate.err.log")

    Wait-CragDemoHttpHealth "http://127.0.0.1:$ControlPort/health" 60 `
        "Control RAG deployment khong healthy."
    Wait-CragDemoHttpHealth "http://127.0.0.1:$CandidatePort/health" 60 `
        "Candidate RAG deployment khong healthy."

    @{
        schema = "rag-profile-pair-process-state-v1"
        source_commit = $head
        profile = $Profile
        scope = $Scope
        snapshot_fingerprint = $SnapshotFingerprint
        activation_bundle = $ActivationBundle
        activation_bundle_sha256 = $ActivationBundleSha256
        control_url = "http://127.0.0.1:$ControlPort"
        candidate_url = "http://127.0.0.1:$CandidatePort"
        processes = $started
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding utf8
    Write-Output "Control va candidate da san sang tren hai process rieng."
}
catch {
    foreach ($item in $started) {
        Stop-Process -Id $item.pid -ErrorAction SilentlyContinue
    }
    throw
}
