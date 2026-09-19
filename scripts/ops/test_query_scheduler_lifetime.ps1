param(
    [ValidateSet('Driver', 'Launcher', 'Payload')][string]$Mode = 'Driver',
    [string]$ProofRoot,
    [string]$TaskName,
    [string]$PythonExe
)

# Synthetic, finite lifecycle proof only. Never launches the RAG operator.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$proofBase = Join-Path $projectRoot '.local/query-scheduler-proof'
if ($PythonExe) {
    $PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
    if ($PythonExe -match '["\r\n]' -or -not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        throw 'Invalid proof interpreter.'
    }
}
if ($Mode -eq 'Driver') {
    if ($ProofRoot -or $TaskName) { throw 'Driver chooses its own fresh identity.' }
    $proofId = [guid]::NewGuid().ToString('N')
    $ProofRoot = Join-Path $proofBase $proofId
    $TaskName = "ChatBotProject-Query-LifecycleProof-$proofId"
    New-Item -ItemType Directory -Path $ProofRoot | Out-Null
}
$ProofRoot = [IO.Path]::GetFullPath($ProofRoot)
if (-not $ProofRoot.StartsWith($proofBase + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase) -or
    $TaskName -cnotmatch '^ChatBotProject-Query-LifecycleProof-[a-f0-9]{32}$' -or
    (Split-Path $ProofRoot -Leaf) -cne $TaskName.Substring(36)) {
    throw 'Invalid isolated proof identity.'
}

function Write-ProofJson([string]$Name, [object]$Value) {
    $path = Join-Path $ProofRoot $Name
    $pendingPath = $path + '.writing-' + [guid]::NewGuid().ToString('N')
    $stream = [IO.File]::Open($pendingPath, [IO.FileMode]::CreateNew)
    try {
        $raw = [Text.Encoding]::UTF8.GetBytes(($Value | ConvertTo-Json -Depth 6))
        $stream.Write($raw, 0, $raw.Length)
        $stream.Flush()
    }
    finally { $stream.Dispose() }
    # Publish only closed, complete bytes. File.Move refuses an existing receipt.
    [IO.File]::Move($pendingPath, $path)
}

if ($Mode -eq 'Launcher') {
    Start-ScheduledTask -TaskName $TaskName -TaskPath '\'
    exit 0
}
if ($Mode -eq 'Payload') {
    Write-ProofJson 'started.json' @{
        schema = 'query-scheduler-synthetic-start-v1'
        pid = $PID
        started_at = [DateTimeOffset]::UtcNow.ToString('o')
        user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    }
    if ($PythonExe) {
        $env:PYTHONDONTWRITEBYTECODE = '1'
        $env:RUN_DB_TESTS = '0'
        $env:RUN_QDRANT_TESTS = '0'
        $env:RUN_EVAL_TESTS = '0'
        $env:RUN_QUERY_TASK_PROOF = '0'
        $env:RAG_EXECUTION_CONTEXT = 'test'
        $env:PYTHONPATH = (Join-Path $projectRoot 'src') + ';' + $projectRoot
        $testReport = Join-Path $ProofRoot 'lifecycle-tests.xml'
        & $PythonExe -m pytest tests/unit/test_query_pilot_windows_job.py tests/unit/test_query_pilot_scheduled_host.py `
            -p no:cacheprovider -o 'addopts=' -q --tb=short `
            "--junitxml=$testReport"
        $testExitCode = $LASTEXITCODE
        Write-ProofJson 'lifecycle-tests.json' @{
            exit_code = $testExitCode
            python_sha256 = (Get-FileHash -LiteralPath $PythonExe -Algorithm SHA256).Hash.ToLowerInvariant()
            scope = 'fake processes and offline host tests only'
            provider_calls = 0
        }
        if ($testExitCode -ne 0) { exit 1 }
    }
    Start-Sleep -Seconds 12
    Write-ProofJson 'completed.json' @{
        schema = 'query-scheduler-synthetic-complete-v1'
        pid = $PID
        completed_at = [DateTimeOffset]::UtcNow.ToString('o')
        provider_calls = 0
        pilot_dispatches = 0
    }
    exit 0
}

