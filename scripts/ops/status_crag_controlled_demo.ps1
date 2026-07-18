$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$statePath = Join-Path $projectRoot ".agents\state\crag-controlled-demo.json"
if (!(Test-Path -LiteralPath $statePath)) {
    Write-Output "Controlled demo chua duoc start."
    exit 2
}
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
$result = foreach ($item in $state.processes) {
    $process = Get-Process -Id $item.pid -ErrorAction SilentlyContinue
    [pscustomobject]@{
        name = $item.name
        pid = $item.pid
        running = [bool]$process
        started_at = $item.started_at
    }
}
$result | Format-Table -AutoSize
if ($result.running -contains $false) { exit 2 }
