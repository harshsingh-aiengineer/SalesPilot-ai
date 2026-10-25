param(
    [string]$Service
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
if ($Service) {
    docker compose logs -f $Service
} else {
    docker compose logs -f
}
