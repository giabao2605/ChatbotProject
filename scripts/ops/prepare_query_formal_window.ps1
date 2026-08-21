[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RunRoot,
    [Parameter(Mandatory = $true)][string]$ExpectedSourceCommit,
    [string]$PythonPath,
    [string]$CampaignRoot,
    [string]$TaskName,
    [string]$MathReleaseRoot,
    [switch]$RevalidateForProviderTraffic
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$expectedCollection = "MechChatbot_CRAG_Eval_v1"

function Get-JsonFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "query_window_required_artifact_missing"
    }
    return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "query_window_required_artifact_missing"
    }
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Assert-SourceState {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ExpectedCommit
    )
    $actualCommit = (& git -C $Root rev-parse HEAD 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or $actualCommit -ne $ExpectedCommit) {
        throw "query_window_source_commit_mismatch"
    }
    $status = & git -C $Root status --porcelain=v1 --untracked-files=all
    if ($LASTEXITCODE -ne 0 -or @($status).Count -ne 0) {
        throw "query_window_worktree_dirty"
    }
}

function Assert-PreparationBindings {
    param(
        [Parameter(Mandatory = $true)]$Packet,
        [Parameter(Mandatory = $true)][string]$Root
    )
    $query = $Packet.capabilities.query_decomposition
    $manifestPath = Join-Path $Root ([string]$query.manifest_reference.path)
    $runnerPath = Join-Path $Root "scripts\decomposition_eval\run_rollout.py"
    if (
        $Packet.schema -ne "query-crag-offline-preparation-v1" -or
        $Packet.status -ne "predeclared_unexecuted" -or
        @($Packet.authorization.PSObject.Properties | Where-Object { $_.Value -eq $true }).Count -ne 0 -or
        (Get-Sha256 -Path $manifestPath) -ne $query.manifest_reference.prepared_sha256 -or
        (Get-Sha256 -Path $runnerPath) -ne $query.execution_bindings.runner_sha256
    ) {
        throw "query_window_preparation_binding_drift"
    }
    return $manifestPath
}

function Assert-MathCampaignTerminal {
    param(
        [Parameter(Mandatory = $true)]$Packet,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ScheduledTaskName
    )
    $public = Get-JsonFile -Path (Join-Path $Root "campaign-public.json")
    $walPath = Join-Path $Root "campaign.wal.jsonl"
    $baseGate = Get-JsonFile -Path (Join-Path $Root "base-gate.json")
    $operatorGate = Get-JsonFile -Path (Join-Path $Root "operator-gate.json")
    $completed = @(
        Get-Content -LiteralPath $walPath |
            ForEach-Object { $_ | ConvertFrom-Json } |
            Where-Object event -eq "attempt_completed"
    ).Count
    $task = Get-ScheduledTask -TaskName $ScheduledTaskName -ErrorAction Stop
    if (
        $public.campaign_id -ne $Packet.math_campaign_dependency.campaign_id -or
        $completed -ne 100 -or
        $baseGate.passed -ne $true -or
        $operatorGate.passed -ne $true -or
        !(Test-Path -LiteralPath (Join-Path $Root "stop.marker") -PathType Leaf) -or
        [string]$task.State -ne "Disabled"
    ) {
        throw "query_window_math_campaign_guard_failed"
    }
}

