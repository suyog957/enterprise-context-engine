from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    service_name: str = "enterprise-context-api"
    service_version: str = "0.2.0"
    environment: str = "production"
    database_url: str = "postgresql://context_app:local_dev_only_change_me@127.0.0.1:5432/enterprise_context"
    fuseki_url: str = "http://localhost:3030/enterprise"
    fuseki_admin_user: str = "admin"
    fuseki_admin_password: str | None = None
    opa_url: str = "http://localhost:8181"
    opensearch_url: str = "http://localhost:9200"
    data_dir: Path = Path("data")
    ontology_dir: Path = Path("ontology")
    migrations_dir: Path = Path("infra/sql")
    embedding_provider: str = "feature_hash"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimensions: int = 384
    dry_run: bool = True
    log_level: str = "INFO"
    # LLM provider: mock (default, deterministic), openai_compatible (e.g. Ollama), bedrock.
    llm_provider: str = "mock"
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_timeout_seconds: float = 30.0
    aws_region: str | None = None
    # OTLP/HTTP collector base URL (Jaeger: http://jaeger:4318). Unset disables export.
    otel_exporter_otlp_endpoint: str | None = None
    jaeger_query_url: str | None = None
    jaeger_public_url: str = "http://127.0.0.1:16686"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
