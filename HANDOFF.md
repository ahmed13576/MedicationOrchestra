# Production-readiness handoff

**Audience:** the engineer (or agent) who picks this repository up next.
**State of this document:** written at commit `2b6ad7d`, against a tree that passes
142 backend tests, a clean `ruff`, and a 46/46 safety-invariant audit.
**Scope:** everything still standing between this codebase and a product that may
be put in front of a real patient, ordered by what must happen first.

> **Note on the branch.** `handoff/fixes-phase1` implements many of the tasks
> below. It is reviewed in [`docs/ADVERSARIAL_REVIEW_2.md`](docs/ADVERSARIAL_REVIEW_2.md) —
> read that before treating anything here as open, and before treating anything
> there as closed. The counts quoted in this document describe the tree at the
> commit it was written against, not that branch.

This is a working document, not a status report. Each task has four parts:

* **Why** — the failure this prevents, stated as something that can happen to a
  person, not as a code smell.
* **Work** — the change, concretely enough to start.
* **Pin** — the test that must exist afterwards. A task is not done when the code
  changes; it is done when a test would fail if someone undid the change.
* **Caveats** — how this fix typically breaks something else. Read this before
  starting, not after.

Task ids are stable (`B-1`, `S-4`, …). Reference them in commits.

---

## 0. Ground truth: what is true today

Do not trust prose in this repository, including this document. Run the gates.

```bash
# Backend unit + contract tests (in-memory Firestore/Gemini fakes, no cloud)
cd backend && PYTHONPATH=/tmp/pylibs:. PROJECT_ID=test-project python3 -m pytest tests/ -q

# Lint
cd backend && PYTHONPATH=/tmp/pylibs /tmp/pylibs/bin/ruff check .

# The 46 safety invariants the review demanded, each executed, not asserted
PYTHONPATH=/tmp/pylibs PROJECT_ID=test-project python3 scripts/safety_invariant_audit.py

# A local server that needs no Google Cloud account, with a demo household
cd backend && PYTHONPATH=/tmp/pylibs:. python3 dev_server.py     # http://localhost:8080/
```

What those gates actually cover, in one paragraph: ingredient identity is exact
(no substring matching), the scheduler is deterministic and every placed dose is
re-checked by an independent verifier against the same cited knowledge base, a
medicine that cannot be placed is reported as unscheduled instead of silently
dropped, duplicate-ingredient and dose-ceiling alerts are patient-scoped, SOS
alerts are validated and tenant-scoped before an outbound message is sent, the
knowledge corpus is copied into the image at build time and the service refuses to
start without it, no DrugBank-derived data ships, and the deployment scripts fail
loudly ("DEPLOYED BUT NOT HEALTHY", exit 1) when the smoke test is not satisfied.

What those gates do **not** cover, and what the rest of this document is about:

| Not covered | Consequence |
| --- | --- |
| Anything against real Firestore, real Firebase Auth, real FCM, real Gemini | The fakes encode our assumptions; production may disagree |
| Clinical correctness of the knowledge base | It is labelled `DEMONSTRATION SET - not clinician-reviewed` |
| Consent as a *gate* | Consent is recorded, never read |
| Multiple service instances | Rate limits and SOS state are in-process |
| The client on a device | No iOS project; notifications never verified end to end |
| Anyone attacking the system | No penetration test, no threat-model rehearsal |

---

## 1. Blockers

These are the tasks that must be finished before a real prescription is uploaded
by someone who is not us.

### B-1 · Clinical review of the knowledge base

**Why.** The corpus ships 195 ingredients, 33 interaction rules, 3 advisories and
4 dose ceilings, every one of them assembled by an engineer from public sources
and explicitly marked *not clinician-reviewed*. A wrong severity here is a wrong
instruction to a caregiver, and it is the single largest unbounded risk in the
repository. Until a named clinician signs the rules, "the invariants hold" only
means the machine is consistent with itself.

**Work.**

1. Recruit one reviewer with prescribing authority (a physician; a pharmacist is
   acceptable for a first pass) and agree the review protocol in writing:
   every rule gets `reviewed_by`, `reviewed_on`, `source_url` and `source_kind`
   (`label` | `guideline` | `systematic review`).
2. Review in order of expected fire frequency, not alphabetically: the ceilings
   first (paracetamol, ibuprofen, aspirin, diclofenac), then the rules whose
   severity is `critical` or `major`, then the rest. Twenty rules signed is worth
   more than thirty-three half-signed.
3. Make the review a file, not a conversation: a `review_status` block per rule
   inside `backend/knowledge/interactions.json` and `dose_ceilings.json`, and a
   `docs/KNOWLEDGE_REVIEW_LOG.md` entry per session (what changed, on whose
   authority, with what disagreement left open).
4. Bump the knowledge version to `2.0.0` when the first real review lands, and
   keep `1.x` readable so that a schedule generated today can still be explained
   six months from now.

**Pin.**

* `test_every_rule_carries_a_reviewer_and_a_date` — iterate the loaded corpus;
  fail when any rule lacks `reviewed_by` / `reviewed_on` / `source_url`, or when
  the top-level `review_status` claims review while a field is empty.
* `test_knowledge_version_is_served_with_every_clinical_response` — a schedule or
  interaction response must carry the knowledge version that produced it, so a
  finding can be tied to a revision after the fact.
* Extend the existing CI knowledge check so the same rule applies on push, not
  only under pytest.

**Caveats.**

* Never let a review edit interaction prose without editing the machine-readable
  field beside it. `min_gap_hours` and `rule_time_gap()` in
  `backend/services/clinical_engine.py` are the only things the scheduler obeys;
  prose that says "48 hours" while the field says `8.0` is worse than no rule.
