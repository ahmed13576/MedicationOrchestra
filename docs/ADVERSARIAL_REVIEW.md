# Adversarial Review — Medication Orchestra

**Reviewer stance:** assume the product is a real, shipped clinical decision aid used by a
72-year-old with a heart condition and by an adult child who is responsible for her. Assume a
malicious label photo, a quota error, a stale cache, and a lawyer. Then ask what the code
actually does.

**Method:** static reading of every backend and client file, plus ten executed experiments against
the real code with only the Google Cloud clients stubbed (no credentials, no network). Every claim
below is reproducible with `python3 scripts/safety_invariant_audit.py`, whose output is quoted
verbatim where relevant. The full repo is 160 files; the safety-critical path is ~3,000 lines.

**Verdict in one paragraph:** this is a well-presented hackathon project whose *headline* feature —
"cross-references all medications … against a database of 20,000+ interactions" — is a **silent
no-op in the deployed configuration**, because the interaction corpus is excluded from both the
container image and the repository. Every household therefore gets HTTP 200, `count: 0`, and a green
checkmark reading *"Your household medications appear to be safe to take together."* The second
safety property — a minimum time gap between interacting drugs — is **enforced only by asking Gemini
nicely**, with no post-generation verification and a degraded path that provably violates the gap.
The app is not dishonest by design; it is dishonest by *failure mode*. That distinction matters,
because it is fixable in days and it is the difference between "demo" and "product".

---

## Fix status (updated after the remediation pass)

The audit command in §0 now exits **0**: `43/43` checks hold, and the test-suite
is `127 passed`. The table below is the honest ledger of what changed and what
still has not.

| # | Finding | Status | Evidence |
|---|---|---|---|
| F-01 | Interaction engine has no data in the deployed artifact | **Fixed** | knowledge base moved into `backend/knowledge/`, shipped by `Dockerfile` (`COPY knowledge/` + a build-time load check), `.dockerignore` no longer excludes it, `/readyz` gates on it, CI builds the image and proves the corpus is inside, plus a check that the service refuses to start without it. INV-1. |
| F-02 | "6-hour gap" is a prompt, not a constraint | **Fixed** | the model no longer schedules. `clinical_engine.build_schedule()` is a deterministic solver, and `verify_schedule()` independently re-checks the finished payload; a disagreement downgrades the result to `unsafe_conflict`. INV-6, `tests/test_clinical_engine.py`. |
| F-03 | Substring drug identity | **Fixed** | exact-match registry (`resolve_medication`), no fuzzy path; `cortisone ≠ hydrocortisone`, `ampicillin ≠ pivampicillin`, real `aspirin + clopidogrel` now found. INV-3, `tests/test_drug_identity.py`. |
| F-04 | "Couldn't read this tablet" rendered as "you are safe" | **Fixed** | coverage ledger on every response; `unchecked[]` names each medicine and why; the client shows an amber "not fully checked" panel unless `is_complete`. INV-2/INV-4, `test_empty_household_never_claims_safety`. |
| F-05 | Duplicate-salt harm invisible | **Fixed** | duplicate-ingredient and dose-ceiling alerts with per-product and per-day arithmetic plus a citation; 4 ingredients carry explicit ceilings. INV-5. |
| F-06 | UI profile-scoped, backend household-scoped | **Fixed** | `profile_id` is honoured; `all` checks each patient separately; unknown profile → 404; the client sends its real profile. INV-8/INV-10. |
| F-07 | SOS broken end-to-end, and a cross-tenant path | **Fixed** | alert ids are persisted and validated (404 otherwise), `collection_group("devices")` replaced by a tenant-scoped token index, delivery reported per recipient with reasons. INV-9. |
| F-08 | Security and abuse posture | **Mostly fixed** | MIME sniffing + size/pixel caps + EXIF stripping; sanitisation that keeps legitimate names; per-user rate limits; explicit CORS; `python-multipart` 0.0.32 (CVE-2024-53981); non-root container; no secrets in the image. Remaining: no pen-test; rate limits are in-process. |
| F-09 | Regulatory and privacy posture empty | **Partly fixed** | versioned consent + `consent_history`, audit trail on every health-data access, export and confirmed deletion, rules that deny client writes. Remaining: published privacy notice, grievance officer, breach runbook, consent-manager integration — listed in `docs/SECURITY_AND_PRIVACY.md` §8. |
| F-10 | Cache correctness | **Partly fixed** | Firestore no longer infers freshness from a possibly-null timestamp: `compute_invalidation_key()` combines the change timestamp with the knowledge-base versions, so correcting a rule invalidates every cached answer. Remaining: the client's on-device cache is still timestamp-based. |
| F-11 | No tests, no CI, no evaluation harness | **Fixed** | 127 tests (in-memory Firestore, no credentials), 43 executed invariant checks, and `.github/workflows/ci.yml` running ruff, pip-audit, the invariant audit, pytest, a knowledge-base integrity check and a container build. Remaining: no evaluation corpus for extraction accuracy. |
| F-12 | Dependency drift, dead weight, one live CVE | **Mostly fixed** | every pin exact and current (fastapi 0.141.1, pillow 12.3.0, firebase-admin 7.6.0, python-multipart 0.0.32); `google-cloud-aiplatform` removed; `Pillow` is now genuinely used. Remaining: `flutter_local_notifications` 17.x stayed put because the bump cannot be verified without a device build. |
| F-13 | Cost and latency structurally bad | **Partly fixed** | the decision path makes zero model calls; the vision path is rate-limited; the schedule is computed locally in milliseconds; the DDI corpus no longer needs indexing. Remaining: no load test, no per-household COGS telemetry. |
| F-14 | Delivery gaps between pitch and app | **Fixed** | `README.md` rewritten to describe the system that exists; the ADK agents that described non-existent orchestration were deleted; the manifest no longer advertises ADK. |
| F-15 | Repository hygiene and documentation drift | **Mostly fixed** | hardcoded project id removed (test-enforced), `agents/.adk/session.db` untracked, generated corpus removed and gitignored, provenance documented in `docs/KNOWLEDGE_SOURCES.md`. Remaining: some `gumloop/` and `adapters/` skill documents still describe the old design. |

