#!/usr/bin/env bash
#
# deploy.sh - one-command Cloud Run deployment for the Medication Orchestra
# backend, for Linux and macOS. It is the same deployment as deploy.ps1, and it
# ends with the same smoke test: the service must be ready, its knowledge base
# must be loaded, and an unauthenticated request must be rejected.
#
#   ./deploy.sh --project my-project-123 [--region asia-south1]
#               [--allow-unauthenticated] [--skip-smoke-test]
#
# Project resolution order: --project, $GOOGLE_CLOUD_PROJECT, `gcloud config`.
#
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-}"
# Health data about Indian households stays in India. asia-south1 (Mumbai) is
# the default, and a region outside DATA_REGIONS is refused rather than
# silently accepted - a deployment is the moment residency is actually decided.
REGION="${REGION:-asia-south1}"
DATA_REGIONS="${DATA_REGIONS:-asia-south1 asia-south2}"
SERVICE_NAME="medication-orchestra-backend"
SA_NAME="medication-orchestra-sa"
REPO_NAME="medication-orchestra"
ALLOW_UNAUTHENTICATED="${ALLOW_UNAUTHENTICATED:-false}"
SKIP_SMOKE_TEST="false"

step()  { printf '\n\033[36m[STEP] %s\033[0m\n' "$1"; }
ok()    { printf '  \033[32m[OK] %s\033[0m\n' "$1"; }
info()  { printf '  \033[90m[..] %s\033[0m\n' "$1"; }
warn()  { printf '  \033[33m[WARN] %s\033[0m\n' "$1"; }
fail()  { printf '  \033[31m[FAIL] %s\033[0m\n' "$1"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project|-p)             PROJECT_ID="$2"; shift 2 ;;
        --region|-r)              REGION="$2"; shift 2 ;;
        --service)                SERVICE_NAME="$2"; shift 2 ;;
        --allow-unauthenticated)  ALLOW_UNAUTHENTICATED="true"; shift ;;
        --skip-smoke-test)        SKIP_SMOKE_TEST="true"; shift ;;
        -h|--help)                sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

region_allowed() {
    local candidate="$1" allowed
    for allowed in $DATA_REGIONS; do
        [[ "$candidate" == "$allowed" ]] && return 0
    done
    return 1
}

if ! region_allowed "$REGION"; then
    echo "ERROR: refusing to deploy health data to '$REGION'." >&2
    echo "       Permitted regions: ${DATA_REGIONS}." >&2
    echo "       The privacy notice tells households their data is stored in India" >&2
    echo "       (docs/SECURITY_AND_PRIVACY.md, section 9). Change the notice first," >&2
    echo "       then DATA_REGIONS - not the other way round." >&2
    exit 2
fi

for tool in gcloud python3 curl; do
    command -v "$tool" >/dev/null || { echo "ERROR: $tool is required but not installed." >&2; exit 2; }
done

if [[ -z "$PROJECT_ID" ]]; then
    PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
fi
if [[ -z "$PROJECT_ID" || "$PROJECT_ID" == "(unset)" ]]; then
    echo "ERROR: no project. Pass --project, set GOOGLE_CLOUD_PROJECT, or run 'gcloud config set project <id>'." >&2
    exit 2
fi

SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}"

cat <<BANNER

=====================================================
 Medication Orchestra -- Cloud Run Deploy
=====================================================
 Project : ${PROJECT_ID}
 Region  : ${REGION}
 Service : ${SERVICE_NAME}
 Image   : ${IMAGE}
=====================================================
BANNER

# ---- Step 1: service account ------------------------------------------------
step "Step 1/6 -- Checking service account..."
if gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
    ok "Service account already exists: $SA_EMAIL"
else
    info "Service account not found. Creating..."
    gcloud iam service-accounts create "$SA_NAME" \
        --display-name "Medication Orchestra Backend" \
        --project "$PROJECT_ID"
    ok "Service account created: $SA_EMAIL"
fi

# ---- Step 2: IAM roles ------------------------------------------------------
step "Step 2/6 -- Granting IAM roles to the service account..."
# Each role is here because the service uses the API behind it. The previous
# script omitted Cloud Messaging, so dose reminders and SOS pushes failed on the
# first real deployment.
grant_role() {
    local role="$1" why="$2"
    info "Granting ${role} (${why}) ..."
    if gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member "serviceAccount:${SA_EMAIL}" --role "$role" --quiet >/dev/null 2>&1; then
        ok "${role} granted"
    else
        warn "Could not grant ${role} - grant it manually if that feature is enabled."
    fi
}
grant_role "roles/datastore.user"                 "Firestore reads and writes"
grant_role "roles/aiplatform.user"                "Vertex AI Gemini for OCR and phrasing"
grant_role "roles/firebaseauth.admin"             "verify Firebase ID tokens"
grant_role "roles/firebasecloudmessaging.admin"   "send dose reminders and SOS pushes"
grant_role "roles/logging.logWriter"              "Cloud Logging"

# ---- Step 3: Artifact Registry ---------------------------------------------
step "Step 3/6 -- Enabling Artifact Registry and creating the image repository..."
gcloud services enable artifactregistry.googleapis.com --project "$PROJECT_ID" >/dev/null
if gcloud artifacts repositories describe "$REPO_NAME" --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
    ok "Repository already exists: $REPO_NAME"
else
    info "Creating repository $REPO_NAME in $REGION ..."
    gcloud artifacts repositories create "$REPO_NAME" \
        --repository-format docker \
        --location "$REGION" \
        --description "Medication Orchestra Docker images" \
        --project "$PROJECT_ID"
    ok "Repository created: $REPO_NAME"
