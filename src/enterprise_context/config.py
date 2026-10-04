from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    service_name: str = "enterprise-context-api"
    environment: str = "production"
    database_url: str = "postgresql://context_app:local_dev_only_change_me@127.0.0.1:5432/enterprise_context"
    fuseki_url: str = "http://localhost:3030/enterprise"
    fuseki_admin_user: str = "admin"
    fuseki_admin_password: str | None = None
    opa_url: str = "http://localhost:8181"
    opensearch_url: str = "http://localhost:9200"
    data_dir: Path = Path("data")
    embedding_provider: str = "feature_hash"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimensions: int = 384
    dry_run: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache

def get_settings() -> Settings:
    return Settings()
