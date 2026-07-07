$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $projectRoot

$pythonExe = Join-Path $projectRoot "chat_env\Scripts\python.exe"
$streamlitExe = Join-Path $projectRoot "chat_env\Scripts\streamlit.exe"
$logsDir = Join-Path $projectRoot "logs"
$ragOutLog = Join-Path $logsDir "rag-server.out.log"
$ragErrLog = Join-Path $logsDir "rag-server.err.log"
$uiOutLog = Join-Path $logsDir "streamlit.out.log"
$uiErrLog = Join-Path $logsDir "streamlit.err.log"

if (!(Test-Path $pythonExe)) {
    throw "Khong tim thay chat_env\Scripts\python.exe. Hay tao/khai bao dung virtualenv truoc khi chay demo."
}

if (!(Test-Path $streamlitExe)) {
    throw "Khong tim thay chat_env\Scripts\streamlit.exe. Hay cai dependency cho virtualenv truoc khi chay demo."
}

if (!(Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir | Out-Null
}

function Stop-ProcessesOnPort {
    param(
        [int[]]$Ports
    )

    foreach ($port in $Ports) {
        $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        if ($listeners) {
            $pids = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
            foreach ($pid in $pids) {
                Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

Stop-ProcessesOnPort -Ports @(8100, 8501)
Start-Sleep -Seconds 2

$envBlock = @{
    PYTHONPATH = "src"
}

$ragProc = Start-Process `
    -FilePath $pythonExe `
    -ArgumentList @("-m", "mech_chatbot.api.rag_server") `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $ragOutLog `
    -RedirectStandardError $ragErrLog `
    -WindowStyle Hidden `
    -PassThru `
    -Environment $envBlock

$uiProc = Start-Process `
    -FilePath $streamlitExe `
    -ArgumentList @("run", "run.py", "--server.port", "8501", "--server.address", "0.0.0.0") `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $uiOutLog `
    -RedirectStandardError $uiErrLog `
    -WindowStyle Hidden `
    -PassThru `
    -Environment $envBlock

Start-Sleep -Seconds 15

$ragHealth = $null
try {
    $ragHealth = Invoke-RestMethod -Uri "http://127.0.0.1:8100/health" -TimeoutSec 10
} catch {
    $ragHealth = $null
}

$uiOk = $false
try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8501" -UseBasicParsing -TimeoutSec 10
    $uiOk = ($resp.StatusCode -eq 200)
} catch {
    $uiOk = $false
}

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
} catch {
    $lanIp = $null
}

Write-Output ("RAG PID: {0}" -f $ragProc.Id)
Write-Output ("Streamlit PID: {0}" -f $uiProc.Id)
Write-Output ("RAG Health: {0}" -f ($(if ($ragHealth) { ($ragHealth | ConvertTo-Json -Compress) } else { "UNAVAILABLE" })))
Write-Output ("UI Ready: {0}" -f $uiOk)
if ($lanIp) {
    Write-Output ("Link LAN: http://{0}:8501" -f $lanIp)
} else {
    Write-Output "Khong tu dong xac dinh duoc IP LAN. Chay ipconfig de lay IPv4 cua may host."
}
