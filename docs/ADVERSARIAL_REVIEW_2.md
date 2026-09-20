# Adversarial review — `handoff/fixes-phase1`

**Subject:** branch `handoff/fixes-phase1` at `a7b536c` ("Dart: fix the analyzer errors
pub-resolve was hiding"), 15 commits on top of `fbde524`.
**Method:** read the diff, then run the system. Nothing below is inferred from
prose: every finding is produced by executing the branch's own code, and every
claim about a test says what that test actually asserts.
**Reviewer stance:** the branch is judged by the standard this repository sets for
itself — *fail loud, never green; a claim in a document must be true of the code;
an invariant counts only when something executes it.*

---

## 0. What I ran

All against a clean checkout of `a7b536c` (a separate git worktree, so the
reviewing tree was never the reviewed one).

| Command | Result |
| --- | --- |
| `pytest tests/ -q` (`backend/`, `PROJECT_ID=ci-project`) | **196 passed** |
| `ruff check backend/` | clean |
| `python3 scripts/safety_invariant_audit.py` | **43/43, exit 0** |
| `python3 scripts/check_doc_counts.py` | "All quoted counts match the knowledge base." |
| `bash -n deploy.sh` | parses |
| Targeted probes (§ C-1 … L-4 below) | the findings |

Not executed, and therefore not claimed: `flutter analyze`, `flutter test`, any
Dart runtime (no toolchain in this environment), PowerShell scripts, and anything
against real Firestore, Firebase Auth, FCM or Gemini — every probe ran on the
in-repo fakes.

---

## 1. Findings at a glance

| id | severity | finding |
| --- | --- | --- |
| **C-1** | **critical** | A prescribed dose whose time is not one of five hard-coded slots is **silently dropped**: it is not scheduled, not reported as unscheduled, and the schedule still says `verified` / "Every dose was placed and re-checked". |
| **H-1** | high | Consent gates **writes, not reads**: after withdrawal the medication list, allergies and profiles are still served (and editable), while `docs/API.md` claims *every* endpoint that touches health data checks a scope. |
| **H-2** | high | The client has no consent surface at all — with the new gate live, a production user's first scan or schedule returns a 403 the app cannot show, explain or clear. |
| **H-3** | high | Six of the new safety disclosures reach the API and no user: `truncated_count`, `unmatched_allergies`, `awaiting_confirmation`, `as_needed`, `tapers`, `food_relation_unmet`. Allergies and meal times cannot be recorded from the app, so the allergy and meal-relation features cannot fire in production. |
| **H-4** | high | The audit lost three checks (46 → 43), one test became vacuous, and three documents still advertise `46`; `check_doc_counts.py` guards only the four knowledge-base counts, so it prints a green line over stale numbers. |
| **M-1** | medium | A twice-daily or three-times-daily medicine can be scheduled 4–5 hours apart with no note; nothing constrains the interval *within* one medicine. |
| **M-2** | medium | The nitrate/PDE5 rule declares a 48 h gap that no same-day timetable can satisfy, so the pair is always reported as a *slot* problem rather than an *avoid*, and the label's "24–48 hours" range is silently narrowed to 48 with no recorded basis. |
| **M-3** | medium | The export's pagination cursor is an ISO **string** passed to `start_after` against a Firestore **timestamp** field. The fake stringifies both sides, so the suite cannot fail; production behaviour is untested and the failure mode is a permanent 503 on page 2. |
| **M-4** | medium | `POST /medications/manual` and `PATCH /medications/{id}` accept unvalidated `timing` — the entry point for C-1. |
| **L-1** | low | `POST /interactions/{alert_id}/acknowledge` accepts an id that does not exist (SOS was fixed for exactly this). |
| **L-2** | low | The "x of y prescribed doses" denominator counts attempted doses, not prescribed ones ("0 of 1" for a twice-daily medicine). |
| **L-3** | low | `LICENSE` names `backend/tests/test_safety_invariants.py (if present)`, which does not exist; `LICENSING.md` says 43 invariants while README/HANDOFF/review say 46. |
| **L-4** | low | The knowledge base is declared Apache-2.0 while its sources are copyrighted works (FDA labels, BNF, Stockley's); the open-core grant deserves one line of legal review and a pointer to `docs/KNOWLEDGE_SOURCES.md` as the authority. |

---

## C-1 · A dose at any time outside the five default slots is silently dropped, and the schedule still says "verified"

**Severity: critical.** This is the failure class this repository exists to make
impossible — a green reassurance over missing data — reintroduced through a new
door.

### What happens

A medicine recorded with a timing the scheduler does not have a slot for
(`07:00`, `14:00`, `19:00`, `07:30`, `21:30`, `23:00`, …) **disappears from the
timetable**. It is not listed in `unscheduled`, not in `conflicts`, not in
`unchecked_medications`, and the response reports:

```json
"schedule_status": "verified",
"verification": {"verified_against_rules": true, "violations": [], "checked_alerts": 0, ...},
"dose_times": [],
"unscheduled": []
```

The client turns that into a green tick and the sentence **"Every dose was placed
and re-checked"** (`medication_orchestra/lib/screens/schedule_screen.dart:344-361`).
The patient gets no reminder and no warning.

### Evidence (executed)

End-to-end through the real endpoint functions, in-memory Firestore:

```python
# scripts/probe: add a medicine the way a prescription would read
add_medication_manually(MedicationPayload(profile_id=pid, brand_name="Eltroxin 50",
    dosage="50mcg", frequency_raw="OD", timing=["07:00"]), user_id=USER)

# then
generate_schedule_endpoint(profile_id=pid, language="en", user_id=USER)
```

```
added: {'saved': True, 'medication_id': 'fbea33c8-…'}
schedule_status: verified
dose_times: []
unscheduled: []
unchecked_medications: []
awaiting_confirmation: []
verification: {'verified_against_rules': True, 'violations': [], 'rules_checked': [],
               'checked_alerts': 0, 'checked_at_slots': 0, 'knowledge_version': {…}}
medicine record timing: ['07:00']
```

The same behaviour at the engine boundary, for four ordinary prescriptions:

| prescription | doses wanted | doses in `dose_times` | `unscheduled` | status |
| --- | --- | --- | --- | --- |
| `BD` at `07:00, 19:00` | 2 | **0** (whole medicine missing) | `[]` | `verified` |
| `BD` at `08:00, 14:00` | 2 | **1** | `[]` | `verified` |
| `OD` at `07:30` | 1 | **0** | `[]` | `verified` |
| `TDS` at `08:00, 14:00, 20:00` | 3 | **2** | `[]` | `verified` |

Only times drawn from `DEFAULT_SLOT_TIMES` (`08:00, 13:00, 17:00, 20:00, 22:00`,
`backend/services/clinical_engine.py:784`) survive.

### Root cause

Two sites, both in `backend/services/clinical_engine.py`:

1. **The solver accepts times that are not slots.** The candidate list is seeded
   with the patient's own preferred times (`clinical_engine.py:1211-1215`), so a
   dose can be *assigned* to e.g. `07:00`:

   ```python
   candidates: list[str] = []
   if preferred_time:
       candidates.append(preferred_time)          # may be outside DEFAULT_SLOT_TIMES
   candidates.extend(t for t in preferred if t not in candidates)
   candidates.extend(t for t in DEFAULT_SLOT_TIMES if t not in candidates)
   ```

2. **The payload renders only slots.** `dose_times` is built by iterating
   `DEFAULT_SLOT_TIMES` (`clinical_engine.py:1279-1288`), so an assignment to
   `07:00` is never emitted:

   ```python
   for slot_time in DEFAULT_SLOT_TIMES:           # 07:00 is not in this tuple
       meds_here = [m for m in active if slot_time in assignments.get(…, [])]
   ```

Because the dose counted as *placed* in `assignments`, the `_record_unscheduled`
path never runs for it, `conflicts` stays empty, and the status expression
(`"partial" if (conflicts or awaiting_confirmation or blocked_medications) else
"verified"`, lines 1350–1353) reports `verified`.

The file's own comment two lines above that expression states the contract that is
being broken:

```python
# "verified"       - every prescribed dose placed, no constraint violated
```

`verify_schedule()` cannot catch it either: it re-checks the *alerts* against the
*payload*; it never asks whether every prescribed dose is in the payload. So the
independent verifier passes a timetable that is missing a medicine — the exact
scenario the verifier exists to prevent.

### Why 196 tests and a 43-check audit miss it

* No test anywhere uses a non-default slot time. Every `timing=[…]` in
  `backend/tests/` is drawn from `{08:00, 13:00, 20:00, …}`; a scan for
  `"0\d:\d\d"` values outside the default set returns nothing.
* The audit's INV-6 profiles use `["08:00"]` and `["20:00"]`.
* The extraction prompt (`backend/services/gemini_service.py:79-81`) *asks* the
  model to speak in default slot times ("breakfast 08:00, lunch 13:00, …"), which
  makes the common path look safe while leaving every other path exposed. It does
  not forbid a real prescription's "7 am" from coming back as `07:00`.
* `PATCH /api/v1/profiles/{pid}/medications/{med_id}` and
  `POST /api/v1/medications/manual` accept `timing` as free-form strings with no
  format or slot validation (`backend/main.py:220-237`, `861-877`), so the client
  and the user can put any time into the record.

### Why it matters

The population this product serves has prescriptions written as *"7 am, empty
stomach"* (levothyroxine), *"after lunch"* (13:30/14:00), *"at night"* (21:30), and
*"1-0-1"* with times the patient states out loud. For all of those the app says the
schedule is verified and reminds the patient of nothing.

### The test that should pin it

```python
@pytest.mark.parametrize("timing", [["07:00"], ["07:00", "19:00"], ["08:00", "14:00", "21:30"]])
def test_a_time_the_slots_do_not_contain_is_honoured_or_reported(registry, timing):
    meds = [med("m1", "Dolo", dosage="650mg", timing=timing)]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    placed = [t["time"] for t in schedule["dose_times"]]
    reported = {u["med_name"] for u in schedule["unscheduled"]}
    assert len(placed) == len(timing) or "Dolo" in reported, (
        f"{timing} produced {placed} and reported nothing: a dose vanished"
    )
```

Plus two properties that would have caught the class of bug rather than this
instance:

* `test_every_prescribed_dose_appears_exactly_once_in_the_payload_or_in_unscheduled`
  — count `sum(doses_per_day(med))` against `len(placed) + len(doses_not_placed)`
  for every medicine, on every scenario in the suite.
* `test_verified_means_every_prescribed_dose_was_placed` — assert that
  `schedule_status == "verified"` **implies** the count above balances.
* An API-level test: a medicine added with `timing=["07:00"]`, then
  `generate_schedule_endpoint`, must not return `verified` with an empty
  timetable.

### How to fix it (and the trap in the obvious fix)

Make the slot set a *starting point*, not the universe: the solver should be able
to place a dose at any validated `HH:MM` time, and `dose_times` should be built
from the times actually assigned (sorted), not from `DEFAULT_SLOT_TIMES`. If a
time is rejected instead — the more conservative option — it must be rejected
loudly: record it through `_record_unscheduled` and set the status to `partial`.

The trap: emitting arbitrary times re-opens interaction gaps, because
`_feasibility_conflict` compares *minutes* and already handles arbitrary times —
good — but the client's slot rendering (`_slot_label`) and the reminder planner
both assume a small set. Whichever direction is chosen, the invariant to add is
the counting test above, not "the times come from a list".

---

## H-1 · Consent gates writes, not reads — and the document says otherwise

**Severity: high.** `D-1`'s stated goal is "consent gates processing, it is no
longer just filed". It gates *some* processing.

### Evidence (executed)

With a recorded, then withdrawn consent record, calling the endpoint functions
directly on the same user:

| endpoint | result |
| --- | --- |
| `POST /api/v1/medications/manual` | **403** `{"error": "consent_required", "missing_scope": "medication_review", …}` |
| `GET /api/v1/interactions` | **403** |
| `POST /api/v1/schedule/generate` | **403** |
| `GET /api/v1/profiles/{pid}/medications` | **200 — full medication list returned** |
| `PATCH /api/v1/profiles/{pid}/medications/{med_id}` | **200 — health record edited** |
| `GET /api/v1/profiles/{pid}/allergies` | **200 — allergy list returned** |
| `GET /api/v1/profiles` | **200** |
| `POST /api/v1/devices/register` | 200 (acceptable) |
| `POST /api/v1/interactions/{id}/acknowledge` | 200 (acceptable) |
| `GET /api/v1/users/export` | 200 (defensible: a subject-access request is itself a right) |

The read of the medication list is the one that matters: withdrawal is supposed to
stop processing, and "processing" plainly includes serving the medicine list to
the app that displays it.

### The claim that is now false

`docs/API.md:269`: **"Every endpoint that reads, stores or sends health data
checks a consent scope."** Twenty of the 31 routes carry no scope check (`grep -n
"@app\." backend/main.py` against the `_require_consent` call sites), and the ones
above are among them.

### The related promise nothing implements

`backend/main.py:1313-1315` records `withdrawn_at` and `deletion_due_at = at + 30
days`, and `docs/SECURITY_AND_PRIVACY.md` §9 says health data is kept "for 30 days
after a consent withdrawal (`deletion_due_at`), so a household can change its
mind." Nothing reads that field: there is no job, no scheduled function, and no
code path that deletes anything at day 30. As written the sentence is a retention
statement, but any reader (and any auditor) will take it as a deletion promise.

### The tests that should pin it

* `test_every_route_is_classified_by_consent_scope` — a table over all 31 routes
  in `main.py`, each marked `scope=<name>` or `public`/`account_management`, so a
  new route fails the test until it is classified. This is the only version that
  survives future development.
* `test_withdrawal_stops_reads_of_medication_data` — the probe above, as an
  assertion.
* `test_the_retention_clock_has_an_owner` — either a test that the deletion job
  runs and erases past-due accounts, or a test that the documentation says the
  clock is recorded but not enforced. Silence is the one unacceptable option.

### Caveats

* `DEV_MODE` + `DEV_AUTH_BYPASS` relaxes the check (`backend/services/consent_service.py:41-50`).
  That is right for local work, and it is also why nobody has seen the failure
  mode in H-2. Keep it, but keep a test that asserts the relaxation requires
  *both* switches (it exists) and one that runs the refusal path with only
  `DEV_MODE` set.
* Reading a profile list is arguably "account management", not health processing.
  Say so explicitly in the table test rather than leaving the absence of a check
  to look like an oversight.

---

## H-2 · The client cannot obtain or even display consent — the gate is a shipping blocker

**Severity: high.** The commit that added the gate did not add the surface that
satisfies it.

### Evidence

* `grep -rn "consent" medication_orchestra/lib` → **no matches**. The app never
  calls `POST /api/v1/users/consent`, has no consent screen, and does not read
  `consent` from `GET /api/v1/users/settings` (it calls that endpoint only for the
  risk-limit settings).
* `grep -rn "consent_required\|missing_scope" medication_orchestra/lib` → no
  matches. A 403 from the gate surfaces to the user as whatever the generic error
  handler says.

In production neither `DEV_MODE` nor `DEV_AUTH_BYPASS` is set, so
`consent_service.require()` refuses every scan, confirm, interaction check and
schedule for an account with no consent record — which is every account, because
nothing can create one. The app is unusable until someone calls the endpoint by
hand; the demo server hides this because the relaxation is on there.

### The tests that should pin it

* `test_the_scan_screen_offers_consent_when_the_api_refuses` — a widget test that
  stubs the 403 `consent_required` body and asserts the screen shows the
  `what_this_covers` text and a way to accept, then retries.
* A client-side contract test that the app's error mapper understands
  `{"error": "consent_required", "missing_scope": …}`. Without it, adding a
  server-side gate is invisible to whoever maintains the app.
* One end-to-end check in the device acceptance list (HANDOFF §B-6): a fresh
  install must be able to reach a schedule without a curl command.

---

## H-3 · Six of the new safety disclosures reach the API and no user

**Severity: high** — not because any of them is wrong, but because a disclosure
nobody sees is not a disclosure, and this repository's first rule is that the user
learns what was not checked.

### Evidence

`grep -rl "<field>" medication_orchestra/lib` for each new field:

| field | backend | client |
| --- | --- | --- |
| `truncated_count` (S-3) | ✓ | **0 files** |
| `unmatched_allergies` (S-2) | ✓ | **0 files** |
| `awaiting_confirmation` (S-7) | ✓ | **0 files** |
| `as_needed` (S-9) | ✓ | **0 files** |
| `tapers` (S-9) | ✓ | **0 files** |
| `food_relation_unmet` (S-6) | ✓ | **0 files** |
| `verified_against_rules` (S-5) | ✓ | **0 files** |

The last one is benign — the client's badge reads `schedule_status`, which still
exists — but the rest are the substance of their features:

* **Allergies (S-2)** are backend-only end to end: `grep -ri allerg
  medication_orchestra/lib` → no matches. There is no way for a caregiver to
  record an allergy from the app, so the allergy check cannot fire for any real
  user. The engine work is good (§7); the feature is unusable.
* **Meal relations (S-6)** are the same: no meal-time UI
  (`grep -rin meal medication_orchestra/lib` → no matches), so every real
  household stays in the `meal_times_unknown` branch forever, and the
  `empty_stomach` dose is always reported as unmet.
* **Truncation (S-3)** is computed and disclosed in `coverage.patients[].truncated_count`
  and never rendered, so a caregiver can still be shown 40 of 47 findings with no
  indication that seven were held back.
* **Confidence gating (S-7)** puts a low-confidence medicine in
  `awaiting_confirmation` — the client neither lists it nor offers the
  confirm-identity call (`/confirm-identity` has no caller in `lib/`), so a
  medicine read with low confidence becomes invisible rather than confirmed.
* **PRN and taper (S-9)** are returned and never shown: an as-needed medicine and
  a reducing course both vanish from the screen, which is worse than the wrong
  schedule, because the user cannot tell they were recognised.
* **Export and deletion (D-3, D-4)** have no UI either. A data-subject right that
  can only be exercised with curl is not a right the household has.

### The test that should pin it

A generated coverage test rather than a per-feature one: parse the schedule and
interaction response schemas from `docs/API.md` (or a small `RESPONSE_FIELDS`
manifest) and assert every user-facing field is either rendered by a widget test
or listed in an explicit `api_only: true` set with a reason. That converts
"forgot the client" from a review finding into a build failure.

### Caveats

* Some of these may be deliberately deferred. That is a legitimate decision; what
  is not legitimate is leaving them unlisted while the branch's own summary reads
  as if they shipped. If they are deferred, say so in one line per field — a
  reader currently has to grep the Dart tree to find out.

---

## H-4 · The audit lost three checks, one test went vacuous, and three documents still say 46

**Severity: high**, because the counts are the repository's own evidence of
coverage.

### The audit shrank

Instrumenting `check()` in both versions and diffing the list of checks that
actually executed: the tree before this branch ran **46** checks, this branch runs
**43**, and the count is printed by the script itself, so the change is invisible
unless someone diffs it. The three that no longer execute are exactly the INV-6
"unplaceable medicine" assertions:

  ```
  each unplaceable medicine is reported once: ['Combiflam']
  every unplaceable medicine has a conflict entry with a reason
  the schedule reports itself as partial
  ```

* They disappeared because the audit's own "tight" profile no longer produces an
  `unscheduled` entry: with every severity default now `0.0`, the solver can place
  all three medicines (verified by running that profile through the branch:
  `status=verified`, `unscheduled=[]`, placed at 08:00/13:00/17:00/20:00/22:00).
  The checks were written as *conditional* checks, so they silently stopped
  existing instead of failing. **A safety audit whose coverage can shrink without
  a signal is a measurement that lies** — the same defect class as the product
  ones.

### A test is now fully vacuous

`backend/tests/test_clinical_engine.py:553-565`,
`test_every_dropped_medicine_has_a_conflict_entry_with_a_reason`, uses the m1/m2/m3
scenario that now places everything:

```
status: verified   unscheduled: []   conflicts: []
```

`{u["med_name"] for u in schedule["unscheduled"]} <= explained` is `set() <= set()`
— trivially true — and the loop over `conflicts` runs zero times. The test
currently proves nothing, and its name is a claim about dropped medicines.

### The documents disagree with each other and with the code

| file | says | truth |
| --- | --- | --- |
| `README.md:116` | "46 checks" | 43 |
| `HANDOFF.md:5` | "142 backend tests … a 46/46 safety-invariant audit" | 196 tests, 43 checks |
| `docs/ADVERSARIAL_REVIEW.md:27` | "`46/46` checks hold" | 43 |
| `LICENSING.md` | "the 43 safety invariants" | 43 ✓ |

`check_doc_counts.py` — the H-2 fix — covers `ingredients`, `interaction rules`,
`brand presentations` and `advisories` only (`PATTERNS`, lines 25–31), so it
prints *"All quoted counts match the knowledge base."* while three documents quote
stale test and audit counts. That green line is precisely the artefact the
repository's rules forbid.

### The tests that should pin it

* **A floor on the audit's own size**: `safety_invariant_audit.py` should carry a
  manifest of invariant ids and assert that every id executed at least one check,
  or the runner should assert `len(RESULTS) >= 46` with a comment explaining the
  number. Cheap, and it turns "a check quietly stopped running" into a failure.
* **Conditional checks are the bug**: rewrite the INV-6 profile so it *cannot*
  place everything (e.g. five medicines that must not co-allocate into five slots,
  or an explicit `assert schedule["unscheduled"], "this scenario must produce an
  unplaceable medicine"` before the assertions). A check that can be skipped needs
  a precondition assertion that fails when the scenario stops being valid.
* **Extend `check_doc_counts.py`** to the two counts that drifted: run
  `pytest --collect-only -q` for the test count and execute or parse the audit for
  the check count, then match `(\d+)\s+(?:backend\s+)?tests?` and
  `(\d+)\s+checks?` in the docs. Failing that, delete the numbers from the prose —
  a count that nobody guards is a liability, not evidence.

---

## M-1 · "Twice daily" can mean four hours apart, silently

**Severity: medium** — the schedule is conflict-free but clinically odd, and the
app prints `BD` next to it.

### Evidence (executed)

| scenario | medicine | placed | interval |
| --- | --- | --- | --- |
| four BD medicines in one household | Warfarin 5mg `BD` | 08:00, 20:00 | 12 h |
| " | Brufen 400 `BD` | **13:00, 17:00** | **4 h** |
| " | Combiflam `BD` | 22:00 (one dose) | — |
| TDS + warfarin + NSAID | Brufen 400 `BD` | **13:00, 20:00** | **7 h** |

The README's own demo household shows the same shape:
`13:00/17:00 Combiflam`.

### Root cause

`FREQUENCY_SLOTS` (`clinical_engine.py:786-792`) seeds *preferences*
(`"bd": (2, (0, 3))` → 08:00 and 20:00); nothing constrains the *result*. When
interaction gaps push doses around (`M-1` in the audit's own INV-6 scenario
moves Ecosprin to 22:00), a BD medicine can end up with the day's two doses four
hours apart. There is no `min_interval_hours` concept anywhere:
`grep -n "min_interval\|spacing" backend/services/clinical_engine.py` → nothing.

### Why it matters

"Twice daily" means roughly every 12 hours; four hours is a different
pharmacokinetic exposure for a drug with a short half-life (ibuprofen), and for a
drug with a narrow window it is worse. The app presents both as the same
instruction, and the caregiver follows the reminder.

### The test that should pin it

```python
def test_a_bd_medicine_is_not_compressed_into_four_hours(registry):
    ...
    assert gap >= 8 or schedule["spacing_warnings"], (
        f"{gap}h between two BD doses with no disclosure"
    )
```

Either enforce a documented minimum interval per frequency code (BD ≥ 8 h,
TDS ≥ 4 h between adjacent doses is a defensible starting policy) and let a
violation push the dose to `unscheduled` with a reason, or compute the interval
and disclose it. What is not acceptable is doing neither.

### Caveats

The policy number is a clinical judgement, not an engineering one — it belongs in
the knowledge base next to the interaction gaps, with a citation, and it must not
be invented to make a test pass (§B-1 of HANDOFF, which is still open).

---

## M-2 · A 48-hour gap cannot be expressed in a one-day timetable

**Severity: medium.** The rule is right; its representation is wrong.

### Evidence (executed)

Isosorbide mononitrate `OD` + sildenafil `OD`:

```
alerts: [('ddi_nitrate_pde5', 'major', 48.0)]
status: partial
placed: [('08:00', ['Isosorbide Mononitrate'])]
unscheduled: [('Sildenafil', 'no_safe_slot_available')]
conflicts: [(['Sildenafil'], 'no_safe_slot_available')]
```

The solver places all doses inside one day, so `abs(a - b) >= 48` can never hold;
the pair *always* produces "no safe slot available today" — a scheduling failure —
for what the label calls a contraindicated combination. The user sees a timetable
they cannot complete rather than the actual instruction: *these must not be taken
together; talk to the prescriber.*

### Two smaller issues in the same rule

* The label's advice reads "within **24–48 hours** of a nitrate"
  (`interactions.json`, `management`), and the machine constraint is `48.0` — the
  stricter end of a cited range, which is defensible, but nothing records *why* 48
  was chosen over 24. Add a `min_gap_basis`/`notes` field; the next reviewer will
  otherwise read it as an invented number.
* The rest of the tree was fixed for this class upstream: nitrate/PDE5 is no
  longer prose-only. Good — this is about the semantics of a >24 h gap, not about
  its existence.

### The test that should pin it

* `test_a_gap_longer_than_a_day_is_reported_as_an_avoid_indication_not_a_slot_conflict`
  — assert the response carries an `avoid`/`contraindicated`-style instruction for
  that pair and that the timetable does not pretend the pair could be scheduled
  with better luck.
* `test_every_rule_records_why_its_gap_was_chosen` — `min_gap_hours` non-zero
  implies a basis field, mirroring the citation rule.

---

## M-3 · The export cursor is a string compared against a timestamp

**Severity: medium**, because the fake hides it and the fix is small.

### Evidence

`backend/services/audit_service.py:110-111` (page_events):

```python
if cursor:
    query = query.start_after({"at": cursor})     # cursor is entries[-1]["at"], an ISO string
```

`_shape()` converts the stored `datetime` to `at.isoformat()`, that string is
returned as `next_cursor`, and the next call passes it straight back into
`start_after` against a field Firestore stores as a `Timestamp`.

In the fake this works by construction:

```python
def _sort_key(value):                     # tests/fakes.py:89-93
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return "" if value is None else str(value)
...
out = [s for s in out if _sort_key(s.to_dict().get(field)) > bound]   # fakes.py:145-148
```

Both sides become strings, so `test_the_whole_audit_trail_can_be_exported_by_paging`
passes. Against real Firestore, `start_after` with a string for a timestamp field
is a type mismatch: the query either errors (→ the endpoint's 503, with the text
"Please try again", which will never help) or silently returns an empty page and
`complete: true` after the first page. Either way the audit trail is broken for
accounts with more than 500 events — the exact case D-3 was written to fix.

I cannot settle which of the two behaviours occurs without real Firestore or the
emulator; that is itself the finding: **the only thing that can decide this is the
integration test HANDOFF §B-2 asks for.**

### The test that should pin it

* `test_the_export_cursor_round_trips_through_a_real_firestore_value` against the
  Firebase emulator (composite cursor of `at` + document id, compared as the
  native timestamp type).
* A unit-level guard that survives the fake: assert the cursor's type is a
  `datetime` (or an opaque token that decodes to one) on the way in and out, so
  the string conversion is caught without infrastructure.

### Caveats

Also worth noting: `GET /users/export` has no client surface (H-3), so no user can
trigger this today. That lowers the urgency and does not change the correctness.

---

## M-4 · The API accepts timing values the scheduler cannot honour

**Severity: medium** — this is the entry point for C-1.

`MedicationPayload.timing` (`backend/main.py:227`) and the `PATCH` allow-list
(`backend/main.py:866-867`) accept any strings; nothing validates `HH:MM`, nothing
rejects a time the slot set cannot express, and nothing warns. The meal-times
endpoint *does* validate (`re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", …)`,
`main.py:786-789`) — the same rigour is missing one function away.

**Fix:** validate the format at the edge, and either make the engine able to place
any valid time (preferred) or refuse the value with a clear message and a
`partial` schedule (conservative). Whichever is chosen, add the counting test from
C-1 so the invariant is enforced, not the intention.

---

## L-1 · Acknowledging an alert that does not exist succeeds

`POST /api/v1/interactions/{alert_id}/acknowledge` (`backend/main.py:970-986`) only
rejects ids that are too long or contain `/`:

```
POST acknowledge("not-a-real-id") → {"status": "acknowledged", "interaction_id": "not-a-real-id"}
```

Compare with SOS, which was fixed for this exact reason in the previous pass
(`main.py:1152-1160` → 404 "That alert is no longer on file"). The consequence is
milder (an acknowledgement is a user's own note, not a message to a family
member), but the pattern is the same: an endpoint that answers "fine" to a
question about something that does not exist.

**Pin:** `test_acknowledging_an_unknown_alert_returns_404` — and, better, a shared
`_known_alert_ids(user_id)` helper used by both endpoints so the two cannot drift.

---

## L-2 · "0 of 1 prescribed doses" for a twice-daily medicine

`backend/services/clinical_engine.py:1565-1575` builds the conflict message with:

```python
f"{placed_counts.get(entry['med_id'], 0)} of "
f"{max(placed_counts.get(entry['med_id'], 0) + len(entry['doses_not_placed']), 1)} "
f"prescribed doses of {entry['med_name']} could be placed …"
```

The denominator counts *attempted* doses, not prescribed ones. Observed:
Ecosprin 75 `BD` with nothing placed produced "0 of 1 prescribed doses". The
prescribed count is already available — `_doses_per_day(med)[0]` — and using it
makes the sentence true: "0 of 2".

**Pin:** `test_the_conflict_message_counts_prescribed_doses` over a BD medicine
with zero placed.

---

## L-3 · Licence documents disagree and reference a file that does not exist

* `LICENSE` lists `backend/tests/test_safety_invariants.py   (if present)`. It is
  not present and never was. A licence that guesses is worse than one that is
  shorter.
* `LICENSING.md` says "the 43 safety invariants"; `README.md`, `HANDOFF.md` and
  `docs/ADVERSARIAL_REVIEW.md` say 46 (see H-4).
* `test_licensing.py` verifies SPDX headers on three open files and three closed
  ones; `backend/knowledge/**` is covered by `knowledge/LICENSE` (fine, data files
  cannot carry headers) but nothing pins the boundary for the remaining ~100
  source files. A one-line generated check (`every .py/.dart file starts with an
  SPDX line matching its directory's claimed licence`) would make the boundary
  real.

---

## L-4 · Open-core grant over copyrighted sources

`backend/knowledge/LICENSE` declares the knowledge base Apache-2.0. The rules are
paraphrases with citations to FDA labelling, BNF, Stockley's and STOPP/START
(`docs/KNOWLEDGE_SOURCES.md`). Facts and paraphrases are generally not the
sources' property, but a *selection and arrangement* claim is not impossible, and
`brand_mapping.csv` carries trademarks, which Apache-2.0 explicitly does not
grant. This needs one line from counsel and one line in `LICENSING.md` pointing at
`docs/KNOWLEDGE_SOURCES.md` as the authority on provenance. Low legal risk, high
cost if it is wrong; it is a paragraph of work.

---

## 2. Claims vs implementation

The repository's own rule is that a prose claim must be true of the code. Current
scorecard for this branch:

| claim | where | status |
| --- | --- | --- |
| "consent gates processing" | commit message, `docs/SECURITY_AND_PRIVACY.md` §5 | **partially true** — writes gated, reads not (H-1) |
| "Every endpoint that reads, stores or sends health data checks a consent scope" | `docs/API.md:269` | **false** (H-1) |
| "`verified` — every prescribed dose placed, no constraint violated" | `clinical_engine.py:1346` | **false for non-slot times** (C-1) |
| "the 46 safety invariants" | `README.md:116`, `HANDOFF.md:5`, `ADVERSARIAL_REVIEW.md:27` | 43 (H-4) |
| "142 backend tests" | `HANDOFF.md:5` | 196 (H-4) |
| "All quoted counts match the knowledge base" | `check_doc_counts.py` output | true *for the four counts it checks*, misleading otherwise (H-4) |
| "the 43 safety invariants" | `LICENSING.md` | ✓ |
| "an as-needed medicine gets NO reminder slot" | `clinical_engine.py:1165-1176` | ✓ (and never reaches a screen — H-3) |
| "This is your own record" on an allergy alert | `interactions` response | ✓ — and the wording is exactly right |
| "we could not match that name … your medicines were NOT checked against it" | `unmatched_allergies` | ✓ — best-in-branch fail-loud behaviour |

---

## 3. What is genuinely fixed (verified by execution, not by reading)

Credit where the work is real — all of the following I re-ran and can vouch for:

* **`min_gap_hours` on all 33 rules** with no prose-only hour claims: every rule
  declares a numeric gap, four are non-zero (48/8/6/4), and a regex over every
  rule's advice/management text finds no hour number that the machine constraint
  contradicts. The severity-0 defaults mean a rule that *forgets* the field yields
  no separation rather than an invented one, and a test pins that.
* **Severity-ordered truncation** keeps every `contraindicated` and `major`
  finding (`clinical_engine.py:225-238`) and discloses a count; the unit test
  manipulates the cap and asserts a critical survives.
* **Allergies** resolve exactly, phrase themselves as the household's record, and
  an unmatched label sets `is_complete: false` with a per-allergy note that says
  the medicines were **not** checked against it. That is the repository's standard,
  met.
* **Deletion** is thorough and self-verifying: 33 documents across 11 stores, a
  re-read that reports anything left behind, `device_index` cleaned, the audit
  trail deliberately retained with a stated reason, and the user document reduced
  to a tombstone + withdrawn consent.
* **Consent refusal payloads** name the missing scope and what it covers, and an
  empty `consent_version` is not treated as consent (`consent_service.granted_scopes`).
* **`verified_against_rules` / `rules_checked` / `knowledge_version`** rename is
  done consistently in the backend and in the audit script.
* **CI** runs `pip-audit --strict`, the invariant audit, the tests, `flutter
  analyze/test`, a container build with a build-time knowledge gate, plus shell
  parse and knowledge-integrity checks — and nothing is `continue-on-error`.
* **`check_doc_counts.py`** genuinely fails when a knowledge-base count is wrong
  (verified by editing a count and watching it exit 1), and is wired into pytest.
* **The removal of `send_dose_reminder`** and the `model_capabilities.yaml`
  scaffolding; the client README; the client-side fixes for
  `flutter_timezone` ≥ 4 (`zone.identifier`), `DropdownButtonFormField.initialValue`
  and the `timezone ^0.9.4` pin that actually resolves.
* **The audit script's own honesty**: 43 checks, each executing real code, exit
  non-zero on any failure.

---

## 4. Limits of this review

* **Dart was not executed.** There is no Flutter toolchain in this environment.
  The client findings are grep-level facts (a symbol does not appear in `lib/`)
  plus a reading of the widget code; `flutter analyze` and `flutter test` in CI
  are the authority for whether the branch compiles.
* **No real infrastructure.** Firestore, Auth, FCM and Gemini were faked. M-3 is
  the finding that most needs the emulator to settle, and C-1 is the one that does
  not — it reproduces entirely inside the engine.
* **PowerShell** (`deploy.ps1`, `monitoring_setup.ps1`, `test_parser.ps1`) was
  inspected structurally only.
* **Not a security review.** No pen-test, no threat model; the tenancy and
  injection checks in the suite were re-read, not re-derived.
* The branch was reviewed as a deployed unit. Where a finding is a *missing client
  surface* (H-2, H-3) it is possible that the intent was API-first; that is a
  scope decision I cannot see from the code, so I have stated what is true rather
  than what was intended.

---

## 5. Suggested order of work

1. **C-1** first and alone. It is the only finding that puts a wrong instruction in
   front of a patient today, it is a one-line class of bug with a counting
   invariant that generalises, and it makes the branch's headline claim ("fail
   loud, never green") true again.
2. **H-2**, because until it is done the product cannot be used in production at
   all — the gate is live and there is no way through it from the app.
3. **H-1** (reads gated, and the `deletion_due_at` promise either implemented or
   withdrawn), together with the `docs/API.md` sentence.
4. **H-4**: the audit floor, the vacuous test, and one guard that keeps the counts
   honest — cheap, and it is the mechanism that would have caught C-1's silence.
5. **H-3**: a field-coverage test, then client surfaces in order of user impact
   (truncation, allergies, awaiting-confirmation, PRN/taper, meal times, export
   and deletion).
6. **M-1 … M-4, L-1 … L-4** as ordinary work, each with the pin named above.

Then re-run the whole gate set, and re-check this document: every finding here has
a reproduction, so "fixed" is a claim that can be verified rather than believed.
