# API contract

Base path `/api/v1`. All endpoints require `Authorization: Bearer <Firebase ID token>`
unless noted. **Every clinical response carries a `coverage` block, and no
endpoint ever returns the word "safe".**

Two rules the client must not work around:

1. **Never render reassurance unless `coverage.is_complete` is `true`.** If it is
   `false`, `unchecked[]` names each medicine that could not be identified and
   why. Showing a green tick anyway is the defect this API was rewritten to
   prevent.
2. **Never combine two patients.** `profile_id=all` means "check every patient,
   separately". There is no endpoint that pairs one person's medicine with
   another's.

---

## Health

### `GET /health` — no auth
```json
{
  "status": "ok",
  "service": "medication-orchestra",
  "version": "2.0.0",
  "decision_engine": "deterministic",
  "knowledge_base": { "ingredients": 195, "interaction_rules": 33,
                      "advisories": 3, "brand_presentations": 175,
                      "ingredients_version": "1.0.1", "interactions_version": "1.1.2" },
  "review_status": "DEMONSTRATION SET - not clinician-reviewed",
  "model_calls_in_decision_path": 0
}
```

### `GET /readyz` — no auth
`200` when the knowledge base passes its minimum thresholds, `503` otherwise.
```json
{ "ready": true,
  "checks": { "knowledge_base": true, "brand_table": true, "review_status": true },
  "knowledge_base": { "…": "…" } }
```

---

## Medications

### `POST /api/v1/medications/scan`
`multipart/form-data`: `image` (file), `profile_id` (str), `image_type` ∈
`auto|prescription|blister_pack`, `language` (str, optional).

Reads the image and returns candidate rows **without saving anything**. The
image is MIME-sniffed from its bytes, size- and pixel-capped, re-encoded to JPEG
and stripped of EXIF (so GPS tags never leave the phone).

```json
{
  "medications": [ { "brand_name": "Dolo 650", "generic_name": "Paracetamol",
                     "dosage": "650mg", "frequency_raw": "TDS",
                     "frequency_english": "Three times daily",
                     "timing": ["08:00","13:00","20:00"], "confidence": "high",
                     "_confidence_score": 90, "_review_issues": [],
                     "requires_review": false } ],
  "count": 1, "profile_id": "…", "detected_type": "prescription",
  "requires_review": false,
  "image": { "width": 1600, "height": 1200, "size_kb": 240,
             "metadata_stripped": true, "notes": [] }
}
```
Errors: `400` unreadable/invalid image, `422` no medicine found, `429` rate limit
(6/min, 120/day), `503` the vision model is unavailable (retry; the user's saved
medicines are unaffected).

### `POST /api/v1/medications/scan-blister`
Same, but always treats the image as a pack/strip.

### `POST /api/v1/medications/confirm`
Saves the rows the user confirmed.
```json
{ "profile_id": "…", "consent_version": "1.0",
  "medications": [ { "brand_name": "Dolo 650", "generic_name": "Paracetamol",
                     "dosage": "650mg", "frequency_raw": "TDS",
                     "timing": ["08:00","13:00","20:00"] } ] }
```
→ `{ "saved": 1, "medication_ids": ["…"] }`

Each row is resolved against the registry at save time, so the stored record
carries `ingredient_ids`, `resolved_by` and `resolution_confidence`. A row whose
ingredients cannot be identified is stored with `resolution_confidence: "none"`
and appears in `unchecked[]` on every subsequent check.

### `POST /api/v1/medications/manual`
Same body as one row of `confirm`'s array (plus `instruction`, `notes`).
→ `{ "saved": true, "medication_id": "…" }`

### `GET /api/v1/profiles/{profile_id}/medications`
Adds an `ingredients[]` array and a `resolution` block
(`resolved_by`, `confidence`, `unresolved`, `unrecognised_labels`) to each record.

### `PATCH /api/v1/profiles/{profile_id}/medications/{med_id}` · `DELETE …`
Allowed fields: `brand_name, generic_name, dosage, frequency_raw,
frequency_english, timing, instruction, notes, status`. Delete is a soft delete
(`status: "inactive"`) so the history survives for the audit trail.

---

## Interactions — the core endpoint