* Adding `reviewed_by` to every rule at once, in one commit, with one name, is the
  failure mode this task is meant to prevent. Prefer many small reviewed batches.
* Severity inflation is the predictable bias of a reviewing clinician: everything
  becomes `critical`, the UI drowns, and caregivers learn to ignore alerts. Cap
  `critical` at the interactions the reviewer will defend in person.
* After the review, re-sync the counts quoted in `README.md`,
  `docs/ADVERSARIAL_REVIEW.md`, `docs/HISTORY.md` and this file
  (`grep -rn "33 rules" .`). The audit asserts invariants, not quoted numbers.

### B-2 · An end-to-end test against real infrastructure

**Why.** Every API test runs against `backend/tests/fakes.py`. Nothing in this
repository has ever exercised Firestore security rules, Firebase Auth tokens, FCM
delivery or Gemini with a real key. The first real deployment is therefore *also*
the first integration test, with a patient's data in it.

**Work.**

1. Add `backend/tests/integration/` that runs against the Firebase emulator suite
   (`firebase.json` at the repository root now declares Firestore, Auth and the
   emulator UI; run `firebase emulators:start`). `firebase.json` also carries the
   `firestore.indexes.json` reference so `firebase deploy --only firestore:rules`
   works as the README claims.
2. Cover the two paths where the fakes are most likely to be wrong: the security
   rules (deny by default, and a client may read its own `users/{uid}` documents
   and none of its own `device_tokens`) and the medication write path
   (`backend/main.py` line ~320 filters `status == "active"`; confirm against a
   real query that this does not silently return everything).
3. Add a recorded-response test for Gemini: capture real extraction responses for
   the fixtures in `backend/tests/fixtures/` (including the AVIF strip photos) and
   replay them, so a provider-side model change is detected as a diff rather than
   discovered in production.
4. Keep a nightly scheduled workflow for the emulator job; keep PR CI fast and
   hermetic.

**Pin.**

* `test_firestore_rules_deny_a_foreign_household` — two users, cross-read attempt,
  expect permission denied. This must run against the emulator; a fake cannot
  express it.
* `test_scan_against_recorded_model_responses_is_stable` — same fixture, same
  extraction, byte-for-byte.
* A CI job that fails (not warns) when the emulator suite is unavailable, on the
  release branch only.

**Caveats.**

* The emulator is not production: no composite-index enforcement, different
  latency, no cold starts, and rules that pass locally can still fail in
  production because of an index. Do not let this job become the only place tests
  run.
* Recorded Gemini responses go stale. Version them, date them, and delete them
  when the extraction prompt changes — otherwise they pin a prompt nobody uses.
* Real FCM cannot be tested from CI. Plan a manual, scripted device test (B-6) and
  an `docs/OPS_RUNBOOK.md` entry for what to check when reminders stop arriving.

### B-3 · Continuous supply-chain scanning and an SBOM

**Why.** The known CVEs (python-multipart, Pillow) were fixed by pinning, once.
Nothing watches for the next one, and the Android build pulls a second dependency
graph that no backend check sees.

**Work.**

1. Add `pip-audit` and `flutter pub outdated` to CI, with an explicit allowlist
   file (`docs/KNOWN_ADVISORIES.md`) that carries an expiry date per entry.
2. Generate a CycloneDX SBOM per release and attach it to the GitHub release; keep
   it out of the source tree.
3. Pin every `uses:` in `.github/workflows/` to a full commit SHA, and let
   Renovate/Dependabot update them.

**Pin.**

* `test_ci_runs_a_vulnerability_audit` — parse the workflow YAML; assert the audit
  step exists and is not `continue-on-error`.
* `test_actions_are_pinned_to_shas` — every `uses:` value matches
  `owner/repo@[0-9a-f]{40}` (or is a local action).
* `test_known_advisories_are_unexpired` — fail once an allowlist entry's expiry
  date has passed.

**Caveats.**

* An audit that fails the build on every advisory will be disabled within a month.
  Fail on new high/critical findings only; make everything else a report.
* SHA-pinned actions never receive security updates on their own. Without Renovate
  you have traded one risk for another.
* Do not "fix" the Python dependency set by bumping packages that are not used —
  the tree was already pruned, and `python-dotenv` in particular must not be
  reintroduced.

### B-4 · Authorization on every write path, and tests that try to break it

**Why.** The API is the only writer, and the API trusts the token's `uid` as the
tenant. That is the right shape. What is missing is evidence: no test tries to
make one household write into another's documents, and `firestore.rules` denies by
default for collections nobody has written a rule for — which is safe, but also
means a legitimate read fails silently the moment someone adds a collection.

**Work.**

1. Write the adversarial suite: for every write path (`profiles`, `medications`,
   `alerts`, `sos`, `devices`, `consent`, `audit`), attempt the operation as user
   B against user A's identifier and assert a 403/404 — never a 500, never a
   silent success.
2. Add an explicit rules test for each collection the client is allowed to touch,
   and an explicit comment in `firestore.rules` for each collection it is not.
3. Assert that a `profile_id` belonging to another user cannot be used as a path
   parameter anywhere (`grep` for handlers that take `profile_id` and check each
   one resolves ownership before use).

**Pin.**

* `test_a_foreign_profile_id_is_refused_on_every_route` — table-driven over the 26
  `@app.` routes in `backend/main.py`; the table is the point, so the test fails
  when a new route is added and not classified.
* `test_every_client_readable_collection_has_a_rule` — parse `firestore.rules`
  and the client's Firestore calls; mismatches fail.

**Caveats.**

* Ownership checks must be a dependency, not a line at the top of each handler
  that someone will forget to copy. Refactor towards one helper
  (`resolve_owned_profile(user_id, profile_id)`) before adding the table test, or
  the test will be written around the current inconsistencies.
