$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is not installed. Install Docker Desktop, start it, then run this script again."
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is not running. Start Docker Desktop, then run this script again."
}

try {
    $ollama = Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5 -UseBasicParsing
    if ($ollama.Content -notmatch "qwen2.5:3b") {
        Write-Host "Ollama is running, but qwen2.5:3b is not installed. Run: ollama pull qwen2.5:3b"
    }
} catch {
    throw "Ollama is not running on http://127.0.0.1:11434. Start the Ollama app, then run this script again."
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Password is change-me-now until you edit .env."
}

Write-Host "Stopping this project's local API, worker, and Vite so Docker can use ports 8000 and 5173."
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='node.exe'" |
    Where-Object { $_.CommandLine -match "uvicorn app.main:app|app.worker.main|SalesPilot-ai\\frontend" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$owner = docker ps --filter "publish=8000" --format "{{.Names}}"
if ($owner -and $owner -notmatch "salespilot-ai") {
    throw "Port 8000 is already used by Docker container '$owner'. Stop that container, then run this script again. Gmail login is registered on port 8000, so SalesPilot has to use it."
}

docker compose up -d --build
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed." }

Write-Host ""
Write-Host "SalesPilot is running."
Write-Host "Open: http://127.0.0.1:5173"
Write-Host "Password: the SALESPILOT_PASSWORD in .env (change-me-now unless you changed it)."
Write-Host "Gmail syncs about every 8 seconds. Ollama is the one already installed on this PC."
