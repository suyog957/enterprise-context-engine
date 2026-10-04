FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# Pick up Debian security fixes released after the base image was built.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade "pip>=26.2"
COPY pyproject.toml README.md ./
COPY src ./src

# Runtime image: application package, ontology and migrations; runs as non-root.
FROM base AS runtime
RUN pip install --no-cache-dir . \
    && useradd --system --uid 10001 --no-create-home appuser
COPY apps/api ./apps/api
COPY ontology ./ontology
COPY infra/sql ./infra/sql
ENV ONTOLOGY_DIR=/app/ontology \
    MIGRATIONS_DIR=/app/infra/sql
USER appuser
EXPOSE 8000
CMD ["uvicorn", "enterprise_context.api:app", "--host", "0.0.0.0", "--port", "8000"]

# Developer tools image: dev dependencies for scripts, lint, type checks and tests.
# The repository is bind-mounted at /work and /work/src shadows the installed package,
# so code changes take effect without rebuilding.
FROM base AS tools
RUN pip install --no-cache-dir ".[dev]"
# Optional: local sentence-transformer embeddings for retrieval evaluation (CPU-only torch).
ARG INSTALL_EMBEDDINGS=false
RUN if [ "$INSTALL_EMBEDDINGS" = "true" ]; then \
      pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
      && pip install --no-cache-dir "sentence-transformers>=3.3,<6.0"; \
    fi
WORKDIR /work
ENV PYTHONPATH=/work/src:/work
