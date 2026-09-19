# Clinical knowledge base — provenance, licensing and review status

**Files in scope**

| File | Contents | Version |
|---|---|---|
| `backend/knowledge/ingredients.json` | 195 active ingredients + synonym/`class` data | 1.0.1 |
| `backend/knowledge/interactions.json` | 33 interaction rules (each with mechanism groups and, where the citation names an interval, `min_gap_hours`), 3 advisories, 4 dose ceilings | 1.1.2 |
| `backend/knowledge/brand_mapping.csv` | 175 Indian brand presentations with composition and strength | 1.0.0 |

**Review status of the shipped set: `DEMONSTRATION SET - not clinician-reviewed`.**
This string is stored in the knowledge base, returned by `/health`, `/readyz` and
every interaction response, and rendered in the client. It is not decoration: it
is the honest statement that no clinical pharmacist has signed off on these
rules yet, and it is a release blocker for charging money (see
`docs/YC_PRODUCT_PLAN.md`, Trust Ladder step 5).

---

## 1. Why the previous corpus was removed

The original build populated its interaction database from a Kaggle dataset
(`db_drug_interactions.csv`, 191,808 pairs) and from the Therapeutics Data
Commons DDI dataset. Both derive from **DrugBank**, whose terms permit academic
use (CC BY-NC 4.0 for the academic release) and require a **paid commercial
licence** for any use in a product.

