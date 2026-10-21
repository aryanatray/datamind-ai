from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./data/app.db"
    secret_key: str = ""
    encryption_key: str = ""
    public_origin: str = "http://localhost:3000"
    cookie_secure: bool = False
    platform_admin_email: str = ""
    platform_admin_user_id: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_max_output_tokens: int = Field(default=4096, ge=256, le=8192)
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    model_cache: Path = Path("data/models")
    storage_root: Path = Path("data/files")
    sqlite_root: Path = Path("data/sources")
    db_allowed_hosts: str = "localhost,127.0.0.1,postgres,mysql,mongo"
    db_ca_file: str = ""
    searxng_url: str = "http://localhost:8080"
    max_upload_mb: int = 20
    max_query_rows: int = 500
    max_result_bytes: int = 2_000_000
    query_timeout_seconds: int = 10
    user_requests_per_minute: int = 12
    workspace_requests_per_minute: int = 30
    global_daily_token_limit: int = 1_000_000
    user_daily_token_limit: int = 100_000


@lru_cache
def settings() -> Settings:
    return Settings()
