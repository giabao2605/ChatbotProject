$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pythonExe = (Resolve-Path -LiteralPath (Join-Path $projectRoot "chat_env\Scripts\python.exe")).Path
$statePath = Join-Path $projectRoot ".agents\state\rag-profile-pair.json"
if (!(Test-Path -LiteralPath $statePath)) {
    Write-Output "RAG profile pair khong co state de stop."
    exit 0
}
$rawState = Get-Content -Raw -LiteralPath $statePath
$convertFromJson = Get-Command ConvertFrom-Json
if ($convertFromJson.Parameters.ContainsKey("DateKind")) {
    $state = $rawState | ConvertFrom-Json -DateKind String
} else {
    Add-Type -AssemblyName System.Web.Extensions
    $serializer = New-Object System.Web.Script.Serialization.JavaScriptSerializer
    $state = $serializer.DeserializeObject($rawState)
}
foreach ($item in $state.processes) {
    $process = Get-Process -Id $item.pid -ErrorAction SilentlyContinue
    if (!$process) { continue }
    if ($process.Path -ne $pythonExe) {
        throw "PID $($item.pid) khong con la Python process da start; tu choi stop."
    }
    $expectedStart = [datetimeoffset]::Parse(
        [string]$item.started_at,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind
    ).UtcDateTime
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