Shipping that data in a commercial product — including in a Docker image, a
Vector Search index, or a fine-tuned model — would have created a licensing
liability over the one asset the business is built on, and the liability would
have surfaced at the worst possible moment (due diligence, or a first enterprise
customer's legal review).

Two further problems with the same corpus:

* it carried **no severity** for most pairs, so "found an interaction" and "this
  could kill you" were indistinguishable to the caller;
* it was **generated at build time into a directory that was then excluded from
  the container** (`backend/.dockerignore`), so the deployed service loaded zero
  records and answered "no interactions found" to everything. That is the
  single worst failure mode this product can have.

Nothing in the shipped knowledge base is imported from that corpus. The
generated files (`backend/data/ddi_processed.json`, `ddi_index.jsonl`) are
gitignored, absent, and no longer read by any code path. CI now fails if the
string `drugbank` appears anywhere in the shipped knowledge files.

## 2. What the rules are drawn from

Each rule carries `source` and `citation` fields, and the registry exposes a
`source` label mapping so a pharmacist can see where a claim came from without
leaving the product.

| Source label | Meaning | Used by |
|---|---|---|
| `FDA_LABEL` | US FDA-approved product labelling, Section 7 "Drug Interactions" | 25 rules |
| `STOCKLEY` | Stockley's Drug Interactions (paraphrased, never reproduced) | 3 rules |
| `NICE` | UK NICE clinical guidance | 2 rules |
| `BEERS` | AGS Beers Criteria (potentially inappropriate medicines in older adults) | 2 rules |
| `BNF`, `STOPP`, `WHO` | available labels, currently unused by any shipped rule | — |

The rules themselves are **original paraphrases written for this product**: the
mechanism, the plain-language management advice and the caregiver action are
authored text, not excerpts. Only the *fact* of the interaction and the
reference it comes from are taken from the source, which is the same footing any
clinical reference tool stands on.

Dose ceilings (`duplicate_ingredient_ceilings`) cite the corresponding product
labelling, for example paracetamol: 4 g/day general, 3 g/day for older adults,
1 g maximum single dose.

## 3. How the data is used (and what must change before it is sold)

Two distinct kinds of data have to be kept separate:

1. **Vocabulary** — ingredient names, synonyms, brand-to-composition mapping.
   The open DrugBank vocabulary release is CC0 and can be used commercially;
   RxNorm (US NLM) is public domain; UNII (FDA) and ATC/WHO are open. The
   intended import path is `ingredients[].external_ids`, which is currently
   **empty for every ingredient** on purpose: no identifier has been imported
   yet, and populating it is the work that turns this from a hand-curated list
   into a maintained vocabulary.
2. **Clinical severity** — what the interaction means and how serious it is.
   This is where a DrugBank licence would have been required. It is instead
   curated per rule, cited, versioned, and must be signed off by a licensed
   clinical pharmacist before the set is used for paid clinical decisions.

### Rule structure: mechanism groups and required intervals

Every rule in `interactions.json` declares:

| Field | Required | Meaning |
|---|---|---|
| `groups` | yes (≥ 2) | The mechanism groups the rule connects. Each group is a list of ingredient ids that are interchangeable for this interaction. An alert fires only when the patient's ingredients span **two different groups**, so a rule listing six NSAIDs does not fire on a patient taking two of them, and no pair from the same group is ever indexed. |
| `ingredients` | yes | The union of `groups`; kept so the data reads as a flat list, and validated at load time to match the groups exactly. |
| `min_gap_hours` | no | Set **only** where the cited advice names a specific interval (aspirin/NSAID 8 h, levothyroxine/iron-calcium 4 h, fluoroquinolone/cations 6 h). The solver and the verifier enforce this number; otherwise the severity default (major 6 h, moderate 2 h) applies. |
| `severity`, `title`, `mechanism`, `management`, `source`, `citation` | yes | Severity drives ordering; the rest is what the user reads. |

A rule whose groups overlap, or which lists an ingredient outside its groups,
raises `KnowledgeBaseError` and the service refuses to start — a mis-grouped rule
would either silence an interaction or fire it on the wrong patients.

## Import plan (tracked as work, not aspiration)

| Step | Source | Licence | Status |
|---|---|---|---|
| Ingredient vocabulary + UNII | FDA UNII / RxNorm | public domain | not started |
| Brand → ingredient mapping | product labelling (INDIA: CDSCO label text), curated | original compilation | 175 presentations in, needs review |
| Interaction rules | FDA labelling + primary literature, each cited | facts, original phrasing | 33 rules in, needs pharmacist review |
| Severity assignment | the cited labelling + Beers/STOPP for the elderly | facts | in, needs pharmacist review |
| Coverage benchmark | RxNav Interaction API (free, no severity) as a *recall* cross-check | NLM terms; non-commercial DrugBank provenance upstream | not started |

The RxNav cross-check is a good example of using a free source correctly: it can
tell us which pairs our rule set *misses*, but its severity data must never be
shipped, so it is a development-time tool only.

## 4. How this is enforced

* `backend/tests/test_hardening.py::test_knowledge_base_ships_no_drugbank_data`
  fails if `drugbank` appears in the shipped knowledge, or if any ingredient
  carries unverified `external_ids`.
* `::test_every_referenced_ingredient_exists_in_the_registry` — a rule that names
  an ingredient the registry cannot produce can never fire, and would be false
  reassurance.
* `::test_every_rule_is_cited_and_has_a_title`, `::test_rule_ids_are_unique`.
* `::test_no_corpus_files_are_tracked_in_git` — no dataset files in the repo.
* CI step "Clinical knowledge base integrity" re-checks all of the above, plus
  that every rule has at least two distinct ingredients.
* `scripts/safety_invariant_audit.py` INV-1 and INV-3 assert that the loaded
  knowledge base is real (>50 ingredients, >10 rules) and that identity is exact.

## 5. Change process

1. Edit the knowledge file; bump its `version` and add a `changelog` entry.
2. Run `python scripts/safety_invariant_audit.py` and
   `cd backend && python -m pytest tests/ -q`.
3. Any clinical change additionally needs a pharmacist's name in `reviewed_by`
   and a date in `reviewed_on` (both currently `null`).
4. Bump the version: every cached answer carries the knowledge-base versions it
   was computed from, so a corrected rule invalidates the old results
   automatically.