### `GET /api/v1/interactions?profile_id=all&language=en`
```json
{
  "interactions": [ {
      "id": "dose_ceiling:dup_paracetamol:<profile>:paracetamol",
      "kind": "dose_ceiling",              // interaction | duplicate_ingredient | dose_ceiling | duplicate_therapy | advisory
      "severity": "major",                  // contraindicated | major | moderate | minor | info
      "title": "Same medicine in 2 products: Paracetamol",
      "detail": "2 of these products contain Paracetamol, so the doses add up:
                 Combiflam 325 mg; Dolo 650 650 mg. That is 975 mg per dose-time.
                 Taken as prescribed - 2600 mg a day - that is below the 3000 mg
                 daily limit for older adults, but the same medicine is arriving
                 from more than one tablet. Ceiling used for this check: 3000 mg
                 per day (older adults).",
      "action": "Check the full list with a pharmacist…",
      "source": "US FDA approved product labelling (Section 7 Drug Interactions)",
      "citation": "Acetaminophen: maximum daily dose 4 g; lower limits advised…",
      "rule_id": "dup_paracetamol",
      "profile_id": "…", "patient_name": "Dad",
      "medications": ["Combiflam", "Dolo 650"],
      "ingredients": ["paracetamol"],
      "med_ids": ["…", "…"],
      "conflicting_pairs": [ { "med_ids": ["…","…"], "ingredients": ["paracetamol"] } ],
      "time_gap_hours": 0,
      "acknowledged": false,
      "explanation": "…", "what_to_do": "…",           // client compatibility
      "med_a_name": "Combiflam", "med_b_name": "Dolo 650"
  } ],
  "count": 1,
  "critical_count": 1,
  "unchecked": [ { "med_id": "…", "brand_name": "handwritten squiggle",
                   "reason": "ingredients_not_identified",
                   "unresolved_parts": ["handwritten squiggle"] } ],
  "coverage": { "medications_total": 3, "medications_fully_checked": 2,
                "medications_unchecked": 1, "coverage_percent": 66.7,
                "is_complete": false,
                "patients": [ { "profile_id": "…", "patient_name": "Dad",
                                "medication_count": 3, "checked_count": 2,
                                "unchecked_count": 1, "alert_count": 1,
                                "critical_count": 1 } ] },
  "knowledge_base": { "…": "…" },
  "review_status": "DEMONSTRATION SET - not clinician-reviewed",
  "explanation_language": "en",
  "profile_id": "all",
  "decision_engine": "deterministic",
  "model_calls_in_decision_path": 0
}
```
With no medications: `count: 0`, `coverage.coverage_percent: 0.0`,
`is_complete: false`, and `message: "No medications found. Scan or add your
medicines first."` — never a reassurance.

Notes on an alert:

* `medications[]` lists **only the products that carry the interacting
  ingredients**. A thyroid tablet that happens to be in the same medicine list is
  not named on a bleeding-risk alert.
* `ingredients[]` is the union of the two mechanism groups that triggered the
  rule, and the alert is keyed by the group pair rather than by the ingredient
  pair. Warfarin + ibuprofen and warfarin + aspirin are therefore one alert
  listing all the products involved, not two near-identical ones.
* `time_gap_hours` is a scheduling constraint, not advice text. Where the rule's
  own citation names an interval (the aspirin/NSAID rule says 8 hours) the rule
  declares `min_gap_hours` and that number is what the solver and the verifier
  enforce; otherwise the severity default applies (major 6 h, moderate 2 h).
* `rule_id` names the rule in `backend/knowledge/interactions.json`, where the
  mechanism, the management advice, the source and the citation live.

`language` only rephrases `title`/`detail` through the model, and only if the
result passes validation (no safety claims, no dropped medicine names, no new
dose instructions). `severity`, `source`, `citation` and the finding set are
never model-generated.

### `POST /api/v1/interactions/{alert_id}/acknowledge`
Persists the dismissal; subsequent checks return `acknowledged: true` for that id.
`DELETE` reverses it. Alert ids are stable across devices and runs.

---

## Schedule

### `POST /api/v1/schedule/generate?profile_id=all`
Builds one schedule per patient with a deterministic solver, then **re-verifies
the finished schedule with an independent checker** before returning it.

```json
{ "schedules": [ {
    "profile_id": "…", "patient_name": "Dad",
    "dose_times": [ { "time": "08:00", "label": "Morning",
                      "medications": [ { "med_id": "…", "med_name": "Warfarin 5mg",
                                         "dose": "5mg", "instruction": "",
                                         "interaction_warning": "…" } ] } ],
    "unscheduled": [], "conflicts": [],
    "safety_notes": ["…"],
    "schedule_status": "verified",            // verified | partial | unsafe_conflict
    "verification": { "verified_against_rules": true, "violations": [],
                      "rules_checked": ["ddi_warfarin_nsaid"],
                      "checked_alerts": 1, "checked_at_slots": 3,
                      "knowledge_version": { "ingredients": "1.0.0",
                                             "interactions": "1.1.3" } },
    "generated_by": "deterministic_solver",
    "knowledge_base": { "…": "…" },
    "review_status": "DEMONSTRATION SET - not clinician-reviewed",
    "unchecked_medications": [],
    "as_needed": [ { "med_id": "…", "med_name": "…", "dose": "…",
                     "note": "Taken only when needed, so it has no reminder times." } ],
    "tapers": [ { "med_id": "…", "med_name": "…",
                  "steps": [ { "step": 1, "dose": "40mg", "duration": "3 days",
                               "timing": ["08:00"], "instruction": "" } ],
                  "note": "…" } ],
    "schedule_kind_notes": [],
    "awaiting_confirmation": [                   // held out of the timetable
      { "med_id": "…", "med_name": "…", "reason": "ingredients_not_identified",
        "note": "We are not sure we read this medicine correctly, so it has no reminder times yet. Please check it." } ] } ],
  "schedule": { "…": "…" },                    // present only for a single patient
  "interactions": [], "interaction_count": 0, "critical_count": 0,
  "unchecked": [], "coverage": { "…": "…" },
  "model_calls_in_decision_path": 0 }
```
A medication may carry `schedule_kind`: `fixed` (same dose every day, the
default), `taper` (a reducing course, with the stated steps in `taper_steps`) or
`prn` (only when needed). A `prn` medicine gets **no** dose times and appears in
`as_needed`. A taper keeps every stated step in `tapers`; the timetable shows the
current step only, and no step is invented or merged. An unrecognised value falls
back to `fixed` and is disclosed in `schedule_kind_notes`.

