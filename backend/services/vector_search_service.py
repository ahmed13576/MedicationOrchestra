"""
vector_search_service.py — Medication Orchestra

Provides drug-drug interaction lookups via:
  1. Vertex AI Vector Search (when deployed — reads vector_search_config.json)
  2. Keyword fallback (always available — searches ddi_processed.json)

Call `get_interaction(drug_a, drug_b)` — it selects the best available method.
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
_DATA_DIR   = Path(__file__).resolve().parent.parent / "data"
_PROCESSED  = _DATA_DIR / "ddi_processed.json"
_VS_CONFIG  = _DATA_DIR / "vector_search_config.json"

# ── Load processed corpus (once at startup) ────────────────────────────────────
_DDI_RECORDS: list = []
_DDI_INDEX: dict = {}   # id -> record (for Vector Search id lookup)

def _load_corpus() -> None:
    global _DDI_RECORDS, _DDI_INDEX
    if _DDI_RECORDS:
        return  # already loaded
    if not _PROCESSED.exists():
        logger.warning("ddi_processed.json not found -- run prepare_corpus.py first")
        return
    with open(_PROCESSED, encoding="utf-8") as f:
        _DDI_RECORDS = json.load(f)
    _DDI_INDEX = {r["id"]: r for r in _DDI_RECORDS}
    logger.info(f"Loaded {len(_DDI_RECORDS)} DDI records from corpus")

_load_corpus()

# ── Vertex AI Vector Search (optional) ────────────────────────────────────────
VECTOR_SEARCH_AVAILABLE = False
_vs_endpoint = None
_embedding_model = None

def _init_vector_search() -> None:
    global VECTOR_SEARCH_AVAILABLE, _vs_endpoint, _embedding_model
    if not _VS_CONFIG.exists():
        logger.info("vector_search_config.json not found -- using keyword fallback")
        return
    try:
        config = json.loads(_VS_CONFIG.read_text(encoding="utf-8"))
        endpoint_name = config.get("endpoint_name")
        if not endpoint_name:
            logger.info("Vector Search endpoint_name not set -- using keyword fallback")
            return

        from google.cloud import aiplatform
        from vertexai.language_models import TextEmbeddingModel
        import vertexai

        vertexai.init(project="project-f9540f8f-d01e-47d3-a36", location="us-central1")
        _embedding_model = TextEmbeddingModel.from_pretrained("text-embedding-004")
        _vs_endpoint = aiplatform.MatchingEngineIndexEndpoint(
            index_endpoint_name=endpoint_name
        )
        VECTOR_SEARCH_AVAILABLE = True
        logger.info(f"Vector Search initialised: {endpoint_name}")
    except Exception as e:
        logger.warning(f"Vector Search init failed (will use fallback): {e}")

_init_vector_search()


# ── Public API ─────────────────────────────────────────────────────────────────

def search_interactions(drug_a: str, drug_b: str, top_k: int = 5) -> list:
    """
    Vector Search lookup. Returns list of matching DDI records.
    Raises RuntimeError if VECTOR_SEARCH_AVAILABLE is False.
    """
    if not VECTOR_SEARCH_AVAILABLE:
        raise RuntimeError("Vector Search not available")
    query = f"{drug_a} {drug_b} drug interaction contraindication side effect"
    embedding = _embedding_model.get_embeddings([query])[0].values
    response = _vs_endpoint.find_neighbors(
        deployed_index_id="ddi_deployed",
        queries=[embedding],
        num_neighbors=top_k,
    )
    results = []
    for neighbor in response[0]:
        record = _DDI_INDEX.get(neighbor.id)
        if record:
            results.append({**record, "vector_distance": neighbor.distance})
    return results


def search_interactions_fallback(drug_a: str, drug_b: str) -> Optional[dict]:
    """
    Keyword alias search against the local JSON corpus.
    Checks both directions (a->b and b->a).
    Returns the HIGHEST-severity match found, or None.
    """
    if not _DDI_RECORDS:
        _load_corpus()

    a = drug_a.strip().lower()
    b = drug_b.strip().lower()

    SEVERITY_ORDER = {"contraindicated": 0, "major": 1, "moderate": 2, "minor": 3}
    best: Optional[dict] = None

    for record in _DDI_RECORDS:
        ra, rb = record["drug_a"], record["drug_b"]

        match_fwd = (_token_match(a, ra) and _token_match(b, rb))
        match_rev = (_token_match(b, ra) and _token_match(a, rb))
        if not (match_fwd or match_rev):
            continue

        if best is None:
            best = record
        elif SEVERITY_ORDER.get(record["severity"], 9) < SEVERITY_ORDER.get(best["severity"], 9):
            best = record

    return best


def get_interaction(drug_a: str, drug_b: str) -> list:
    """
    Unified lookup: tries Vector Search first, falls back to keyword search.
    Always returns a list (empty list = no interactions found).
    """
    if VECTOR_SEARCH_AVAILABLE:
        try:
            results = search_interactions(drug_a, drug_b)
            if results:
                logger.info(f"Vector Search found {len(results)} hits for {drug_a}+{drug_b}")
                return results
        except Exception as e:
            logger.warning(f"Vector Search error, using fallback: {e}")

    result = search_interactions_fallback(drug_a, drug_b)
    if result:
        logger.info(f"Keyword fallback: {drug_a}+{drug_b} -> {result['severity']}")
        return [result]
    return []


# ── Internal helpers ───────────────────────────────────────────────────────────

def _token_match(query: str, corpus_name: str) -> bool:
    """True if query is a substring of corpus_name or corpus_name is a substring of query."""
    q, c = query.strip(), corpus_name.strip()
    return q in c or c in q
