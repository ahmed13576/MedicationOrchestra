<#
.SYNOPSIS
    Sets up Flutter, Android SDK, and Java JDK from downloaded ZIPs.
    Run this script after all downloads are complete.
    
.DESCRIPTION
    1. Extracts OpenJDK 17 -> sets JAVA_HOME
    2. Extracts Flutter SDK -> adds to PATH
    3. Extracts Android command-line tools -> sets ANDROID_HOME
    4. Installs Android SDK platform-tools + build-tools
    5. Accepts Android licenses
    6. Runs flutter doctor
#>

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "`n[STEP] $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "[OK]   $msg" -ForegroundColor Green }
function Write-ERR($msg)  { Write-Host "[ERR]  $msg" -ForegroundColor Red }

$HOME_DIR    = $env:USERPROFILE  # C:\Users\moham
$JDK_ZIP     = "$HOME_DIR\openjdk-17.zip"
$FLUTTER_ZIP = "$HOME_DIR\flutter_sdk.zip"
$ANDROID_ZIP = "$HOME_DIR\android_cmdtools.zip"
$JDK_DIR     = "$HOME_DIR\openjdk-17"
$FLUTTER_DIR = "$HOME_DIR\flutter"
$ANDROID_DIR = "$HOME_DIR\Android"

# -------------------------------------------------------
# 1. Extract OpenJDK 17
# -------------------------------------------------------
Write-Step "Extracting OpenJDK 17"
if (-not (Test-Path $JDK_ZIP)) { Write-ERR "Missing: $JDK_ZIP"; exit 1 }
if (Test-Path $JDK_DIR) { Remove-Item $JDK_DIR -Recurse -Force }
$tmpDir = "$HOME_DIR\jdk-tmp"
if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
Expand-Archive -Path $JDK_ZIP -DestinationPath $tmpDir -Force
$extracted = Get-ChildItem $tmpDir -Directory | Select-Object -First 1
Move-Item $extracted.FullName $JDK_DIR
Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $JDK_ZIP -Force
Write-OK "JDK at $JDK_DIR"
& "$JDK_DIR\bin\java.exe" -version

# -------------------------------------------------------
# 2. Extract Flutter SDK
# -------------------------------------------------------
Write-Step "Extracting Flutter SDK"
if (-not (Test-Path $FLUTTER_ZIP)) { Write-ERR "Missing: $FLUTTER_ZIP"; exit 1 }
if (Test-Path $FLUTTER_DIR) { Remove-Item $FLUTTER_DIR -Recurse -Force }
$tmpDir = "$HOME_DIR\flutter-tmp"
if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
Expand-Archive -Path $FLUTTER_ZIP -DestinationPath $tmpDir -Force
$extracted = Get-ChildItem $tmpDir -Directory | Select-Object -First 1
Move-Item $extracted.FullName $FLUTTER_DIR
Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $FLUTTER_ZIP -Force
Write-OK "Flutter at $FLUTTER_DIR"

# -------------------------------------------------------
# 3. Extract Android command-line tools
# -------------------------------------------------------
Write-Step "Extracting Android command-line tools"
if (-not (Test-Path $ANDROID_ZIP)) { Write-ERR "Missing: $ANDROID_ZIP"; exit 1 }
$androidLatest = "$ANDROID_DIR\cmdline-tools\latest"
if (Test-Path $ANDROID_DIR) { Remove-Item $ANDROID_DIR -Recurse -Force }
New-Item -ItemType Directory -Path $androidLatest -Force | Out-Null
$tmpDir = "$HOME_DIR\android-tmp"
if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
Expand-Archive -Path $ANDROID_ZIP -DestinationPath $tmpDir -Force
# The zip contains a "cmdline-tools" folder — move its contents into latest/
$extracted = Get-ChildItem $tmpDir -Directory | Select-Object -First 1
Get-ChildItem $extracted.FullName | Move-Item -Destination $androidLatest
Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $ANDROID_ZIP -Force
Write-OK "Android tools at $androidLatest"

# -------------------------------------------------------
# 4. Set environment variables (User-level, permanent)
# -------------------------------------------------------
Write-Step "Setting environment variables"
[Environment]::SetEnvironmentVariable("JAVA_HOME", $JDK_DIR, "User")
[Environment]::SetEnvironmentVariable("ANDROID_HOME", $ANDROID_DIR, "User")
[Environment]::SetEnvironmentVariable("ANDROID_SDK_ROOT", $ANDROID_DIR, "User")

# Update PATH
$currentPath = [Environment]::GetEnvironmentVariable("PATH", "User") -split ";"
$newEntries = @(
    "$JDK_DIR\bin",
    "$FLUTTER_DIR\bin",
    "$ANDROID_DIR\cmdline-tools\latest\bin",
    "$ANDROID_DIR\platform-tools"
)
$newEntries | ForEach-Object {
    if ($currentPath -notcontains $_) { $currentPath += $_ }
}
[Environment]::SetEnvironmentVariable("PATH", ($currentPath -join ";"), "User")
Write-OK "PATH updated. Reload PowerShell to apply."

# Also set for current session
$env:JAVA_HOME = $JDK_DIR
$env:ANDROID_HOME = $ANDROID_DIR
$env:ANDROID_SDK_ROOT = $ANDROID_DIR
$env:PATH = "$JDK_DIR\bin;$FLUTTER_DIR\bin;$ANDROID_DIR\cmdline-tools\latest\bin;$ANDROID_DIR\platform-tools;$env:PATH"

# -------------------------------------------------------
# 5. Install Android SDK components
# -------------------------------------------------------
Write-Step "Installing Android SDK platform-tools and build-tools"
$sdkmanager = "$androidLatest\bin\sdkmanager.bat"
& $sdkmanager "platform-tools" "build-tools;34.0.0" "platforms;android-34" 2>&1 | Tee-Object -Variable sdkOutput
Write-OK "Android SDK components installed"

# -------------------------------------------------------
# 6. Accept Android licenses
# -------------------------------------------------------
Write-Step "Accepting Android licenses"
$yes = "y`n" * 10
$yes | & $sdkmanager --licenses 2>&1 | Out-Null
Write-OK "Android licenses accepted"

# -------------------------------------------------------
# 7. Run flutter doctor
# -------------------------------------------------------
Write-Step "Running flutter doctor"
& "$FLUTTER_DIR\bin\flutter.bat" doctor --android-licenses 2>&1 | ForEach-Object { "y" } | Out-Null
& "$FLUTTER_DIR\bin\flutter.bat" doctor 2>&1

Write-Host "`n=== Setup Complete ===" -ForegroundColor Green
Write-Host "IMPORTANT: Open a NEW PowerShell window to use flutter/java commands."
Write-Host "Then run: flutter create medication_orchestra --org com.medicationorchestra --platforms android,ios,web"