* 403 vs 404 leaks existence. Prefer 404 for another user's resource everywhere,
  and pin that choice in the table test so it stays uniform.

### B-5 · Put the service behind a controlled front door

**Why.** `deploy.sh` publishes the Cloud Run service with
`--allow-unauthenticated` and relies on in-app token checks (recorded as gap 3 in
`docs/SECURITY_AND_PRIVACY.md`). Every unauthenticated request reaches the
application, so the app-level checks are the only wall, and _rate limiting is
in-process_ (task P-1) — the two weaknesses compound.

**Work.**

1. Prefer ingress restriction plus an authenticated load balancer (or IAP) over a
   public service URL; if the public URL must stay for the mobile client, restrict
   ingress to the LB's ranges and add Cloud Armor with a rate-based rule.
2. Set request limits at the edge as well as in the app: max body size, max image
   dimensions, a timeout below the client's.
3. Keep the existing smoke-test probe (a bogus bearer must get 401/403) as the
   invariant that survives whatever front door you choose.

**Pin.**

* Extend `backend/tests/test_deployment_scripts.py`: the extracted deploy command
  must contain either `--ingress=internal-and-cloud-load-balancing` or an explicit
  `--no-allow-unauthenticated`, and the smoke test must still assert the bogus
  bearer is refused.

**Caveats.**

* `--allow-unauthenticated` is required by the current Flutter client flow, which
  authenticates with a Firebase ID token *after* the request arrives. Switching to
  IAP means the client must send an IAP token instead — a client release, not just
  a deploy flag. Do not flip the flag without the client change, or every request
  fails with a 302 to a login page.
* The local dev server deliberately bypasses auth (`DEV_AUTH_BYPASS`, refused
  whenever `K_SERVICE` is set). Keep that refusal, and keep the auth probe
  expectations in the deployment test — they only hold on a real deployment.

### B-6 · A scripted device acceptance test

**Why.** The product's promise is "the right pill at the right time". The reminder
path — permission, timezone, scheduling, re-scheduling after a reboot, and the
notification actually appearing — has never been verified on a device, and the
Flutter tests deliberately avoid platform channels.

**Work.**

1. Write `docs/DEVICE_ACCEPTANCE.md`: ten numbered checks (install, sign in, scan
   one of the fixture photos, confirm the identified/not-identified split, open the
   schedule, allow notifications, receive one, reboot and receive the next,
   withdraw consent, delete data, confirm the app is empty). Every check has an
   expected observation and a place to record the device, OS version and date.
2. Execute it on at least one low-end Android phone (the actual target user is
   often on 3–4 GB of RAM) before every release; record results in the log.
3. Add `flutter test integration_test/` for the parts that do not need a cloud
   backend, and keep the ten manual checks for the rest.

**Pin.**

* The acceptance log is a release artefact: a CI (or manual, documented) gate that
  refuses to tag a release with an unexecuted or failed checklist.
* One automated integration test that proves the app launches, reads a fake
  backend and renders the empty state with a coverage warning when coverage is
  incomplete.

**Caveats.**

* Notification behaviour depends on OEM battery optimizations that differ per
  manufacturer. One phone is a smoke test, not coverage; note which OEMs have not
  been tried.
* Tests that need a real Gemini key must be marked and must never run on forks.

---

## 2. The safety engine

Ordered most dangerous first.

### S-1 · Every separation rule needs a machine-readable gap

**Why.** Three rules carry a `min_gap_hours` (aspirin+NSAID 8.0 h,
levothyroxine+iron/calcium 4.0 h, fluoroquinolone+cation 6.0 h). At least one
documented interaction — nitrate + PDE5 inhibitor, 48 h — exists only as prose and
therefore cannot be enforced by the scheduler or checked by the verifier.

**Work.** Audit every rule whose advice is "separate", "space out" or "wait": each
must carry `min_gap_hours`. Add the nitrate/PDE5 pair with its 48 h gap and a
citation.

**Pin.**

* `test_every_separation_rule_declares_a_gap` — no rule may contain separation
  language without a numeric gap.
* `test_prose_hours_match_the_machine_gap` — extract `\d+\s*(h|hr|hrs|hour|hours)`
  from rule prose; it must either be absent or equal to `min_gap_hours`. This one
  test prevents the whole class of "the text says 48 hours, the engine enforces 8".
* `test_a_rule_without_a_gap_refuses_to_schedule` — build a knowledge base with
  `min_gap_hours` surgically removed and assert the request fails loudly rather
  than falling back to a default.

**Caveats.**

* If `min_gap_hours` is missing today, the engine falls back to
  `DEFAULT_TIME_GAPS`. That fallback is exactly the silent-wrong-answer failure
  this repository is built to avoid: refuse instead.
* Never widen `DEFAULT_TIME_GAPS` to make a failing schedule pass; fix the rule.
* Some real interactions have no meaningful separation window (they are
  contraindications). Those need an explicit `action: "avoid"`, not a fabricated
  gap.

### S-2 · Allergies and intolerances

**Why.** There is no allergy model anywhere. A patient with a documented penicillin
or sulfa allergy is schedule-today, alert-never. This is the most common question a
caregiver will ask of a medication app and the answer is currently silence.

**Work.**

1. Add an allergy list to the patient profile (free text plus a resolver against
   the ingredient registry; unresolved entries are shown, never dropped).
2. Make the interaction check test allergies before the schedule is produced, and
   make an allergy hit `critical` with an `action: "avoid"` — never a soft warning.
3. Store the allergy entry with who recorded it and when (it may be the caregiver's
   belief, not a diagnosis).

**Pin.**

* `test_a_recorded_allergy_is_reported_and_blocks_the_schedule` — the medicine is
  not placed and the response carries the alert.
