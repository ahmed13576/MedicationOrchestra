# Medication Orchestra → a YC-Shaped Company

*Companion to `docs/ADVERSARIAL_REVIEW.md`. That document is about what is broken. This one is about
what to build, for whom, at what price, and in what order — assuming you want a real company and not
a certificate.*

---

## 0. The one-line reframe

> **You are not selling "AI that reads prescriptions". You are selling verified medication-safety
> cover for Indian families managing a parent on five or more drugs — with a human pharmacist
> accountable for the answer.**

The OCR is the easy part and it is commoditised. The reason this can be a company is that the
*verification* — normalising 50,000 Indian brand names to salts and strengths, catching the salt that
two different strips share, checking the arithmetic of daily dose ceilings, and having a licensed
human sign the alert — is unglamorous, non-obvious, and compounding. Google can read the label. It
will not staff a pharmacist queue or chase CDSCO label updates for Indian brands.

**Pitch, for the application form:**

> 167 million Indians are over 60, 49% of them are on five or more medications, and the country has
> ~350 geriatricians for all of them. Most prescriptions are never reconciled against each other,
> because there is no unified record and no pharmacist at the chemist counter who has time. We are the
> medication safety layer for the family caregiver: photograph the strips, and we tell you — with a
> pharmacist in the loop — what is dangerous, what is duplicated, and what to ask the doctor. Our
> wedge is the verified Indian brand→salt→strength graph that no one else is building.

---

## 1. Why the current build would not clear a YC screen (and it isn't the code quality)

1. **The demo is not deterministic.** A partner would photograph a strip, see a green checkmark, and
   ask "how do you know?" The honest answer today is "we don't — the corpus is missing from the image."
   Fixing F-01 through F-05 in the review turns this into the strongest part of the pitch.
2. **No buyer.** "Elderly patients in India" is a user, not a buyer. The person with the credit card is
   a 38–55-year-old adult child (metro or NRI) who already pays ₹2,000–11,000/month for elder care.
3. **No measured error rate.** For safety software, "we have no false negatives on a 200-pair,
   clinician-reviewed gold set" is a *product feature*. Today there is no gold set.
4. **No recurring reason to open the app.** A one-shot interaction check has no retention. The loop
   must be daily: dose adherence, refill/expiry, "Mummy skipped her morning BP tablet", monthly
   pharmacist-reviewed report for the next doctor visit.
5. **Legal exposure is uncapped.** A green "safe to take together" banner, no consent, no ToS, no
   clinical reviewer, on a corpus used without a licence (§4). Fixable, but it must be fixed *before*
   the first paying user, not after.

---

## 2. Market: the numbers you can defend in a partner meeting

| Fact | Value | Source |
|---|---|---|
| Indians aged 60+ today | **~167 million** (12% of population), projected 319–347 M by 2050 | ASLI/JLL, Sept 2026; UNFPA |
| Geriatricians available | **~300–350 for 150 M+ elderly** | Frontline, Apr 2026 |
| Polypharmacy (≥5 drugs) in older Indians | **49% pooled** (33.7% in urban community cohorts; up to 70% in some settings) | Frontiers in Pharmacology meta-analysis; *Scientific Reports* 2025 |
| Potentially inappropriate medications | **~28%** of older Indian adults | Frontiers meta-analysis |
| Concurrent traditional + allopathic medicine use | **27.3%** (Ayurveda/Unani/Siddha/Homeopathy) | *Scientific Reports* 2025 |
| Self-medication among older urban adults | **19.7%** | *Scientific Reports* 2025 |
| Elder-care subscription pricing already accepted in the market | **₹1,999–10,999/month** (Emoha tiers); ₹990–4,900/year for lighter plans | Emoha plans, 2026 |
| India senior-care market | **~$7 B and growing** | ThePrint, 2025 |
| Regulated digital-health rails | **70+ crore ABHA IDs, 100+ crore linked health records, 4 lakh+ registered facilities** (2026); ABDM milestone certification is now a prerequisite for AB-PMJAY claims and Bima Sugam cashless | NHA/ABDM, 2026 |

**Why now (the "why now" slide).**

