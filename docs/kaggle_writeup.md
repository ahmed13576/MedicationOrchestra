# Medication Orchestra: A Household-Level, Multi-Agent Guardian for Geriatric Medication Safety

![Medication Orchestra Cover Image](file:///c:/Users/moham/Documents/MedicationOrchestrationAgent/docs/kaggle_thumbnail.png)

**Track:** Concierge Agents
**Public Repository:** [GitHub Repository](https://github.com/mohamed/MedicationOrchestrationAgent)

---

## 1. Executive Summary & The Personal Story

In many developing countries, particularly India, elderly patients routinely manage multiple chronic conditions (co-morbidities) like diabetes, hypertension, and heart disease. To treat these, they visit independent specialists—a cardiologist, a diabetologist, and a general physician—often at different clinics or hospitals. 

Because doctor consultations in high-load Indian clinics are hurried (averaging under 5 minutes), and there is **no unified Electronic Health Record (EHR)** system, doctors only see the medications they prescribe. The patient is the sole link carrying this information between clinics, often relying on memory or a disheveled bag of paper prescriptions.

This fragmentation creates a silent, deadly hazard: **Drug-Drug Interactions (DDIs)**. 

### The Spark Behind Medication Orchestra
The inspiration for this project comes from a first-hand household crisis. Several of my elderly relatives consult multiple doctors. During a recent routine consultation, a specialist, hurried by a long queue of patients, prescribed a common NSAID (Brufen/Ibuprofen) for knee pain. They did not know that my relative was already taking a prescribed blood thinner (Warfarin) from another doctor. 

No one checked the interaction. Within days, this combination caused severe bleeding, leading to an emergency hospitalization. In an over-stretched healthcare system, hurried doctor visits and fragmented pharmacy shops fail to catch these conflicts. 

We built **Medication Orchestra** to be the safety net that catches these errors *before* the first dose is taken. By simply taking a photo of a prescription or medicine pack, a team of cooperative AI agents reads the text, cross-references it against a household-wide drug database, alerts family members of dangerous interactions, and generates a safe, staggered medication schedule.

---

## 2. The DDI Crisis: Global & Local Reality

Our solution is built on a foundation of verifiable healthcare statistics:

1. **Adverse Drug Reactions (ADRs)** are estimated to be between the **4th and 6th leading cause of death worldwide**, ranking alongside heart disease, stroke, and cancer (*PubMed, 2023*).
2. Geriatric patients on polypharmacy (5+ concurrent medications) face a **double risk of hospitalization** (Odds Ratio 2.66, *Scientific Reports, 2024*).
3. In hospital cohorts, major DDIs are identified in **16.41% of geriatric prescriptions**, directly correlating with prolonged hospitalizations and severe complications (*European Journal of CV Medicine, 2025*).
4. **The India-Specific Iceberg:** While the global average rate of reporting adverse reactions is around 5%, India’s reporting rate is **under 1%** (*JPHI India, 2025*). The lack of integrated dispensing databases at local pharmacies means there is no automated line of defense at the point of sale.

Medication Orchestra bridges this gap directly at the household level. It aggregates medications across multiple family members, providing caregivers with a single dashboard to check and schedule drugs safely.

---

## 3. Tech Stack & Multi-Agent Architecture (ADK)

Medication Orchestra uses a modern, distributed stack combining a cross-platform client app, a containerized serverless backend, and a cooperative team of AI agents.

```
┌─────────────────────────────────────────────────────────────┐
│                    FLUTTER APP (Client)                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │
│  │ Camera   │ │ Med List │ │ Interact │ │ Emergency    │  │
│  │ Screen   │ │ Screen   │ │ Alerts   │ │ SOS Button   │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬───────┘  │
│       │            │            │               │           │
│       ▼            ▼            ▼               ▼           │
│  ┌─────────────────────────────────────────────────────┐   │
│  │           Firebase Auth + Firestore (Local)         │   │
│  └─────────────────────┬───────────────────────────────┘   │
└────────────────────────┼───────────────────────────────────┘
                         │ HTTPS
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              CLOUD RUN (Backend API)                         │
│  ┌──────────────────────────────────────────────────────┐   │
│  │           GOOGLE ADK AGENT ORCHESTRATOR              │   │
│  │                                                       │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │   │
│  │  │ Intake   │→ │ Interact │→ │ Schedule & Alert │  │   │
│  │  │ Agent    │  │ Checker  │  │ Agent            │  │   │
│  │  └──────────┘  └──────────┘  └──────────────────┘  │   │
│  └──────────────────────────────────────────────────────┘   │
│         │                    │                               │
│         ▼                    ▼                               │
│  ┌──────────────┐  ┌──────────────────────┐                 │
│  │ Gemini API   │  │ Vertex AI Vector     │                 │
│  │ (Vision +    │  │ Search               │                 │
│  │  Text)       │  │ (DDI RAG Corpus)     │                 │
│  └──────────────┘  └──────────────────────┘                 │
└─────────────────────────────────────────────────────────────┘
```

The core backend intelligence is implemented using the **Google Agent Development Kit (ADK)**, coordinating four specialist agents:

1. **Intake Agent (Gemini 2.5 Flash Vision):** Extracts drug names, dosages, and frequencies from prescription photos. It understands common abbreviations (e.g., OD, BD, TDS, QID) and maps them to standard schedules.
2. **Search Grounding Agent (Google Search Grounding):** Translates regional Indian brand names (e.g., "Combiflam", "Dolo 650", "Ecosprin") into their correct active generics (Ibuprofen + Paracetamol, Aspirin) using Google Search.
3. **Interaction Checker Agent (Vertex AI RAG):** Takes the generic names and queries a vector database containing a structured corpus of over 20,000 drug interaction pairs. It flags conflicts, categorizes their severity (Critical, Major, Moderate, Minor), and provides clinical explanations.
4. **Schedule & Alert Agent (ADK Orchestrator + FCM):** Analyzes the verified medications and generates a safe schedule. If two drugs conflict but are both necessary, the agent calculates a safe staggering gap (e.g., minimum 6-hour gap) and schedules alerts. It also triggers instant push notifications (SOS) to family members when an adverse interaction is logged.

---

## 4. Rigorous Security & Prompt Injection Mitigation

Concierge agents handle highly sensitive personal health information. Ensuring safety and preventing manipulation of the agentic pipeline was a top priority, achieved through a 3-layer security system:

### A. Input Sanitization (`sanitize_drug_input`)
All user-derived strings (OCR outputs or manual inputs) are passed through a strict validator before being injected into LLM prompts. 
- **Strict Character Constraints:** Only letters, digits, and standard medical notation characters are allowed; all symbols typically used in prompt injections (e.g., `<|`, `]]`, `{{`, `---`, `\n`) are stripped.
- **Trigger Word Filtering:** Inputs are evaluated against a blocklist of injection trigger phrases (e.g., "ignore", "override", "system prompt", "jailbreak"). If detected, the input is immediately discarded.
- **Length Bounds:** Strict character limits restrict the payload size to prevent buffer or token injection overruns.

### B. Output Schema Validation (`validate_agent_output`)
To guarantee deterministic execution, all JSON payloads returned by the Gemini models are parsed and validated against strict Pydantic schemas. If a model attempts to deviate from the expected JSON structure or outputs injection-like phrases in its response, the orchestrator rejects the run and falls back to a safe local rule-set.

### C. Zero-Trust Cloud Architecture
- **No API Keys in Code:** Authentication uses Google Cloud **Application Default Credentials (ADC)** linked directly to the Cloud Run Service Account.
- **Least Privilege Access:** The backend service account is restricted to the bare minimum IAM roles needed to run: read-only access to Vertex AI, read/write to Firestore, and write-only to Cloud Logging.
- **Non-Root Containers:** The backend Dockerfile is hardened to run as an unprivileged, non-root user (`appuser`), isolating the container from system vulnerabilities.

---

## 5. Development Journey: Mitigating the 7 Phases of Vibe Coding

Building a robust, production-grade application through vibe coding required managing context, optimizing performance, and handling failures systematically:

1. **Phase 1: Project Scaffolding:** Established the client-server foundation. Setup Firebase, Firestore rules, and the initial FastAPI docker image.
2. **Phase 2: Gemini Vision OCR:** Developed the prescription scanning pipeline. The initial prototype was slow and fragile under low light. We introduced image resizing/preprocessing client-side to decrease latency and improve OCR accuracy.
3. **Phase 3: DDI RAG Corpus:** Connected the backend to a DrugBank vector index. To address API quota limits, we implemented structured request pooling.
4. **Phase 4: Optimization & Retries:** Faced severe Vertex AI 429 rate limit issues during bulk scanning. We implemented an exponential backoff retry mechanism specifically targeting resource-exhaustion errors.
5. **Phase 5: Push Notifications & Caching:** Added FCM notifications and SOS emergency alerts. To resolve API timeout errors during medication checks, we built a read-through local cache. If no new medications are added to a profile, the app reuses cached interaction data on-device rather than running expensive agent pipelines.
6. **Phase 6: Production Deployment:** Migrated the backend to containerized Cloud Run. Configured scale-to-zero settings to minimize cost and created `deploy.ps1` for automated script deployments.
7. **Phase 7: Auth, UI Polish & Observability:** Implemented secure Email/Password authentication in Flutter, fool-proofed the dosage scheduling UI, and deployed a 4-chart health dashboard to GCP for tracking latency and error spikes.

---

## 6. Fulfilling the Kaggle Course Concepts

Our implementation directly demonstrates the core concepts taught throughout the course:

| Concept | Implementation in Medication Orchestra |
|---|---|
| **Multi-Agent System** | Coordinated agents (Intake, Grounding, Interaction, and Alerting) built using the Google ADK framework. |
| **Antigravity Developer Workflow** | Vibe coded using the Antigravity IDE and CLI. Built-in logs and metric pipelines were used to debug the FastAPI server in real-time. |
| **Security & Safety** | ep2e Firebase authentication, robust input sanitization, strict Pydantic output validation, and non-root Docker configurations. |
| **Deployability** | Containerized FastAPI backend deployed to serverless Google Cloud Run using service accounts and ADC. |
| **Agent Observability** | Configured structured JSON logging on Cloud Run and set up a Google Cloud Monitoring dashboard monitoring request rate, latency (P50/P95/P99), and active scaling instances. |

---

## 7. Conclusion

By placing a cooperative team of AI agents at the center of the household healthcare loop, **Medication Orchestra** turns a fragmented, dangerous process into a safe, orchestrated experience. It keeps personal data secure, leverages Google's state-of-the-art Agent Development Kit, and solves a critical, real-world health challenge facing millions of elderly patients and their caregivers every single day.
