function Wait-CragDemoHttpHealth {
    param(
        [string]$Url,
        [int]$Attempts,
        [string]$FailureMessage
    )
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri $Url -TimeoutSec 5
            if ($health.status -eq "ok") { return }
        }
        catch {
        }
        if ($attempt -eq $Attempts) { throw $FailureMessage }
        Start-Sleep -Seconds 2
    }
}


function Start-CragDemoProcess {
    param(
        [string]$PythonExe,
        [string]$ProjectRoot,
        [string]$Name,
        [hashtable]$Environment,
        [string]$Module,
        [string]$OutLog,
        [string]$ErrLog
    )
    $saved = @{}
    foreach ($key in $Environment.Keys) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
        [Environment]::SetEnvironmentVariable($key, [string]$Environment[$key], "Process")
    }
    try {
        $process = Start-Process -FilePath $PythonExe `
            -ArgumentList @("-m", $Module) `
            -WorkingDirectory $ProjectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $OutLog `
            -RedirectStandardError $ErrLog `
            -PassThru
        return @{
            name = $Name
            pid = $process.Id
            started_at = $process.StartTime.ToUniversalTime().ToString("o")
        }
    }
    finally {
        foreach ($key in $Environment.Keys) {
            [Environment]::SetEnvironmentVariable($key, $saved[$key], "Process")
        }
    }
}