**The one thing that did not change:** the knowledge base is still
`DEMONSTRATION SET - not clinician-reviewed`. That is a deliberate line — the
fixes made the system honest about its coverage, but honesty is not the same as
clinical validation, and the product must not charge money until a pharmacist has
signed the rules off (`docs/KNOWLEDGE_SOURCES.md` §3).

---

## 0. Reproduction

```bash
# from the repository root
python3 scripts/safety_invariant_audit.py        # 0/10 safety invariants hold
```

Artifacts quoted in this review (all generated locally, nothing mocked except GCP clients):

| Artifact | What it proves |
|---|---|
| `scripts/safety_invariant_audit.py` | Encodes 10 claimed safety properties as executable checks |
| container simulation | `_DDI_RECORDS == 0`, `VECTOR_SEARCH_AVAILABLE == False`, `get_interaction(...) == []` |
| matcher probe | `cortisone ~ hydrocortisone`, `ampicillin ~ pivampicillin` all match as the *same* drug |
| household probe | dad's warfarin + mom's ibuprofen raise a "major interaction" alert |
| schedule probe | fallback places warfarin + ibuprofen in the same slot when a 4 h gap is required |
| scheduler probe | a schedule with a **falsified** `safety_notes` field is persisted verbatim |
| 50k-row benchmark | corpus load 0.11 s; 45-pair scan 0.53 s of blocking CPU per request |

---

## 1. Severity legend

| Level | Meaning |
|---|---|
| **S0 — Blocker** | The product cannot truthfully ship; a user can be harmed or misled by the default path |
| **S1 — High** | Breaks the security, privacy, or regulatory posture; or destroys the value proposition in normal use |
| **S2 — Medium** | Will cost money, trust, or engineering velocity; fix before scaling |
| **S3 — Low** | Hygiene, polish, and doc drift |

---

## 2. S0 — Blockers

### F-01. The interaction engine has no data in the deployed artifact *(S0)*

**Evidence**

* `backend/.dockerignore:16` → `data/`
* `.gitignore` → `backend/data/ddi_processed.json`, `backend/data/ddi_index.jsonl`, `db_drug_interactions.csv`
* `git ls-files backend` → ships `data/*.py` only; no corpus file
* `backend/services/vector_search_service.py:33` → `logger.warning("ddi_processed.json not found -- run prepare_corpus.py first")`, then `_DDI_RECORDS` stays `[]`
* `vector_search_service.py:104-124` → `search_interactions_fallback()` loops over an empty list and returns `None`
* `interaction_checker_agent.py:118-121` → `ddi_results` empty → `continue` → the pair is dropped
* `interactions_screen.dart:319` → *"Your household medications appear to be safe to take together."*

**Observed (container simulation, exact code):**

```
records in memory: 0 | VECTOR_SEARCH_AVAILABLE: False
  get_interaction('warfarin', 'ibuprofen')   -> []
  get_interaction('aspirin', 'warfarin')     -> []
  get_interaction('clopidogrel', 'omeprazole') -> []
```

**Impact.** The single most dangerous failure mode a medication-safety product can have: **false
reassurance**. Not "the feature is off" — the feature reports *success with zero findings*. The README
sells a 20,000-interaction database; the container contains a warning log nobody reads. A caregiver
who checks this app instead of calling a pharmacist is actively worse off than one who never
installed it, because she now has a green checkmark.

**Also broken by the same exclusion:** `vector_search_config.json` (the Vertex AI endpoint handle)
lives in the same ignored directory, so the "RAG" path is dead too — `VECTOR_SEARCH_AVAILABLE` is
`False` in every environment, which means the fallback is not a fallback, it *is* the product.

**Fix (hours, not weeks).**
1. Bake a *pinned, versioned* corpus into the image (`COPY data/ddi_processed.json /app/data/`) or
   fetch it at startup from GCS/Git-LFS with a checksum, and **fail the Cloud Run health check** if
   the record count is below a floor. A `--no-traffic` deploy that loads 0 records must never
   receive traffic.
2. Record `corpus_version` on every interaction result and in the cache key.
3. Add the invariant to CI: `assert len(records) > 100_000` (or whatever the licensed corpus yields).

---

### F-02. The "minimum 6-hour gap" is a prompt, not a constraint *(S0)*

The README's fourth bullet promises that the scheduler "staggers conflicting drugs … with a minimum
6-hour gap". Here is the entire enforcement mechanism:

