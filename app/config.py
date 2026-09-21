"""Typed application settings, read from environment variables or a local .env."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Shared defaults; override a field with RAG_<FIELD> in the environment or .env."""

    model_config = SettingsConfigDict(
        env_prefix="RAG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Multimodal RAG Ingestion"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    # Durable storage for document records and the images referenced by those records.
    database_url: str = "sqlite:///local/multimodal_rag.db"
    image_dir: Path = Path("local/images")
    # Ignore pictures smaller than this fraction of their page (0.001 = 0.1%).
    min_image_area_ratio: float = Field(default=0.001, ge=0, lt=1)