1. **DPDP Act is now on a clock**: Rules notified 13 Nov 2025; Consent Managers go live 13 Nov 2026;
   **full compliance deadline 13 May 2027**. Health data is the highest-sensitivity category, and every
   incumbent elder-care player is now forced to buy or build a consent + audit layer. 2026 is the build
   year — sell them the compliant layer instead of letting them build it badly.
2. **ABDM finally has rails**: HIU/consent flows mean you can, *with patient consent*, pull
   prescriptions and discharge summaries from any linked facility. That kills the cold-start problem
   that used to make this product annoying to set up.
3. **Vision models crossed the handwriting threshold** — and Indian prescription shorthand
   (OD/BD/TDS/HS/"1-0-1") is exactly the kind of domain narrowness a small team can still win on.
4. **UPI Autopay** makes ₹199/month subscriptions frictionless for the adult-child payer.
5. **Demographics on a timer**: the 60+ population roughly doubles by 2050; the caregiving children
   increasingly live in a different city or a different country.

---

## 3. Beachhead: pick one, win it, then expand

| Wedge | Buyer | Why it's attractive | Why it's hard | Verdict |
|---|---|---|---|---|
| **A. D2C caregiver (metro/NRI adult child)** | Adult child | Highest urgency; pays today for elder care; NRI ARPU 5–10× domestic | Consumer CAC is brutal in India; needs a viral loop | **Primary, NRI-first** |
| **B. B2B2C via elder-care platforms** (Emoha, Samarth, KITES, Portea, Apollo Home) | Head of Care / Product | They already sell to exactly your ICP; you become a feature in a ₹2,000–11,000/month bundle; they have care coordinators who can act on alerts | Long-ish sales cycle; concentration risk | **Primary, revenue-first** |
| **C. Post-discharge care transitions (hospitals, insurers, TPAs)** | Discharge desk / medical director / insurer | Readmissions are the payer's loss; AB-PMJAY + Bima Sugam make digital records a requirement; a pharmacist-verified med-reconciliation report is a billable discharge service | Enterprise sales; clinical governance; slow | **Phase 2 (2027)** |
| **D. Chemist point-of-sale** | Pharmacy chains | Directly intercepts the moment of dispensing; builds your brand graph | Counter staff have no time; chains move slowly; you become a POS vendor | **Use for data partnerships, not a sales channel yet** |

**Who you are for (write this on the wall):** *a 45-year-old with a parent on 6–10 chronic
medications, at least two prescribers, at least one chemist-run top-up, and no single doctor who has
seen the whole list.* Two sub-personas:

* **The NRI daughter** (US/UK/Gulf): pays in USD, wants monthly proof and a phone number to call.
  ARPU ₹1,500–4,000/month equivalent. Start here — she is the one who books a ₹6,999 Emoha plan
  without blinking, and she is reachable through NRI communities and Indian-physician networks.
* **The metro son with an ageing parent next door**: ₹199–499/month, price-sensitive, needs the
  WhatsApp experience and Hindi/regional language.

**Jobs to be done (in their words):**
1. "Tell me if the two strips my father is taking together are dangerous."
2. "Tell me he actually took them, without me nagging him 4 times a day."
3. "Give me one page I can put in front of the doctor at the next visit."
4. "If something goes wrong at 2 a.m., have someone to call."

Everything you build should serve one of those four sentences. Today the app serves #1 badly.

---

## 4. Data and moat: what actually compounds

| Asset | Why it compounds | How to get it |
|---|---|---|
| **Indian brand → salt → strength graph** | 50,000+ brands, hundreds of unbranded SKUs, combos change quarterly; every new household teaches the graph something | Seed from CDSCO/mfr label data + open datasets; grow from user-confirmed scans; **pharmacist-reviewed diffs**; version it and publish the version on every answer |
| **Curated interaction knowledge with provenance** | Regulators and hospitals ask "why did you say that?" — the answer must be a citation, a version, and a date | **Licence it (see below)**; layer India-specific rules (Beers/STOPP-START, CDSCO label warnings) on top |
| **Ayurveda / herbal × allopathic interactions** | 27% of older Indians take both; almost nobody has curated this | Partner with an Ayurvedic teaching institution; publish the method; this is a genuine publication-grade contribution and a marketing asset |
| **Human pharmacist network** | Turns "AI said" into "a pharmacist signed"; unlocks B2B contracts and the premium tier | Start with 2–3 part-time clinical pharmacists; make their review the paid tier |
| **Outcome + audit data** | Which alerts led to a change; which changes avoided a hospitalisation — the evidence base no competitor can copy | Every alert carries a case id; follow-up prompts 14 days later ("did your doctor change it?") |
| **ABDM certification (HIU/PHR-app milestones)** | Table stakes for enterprise and government channels | NHA sandbox → milestones → security audit → production credentials (3–6 months) |

