$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pythonExe = (Resolve-Path -LiteralPath (Join-Path $projectRoot "chat_env\Scripts\python.exe")).Path
$statePath = Join-Path $projectRoot ".agents\state\crag-controlled-demo.json"
if (!(Test-Path -LiteralPath $statePath)) {
    Write-Output "Controlled demo khong co state de stop."
    exit 0
}
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
$ordered = @($state.processes | Sort-Object { if ($_.name -eq "gateway") { 0 } elseif ($_.name -eq "candidate") { 1 } else { 2 } })
foreach ($item in $ordered) {
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
Write-Output "Controlled demo da stop; process state da duoc xoa."
