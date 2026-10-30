[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,
    [Parameter(Mandatory = $true)]
    [string]$PythonExe,
    [Parameter(Mandatory = $true)]
    [string]$WorkerOutputLog,
    [Parameter(Mandatory = $true)]
    [string]$WorkerErrorLog,
    [Parameter(Mandatory = $true)]
    [string]$ReadyFile,
    [int]$RestartDelaySeconds = 3
)

$ErrorActionPreference = "Stop"

$resolvedProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$hashAlgorithm = [System.Security.Cryptography.SHA256]::Create()
try {
    $rootBytes = [System.Text.Encoding]::UTF8.GetBytes($resolvedProjectRoot.ToLowerInvariant())
    $rootHash = ([System.BitConverter]::ToString($hashAlgorithm.ComputeHash($rootBytes))).Replace("-", "")
}
finally {
    $hashAlgorithm.Dispose()
}

$mutexName = "Local\ChatBotProject.IngestionWorker.$rootHash"
$createdNew = $false
$mutex = [System.Threading.Mutex]::new($false, $mutexName, [ref]$createdNew)
$mutexOwned = $false
if (!$createdNew) {
    Write-Output "An ingestion worker supervisor is already running for $resolvedProjectRoot."
    $mutex.Dispose()
    exit 0
}
$mutex.WaitOne() | Out-Null
$mutexOwned = $true

$workerProcess = $null

function Write-SupervisorLog {
    param([string]$Message)
    $timestamp = [DateTimeOffset]::Now.ToString("o")
    Write-Output "$timestamp $Message"
}

try {
    $restartDelay = [Math]::Max(1, $RestartDelaySeconds)
    while ($true) {
        $workerProcess = Start-Process `
            -FilePath $PythonExe `
            -ArgumentList @("run_worker.py") `
            -WorkingDirectory $resolvedProjectRoot `
            -RedirectStandardOutput $WorkerOutputLog `
            -RedirectStandardError $WorkerErrorLog `
            -WindowStyle Hidden `
            -PassThru

        # Cache the process handle so Windows PowerShell retains the exit code.
        $null = $workerProcess.Handle
        Write-SupervisorLog ("started worker pid={0}" -f $workerProcess.Id)
        try {
            $workerProcess.WaitForExit()
        }
        catch {
            # A very short-lived process can disappear before Wait-Process resolves its PID.
            Wait-Process -InputObject $workerProcess -ErrorAction SilentlyContinue
        }
        $exitCode = $workerProcess.ExitCode
        if ($exitCode -eq 73) {
            Write-SupervisorLog "Another ingestion worker owns this host; supervisor stopping."
            exit 73
        }
        Write-SupervisorLog ("worker pid={0} exited code={1}; restarting in {2}s" -f $workerProcess.Id, $exitCode, $restartDelay)
        Start-Sleep -Seconds $restartDelay
    }
}
finally {
    if ($workerProcess -and !$workerProcess.HasExited) {
        Stop-Process -Id $workerProcess.Id -Force -ErrorAction SilentlyContinue
    }
    if ($mutexOwned) {
        try {
            $mutex.ReleaseMutex()
        }
        catch [System.Threading.SynchronizationLockException] {
        }
    }
    $mutex.Dispose()
}
