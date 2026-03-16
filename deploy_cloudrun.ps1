[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [string]$Region = "europe-west1",
    [string]$ServiceName = "trading-brief-dashboard",
    [string]$EnvFile = "cloudrun.env.yaml"
)

$ErrorActionPreference = "Stop"

function Assert-Command([string]$name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "Command '$name' not found. Install Google Cloud SDK first."
    }
}

Assert-Command "gcloud"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

Write-Host "Configuring gcloud project: $ProjectId"
& gcloud config set project $ProjectId | Out-Host

$deployArgs = @(
    "run", "deploy", $ServiceName,
    "--source", ".",
    "--platform", "managed",
    "--region", $Region,
    "--project", $ProjectId,
    "--allow-unauthenticated",
    "--port", "8080",
    "--cpu", "1",
    "--memory", "512Mi",
    "--min-instances", "0",
    "--max-instances", "1"
)

if (Test-Path $EnvFile) {
    Write-Host "Using env vars file: $EnvFile"
    $deployArgs += @("--env-vars-file", $EnvFile)
} else {
    Write-Host "No $EnvFile found. Deploying without extra env vars." -ForegroundColor Yellow
}

Write-Host "Deploying Cloud Run service '$ServiceName' in $Region..."
& gcloud @deployArgs | Out-Host

$url = (& gcloud run services describe $ServiceName --region $Region --project $ProjectId --format "value(status.url)").Trim()
if (-not [string]::IsNullOrWhiteSpace($url)) {
    Write-Host ""
    Write-Host "Service URL: $url" -ForegroundColor Green
    Start-Process $url
}
