# 💊 Medication Orchestra — household medication safety

**Stack:** Flutter (client) + FastAPI (backend) + a deterministic clinical rule
engine + Vertex AI Gemini for *reading* and *phrasing* only.

> Photograph a prescription or a medicine strip. The app reads it, identifies the
> active ingredients, checks them against a curated interaction knowledge base —
> and tells you plainly when it could not identify something, instead of
> reassuring you.

---

## What this is (and what it deliberately is not)

**The clinical decision is made by deterministic code, not by a model.**
`backend/services/medication_registry.py` resolves brands and salts by exact
match; `backend/services/clinical_engine.py` applies curated, cited rules,
duplicate-ingredient arithmetic and gap constraints. Gemini reads handwriting and
rephrases an explanation into Hindi or Tamil, and that is all it does. Every
response reports `"model_calls_in_decision_path": 0`, and the invariant audit
proves the findings are identical when the model is unreachable.

**It never says "safe".** Every response carries a coverage ledger. If a medicine
could not be identified, it is named in `unchecked[]` with a reason, and the
client shows an amber "not fully checked" panel instead of a green tick. Before
the rewrite the app showed a green tick over an *empty* corpus.

**It never mixes two patients.** `profile_id=all` checks each person separately;
there is no code path that pairs one household member's medicine with another's.

**A rule only fires across an interaction.** Each rule declares the mechanism
groups it connects (a blood thinner, an NSAID, an SSRI) and only pairs ingredients
from *different* groups. Two painkillers of the same class are not "an
antidepressant plus a painkiller"; that false alarm used to be reachable because
the rule listed a whole drug class as one flat ingredient list. Groups are
validated at load time — a rule with overlapping or missing groups stops the
service rather than mis-firing.

**An interval in the citation is an interval in the schedule.** Where a rule's own
cited advice names a time (the aspirin/NSAID rule says 8 hours), the rule carries
`min_gap_hours` and the solver and the independent verifier enforce that number,
not a generic severity default.

**The knowledge base has not been reviewed by a clinician yet.** The shipped set
(195 ingredients, 33 rules, 175 brand presentations) is marked
`DEMONSTRATION SET - not clinician-reviewed` in the data, in `/health`, and on
screen. The source of every rule is recorded in
[docs/KNOWLEDGE_SOURCES.md](docs/KNOWLEDGE_SOURCES.md); the licensing history
matters — the original corpus derived from DrugBank and could not legally be
shipped in a commercial product, so it was removed entirely.

---

## Architecture

```mermaid
flowchart TD
    A[Prescription / strip photo] --> B[image_service<br>sniff MIME, cap size,<br>strip EXIF, downscale]
    B --> C[gemini_service<br>extraction only]
    C --> D[medication_registry<br>exact brand + salt identity]
    D --> E[clinical_engine<br>interactions, duplicate salts,<br>ceilings, gap constraints,<br>independent schedule verifier]
    E --> F[explanation_service<br>phrasing only, validated<br>falls back to curated text]
    E --> G[(Firestore<br>users/{uid}/…)]
    E --> H[SOS via FCM<br>tenant-scoped device index]
```

| Layer | Files |
|---|---|
| API | `backend/main.py` (v2.0.0), `docs/API.md` |
| Clinical core | `backend/services/medication_registry.py`, `clinical_engine.py` |
| Knowledge base | `backend/knowledge/*` — see `docs/KNOWLEDGE_SOURCES.md` |
| Model surface (phrasing only) | `backend/services/explanation_service.py` |
| Model surface (vision only) | `backend/services/gemini_service.py` |
| Safety, privacy, audit | `auth_service.py`, `image_service.py`, `rate_limit.py`, `audit_service.py`, `notification_service.py`, `firestore.rules`, `docs/SECURITY_AND_PRIVACY.md` |
| Client | `medication_orchestra/lib/` |

The ADK agent classes that used to live in `backend/agents/` were deleted: they
described an orchestration that the code did not implement, made the model a
decision-maker, and were the source of the "no interactions found" failure.

---

## Running it locally

### The one-command demo (no Google Cloud account needed)
```bash
cd backend && python3 dev_server.py         # http://localhost:8080
```
It replaces Firestore with an in-memory store, stubs auth and FCM, and seeds a
demo household: an older patient on warfarin, Brufen, Dolo 650, Combiflam,
Ecosprin and Clopilet, plus one illegible entry so the coverage ledger has
something to report. The app itself is the real one — same engine, same rules.
```bash
curl -s localhost:8080/api/v1/interactions -H 'Authorization: Bearer demo'
curl -s -X POST localhost:8080/api/v1/schedule/generate -H 'Authorization: Bearer demo'
```
It prints a warning banner and binds to all interfaces: never expose it.

### Backend against real Google Cloud
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