function Assert-MathRelease {
    param(
        [Parameter(Mandatory = $true)]$Packet,
        [Parameter(Mandatory = $true)][string]$Root
    )
    $dependency = $Packet.math_default_rollout_dependency
    $ledgerPath = Join-Path $Root "release-decisions.json"
    $bundlePath = Join-Path $Root "activation-bundle.json"
    $ledger = Get-JsonFile -Path $ledgerPath
    $bundle = Get-JsonFile -Path $bundlePath
    if (
        (Get-Sha256 -Path $ledgerPath) -ne $dependency.release_decisions_sha256 -or
        (Get-Sha256 -Path $bundlePath) -ne $dependency.activation_bundle_sha256 -or
        $ledger.status -ne "complete" -or
        $ledger.source_commit -ne $dependency.source_commit -or
        $ledger.decisions.RAG_GROUNDED_MATH_ENABLED.decision -ne "accepted" -or
        $ledger.decisions.RAG_QUERY_DECOMPOSITION_ENABLED.decision -ne "rejected" -or
        $ledger.decisions.RAG_CRAG_ENABLED.decision -ne "rejected" -or
        $ledger.decisions.RAG_CLAIM_REPAIR_ENABLED.decision -ne "rejected" -or
        $ledger.decisions.RAG_LATE_INTERACTION_ENABLED.decision -ne "rejected" -or
        $ledger.decisions.RAG_GRAPH_RETRIEVAL_ENABLED.decision -ne "rejected" -or
        $ledger.decisions.RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED.decision -ne "rejected" -or
        $bundle.scope -ne "default_rollout" -or
        $bundle.source_commit -ne $dependency.source_commit -or
        $bundle.activation_profile -ne "selective" -or
        $bundle.feature_flags.RAG_GROUNDED_MATH_ENABLED -ne $true -or
        $bundle.feature_flags.RAG_QUERY_DECOMPOSITION_ENABLED -ne $false -or
        $bundle.feature_flags.RAG_CRAG_ENABLED -ne $false -or
        $bundle.feature_flags.RAG_CLAIM_REPAIR_ENABLED -ne $false -or
        $bundle.feature_flags.RAG_LATE_INTERACTION_ENABLED -ne $false -or
        $bundle.feature_flags.RAG_GRAPH_RETRIEVAL_ENABLED -ne $false -or
        $bundle.feature_flags.RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED -ne $false
    ) {
        throw "query_window_math_release_binding_drift"
    }
}

function Invoke-OfflineReadinessStep {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [Parameter(Mandatory = $true)][string]$FailureCode
    )
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
        throw $FailureCode
    }
}

function Assert-OfflineEvidence {
    param(
        [Parameter(Mandatory = $true)][string]$PreflightPath,
        [Parameter(Mandatory = $true)][string]$RollbackPath,
        [Parameter(Mandatory = $true)][string]$ExpectedCommit
    )
    $preflight = Get-JsonFile -Path $PreflightPath
    $rollback = Get-JsonFile -Path $RollbackPath
    $rollbackFlags = @($rollback.flags)
    if (
        $preflight.schema -ne "decomposition-fixture-preflight-v1" -or
        $preflight.passed -ne $true -or
        $preflight.batch -ne "crag-eval-v1" -or
        $preflight.collection -ne $expectedCollection -or
        [int]$preflight.checked_cases -ne 13 -or
        @($preflight.failures).Count -ne 0 -or
        $rollback.schema -ne "rollback-test-evidence-v1" -or
        $rollback.passed -ne $true -or
        $rollback.git_sha -ne $ExpectedCommit -or
        $rollbackFlags.Count -ne 1 -or
        $rollbackFlags[0] -ne "RAG_QUERY_DECOMPOSITION_ENABLED" -or
        $rollback.verified_flag_state.RAG_QUERY_DECOMPOSITION_ENABLED -ne $false -or
        [int]$rollback.exit_code -ne 0
    ) {
        throw "query_window_offline_evidence_invalid"
    }
}

