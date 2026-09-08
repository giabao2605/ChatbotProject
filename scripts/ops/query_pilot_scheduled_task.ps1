<##
Prepare/inspect by default. -Register creates one on-demand, current-user task;
-Register -Start also starts it. No triggers, retries, restart or catch-up.
Interactive logon is required throughout. Logoff/reboot are terminal, not resume.
Credentials must be readable by the task user through the application's settings
loader (dotenv plus environment overrides). The host passes the resolved service
token only in child memory; never in its action, arguments, packet, or task XML.
##>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Packet,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{64}$')][string]$PacketSha256,
    [switch]$Register,
    [switch]$Start
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$phase = 'packet_read'

try {
    # A parent PowerShell edition can supply an incompatible PSModulePath.
    # Resolve platform cmdlets from this interpreter, never an ambient module.
    Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Management/Microsoft.PowerShell.Management.psd1') -ErrorAction Stop
    Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
    if ($Start -and -not $Register) { throw 'start_requires_register' }
    $packetPath = (Resolve-Path -LiteralPath $Packet).Path
    if ((Get-FileHash -LiteralPath $packetPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $PacketSha256) {
        throw 'packet_drift'
    }
    $phase = 'packet_parse'
    $value = Get-Content -LiteralPath $packetPath -Raw | ConvertFrom-Json
    $sourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
    $hostScript = Join-Path $PSScriptRoot 'query_pilot_scheduled_host.py'
    $pythonExe = (Resolve-Path -LiteralPath $value.operator.python_exe).Path
    if ([IO.Path]::GetFullPath($value.operator.source_root) -ne $sourceRoot) {
        throw 'source_root_mismatch'
    }
    $phase = 'executable_binding'
    foreach ($binding in @(
        @{ Path = $pythonExe; Hash = $value.interpreter_sha256 },
        @{ Path = $hostScript; Hash = $value.host_sha256 },
        @{ Path = $PSCommandPath; Hash = $value.task_script_sha256 },
        @{ Path = (Join-Path $PSScriptRoot 'query_pilot_windows_job.py'); Hash = $value.job_sha256 }
    )) {
        if ((Get-FileHash -LiteralPath $binding.Path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $binding.Hash) {
            throw 'executable_drift'
        }
    }
    foreach ($path in @($hostScript, $packetPath, $pythonExe, $sourceRoot)) {
        if ($path -match '["\r\n]') { throw 'task_path_invalid' }
    }
    $phase = 'python_validation'
    $validation = & $pythonExe $hostScript validate --packet $packetPath --packet-sha256 $PacketSha256
    if ($LASTEXITCODE -ne 0) { throw 'packet_validation_failed' }
    $checked = $validation | ConvertFrom-Json
    if ($checked.status -ne 'validated' -or $checked.python_exe -ne $pythonExe) {
        throw 'packet_validation_failed'
    }
    $taskName = 'ChatBotProject-Query-Governed-' + $PacketSha256.Substring(0, 32)
    if (-not $Register) {
        @{ status = 'prepared'; task_name = $taskName; task_registered = $false;
           runtime_started = $false; interactive_only = $true } | ConvertTo-Json -Compress
        exit 0
    }
    $phase = 'task_registration'
    $scheduledTasksModule = Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell/v1.0/Modules/ScheduledTasks/ScheduledTasks.psd1'
    Import-Module $scheduledTasksModule -ErrorAction Stop
    if (Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue) {
        throw 'task_already_exists'
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    $arguments = '"' + $hostScript + '" run --packet "' + $packetPath + '" --packet-sha256 ' + $PacketSha256
    $action = New-ScheduledTaskAction -Execute $pythonExe -Argument $arguments -WorkingDirectory $sourceRoot
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 0 `
        -ExecutionTimeLimit (New-TimeSpan -Hours 26) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $definition = New-ScheduledTask -Action $action -Principal $principal -Settings $settings
    Register-ScheduledTask -TaskName $taskName -TaskPath '\' -InputObject $definition | Out-Null
    if ($Start) { Start-ScheduledTask -TaskName $taskName -TaskPath '\' }
    @{ status = 'registered'; task_name = $taskName; task_registered = $true;
       start_requested = [bool]$Start; interactive_only = $true;
       retry_authorized = $false; catch_up_authorized = $false } | ConvertTo-Json -Compress
} catch {
    # Do not echo exception content, provider settings, or packet fields.
    $reason = 'scheduled_task_rejected'
    $safeReasons = @('start_requires_register', 'packet_drift', 'source_root_mismatch',
        'executable_drift', 'task_path_invalid', 'packet_validation_failed', 'task_already_exists')
    if ($safeReasons -contains $_.Exception.Message) { $reason = $_.Exception.Message }
    @{ status = 'terminal_failure'; reason = $reason; phase = $phase;
       error_type = $_.Exception.GetType().Name; retry_authorized = $false } | ConvertTo-Json -Compress
    exit 1
}
