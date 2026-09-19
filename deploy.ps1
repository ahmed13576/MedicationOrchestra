<#
.SYNOPSIS
    One-command Cloud Run deployment for the Medication Orchestra backend.

.DESCRIPTION
    Steps:
      1. Resolve the target project (parameter, env var, or gcloud config)
      2. Create the dedicated service account and grant the IAM roles it needs
      3. Create the Artifact Registry repository
      4. Build and push the image via Cloud Build
      5. Deploy to Cloud Run
      6. Smoke-test the deployment: the service must be ready, the knowledge base
         must be loaded, and an unauthenticated/invalid request must be rejected

    Step 6 exits non-zero if any of those fail, so a deployment that "succeeded"
    but cannot make a clinical judgement is reported as a failure.

.PARAMETER ProjectId
    Google Cloud project. Defaults to $env:GOOGLE_CLOUD_PROJECT, then to the
    project gcloud is configured with.

.PARAMETER Region
    Cloud Run region. Default: us-central1

.EXAMPLE
    .\deploy.ps1 -ProjectId my-project-123

.USAGE
    Run from the repository root. Re-run to update the backend after code changes.
#>

[CmdletBinding()]
param(
    [string]$ProjectId = $env:GOOGLE_CLOUD_PROJECT,
    [string]$Region = $(if ($env:REGION) { $env:REGION } else { "us-central1" }),
    [string]$ServiceName = "medication-orchestra-backend",
    [string]$SaName = "medication-orchestra-sa",
    [string]$RepoName = "medication-orchestra",
    [switch]$SkipSmokeTest,
    [switch]$AllowUnauthenticated
)

$ErrorActionPreference = "Stop"

# ---- Configuration ----------------------------------------------------------
if (-not $ProjectId) {
    $ProjectId = (gcloud config get-value project 2>$null)
    if (-not $ProjectId -or $ProjectId -eq "(unset)") {
        Write-Host "ERROR: no project. Pass -ProjectId, set GOOGLE_CLOUD_PROJECT, or run 'gcloud config set project <id>'." -ForegroundColor Red
        exit 2
    }
}
$SA_EMAIL = "$SaName@$ProjectId.iam.gserviceaccount.com"
$IMAGE = "$Region-docker.pkg.dev/$ProjectId/$RepoName/$ServiceName"

# ---- Helpers ----------------------------------------------------------------
function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "[STEP] $msg" -ForegroundColor Cyan
}

function Write-OK([string]$msg) {
    Write-Host "  [OK] $msg" -ForegroundColor Green
}

function Write-Info([string]$msg) {
    Write-Host "  [..] $msg" -ForegroundColor Gray
}

function Write-Warn([string]$msg) {
    Write-Host "  [WARN] $msg" -ForegroundColor Yellow
}

function Write-Fail([string]$msg) {
    Write-Host "  [FAIL] $msg" -ForegroundColor Red
}

function Invoke-Gcloud {
    param([string[]]$Arguments)
    $output = & gcloud @Arguments 2>&1
    return @{ ExitCode = $LASTEXITCODE; Output = $output }
}

# ---- Main -------------------------------------------------------------------
$smokeFailures = @()

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Blue
Write-Host " Medication Orchestra -- Cloud Run Deploy" -ForegroundColor Blue
Write-Host "=====================================================" -ForegroundColor Blue
Write-Host " Project : $ProjectId"
Write-Host " Region  : $Region"
Write-Host " Service : $ServiceName"
Write-Host " Image   : $IMAGE"
Write-Host "=====================================================" -ForegroundColor Blue

# ---- Step 1: Service Account ------------------------------------------------
Write-Step "Step 1/6 -- Checking service account..."
$saCheck = Invoke-Gcloud @("iam", "service-accounts", "describe", $SA_EMAIL, "--project", $ProjectId)
if ($saCheck.ExitCode -ne 0) {
    Write-Info "Service account not found. Creating..."
    gcloud iam service-accounts create $SaName `
        --display-name "Medication Orchestra Backend" `
        --project $ProjectId
    if ($LASTEXITCODE -ne 0) { throw "Failed to create service account." }
    Write-OK "Service account created: $SA_EMAIL"
} else {
    Write-OK "Service account already exists: $SA_EMAIL"
}

