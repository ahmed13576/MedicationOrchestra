# Security, privacy and the audit trail

Prescription images and medicine lists are the most sensitive category of
personal data this product touches. This document states what is actually
implemented, with the file that implements it, and what is still missing.

---

## 1. Authentication and authorisation

* **Firebase ID tokens only.** `services/auth_service.py` verifies every request
  with `firebase_admin.auth.verify_id_token`; expired, revoked and malformed
  tokens each get an explicit `401` with a user-facing sentence.
* **No developer bypass in a deployed service.** `DEV_AUTH_BYPASS=true` is
  honoured only when `K_SERVICE` is unset (i.e. never on Cloud Run) and logs a
  warning on every bypassed request. Covered by
  `tests/test_api_contract.py::test_dev_bypass_is_disabled_in_a_managed_runtime`.
* **Tenant scoping is structural.** Every read and write goes through
  `users/{uid}/…`, where `uid` comes from the verified token — never from the
  request body. `::test_one_users_data_is_never_visible_to_another` proves it.
* **No cross-tenant queries.** The old SOS path used
  `collection_group("devices")`, which reads every user's devices and then wrote
  into another user's document. It is replaced by a `device_index/{sha256(token)}`
  lookup. `tests/fakes.py` raises `AssertionError` if `collection_group()` is
  ever called, and the API tests spy on it.
* **Firestore rules deny client writes.** `firestore.rules` allows a client to
  read its own display data and nothing else: not the audit trail
  (`users/{uid}/audit`), not `device_index`, and no writes anywhere. Clinical
  writes must go through the backend, which resolves ingredient identity,
  validates the fields, writes the audit entry and invalidates the cached
  answer. This matters: a client that could write `medications` directly could
  delete the drug that raised the warning.

## 2. Input handling

| Concern | Implementation |
|---|---|
| Image type spoofing | `services/image_service.py::sniff_mime` reads magic bytes (incl. ISO-BMFF brands for HEIC/HEIF/AVIF and WEBP); the client's declared type is ignored |
| Upload size / decompression bombs | 10 MB cap, 40 MP guard, Pillow `verify()` before decode |
| Metadata leakage (GPS in EXIF) | every image is re-encoded to JPEG, which drops EXIF; asserted by `test_metadata_is_stripped_from_prepared_images` |
| Prompt injection from a prescription | `agents/agent_security.py::sanitize_text_input` neutralises template markers, fences and control characters, and strips instruction phrases **without deleting the medicine name** — the previous version returned `""`, silently removing a real drug from the list |
| Unbounded input | pydantic field limits on every payload; `_clean_fields()` caps string lengths and array sizes |
| Cost abuse | per-user rate limits on every model-backed endpoint (`services/rate_limit.py`) |
| Request bodies parsed before auth (CVE-2024-53981) | `python-multipart` pinned to `0.0.32`; `pip-audit --strict` runs in CI |

## 3. The model cannot change a clinical finding

`services/explanation_service.py` is the only place a language model is used, and
only to rephrase text that the deterministic engine already produced. Every
rewrite is validated before it is used:

* rejected if it asserts safety ("safe to take together", "no need to worry");
* rejected if it drops every medicine name from the finding;
* rejected if it softens the severity of a `major`/`contraindicated` finding;
* rejected if it introduces a dose instruction that was not in the source text;
* the finding set, severities, citations and ids are **never** taken from the
  model; if the model fails for any reason, the curated text is used and the
  response is unchanged.

`test_hardening.py` covers each rule; `INV-7` proves the findings are byte-for-byte
identical with the model unreachable.

## 4. Audit trail

`services/audit_service.py` appends an event for every access to health data:
`medication.scan`, `medication.created/updated/deleted`, `interaction.checked`,
`schedule.generated`, `alert.acknowledged`, `sos.sent`, `device.registered`,
`family.changed`, `data.exported`, `data.deleted`, `consent.recorded`.

* Written to `users/{uid}/audit/{uuid}`, which is closed to clients by the
  security rules and readable only through the authenticated export endpoint.
