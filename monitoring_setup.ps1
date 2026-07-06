<#
.SYNOPSIS
    Sets up Cloud Monitoring dashboard and log-based metrics for Medication Orchestra.

.DESCRIPTION
    This script:
      1. Uploads the 4-chart monitoring dashboard to Cloud Monitoring
      2. Creates a log-based metric that counts 5xx errors from the backend
      3. Prints three direct bookmark links (Dashboard, Logs, Metrics)
      4. Shows manual instructions for setting up email alerts

.USAGE
    From the project root (run AFTER deploy.ps1):
        .\monitoring_setup.ps1
#>

# ---- Configuration (must match deploy.ps1) -----------------------------------
$PROJECT_ID   = "project-f9540f8f-d01e-47d3-a36"
$REGION       = "us-central1"
$SERVICE_NAME = "medication-orchestra-backend"

# ---- Helper ------------------------------------------------------------------
function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "[STEP] $msg" -ForegroundColor Cyan
}

function Write-Success([string]$msg) {
    Write-Host "  [OK] $msg" -ForegroundColor Green
}

function Write-Info([string]$msg) {
    Write-Host "  [..] $msg" -ForegroundColor Gray
}

# ---- Main --------------------------------------------------------------------
try {
    Write-Host ""
    Write-Host "=====================================================" -ForegroundColor Blue
    Write-Host " Medication Orchestra -- Monitoring Setup" -ForegroundColor Blue
    Write-Host "=====================================================" -ForegroundColor Blue

    # ---- Step 1: Upload Dashboard --------------------------------------------
    Write-Step "Step 1/3 -- Uploading 4-chart monitoring dashboard..."

    if (-not (Test-Path "monitoring_dashboard.json")) {
        throw "monitoring_dashboard.json not found. Make sure you are running from the project root."
    }

    gcloud monitoring dashboards create `
        --config-from-file="monitoring_dashboard.json" `
        --project $PROJECT_ID
    if ($LASTEXITCODE -ne 0) { throw "Failed to upload dashboard." }
    Write-Success "Dashboard uploaded to Cloud Monitoring"

    # ---- Step 2: Create Log-Based Metric for 5xx Errors ----------------------
    Write-Step "Step 2/3 -- Creating log-based metric for 5xx errors..."

    # Check if metric already exists
    $metricCheck = gcloud logging metrics describe medication_5xx_errors --project $PROJECT_ID 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Info "Log metric 'medication_5xx_errors' already exists -- skipping creation."
    } else {
        gcloud logging metrics create medication_5xx_errors `
            --description="5xx HTTP errors from medication-orchestra backend on Cloud Run" `
            --log-filter="resource.type=`"cloud_run_revision`" AND resource.labels.service_name=`"$SERVICE_NAME`" AND httpRequest.status>=500" `
            --project $PROJECT_ID
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [WARN] Could not create log metric (check permissions). Continuing..." -ForegroundColor Yellow
        } else {
            Write-Success "Log-based metric 'medication_5xx_errors' created"
        }
    }

    # ---- Step 3: Print Bookmark Links ----------------------------------------
    Write-Step "Step 3/3 -- Your monitoring bookmark links..."

    $encodedProject = [System.Uri]::EscapeDataString($PROJECT_ID)

    Write-Host ""
    Write-Host "=====================================================" -ForegroundColor Green
    Write-Host " [OK] Monitoring setup complete! Bookmark these links:" -ForegroundColor Green
    Write-Host ""
    Write-Host " [1] Dashboard (4 charts -- your main view):" -ForegroundColor Cyan
    Write-Host ("    https://console.cloud.google.com/monitoring/dashboards?project=" + $PROJECT_ID) -ForegroundColor White
    Write-Host ""
    Write-Host " [2] Live Logs (filter to your service):" -ForegroundColor Cyan
    Write-Host ("    https://console.cloud.google.com/run/detail/" + $REGION + "/" + $SERVICE_NAME + "/logs?project=" + $PROJECT_ID) -ForegroundColor White
    Write-Host ""
    Write-Host " [3] Service Metrics (requests, latency, instances):" -ForegroundColor Cyan
    Write-Host ("    https://console.cloud.google.com/run/detail/" + $REGION + "/" + $SERVICE_NAME + "/metrics?project=" + $PROJECT_ID) -ForegroundColor White
    Write-Host ""
    Write-Host "=====================================================" -ForegroundColor Yellow
    Write-Host " [!] To receive 5xx email alerts (manual step):" -ForegroundColor Yellow
    Write-Host ""
    Write-Host ("  1. Open: https://console.cloud.google.com/monitoring/alerting?project=" + $PROJECT_ID)
    Write-Host "  2. Click 'Create Policy'"
    Write-Host "  3. Click 'Select a metric' -> search 'medication_5xx_errors'"
    Write-Host "  4. Set threshold: Count > 5 in a 5-minute rolling window"
    Write-Host "  5. Add notification channel -> Email -> enter your address"
    Write-Host "  6. Name the policy: 'Medication Orchestra 5xx Alert'"
    Write-Host "  7. Save"
    Write-Host ""
    Write-Host " This takes ~2 minutes and gives you email on any backend error spike." -ForegroundColor Gray
    Write-Host "=====================================================" -ForegroundColor Yellow

} catch {
    Write-Host ""
    Write-Host "=====================================================" -ForegroundColor Red
    Write-Host (" [X] Monitoring setup failed: " + $_) -ForegroundColor Red
    Write-Host "=====================================================" -ForegroundColor Red
    exit 1
}
