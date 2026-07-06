<#
.SYNOPSIS
    Cleanup script for Medication Orchestra dev environment.
    Run this after the project is complete to reclaim disk space.
    
.DESCRIPTION
    Removes Flutter SDK, Android command-line tools, OpenJDK 17,
    the Python virtual environment, and cleans up PATH/env vars.
    
.NOTES
    Run as Administrator for full cleanup.
    Estimated space reclaimed: ~5-8 GB
#>

param(
    [switch]$DryRun,
    [switch]$KeepFlutter,
    [switch]$KeepAndroid,
    [switch]$KeepJava
)

$ErrorActionPreference = "Continue"

function Write-Step($msg) { Write-Host "`n[STEP] $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "[OK]   $msg" -ForegroundColor Green }
function Write-WARN($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-DRY($msg)  { Write-Host "[DRY]  Would remove: $msg" -ForegroundColor DarkGray }

if ($DryRun) {
    Write-Host "`n=== DRY RUN MODE - Nothing will be deleted ===" -ForegroundColor Yellow
}

# -------------------------------------------------------
# 1. Remove Flutter SDK
# -------------------------------------------------------
Write-Step "Flutter SDK (C:\flutter)"
if (-not $KeepFlutter) {
    if (Test-Path "C:\flutter") {
        if ($DryRun) { Write-DRY "C:\flutter (~2 GB)" }
        else {
            Remove-Item -Recurse -Force "C:\flutter"
            Write-OK "Removed C:\flutter"
        }
    } else { Write-WARN "C:\flutter not found, skipping" }
} else { Write-WARN "Skipping Flutter (--KeepFlutter)" }

# -------------------------------------------------------
# 2. Remove Android command-line tools & SDK
# -------------------------------------------------------
Write-Step "Android SDK (C:\Android)"
if (-not $KeepAndroid) {
    if (Test-Path "C:\Android") {
        if ($DryRun) { Write-DRY "C:\Android (~2-3 GB)" }
        else {
            Remove-Item -Recurse -Force "C:\Android"
            Write-OK "Removed C:\Android"
        }
    } else { Write-WARN "C:\Android not found, skipping" }
} else { Write-WARN "Skipping Android SDK (--KeepAndroid)" }

# -------------------------------------------------------
# 3. Remove OpenJDK 17
# -------------------------------------------------------
Write-Step "OpenJDK 17 (C:\openjdk-17)"
if (-not $KeepJava) {
    if (Test-Path "C:\openjdk-17") {
        if ($DryRun) { Write-DRY "C:\openjdk-17 (~300 MB)" }
        else {
            Remove-Item -Recurse -Force "C:\openjdk-17"
            Write-OK "Removed C:\openjdk-17"
        }
    } else { Write-WARN "C:\openjdk-17 not found, skipping" }
} else { Write-WARN "Skipping Java (--KeepJava)" }

# -------------------------------------------------------
# 4. Remove Python venv
# -------------------------------------------------------
Write-Step "Python virtual environment (backend\.venv)"
$venvPath = "C:\Users\moham\Documents\MedicationOrchestrationAgent\backend\.venv"
if (Test-Path $venvPath) {
    if ($DryRun) { Write-DRY "$venvPath (~500 MB)" }
    else {
        Remove-Item -Recurse -Force $venvPath
        Write-OK "Removed $venvPath"
    }
} else { Write-WARN "backend\.venv not found, skipping" }

# -------------------------------------------------------
# 5. Remove Dart pub cache
# -------------------------------------------------------
Write-Step "Dart pub cache"
$dartCache = "$env:LOCALAPPDATA\Pub\Cache"
if (Test-Path $dartCache) {
    if ($DryRun) { Write-DRY "$dartCache" }
    else {
        Remove-Item -Recurse -Force $dartCache
        Write-OK "Removed Dart pub cache"
    }
} else { Write-WARN "Dart pub cache not found, skipping" }

# -------------------------------------------------------
# 6. Remove Dart global pub cache
# -------------------------------------------------------
Write-Step "Dart global activated packages"
$dartGlobal = "$env:APPDATA\Pub\Cache"
if (Test-Path $dartGlobal) {
    if ($DryRun) { Write-DRY "$dartGlobal" }
    else {
        Remove-Item -Recurse -Force $dartGlobal
        Write-OK "Removed Dart global pub cache"
    }
} else { Write-WARN "Dart global cache not found, skipping" }

# -------------------------------------------------------
# 7. Remove Flutter-related env vars from user PATH
# -------------------------------------------------------
Write-Step "Cleaning up PATH environment variables"
if (-not $DryRun) {
    $userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
    $newPath = ($userPath -split ";") | Where-Object {
        $_ -notmatch "flutter" -and $_ -notmatch "openjdk-17" -and $_ -notmatch "C:\\Android"
    }
    [Environment]::SetEnvironmentVariable("PATH", ($newPath -join ";"), "User")
    Write-OK "Removed flutter/java/android entries from user PATH"

    # Remove JAVA_HOME and ANDROID_HOME
    [Environment]::SetEnvironmentVariable("JAVA_HOME", $null, "User")
    [Environment]::SetEnvironmentVariable("ANDROID_HOME", $null, "User")
    [Environment]::SetEnvironmentVariable("ANDROID_SDK_ROOT", $null, "User")
    Write-OK "Removed JAVA_HOME, ANDROID_HOME, ANDROID_SDK_ROOT"
} else {
    Write-DRY "Would clean flutter/java/android from user PATH"
    Write-DRY "Would remove JAVA_HOME, ANDROID_HOME, ANDROID_SDK_ROOT"
}

# -------------------------------------------------------
# Summary
# -------------------------------------------------------
Write-Host "`n=== Cleanup Complete ===" -ForegroundColor Green
Write-Host "Estimated space reclaimed: ~5-8 GB"
Write-Host "Note: Python 3.12 and gcloud CLI were NOT removed (pre-existing tools)"
if ($DryRun) {
    Write-Host "`nRun without -DryRun to actually delete files." -ForegroundColor Yellow
}
