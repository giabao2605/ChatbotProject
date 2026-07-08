$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $projectRoot

$pythonExe = Join-Path $projectRoot "chat_env\Scripts\python.exe"
$streamlitExe = Join-Path $projectRoot "chat_env\Scripts\streamlit.exe"
$chatUiDir = Join-Path $projectRoot "chat-ui"
$logsDir = Join-Path $projectRoot "logs"
$ragOutLog = Join-Path $logsDir "rag-server.out.log"
$ragErrLog = Join-Path $logsDir "rag-server.err.log"
$uiOutLog = Join-Path $logsDir "streamlit.out.log"
$uiErrLog = Join-Path $logsDir "streamlit.err.log"
$chatOutLog = Join-Path $logsDir "chat-ui.out.log"
$chatErrLog = Join-Path $logsDir "chat-ui.err.log"
$workerOutLog = Join-Path $logsDir "worker.out.log"
$workerErrLog = Join-Path $logsDir "worker.err.log"

if (!(Test-Path $pythonExe)) {
    throw "Khong tim thay chat_env\Scripts\python.exe. Hay tao/khai bao dung virtualenv truoc khi chay demo."
}
if (!(Test-Path $streamlitExe)) {
    throw "Khong tim thay chat_env\Scripts\streamlit.exe. Hay cai dependency cho virtualenv truoc khi chay demo."
}
if (!(Test-Path $chatUiDir)) {
    throw "Khong tim thay thu muc chat-ui. Hay copy app Next.js vao '$chatUiDir', roi chay 'npm install' va 'npm run build' truoc."
}
if (!(Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir | Out-Null
}

# --- Doc bien can thiet tu file .env chinh (nguon su that duy nhat) ---
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
$bridgeSecret = Get-DotEnvValue -Path $envPath -Key "CHAT_BRIDGE_SECRET"
$ragServerUrl = Get-DotEnvValue -Path $envPath -Key "RAG_SERVER_URL"
if (!$ragServerUrl) { $ragServerUrl = "http://127.0.0.1:8100" }
if (!$serviceToken) { $serviceToken = "" }
if (!$bridgeSecret) {
    throw "Thieu CHAT_BRIDGE_SECRET trong .env. Hay them mot chuoi bi mat dai, vd tao bang: python -c ""import secrets; print(secrets.token_hex(32))"""
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
    # Kill cac tien trinh theo noi dung dong lenh (vd worker khong nghe cong nao).
    # Vi du pattern (regex): 'run_worker\.py', 'run_server\.py', '\brun\.py\b'
    param([string[]]$Patterns)
    $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
    foreach ($proc in $procs) {
        if ($proc.ProcessId -eq $PID) { continue }        # khong tu kill chinh script nay
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
    param(
        [string]$Uri,
        [int]$TimeoutSeconds
    )

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
    param(
        [string]$Uri,
        [int]$TimeoutSeconds
    )

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

# --- Don sach: kill theo cong VA theo dong lenh (bat ca worker khong nghe cong) ---
Stop-ProcessesOnPort -Ports @(8100, 8501, 3000)
Stop-ProcessesByCommandLine -Patterns @('run_worker\.py', 'run_server\.py', '\brun\.py\b')
Start-Sleep -Seconds 2

# --- Xac dinh IP LAN cua may host (tranh bay localhost) ---
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
$chatUiBaseUrl = "http://{0}:3000" -f $lanIp

# --- 1. RAG server (cong 8100) ---
$ragEnv = @{ PYTHONPATH = "src" }
$ragProc = Start-ProcessWithEnv `
    -FilePath $pythonExe `
    -ArgumentList @("-m", "mech_chatbot.api.rag_server") `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $ragOutLog `
    -RedirectStandardError $ragErrLog `
    -Environment $ragEnv

# --- 1b. Ingestion worker (chay nen, KHONG nghe cong) ---
$workerEnv = @{ PYTHONPATH = "src" }
$workerProc = Start-ProcessWithEnv `
    -FilePath $pythonExe `
    -ArgumentList @("run_worker.py") `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $workerOutLog `
    -RedirectStandardError $workerErrLog `
    -Environment $workerEnv

# --- 2. Next.js chat UI (cong 3000, mo ra LAN) ---
$chatEnv = @{
    RAG_SERVER_URL     = $ragServerUrl
    RAG_SERVICE_TOKEN  = $serviceToken
    CHAT_BRIDGE_SECRET = $bridgeSecret
    PORT               = "3000"
    HOSTNAME           = "0.0.0.0"
}
$chatProc = Start-ProcessWithEnv `
    -FilePath "npm.cmd" `
    -ArgumentList @("run", "start") `
    -WorkingDirectory $chatUiDir `
    -RedirectStandardOutput $chatOutLog `
    -RedirectStandardError $chatErrLog `
    -Environment $chatEnv

# --- 3. Streamlit (cong 8501, mo ra LAN, bat che do nhung chat Next.js) ---
$uiEnv = @{
    PYTHONPATH         = "src"
    USE_NEXTJS_CHAT    = "1"
    CHAT_UI_BASE_URL   = $chatUiBaseUrl
    CHAT_BRIDGE_SECRET = $bridgeSecret
}
$uiProc = Start-ProcessWithEnv `
    -FilePath $streamlitExe `
    -ArgumentList @("run", "run.py", "--server.port", "8501", "--server.address", "0.0.0.0") `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $uiOutLog `
    -RedirectStandardError $uiErrLog `
    -Environment $uiEnv

$ragHealth = Wait-RestMethod -Uri "http://127.0.0.1:8100/health" -TimeoutSeconds 90
$chatOk = Wait-WebOk -Uri "http://127.0.0.1:3000" -TimeoutSeconds 30
$uiOk = Wait-WebOk -Uri "http://127.0.0.1:8501" -TimeoutSeconds 30

Write-Output ("RAG PID: {0}" -f $ragProc.Id)
Write-Output ("Worker PID: {0}" -f $workerProc.Id)
Write-Output ("Chat UI PID: {0}" -f $chatProc.Id)
Write-Output ("Streamlit PID: {0}" -f $uiProc.Id)
Write-Output ("RAG Health: {0}" -f ($(if ($ragHealth) { ($ragHealth | ConvertTo-Json -Compress) } else { "UNAVAILABLE" })))
Write-Output ("Chat UI Ready: {0}" -f $chatOk)
Write-Output ("Streamlit UI Ready: {0}" -f $uiOk)
Write-Output ("Chat UI Base URL (dung cho iframe): {0}" -f $chatUiBaseUrl)
Write-Output ("==> LINK DEMO (mo tren cac may cung LAN): http://{0}:8501" -f $lanIp)