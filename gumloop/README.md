# Sprint skills (partial)

The day-by-day build instructions from the original hackathon sprint. Most of
them were deleted — see [docs/HISTORY.md](../docs/HISTORY.md) for what went and
why:

* `01-project-setupSKILL.md` — scaffolded the ADK/Vector Search layout
* `03-rag-interaction-checkerSKILL.md` — built the DrugBank corpus and the RAG index
* `04-adk-orchestration-fcmSKILL.md` — built the agents that decided clinical questions
* `*agents.txt` — project context telling the next contributor not to deviate
  from that design

What remains is the part that still matches the shipped system:

* `02-gemini-vision-intakeSKILL.md` — reading a prescription photo. The model
  extracts text; identity, interaction checking and scheduling are deterministic.
* `05-flutter-uiSKILL.md` — the Flutter screens and their severity colours.

The current design and the rules for changing it are in the root
[README.md](../README.md) and [docs/](../docs).