fi

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format 'value(projectNumber)')"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member "serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
    --role "roles/artifactregistry.writer" --quiet >/dev/null
ok "Artifact Registry ready"

# ---- Step 4: build ----------------------------------------------------------
step "Step 4/6 -- Building the image via Cloud Build..."
info "Uploading ./backend (2-3 minutes). The build fails if the knowledge base is missing."
gcloud builds submit ./backend --tag "$IMAGE" --project "$PROJECT_ID"
ok "Image built and pushed: $IMAGE"

# ---- Step 5: deploy ---------------------------------------------------------
step "Step 5/6 -- Deploying to Cloud Run..."
# DEV_AUTH_BYPASS is deliberately not passed: the service ignores it outside a
# local run, and not passing it means it cannot be inherited from the shell.
deploy_args=(
    run deploy "$SERVICE_NAME"
    --image "$IMAGE"
    --region "$REGION"
    --project "$PROJECT_ID"
    --service-account "$SA_EMAIL"
    --platform managed
    --timeout 300
    --min-instances 0
    --max-instances 5
    --memory 512Mi
    --cpu 1
    --set-env-vars "DEV_MODE=false,PROJECT_ID=${PROJECT_ID}"
)
if [[ "$ALLOW_UNAUTHENTICATED" == "true" ]]; then
    warn "--allow-unauthenticated: the API is reachable without Cloud Run IAM. Firebase auth still guards every endpoint."
    deploy_args+=(--allow-unauthenticated)
fi
gcloud "${deploy_args[@]}"
ok "Deployed to Cloud Run"

# ---- Step 6: smoke test -----------------------------------------------------
step "Step 6/6 -- Smoke-testing the deployment..."
SERVICE_URL="$(gcloud run services describe "$SERVICE_NAME" \
    --region "$REGION" --project "$PROJECT_ID" --format 'value(status.url)')"
[[ -n "$SERVICE_URL" ]] || { fail "Could not retrieve the service URL."; exit 1; }
info "Service URL: $SERVICE_URL"

failures=()

if [[ "$SKIP_SMOKE_TEST" == "true" ]]; then
    warn "--skip-smoke-test: /health, /readyz and the auth probe were not run."
else
    # 1. Knowledge base. /health returns 200 even with an empty corpus, so the
    #    payload is checked, not the status code - that empty corpus returning
    #    "no interactions found" is the failure this whole step exists for.
    health="$(curl -fsS --max-time 30 "${SERVICE_URL}/health" || true)"
    if [[ -z "$health" ]]; then
        failures+=("/health is unreachable")
    else
        # NOTE: no backslashes inside the f-strings below - Python before 3.12
        # rejects them, and this has to run on the operator's laptop, not only in
        # CI. (The first version of this script failed here on Python 3.11.)
        read -r verdict detail < <(printf '%s' "$health" | python3 -c '
import json, sys

try:
    payload = json.load(sys.stdin)
    kb = payload.get("knowledge_base") or {}
    ingredients = kb.get("ingredients", 0)
    rules = kb.get("interaction_rules", 0)
    review = payload.get("review_status") or ""
    if ingredients < 50 or rules < 10:
        print("fail", "implausibly small knowledge base: %s ingredients, %s rules"
              % (ingredients, rules))
    elif not review:
        print("fail", "no review status; the client cannot warn the user")
    else:
        print("ok", "%s ingredients, %s rules, review=%s" % (ingredients, rules, review))
except Exception as exc:
    print("fail", "unreadable /health payload: %s" % (exc,))
')
        if [[ "$verdict" == "ok" ]]; then
            ok "/health: ${detail}"
        else
            failures+=("/health: ${detail}")
        fi
    fi

    # 2. Readiness, which enforces the thresholds with a 503.
    if ready="$(curl -fsS --max-time 30 "${SERVICE_URL}/readyz" 2>/dev/null)" \
        && [[ "$(printf '%s' "$ready" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("ready"))')" == "True" ]]; then
        ok "/readyz: ready"
    else
        failures+=("/readyz is not ready")
    fi

    # 3. Authentication must be enforced. This is what a leftover
    #    DEV_AUTH_BYPASS or a permissive proxy would fail.
    status="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 \
        -H 'Authorization: Bearer definitely-not-a-valid-token' \
        "${SERVICE_URL}/api/v1/profiles" || echo 000)"
    case "$status" in
        401|403) ok "/api/v1/profiles with an invalid token: ${status} as expected" ;;
        000)     failures+=("the authentication probe could not reach the service") ;;
        *)       failures+=("an invalid token produced HTTP ${status} instead of 401 - authentication is not enforced") ;;
    esac
fi

if (( ${#failures[@]} > 0 )); then
    printf '\n\033[31m=====================================================\033[0m\n'
    printf '\033[31m DEPLOYED BUT NOT HEALTHY: %s\033[0m\n' "$SERVICE_URL"
    printf '\033[31m=====================================================\033[0m\n'
    for f in "${failures[@]}"; do fail "$f"; done
    printf '\n Logs: gcloud run services logs read %s --region %s --project %s\n' \
        "$SERVICE_NAME" "$REGION" "$PROJECT_ID"
    exit 1
fi

printf '\n\033[32m=====================================================\033[0m\n'
printf '\033[32m DEPLOYED AND VERIFIED -> %s\033[0m\n' "$SERVICE_URL"
printf '\033[32m=====================================================\033[0m\n'
cat <<NEXT

 Build the Android client:
   flutter build apk --dart-define=API_BASE_URL=${SERVICE_URL}

 Firestore rules (deny-by-default; run once per rules change):
   firebase deploy --only firestore:rules --project ${PROJECT_ID}
NEXT