$powershellExe = Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell/v1.0/powershell.exe'
$scriptPath = $PSCommandPath
$result = [ordered]@{
    schema = 'query-scheduler-lifetime-proof-v1'
    task_name = $TaskName
    proof_root = $ProofRoot
    started_at = [DateTimeOffset]::UtcNow.ToString('o')
    status = 'failed'
    registered = $false
    task_removed = $false
    provider_traffic_authorized = $false
    pilot_dispatch_authorized = $false
    limitations = @('synthetic only', 'interactive logon only', 'no Codex crash or reboot induced')
}
$registered = $false
$launcher = $null
$payloadProcess = $null
try {
    $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}" -Mode Payload -ProofRoot "{1}" -TaskName "{2}"' -f $scriptPath, $ProofRoot, $TaskName
    if ($PythonExe) { $arguments += ' -PythonExe "' + $PythonExe + '"' }
    $action = New-ScheduledTaskAction -Execute $powershellExe -Argument $arguments -WorkingDirectory $projectRoot
    $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
    $proofLimitSeconds = if ($PythonExe) { 180 } else { 45 }
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds $proofLimitSeconds) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $definition = New-ScheduledTask -Action $action -Principal $principal -Settings $settings
    if (@($definition.Triggers | Where-Object { $null -ne $_ }).Count -ne 0 -or $definition.Settings.RestartCount -ne 0) {
        throw 'Proof must not have triggers or automatic retries.'
    }
    Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -InputObject $definition | Out-Null
    $registered = $true
    $result.registered = $true
    $registeredTask = Get-ScheduledTask -TaskName $TaskName -TaskPath '\'
    Write-ProofJson 'task-definition.json' @{
        task_name = $TaskName
        trigger_count = @($registeredTask.Triggers | Where-Object { $null -ne $_ }).Count
        restart_count = $registeredTask.Settings.RestartCount
        logon_type = [string]$registeredTask.Principal.LogonType
        run_level = [string]$registeredTask.Principal.RunLevel
        action = $registeredTask.Actions.Execute
    }
    $launchArguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}" -Mode Launcher -ProofRoot "{1}" -TaskName "{2}"' -f $scriptPath, $ProofRoot, $TaskName
    $launcher = Start-Process -FilePath $powershellExe -ArgumentList $launchArguments -WindowStyle Hidden -PassThru
    $result.launcher_pid = $launcher.Id
    if (-not $launcher.WaitForExit(15000)) { throw 'Launcher timeout.' }
    if ($launcher.ExitCode -ne 0) { throw 'Launcher failed.' }
    $result.launcher_exited_at = [DateTimeOffset]::UtcNow.ToString('o')
    $startDeadline = [DateTimeOffset]::UtcNow.AddSeconds(10)
    while (-not (Test-Path (Join-Path $ProofRoot 'started.json'))) {
        if ([DateTimeOffset]::UtcNow -ge $startDeadline) { throw 'Payload start timeout.' }
        Start-Sleep -Milliseconds 250
    }
    $started = Get-Content (Join-Path $ProofRoot 'started.json') -Raw | ConvertFrom-Json
    $payloadProcess = Get-Process -Id $started.pid
    $result.payload_observed_alive_after_launcher_exit = -not $payloadProcess.HasExited
    $result.payload_observed_at = [DateTimeOffset]::UtcNow.ToString('o')
    $completionSeconds = if ($PythonExe) { 150 } else { 30 }
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($completionSeconds)
    do {
        $task = Get-ScheduledTask -TaskName $TaskName -TaskPath '\'
        if ((Test-Path (Join-Path $ProofRoot 'completed.json')) -and $task.State -ne 'Running') { break }
        Start-Sleep -Milliseconds 250
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    $started = Get-Content (Join-Path $ProofRoot 'started.json') -Raw | ConvertFrom-Json
    $completed = Get-Content (Join-Path $ProofRoot 'completed.json') -Raw | ConvertFrom-Json
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -TaskPath '\'
    $result.task_result = $info.LastTaskResult
    $result.payload_pid = $started.pid
    $result.payload_completed_at = $completed.completed_at
    $result.survived_launcher_exit = (
        $started.pid -eq $completed.pid -and
        [DateTimeOffset]::Parse($completed.completed_at) -gt [DateTimeOffset]::Parse($result.launcher_exited_at)
    )
    $result.payload_exited = $payloadProcess.WaitForExit(5000)
    if ($PythonExe) {
        $testProof = Get-Content -LiteralPath (Join-Path $ProofRoot 'lifecycle-tests.json') -Raw | ConvertFrom-Json
        $result.lifecycle_test_exit_code = $testProof.exit_code
        if ($testProof.exit_code -ne 0) { throw 'Offline lifecycle tests failed inside task.' }
    }
    if (-not $result.survived_launcher_exit -or -not $result.payload_exited -or
        -not $result.payload_observed_alive_after_launcher_exit -or
        $info.LastTaskResult -ne 0 -or $task.State -eq 'Running') {
        throw 'Synthetic lifetime assertions failed.'
    }
    $result.status = 'passed'
}
catch {
    $result.error_type = $_.Exception.GetType().FullName
    $result.error_message = $_.Exception.Message
}
finally {
    if ($null -ne $launcher -and -not $launcher.HasExited) { $launcher.Kill(); $launcher.WaitForExit(5000) | Out-Null }
    if ($registered) {
        $task = Get-ScheduledTask -TaskName $TaskName -TaskPath '\'
        if ($task.State -eq 'Running') { Stop-ScheduledTask -TaskName $TaskName -TaskPath '\' }
        Unregister-ScheduledTask -TaskName $TaskName -TaskPath '\' -Confirm:$false
    }
    $result.task_removed = @(Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue).Count -eq 0
    if (-not $result.task_removed) { $result.status = 'failed' }
    $result.finished_at = [DateTimeOffset]::UtcNow.ToString('o')
    Write-ProofJson 'result.json' $result
}
$result | ConvertTo-Json -Depth 6
if ($result.status -ne 'passed') { exit 1 }
