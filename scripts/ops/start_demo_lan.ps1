$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $projectRoot

$pythonExe = Join-Path $projectRoot "chat_env\Scripts\python.exe"
$logsDir = Join-Path $projectRoot "logs"
$ragOutLog = Join-Path $logsDir "rag-server.out.log"
$ragErrLog = Join-Path $logsDir "rag-server.err.log"
$appOutLog = Join-Path $logsDir "app-api.out.log"
$appErrLog = Join-Path $logsDir "app-api.err.log"
$workerOutLog = Join-Path $logsDir "worker.out.log"
$workerErrLog = Join-Path $logsDir "worker.err.log"

if (!(Test-Path $pythonExe)) {
    throw "Khong tim thay chat_env\Scripts\python.exe. Hay tao/khai bao dung virtualenv truoc khi chay demo."
}
if (!(Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir | Out-Null
}

# Build Vue sources before starting the static app server. Without this step,
# start_demo_lan.ps1 would keep serving an old web-ui/dist even after source
# files were overwritten by a patch.
$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (!$npmCommand) {
    $npmCommand = Get-Command npm -ErrorAction SilentlyContinue
}
if (!$npmCommand) {
    throw "Khong tim thay npm. Hay cai Node.js LTS de build web-ui."
}
$webUiDir = Join-Path $projectRoot "web-ui"
Write-Output "Dang build Vue frontend..."
Push-Location $webUiDir
try {
    if (!(Test-Path (Join-Path $webUiDir "node_modules"))) {
        & $npmCommand.Source ci
        if ($LASTEXITCODE -ne 0) {
            throw "npm ci that bai."
        }
    }
    & $npmCommand.Source run build
    if ($LASTEXITCODE -ne 0) {
        throw "Build Vue frontend that bai."
    }
}
finally {
    Pop-Location
}

Write-Output "Dang kiem tra va cap nhat schema database..."
& $pythonExe "scripts\migrations\migrate.py"
if ($LASTEXITCODE -ne 0) {
    throw "Migration database that bai. Khong khoi dong service de tranh chay sai schema."
}

Write-Output "Dang kiem tra Qdrant payload indexes..."
& $pythonExe "scripts\create_qdrant_indexes.py"
if ($LASTEXITCODE -ne 0) {
    throw "Khoi tao Qdrant payload indexes that bai."
}

Write-Output "Dang kiem tra Qdrant serving metadata..."
& $pythonExe "scripts\migrations\backfill_qdrant_servable.py"
if ($LASTEXITCODE -ne 0) {
    throw "Backfill Qdrant serving metadata that bai."
}

Write-Output "Dang dong bo Qdrant governance metadata..."
& $pythonExe "scripts\migrations\backfill_qdrant_governance_metadata.py"
if ($LASTEXITCODE -ne 0) {
    throw "Dong bo Qdrant governance metadata that bai."
}

Write-Output "Dang chay production preflight read-only..."
& $pythonExe "scripts\ops\production_preflight.py" --skip-health
if ($LASTEXITCODE -ne 0) {
    throw "Production preflight khong dat. Khong khoi dong service."
}

function Get-DotEnvValue {
    param([string]$Path, [string]$Key)
    if (!(Test-Path $Path)) { return $null }
    foreach ($line in Get-Content $Path) {
        $trimmed = $line.Trim()
        if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
        $idx = $trimmed.IndexOf("=")
        if ($idx -lt 1) { continue }
        $k = $trimmed.Substring(0, $idx).Trim()
        if ($k -eq $Key) {
            return $trimmed.Substring($idx + 1).Trim().Trim('"')
        }
    }
    return $null
}

$envPath = Join-Path $projectRoot ".env"
$serviceToken = Get-DotEnvValue -Path $envPath -Key "RAG_SERVICE_TOKEN"
$sessionSecret = Get-DotEnvValue -Path $envPath -Key "APP_SESSION_SECRET"
$bridgeSecret = Get-DotEnvValue -Path $envPath -Key "CHAT_BRIDGE_SECRET"
$ragServerUrl = Get-DotEnvValue -Path $envPath -Key "RAG_SERVER_URL"
$appMode = $env:APP_ENV
if (!$appMode) { $appMode = Get-DotEnvValue -Path $envPath -Key "APP_ENV" }
$appTrustedHosts = $env:APP_TRUSTED_HOSTS
if (!$appTrustedHosts) {
    $appTrustedHosts = Get-DotEnvValue -Path $envPath -Key "APP_TRUSTED_HOSTS"
}
if (!$ragServerUrl) { $ragServerUrl = "http://127.0.0.1:8100" }
if (!$serviceToken) { $serviceToken = "" }
if (!$sessionSecret -and !$bridgeSecret -and !$serviceToken) {
    throw "Thieu APP_SESSION_SECRET, CHAT_BRIDGE_SECRET hoac RAG_SERVICE_TOKEN trong .env. Tao bang: python -c ""import secrets; print(secrets.token_urlsafe(48))"""
}

function Assert-PortsAvailable {
    param([int[]]$Ports)
    foreach ($port in $Ports) {
        $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        if ($listeners) {
            $pids = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
            throw "Port $port dang duoc su dung boi PID $($pids -join ','). Launcher khong dung process dang chay."
        }
    }
}

function Start-ProcessWithEnv {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory,
        [string]$RedirectStandardOutput,
        [string]$RedirectStandardError,
        [hashtable]$Environment
    )

    $oldValues = @{}
    try {
        foreach ($key in $Environment.Keys) {
            $oldValues[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
            [Environment]::SetEnvironmentVariable($key, [string]$Environment[$key], "Process")
        }

        return Start-Process `
            -FilePath $FilePath `
            -ArgumentList $ArgumentList `
            -WorkingDirectory $WorkingDirectory `
            -RedirectStandardOutput $RedirectStandardOutput `
            -RedirectStandardError $RedirectStandardError `
            -WindowStyle Hidden `
            -PassThru
    }
    finally {
        foreach ($key in $Environment.Keys) {
            [Environment]::SetEnvironmentVariable($key, $oldValues[$key], "Process")
        }
    }
}

function Wait-RestMethod {
    param([string]$Uri, [int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            return Invoke-RestMethod -Uri $Uri -TimeoutSec 5
        }
        catch {
            Start-Sleep -Seconds 2
        }
    } while ((Get-Date) -lt $deadline)
    return $null
}

function Wait-WebOk {
    param([string]$Uri, [int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $resp = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 5
            if ($resp.StatusCode -eq 200) { return $true }
        }
        catch {
            Start-Sleep -Seconds 2
        }
    } while ((Get-Date) -lt $deadline)
    return $false
}

Assert-PortsAvailable -Ports @(8100, 8080)

$lanIp = $null
try {
    $ipv4Lines = ipconfig | Select-String "IPv4 Address"
    foreach ($line in $ipv4Lines) {
        $candidate = (($line.ToString() -split ":")[-1]).Trim()
        if ($candidate -and $candidate -notlike "127.*" -and $candidate -notlike "172.26.*") {
            $lanIp = $candidate
            break
        }
    }
}
catch {
    $lanIp = $null
}
if (!$lanIp) { $lanIp = "localhost" }

$ownedProcesses = @()
try {
$ragProc = Start-ProcessWithEnv `
    -FilePath $pythonExe `
    -ArgumentList @("-m", "mech_chatbot.api.rag_server") `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $ragOutLog `
    -RedirectStandardError $ragErrLog `
    -Environment @{ PYTHONPATH = "src"; OMP_NUM_THREADS = "4" }
$ownedProcesses += $ragProc

$workerProc = Start-ProcessWithEnv `
    -FilePath $pythonExe `
    -ArgumentList @("run_worker.py") `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $workerOutLog `
    -RedirectStandardError $workerErrLog `
    -Environment @{ PYTHONPATH = "src" }
$ownedProcesses += $workerProc

$baseAppEnv = @{
    PYTHONPATH       = "src"
    APP_SERVER_HOST  = "0.0.0.0"
    APP_SERVER_PORT  = "8080"
    RAG_SERVER_URL   = $ragServerUrl
    RAG_SERVICE_TOKEN = $serviceToken
}
if ($appMode -in @("prod", "production")) {
    $configuredTrustedHosts = @(
        $appTrustedHosts -split "," |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ }
    )
    $trustedHosts = @("localhost", "127.0.0.1", $lanIp) + $configuredTrustedHosts
    $appEnv = $baseAppEnv + @{
        APP_TRUSTED_HOSTS = ($trustedHosts | Sort-Object -Unique) -join ","
    }
}
else {
    $appEnv = $baseAppEnv
}
$appProc = Start-ProcessWithEnv `
    -FilePath $pythonExe `
    -ArgumentList @("-m", "mech_chatbot.api.app_server") `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $appOutLog `
    -RedirectStandardError $appErrLog `
    -Environment $appEnv
$ownedProcesses += $appProc

$ragHealth = Wait-RestMethod -Uri "http://127.0.0.1:8100/health" -TimeoutSeconds 90
& $pythonExe "scripts\ops\production_preflight.py" `
    --health-only `
    --rag-health-url "http://127.0.0.1:8100/health"
if ($LASTEXITCODE -ne 0) {
    throw "RAG readiness khong dat contract production."
}
$appOk = Wait-WebOk -Uri "http://127.0.0.1:8080" -TimeoutSeconds 30
if (!$appOk) {
    throw "App API khong dat readiness. Kiem tra app-api.err.log."
}

Write-Output ("RAG PID: {0}" -f $ragProc.Id)
Write-Output ("Worker PID: {0}" -f $workerProc.Id)
Write-Output ("App API PID: {0}" -f $appProc.Id)
Write-Output ("RAG Health: {0}" -f ($(if ($ragHealth) { ($ragHealth | ConvertTo-Json -Compress) } else { "UNAVAILABLE" })))
Write-Output ("App Ready: {0}" -f $appOk)
Write-Output ("==> LINK DEMO (mo tren cac may cung LAN): http://{0}:8080" -f $lanIp)
}
catch {
    foreach ($ownedProcess in $ownedProcesses) {
        if ($ownedProcess -and !$ownedProcess.HasExited) {
            Stop-Process -Id $ownedProcess.Id -Force -ErrorAction SilentlyContinue
        }
    }
    throw
}
