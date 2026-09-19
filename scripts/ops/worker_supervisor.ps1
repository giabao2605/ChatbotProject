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

$resolvedReadyFile = [System.IO.Path]::GetFullPath($ReadyFile)
$workerProcess = $null

function Remove-ReadinessMarker {
    if (Test-Path -LiteralPath $resolvedReadyFile) {
        Remove-Item -LiteralPath $resolvedReadyFile -Force -ErrorAction SilentlyContinue
    }
}

function Write-SupervisorLog {
    param([string]$Message)
    $timestamp = [DateTimeOffset]::Now.ToString("o")
    Write-Output "$timestamp $Message"
}

try {
    $restartDelay = [Math]::Max(1, $RestartDelaySeconds)
    while ($true) {
        Remove-ReadinessMarker
        $workerProcess = Start-Process `
            -FilePath $PythonExe `
            -ArgumentList @("run_worker.py") `
            -WorkingDirectory $resolvedProjectRoot `
            -RedirectStandardOutput $WorkerOutputLog `
            -RedirectStandardError $WorkerErrorLog `
            -WindowStyle Hidden `
            -PassThru

        Write-SupervisorLog ("started worker pid={0}" -f $workerProcess.Id)
        try {
            $workerProcess.WaitForExit()
        }
        catch {
            # A very short-lived process can disappear before Wait-Process resolves its PID.
            Wait-Process -InputObject $workerProcess -ErrorAction SilentlyContinue
        }
        $exitCode = $workerProcess.ExitCode
        Remove-ReadinessMarker
        Write-SupervisorLog ("worker pid={0} exited code={1}; restarting in {2}s" -f $workerProcess.Id, $exitCode, $restartDelay)
        Start-Sleep -Seconds $restartDelay
    }
}
finally {
    Remove-ReadinessMarker
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