* `schedule_alert_agent.py:100-135` — the gaps are pasted into a Gemini prompt as JSON.
* `schedule_alert_agent.py:166` — `schedule = json.loads(text)` … then `:180-190` writes it straight
  to Firestore. `validate_agent_output` is **never called on the schedule at all**, and even if it
  were, it only checks top-level string keys of a flat dict — not a nested `dose_times[].medications[]`
  structure, and not the arithmetic of whether two drugs actually share a slot.
* `schedule_alert_agent.py:193-215` — `_fallback_schedule()` ignores `interactions` entirely and sets
  `interaction_warning: None` and `safety_notes: []` for every medication. This path runs **whenever
  Gemini errors, 429s, or the quota retry loop gives up** — i.e. on exactly the busy days.

**Observed (real code, stubbed Gemini):**

```
A. FALLBACK SCHEDULE (used whenever Gemini errors / 429 / quota)
  08:00  ['Warfarin 5mg (blood thinner)', 'Brufen (Ibuprofen 400)']   interaction_warning=[None, None]
  20:00  ['Warfarin 5mg (blood thinner)', 'Brufen (Ibuprofen 400)']   interaction_warning=[None, None]
  >>> required gap: 4h. Actual gap: 0 minutes. No warning. Silent violation.

B. GEMINI PATH
  accepted schedule total_dose_times: 1
  warfarin and ibuprofen both at: ['08:00']
  model-authored safety_notes stored as fact: ['Warfarin and Ibuprofen are kept safely apart']
```

**Impact.** The safety property that makes this product worth money is unenforced, unverified, and
falsifiable by the model itself. The app will confidently print a sentence claiming the opposite of
what the schedule does. If a patient bleeds, the artefact of record is a Firestore document
containing an AI-authored claim of safety that the code never checked.

**Fix.**
1. Move scheduling to a **deterministic constraint solver** (topological/CP on dose times, meal
   anchors, frequency codes, and gap constraints). Use the LLM only to *label* and *explain* the
   result, never to compute it.
2. Add `verify_schedule(schedule, interactions)` as a hard gate before persistence: recompute the
   gap for every interacting pair across all slots; reject or repair on violation; recompute
   `total_dose_times` rather than trusting the model.
3. Delete the fallback's claim to safety: if gaps cannot be satisfied, emit
   `"schedule_status": "unsafe_conflict"` with the specific pair and tell the user to consult a
   pharmacist, instead of quietly inventing a schedule.
4. Never persist a model-authored `safety_notes` string. Generate notes from the verified constraint
   set.

---

### F-03. Drug identity is substring matching — it fabricates matches and hides real ones *(S0)*

`vector_search_service.py:154-157`:

```python
def _token_match(query, corpus_name):
    q, c = query.strip(), corpus_name.strip()
    return q in c or c in q          # e.g. "cortisone" in "hydrocortisone" -> True
```

**Observed false positives (different drugs, same result):**

```
'cortisone' ~ 'hydrocortisone' -> True
'ampicillin' ~ 'pivampicillin' -> True
'codeine' ~ 'dihydrocodeine'  -> True
'cortisone' + 'warfarin' -> matched the HYDROCORTISONE record
```

**Observed false negatives (real interactions, reported as no interaction):**

```
'amoxicillin + clavulanic acid' + 'warfarin'        -> NO MATCH
'Aspirin (Acetylsalicylic Acid) 75mg' + 'Clopidogrel 75mg' -> NO MATCH
'Ferrous Ascorbate 100mg' + 'Levothyroxine 50mcg'   -> NO MATCH
```

Three compounding problems:

1. **Identity by substring** conflates distinct salts and prodrugs. Because the function is
   symmetric and the corpus scan keeps the *highest-severity* record found
   (`vector_search_service.py:115-130`), a user on cortisone can be shown a bleeding-risk alert
   derived from hydrocortisone. Wrong drug, wrong claim, delivered as clinical advice.
2. **Free-text generics never normalise.** Gemini returns `"Aspirin (Acetylsalicylic Acid) 75mg"`;
   the corpus stores `aspirin`. Exact-ish wording matches by luck, not design.
3. **`severity` is regex-inferred from prose** (`prepare_corpus.py:53-96`), including catch-alls
   `"may increase the risk"` and `"the risk or severity of adverse effects"` — the generic DrugBank
   sentence template — so a large fraction of the corpus is labelled **major** while genuine
   moderates are filtered out (`interaction_checker_agent.py:125` drops `minor`, and the 50,000-record
   cap at `prepare_corpus.py:28,180` silently discards tail records). Alert fatigue on one side,
   invisible false negatives on the other.

**Fix.** Replace string containment with a **normalised drug registry**: RxNorm/ATC/UNII identifiers
plus an explicit Indian brand→salt table with strengths; match on `(ingredient_id, strength)` tuples,
never on free text. Treat combination products as *sets* of ingredients, not opaque strings. Store a
curated severity per ingredient pair with provenance, not a regex verdict over marketing prose.

---

### F-04. "We couldn't read this tablet" is rendered as "you are safe" *(S0)*

* `interaction_checker_agent.py:103-106` — if either generic name is empty, `continue`. No record,
  no flag, no count.
