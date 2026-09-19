$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

function Invoke-Python {
    param([string[]]$Arguments)
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 @Arguments
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python @Arguments
    } else {
        throw "Python 3 is required."
    }
}

if (-not (Test-Path ".env")) {
    Write-Host "No .env found. Generating secure local defaults..."
    Invoke-Python @("scripts/bootstrap_open_source.py")
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker Desktop / Docker Compose is required."
}

Write-Host "Building and starting safe core services..."
& docker compose up -d --build db api settlement webhook

Write-Host "Waiting for the API health gate..."
$Healthy = $false
for ($i = 0; $i -lt 40; $i++) {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8088/healthz" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            $Healthy = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}

if (-not $Healthy) {
    throw "API did not become healthy. Run: docker compose ps"
}

$secretLine = Get-Content ".env" | Where-Object { $_ -like "INTERNAL_SECRET=*" } | Select-Object -First 1
$secret = $secretLine.Substring("INTERNAL_SECRET=".Length)

Write-Host ""
Write-Host "KHQR Self-Develop is running."
Write-Host "Dashboard: http://127.0.0.1:8088/dashboard"
Write-Host "Core API:   http://127.0.0.1:8088"
Write-Host ""
Write-Host "Dashboard login secret (same as INTERNAL_SECRET):"
Write-Host $secret
Write-Host ""
Write-Host "Safety defaults preserved:"
Write-Host "  TELEGRAM_SHADOW_ONLY=true"
Write-Host "  ALLOW_LIVE_TELEGRAM=false"
Write-Host "  ALLOW_SHADOW_PROMOTION=false"
Write-Host ""
Write-Host "The Telegram collector is NOT started by install.ps1."
Write-Host "Complete Setup in the dashboard before any activation."
