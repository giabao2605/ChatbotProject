param([string]$Packet, [string]$PacketSha256, [string]$Wrapper, [switch]$Start)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Management/Microsoft.PowerShell.Management.psd1')
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1')
Import-Module (Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell/v1.0/Modules/ScheduledTasks/ScheduledTasks.psd1')
if ($PacketSha256 -notmatch '^[a-f0-9]{64}$') { throw 'invalid_proof_digest' }
if ($Start) {
    $fixtureHost = Join-Path (Split-Path $Wrapper -Parent) 'query_pilot_scheduled_host.py'
    if (-not (Get-Content -LiteralPath $fixtureHost -Raw).Contains('"""Inserted only into a temporary host before its synthetic Git freeze.')) {
        throw 'synthetic_transport_stub_required'
    }
    $handoffMarker = Join-Path ([IO.Path]::GetFullPath((Join-Path (Split-Path $Wrapper -Parent) '../..'))) '.local/scheduled-handoff.json'
    if (Test-Path -LiteralPath $handoffMarker) { throw 'proof_handoff_marker_not_fresh' }
}
$taskName = 'ChatBotProject-Query-Governed-' + $PacketSha256.Substring(0, 32)
if (Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue) {
    throw 'proof_task_already_exists'
}
# Start is used only with the fixture's finite transport stub, never a RAG host.
# The exact digest-derived name was absent before entering this owned scope.
try {
    $startArgs = @()
    if ($Start) { $startArgs = @('-Start') }
    $output = & (Join-Path $PSHOME 'powershell.exe') -NoProfile -NonInteractive -File $Wrapper -Packet $Packet -PacketSha256 $PacketSha256 -Register @startArgs
    if ($LASTEXITCODE -ne 0) { throw ('wrapper_registration_failed: ' + $output) }
    $registered = $output | ConvertFrom-Json
    if (-not $registered.task_registered -or $registered.start_requested -ne [bool]$Start) { throw 'unexpected_registration_status' }
    $taskResult = $null
    if ($Start) {
        $handoffTimer = [Diagnostics.Stopwatch]::StartNew()
        do {
            $task = Get-ScheduledTask -TaskName $taskName -TaskPath '\'
            $info = Get-ScheduledTaskInfo -TaskName $taskName -TaskPath '\'
            if ((Test-Path -LiteralPath $handoffMarker) -and $task.State -ne 'Running') { break }
            Start-Sleep -Milliseconds 100
        } while ($handoffTimer.Elapsed.TotalSeconds -lt 20)
        if (-not (Test-Path -LiteralPath $handoffMarker) -or $task.State -eq 'Running') {
            throw ('proof_handoff_timeout state={0} result={1} last_run={2:o}' -f
                $task.State, $info.LastTaskResult, $info.LastRunTime)
        }
        $taskResult = $info.LastTaskResult
    }
    $xml = Export-ScheduledTask -TaskName $taskName -TaskPath '\'
    $task = Get-ScheduledTask -TaskName $taskName -TaskPath '\'
    @{ task_name = $taskName; xml = $xml; task_started = [bool]$Start; task_result = $taskResult;
       run_level = [string]$task.Principal.RunLevel;
       restart_count = $task.Settings.RestartCount } | ConvertTo-Json -Compress
} finally {
    $owned = Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue
    if ($owned) {
        if ($owned.State -eq 'Running') { Stop-ScheduledTask -TaskName $taskName -TaskPath '\' }
        Unregister-ScheduledTask -TaskName $taskName -TaskPath '\' -Confirm:$false
    }
    if (Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue) {
        throw 'proof_task_cleanup_failed'
    }
}