* `agent_security.py:90-95` — if a sanitizer trigger matches, the name becomes `""` and the pair is
  dropped for the same reason. A hostile or merely unusual label *disables* the check silently.
* `main.py` returns `{interactions: [], count: 0}`; the client renders the green state above.

**Observed:** a medication named *"Unreadable label"* with no generic appears in **0 alerts and 0
"could not check" notices**.

**Impact.** The dangerous direction of error in a safety tool is the silent one. Coverage is never
communicated, so the user cannot distinguish "checked 8 drugs, 0 problems" from "checked 0 drugs,
8 problems". This is the single highest-leverage UX/safety change available: report
**"checked 8 of 11 medications; 3 could not be identified — ask your pharmacist about these"**.

**Fix.** First-class `unchecked` records in the API schema, a coverage banner in the client, and a
*hard* rule: if any medication is unresolved, the headline can never be green.

---

### F-05. The most common preventable harm in this population is invisible to the engine *(S0)*

`interaction_checker_agent.py:108` skips any pair whose generics are equal, and there is no
shared-ingredient analysis anywhere in the codebase.

**Observed:**

```
grandma takes Dolo 650 (paracetamol) AND Combiflam (paracetamol + ibuprofen)
-> duplicate-paracetamol alert: False        # 1300 mg paracetamol per dose-time, unflagged
```

Indian households *routinely* hold several brands containing the same salt — paracetamol, ibuprofen,
pantoprazole, amoxicillin. Double-dosing paracetamol is the leading cause of acute liver failure in
the West and a documented overdose pattern in India. The engine checks drug *pairs* and never
ingredient *totals*, and it has no notion of maximum daily dose, renal/hepatic adjustment, age, or
weight — so the four most useful clinical checks a caregiver actually needs are absent:

* duplicate active ingredient across products,
* cumulative daily dose vs ceiling (e.g. paracetamol 4 g/day; 3 g/day in the elderly),
* contraindicated-in-elderly screens (Beers criteria),
* renal/hepatic dose adjustment.

**Fix.** Build the interaction check on **ingredient multisets** and add a deterministic dose-ledger:
sum each ingredient per day, compare against a per-salt ceiling with age weighting, and surface
"you are already taking this ingredient in another tablet".

---

## 3. S1 — High

### F-06. The app is profile-scoped in the UI and household-scoped in the backend *(S1)*

* `firestore_service.py:35-53` — `get_household_medications()` walks **every profile** and returns one flat list.
* `interaction_checker_agent.py:79-100` — pairs *all* of them: dad's warfarin with mom's ibuprofen.
* `main.py:355` — `profile_id: str = Query(default="default")` is accepted and **never used**.
* `local_cache_service.dart` — keyed by `profileId`, storing household-wide data.

**Observed:**

```
ALERT: Warfarin [profile dad] + Brufen [profile mom] -> major
ALERT: Warfarin [profile dad] + Combiflam [profile grandma] -> major
```

**Impact.** The advertised "household check" is clinically meaningless: alerts between people who
never share a body. It is also a **false-alarm machine** — every extra profile multiplies spurious
alerts by n², which is precisely how you train a caregiver to ignore the red banner. Meanwhile the
schedule agent mixes every profile's medications into one daily timetable, and the profile-keyed
on-device cache means switching profiles shows you another person's data under your own name.

**Fix.** *Two* clearly separated products inside one app: (a) per-patient interaction check and
schedule, scoped by `profile_id` end-to-end and used as the cache key; (b) the household view as an
explicit *caregiver dashboard* that lists each patient's own alerts and never cross-pairs. Add an
integration test that a two-profile household produces zero cross-profile alerts.

### F-07. SOS is broken end-to-end — and it is a cross-tenant data path *(S1)*

* `main.dart:638` — sends `topInteraction['id'] ?? ''`, but interaction dicts have no `id` field
  (`interaction_checker_agent.py:130-152`), so the client posts `interaction_id: ""`.
* `main.py:529-533` — looks up `users/{uid}/interactions/{interaction_id}`; the only document ever
  written is `interactions/latest` (`firestore_service.py:294-309`). **Any** id the client could send
  misses, so SOS returns 404 every time.
* `main.py:539` — `db.collection_group("devices").where("token", "==", token)` scans **every user's**
  devices to resolve a token, then `main.py:560` **writes** `last_sos_received_at` into *another
  user's* document. That is a cross-tenant write triggered by any authenticated account, and a token
  existence oracle.
* No rate limit on the *sender*: the recipient-side limit (default 2 h) is the only brake, and on any
  error the loop falls through to `allowed_tokens.append(token)` — fail-open.

**Impact.** The emergency feature — the emotional heart of the pitch — has never worked. The
cross-tenant query is also a tenancy-boundary bug that a security reviewer will find in five minutes.
Additionally, family onboarding requires one person to copy a raw FCM token out of Settings and text
it to another: unusable for the actual customer (an adult child in another city, a grandparent who
does not know what a token is), and anyone who obtains the token can be spammed.

**Fix.** Have the server mint stable `interaction_id`s (hash of patient + ingredient pair + corpus
version) and persist per-interaction documents. Replace token sharing with phone-number invites
(SMS/WhatsApp deep link) server-side. Never use `collection_group` for authorisation decisions: look
the recipient up in an indexed `devices/{token_hash}` collection. Make SOS at-least-once with an
idempotency key, and return a delivery receipt the UI can show.

