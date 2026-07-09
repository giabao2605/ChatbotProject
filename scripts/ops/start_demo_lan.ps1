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
if (!$ragServerUrl) { $ragServerUrl = "http://127.0.0.1:8100" }
if (!$serviceToken) { $serviceToken = "" }
if (!$sessionSecret -and !$bridgeSecret -and !$serviceToken) {
    throw "Thieu APP_SESSION_SECRET, CHAT_BRIDGE_SECRET hoac RAG_SERVICE_TOKEN trong .env. Tao bang: python -c ""import secrets; print(secrets.token_urlsafe(48))"""
}

function Stop-ProcessesOnPort {
    param([int[]]$Ports)
    foreach ($port in $Ports) {
        $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        if ($listeners) {
            $pids = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
            foreach ($processId in $pids) {
                Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

function Stop-ProcessesByCommandLine {
    param([string[]]$Patterns)
    $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
    foreach ($proc in $procs) {
        if ($proc.ProcessId -eq $PID) { continue }
        if (-not $proc.CommandLine) { continue }
        foreach ($pattern in $Patterns) {
            if ($proc.CommandLine -match $pattern) {
                Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
                break
            }
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

Stop-ProcessesOnPort -Ports @(8100, 8080)
Stop-ProcessesByCommandLine -Patterns @('mech_chatbot\.api\.rag_server', 'mech_chatbot\.api\.app_server', 'run_worker\.py')
Start-Sleep -Seconds 2

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

$ragProc = Start-ProcessWithEnv `
    -FilePath $pythonExe `
    -ArgumentList @("-m", "mech_chatbot.api.rag_server") `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $ragOutLog `
    -RedirectStandardError $ragErrLog `
    -Environment @{ PYTHONPATH = "src" }

$workerProc = Start-ProcessWithEnv `
    -FilePath $pythonExe `
    -ArgumentList @("run_worker.py") `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $workerOutLog `
    -RedirectStandardError $workerErrLog `
    -Environment @{ PYTHONPATH = "src" }

$appEnv = @{
    PYTHONPATH       = "src"
    APP_SERVER_HOST  = "0.0.0.0"
    APP_SERVER_PORT  = "8080"
    RAG_SERVER_URL   = $ragServerUrl
    RAG_SERVICE_TOKEN = $serviceToken
}
$appProc = Start-ProcessWithEnv `
    -FilePath $pythonExe `
    -ArgumentList @("-m", "mech_chatbot.api.app_server") `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $appOutLog `
    -RedirectStandardError $appErrLog `
    -Environment $appEnv

$ragHealth = Wait-RestMethod -Uri "http://127.0.0.1:8100/health" -TimeoutSeconds 90
$appOk = Wait-WebOk -Uri "http://127.0.0.1:8080" -TimeoutSeconds 30

Write-Output ("RAG PID: {0}" -f $ragProc.Id)
Write-Output ("Worker PID: {0}" -f $workerProc.Id)
Write-Output ("App API PID: {0}" -f $appProc.Id)
Write-Output ("RAG Health: {0}" -f ($(if ($ragHealth) { ($ragHealth | ConvertTo-Json -Compress) } else { "UNAVAILABLE" })))
Write-Output ("App Ready: {0}" -f $appOk)
Write-Output ("==> LINK DEMO (mo tren cac may cung LAN): http://{0}:8080" -f $lanIp)