if (
    $env:QDRANT_COLLECTION -ne $expectedCollection -or
    $env:RAG_EVAL_EXPECTED_COLLECTION -ne $expectedCollection
) {
    throw "query_eval_collection_binding_invalid"
}
if ($ExpectedSourceCommit -notmatch '^[0-9a-f]{40}$') {
    throw "query_window_expected_commit_invalid"
}
if ($RevalidateForProviderTraffic) {
    if (!(Test-Path -LiteralPath $RunRoot -PathType Container)) {
        throw "query_window_run_root_missing"
    }
    if ($env:EXTERNAL_PROCESSING_POLICY -ne "all_external") {
        [ordered]@{
            schema = "query-provider-boundary-failure-v1"
            status = "tombstoned"
            source_commit = $ExpectedSourceCommit
            reason = "external_processing_policy_invalid"
        } | ConvertTo-Json | Set-Content -LiteralPath (
            Join-Path $RunRoot "provider-boundary-policy-failure.json"
        ) -Encoding utf8
        throw "query_window_external_processing_policy_invalid"
    }
}
elseif (Test-Path -LiteralPath $RunRoot) {
    throw "query_window_run_root_must_not_exist"
}
Assert-SourceState -Root $projectRoot -ExpectedCommit $ExpectedSourceCommit
if (
    [string]::IsNullOrWhiteSpace($PythonPath) -or
    [string]::IsNullOrWhiteSpace($CampaignRoot) -or
    [string]::IsNullOrWhiteSpace($TaskName) -or
    [string]::IsNullOrWhiteSpace($MathReleaseRoot) -or
    !(Test-Path -LiteralPath $PythonPath -PathType Leaf) -or
    $env:RUN_DECOMPOSITION_EVAL_FIXTURE -ne "1"
) {
    throw "query_window_operator_inputs_invalid"
}
$packetPath = Join-Path $projectRoot "data\integrated_hardening_v1\evidence\query-crag-offline-preparation.json"
$packet = Get-JsonFile -Path $packetPath
$manifestPath = Assert-PreparationBindings -Packet $packet -Root $projectRoot
Assert-MathCampaignTerminal -Packet $packet -Root $CampaignRoot -ScheduledTaskName $TaskName
Assert-MathRelease -Packet $packet -Root $MathReleaseRoot

if ($RevalidateForProviderTraffic) {
    $existingItems = @(Get-ChildItem -LiteralPath $RunRoot -Force)
    $preparationPreflight = Join-Path $RunRoot "preflight.json"
    $preparationRollback = Join-Path $RunRoot "rollback.json"
    if (
        $existingItems.Count -ne 2 -or
        !(Test-Path -LiteralPath $preparationPreflight -PathType Leaf) -or
        !(Test-Path -LiteralPath $preparationRollback -PathType Leaf)
    ) {
        throw "query_window_provider_boundary_root_invalid"
    }
    Assert-OfflineEvidence -PreflightPath $preparationPreflight `
        -RollbackPath $preparationRollback -ExpectedCommit $ExpectedSourceCommit
    $preflightPath = Join-Path $RunRoot "preflight-provider-boundary.json"
    $rollbackPath = Join-Path $RunRoot "rollback-provider-boundary.json"
}
else {
    New-Item -ItemType Directory -Path $RunRoot -ErrorAction Stop | Out-Null
    $preflightPath = Join-Path $RunRoot "preflight.json"
    $rollbackPath = Join-Path $RunRoot "rollback.json"
}
$env:PYTHONDONTWRITEBYTECODE = "1"
Push-Location -LiteralPath $projectRoot
try {
    Invoke-OfflineReadinessStep -Executable $PythonPath `
        -Arguments @("-m", "scripts.decomposition_eval.preflight", "--manifest", $manifestPath, "--output", $preflightPath) `
        -OutputPath $preflightPath -FailureCode "query_window_preflight_failed"
    Invoke-OfflineReadinessStep -Executable $PythonPath `
        -Arguments @("-m", "scripts.decomposition_eval.verify_rollback", "--output", $rollbackPath) `
        -OutputPath $rollbackPath -FailureCode "query_window_rollback_failed"
}
finally {
    Pop-Location
}
Assert-SourceState -Root $projectRoot -ExpectedCommit $ExpectedSourceCommit
Assert-OfflineEvidence -PreflightPath $preflightPath `
    -RollbackPath $rollbackPath -ExpectedCommit $ExpectedSourceCommit
if ($RevalidateForProviderTraffic) {
    Write-Output "QUERY_FORMAL_WINDOW_PROVIDER_BOUNDARY_READY $RunRoot"
}
else {
    Write-Output "QUERY_FORMAL_WINDOW_OFFLINE_READY $RunRoot"
}