* `test_an_unresolved_allergy_name_is_surfaced_not_ignored` — the API must say it
  could not match the name, exactly as the scan path does for unresolved medicine.
* `test_no_green_banner_when_an_allergy_is_unmatched` — coverage/`is_complete`
  must be false.

**Caveats.**

* Allergies are recorded by non-clinicians ("she reacted to something white"). Do
  not let the app assert an allergy as fact; phrase it as "you recorded".
* Cross-reactivity (penicillins/cephalosporins) is a clinical judgement. Ship the
  conservative version (block) and let the pharmacist tier relax it (U-5), never
  the other way round.

### S-3 · Truncation must never hide a critical alert

**Why.** `MAX_ALERTS_PER_PATIENT = 40` truncates alerts per patient. The client
shows what it receives; nobody is told that something was withheld. A truncated
`critical` alert is a person taking a dangerous combination while the app talks
about the eleventh-mildest finding.

**Work.** Order by clinical severity before truncation, keep every `critical` and
`major` alert regardless of the cap, and report the truncation count so the client
can say "and N more".

**Pin.**

* `test_truncation_never_drops_a_critical_alert` — 100 generated alerts, assert
  every critical survives.
* `test_truncation_is_disclosed_in_the_response` and a widget test that renders the
  disclosure.

**Caveats.**

* Truncation happens after verification in some paths; make sure the verifier sees
  the same set the client does, otherwise `verified: true` describes a list nobody
  received.
* Sort order must be deterministic (it is today) or the truncation boundary moves
  between requests and the disclosure count flaps.

### S-4 · Mechanism-group duplicates, not just exact ingredients

**Why.** The registry matches exact ingredients, which is right. But ibuprofen and
diclofenac are different ingredients with the same mechanism, and taking both is a
real (and common) NSAID-duplication problem. The CI knowledge check already builds
disjoint mechanism groups; the runtime does not use them.

**Work.** Use the same `groups` data at runtime: two scheduled medicines in the same
group with a shared mechanism of action produce a duplicate-therapy alert, cited to
the same source as the group definition.

**Pin.** `test_two_different_drugs_in_one_mechanism_group_alert` (ibuprofen +
diclofenac), and `test_a_group_alert_cites_its_source`.

**Caveats.**

* Groups are coarse. Two ACE inhibitors are a duplication; two different
  anti-malarials may be a correct combination. Set severity to `moderate` until
  B-1 gives a reviewer's opinion, and never block on it.
* Do not let this reintroduce fuzzy name matching — the group check works on
  resolved ingredient ids, after exact resolution has already run.

### S-5 · What "verified" means has to be legible

**Why.** `verification.verified: true` means "every dose we placed is consistent
with the rules we know". A caregiver reading "verified" hears "this regimen is
safe". Those are different claims, and the gap between them is the kind of thing
that ends up in a court exhibit.

**Work.** Rename or annotate: `verified_against_rules`, plus
`rules_checked` and `knowledge_version`. Change the client copy to "Checked against
N rules — the doctor's prescription is still the authority", and keep the tick
conditional on `coverage.is_complete` as it is today.

**Pin.** A contract test asserting the field names, and a widget test pinning the
client string so a future redesign cannot reintroduce "Safe".

**Caveats.** Any rename touches the Flutter client, the API docs and the audit
script at once; do it in one commit, and update `docs/API.md` in the same commit or
the next reader will document the old name.

### S-6 · Meal relation, form and route

**Why.** Prescriptions say "after food", "twice daily with meals", "sublingual".
Extraction reads these but the scheduler places doses in generic slots, so a
meal-dependent instruction becomes an arbitrary time. For levothyroxine (empty
stomach) and metformin (with food, GI tolerance) this is the difference between
tolerable and intolerable.

**Work.** Carry a `food_relation` field from extraction through to scheduling; the
scheduler may move a dose to satisfy it, and must mark the dose as
`food_relation_unmet` rather than pretending when no slot matches.

**Pin.** `test_a_meal_relation_survives_extraction_to_schedule` and
`test_an_unmeetable_meal_relation_is_reported_not_ignored`.

**Caveats.** Meal times are unknown to the system; do not invent them from
time-of-day heuristics without saying so. Ask the user once, store it, and treat
"unknown meal time" as unmet rather than defaulting to breakfast.

### S-7 · Confidence must gate scheduling

**Why.** A handwritten squiggle resolves to a name with low confidence and is
carried through as a normal medicine. If it is scheduled, the caregiver gets a
reminder for a drug nobody verified.

**Work.** Thread the extraction confidence, and require confirmation for low
confidence before the medicine participates in scheduling; show it in the
identified/unidentified split the client already renders.

**Pin.** `test_a_low_confidence_medicine_is_never_scheduled_silently`, plus a
contract test that the confidence survives JSON serialization (the place this kind
of field is usually lost).

**Caveats.** Do not present confidence as a percentage to users — "not sure, please
check" is the honest phrasing. And never let a confirmation tap be remembered as
evidence about the medicine itself.

### S-8 · Patient modifiers: age, weight, renal and hepatic function

**Why.** The four dose ceilings are absolute. A 45 kg 85-year-old with a
creatinine clearance of 30 is not the same patient as a 90 kg 40-year-old, and
both are in the demo household's target population.

**Work.** Record age, weight, pregnancy/lactation and a coarse renal/hepatic flag on
the profile; add ceiling modifiers as knowledge data (not code) with citations. Where
a modifier is missing, the response must say the ceiling was applied
unmodified — never imply it was personalised.

**Pin.** `test_a_ceiling_is_modifiable_by_knowledge_data` and
`test_missing_patient_modifiers_are_disclosed`.

