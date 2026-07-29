$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pythonExe = (Resolve-Path -LiteralPath (Join-Path $projectRoot "chat_env\Scripts\python.exe")).Path
$statePath = Join-Path $projectRoot ".agents\state\rag-profile-pair.json"
if (!(Test-Path -LiteralPath $statePath)) {
    Write-Output "RAG profile pair khong co state de stop."
    exit 0
}
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
foreach ($item in $state.processes) {
    $process = Get-Process -Id $item.pid -ErrorAction SilentlyContinue
    if (!$process) { continue }
    if ($process.Path -ne $pythonExe) {
        throw "PID $($item.pid) khong con la Python process da start; tu choi stop."
    }
    $expectedStart = [datetime]::Parse([string]$item.started_at).ToUniversalTime()
    $actualStart = $process.StartTime.ToUniversalTime()
    if ([math]::Abs(($actualStart - $expectedStart).TotalSeconds) -gt 1) {
        throw "PID $($item.pid) da bi process khac tai su dung; tu choi stop."
    }
    Stop-Process -Id $item.pid
    Wait-Process -Id $item.pid -Timeout 15 -ErrorAction SilentlyContinue
    Write-Output "Da stop $($item.name) PID $($item.pid)."
}
Remove-Item -LiteralPath $statePath
Write-Output "RAG profile pair da stop; process state da duoc xoa."