### F-08. Security and abuse posture *(S1)*

| Finding | Evidence | Why it matters |
|---|---|---|
| Unauthenticated multipart parse (CVE-2024-53981) | `requirements.txt` pins `python-multipart==0.0.12`; FastAPI 0.115.0 parses the form body at `routing.py:247` **before** `solve_dependencies()` at `:291` | CVSS 7.5 DoS, fixed in 0.0.18. Any unauthenticated caller can stall the event loop on a Cloud Run instance |
| Wildcard CORS **with** credentials | `main.py:63-64` | Invalid combination; with `--allow-unauthenticated` it invites browser-side abuse of a health API |
| No rate limiting, quotas, or cost caps | every endpoint; `scan` calls two Vision models per request | An account (or leaked token) can burn Vertex quota and money at line rate; `max-instances 5` is the only ceiling |
| Hard-coded project id in 8 files | `main.py:75`, `firestore_service.py:17`, `gemini_service.py:36`, agents, `deploy.ps1`, `monitoring_setup.ps1` | No dev/staging isolation; a fork silently writes to the author's production project |
| `DEV_MODE` auth bypass, inconsistent comparison | `auth_service.py:57` uses `== "true"`; `main.py:41` uses `.lower() == "true"` | One typo in env config = every request becomes `dev-user-123`; the deploy script's own verification step exists because the author knows it |
| Firestore rules allow client writes to the whole user tree | `firestore.rules:6` — `allow read, write` | Any client can rewrite medication history and acknowledged alerts, bypassing every server-side check; no field validation, no audit trail |
| MD5 for token identity | `main.py:409` | Not a secret, but the pattern signals hash-by-habit |
| No image validation; MIME hard-coded | `gemini_service.py:317` always sends `image/jpeg`; the repo's own fixtures are **AVIF** | Uploads of PNG/HEIC/AVIF are mislabelled to the model; arbitrary bytes reach Vertex. No decode/re-encode, no EXIF strip |

**Fix, in order:** bump `python-multipart>=0.0.18` (and while you are there, close the ~200 commits of
drift — see F-12); restrict CORS to the real client origins; add per-user rate limits and a Vertex
spend cap; move project ids to env vars with a **fail-closed** startup check; delete `DEV_MODE` from
the auth path entirely (use a local Firebase emulator instead); tighten rules to
`allow read: if request.auth.uid == userId; allow write: false` (all writes through the API) and add a
server-side audit log of every PHI read/write. Decode and re-encode every upload with Pillow (which is
already a dependency but never imported) and pass the real MIME type.

### F-09. Regulatory and privacy posture is empty *(S1)*

There is **no** privacy policy, terms of service, consent capture, data-deletion or export path,
audit trail, breach playbook, or data-residency decision anywhere in the repo (grep for
`consent|privacy|terms|dpdp|hipaa|gdpr` returns nothing). Meanwhile the app ingests prescription
images and medication lists — the most sensitive category of personal data — and ships them to
Vertex AI, stores them in Firestore under `us-central1`, and displays clinical guidance under a
header that reads like an all-clear.

Specifics that a serious buyer or investor will find:

* **Medical claims without a safety case.** "Appear to be safe to take together" is a clinical
  assertion. There is no evaluation set, no false-negative rate, no clinical reviewer, no documented
  intended use, and the only disclaimer is a trailing sentence appended by the *model* per the prompt
  (`interaction_checker_agent.py:60-75`).
* **DPDP Act 2023 (India):** no consent artefact, no purpose limitation, no notice, no
  data-principal rights (access/correction/erasure), no Data Protection Officer path, no breach
  notification plan; storage in `us-central1` with no transfer analysis.
* **HIPAA (US), if ever applicable:** no BAAs documented, no audit controls, no access logging, no
  minimum-necessary scoping, no risk analysis.
* **No provenance for clinical data.** The corpus is derived from a CSV with no license file, no
  citation, no version, and no date — so you cannot tell a regulator *why* an alert fired on a given
  day. For a decision-support tool this is the first thing an auditor asks for.

**Fix.** Before any public launch: publish a privacy policy + ToS; capture explicit, granular consent
(front door, not buried); add account deletion and data export; log every PHI access with
`who/what/when/why`; pin the corpus with a version, licence, and citation and record that version on
every result; adopt a written intended-use statement and keep marketing language inside it; set up a
clinical advisory review for alert copy; move to `asia-south1` with an explicit retention schedule.

### F-10. Cache correctness — the safety answer can outlive the data it came from *(S1)*

* `firestore_service.py:277,332` — `if household_updated_at is None or generated_at >= household_updated_at: HIT`.
  If the timestamp was never written (first-run failure, swallowed exception at `:231`), **every**
  cache is treated as valid forever.
* `interaction_checker_agent.py:300-315` — the grounding write-back is an untracked
  `asyncio.create_task(...)` that calls `update_medication_generic`, which in turn calls
  `touch_household_updated_at`. On Cloud Run, work spawned this way can outlive the request or be
  dropped; if it lands after `save_cached_interactions`, the cache is born stale.
* No corpus version in the cache key: fixing a dose rule or the corpus does **not** invalidate a
  single user's cached verdict.
