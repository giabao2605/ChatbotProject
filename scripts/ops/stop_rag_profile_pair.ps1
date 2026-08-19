$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
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
$stateProcesses = @($state.processes)
if (
    $stateProcesses.Count -ne 2 -or
    @($stateProcesses | Where-Object {
        $null -eq $_.pid -or
        $null -eq $_.port -or
        $null -eq $_.started_at
    }).Count -ne 0
) {
    throw "State requires exactly two listener entries with listener metadata."
}
$validatedProcesses = @()
$seenNames = @{}
$seenPids = @{}
$seenPorts = @{}
foreach ($item in $stateProcesses) {
    $processName = [string]$item.name
    if ([string]::IsNullOrWhiteSpace($processName)) {
        throw "State listener name cannot be empty."
    }
    $storedPid = 0
    if (![int]::TryParse([string]$item.pid, [ref]$storedPid) -or $storedPid -le 0) {
        throw "State listener PID must be a positive integer."
    }
    $port = 0
    if (![int]::TryParse([string]$item.port, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        throw "State listener port must be an integer from 1 through 65535."
    }
    try {
        $startedAt = [datetimeoffset]::Parse(
            [string]$item.started_at,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        ).UtcDateTime
    }
    catch {
        throw "State listener started_at is invalid."
    }
    if ($seenNames.ContainsKey($processName)) { throw "State listener names must be distinct." }
    if ($seenPids.ContainsKey($storedPid)) { throw "State listener PIDs must be distinct." }
    if ($seenPorts.ContainsKey($port)) { throw "State listener ports must be distinct." }
    $seenNames[$processName] = $true
    $seenPids[$storedPid] = $true
    $seenPorts[$port] = $true
    $validatedProcesses += [pscustomobject]@{
        name = $processName
        pid = $storedPid
        port = $port
        started_at = $startedAt
    }
}
foreach ($entry in $validatedProcesses) {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $entry.port -ErrorAction SilentlyContinue)
    if ($listeners.Count -ne 1) {
        throw "Expected exactly one listener on port $($entry.port)."
    }
    if ([int]$listeners[0].OwningProcess -ne $entry.pid) {
        throw "PID $($entry.pid) does not own port $($entry.port)."
    }
    $process = Get-Process -Id $entry.pid -ErrorAction Stop
    if ($process.ProcessName -notmatch '^python(?:\.exe)?$') {
        throw "PID $($entry.pid) is not the expected Python listener."
    }
    $actualStart = $process.StartTime.ToUniversalTime()
    if ([math]::Abs(($actualStart - $entry.started_at).TotalSeconds) -gt 1) {
        throw "PID $($entry.pid) start time does not match state."
    }
    $cimProcess = Get-CimInstance -ClassName Win32_Process `
        -Filter "ProcessId = $($entry.pid)" -ErrorAction Stop
    if (
        !$cimProcess -or
        [string]$cimProcess.CommandLine -notmatch '(?:^|\s)-m\s+mech_chatbot\.api\.rag_server(?:\s|$)'
    ) {
        throw "PID $($entry.pid) command line does not match mech_chatbot.api.rag_server."
    }
}
foreach ($entry in $validatedProcesses) {
    Stop-Process -Id $entry.pid -ErrorAction Stop
}
$deadline = [datetime]::UtcNow.AddSeconds(15)
do {
    $remainingProcesses = @(
        $validatedProcesses | Where-Object {
            Get-Process -Id $_.pid -ErrorAction SilentlyContinue
        }
    )
    $remainingListeners = @(
        $validatedProcesses | Where-Object {
            @(Get-NetTCPConnection -State Listen -LocalPort $_.port -ErrorAction SilentlyContinue).Count -ne 0
        }
    )
    if ($remainingProcesses.Count -eq 0 -and $remainingListeners.Count -eq 0) { break }
    Start-Sleep -Milliseconds 250
} while ([datetime]::UtcNow -lt $deadline)
if ($remainingProcesses.Count -ne 0) {
    throw "Timed out waiting for RAG profile processes to stop."
}
if ($remainingListeners.Count -ne 0) {
    throw "Timed out waiting for RAG profile ports to stop."
}
foreach ($entry in $validatedProcesses) {
    if (Get-Process -Id $entry.pid -ErrorAction SilentlyContinue) {
        throw "PID $($entry.pid) is still running after stop."
    }
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $entry.port -ErrorAction SilentlyContinue)
    if ($listeners.Count -ne 0) {
        throw "Port $($entry.port) is still listening after stop."
    }
    Write-Output "Da stop $($entry.name) PID $($entry.pid)."
}
Remove-Item -LiteralPath $statePath -ErrorAction Stop
Write-Output "RAG profile pair da stop; process state da duoc xoa."
