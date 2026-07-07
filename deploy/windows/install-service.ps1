# Installs NetMon as a Windows service via NSSM.
# Run from an ELEVATED PowerShell:  powershell -ExecutionPolicy Bypass -File install-service.ps1

$ErrorActionPreference = "Stop"

$ServiceName = "NetMon"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$LogDir = Join-Path $RepoRoot "logs"

# --- Preconditions ----------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "This script must run in an elevated (Administrator) PowerShell."
}

# NSSM acquisition: prefer an nssm.exe already next to this script, then one
# on PATH; otherwise download the official 2.24 release and verify its SHA-256
# against the published checksum before trusting it.
$NssmZipUrl = "https://nssm.cc/release/nssm-2.24.zip"
$NssmZipSha256 = "727D1E42275C605E0F04ABA98095C38A8E1E46DEF453CDFFCE42869428AA6743"

$nssm = Join-Path $PSScriptRoot "nssm.exe"
if (-not (Test-Path $nssm)) {
    $cmd = Get-Command nssm -ErrorAction SilentlyContinue
    if ($cmd) {
        $nssm = $cmd.Source
    } else {
        Write-Host "nssm.exe not found locally - downloading official 2.24 release..."
        $zipPath = Join-Path $PSScriptRoot "nssm-2.24.zip"
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $NssmZipUrl -OutFile $zipPath -UseBasicParsing

        $actual = (Get-FileHash $zipPath -Algorithm SHA256).Hash
        if ($actual -ne $NssmZipSha256) {
            Remove-Item $zipPath -Force
            Write-Error ("SHA-256 MISMATCH for $NssmZipUrl`n" +
                "  expected: $NssmZipSha256`n" +
                "  actual:   $actual`n" +
                "Refusing to install. Download NSSM manually from https://nssm.cc/download," +
                " verify it yourself, and place nssm.exe (win64) in this folder.")
        }

        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip = [IO.Compression.ZipFile]::OpenRead($zipPath)
        try {
            $entry = $zip.Entries | Where-Object { $_.FullName -eq "nssm-2.24/win64/nssm.exe" }
            if (-not $entry) { Write-Error "win64/nssm.exe not found inside $zipPath." }
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $nssm, $true)
        } finally {
            $zip.Dispose()
        }
        Remove-Item $zipPath -Force
        Write-Host "NSSM 2.24 (win64) downloaded, verified, and extracted." -ForegroundColor Green
    }
}

$python = (Get-Command python).Source
Write-Host "NSSM:      $nssm"
Write-Host "Python:    $python"
Write-Host "Repo root: $RepoRoot"

if (Get-Service $ServiceName -ErrorAction SilentlyContinue) {
    Write-Error "Service '$ServiceName' already exists. Run uninstall-service.ps1 first."
}

New-Item -ItemType Directory -Force $LogDir | Out-Null

# --- Install ----------------------------------------------------------
& $nssm install $ServiceName $python "-m" "netmon.main"
& $nssm set $ServiceName AppDirectory $RepoRoot
& $nssm set $ServiceName DisplayName "NetMon Internet Monitor"
& $nssm set $ServiceName Description "Tracks internet speed, latency, packet loss, and outages."
& $nssm set $ServiceName Start SERVICE_AUTO_START

# Service console output (app logs also go to netmon.log via the app itself).
& $nssm set $ServiceName AppStdout (Join-Path $LogDir "service-out.log")
& $nssm set $ServiceName AppStderr (Join-Path $LogDir "service-err.log")
& $nssm set $ServiceName AppRotateFiles 1
& $nssm set $ServiceName AppRotateBytes 10485760

# Graceful stop: send Ctrl+C (-> SIGINT handler -> clean scheduler shutdown),
# give it 15s before escalating.
& $nssm set $ServiceName AppStopMethodSkip 0
& $nssm set $ServiceName AppStopMethodConsole 15000

# Restart on crash, throttled so a broken config can't hot-loop (also
# prevents rapid restarts from burning speed-test data). NOTE: do not set
# AppRestartDelay — NSSM applies it to the FIRST start too, leaving the
# service stuck in "start pending" for the whole delay.
& $nssm set $ServiceName AppExit Default Restart
& $nssm set $ServiceName AppThrottle 60000

Write-Host ""
Write-Host "Service installed." -ForegroundColor Green
Write-Host "Start it with:   Start-Service NetMon    (or: & '$nssm' start NetMon)"
Write-Host "Dashboard:       http://127.0.0.1:5000 (per config.yaml)"
Write-Host "Stop it with:    Stop-Service NetMon"