* `record()` never raises: a failing audit write must not break a clinical
  request, but it logs at `ERROR` so it cannot pass unnoticed.
* The deletion event survives the deletion (it contains no health data), because
  "we deleted it" has to be demonstrable.

## 5. Consent and data-subject rights (DPDP Act 2023)

| DPDP requirement | Implementation | Status |
|---|---|---|
| Consent must be free, specific, informed, unambiguous and **demonstrable** | `POST /api/v1/users/consent` records `{version, accepted, purposes, timestamp}` in `users/{uid}` **and** appends to `consent_history`; audited as `consent.recorded` | implemented |
| Consent withdrawal must be as easy as giving it | the same endpoint with `accepted: false`; the withdrawal is recorded, not just overwritten | implemented |
| Purpose limitation | the `purposes[]` list is stored with the consent; the backend uses health data only for medication safety, explanations and user-requested export | implemented, needs a published privacy notice |
| Right to access | `GET /api/v1/users/export` returns profiles, medicines, settings and the audit trail as one JSON document | implemented |
| Right to erasure | `DELETE /api/v1/users/data?confirm=DELETE_MY_DATA`, confirmed explicitly so a stray tap cannot erase a history; erasure is audited | implemented |
| Data minimisation | images are processed and discarded, never stored; only extracted text is kept | implemented |
| Breach notification | not implemented | **open** |
| Published privacy notice + grievance officer | not implemented | **open** |
| Consent manager integration (DPDP Rules phase, 2026-11) | not started | **open** |

Timeline: the DPDP Rules were notified on 2025-11-13. Board obligations apply
from 2025-11-13, consent-manager provisions from 2026-11-13, and the full
compliance regime (including penalties) from 2027-05-13. The three open items
above are the ones that must be closed before onboarding any paying household.

## 6. Secrets and supply chain

* No API keys anywhere: Vertex AI and Firestore use Application Default
  Credentials; a local `service-account.json` is gitignored and excluded from the
  image by `.dockerignore`.
* Dependencies are pinned exactly (`backend/requirements.txt`), and
  `requirements-dev.txt` carries the tooling. CI runs `pip-audit --strict`.
* The container runs as a non-root user and is built without the test directory.
  CI asserts that no secret file is present in the image.
* No GCP project id appears in the backend, the scripts or the deployment config
  (`test_no_project_id_outside_the_client_firebase_config`). The previous tree
  hardcoded one in eight files. The three exceptions are the client's generated
  Firebase configuration — `medication_orchestra/firebase.json`,
  `lib/firebase_options.dart` and `android/app/google-services.json` — which name
  the **demo** project and must be regenerated with `flutterfire configure`
  before any real deployment (docs/HANDOFF.md, task S-1).

## 7. Emergency-path specifics (SOS)

The SOS endpoint is the highest-consequence path in the product, and the
previous implementation could not work at all: the client posted
`interaction_id`, which the payload model did not accept, and the backend looked
up a document that was never written. Now:

* the alert is identified by an id the API itself persisted during the check, and
  an id it does not recognise returns `404` with an explanation;
* the notification payload carries no diagnosis beyond the medicine names and the
  curated action text;
* delivery is reported per recipient (`sent`, `invalid_token`,
  `temporarily_unavailable`, `no_device_linked`, `rate_limited`) — an emergency
  alert is not a place for optimistic UI;
* per-contact cooldown (`sos_rate_limit_hours`, default 2) prevents notification
  fatigue, and the sender is rate-limited separately.

## 8. Known gaps

1. No published privacy notice, no grievance officer, no breach-notification runbook.
2. No penetration test; no third-party security review.
3. Cloud Run ingress is not restricted to the load balancer in `deploy.ps1`
   (`--allow-unauthenticated` with auth enforced in-app); an IAP or
   ingress-restricted deployment is the better shape.
4. Rate limiting is in-process, so it resets on a cold start and does not span
   instances. Acceptable for cost control now; move to a shared counter
   (Firestore/Memorystore) before scaling.
5. The knowledge base has not been clinically reviewed — see
   `docs/KNOWLEDGE_SOURCES.md`. This is a safety gap, not just a compliance one.