* Acknowledgements never round-trip: `main.py:384-394` writes to `acknowledged_interactions/{id}`,
  nothing reads it back, and `interaction_checker_agent.py:151` hard-codes `"acknowledged": False`.
  The Flutter client mutates local state (`interactions_screen.dart:160`), so **every acknowledged
  alert comes back as new** on the next refresh — the classic alert-fatigue generator.
* `touch_household_updated_at` is called synchronously from an `async` handler
  (`main.py:213, 261`) — a blocking Firestore write on the event loop.

**Fix.** Treat "unknown timestamp" as *stale*. Make invalidation explicit and keyed
(`meds_hash + corpus_version + rule_version`). Replace fire-and-forget tasks with awaited writes or a
durable task queue. Store acknowledgements keyed by the server-minted interaction id and merge them
into the response.

---

## 4. S2 — Medium

### F-11. There is no test suite, no CI, and no evaluation harness *(S2)*

* No `.github/workflows`, no `pytest.ini`/`pyproject.toml`, no ruff/black/mypy config, no backend
  tests at all. `sanity_check.py` parses `main.py` for undefined names and counts packages in
  `requirements.txt` — it is a linter, not a test, and it is not wired into anything.
* The only test in the repo is `medication_orchestra/test/widget_test.dart`, a smoke test that pumps
  the app widget without Firebase initialised.
* There is **no evaluation set for the interaction logic**: no gold-standard pairs, no measured
  false-negative rate, no OCR accuracy set (the six files in `test/` are AVIF images with no expected
  output and no harness that reads them).

**Why this is the thing that actually kills the company:** you cannot price, sell, or defend a safety
product whose error rate you do not measure. Remediating F-01 through F-05 requires a regression suite
that fails when a known-dangerous pair stops being flagged.

**Fix.** `pytest` + `ruff` + `mypy` in a GitHub Actions workflow that runs
`scripts/safety_invariant_audit.py`; a versioned gold-standard set (start with 100 curated pairs the
WHO/BNF calls dangerous, 50 safe pairs, and 30 real Indian brand strings); a nightly eval that reports
false negatives, false positives, and OCR field accuracy; and a blocking gate on any regression.

### F-12. Dependency drift, dead weight, and one live CVE *(S2)*

Pinned vs latest (checked 2026-09-17):

| Package | Pinned | Latest | Note |
|---|---|---|---|
| fastapi | 0.115.0 | 0.141.1 | ~2 years of fixes |
| uvicorn | 0.30.6 | 0.53.0 | |
| firebase-admin | 6.5.0 | 7.6.0 | major version behind |
| google-genai | `>=1.10.0` | 2.24.0 | unbounded floor; SDK API churn |
| google-cloud-firestore | 2.19.0 | 2.30.0 | |
| python-multipart | 0.0.12 | 0.0.32 | **CVE-2024-53981**, fixed 0.0.18 |
| pydantic | 2.9.2 | 2.13.5 | |
| pillow | 10.4.0 | 12.3.0 | **never imported anywhere**; multiple CVEs since (11.3.0, 12.1.1, 12.2.0, 12.3.0) |

`google-genai>=1.10.0` with no upper bound plus a 2.x major version live is a "works on my machine
until PyPI moves" bug. There is no lock file — `requirements.txt` uses `==` for most and `>=` for one,
so two deploys a month apart are not the same software.

**Fix.** Adopt a lock file (`uv.lock`/`pip-compile`), Dependabot with a 7-day delay, and a
`pip-audit` step in CI. Delete `pillow` unless you actually use it (see F-08 — you should).

### F-13. Cost and latency are unmeasured and structurally bad *(S2)*

Measured locally on the real fallback path with a 50,000-record corpus:

```
corpus size on disk: 18.4 MB
cold load + index build (per Cloud Run instance): 0.11 s
one /api/v1/interactions run: 45 pairs, 45 hits, 0.53 s of pure-CPU linear scanning
```

The 0.53 s is *blocking* CPU inside an `async` handler (`get_interaction` is called synchronously at
`interaction_checker_agent.py:118`), on a service configured with **1 vCPU** and `max-instances 5`.
Worse is the call pattern: for every pair with a hit, `_generate_alert` is awaited **sequentially**
(`:133-137`), one Gemini request per pair. A realistic 10-medication household with 8 hits is 8 serial
LLM round-trips (~15–40 s) plus grounding calls for unknown brands — against a client `receiveTimeout`
of **60 s** (`interactions_screen.dart:100`). Bigger households will simply time out and fall back to
cached or error state, and every retry is billed again.

**Fix.** One batched LLM call per household (the explanations are independent; a single JSON-mode call
with an array of pairs cuts cost and latency by ~10×); move the corpus lookup into an in-process
index (`dict[(ingredient_a, ingredient_b)]`, O(1)) or keep it in a real vector/graph store; wrap CPU
work in `asyncio.to_thread`; add a semaphore plus a per-user daily budget; cache *explanations* by
`(ingredient_a, ingredient_b, severity, corpus_version)` so the tenth user with warfarin+ibuprofen
costs nothing. Then instrument cost per household-check as a first-class metric — the Google Cloud
dashboard in `monitoring_setup.ps1` measures latency and 5xx, i.e. infrastructure health, and says
nothing about whether the safety answers are correct.

