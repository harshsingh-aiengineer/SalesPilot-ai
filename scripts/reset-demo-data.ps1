$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
Write-Host "This deletes leads marked synthetic_seed and inserts the fictional Dubai sample again."
Write-Host "Real Gmail messages and leads you created are kept."
docker compose exec api python -m app.seed --reset --yes
