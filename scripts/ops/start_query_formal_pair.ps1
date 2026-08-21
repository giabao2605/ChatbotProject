[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PythonPath,

    [Parameter(Mandatory = $true)]
    [string]$Manifest,

    [Parameter(Mandatory = $true)]
    [string]$OutputDir,

    [Parameter(Mandatory = $true)]
    [string]$Trace,

    [Parameter(Mandatory = $true)]
    [string]$ProviderSmokeArtifact,

    [Parameter(Mandatory = $true)]
    [string]$RollbackTestArtifact
)

$ErrorActionPreference = "Stop"

$isDriveAbsolute = $PythonPath -match "^[A-Za-z]:[\\/]"
$isUncAbsolute = $PythonPath -match "^[\\/]{2}[^\\/]+[\\/]+[^\\/]+"
if (-not ($isDriveAbsolute -or $isUncAbsolute)) {
    throw "query_formal_pair_python_path_must_be_absolute"
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "query_formal_pair_python_not_found"
}

$requiredInputs = @(
    @{ Name = "manifest"; Path = $Manifest },
    @{ Name = "provider_smoke_artifact"; Path = $ProviderSmokeArtifact },
    @{ Name = "rollback_test_artifact"; Path = $RollbackTestArtifact }
)
foreach ($inputArtifact in $requiredInputs) {
    if (-not (Test-Path -LiteralPath $inputArtifact.Path -PathType Leaf)) {
        throw "query_formal_pair_input_must_be_file:$($inputArtifact.Name)"
    }
}

$pythonExe = (Resolve-Path -LiteralPath $PythonPath).Path
$manifestPath = (Resolve-Path -LiteralPath $Manifest).Path
$providerSmokePath = (Resolve-Path -LiteralPath $ProviderSmokeArtifact).Path
$rollbackPath = (Resolve-Path -LiteralPath $RollbackTestArtifact).Path
$tracePath = [System.IO.Path]::GetFullPath($Trace)
$outputPath = [System.IO.Path]::GetFullPath($OutputDir)
$traceParent = Split-Path -Parent $tracePath

if (-not (Test-Path -LiteralPath $traceParent -PathType Container)) {
    throw "query_formal_pair_trace_parent_not_found"
}
if (Test-Path -LiteralPath $tracePath) {
    throw "query_formal_pair_trace_must_not_exist"
}
if (Test-Path -LiteralPath $outputPath) {
    throw "query_formal_pair_output_must_not_exist"
}

try {
    $probeOutput = & $pythonExe -c "import sys; print(sys.executable)"
} catch {
    throw "query_formal_pair_python_probe_failed"
}
if ($LASTEXITCODE -ne 0) {
    throw "query_formal_pair_python_probe_failed"
}

$reportedPython = [string]($probeOutput | Select-Object -Last 1)
if ([string]::IsNullOrWhiteSpace($reportedPython)) {
    throw "query_formal_pair_python_probe_failed"
}
$reportedPython = [System.IO.Path]::GetFullPath($reportedPython.Trim())
if (-not [string]::Equals(
    $reportedPython,
    $pythonExe,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "query_formal_pair_python_binding_mismatch"
}

& $pythonExe -m scripts.decomposition_eval.run_rollout `
    --manifest $manifestPath `
    --output-dir $outputPath `
    --trace $tracePath `
    --provider-smoke-artifact $providerSmokePath `
    --rollback-test-artifact $rollbackPath `
    --validate-inputs-only
if ($LASTEXITCODE -ne 0) {
    throw "query_formal_pair_input_validation_failed:$LASTEXITCODE"
}

$traceStream = [System.IO.File]::Open(
    $tracePath,
    [System.IO.FileMode]::CreateNew,
    [System.IO.FileAccess]::Write,
    [System.IO.FileShare]::None
)
$traceStream.Dispose()

& $pythonExe -m scripts.decomposition_eval.run_rollout `
    --manifest $manifestPath `
    --output-dir $outputPath `
    --trace $tracePath `
    --provider-smoke-artifact $providerSmokePath `
    --rollback-test-artifact $rollbackPath
if ($LASTEXITCODE -ne 0) {
    throw "query_formal_pair_runner_failed:$LASTEXITCODE"
}
