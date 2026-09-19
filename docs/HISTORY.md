# What was here before, and why it was removed

This repository started as a hackathon prototype. The review in
[ADVERSARIAL_REVIEW.md](ADVERSARIAL_REVIEW.md) describes what that prototype did
and why it was unsafe; this file records the files that were deleted rather than
left in the tree describing a design that no longer exists.

## Deleted: `backend/agents/*` (ADK agents)

`orchestrator.py`, `intake_agent.py`, `interaction_checker_agent.py`,
`schedule_alert_agent.py` and `search_grounding_agent.py`. They put a language
model in the decision path: the "safe schedule" was produced by prompting an
agent and the result was sometimes paraphrased into claims the knowledge base did
not support. The clinical core is now deterministic
(`services/clinical_engine.py`) and the model is confined to reading images and
rephrasing an explanation that has already been fixed.
`agents/agent_security.py` — the input sanitiser and output validator — remains,
and is now covered by tests.

## Deleted: the DrugBank-derived corpus pipeline

`backend/data/prepare_corpus.py`, `create_vector_index.py`, `verify_corpus.py`,
`verify_vs.py`, `verify_agents.py`, `backend/services/vector_search_service.py`,
and the prebuilt `backend/data/brand_mapping.csv`.

Two problems, both fatal:

1. **Licensing.** The corpus derived from DrugBank, whose licence does not permit
   commercial use, and it was being shipped as the product's clinical knowledge.
   See [KNOWLEDGE_SOURCES.md](KNOWLEDGE_SOURCES.md) for what replaced it and the
   plan for open, properly-licensed vocabulary.
2. **It was empty.** The generated files were gitignored and excluded by
   `.dockerignore`, so the deployed container loaded an empty corpus and answered
   "no interactions found" for every patient — with a green tick. The knowledge
   base now lives in `backend/knowledge/`, is tracked in git, is required by the
   Docker build, is re-checked by `/readyz`, and the service refuses to start
   without it.

## Deleted: sprint instructions that would rebuild the old design

`gumloop/01`, `03`, `04` and the agent-context file `gumloop/*.agents.txt`
instructed the next contributor to install `google-adk`, download the DrugBank
DDI corpus, index it in Vertex AI Vector Search, and build the agent that made
the clinical decision. They were sprint scaffolding for a design the review
rejected, and one of them was an instruction to introduce the licensing problem
again. The two skills that still describe the current design — Gemini vision
intake and the Flutter UI — were kept.

## Deleted: `sanity_check.py`

It scanned `main.py` for undefined names. Ruff (`F821`) does that for the whole
backend, on every push, alongside 127 tests and the safety invariant audit.
