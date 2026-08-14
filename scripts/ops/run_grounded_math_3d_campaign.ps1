[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [Parameter(Mandatory = $true)][string]$ToolingRoot,
    [Parameter(Mandatory = $true)][string]$CampaignRoot,
    [Parameter(Mandatory = $true)][string]$WindowPath,
    [Parameter(Mandatory = $true)][string]$StatePath,
    [Parameter(Mandatory = $true)][string]$HealthPath,
    [Parameter(Mandatory = $true)][string]$LiveHealthPath,
    [Parameter(Mandatory = $true)][string]$TracePath,
    [Parameter(Mandatory = $true)][string]$ProviderSmokePath,
    [Parameter(Mandatory = $true)][string]$BaseGatePath,
    [Parameter(Mandatory = $true)][string]$ReleaseDecisionsPath,
    [Parameter(Mandatory = $true)][string]$DotenvPath,
    [Parameter(Mandatory = $true)][string]$TaskName,
    [Parameter(Mandatory = $true)][string]$StopMarkerPath
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$boundPaths = @(
    $PythonPath,
    $ToolingRoot,
    $CampaignRoot,
    $WindowPath,
    $StatePath,
    $HealthPath,
    $LiveHealthPath,
    $TracePath,
    $ProviderSmokePath,
    $BaseGatePath,
    $ReleaseDecisionsPath,
    $DotenvPath,
    $StopMarkerPath
)
if ($boundPaths.Where({ -not [IO.Path]::IsPathFullyQualified($_) }).Count -ne 0) {
    throw 'all_paths_must_be_absolute'
}
$resolvedCampaignRoot = [IO.Path]::GetFullPath($CampaignRoot).TrimEnd('\')
$resolvedHealthPath = [IO.Path]::GetFullPath($HealthPath)
$resolvedLiveHealthPath = [IO.Path]::GetFullPath($LiveHealthPath)
if (
    $resolvedLiveHealthPath.Equals(
        $resolvedHealthPath,
        [StringComparison]::OrdinalIgnoreCase
    ) -or
    -not [IO.Path]::GetDirectoryName($resolvedLiveHealthPath).Equals(
        $resolvedCampaignRoot,
        [StringComparison]::OrdinalIgnoreCase
    ) -or
    -not [IO.Path]::GetFileName($resolvedLiveHealthPath).Equals(
        'live-health.json',
        [StringComparison]::OrdinalIgnoreCase
    )
) {
    throw 'live_health_path_invalid'
}
if (Test-Path -LiteralPath $StopMarkerPath) {
    exit 0
}
$env:PYTHONPATH = (Join-Path $ToolingRoot 'src') + ';' + $ToolingRoot

$pilotGate = Join-Path $ToolingRoot 'scripts\ops\grounded_math_pilot_gate.py'
$operatorTraffic = Join-Path $ToolingRoot 'scripts\ops\grounded_math_operator_traffic.py'
$operatorGate = Join-Path $CampaignRoot 'operator-gate.json'
$requiredFiles = @(
    $PythonPath,
    $pilotGate,
    $operatorTraffic,
    $WindowPath,
    $StatePath,
    $HealthPath,
    $TracePath,
    $ProviderSmokePath,
    $ReleaseDecisionsPath,
    $DotenvPath
)
if ($requiredFiles.Where({ -not (Test-Path -LiteralPath $_ -PathType Leaf) }).Count -ne 0) {
    throw 'required_campaign_file_missing'
}

try {
    Push-Location -LiteralPath $ToolingRoot
    try {
        & $PythonPath $operatorTraffic capture-health `
            --root $CampaignRoot `
            --release-decisions $ReleaseDecisionsPath `
            --output $LiveHealthPath `
            --dotenv $DotenvPath
        if ($LASTEXITCODE -ne 0) {
            throw 'health_capture_failed'
        }

        & $PythonPath $pilotGate `
            --window $WindowPath `
            --state $StatePath `
            --health-capture $LiveHealthPath `
            --trace $TracePath `
            --provider-smoke $ProviderSmokePath `
            --output $BaseGatePath
        if ($LASTEXITCODE -notin @(0, 2)) {
            throw 'canonical_gate_refresh_failed'
        }

        & $PythonPath $operatorTraffic run-due `
            --root $CampaignRoot `
            --base-gate $BaseGatePath `
            --live-health $LiveHealthPath `
            --release-decisions $ReleaseDecisionsPath `
            --dotenv $DotenvPath
        if ($LASTEXITCODE -ne 0) {
            throw 'operator_dispatch_failed'
        }

        & $PythonPath $pilotGate `
            --window $WindowPath `
            --state $StatePath `
            --health-capture $LiveHealthPath `
            --trace $TracePath `
            --provider-smoke $ProviderSmokePath `
            --output $BaseGatePath
        if ($LASTEXITCODE -notin @(0, 2)) {
            throw 'canonical_gate_reconciliation_failed'
        }

        & $PythonPath $operatorTraffic gate `
            --root $CampaignRoot `
            --base-gate $BaseGatePath `
            --release-decisions $ReleaseDecisionsPath `
            --output $operatorGate
        if ($LASTEXITCODE -notin @(0, 2)) {
            throw 'operator_gate_refresh_failed'
        }
        $operatorGateValue = Get-Content -Raw -LiteralPath $operatorGate |
            ConvertFrom-Json
        if ($operatorGateValue.passed -eq $true) {
            if (-not (Test-Path -LiteralPath $StopMarkerPath)) {
                New-Item -ItemType File -Path $StopMarkerPath | Out-Null
            }
            Disable-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue |
                Out-Null
        }
    }
    finally {
        Pop-Location
    }
}
catch {
    if (-not (Test-Path -LiteralPath $StopMarkerPath)) {
        New-Item -ItemType File -Path $StopMarkerPath | Out-Null
    }
    Disable-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null
    exit 1
}
