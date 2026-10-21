# Pasang "Scan Kantor" sebagai Windows service (nssm) + buka port di firewall.
# Aman dijalankan ulang (sekaligus untuk update: service di-restart).
#
# Jalankan di PC server, PowerShell "Run as Administrator":
#   Set-Location C:\Users\TCI-PC-010\office-scan
#   powershell -ExecutionPolicy Bypass -File .\install_service.ps1

param(
    [string]$ServiceName = "OfficeScan",
    [int]$Port = 8500,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$nssm = Join-Path $root "nssm.exe"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw "Jalankan PowerShell dengan 'Run as Administrator'." }

# nssm.exe dipinjam dari aplikasi Legal Contract di sebelahnya.
if (-not (Test-Path $nssm)) {
    $candidate = Join-Path (Split-Path $root) "legal-contract\nssm.exe"
    if (Test-Path $candidate) { Copy-Item $candidate $nssm } else { throw "nssm.exe tidak ditemukan." }
}

# Python bawaan server (tanpa venv; aplikasi ini hanya memakai pustaka bawaan).
if (-not $Python) {
    $Python = @(
        "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe",
        "C:\Users\TCI-PC-010\AppData\Local\Python\pythoncore-3.14-64\python.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Python) { $Python = (Get-Command python -ErrorAction Stop).Source }
}
Write-Host "Python: $Python"

$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logDir, (Join-Path $root "data") | Out-Null

Write-Host "1/2 Service '$ServiceName' (port $Port)" -ForegroundColor Cyan
if (-not (Get-Service $ServiceName -ErrorAction SilentlyContinue)) {
    & $nssm install $ServiceName $Python (Join-Path $root "scan_server.py") | Out-Null
}
& $nssm set $ServiceName Application $Python | Out-Null
& $nssm set $ServiceName AppParameters (Join-Path $root "scan_server.py") | Out-Null
& $nssm set $ServiceName AppDirectory $root | Out-Null
& $nssm set $ServiceName DisplayName "Scan Kantor (EPSON L3210)" | Out-Null
& $nssm set $ServiceName Start SERVICE_AUTO_START | Out-Null
& $nssm set $ServiceName AppEnvironmentExtra "SCAN_PORT=$Port" | Out-Null
& $nssm set $ServiceName AppStdout (Join-Path $logDir "service.log") | Out-Null
& $nssm set $ServiceName AppStderr (Join-Path $logDir "service.log") | Out-Null
& $nssm set $ServiceName AppRotateFiles 1 | Out-Null
& $nssm set $ServiceName AppRotateBytes 1048576 | Out-Null
& $nssm restart $ServiceName | Out-Null

Write-Host "2/2 Firewall: izinkan port $Port dari jaringan kantor" -ForegroundColor Cyan
$rule = "Scan Kantor ($Port)"
if (-not (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $rule -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
}

Start-Sleep -Seconds 3
try {
    $code = (Invoke-WebRequest "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 10).StatusCode
    Write-Host "`nSELESAI (HTTP $code). Buka dari laptop: http://192.168.120.229:$Port" -ForegroundColor Green
} catch {
    Write-Host "`nService belum merespons. Cek $logDir\service.log" -ForegroundColor Yellow
    & $nssm status $ServiceName
}