`awaiting_confirmation` lists medicines read with low confidence
(`resolution_confidence` of `low`/`none`, or a `confidence` of `low` from the
reader). They get no dose times until a person confirms the identity with
`POST /api/v1/profiles/{profile_id}/medications/{med_id}/confirm-identity`,
which stores `identity_confirmed` with who and when. Confidence is never shown
as a percentage: the honest phrasing is "not sure, please check".

`partial` means some prescribed doses could not be placed while honouring every
required gap, or a medicine is awaiting confirmation — the client shows which, and says so. `unsafe_conflict` means the
verifier disagreed with the builder; the schedule must not be presented as safe.

---

## Family and SOS

### `GET /api/v1/family` · `POST /api/v1/family` · `DELETE /api/v1/family/{member_id}`
`POST` body: `{ "name": "Anita", "phone": "+91…", "fcm_token": "…",
"relationship": "daughter" }`. A contact with no `fcm_token` is stored as
`pending_invite` and reported as unreachable rather than counted as deliverable.

### `POST /api/v1/sos/alert`
Body: `{ "alert_id": "<id from GET /interactions>", "patient_name": "Dad" }`.

```json
{ "sent_to": 1, "failed": 0,
  "skipped": [ { "member_id": "…", "name": "Son", "reason": "no_device_linked" } ],
  "total_family_members": 2, "alert_id": "…",
  "message": "Alert delivered to 1 of 2 family contact(s). 1 skipped." }
```
Errors: `404` the alert id does not correspond to a finding the API persisted
(open the Interactions screen and try again); `409` no contacts, none reachable,
or a contact is inside the per-contact cooldown window (`sos_rate_limit_hours`,
default 2); `429` rate limit (2/min sender, 4/min outbound).

---

## Devices

### `POST /api/v1/devices/register`
`{ "fcm_token": "…", "platform": "android" }` → `{ "registered": true }`.
Writes `users/{uid}/devices/{sha256(token)}` **and** an indexed
`device_index/{sha256(token)}` record. The index is what makes notifications work
without scanning every user's devices — the previous `collection_group("devices")`
query crossed tenant boundaries.

---

## Account, consent, privacy

| Endpoint | Purpose |
|---|---|
| `GET/POST /api/v1/users/settings` | language, `sos_rate_limit_hours`, preferred contact |
| `GET /api/v1/users/settings` → `consent` | the currently recorded consent decision |
| `POST /api/v1/users/consent` | `{ "consent_version": "1.0", "accepted": true, "purposes": ["medication_safety"] }`; every decision is appended to `consent_history` and audited (§ DPDP: consent must be demonstrable) |
| `GET /api/v1/users/export` | everything held about the account: profiles, medications, settings, audit trail |
| `DELETE /api/v1/users/data?confirm=DELETE_MY_DATA` | erases health data; the audit record of the deletion survives (it holds no health data) |

Profiles: `GET /api/v1/profiles`, `POST /api/v1/profiles`
(`{ "name": "Dad", "age_band": "70-79" }`), `DELETE /api/v1/profiles/{id}`.

---

## Error shape

```json
{ "detail": "A sentence written for the user, not for a log file." }
```
`401` unauthenticated · `403` forbidden · `404` unknown profile or alert ·
`409` a state conflict the user can act on · `422` validation / nothing found ·
`429` rate limited (with `Retry-After`) · `503` a dependency is down.

## Rate limits (per user)

| Key | Per minute | Per day |
|---|---|---|
| `scan` | 6 | 120 |
| `confirm` | 12 | 300 |
| `interactions` | 12 | 300 |
| `schedule` | 6 | 120 |
| `sos` | 2 | 20 |
| `sos_outbound` | 4 | 60 |
