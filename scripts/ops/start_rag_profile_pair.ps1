param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("crag_claim", "grounded_math", "query_decomposition", "graph_retrieval", "community_summaries", "selective")]
    [string]$Profile,

    [ValidateSet("RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED", "RAG_GROUNDED_MATH_ENABLED", "RAG_LATE_INTERACTION_ENABLED", "RAG_QUERY_DECOMPOSITION_ENABLED", "RAG_GRAPH_RETRIEVAL_ENABLED", "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED")]
    [string[]]$EnableFeature = @(),

    [ValidateSet("evaluation", "controlled_demo", "default_rollout")]
    [string]$Scope = "evaluation",

    [string]$ActivationBundle,
    [string]$ActivationBundleSha256,

    [Parameter(Mandatory = $true)]
    [string]$SnapshotFingerprint,

    [int]$ControlPort = 8101,
    [int]$CandidatePort = 8102,
    [string]$ControlDeploymentId = "rag-profile-control",
    [string]$CandidateDeploymentId = "rag-profile-candidate",
    [string]$ProjectRoot,
    [string]$PythonExe,
    [string]$SqlDatabase,
    [string]$QdrantCollection,
    [string]$RestoreEvidence,
    [string]$RestoreEvidenceSha256
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}
$projectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
. (Join-Path $PSScriptRoot "crag_controlled_demo_common.ps1")
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $PythonExe = Join-Path $projectRoot "chat_env\Scripts\python.exe"
}
$pythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
$pythonPath = Join-Path $projectRoot "src"
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
$EnableFeature = @($EnableFeature | Select-Object -Unique)
if ($Profile -eq "selective" -and $Scope -eq "evaluation" -and $EnableFeature.Count -eq 0) {
    throw "Selective profile requires EnableFeature in evaluation."
}
if ($Profile -ne "selective" -and $EnableFeature.Count -gt 0) {
    throw "EnableFeature chi hop le voi profile selective."
}
if ($Scope -ne "evaluation" -and $EnableFeature.Count -gt 0) {
    throw "Live selective flags come from ActivationBundle; do not pass EnableFeature."
}
if ($SqlDatabase -and $SqlDatabase -notmatch "^[A-Za-z0-9][A-Za-z0-9_]*$") {
    throw "SqlDatabase contains invalid characters."
}
if (
    $QdrantCollection -and
    $QdrantCollection -notmatch "^[A-Za-z0-9][A-Za-z0-9_.-]*$"
) {
    throw "QdrantCollection contains invalid characters."
}
if ($Scope -ne "evaluation") {
    if ([string]::IsNullOrWhiteSpace($ActivationBundle)) {
        throw "Live scope can ActivationBundle."
    }
    if ([string]::IsNullOrWhiteSpace($ActivationBundleSha256)) {
        throw "Live scope can ActivationBundleSha256."
    }
    if (![IO.Path]::IsPathRooted($ActivationBundle)) {
        $ActivationBundle = Join-Path $projectRoot $ActivationBundle
    }
    $ActivationBundle = (Resolve-Path -LiteralPath $ActivationBundle).Path
}
if (![string]::IsNullOrWhiteSpace($RestoreEvidence)) {
    if (![IO.Path]::IsPathRooted($RestoreEvidence)) {
        $RestoreEvidence = Join-Path $projectRoot $RestoreEvidence
    }
    $RestoreEvidence = (Resolve-Path -LiteralPath $RestoreEvidence).Path
}
if (
    ![string]::IsNullOrWhiteSpace($RestoreEvidenceSha256) -and
    $RestoreEvidenceSha256 -notmatch "^[0-9a-fA-F]{64}$"
) {
    throw "RestoreEvidenceSha256 must be a SHA-256 digest."
}
if (![string]::IsNullOrWhiteSpace($RestoreEvidenceSha256)) {
    $RestoreEvidenceSha256 = $RestoreEvidenceSha256.ToLowerInvariant()
}

Push-Location $projectRoot
try {
    $headOutput = & git rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or !$headOutput) {
        throw "Khong doc duoc git commit hien tai."
    }
    $head = ($headOutput -join "").Trim()
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

