$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot "crag_controlled_demo_common.ps1")
$pythonExe = Join-Path $projectRoot "chat_env\Scripts\python.exe"
$statePath = Join-Path $projectRoot ".agents\state\crag-controlled-demo.json"
$logsDir = Join-Path $projectRoot "logs\crag-controlled-demo"

if (!(Test-Path -LiteralPath $statePath)) {
    throw "Chua co control/candidate state. Hay chay start truoc."
}
if ([string]::IsNullOrWhiteSpace($env:CRAG_PILOT_ASSIGNMENT_SALT)) {
    throw "Thieu CRAG_PILOT_ASSIGNMENT_SALT trong process hien tai."
}
if (Get-NetTCPConnection -State Listen -LocalPort 8080 -ErrorAction SilentlyContinue) {
    throw "Port 8080 dang duoc su dung."
}

$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.gateway_enabled) { throw "Gateway da duoc enable." }
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $state.config).Hash.ToLowerInvariant() -ne $state.config_sha256) {
    throw "Config da thay doi sau preflight; tu choi enable gateway."
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $state.preflight).Hash.ToLowerInvariant() -ne $state.preflight_sha256) {
    throw "Preflight artifact da thay doi; tu choi enable gateway."
}
$demoConfig = Get-Content -Raw -LiteralPath $state.config | ConvertFrom-Json
$preflight = Get-Content -Raw -LiteralPath $state.preflight | ConvertFrom-Json
if (!$preflight.passed) { throw "Deployment preflight khong passed; tu choi enable gateway." }
if ([string]$demoConfig.eligible_cohort.department -ne "Technical") {
    throw "Controlled demo chi cho phep department Technical."
}
if ([string]$demoConfig.eligible_cohort.site -ne "HQ") {
    throw "Controlled demo chi cho phep site HQ."
}
$actorHashes = @($demoConfig.eligible_cohort.actor_hashes)
if ($actorHashes.Count -lt 2 -or $actorHashes.Count -gt 10) {
    throw "Controlled demo can 2-10 actor hashes da pin trong config."
}
foreach ($value in $actorHashes) {
    if ([string]$value -notmatch '^[0-9a-fA-F]{64}$') {
        throw "eligible_cohort.actor_hashes phai la SHA-256 hex."
    }
}
$cohortBytes = [Text.Encoding]::UTF8.GetBytes((($actorHashes | Sort-Object) -join "`n"))
$sha256 = [Security.Cryptography.SHA256]::Create()
try {
    $actualCohortHash = ([BitConverter]::ToString($sha256.ComputeHash($cohortBytes))).Replace("-", "").ToLowerInvariant()
}
finally {
    $sha256.Dispose()
}
if ($actualCohortHash -ne [string]$demoConfig.eligible_cohort.sha256) {
    throw "eligible_cohort.sha256 khong khop actor_hashes."
}
foreach ($item in $state.processes) {
    $process = Get-Process -Id $item.pid -ErrorAction SilentlyContinue
    if (!$process) { throw "$($item.name) process khong con chay." }
    $expectedStart = [datetime]::Parse([string]$item.started_at).ToUniversalTime()
    if ([math]::Abs(($process.StartTime.ToUniversalTime() - $expectedStart).TotalSeconds) -gt 1) {
        throw "PID $($item.pid) da bi tai su dung; tu choi enable gateway."
    }
}

$appEnv = @{
    APP_SERVER_PORT = "8080"
    RAG_SERVER_URL = [string]$demoConfig.deployment_urls.control
    RAG_TRACE_LOG_FILE = Join-Path $logsDir "gateway-trace.jsonl"
    CRAG_PILOT_ENABLED = "true"
    CRAG_PILOT_EXPERIMENT_ID = [string]$demoConfig.experiment_id
    CRAG_PILOT_ASSIGNMENT_SALT = [string]$env:CRAG_PILOT_ASSIGNMENT_SALT
    CRAG_PILOT_DEPARTMENT = [string]$demoConfig.eligible_cohort.department
    CRAG_PILOT_SITE = [string]$demoConfig.eligible_cohort.site
    CRAG_PILOT_ALLOWED_ACTOR_HASHES = ($actorHashes -join ',')
    CRAG_PILOT_COHORT_SHA256 = [string]$demoConfig.eligible_cohort.sha256
    CRAG_PILOT_CONTROL_URL = [string]$demoConfig.deployment_urls.control
    CRAG_PILOT_CANDIDATE_URL = [string]$demoConfig.deployment_urls.candidate
    CRAG_PILOT_CONTROL_DEPLOYMENT_ID = [string]$demoConfig.deployments.control.id
    CRAG_PILOT_CANDIDATE_DEPLOYMENT_ID = [string]$demoConfig.deployments.candidate.id
    CRAG_PILOT_SNAPSHOT_FINGERPRINT = [string]$demoConfig.snapshot_fingerprint
}
$saved = @{}
foreach ($key in $appEnv.Keys) {
    $saved[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
    [Environment]::SetEnvironmentVariable($key, [string]$appEnv[$key], "Process")
}
try {
    $process = Start-Process -FilePath $pythonExe `
        -ArgumentList @("-m", "mech_chatbot.api.app_server") `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logsDir "gateway.out.log") `
        -RedirectStandardError (Join-Path $logsDir "gateway.err.log") `
        -PassThru
}
finally {
    foreach ($key in $appEnv.Keys) {
        [Environment]::SetEnvironmentVariable($key, $saved[$key], "Process")
    }
}

try {
    Wait-CragDemoHttpHealth "http://127.0.0.1:8080/api/health" 30 `
        "Browser gateway khong healthy tren port 8080."
    $gateway = [pscustomobject]@{
        name = "gateway"
        pid = $process.Id
        started_at = $process.StartTime.ToUniversalTime().ToString("o")
    }
    $state.processes = @($state.processes) + $gateway
    $state.gateway_enabled = $true
    $state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding utf8
    Write-Output "Gateway da enable sau khi preflight duoc review."
}
catch {
    Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
    throw
}