**Caveats.** This is where an unreviewed knowledge base does the most damage — a
fabricated renal adjustment is worse than no adjustment. Do not start this before
B-1 has produced a reviewer, and keep every modifier a data change with a citation.

### S-9 · Titration, tapering and as-needed dosing

**Why.** Warfarin is dosed to an INR, prednisolone tapers, paracetamol is
`PRN`. The model today is one flat dose per medicine, so a taper becomes a single
repeating time and an as-needed drug gets a permanent slot.

**Work.** Extend the medication model with `schedule_kind`
(`fixed` | `taper` | `prn`) and make the engine's handling explicit for each; `prn`
must not create reminder slots at all, and `taper` must express its steps.

**Pin.** `test_a_prn_medicine_creates_no_reminders` and
`test_a_taper_step_is_not_flattened_into_one_dose`.

**Caveats.** Anything unrecognised must fall back to `fixed` **and say so**, not
guess. And never generate a taper the prescription did not state — extraction may
miss a step, and inventing one is a dosing error.

---

## 3. Platform and operations

### P-1 · Shared rate limiting

**Why.** `backend/services/rate_limit.py` keeps counters in process memory. A second
Cloud Run instance has its own budget, and a cold start resets the day's counters —
so the limits that protect a costly Gemini call and the SOS endpoint are advisory
once the service scales past one instance (gap 4 in `docs/SECURITY_AND_PRIVACY.md`).

**Work.** Put the counter behind the existing `check()` interface using Firestore
transactions or Memorystore, keyed per user as today. Add a global concurrency
guard and make every limit environment-configurable with the current values as
defaults (scan 6/min 120/day; confirm 12/300; interactions 12/300; schedule 6/120;
sos 2/20; sos_outbound 4/60).

**Pin.** `test_two_limiter_instances_share_a_budget` (exhaust via one, assert the
other refuses) and `test_limits_are_read_from_configuration`.

**Caveats.** A network round trip per request adds latency to `/scan`; keep the
client cached and measure before and after. Do not respond to latency by raising a
limit — the limit is a cost control and a safety control.

### P-2 · Cold starts, minimum instances and concurrency

**Why.** The service loads the knowledge base at startup and refuses to start
without it (deliberately). That makes cold starts expensive in the path a caregiver
uses while standing in a pharmacy.

**Work.** Set `--min-instances` for the production service, measure p95 for
`/scan` cold and warm, cap concurrency so the in-request LLM calls do not exhaust
memory, and record the numbers in `docs/OPS_RUNBOOK.md`. Keep `dev_server.py` as the
zero-cloud way to develop; never let a local convenience (auth bypass, seeded demo)
enter the deployed image.

**Pin.** Extend the deployment-script tests to assert the deployed service
configuration (min instances, memory, timeout, concurrency) rather than trusting a
runbook number.

### P-3 · Indexes, backups and retention

**Why.** `firestore.indexes.json` now exists but is empty — correct today, because
the queries in the tree are single-field equality filters. The first composite
query someone adds will fail at runtime, in production, under load.

**Work.** Add indexes with each new query; enable Firestore PITR/backups; state the
retention window in `docs/SECURITY_AND_PRIVACY.md` and make it true in code (a
scheduled job that purges what the notice says is purged).

**Pin.** `test_every_composite_query_has_an_index` (scan the source for
`.where(...).order_by(...)` and check the index file) and
`test_retention_job_deletes_only_expired_records`.

### P-4 · Alerting, not just a dashboard

**Why.** `monitoring_dashboard.json` gives four tiles; tiles do not wake anyone.
The failure modes that matter (KB failed to load, readiness failing, verification
failure rate rising, rate-limit rejections spiking, 5xx rate) are invisible unless
someone is looking.

**Work.** Add alert policies to `monitoring_setup.ps1` (or better, a
`monitoring_policies.json` applied by the script) with notification channels:
`/readyz` failing, 5xx ratio, p95 latency, any occurrence of a startup failure, and
a log-based alert for the deploy smoke-test marker. Explain the local-dev limits of
`monitoring_setup.ps1` at the top of the file.

**Pin.** Extend `backend/tests/test_deployment_scripts.py` to assert the policy list
matches the documented set, so a new critical endpoint cannot be added without an
alert.

### P-5 · Deployments need a rollback story

**Why.** `deploy.sh` builds and shifts traffic to a new revision in one step, and
"DEPLOYED BUT NOT HEALTHY" is a very good failure message with no next step. The
knowledge base is baked into the image, so a bad corpus is a bad deployment.

**Work.** Tag revisions with the knowledge version, deploy with `--no-traffic`,
run the smoke test against the tagged revision URL, then shift traffic; document the
rollback command (`gcloud run services update-traffic ... --to-revisions`) in the
runbook and practise it once.

**Pin.** Extend the deployment-script test: the script must contain the
traffic-shift step and the rollback documentation must name the same revision tag
format.

**Caveats.** Two revisions running simultaneously means the schema must tolerate
both; `2.0.0` from B-1 is the moment to decide whether old clients keep working.

### P-6 · Server-side reminders or honest deletion

**Why.** `notification_service.send_dose_reminder` has no callers. It reads like a
feature and is dead code — a trap for the next engineer, and a lie in the
architecture diagram. Dose reminders are currently on-device only
(`medication_orchestra/lib/services/reminder_planner.dart`), which is a legitimate
design.

**Work.** Either delete the function and document the on-device design in the API
docs, or implement it with FCM and a scheduler that survives the phone being off.
Do not leave it half-there.

**Pin.** A test that fails on dead code in the notification service, or a contract
test for the implemented endpoint if it survives.

### P-7 · SOS must not depend on a push notification

**Why.** The SOS path is what a caregiver uses in an emergency. Push to a phone
that is off, out of battery, or with notifications muted is not an emergency
channel, and the current design has no second channel.