### ⚠️ Licensing: the corpus you have today is a liability, not an asset

The 191,543-row `db_drug_interactions.csv` used by `prepare_corpus.py` is a Kaggle mirror of a
DrugBank-derived dataset (TDCommons). DrugBank's own terms state that use and redistribution of its
content requires a licence — free for qualifying academic work, **paid for commercial use**. You are
about to commercialise a product whose entire clinical content sits on that file.

Do this before you take money:

1. **Normalisation layer (free):** DrugBank *Open Data* (CC0 vocabulary: names, synonyms, IDs) and
   public RxNorm/ATC/UNII identifiers to canonicalise ingredient names.
2. **Interaction layer (licensed or open):** NLM's RxNav Drug Interaction API is usable without a
   licence but carries **no severity grading**; so either buy a commercial DDI licence (DrugBank
   Commercial, Elsevier/Stockley's, Micromedex) for the *graded* content, or build severity yourself
   from FDA/CDSCO labels + published criteria, with citations.
3. **Never ship a regex severity classifier as clinical truth** (`prepare_corpus.py:53–96`). Severity
   must be curated, cited, and versioned, with a clinician signing off on the top ~500 pairs.
4. **Publish a data provenance page**: source, licence, version, date, and review status. This is
   simultaneously a compliance artefact and a sales asset ("here is our method").

---

## 5. The product to build: "The Trust Ladder"

Replace today's single-shot check with five rungs. Each rung must be *provable*; the UI must always
show which rung you are on.

```
1. READ      vision extracts brand, salt, strength      -> confidence per field, never silently dropped
2. CONFIRM   user ticks each item (photo + human)       -> "3 of 11 need a closer look"
3. CHECK     deterministic engine over ingredient sets  -> pairwise + duplicate salt + daily ceilings
                                                           + elderly-inappropriate screen
4. EXPLAIN   one batched LLM call, no arithmetic         -> plain Hindi/English/regional copy
5. VERIFY    pharmacist signs high-severity alerts       -> badge + case id + audit trail
             + scheduled review for the next doctor visit
```

**Non-negotiables (these become your engineering culture):**

1. **The safety core is deterministic.** Ingredient sets, ceilings, gaps, and severity come from a
   versioned rules + knowledge base. The LLM never computes, never decides, never writes a safety
   claim. *(Review F-02, F-03.)*
2. **The system fails loud.** Empty corpus, unresolved drug, degraded schedule → the UI says so and
   the headline is never green. *(F-01, F-04.)*
3. **Coverage is a number on screen:** "checked 8 of 11 — 3 unreadable". *(F-04.)*
4. **Patient-scoped by construction.** One check per person; the household view is a caregiver
   dashboard, never a cross-patient pair generator. *(F-06.)*
5. **Every clinical statement is traceable** to (source, version, date, reviewer). *(F-09.)*
6. **Measured error rates**, published internally and (eventually) publicly. *(F-11.)*

**Feature set v2 (in build order):**

| # | Feature | Why it earns its place |
|---|---|---|
| 1 | Verified safety core + coverage ledger + pharmacist-signed alerts | The product. Everything else is packaging |
| 2 | **Adherence loop** — WhatsApp dose reminders, "taken/skipped" reply, adherence streak, escalation to the caregiver after 2 misses | Converts a one-shot check into a daily habit; the actual retention engine |
| 3 | **Refill & expiry radar** — "3 days of Telma left; the strip expires in 4 weeks" with one-tap reorder via a pharmacy partner | Solves the most common failure (running out) and creates affiliate revenue |
| 4 | **Doctor-visit one-pager** — a PDF/WhatsApp card: full list, salts, duplicates, alerts, adherence %, questions to ask | The artefact caregivers print and show; the thing that gets your app recommended by GPs |
| 5 | **Caregiver dashboard** (multi-patient, with per-person alerts) + **phone-number invites** (no token copying) | The household thesis, done correctly; viral loop |
| 6 | **SOS with at-least-once delivery, receipts, and an escalation ladder** (family → care coordinator → ambulance) | Fixes the broken feature; the "peace of mind" headline for NRI buyers |
| 7 | **ABDM (HIU) import** of prescriptions/discharge summaries with consent | Removes data entry; the enterprise unlock |
| 8 | **Hindi/regional UI + WhatsApp-first flows** for the parent, app-optional | The parent will not install an app; the caregiver will |
| 9 | **Pharmacist review tier** (async, 24 h, with a call-back) | Premium pricing, B2B credibility, clinical governance |
| 10 | **Ayurveda/allopathy checker** | Nobody else has it; 27% of your market needs it; press-worthy |

**Delete, without nostalgia:** the household-wide cross-patient pairing; the LLM schedule generator
(replace with a solver); the green "safe" empty state; FCM-token-based family invites; client-side
Firestore writes; `send_dose_reminder` (wire it or delete it); the committed `session.db`; the
GSD/gumloop scaffolding; `docs/token-optimization-guide.md` and friends.

---

## 6. Business model and unit economics (worked, not hand-waved)

**Pricing**

| Tier | Price | For | Contents |
|---|---|---|---|
| Free | ₹0 | Acquisition | 1 profile, 5 meds, unlimited checks, coverage ledger, adherence reminders |
| Family | **₹299/mo** (₹2,499/yr) | Metro caregiver | 4 profiles, refill radar, doctor one-pager, WhatsApp, SOS |
| Family+ Pharmacist | **₹799/mo** | NRI / high-acuity | Everything + pharmacist-reviewed list every quarter + priority call-back |
| Clinic / elder-care partner | **₹150–400 per active elder per month** (volume-tiered) | Emoha-type platforms, clinics, home-nursing | White-label dashboard, care-coordinator seat, API, SLA |

Anchor against what the market already pays: Emoha ₹1,999–10,999/month. Your ₹299–799 sits underneath
as a focused safety module, and partners can bundle you inside their existing plan at 3–4× markup.

**Cost to serve one active household (list prices, Gemini 2.5 Flash, Sept 2026: $0.30/M input,
$2.50/M output; ~₹88/$)**

| Item | Assumption | Cost/month |
|---|---|---|
| Vision scans | 6 pages/mo × ~1,000 image tokens + 600 prompt tokens + 800 output ≈ $0.0025 each | ₹1.3 |
| Interaction explanations (batched) | 8 checks/mo × 1 call (~3k in / 2k out) ≈ $0.007 each | ₹5.0 |
| Grounding for unknown brands | 20 brands/mo × $0.0005 | ₹0.9 |
| Firestore + Cloud Run + FCM | Always-on-ish serverless for a light workload | ₹20–40 |
| WhatsApp Business (utility) | ~40 messages/mo @ ₹0.35 | ₹14 |
| **Technical COGS** | | **≈ ₹45–65** |
| Pharmacist review (Family+ only) | 15 min × ₹150/hr, quarterly → ~₹19/mo amortised | ₹19 (Family+) |

Gross margin: **~80% on Family (₹299)**, ~75% on Family+ (₹799), and ~65–70% on partner seats
(₹200/elder) once you pay for support. NRE/non-Indian inference and storage costs are negligible
against these prices — the constraint is *verification labour*, which is exactly why the pharmacist
tier is priced where it is. Recheck real Vertex bills monthly; the plan in `monitoring_setup.ps1`
measures infrastructure, not cost per household, so add a `cost_per_check` counter.

**Target unit economics (12 months):** CAC ≤ ₹400 blended (NRI: ₹1,200 but ARPU ₹1,500+; partner-sourced:
~₹0), M6 retention ≥ 65% for paying caregivers, LTV/CAC ≥ 3, payback ≤ 6 months.

---

## 7. Distribution: four channels, sequenced

1. **Elder-care platforms (months 0–3).** They own the ICP, the trust, and the care coordinators.
   Lead with the *compliance* angle (DPDP deadline May 2027 + ABDM records) and the *clinical* angle
   (pharmacist-signed medication review). Offer a 60-day paid pilot on 50 elders with a written
   outcome metric (alerts confirmed by clinicians per 100 reviews). Target: 2 signed partners.
2. **NRI caregiver communities (months 1–6).** Indian physician associations, NRI Facebook/WhatsApp
   groups, Telugu/Tamil/Gujarati diaspora communities, r/ABCDesis-style venues. Content that travels:
   "the interaction that put my father in the ICU" (your own story), "why your father's two strips
   contain the same salt", and a monthly *Medication Safety Report for Indian Families*.
3. **Hospitals and insurers (months 6–18).** Post-discharge medication reconciliation is a measurable
   readmission-reduction service; ABDM M2/M3 plus Bima Sugam make digital records a claim-processing
   requirement. Sell as a discharge-desk service, priced per patient, with your report attached to the
   discharge summary.
4. **Doctors and chemists as amplifiers (ongoing).** GPs do not want to reconcile five specialists'
   prescriptions by hand; hand them the one-pager. Chemists get a free "scan the strip" tool that grows
   your brand graph — data, not revenue, is the point.

**The viral loop to instrument:** caregiver invites a sibling → sibling adds a second parent → the
household pays for 4 profiles. Measure invites sent, accept rate, and profiles-per-household.

---

## 8. Compliance and regulatory strategy (do it in this order)

| Layer | Action | Deadline/Trigger |
|---|---|---|
| **Intended use** | Write and freeze it: "medication information, adherence support, and pharmacist-mediated review; not diagnosis, not prescribing, not a substitute for medical advice." Marketing must never say "safe" | Now |
| **Consent & privacy** | DPDP-grade notice, granular consent, purpose limitation, data-principal rights (access/correction/erasure), retention schedule, Consent Manager integration, breach playbook | Consent Managers live **13 Nov 2026**; full compliance **13 May 2027** |
| **Data residency & security** | Move PHI to `asia-south1`; AES-256 at rest, TLS 1.2+ in transit, field-level encryption for identifiers, RBAC, immutable audit logs (who saw what, when, why), VAPT by a CERT-In empanelled vendor | Before paid pilots |
| **Clinical governance** | Clinical advisory board (geriatrician + clinical pharmacist + a medico-legal advisor); documented alert-copy review; an adverse-event/incident process; a red-team exercise each quarter | Before v1 GA |
| **ABDM** | Register as HFR; build HIU consent flows; consider PHR-app milestone certification for the family dashboard | 3–6 months |
| **SaMD / CDSCO** | Stay on the information/adherence side of the line; if you ever surface dosing *recommendations* per patient (e.g. adjust for renal function), get a regulatory opinion first and consider a device classification path | Before any dose-adjustment feature |
| **Certifications** | ISO 27001 + ISO 27701 as the enterprise gate; ISO 13485 only if you go clinician-facing | 12–18 months |
| **Insurance & liability** | Professional indemnity + technology E&O; clear limitation of liability in the ToS; never let the app assert safety | Before first payment |

---

## 9. Metrics that decide whether this is a company

**Safety (report monthly, to the board and eventually the public):**
* False-negative rate on the clinician-reviewed gold set (target **0%** on the "do-not-miss" list)
* Coverage rate (share of a household's medications actually checked; target ≥ 95%)
* Alerts confirmed as correct by a pharmacist; alerts actioned by the user or doctor
* Time from scan to verified answer (target < 60 s without review, < 24 h with)

**Product:**
* Time-to-first-verified-check (< 3 minutes from install)
* Adherence reply rate; 7-day and 30-day adherence per patient
* Profiles per household; invites accepted
* Doctor one-pagers actually shared (the leading indicator of GP word-of-mouth)

**Business:**
* Active paying households; ARPU by channel (NRI vs metro vs partner)
* Partner-seats live and elder-months delivered
* CAC by channel, M6 retention, gross margin after verification labour
* Revenue concentration (no partner > 30% by month 12)

---

## 10. The next 90 days

**Weeks 1–2 — "make it honest" (review §8, P0).**
Ship the corpus in the image with a health-check floor; kill the green empty state; add the coverage
ledger; make degradation visible; wire `scripts/safety_invariant_audit.py` into CI. *Deliverable: a
demo where nothing can silently claim safety.*

**Weeks 3–6 — "make it correct".**
Deterministic scheduler + verifier; ingredient-registry matching; duplicate-salt and daily-ceiling
checks; patient-scoped checks; build the 200-pair clinician-reviewed gold set and the nightly eval.
*Deliverable: a one-page accuracy report signed by a clinical pharmacist.*

**Weeks 7–10 — "make it usable".**
WhatsApp-first flows, adherence loop, refill/expiry radar, phone-number invites, caregiver dashboard,
doctor one-pager, Hindi UI. *Deliverable: a caregiver can onboard her mother in under 3 minutes.*

**Weeks 11–13 — "make it sellable".**
Two paid elder-care pilots; the compliance pack (notice, consent, DPIA, audit log, residency);
pricing live with UPI Autopay; partner dashboard; the first 20 paying households.
*Deliverable: revenue, retention numbers, and two signed pilot references for the YC application.*

**Hiring (in order):** founding engineer (backend + deterministic rules) → clinical pharmacist
(part-time, then full-time) → growth/community (Hindi + English, NRI-fluent) → geriatrician advisor
(equity) → data/ML engineer for the brand graph.

**12-month roadmap:** post-discharge pilot with a hospital or insurer, ABDM HIU live, Ayurveda/
allopathy module published, 10,000 paying households or 25,000 partner seats, ₹1–1.5 crore ARR run-rate,
published safety report v1.

---

## 11. The YC application kit

**One-liner:** *Medication safety cover for India's 167 million seniors — verified by pharmacists, not
guessed by AI.*

**Three-minute demo script (what a partner should see):**
1. *(20 s)* Photograph three real strips, including a combination product. Show the extraction with
   per-field confidence and "2 of 3 confirmed".
2. *(40 s)* The safety screen: one major alert with a *citation*, one duplicate-salt warning
   ("Dolo 650 + Combiflam = 1300 mg paracetamol per dose — above the safe single-dose range for her
   age"), and the coverage ledger ("checked 3 of 3").
3. *(40 s)* The pharmacist badge: tap it to see who signed, when, against which corpus version.
4. *(40 s)* Monday morning: the WhatsApp dose reminder, the "Taken" reply, the escalation to the
   caregiver after two misses.
5. *(30 s)* The doctor one-pager, generated and sent on WhatsApp.
6. *(20 s)* The degraded path — deliberately break the corpus and show the app refusing to say "safe".

**Five numbers to have ready:** number of verified checks delivered; % coverage on a real household;
false-negative rate on the gold set; M6 retention of paying caregivers; ARR and gross margin.

**Questions you will get — and the answers that win:**

| Question | Answer |
|---|---|
| "What if Google/Apple ships this in the camera app?" | They will read labels. They will not staff Indian pharmacists, license Indian brand data, or sign alerts. Our moat is the verified graph + human accountability + ABDM integrations, not OCR. |
| "Why won't users just ask the chemist?" | The chemist has 40 seconds and sells what is in stock. Our own data shows 28% of these patients are on potentially inappropriate medication and 27% mix Ayurveda with allopathy — nobody is checking either. |
| "How do you get data?" | Licensed interaction content + CDSCO label data + a curated brand graph we grow from confirmed scans and pharmacist review; every answer carries a version and a citation. |
| "Isn't this regulated?" | We deliberately sit on the information/adherence side of the line, with a written intended-use statement, a clinical advisory board, and no dosing recommendations. If we add dose adjustment we will take a device-classification opinion first. |
| "How big can this get?" | 167 M seniors → ~80 M polypharmacy households; at ₹299/month and 1% penetration that is ~₹2,900 crore ARR. We do not need 1%: 100,000 paying households is ₹36 crore ARR in India pricing, before partner seats and the NRI corridor at 5× ARPU. |
| "Why you?" | You lived the warfarin/NSAID hospitalisation that this product prevents; you have the Indian prescription vocabulary in the prompt already; and you are willing to build the boring verified-data layer that competitors skip. |
| "Why now?" | DPDP's full-compliance deadline is 13 May 2027 and ABDM crossed 100 crore linked records in 2026 — the trust rails and the distribution rails both appeared in the last 12 months. |
| "What's the biggest risk?" | A false negative that hurts someone. That is why the safety core is deterministic, coverage is always on screen, a pharmacist signs high-severity alerts, and we publish our error rate. |

---

## 12. Risks and mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| A missed interaction harms a patient | Existential | Deterministic core; fail-loud design; 0% FN gate on a curated do-not-miss list; pharmacist review for major/contraindicated; insurance; disclaimers |
| DrugBank/Kaggle licence challenge | High | Replace with licensed + open + self-curated data before monetising; publish provenance |
| Consumer CAC too high in India | High | Lead with B2B2C elder-care partners and the NRI corridor; content-led acquisition; the family invite loop |
| Incumbent (1mg/PharmEasy/Practo/Emoha) copies the feature | Medium | Partner with them early; own the verified graph, the audit trail, and the pharmacist network — the parts that don't copy in a quarter |
| Model costs or latency blow up | Medium | One batched call per check; aggressive explanation caching; deterministic core does the work; per-user budget caps |
| Regulatory reclassification into SaMD | Medium | Frozen intended-use statement; legal opinion before any dosing feature; ISO path staged |
| WhatsApp/platform policy change | Medium | Keep an app + SMS fallback; do not make one channel the product |
| Partner concentration | Medium | Cap any single partner at 30% of revenue by month 12; run D2C in parallel from day one |

---

## 13. Anti-goals (write these down and refuse them)

* **No diagnosis, no prescribing, no dose changes.** Information, adherence, and pharmacist-mediated
  review only.
* **No "AI doctor" chat.** A general chatbot will hallucinate a drug; your value is narrow and verified.
* **No EHR.** Integrate via ABDM; do not become a records system.
* **No marketplace, no pharmacy of your own.** Reorder through partners.
* **No household-wide interaction engine.** Two people, two bodies, two checks.
* **No green "safe" banner.** Ever. "No known issues found in the X medications we could verify" is the
  strongest honest sentence you are allowed to print.

---

## 14. How the remediation maps to the business

| Review finding | Business consequence if unfixed | Where it shows up |
|---|---|---|
| F-01 empty corpus | You are shipping a false all-clear; one incident ends the company | Week 1 |
| F-02 unverified schedule | The core clinical promise is unenforceable; no hospital will contract | Weeks 3–6 |
| F-03 substring matching | Wrong-drug alerts destroy pharmacist and doctor trust on first contact | Weeks 3–6 |
| F-04 silent skips | You cannot state a coverage number, which is the first question a clinician asks | Week 1 |
| F-05 no duplicate-salt check | You miss the most common preventable harm in the ICP | Weeks 3–6 |
| F-06 cross-patient alerts | Alert fatigue in exactly the caregiver you are selling to | Weeks 3–6 |
| F-07 broken SOS | The NRI peace-of-mind promise has no delivery | Weeks 7–10 |
| F-08/F-12 security + drift | Fails partner due diligence; unauthenticated DoS on a public endpoint | Weeks 7–10 |
| F-09 compliance vacuum | Blocks every B2B contract after May 2027 | Weeks 11–13 |
| F-11 no evaluation | You cannot answer "how accurate is it?" — the only question that matters | Weeks 3–6 |

---

## 15. Sources

* ASLI/JLL & Ministry of Social Justice, senior population ≈166.9 M — The New Indian Express, Sept 2026
* UNFPA India, 153 M aged 60+ → 347 M by 2050 — india.unfpa.org
* Geriatrician shortage (~300–350 for 150 M+) — Frontline (The Hindu), Apr 2026
* Polypharmacy 49% pooled; PIM use 28% — Frontiers in Pharmacology meta-analysis (India)
* Polypharmacy 33.7%; 27.3% traditional/complementary use; 19.7% self-medication — *Scientific Reports*, 2025
* Elder-care subscription pricing ₹1,999–10,999/month — emoha.com/plans (2026); ₹7 B senior-care market — ThePrint, 2025
* ABDM: 70+ crore ABHA IDs, 100+ crore linked records, 4 lakh+ facilities; milestone certification prerequisites — NHA/ABDM reporting, 2026
* DPDP Rules notified 13 Nov 2025; Consent Managers 13 Nov 2026; full compliance 13 May 2027 — MeitY/DPDP guides, 2026
* Gemini 2.5 Flash list pricing ($0.30/M in, $2.50/M out; image input $0.30/M) — Google AI pricing / OpenRouter, Sept 2026
* DrugBank licensing (commercial use requires a licence; Open Data is CC0; RxNav carries no severity) — go.drugbank.com/datasets, lhncbc.nlm.nih.gov/RxNav
