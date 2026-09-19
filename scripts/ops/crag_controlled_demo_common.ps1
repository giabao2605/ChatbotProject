function Wait-CragDemoHttpHealth {
    param(
        [string]$Url,
        [int]$Attempts,
        [string]$FailureMessage,
        [string]$ServiceToken,
        [ValidateSet("live", "evaluation", "controlled_demo", "default_rollout")]
        [string]$ActivationScope = "live"
    )
    $headers = @{}
    if (![string]::IsNullOrWhiteSpace($ServiceToken)) {
        $headers["X-RAG-Service-Token"] = $ServiceToken
    }
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri $Url -TimeoutSec 5 -Headers $headers
            $status = [string]$health.status
            $scope = [string]$health.activation_scope
            $context = [string]$health.execution_context
            $ragLoaded = $health.rag_loaded -is [bool] -and $health.rag_loaded -eq $true
            $activationValid = $health.activation_valid -is [bool] -and $health.activation_valid -eq $true
            $liveAuthorized = $health.live_authorized -is [bool] -and $health.live_authorized -eq $true
            $notLiveAuthorized = $health.live_authorized -is [bool] -and $health.live_authorized -eq $false
            $evaluationReady = (
                $ActivationScope -eq "evaluation" -and
                $status -eq "degraded" -and
                $ragLoaded -and
                $activationValid -and
                $notLiveAuthorized -and
                $scope -eq "evaluation" -and
                $context -eq "evaluation"
            )
            $liveReady = (
                $ActivationScope -in @("controlled_demo", "default_rollout") -and
                $status -eq "ok" -and
                $ragLoaded -and
                $activationValid -and
                $liveAuthorized -and
                $scope -eq $ActivationScope -and
                $context -eq "production"
            )
            $legacyReady = (
                $ActivationScope -eq "live" -and
                $status -eq "ok"
            )
            if ($evaluationReady -or $liveReady -or $legacyReady) { return }
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
            if ($null -eq $saved[$key]) {
                Remove-Item -LiteralPath "Env:$key" -ErrorAction SilentlyContinue
            }
            else {
                [Environment]::SetEnvironmentVariable($key, $saved[$key], "Process")
            }
        }
    }
}
