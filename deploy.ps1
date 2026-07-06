<#
.SYNOPSIS
    One-command Cloud Run deployment for Medication Orchestra backend.

.DESCRIPTION
    Steps:
      1. Create the dedicated service account (if not already exists)
      2. Grant the required IAM roles to the service account
      3. Build and push the Docker image via Cloud Build
      4. Deploy the image to Cloud Run with ADC credentials
      5. Print the live backend URL and Flutter build command

.USAGE
    From the project root:
        .\deploy.ps1

    To update the backend after code changes, just run it again.
#>

# ---- Configuration (update these if you rename anything) --------------------
$PROJECT_ID   = "project-f9540f8f-d01e-47d3-a36"
$REGION       = "us-central1"
$SERVICE_NAME = "medication-orchestra-backend"
$SA_NAME      = "medication-orchestra-sa"
$SA_EMAIL     = "$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
$REPO_NAME    = "medication-orchestra"
$IMAGE        = "$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$SERVICE_NAME"

# ---- Helper ------------------------------------------------------------------
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

# ---- Main --------------------------------------------------------------------
try {
    Write-Host ""
    Write-Host "=====================================================" -ForegroundColor Blue
    Write-Host " Medication Orchestra -- Cloud Run Deploy" -ForegroundColor Blue
    Write-Host "=====================================================" -ForegroundColor Blue
    Write-Host " Project : $PROJECT_ID"
    Write-Host " Region  : $REGION"
    Write-Host " Service : $SERVICE_NAME"
    Write-Host " Image   : $IMAGE"
    Write-Host "=====================================================" -ForegroundColor Blue

    # ---- Step 1: Service Account ---------------------------------------------
    Write-Step "Step 1/5 -- Checking service account..."
    $saCheck = gcloud iam service-accounts describe $SA_EMAIL --project $PROJECT_ID 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Info "Service account not found. Creating..."
        gcloud iam service-accounts create $SA_NAME `
            --display-name="Medication Orchestra Backend" `
            --project $PROJECT_ID
        if ($LASTEXITCODE -ne 0) { throw "Failed to create service account." }
        Write-OK "Service account created: $SA_EMAIL"
    } else {
        Write-OK "Service account already exists: $SA_EMAIL"
    }

    # ---- Step 2: IAM Roles ---------------------------------------------------
    Write-Step "Step 2/5 -- Granting IAM roles to service account..."

    $roles = @(
        "roles/datastore.user",
        "roles/aiplatform.user",
        "roles/firebaseauth.admin",
        "roles/logging.logWriter"
    )

    foreach ($role in $roles) {
        Write-Info "Granting $role ..."
        gcloud projects add-iam-policy-binding $PROJECT_ID `
            --member="serviceAccount:$SA_EMAIL" `
            --role=$role `
            --quiet 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [WARN] Failed to grant $role (may already be granted, continuing...)" -ForegroundColor Yellow
        } else {
            Write-OK "$role granted"
        }
    }

    # ---- Step 3: Artifact Registry ------------------------------------------
    Write-Step "Step 3/6 -- Enabling Artifact Registry and creating image repository..."

    # Enable the API (idempotent)
    Write-Info "Enabling artifactregistry.googleapis.com ..."
    gcloud services enable artifactregistry.googleapis.com --project $PROJECT_ID 2>&1 | Out-Null

    # Create the Docker repository if it doesn't exist yet
    $repoCheck = gcloud artifacts repositories describe $REPO_NAME `
        --location $REGION `
        --project $PROJECT_ID 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Info "Repository not found. Creating $REPO_NAME in $REGION ..."
        gcloud artifacts repositories create $REPO_NAME `
            --repository-format=docker `
            --location=$REGION `
            --description="Medication Orchestra Docker images" `
            --project $PROJECT_ID
        if ($LASTEXITCODE -ne 0) { throw "Failed to create Artifact Registry repository." }
        Write-OK "Repository created: $REPO_NAME"
    } else {
        Write-OK "Repository already exists: $REPO_NAME"
    }

    # Cloud Build runs as PROJECT_NUMBER@cloudbuild.gserviceaccount.com
    # It needs artifactregistry.writer to push images
    Write-Info "Granting Cloud Build SA write access to Artifact Registry ..."
    $PROJECT_NUMBER = gcloud projects describe $PROJECT_ID --format="value(projectNumber)"
    $CB_SA = "$PROJECT_NUMBER@cloudbuild.gserviceaccount.com"
    gcloud projects add-iam-policy-binding $PROJECT_ID `
        --member="serviceAccount:$CB_SA" `
        --role="roles/artifactregistry.writer" `
        --quiet 2>&1 | Out-Null
    Write-OK "Artifact Registry ready"

    # ---- Step 4: Build Image ------------------------------------------------
    Write-Step "Step 4/6 -- Building Docker image via Cloud Build..."
    Write-Info "Uploading ./backend to Cloud Build (this takes 2-3 minutes)..."
    gcloud builds submit ./backend --tag $IMAGE --project $PROJECT_ID
    if ($LASTEXITCODE -ne 0) { throw "Cloud Build failed. Check output above for errors." }
    Write-OK "Image built and pushed: $IMAGE"

    # ---- Step 5: Deploy to Cloud Run ----------------------------------------
    Write-Step "Step 5/6 -- Deploying to Cloud Run..."
    gcloud run deploy $SERVICE_NAME `
        --image $IMAGE `
        --region $REGION `
        --project $PROJECT_ID `
        --service-account $SA_EMAIL `
        --platform managed `
        --allow-unauthenticated `
        --timeout 300 `
        --min-instances 0 `
        --max-instances 5 `
        --memory 512Mi `
        --cpu 1 `
        --set-env-vars "DEV_MODE=false"
    if ($LASTEXITCODE -ne 0) { throw "Cloud Run deployment failed. Check output above." }
    Write-OK "Deployed to Cloud Run"

    # ---- Step 6: Print URL --------------------------------------------------
    Write-Step "Step 6/6 -- Fetching service URL..."
    $SERVICE_URL = gcloud run services describe $SERVICE_NAME `
        --region $REGION `
        --project $PROJECT_ID `
        --format "value(status.url)"
    if ($LASTEXITCODE -ne 0) { throw "Could not retrieve service URL." }

    $healthUrl   = $SERVICE_URL + "/health"
    $profilesUrl = $SERVICE_URL + "/api/v1/profiles"
    $flutterCmd  = "flutter build apk --dart-define=API_BASE_URL=" + $SERVICE_URL

    Write-Host ""
    Write-Host "=====================================================" -ForegroundColor Green
    Write-Host " DEPLOYED -> $SERVICE_URL" -ForegroundColor Green
    Write-Host "=====================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host " 1. Verify health (expect {status:ok}):" -ForegroundColor Cyan
    Write-Host "    Invoke-RestMethod -Uri $healthUrl" -ForegroundColor White
    Write-Host ""
    Write-Host " 2. Build Flutter APK for production:" -ForegroundColor Cyan
    Write-Host "    $flutterCmd" -ForegroundColor White
    Write-Host ""
    Write-Host " 3. Verify DEV_MODE is OFF:" -ForegroundColor Cyan
    Write-Host "    GET $profilesUrl" -ForegroundColor White
    Write-Host "    Add header -> Authorization: Bearer bad-token" -ForegroundColor White
    Write-Host "    Expected  -> 401 Unauthorized (NOT 200)" -ForegroundColor White
    Write-Host "=====================================================" -ForegroundColor Green

} catch {
    Write-Host ""
    Write-Host "=====================================================" -ForegroundColor Red
    Write-Host " DEPLOY FAILED: $_" -ForegroundColor Red
    Write-Host "=====================================================" -ForegroundColor Red
    exit 1
}
