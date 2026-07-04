from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTORAG_", env_file=".env")

    database_url: str = "postgresql://autorag:autorag@127.0.0.1:5432/autorag"
    storage_dir: Path = Path("data/documents")
    embedding_provider: str = "hash"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_dimension: int = 384


settings = Settings()
