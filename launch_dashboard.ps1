[CmdletBinding()]
param(
    [string]$Url = "http://127.0.0.1:8000",
    [int]$TimeoutSec = 30
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

function Import-DotEnv([string]$envPath) {
    if (-not (Test-Path $envPath)) {
        return
    }
    foreach ($rawLine in Get-Content $envPath) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            continue
        }
        $name = ""
        $value = ""

        if ($line.StartsWith('$env:', [System.StringComparison]::OrdinalIgnoreCase)) {
            $envAssign = $line.Substring(5)
            $eqIndex = $envAssign.IndexOf("=")
            if ($eqIndex -le 0) {
                continue
            }
            $name = $envAssign.Substring(0, $eqIndex).Trim()
            $value = $envAssign.Substring($eqIndex + 1).Trim()
        } else {
            $eqIndex = $line.IndexOf("=")
            if ($eqIndex -le 0) {
                continue
            }
            $name = $line.Substring(0, $eqIndex).Trim()
            $value = $line.Substring($eqIndex + 1).Trim()
        }

        if ($name.StartsWith([char]0xFEFF)) {
            $name = $name.TrimStart([char]0xFEFF)
        }
        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

Import-DotEnv (Join-Path $projectRoot ".env")

$logDir = Join-Path $projectRoot "logs"
if (-not (Test-Path $logDir)) {
    New-Item -Path $logDir -ItemType Directory | Out-Null
}
$outLog = Join-Path $logDir "dashboard-server.out.log"
$errLog = Join-Path $logDir "dashboard-server.err.log"

function Write-UserError([string]$message) {
    Write-Host ""
    Write-Host "[ERREUR] $message" -ForegroundColor Red
    Write-Host "Consultez les logs:"
    Write-Host "- $outLog"
    Write-Host "- $errLog"
    Write-Host ""
}

$pythonExe = Join-Path $projectRoot ".venv\\Scripts\\python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonExe = "python"
}

# If already running, just open browser.
try {
    $already = Invoke-WebRequest -Uri "$Url/" -UseBasicParsing -TimeoutSec 2
    if ($already.StatusCode -ge 200 -and $already.StatusCode -lt 500) {
        Start-Process $Url
        Write-Host "Dashboard deja actif. Ouverture du navigateur..."
        exit 0
    }
} catch {
    # continue: server probably not running
}

Write-Host "Demarrage du serveur dashboard..."

$process = Start-Process -FilePath $pythonExe -ArgumentList "server.py" -WorkingDirectory $projectRoot -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru -WindowStyle Minimized

$deadline = (Get-Date).AddSeconds($TimeoutSec)
$ready = $false

while ((Get-Date) -lt $deadline) {
    if ($process.HasExited) {
        Write-UserError "Le serveur s'est arrete juste apres le lancement (code: $($process.ExitCode))."
        exit 1
    }

    try {
        $response = Invoke-WebRequest -Uri "$Url/" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
            $ready = $true
            break
        }
    } catch {
        Start-Sleep -Milliseconds 700
    }
}

if (-not $ready) {
    Write-UserError "Le serveur n'a pas repondu sur $Url apres $TimeoutSec secondes."
    exit 1
}

Start-Process $Url
Write-Host "Dashboard lance. URL ouverte: $Url"
exit 0
