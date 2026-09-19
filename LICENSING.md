# Licensing

Medication Orchestra is **open-core**: the part that decides what is safe is
open source, the product around it is not.

## The open part — Apache-2.0

| Path | What it is |
|---|---|
| `backend/services/clinical_engine.py` | the deterministic engine: interaction checks, duplicate ingredients, dose ceilings, allergies, separation gaps, scheduling and the verifier |
| `backend/knowledge/` | the knowledge base: ingredients, interaction rules with citations, brand mapping |
| `scripts/safety_invariant_audit.py` | the 43 safety invariants that must hold on every change |
| `backend/tests/test_clinical_engine.py` | the tests that pin the engine's behaviour |

Full text: `LICENSES/Apache-2.0.txt`.

**Why these and not others.** A rule that says "do not give this nitrate within
48 hours of that PDE5 inhibitor" is a clinical claim. A claim nobody can read
cannot be checked by a pharmacist, corrected by a clinician, or trusted by a
household. Publishing the engine, the rules, their citations and the invariants
that guard them is how the safety argument is made in public. Apache-2.0 also
carries an explicit patent grant and requires attribution, so contributions and
reuse are both safe.

## The closed part — all rights reserved

The API service (`backend/main.py`, the services around the engine), the mobile
client (`medication_orchestra/`), the deployment scripts and the product
documentation are proprietary. They are the product.

## If you want to use the engine

You may, under Apache-2.0: keep the copyright and licence notice, state your
changes, and accept that there is no warranty. Two things to understand first:

1. **The knowledge base is a demonstration set.** It has not been reviewed by a
   clinician (`docs/KNOWLEDGE_SOURCES.md` says so, and the API reports
   `review_status` on every response). Do not ship it to patients without a
   qualified review.
2. **It is not a medical device.** Using it in a product that diagnoses, treats
   or advises may put you under medical-device regulation in your country. That
   is your responsibility, not ours.

## Contributing

Contributions to the open part are accepted under Apache-2.0. A rule change must
arrive with its citation and a test; a rule without a source is not merged.
