$errors = $null
$null = [System.Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path "deploy.ps1").Path,
    [ref]$null,
    [ref]$errors
)
if ($errors.Count -gt 0) {
    Write-Host "ERRORS:" -ForegroundColor Red
    $errors | ForEach-Object { Write-Host $_.Message -ForegroundColor Red }
    exit 1
} else {
    Write-Host "deploy.ps1 syntax: OK" -ForegroundColor Green
}
