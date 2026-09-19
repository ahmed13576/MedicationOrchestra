# Sprint skills — superseded

These files are the day-by-day build plan from the original prototype sprint.
They are kept for history, with one important correction.

**Two of them were removed:**

* `03-rag-interaction-checkerSKILL.md` instructed the team to download the
  **DrugBank DDI corpus** and index it in Vertex AI Vector Search. That corpus
  cannot be used commercially (see `docs/KNOWLEDGE_SOURCES.md`), and the
  severity-free pair list it produced is what made the deployed app answer
  "no interactions found". It is replaced by a curated, cited knowledge base and
  a deterministic engine.
* `04-adk-orchestration-fcmSKILL.md` described ADK agents that decided clinical
  questions and generated schedules. The model no longer makes clinical
  decisions, and the agent classes were deleted.

**`sanity_check.py` was also removed** from the repository root: it scanned
`main.py` for undefined names, which Ruff (`F821`) now does for the whole backend
on every push, alongside the test-suite and the safety invariant audit.

The shipped design is described in the root `README.md` and `docs/API.md`.