### F-14. Delivery gaps between the pitch and the running app *(S2)*

| Promise | Reality |
|---|---|
| "sends alerts" / dose reminders | `notification_service.send_dose_reminder()` has **zero callers**; there is no scheduler (no Cloud Scheduler, no WorkManager, no cron). The reminder feature does not exist |
| "alerts your family if something goes wrong" | SOS returns 404 end-to-end (F-07) |
| "ADK multi-agent orchestration" | Hand-rolled `orchestrator.py` of plain `async` functions; no ADK, no agent runtime, no tool calling. `backend/agents/intake_agent.py` is a one-line comment; `agents-cli-manifest.yaml` and the committed `agents/.adk/session.db` are leftovers |
| "20,000+ interactions" | Corpus is generated locally, gitignored, and excluded from the image; the cap is 50,000 *post-filter*, minus everything the severity sort drops |
| Deployment | Windows-only PowerShell (`deploy.ps1`), no IaC, no staging, no rollback, no migrations, `--allow-unauthenticated`, image built with `COPY . .` including `agents/.adk/session.db` |
| Onboarding | Family members are added by copy-pasting raw FCM tokens between devices |

### F-15. Repository hygiene and documentation drift *(S2)*

* `backend/agents/.adk/session.db` — a 36 KB SQLite session database with a session named **"hi"**
  is committed and shipped in the image. It is stale junk at best and a conversation-data leak at
  worst.
* `firebase_options.dart` carries `'REDACTED_WEB_API_KEY'` / `'REDACTED_ANDROID_API_KEY'` while
  `android/app/google-services.json` ships a live API key and project id — so secrets were scrubbed
  inconsistently. Net effect: **a fresh clone cannot run the app at all**, and the README never says
  `flutterfire configure`.
* README's file tree lists `agents/interaction_agent.py` and `agents/schedule_agent.py`, which do not
  exist; the "3-layer security" section describes output validation that is never applied to the
  highest-risk output (the schedule).
* `docs/token-optimization-guide.md`, `docs/model-selection-playbook.md`, `docs/runbook.md`,
  `model_capabilities.yaml`, `adapters/*.md`, `gumloop/*SKILL.md`, `test_parser.ps1`, `sanity_check.py`
  are generic AI-coding-framework scaffolding, not product documentation. They inflate the repo and
  signal "generated, not owned".
* `docs/kaggle_writeup.md` links a cover image from `file:///c:/Users/...` and a GitHub repo that
  isn't this one.
* No `LICENSE`, no `CONTRIBUTING.md`, no `.env.example`, no CHANGELOG at the root.

---

## 5. Claim vs code — the table a technical due-diligence reviewer will build

| README claim | Code reality |
|---|---|
| "Cross-references all medications in the household against a database of 20,000+ interactions" | 0 records in the deployed artifact; returns `count: 0` for every user |
| "Staggers conflicting drugs with a minimum 6-hour gap" | Gap is a prompt instruction; the degraded path provably places them together; nothing verifies |
| "3-layer security system" | Input denylist + a flat-dict output check that never runs on the schedule; no security review, no tests |
| "Least-privilege Cloud Run footprint" | `--allow-unauthenticated`, wildcard CORS with credentials, client-writable Firestore rules, `DEV_MODE` auth bypass in the code path |
| "Google ADK multi-agent orchestration" | Hand-rolled async functions; no ADK; stub file named `intake_agent.py` |
| "Sends alerts / dose reminders" | `send_dose_reminder` is dead code; no scheduler exists |
| "Alerts your family if something goes wrong" | SOS 404s on every call |
| "Translates Indian brand names" | 50-entry hard-coded dict in `gemini_service.py`, plus a separate 37-row CSV that nothing reads |

---

## 6. Adversarial scenarios

**S-1 — The green checkmark (default path, no attacker).** A daughter photographs her father's four
prescription boxes. Vision works; brands resolve; the interaction engine runs on an empty corpus and
returns `[]`. The app says *"Your household medications appear to be safe to take together."* She
skips the pharmacist call. This happens for **every user on every check** in the deployed
configuration. It is not an edge case; it is the product.

**S-2 — The quarantined label.** A blister pack photo contains an unfamiliar brand. Grounding fails
(no Search grounding configured for the region, quota, or an odd name). `generic_name` stays empty →
pair skipped → the drug is invisible in the results. The user sees one alert (about the drugs that did
resolve) and reasonably concludes everything else was checked. There is no counter on screen to
contradict the inference.

**S-3 — The 429 that invents a schedule.** It is 9 p.m.; Vertex is throttling. `schedule_alert_agent`
falls back to `_fallback_schedule`, which drops both drugs into the same 08:00 slot with
`interaction_warning: None` and `safety_notes: []`. The schedule screen renders it as the day's plan.
Nothing in the UI says "Gemini failed, this is degraded".

**S-4 — The trustworthy lie.** A prompt-influenced or simply over-helpful model returns
`safety_notes: ["Warfarin and Ibuprofen are kept safely apart"]` alongside a schedule that puts them
in the same slot. The note is stored as data and rendered as fact. If there is a bleed, the audit
trail contains an AI-authored assertion of safety that no code ever verified.

