FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

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
WORKDIR /work
ENV PYTHONPATH=/work/src:/work
