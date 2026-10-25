$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$model = "qwen2.5:3b"
if ($env:OLLAMA_MODEL) { $model = $env:OLLAMA_MODEL }
Write-Host "Installed models:"
docker compose exec ollama ollama list
Write-Host ""
Write-Host "Readiness prompt:"
docker compose exec api python -c "from app.llm import probe_model; import json; print(json.dumps(probe_model(), indent=2))"
