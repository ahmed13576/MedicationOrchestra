FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so code changes do not invalidate the layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY main.py ./
COPY agents/ ./agents/
COPY services/ ./services/

# The clinical knowledge base ships WITH the image. Without it there is nothing
# to check against, so this COPY is a correctness requirement, not packaging.
COPY knowledge/ ./knowledge/

# Fail the build if the knowledge base is missing or too small to be real.
RUN python -c "\
import sys; sys.path.insert(0, '.');\
from services.medication_registry import get_registry;\
r = get_registry(); d = r.describe();\
print('knowledge base:', d);\
sys.exit(0 if d['ingredients'] > 50 and d['interaction_rules'] > 10 else 1)"

# Non-root runtime user.
RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