**Work.** Add an escalation ladder with an SMS/voice fallback for recipients who do
not acknowledge within N minutes, with the acknowledgement recorded per recipient
(the per-recipient receipts already exist). Keep every outbound message
tenant-scoped and validated, as today.

**Pin.** `test_an_unacknowledged_sos_escalates_to_the_next_channel` and
`test_escalation_never_contacts_a_non_consenting_recipient`.

**Caveats.** Escalation is itself a rate-limited, costly, and legally sensitive
action. Make it opt-in per recipient, log every attempt in the audit trail, and
never send to a number that has not confirmed.

### P-8 · Configuration and secret hygiene, verified

**Why.** Configuration is read from environment variables, and the demo project id
lives in the client's generated Firebase files. Both are legitimate; neither is
currently proven to be free of real values in the wrong place.

**Work.** Move anything sensitive to Secret Manager, keep the deploy script's env
list to `DEV_MODE` and `PROJECT_ID`, and add a pre-commit or CI check for keys,
tokens and service-account JSON.

**Pin.** Extend the repository-wide project-id test (see `backend/tests/test_hardening.py`)
with a secret-pattern scan, and assert the deployed environment list stays minimal
in the deployment-script test.

### P-9 · Load and failure rehearsal

**Why.** Nobody has seen this system under concurrent load, and nobody has
deliberately broken it. The verifier, the caps and the alert truncation all behave
differently at the edges.

**Work.** A short load test (k6 or Locust) against the dev server with the LLM
disabled, recording latency and error rates; then a failure rehearsal: kill the
knowledge file, revoke the Gemini key, return 500s from Firestore, and confirm the
system fails loudly and readably in each case.

**Pin.** A scripted (not manual) rehearsal whose expected outcome per scenario is
asserted, so "fails loudly" is a tested claim rather than a hope.

---

## 4. Privacy, consent and law

`docs/SECURITY_AND_PRIVACY.md` §5 and §8 already list the gaps; this section turns
them into tasks. The DPDP dates used there: Rules notified 2025-11-13, board duties
the same date, consent-manager integration from 2026-11-13, full regime 2027-05-13.

### D-1 · Consent must gate processing, not just be recorded

**Why.** `POST /api/v1/users/consent` writes a versioned entry with a timestamp
(`backend/main.py` ~1059) and nothing reads it. The scan, confirm and schedule
endpoints process health data with no consent check at all. Recording intent while
processing regardless is the exact pattern a data-protection regulator looks for
first.

**Work.** Define the consent scopes (processing for the purpose of medication
review; storing photographs; sharing with a caregiver; sending SOS to named
recipients). Check the relevant scope in the dependency that already verifies the
token, and return the specific scope missing. Withdrawal must immediately stop
processing and start retention-limited deletion.

**Pin.**

* `test_scan_is_refused_without_a_recorded_consent`
* `test_withdrawing_consent_blocks_the_next_scan`
* `test_the_refusal_names_the_scope_that_is_missing`
* A contract test that a consent check cannot be bypassed by an empty
  `consent_version`.

**Caveats.**

* The dev server and the demo path must keep working, or local development dies.
  Gate the relaxation on `DEV_MODE` + the existing `DEV_AUTH_BYPASS` guard, and keep
  the production refusal unconditional.
* Existing accounts have no consent record. Decide explicitly between
  re-prompt (better) and grandfathering, and write the decision down — an implicit
  third option where the check only applies to new users is how these bugs live
  forever.
* Consent for a person who is not the app user (the caregiver records data about a
  parent) is a lawful-basis question, not a checkbox: record who consented, for
  whom, and on what authority.

### D-2 · A published notice, a grievance officer and a breach runbook

**Why.** Gap 1 in `docs/SECURITY_AND_PRIVACY.md`. DPDP requires a notice a data
principal can actually read and a way to complain; a breach without a runbook
becomes a breach with a delay.

**Work.** Write the notice in plain language (English + Hindi), name the grievance
officer and the response window, and write the breach runbook: detection, 72-hour
assessment, notification duties, the people who decide.

**Pin.** A test that the notice file exists, carries a version and a date, and that
the app's About screen links to the same version string.

### D-3 · Complete, paginated, asynchronous export

**Why.** `GET /api/v1/users/export` returns everything in one JSON document, with the
audit trail capped at 500 events (`audit_service.list_events(..., limit=500)`) and no
pagination. For a family with a year of scans that is an incomplete answer to a
data-subject access request, and "we truncated it" is not a defence.

**Work.** Make the export complete: paginate the audit trail, include SOS records,
device tokens metadata and consent history, and return a job id with a signed,
expiring download for large accounts.

**Pin.** `test_export_includes_every_collection_we_hold_for_the_user` (compare
against an explicit list — the list is the test) and
`test_export_is_not_silently_truncated` (seed > 500 audit events).

### D-4 · Deletion that is actually deletion

**Why.** The delete endpoint removes the user's documents and writes a tombstone
(`consent.accepted: false`). It does not obviously cover FCM device tokens, audit
entries, cached client data, or backups. DPDP erasure has to be defensible end to
end.

