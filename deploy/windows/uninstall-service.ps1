# Removes the NetMon Windows service.
# Run from an ELEVATED PowerShell:  powershell -ExecutionPolicy Bypass -File uninstall-service.ps1

$ErrorActionPreference = "Stop"
$ServiceName = "NetMon"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "This script must run in an elevated (Administrator) PowerShell."
}

$nssm = Join-Path $PSScriptRoot "nssm.exe"
if (-not (Test-Path $nssm)) {
    $cmd = Get-Command nssm -ErrorAction SilentlyContinue
    if ($cmd) { $nssm = $cmd.Source } else { Write-Error "nssm.exe not found." }
}

if (-not (Get-Service $ServiceName -ErrorAction SilentlyContinue)) {
    Write-Host "Service '$ServiceName' is not installed. Nothing to do."
    exit 0
}

& $nssm stop $ServiceName
& $nssm remove $ServiceName confirm
Write-Host "Service removed." -ForegroundColor Green