# ---- Step 2: IAM Roles ------------------------------------------------------
Write-Step "Step 2/6 -- Granting IAM roles to the service account..."
Write-Info "The service needs Firestore, the model (extraction/phrasing only), and FCM."

$roles = @(
    @{ Id = "roles/datastore.user";              Why = "Firestore reads and writes" },
    @{ Id = "roles/aiplatform.user";             Why = "Vertex AI Gemini for OCR and phrasing" },
    @{ Id = "roles/firebaseauth.admin";          Why = "verify Firebase ID tokens" },
    @{ Id = "roles/firebasecloudmessaging.admin"; Why = "send dose reminders and SOS pushes" },
    @{ Id = "roles/logging.logWriter";           Why = "Cloud Logging" }
)

foreach ($role in $roles) {
    Write-Info "Granting $($role.Id) ($($role.Why)) ..."
    gcloud projects add-iam-policy-binding $ProjectId `
        --member "serviceAccount:$SA_EMAIL" `
        --role $role.Id `
        --quiet 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Could not grant $($role.Id) - grant it manually if the feature is enabled."
    } else {
        Write-OK "$($role.Id) granted"
    }
}

# ---- Step 3: Artifact Registry ---------------------------------------------
Write-Step "Step 3/6 -- Enabling Artifact Registry and creating the image repository..."

gcloud services enable artifactregistry.googleapis.com --project $ProjectId 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not enable artifactregistry.googleapis.com." }

$repoCheck = Invoke-Gcloud @("artifacts", "repositories", "describe", $RepoName, "--location", $Region, "--project", $ProjectId)
if ($repoCheck.ExitCode -ne 0) {
    Write-Info "Repository not found. Creating $RepoName in $Region ..."
    gcloud artifacts repositories create $RepoName `
        --repository-format docker `
        --location $Region `
        --description "Medication Orchestra Docker images" `
        --project $ProjectId
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the Artifact Registry repository." }
    Write-OK "Repository created: $RepoName"
} else {
    Write-OK "Repository already exists: $RepoName"
}

# Cloud Build runs as PROJECT_NUMBER@cloudbuild.gserviceaccount.com
$PROJECT_NUMBER = gcloud projects describe $ProjectId --format "value(projectNumber)"
$CB_SA = "$PROJECT_NUMBER@cloudbuild.gserviceaccount.com"
gcloud projects add-iam-policy-binding $ProjectId `
    --member "serviceAccount:$CB_SA" `
    --role "roles/artifactregistry.writer" `
    --quiet 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not grant Cloud Build Artifact Registry write access." }
Write-OK "Artifact Registry ready"

# ---- Step 4: Build Image ----------------------------------------------------
Write-Step "Step 4/6 -- Building the image via Cloud Build..."
Write-Info "Uploading ./backend (2-3 minutes). The build fails if the knowledge base is missing."
gcloud builds submit ./backend --tag $IMAGE --project $ProjectId
if ($LASTEXITCODE -ne 0) { throw "Cloud Build failed. Check the output above." }
Write-OK "Image built and pushed: $IMAGE"

# ---- Step 5: Deploy to Cloud Run -------------------------------------------
Write-Step "Step 5/6 -- Deploying to Cloud Run..."

# NOTE: DEV_AUTH_BYPASS is deliberately not set here - the service ignores it
# outside local development, but not setting it means it cannot be inherited.
$deployArgs = @(
    "run", "deploy", $ServiceName,
    "--image", $IMAGE,
    "--region", $Region,
    "--project", $ProjectId,
    "--service-account", $SA_EMAIL,
    "--platform", "managed",
    "--timeout", "300",
    "--min-instances", "0",
    "--max-instances", "5",
    "--memory", "512Mi",
    "--cpu", "1",
    "--set-env-vars", "DEV_MODE=false,PROJECT_ID=$ProjectId"
)
if ($AllowUnauthenticated -or $env:ALLOW_UNAUTHENTICATED -eq "true") {
    Write-Warn "-AllowUnauthenticated: the API is reachable without Cloud Run IAM. Firebase auth still guards every endpoint."
    $deployArgs += "--allow-unauthenticated"
}

gcloud @deployArgs
if ($LASTEXITCODE -ne 0) { throw "Cloud Run deployment failed. Check the output above." }
Write-OK "Deployed to Cloud Run"

# ---- Step 6: Smoke test -----------------------------------------------------
Write-Step "Step 6/6 -- Smoke-testing the deployment..."

$SERVICE_URL = gcloud run services describe $ServiceName `
    --region $Region --project $ProjectId --format "value(status.url)"
if ($LASTEXITCODE -ne 0 -or -not $SERVICE_URL) { throw "Could not retrieve the service URL." }
Write-Info "Service URL: $SERVICE_URL"

if (-not $SkipSmokeTest) {
    # 1. Liveness AND the knowledge base. /health reports the corpus it loaded,
    #    so a deploy that shipped without it is caught here rather than by the
    #    first caregiver who is told "no interactions found".
    try {
        $health = Invoke-RestMethod -Uri "$SERVICE_URL/health" -TimeoutSec 30
        $kb = $health.knowledge_base
        if ($kb.ingredients -lt 50 -or $kb.interaction_rules -lt 10) {
            $smokeFailures += "/health reports an implausibly small knowledge base: $($kb | ConvertTo-Json -Compress)"
        } elseif (-not $health.review_status) {
            $smokeFailures += "/health reports no review status; the client cannot warn the user"
        } else {
            Write-OK "/health: $($kb.ingredients) ingredients, $($kb.interaction_rules) rules, review=$($health.review_status)"
        }
    } catch {
        $smokeFailures += "/health is unreachable: $_"
    }

    # 2. Readiness, which enforces the knowledge-base thresholds with a 503.
    try {
        $ready = Invoke-RestMethod -Uri "$SERVICE_URL/readyz" -TimeoutSec 30
        if ($ready.ready -eq $true) {
            Write-OK "/readyz: ready"
        } else {
            $smokeFailures += "/readyz says not ready: $($ready.checks | ConvertTo-Json -Compress)"
        }
    } catch {
        $smokeFailures += "/readyz failed: $_"
    }

    # 3. A protected endpoint must reject a token it cannot verify. This is the
    #    check that a DEV_AUTH_BYPASS or a permissive proxy would fail. Written
    #    for Windows PowerShell 5.1 as well as 7+, so it uses the exception
    #    rather than -SkipHttpErrorCheck.
    try {
        $resp = Invoke-WebRequest -Uri "$SERVICE_URL/api/v1/profiles" `
            -Headers @{ Authorization = "Bearer definitely-not-a-valid-token" } `
            -TimeoutSec 30 -UseBasicParsing -ErrorAction Stop
        $smokeFailures += "an invalid token was accepted with HTTP $($resp.StatusCode) - authentication is not enforced"
    } catch {
        $status = $null
        if ($_.Exception.Response) {
            $status = [int]$_.Exception.Response.StatusCode
        }
        if ($status -eq 401 -or $status -eq 403) {
            Write-OK "/api/v1/profiles with an invalid token: $status as expected"
        } elseif ($status) {
            $smokeFailures += "an invalid token produced HTTP $status instead of 401"
        } else {
            $smokeFailures += "the authentication probe could not reach the service: $_"
        }
    }
} else {
    Write-Warn "-SkipSmokeTest: /health, /readyz and the auth probe were not run."
}

Write-Host ""
if ($smokeFailures.Count -gt 0) {
    Write-Host "=====================================================" -ForegroundColor Red
    Write-Host " DEPLOYED BUT NOT HEALTHY: $SERVICE_URL" -ForegroundColor Red
    Write-Host "=====================================================" -ForegroundColor Red
    foreach ($failure in $smokeFailures) { Write-Fail $failure }
    Write-Host ""
    Write-Host " Logs: gcloud run services logs read $ServiceName --region $Region --project $ProjectId" -ForegroundColor Cyan
    exit 1
}

$flutterCmd = "flutter build apk --dart-define=API_BASE_URL=$SERVICE_URL"

Write-Host "=====================================================" -ForegroundColor Green
Write-Host " DEPLOYED AND VERIFIED -> $SERVICE_URL" -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Green
Write-Host ""
Write-Host " Build the Android client:" -ForegroundColor Cyan
Write-Host "   $flutterCmd" -ForegroundColor White
Write-Host ""
Write-Host " Firestore rules (deny-by-default; run once per rules change):" -ForegroundColor Cyan
Write-Host "   firebase deploy --only firestore:rules --project $ProjectId" -ForegroundColor White
Write-Host ""
