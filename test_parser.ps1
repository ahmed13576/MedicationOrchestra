<#
.SYNOPSIS
    Parses the deployment PowerShell scripts and reports syntax errors.

.DESCRIPTION
    These scripts touch production, so a typo should be caught before a deploy
    rather than halfway through one. CI cannot run this (GitHub's Linux runners
    have no PowerShell here), so run it locally after editing them:

        .\test_parser.ps1                # checks every *.ps1 in the repository root
        .\test_parser.ps1 deploy.ps1     # checks specific files

    Exits non-zero when any file fails to parse.
#>

[CmdletBinding()]
param(
    [string[]]$Path = (Get-ChildItem -Path $PSScriptRoot -Filter "*.ps1" |
        Where-Object { $_.Name -ne $MyInvocation.MyCommand.Name } |
        Select-Object -ExpandProperty Name)
)

if (-not $Path -or $Path.Count -eq 0) {
    Write-Host "No PowerShell scripts found to check." -ForegroundColor Yellow
    exit 0
}

$failed = 0
foreach ($file in $Path) {
    $resolved = Resolve-Path $file -ErrorAction SilentlyContinue
    if (-not $resolved) {
        Write-Host "[MISSING] $file" -ForegroundColor Red
        $failed++
        continue
    }

    $errors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile(
        $resolved.Path, [ref]$null, [ref]$errors
    )

    if ($errors.Count -gt 0) {
        Write-Host "[FAIL] $file" -ForegroundColor Red
        $errors | ForEach-Object {
            Write-Host ("        line {0}: {1}" -f $_.Extent.StartLineNumber, $_.Message) -ForegroundColor Red
        }
        $failed++
    } else {
        Write-Host "[OK]   $file" -ForegroundColor Green
    }
}

if ($failed -gt 0) {
    Write-Host "$failed file(s) failed to parse." -ForegroundColor Red
    exit 1
}

Write-Host "All PowerShell scripts parse." -ForegroundColor Green
exit 0