**S-5 — The cross-patient alarm.** A caregiver manages both parents. Her father is on warfarin, her
mother on ibuprofen for a knee. The app raises a **major bleeding-risk alert** that is medically
meaningless, because the two prescriptions belong to two different people. She learns to dismiss
major alerts — which is exactly what you must not teach a caregiver.

**S-6 — The quota drain.** A single authenticated account loops `/api/v1/medications/scan` with 9 MB
JPEGs. Each request costs two Vision calls plus storage; there is no per-user rate limit, no spend
cap, and `max-instances 5`. On a $0-budget student project this is a denial-of-wallet; on a startup it
is the whole month's credits in an afternoon.

---

## 7. What is genuinely good (do not lose it)

1. **The clinical framing and the personal story** (`docs/kaggle_writeup.md`) — the warfarin/NSAID
   hospitalisation narrative is exactly the wedge a healthcare investor remembers. Keep it; it is the
   company's origin story.
2. **Indian prescription vocabulary in the vision prompt** — OD/BD/TDS/QID/HS/AC/PC, "1-0-1" dosing,
   brand-first packaging (`gemini_service.py:44-110`). This is real domain knowledge and a genuine
   differentiator against Western apps.
3. **Confidence scoring and forced review** (`calculate_confidence_score`, thresholds 80/50) — the
   right instinct: distinguish "read it confidently" from "guessed". It just needs to be wired to the
   coverage ledger.
4. **The two-level cache design intent** — invalidate interaction and schedule caches on medication
   change; skip the model when nothing changed. The bug is in the details (F-10), not the concept.
5. **Operational thoughtfulness for a student project** — structured JSON logging that Cloud Logging
   can parse, exponential backoff on 429 with a clear retry budget, non-root container, ADC instead of
   API keys, an offline-first Flutter cache with a visible "cached at" timestamp, soft-delete for
   auditability, retry-aware client error copy ("The AI is busy… try again in 30 seconds").
6. **The right architectural insight** — "the LLM reads, deterministic systems decide." The code does
   not yet honour it, but the author clearly reached the idea; the whole remediation plan is making
   the code match it.

---

## 8. Prioritised remediation plan

**Week 1 — make it honest (P0).**
1. Ship a pinned corpus in the image; fail health checks below a record floor; expose `corpus_version`. *(F-01)*
2. Delete the green empty state until coverage is known; add `unchecked[]` to the API and a coverage
   banner ("checked 8 of 11") to the client. *(F-04)*
3. Print `"schedule_status": "degraded"` when Gemini fails instead of silently emitting an unverified
   plan; never persist a model-authored safety note. *(F-02)*
4. Wire `scripts/safety_invariant_audit.py` into CI as a blocking gate. *(F-11)*

**Week 2–3 — make it correct (P0/P1).**
5. Deterministic scheduler (constraint solver) + `verify_schedule()` gate before persistence. *(F-02)*
6. Ingredient-registry matching (RxNorm/ATC + curated Indian brand table) replacing substring
   containment; regenerate the corpus with curated severities and provenance. *(F-03)*
7. Duplicate-ingredient and daily-dose-ceiling ledger; Beers-criteria list for 65+. *(F-05)*
8. Patient-scoped checks end-to-end; household view becomes a caregiver dashboard. *(F-06)*

**Week 4–6 — make it safe to operate (P1).**
9. Server-minted interaction ids; per-interaction documents; acknowledgement round-trip. *(F-07, F-10)*
10. Phone-number invites; indexed `devices/{token_hash}`; delete the `collection_group` lookup and the
    cross-user write. *(F-07)*
11. Dependency bump + lock file + `pip-audit`; delete unused `pillow` or put it to work validating
    uploads; restrict CORS; add rate limits and a Vertex budget cap; remove `DEV_MODE`; tighten
    Firestore rules to read-only clients; audit-log PHI access. *(F-08, F-12)*
12. Cache keys become `hash(meds) + corpus_version + rule_version`; unknown timestamp means stale;
    awaited grounding writes. *(F-10)*

**Week 7–10 — make it sellable (P1/P2).**
13. Gold-standard evaluation set + nightly accuracy report (false-negative rate is *the* headline
    metric). *(F-11)*
14. Batch the explanations into one LLM call per household; instrument cost per check. *(F-13)*
15. Privacy policy, ToS, consent capture, deletion/export, retention schedule, `asia-south1`. *(F-09)*
16. One-command Linux/macOS deploy (or Terraform), staging environment, `flutterfire configure` in the
    README, delete the committed `session.db` and the framework scaffolding. *(F-14, F-15)*

**Definition of done for "we can talk to a hospital":** a red-team report showing 0% false negatives on
the gold set, a coverage ledger on every screen, a verified (not prompted) schedule, an audit trail of
every alert, and a privacy/consent story a DPO will sign.

---

## 9. Closing assessment

The engineering instinct here is above the median for a student project: the prompts show real
clinical domain knowledge, the caching and retry design is thoughtful, and the personal origin story
is legitimately compelling. What is missing is the discipline that separates a **demo** from a
**medical product**: an artefact that cannot silently degrade, a safety property enforced by code
rather than by prose, measurable error rates, and a failure mode that escalates to a human.

Fix F-01 through F-05 and this becomes a plausible seed-stage product. Leave them and the app is a
green checkmark that means nothing — which, in this category, is worse than no app at all.