**Work.** Enumerate every store that holds user data (Firestore, FCM tokens, logs,
the client's `local_cache_service`, backups) and handle each explicitly: delete,
or document why it is retained and for how long. Make the client clear its cache on
deletion and prove it.

**Pin.** `test_deletion_removes_every_enumerated_store` and
`test_the_client_clears_its_cache_after_account_deletion` (widget/unit test against
the cache service). A test asserting the notification tokens are gone.

### D-5 · Sub-processors and data residency, written down

**Why.** Prescription photographs and medicine lists go to Google's Gemini API. That
is a sub-processor disclosure, and the region where Firestore and Cloud Run live is
a residency decision that is currently implicit in a `gcloud` command.

**Work.** Document the sub-processors (Google Cloud, Gemini, FCM), confirm the
no-training configuration for the Gemini tier in use, choose an India region for
storage and processing, and state the retention at the provider.

**Pin.** Extend the deployment-script test to assert the region, so a `gcloud` run
in the wrong region fails CI rather than silently moving patient data.

### D-6 · Consent-manager readiness

**Why.** DPDP's consent-manager interoperability phase starts 2026-11-13. It is a
design constraint (consent artefacts must be machine-readable and revocable
through a third party), not a last-minute integration.

**Work.** Keep consent as structured records with versions and scopes (D-1 is the
prerequisite), and write the mapping from the app's consent scopes to a consent
artefact, even if the integration itself is deferred.

### D-7 · Age, guardianship and capacity

**Why.** A medication app used by a caregiver for an elderly parent is processing
data about someone who may not be the account holder and may not have capacity.

**Work.** Record the relationship and the lawful basis, allow a profile to be marked
as belonging to a person who cannot consent themselves, and define what happens
when they object.

---

## 5. The client

The Flutter client is Android + web only. There is **no `ios/` directory** — iOS is
unbuilt, not merely unconfigured — and no localisation: the UI is English-only while
the backend explanation service speaks en/hi/hinglish/bn/ta/te/mr.

### C-1 · iOS, or an explicit decision not to ship it

**Why.** Half the target market's caregivers use iPhones, and "we forgot" and "we
decided against it for v1" have different consequences for a product plan.

**Work.** Either run `flutter create --platforms=ios .` and configure Firebase for
iOS (a real project, including the `flutter_local_notifications` iOS setup with
`timezone`/`flutter_timezone`), or write the decision and its cost in
`docs/YC_PRODUCT_PLAN.md` §5.

**Pin.** A CI matrix entry that runs `flutter build ios --no-codesign` once the
platform exists; until then, a test asserting the decision is documented.

### C-2 · Notification plumbing proven on a device

**Why.** `zonedSchedule` requires the timezone database to be initialised and the
device zone to be read via `flutter_timezone`; daily repeats use
`matchDateTimeComponents: DateTimeComponents.time`; `POST_NOTIFICATIONS` (Android 13+)
must be requested at runtime and `RECEIVE_BOOT_COMPLETED` is already declared in the
manifest. None of this can be validated by a unit test on a laptop.

**Work.** Verify the initialisation order in `fcm_service.dart`, assert the
inexact alarm mode (`AndroidScheduleMode.inexactAllowWhileIdle`, which needs no
special alarm permission on Android 14+), and reschedule after reboot and timezone
change. Then run the device checklist (B-6).

**Pin.** Unit tests on `reminder_planner.dart` for the pure planning rules (they
exist; extend them for timezone and reboot cases) plus the device checklist.

**Caveats.** If exact alarms are ever needed, Play policy requires justification —
prefer inexact alarms and say why in the code comment.

### C-3 · Localisation

**Why.** A Hindi-speaking caregiver who cannot read the schedule is not served by a
Hindi explanation on an English screen.

**Work.** Add `flutter_localizations` + ARB files, start with hi and en, and either
wire the remaining backend languages or mark them unsupported in the UI (better an
honest list than a silent fallback).

**Pin.** `test_every_user_facing_string_is_localised` (a lint-style test over
`lib/` for hardcoded strings) and a widget test rendering the schedule in hi at 1.5×
font scale.

### C-4 · Accessibility for the actual user

**Why.** The primary user is an elderly caregiver, often with reduced vision and
tremor. The schedule screen is a list of times and pill names with severity colours.

**Work.** TalkBack labels on every interactive element, semantic headings, contrast
checks on the alert colours (severity must not be conveyed by colour alone — add
icons and text), 200 % font scaling without clipping, and touch targets ≥ 48 dp.

**Pin.** Widget tests at 2× text scale asserting no overflow errors, and a semantics
test asserting each alert has a spoken label that includes the severity word.

### C-5 · Cache trust boundary

**Why.** The on-device cache can show yesterday's schedule with today's coverage
state. The rule already coded — a cache entry without a coverage block renders as
"not fully checked" — is exactly right, and it is the kind of rule that a refactor
deletes silently.

**Work.** Keep the coverage ledger in the cache entry; show the age of the cached
data ("as of 6 hours ago"); never present cached data as current when the network is
down without saying so.

**Pin.** Widget tests replaying the three cache states (fresh, stale, coverage
missing).

### C-6 · Permissions and graceful refusal

**Why.** Camera, photos and notifications can all be refused. The interesting case is
a user who refuses notifications: the app still has to be useful.

**Work.** Make each refusal recoverable in settings, explain what is lost, and never
block the scan flow because notifications are off.

**Pin.** Widget tests for each permission state, using the existing
`permission_handler` abstraction.

### C-7 · Update path and version discipline

**Why.** The Android build config uses the debug signing configuration
(`medication_orchestra/android/app/build.gradle.kts`), and the changelog is a set of
phase notes. A release build needs a real signing config, an `applicationId` that
will not change, and a version scheme tied to the knowledge version.

**Work.** Add a release signing config, decide the `applicationId` permanently, and
stamp the app's version together with the knowledge version so a support
conversation can identify both from a screenshot.

---

## 6. Product

Ordering and rationale live in `docs/YC_PRODUCT_PLAN.md` §5; this list exists so the
engineering handoff does not lose it. Each of these is a project, not a task.

* **U-1 · Adherence loop** — did the dose happen? Today the app schedules but never
  learns. This is the retention engine and the source of the only data that makes
  the product more valuable over time.
* **U-2 · Refill and expiry radar** — count tablets, predict run-out, warn before
  the last strip. Cheap, obviously useful, and it builds the habit loop.
* **U-3 · Doctor-visit one-pager** — a printable summary of current medicines,
  recent alerts and adherence. The artefact that gets the product into a physician's
  hands.
* **U-4 · Caregiver dashboard and phone invites** — two siblings, one parent, one
  schedule. The current model is a single account with profiles; it needs real
  membership and per-member permissions.
* **U-5 · Pharmacist review tier** — rung 5 of the trust ladder in
  `docs/YC_PRODUCT_PLAN.md`: a pharmacist signs the high-severity alerts. This is
  the clinical-governance unlock that makes B-1 repeatable rather than a one-off.
* **U-6 · ABDM (HIU) import** — pull a real prescription rather than photographing
  it; changes the data model more than it looks.
* **U-7 · Hindi / WhatsApp flows** — where the users are; also the cheapest
  distribution channel available.

---

## 7. Repository hygiene

* **H-1 · A licence.** There is no `LICENSE` file: the repository's own terms are
  undefined, which is a problem for a commercial product, for the open-data
  provenance claims in `docs/KNOWLEDGE_SOURCES.md`, and for anyone who contributes.
  Decide (proprietary or open-core), add the file, and state the third-party
  licences of the knowledge sources beside it.
* **H-2 · Documentation drift is a real defect here, and it is not automated.**
  Test counts, invariant counts and knowledge versions are quoted in prose in five
  documents. Add `scripts/check_doc_counts.py` that runs the suite, counts the
  tests and the audit checks, and fails when a quoted number disagrees.
* **H-3 · Architecture decision records.** The rules that must not be broken (no
  model decides anything clinical; fail loud, never green; patient-scoped by
  default; the corpus ships in the image) currently live in prose and in tests.
  Write them as ADRs under `docs/adr/` and reference them from the tests that
  enforce them, so a newcomer reads the reason, not just the assertion.
* **H-4 · The client's generated Firebase files name the demo project.**
  `medication_orchestra/firebase.json`, `lib/firebase_options.dart` and
  `android/app/google-services.json` are the only places a project id is allowed,
  and the repository-wide test now enforces that. They must be regenerated with
  `flutterfire configure` before shipping, and `.firebaserc` at the root carries a
  placeholder default project for the same reason.
* **H-5 · Repository size and generated files.** Keep build outputs, the Flutter
  `.dart_tool`, verification images and caught exceptions out of the tree; the
  deployment scripts and CI already validate what must be present.

---

## 8. Rules for working in this repository

Read `docs/HISTORY.md` for why the current design is shaped the way it is; most of
the surprises here are deliberate.

1. **A test that does not execute is not evidence.** Several checks are
   inspection-only (the PowerShell parsers, the deployment scripts). Say which is
   which; do not upgrade "parsed" to "verified".
2. **Fail loud, never green.** If coverage is incomplete, if a medicine cannot be
   resolved, if a schedule cannot be built, the answer is a visible gap. There is
   no code path in this repository that is allowed to print reassurance over
   missing data.
3. **The model never decides anything clinical.** Gemini extracts; the explanation
   service phrases pre-decided findings and is validated against banned phrases and
   dropped names, with a curated-text fallback. Anything clinical that arrives from
   a model is a bug, however plausible it reads.
4. **Every clinical claim is cited and versioned.** A rule without a source and a
   version cannot ship.
5. **Patient scope is the default scope.** Any query that spans households, or any
   `collection_group` call, is a defect until proven otherwise — there is a test
   that fails the build on `collection_group(`.
6. **Never widen a constant to make a test pass.** `DEFAULT_TIME_GAPS`,
   `MAX_ALERTS_PER_PATIENT` and the rate limits are load-bearing.
7. **Count your facts.** If you quote a number in a document, re-derive it; the
   previous passes found stale counts in five files.
8. **Run the gates before you commit** (§0), and run the 46-invariant audit after
   any change to the engine, the knowledge base or the deployment path.

---

## Appendix A · The gates, and what each one is for

| Gate | Command | What a failure means |
| --- | --- | --- |
| Backend tests | `pytest tests/ -q` (cwd `backend`, `PYTHONPATH` set) | A contract, an engine rule or a hardening invariant regressed |
| Lint | `ruff check .` | Style only; never a reason to skip a gate |
| Safety invariants | `python3 scripts/safety_invariant_audit.py` | One of the 46 properties the adversarial review demanded no longer holds |
| Deployment scripts | `pytest tests/test_deployment_scripts.py` | A deploy would ship something the smoke test cannot verify |
| PowerShell parse | `test_parser.ps1` (structural only) | A script is syntactically broken — not that it runs |
| Client | `flutter analyze` / `flutter test` (CI) | Client logic or the coverage-ledger rule regressed |
| Container | CI build + build-time knowledge gate | The image would not contain a usable corpus |

## Appendix B · A suggested first two weeks

Day 1–2: run every gate in §0, then start the dev server and generate a schedule
for the demo household. Read `docs/ADVERSARIAL_REVIEW.md`; the invariants are the
product.

Week 1: **B-2** (emulator + integration harness) and **S-1** (every separation rule
carries a gap) — both are self-contained, both raise confidence in everything
afterwards. In parallel, start the **B-1** review conversation; it has the longest
lead time.

Week 2: **D-1** (consent gates processing) and **B-4/B-5** (the adversarial
authorization suite and the front door), while the reviewer works through the
ceilings. Use the review's first findings to write **S-8** as data, not code.

Do not start **U-1** or **U-2** before **B-1** has a named reviewer: shipping more
features on an unreviewed corpus increases the surface of a claim nobody has
signed.