export PROJECT_ID=your-gcp-project-id     # required; the service refuses to start without it
export DEV_AUTH_BYPASS=true               # local only; ignored when K_SERVICE is set
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```
Health: `curl localhost:8080/health` · readiness: `curl localhost:8080/readyz`

### Tests (no credentials, no network — the cloud clients are stubbed)
```bash
cd backend && python -m pytest tests/ -q          # 140 tests
python scripts/safety_invariant_audit.py          # 46 checks, exits 0 only if every invariant holds
```

### Client
```bash
cd medication_orchestra
flutter pub get
flutter test
flutter run --dart-define=API_BASE_URL=http://<your-lan-ip>:8080
```

### Container
```bash
docker build -t medication-orchestra ./backend
docker run -p 8080:8080 -e PROJECT_ID=your-project medication-orchestra
```
The build **fails** if the knowledge base is missing or implausibly small, so a
container that cannot make a clinical judgement can never be deployed.

---

## What the safety invariants guarantee

`scripts/safety_invariant_audit.py` executes the real code and exits non-zero on
any violation. The ten invariants:

| # | Invariant |
|---|---|
| INV-1 | The clinical knowledge base ships with the service (in the image, checked at build time, at `/readyz`, and in CI) |
| INV-2 | An empty, unconfigured or knowledge-base-less service fails loud — never a green "safe" |
| INV-3 | Drug identity is exact: `cortisone ≠ hydrocortisone`, `ampicillin ≠ pivampicillin`, and the real `aspirin + clopidogrel` pair *is* found. A rule never fires on two drugs from the same side of the interaction, and every rule's mechanism groups are disjoint and complete |
| INV-4 | A medicine that could not be identified is reported as unchecked, with a reason |
| INV-5 | Duplicate ingredients are visible with the arithmetic (Dolo 650 + Combiflam = 975 mg per dose-time, 2600 mg/day) and a citation |
| INV-6 | The schedule honours required gaps — including a rule's own cited interval — or says it could not; an independent verifier re-checks the result, and an alert names only the medicines that actually interact |
| INV-7 | No model participates in a clinical decision |
| INV-8 | Two patients' medicines are never paired |
| INV-9 | Acknowledgements persist, and SOS references a finding that really exists |
| INV-10 | Access is tenant-scoped and profile-scoped, with an audit trail |

---

## Cloud Run deployment

The same deployment exists for both platforms, and both end with the same smoke
test:

```powershell
.\deploy.ps1 -ProjectId my-project-123          # Windows
```
```bash
./deploy.sh --project my-project-123            # Linux / macOS
```

It takes the project from `-ProjectId`, `$env:GOOGLE_CLOUD_PROJECT`, or your
`gcloud config` — nothing is hardcoded. It creates the service account and grants
the roles the service actually uses (`datastore.user`, `aiplatform.user`,
`firebaseauth.admin`, `firebasecloudmessaging.admin`, `logging.logWriter`),
builds via Cloud Build, deploys with `DEV_MODE=false` and `PROJECT_ID` set, then
**smoke-tests what it deployed**:

1. `/health` reports a plausible knowledge base (ingredients, rules, review status);
2. `/readyz` is ready;
3. `/api/v1/profiles` with an invalid token returns 401.

Any of those failing makes the script exit non-zero and print the log command —
a deployment that cannot make a clinical judgement is reported as a failure, not
as success. Use `-SkipSmokeTest` only when you intend to run the checks yourself.
The API is public unless you pass `-AllowUnauthenticated` explicitly (Firebase
auth still guards every endpoint); set `ALLOWED_ORIGINS` for browser clients,
which are denied by default outside localhost.

---

## Repository map

```text
backend/
├── main.py                  # API (v2.0.0)
├── knowledge/               # ingredients.json, interactions.json, brand_mapping.csv
├── services/                # registry, engine, model surfaces, auth, audit, limits, images
├── agents/agent_security.py # input sanitisation + output validation
├── dev_server.py            # run the real app locally with no credentials
└── tests/                   # 140 tests, the in-memory Firestore fake, strip fixtures
medication_orchestra/        # Flutter client
scripts/safety_invariant_audit.py
deploy.ps1 / deploy.sh       # Cloud Run deploy, smoke-tested (same gates in both)
monitoring_setup.ps1         # dashboard + 5xx log metric
docs/                        # API, knowledge sources, security & privacy, history, review, plan
```

## Documentation

* [docs/API.md](docs/API.md) — the endpoint contract
* [docs/HISTORY.md](docs/HISTORY.md) — what was deleted from this repository, and why
* [docs/KNOWLEDGE_SOURCES.md](docs/KNOWLEDGE_SOURCES.md) — where every clinical claim comes from, and its licence
* [docs/SECURITY_AND_PRIVACY.md](docs/SECURITY_AND_PRIVACY.md) — auth, tenancy, consent, audit, DPDP status
* [docs/ADVERSARIAL_REVIEW.md](docs/ADVERSARIAL_REVIEW.md) — the review that produced this work, with fix status
* [docs/YC_PRODUCT_PLAN.md](docs/YC_PRODUCT_PLAN.md) — product, pricing and go-to-market

## Medical disclaimer

This software is a safety aid, not a substitute for a doctor or pharmacist. Its
knowledge base is a demonstration set that has not been reviewed by a clinician.
Always confirm with a healthcare professional before starting, stopping or
changing any medicine.