function Get-RagServiceToken {
    $token = [Environment]::GetEnvironmentVariable("RAG_SERVICE_TOKEN", "Process")
    if (![string]::IsNullOrWhiteSpace($token)) {
        return $token
    }

    $previousPythonPath = [Environment]::GetEnvironmentVariable("PYTHONPATH", "Process")
    [Environment]::SetEnvironmentVariable("PYTHONPATH", $pythonPath, "Process")
    Push-Location $projectRoot
    try {
        $tokenOutput = & $pythonExe -c `
            "from mech_chatbot.config.settings import load_settings; print(load_settings().RAG_SERVICE_TOKEN)"
        if ($LASTEXITCODE -ne 0) {
            throw "Khong doc duoc RAG service token tu runtime settings."
        }
    }
    finally {
        Pop-Location
        [Environment]::SetEnvironmentVariable(
            "PYTHONPATH", $previousPythonPath, "Process"
        )
    }
    return ($tokenOutput -join "").Trim()
}

function Render-ProfileEnvironment {
    param([string]$TargetProfile)
    $arguments = @(
        "-m", "scripts.ops.render_activation_profile",
        "--profile", $TargetProfile,
        "--scope", $Scope
    )
    if ($TargetProfile -eq "selective" -and $Scope -eq "evaluation") {
        foreach ($feature in $EnableFeature) {
            $arguments += @("--enable-feature", $feature)
        }
    }
    if ($Scope -ne "evaluation" -and $TargetProfile -ne "all_off") {
        $arguments += @(
            "--activation-bundle", $ActivationBundle,
            "--activation-bundle-sha256", $ActivationBundleSha256
        )
    }
    $previousPythonPath = [Environment]::GetEnvironmentVariable("PYTHONPATH", "Process")
    [Environment]::SetEnvironmentVariable("PYTHONPATH", $pythonPath, "Process")
    Push-Location $projectRoot
    try {
        $output = & $pythonExe @arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Khong render duoc activation profile $TargetProfile."
        }
    }
    finally {
        Pop-Location
        [Environment]::SetEnvironmentVariable(
            "PYTHONPATH", $previousPythonPath, "Process"
        )
    }
    return ConvertTo-EnvironmentTable (($output -join [Environment]::NewLine) | ConvertFrom-Json)
}

$controlEnv = Render-ProfileEnvironment "all_off"
$candidateEnv = Render-ProfileEnvironment $Profile
$actualFeatures = @(
    $candidateEnv.Keys |
        Where-Object {
            $_ -like "RAG_*_ENABLED" -and $candidateEnv[$_] -eq "true"
        } |
        Sort-Object
)
if ($Profile -eq "selective" -and $Scope -eq "evaluation") {
    $requestedFeatures = @($EnableFeature | Sort-Object)
    if (@(Compare-Object $requestedFeatures $actualFeatures).Count -gt 0) {
        throw "Selective bundle flags do not match EnableFeature."
    }
}
if (
    $Scope -eq "controlled_demo" -and
    $actualFeatures -contains "RAG_GRAPH_RETRIEVAL_ENABLED" -and
    ([string]::IsNullOrWhiteSpace($SqlDatabase) -or
     [string]::IsNullOrWhiteSpace($QdrantCollection))
) {
    throw "Graph controlled_demo requires SqlDatabase and QdrantCollection."
}
if (
    $Scope -eq "controlled_demo" -and
    $actualFeatures -contains "RAG_GRAPH_RETRIEVAL_ENABLED" -and
    ([string]::IsNullOrWhiteSpace($RestoreEvidence) -or
     [string]::IsNullOrWhiteSpace($RestoreEvidenceSha256))
) {
    throw "Graph controlled_demo requires verified restore evidence."
}
if (
    $Scope -eq "controlled_demo" -and
    $actualFeatures -contains "RAG_GRAPH_RETRIEVAL_ENABLED"
) {
    $previousPythonPath = [Environment]::GetEnvironmentVariable("PYTHONPATH", "Process")
    [Environment]::SetEnvironmentVariable("PYTHONPATH", $pythonPath, "Process")
    Push-Location $projectRoot
    try {
        $verifiedFingerprint = & $pythonExe -m scripts.ops.verify_restore_evidence `
            --evidence $RestoreEvidence --sha256 $RestoreEvidenceSha256
        if ($LASTEXITCODE -ne 0) {
            throw "Graph restore evidence verification failed."
        }
    }
    finally {
        Pop-Location
        [Environment]::SetEnvironmentVariable(
            "PYTHONPATH", $previousPythonPath, "Process"
        )
    }
    if (($verifiedFingerprint -join "").Trim() -ne $SnapshotFingerprint) {
        throw "Graph restore evidence fingerprint does not match SnapshotFingerprint."
    }
}
if (
    $Scope -eq "controlled_demo" -and
    $actualFeatures -contains "RAG_GRAPH_RETRIEVAL_ENABLED" -and
    $candidateEnv.RAG_GRAPH_FINGERPRINT -ne $SnapshotFingerprint
) {
    throw "Graph controlled_demo fingerprint must match ActivationBundle."
}
if ($Scope -ne "evaluation" -and $candidateEnv.RAG_DEPLOYMENT_GIT_SHA -ne $head) {
    throw "Activation bundle khong trung git commit hien tai."
}

$common = @{
    PYTHONPATH = $pythonPath
    RAG_SNAPSHOT_FINGERPRINT = $SnapshotFingerprint
    RAG_EVAL_FORCE_AMBIGUOUS = "false"
    RAG_REQUEST_DEADLINE_SECONDS = "120"
    PARENT_CONTEXT_MAX_WORKERS = "4"
}
if ($SqlDatabase) {
    $common.SQL_DATABASE = $SqlDatabase
}
if ($QdrantCollection) {
    $common.QDRANT_COLLECTION = $QdrantCollection
}
if ($RestoreEvidenceSha256) {
    $common.RAG_RESTORE_EVIDENCE_SHA256 = $RestoreEvidenceSha256
}
$serviceToken = Get-RagServiceToken
if (![string]::IsNullOrWhiteSpace($serviceToken)) {
    $common.RAG_SERVICE_TOKEN = $serviceToken
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
        "Control RAG deployment khong healthy." $serviceToken $Scope
    Wait-CragDemoHttpHealth "http://127.0.0.1:$CandidatePort/health" 60 `
        "Candidate RAG deployment khong healthy." $serviceToken $Scope

    @{
        schema = "rag-profile-pair-process-state-v1"
        source_commit = $head
        profile = $Profile
        enabled_features = $actualFeatures
        scope = $Scope
        snapshot_fingerprint = $SnapshotFingerprint
        activation_bundle = $ActivationBundle
        activation_bundle_sha256 = $ActivationBundleSha256
        restore_evidence = $RestoreEvidence
        restore_evidence_sha256 = $RestoreEvidenceSha256
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
