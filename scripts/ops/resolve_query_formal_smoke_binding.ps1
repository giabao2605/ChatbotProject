[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ProviderSmokeArtifact
)

$ErrorActionPreference = "Stop"

function ConvertTo-SmokeInstant {
    param([Parameter(Mandatory = $true)]$Value)
    try {
        if ($Value -is [datetime]) {
            return [DateTimeOffset]$Value
        }
        return [DateTimeOffset]::Parse(
            [string]$Value,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        )
    }
    catch {
        throw "query_formal_provider_smoke_timestamp_invalid"
    }
}

if (!(Test-Path -LiteralPath $ProviderSmokeArtifact -PathType Leaf)) {
    throw "query_formal_provider_smoke_missing"
}

$smoke = Get-Content -Raw -LiteralPath $ProviderSmokeArtifact | ConvertFrom-Json
$providerSha256 = [string]$smoke.provider_configuration_sha256
if (
    $smoke.schema -ne "provider-smoke-v1" -or
    $smoke.passed -ne $true -or
    $smoke.request_count -ne 5 -or
    $smoke.successful_requests -ne 5 -or
    $smoke.failed_requests -ne 0 -or
    $smoke.provider_retries -ne 0 -or
    $smoke.max_attempts_per_request -ne 1 -or
    [double]$smoke.request_timeout_seconds -ne 30.0 -or
    $smoke.provider_outcome.reason -ne "provider_available" -or
    $providerSha256 -notmatch "^[0-9a-f]{64}$"
) {
    throw "query_formal_provider_smoke_invalid"
}

$completedAt = ConvertTo-SmokeInstant -Value $smoke.completed_at
$format = "yyyy-MM-ddTHH:mm:ss.fffffff'Z'"
$culture = [Globalization.CultureInfo]::InvariantCulture
[pscustomobject]@{
    provider_smoke_sha256 = (
        Get-FileHash -Algorithm SHA256 -LiteralPath $ProviderSmokeArtifact
    ).Hash.ToLowerInvariant()
    provider_configuration_sha256 = $providerSha256
    completed_at = $completedAt.ToUniversalTime().ToString($format, $culture)
    baseline_must_start_before = $completedAt.AddMinutes(30).ToUniversalTime().ToString(
        $format,
        $culture
    )
}
