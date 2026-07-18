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
$required = @(
    $demoConfig.git_sha,
    $demoConfig.snapshot_fingerprint,
    $demoConfig.experiment_id,
    $demoConfig.eligible_cohort.department,
    $demoConfig.eligible_cohort.sha256,
    $demoConfig.deployments.control.id,
    $demoConfig.deployments.candidate.id
)
if ($required | Where-Object { [string]::IsNullOrWhiteSpace([string]$_) -or [string]$_ -match "REPLACE_WITH" }) {
    throw "Config con thieu gia tri pinned hoac van chua placeholder."
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

function Start-DemoProcess {
    param(
        [string]$Name,
        [hashtable]$Environment,
        [string]$Module,
        [string]$OutLog,
        [string]$ErrLog
    )
    $saved = @{}
    foreach ($key in $Environment.Keys) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
        [Environment]::SetEnvironmentVariable($key, [string]$Environment[$key], "Process")
    }
    try {
        $process = Start-Process -FilePath $pythonExe `
            -ArgumentList @("-m", $Module) `
            -WorkingDirectory $projectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $OutLog `
            -RedirectStandardError $ErrLog `
            -PassThru
        return @{
            name = $Name
            pid = $process.Id
            started_at = $process.StartTime.ToUniversalTime().ToString("o")
        }
    }
    finally {
        foreach ($key in $Environment.Keys) {
            [Environment]::SetEnvironmentVariable($key, $saved[$key], "Process")
        }
    }
}

$common = @{
    RAG_DEPLOYMENT_GIT_SHA = [string]$demoConfig.git_sha
    RAG_SNAPSHOT_FINGERPRINT = [string]$demoConfig.snapshot_fingerprint
    CRAG_PILOT_ASSIGNMENT_SALT = [string]$env:CRAG_PILOT_ASSIGNMENT_SALT
    RAG_GROUNDED_MATH_ENABLED = "false"
    RAG_LATE_INTERACTION_ENABLED = "false"
    RAG_QUERY_DECOMPOSITION_ENABLED = "false"
    RAG_GRAPH_RETRIEVAL_ENABLED = "false"
    RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED = "false"
    PARENT_CONTEXT_MAX_WORKERS = "4"
}
$started = @()
try {
    $controlEnv = $common.Clone()
    $controlEnv.RAG_SERVER_PORT = "8101"
    $controlEnv.RAG_DEPLOYMENT_ID = [string]$demoConfig.deployments.control.id
    $controlEnv.RAG_CRAG_ENABLED = "false"
    $controlEnv.RAG_CLAIM_REPAIR_ENABLED = "false"
    $controlEnv.RAG_TRACE_LOG_FILE = Join-Path $logsDir "control-trace.jsonl"
    $started += Start-DemoProcess "control" $controlEnv "mech_chatbot.api.rag_server" `
        (Join-Path $logsDir "control.out.log") (Join-Path $logsDir "control.err.log")

    $candidateEnv = $common.Clone()
    $candidateEnv.RAG_SERVER_PORT = "8102"
    $candidateEnv.RAG_DEPLOYMENT_ID = [string]$demoConfig.deployments.candidate.id
    $candidateEnv.RAG_CRAG_ENABLED = "true"
    $candidateEnv.RAG_CLAIM_REPAIR_ENABLED = "true"
    $candidateEnv.RAG_TRACE_LOG_FILE = Join-Path $logsDir "candidate-trace.jsonl"
    $started += Start-DemoProcess "candidate" $candidateEnv "mech_chatbot.api.rag_server" `
        (Join-Path $logsDir "candidate.out.log") (Join-Path $logsDir "candidate.err.log")

    $controlUrl = ([string]$demoConfig.deployment_urls.control).TrimEnd('/')
    $candidateUrl = ([string]$demoConfig.deployment_urls.candidate).TrimEnd('/')
    Wait-CragDemoHttpHealth "$controlUrl/health" 60 "Control RAG deployment khong healthy."
    Wait-CragDemoHttpHealth "$candidateUrl/health" 60 "Candidate RAG deployment khong healthy."

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
