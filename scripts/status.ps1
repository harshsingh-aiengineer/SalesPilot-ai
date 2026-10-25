$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
docker compose ps
Write-Host ""
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/health" -TimeoutSec 5
    Write-Host ("API health: " + $health.status)
} catch {
    Write-Host "API health: not reachable"
}
