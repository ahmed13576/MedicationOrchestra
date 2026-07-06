"""
create_vector_index.py — Medication Orchestra Phase 3

One-time setup script that:
  1. Embeds the DDI corpus with text-embedding-004 -> ddi_embeddings.jsonl
  2. Creates a Vertex AI Vector Search streaming index (768 dims)
  3. Creates an index endpoint and deploys the index to it
  4. Streams embeddings into the deployed index via upsert_datapoints
  5. Writes backend/data/vector_search_config.json with index + endpoint names

Run from project root (ONCE — idempotent on re-run):
  python backend/data/create_vector_index.py

Requirements: ADC must be configured (gcloud auth application-default login)
GCP Project: project-f9540f8f-d01e-47d3-a36
"""

import json
import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
PROJECT_ID   = "project-f9540f8f-d01e-47d3-a36"
LOCATION     = "us-central1"
INDEX_NAME   = "medication-ddi-index"
ENDPOINT_NAME = "medication-ddi-endpoint"
DEPLOYED_ID  = "ddi_deployed"
DIMENSIONS   = 768
EMBED_BATCH  = 250   # text-embedding-004 API limit per call

DATA_DIR     = Path(__file__).resolve().parent
JSONL_PATH   = DATA_DIR / "ddi_index.jsonl"
EMBED_PATH   = DATA_DIR / "ddi_embeddings.jsonl"
CONFIG_PATH  = DATA_DIR / "vector_search_config.json"

# ── Helpers ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {"index_name": None, "endpoint_name": None}

def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    logger.info(f"Config saved -> {CONFIG_PATH}")


# ── Step 1: Embed corpus ───────────────────────────────────────────────────────

