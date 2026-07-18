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
