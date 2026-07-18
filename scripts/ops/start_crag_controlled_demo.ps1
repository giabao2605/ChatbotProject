param(
    [Parameter(Mandatory = $true)]
    [string]$Config,
    [switch]$StartGateway
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
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

foreach ($port in 8080, 8101, 8102) {
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

function Wait-RagHealth {
    param([string]$Url)
    for ($attempt = 1; $attempt -le 60; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri "$($Url.TrimEnd('/'))/health" -TimeoutSec 5
            if ($health.status -eq "ok") { return }
        }
        catch {
        }
        Start-Sleep -Seconds 2
    }
    throw "RAG deployment khong healthy: $Url"
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
    $started += Start-DemoProcess "control" $controlEnv "mech_chatbot.api.rag_server" `
        (Join-Path $logsDir "control.out.log") (Join-Path $logsDir "control.err.log")

    $candidateEnv = $common.Clone()
    $candidateEnv.RAG_SERVER_PORT = "8102"
    $candidateEnv.RAG_DEPLOYMENT_ID = [string]$demoConfig.deployments.candidate.id
    $candidateEnv.RAG_CRAG_ENABLED = "true"
    $candidateEnv.RAG_CLAIM_REPAIR_ENABLED = "true"
    $started += Start-DemoProcess "candidate" $candidateEnv "mech_chatbot.api.rag_server" `
        (Join-Path $logsDir "candidate.out.log") (Join-Path $logsDir "candidate.err.log")

    Wait-RagHealth ([string]$demoConfig.deployment_urls.control)
    Wait-RagHealth ([string]$demoConfig.deployment_urls.candidate)

    $preflightPath = Join-Path (Split-Path -Parent $configPath) "deployment-preflight.json"
    & $pythonExe -m scripts.eval.crag_pilot_preflight `
        --config $configPath --output $preflightPath
    if ($LASTEXITCODE -ne 0) {
        throw "Deployment preflight khong dat."
    }

    if ($StartGateway) {
        $appEnv = @{
            APP_SERVER_PORT = "8080"
            RAG_SERVER_URL = [string]$demoConfig.deployment_urls.control
            CRAG_PILOT_ENABLED = "true"
            CRAG_PILOT_EXPERIMENT_ID = [string]$demoConfig.experiment_id
            CRAG_PILOT_ASSIGNMENT_SALT = [string]$env:CRAG_PILOT_ASSIGNMENT_SALT
            CRAG_PILOT_DEPARTMENT = [string]$demoConfig.eligible_cohort.department
            CRAG_PILOT_COHORT_SHA256 = [string]$demoConfig.eligible_cohort.sha256
            CRAG_PILOT_CONTROL_URL = [string]$demoConfig.deployment_urls.control
            CRAG_PILOT_CANDIDATE_URL = [string]$demoConfig.deployment_urls.candidate
            CRAG_PILOT_CONTROL_DEPLOYMENT_ID = [string]$demoConfig.deployments.control.id
            CRAG_PILOT_CANDIDATE_DEPLOYMENT_ID = [string]$demoConfig.deployments.candidate.id
            CRAG_PILOT_SNAPSHOT_FINGERPRINT = [string]$demoConfig.snapshot_fingerprint
        }
        $started += Start-DemoProcess "gateway" $appEnv "mech_chatbot.api.app_server" `
            (Join-Path $logsDir "gateway.out.log") (Join-Path $logsDir "gateway.err.log")
        for ($attempt = 1; $attempt -le 30; $attempt++) {
            try {
                $appHealth = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/health" -TimeoutSec 5
                if ($appHealth.status -eq "ok") { break }
            }
            catch {
            }
            if ($attempt -eq 30) { throw "Browser gateway khong healthy tren port 8080." }
            Start-Sleep -Seconds 2
        }
    }

    @{
        schema = "crag-controlled-demo-process-state-v1"
        config = $configPath
        preflight = $preflightPath
        gateway_enabled = [bool]$StartGateway
        processes = $started
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding utf8
    Write-Output "Controlled demo da start. State: $statePath"
}
catch {
    foreach ($item in ($started | Sort-Object { if ($_.name -eq "gateway") { 0 } else { 1 } })) {
        Stop-Process -Id $item.pid -ErrorAction SilentlyContinue
    }
    throw
}