def embed_corpus() -> None:
    """Generate text embeddings for all entries in ddi_index.jsonl."""
    if EMBED_PATH.exists():
        logger.info("ddi_embeddings.jsonl already exists — skipping embedding step")
        return

    import vertexai
    from vertexai.language_models import TextEmbeddingModel

    vertexai.init(project=PROJECT_ID, location=LOCATION)
    model = TextEmbeddingModel.from_pretrained("text-embedding-004")

    logger.info(f"Loading corpus from {JSONL_PATH}")
    entries = [json.loads(line) for line in JSONL_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    total = len(entries)
    logger.info(f"Embedding {total} records in batches of {EMBED_BATCH}...")

    written = 0
    with open(EMBED_PATH, "w", encoding="utf-8") as out_f:
        for start in range(0, total, EMBED_BATCH):
            batch = entries[start: start + EMBED_BATCH]
            texts = [e["embedding_text"] for e in batch]
            embeddings = model.get_embeddings(texts)
            for entry, emb in zip(batch, embeddings):
                line = json.dumps({"id": entry["id"], "embedding": emb.values}, ensure_ascii=False)
                out_f.write(line + "\n")
                written += 1
            if start % 5000 == 0 and start > 0:
                logger.info(f"  Embedded {start:,}/{total:,}")

    logger.info(f"Embeddings written: {written} -> {EMBED_PATH}")


# ── Step 2: Create Vector Search index ────────────────────────────────────────

def create_index(cfg: dict) -> str:
    """Create the Vertex AI Vector Search index. Returns resource name."""
    from google.cloud import aiplatform
    aiplatform.init(project=PROJECT_ID, location=LOCATION)

    if cfg.get("index_name"):
        logger.info(f"Index already exists: {cfg['index_name']}")
        return cfg["index_name"]

    logger.info("Creating Vertex AI Vector Search index (streaming, 768 dims)...")
    index = aiplatform.MatchingEngineIndex.create_tree_ah_index(
        display_name=INDEX_NAME,
        dimensions=DIMENSIONS,
        approximate_neighbors_count=10,
        distance_measure_type="DOT_PRODUCT_DISTANCE",
        index_update_method="STREAM_UPDATE",
        description="Medication Orchestra DDI drug interaction corpus",
    )
    resource_name = index.resource_name
    logger.info(f"Index created: {resource_name}")
    return resource_name


# ── Step 3: Create endpoint + deploy ──────────────────────────────────────────

def create_endpoint_and_deploy(index_resource_name: str, cfg: dict) -> str:
    """Create endpoint, deploy index, return endpoint resource name."""
    from google.cloud import aiplatform
    aiplatform.init(project=PROJECT_ID, location=LOCATION)

    if cfg.get("endpoint_name"):
        logger.info(f"Endpoint already exists: {cfg['endpoint_name']}")
        return cfg["endpoint_name"]

    logger.info("Creating index endpoint (public)...")
    endpoint = aiplatform.MatchingEngineIndexEndpoint.create(
        display_name=ENDPOINT_NAME,
        public_endpoint_enabled=True,
        description="Medication Orchestra DDI endpoint",
    )
    endpoint_resource = endpoint.resource_name
    logger.info(f"Endpoint created: {endpoint_resource}")

    logger.info(f"Deploying index to endpoint as '{DEPLOYED_ID}'...")
    logger.info("This may take 10-30 minutes. Please wait...")
    index_obj = aiplatform.MatchingEngineIndex(index_name=index_resource_name)
    endpoint.deploy_index(
        index=index_obj,
        deployed_index_id=DEPLOYED_ID,
        display_name=DEPLOYED_ID,
    )
    logger.info(f"Index deployed to endpoint: {endpoint_resource}")
    return endpoint_resource


# ── Step 4: Upsert embeddings ──────────────────────────────────────────────────

def upsert_embeddings(endpoint_resource: str) -> None:
    """Stream all embeddings into the deployed index via upsert_datapoints."""
    from google.cloud import aiplatform
    aiplatform.init(project=PROJECT_ID, location=LOCATION)

    logger.info(f"Loading embeddings from {EMBED_PATH}...")
    lines = EMBED_PATH.read_text(encoding="utf-8").splitlines()
    total = len(lines)
    logger.info(f"Upserting {total} datapoints...")

    index_obj = None
    # find the index via endpoint
    endpoint = aiplatform.MatchingEngineIndexEndpoint(index_endpoint_name=endpoint_resource)
    for di in endpoint.deployed_indexes:
        if di.id == DEPLOYED_ID:
            index_obj = aiplatform.MatchingEngineIndex(index_name=di.index)
            break

    if index_obj is None:
        raise RuntimeError(f"Could not find deployed index '{DEPLOYED_ID}' on endpoint")

    UPSERT_BATCH = 100
    for start in range(0, total, UPSERT_BATCH):
        batch_lines = lines[start: start + UPSERT_BATCH]
        datapoints = []
        for line in batch_lines:
            entry = json.loads(line)
            from google.cloud.aiplatform_v1.types import index as index_v1
            dp = index_v1.IndexDatapoint(
                datapoint_id=entry["id"],
                feature_vector=entry["embedding"],
            )
            datapoints.append(dp)
        index_obj.upsert_datapoints(datapoints=datapoints)
        if start % 5000 == 0 and start > 0:
            logger.info(f"  Upserted {start:,}/{total:,}")

    logger.info(f"All {total} datapoints upserted successfully")


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cfg = load_config()

    # Step 1: Embed (skips if already done)
    embed_corpus()

    # Step 2: Create index
    index_name = create_index(cfg)
    cfg["index_name"] = index_name
    save_config(cfg)

    # Step 3: Create endpoint + deploy
    endpoint_name = create_endpoint_and_deploy(index_name, cfg)
    cfg["endpoint_name"] = endpoint_name
    save_config(cfg)

    # Step 4: Upsert embeddings
    upsert_embeddings(endpoint_name)

    logger.info("=" * 60)
    logger.info("Vector Search setup complete!")
    logger.info(f"Index:    {cfg['index_name']}")
    logger.info(f"Endpoint: {cfg['endpoint_name']}")
    logger.info("Run the backend — vector_search_service.py will auto-detect the config.")
    logger.info("=" * 60)
