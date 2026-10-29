"""Settings read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

DEFAULT_DATABASE_URL = "sqlite:///data/ipa.db"
CATEGORIES = ["SQL", "Python", "Spark", "ETL", "Data Modelling", "Behavioural"]


class Settings(BaseModel):
    database_url: str = DEFAULT_DATABASE_URL
    llm_provider: str = "anthropic"
    llm_model: str = ""
    llm_fallback_model: str = ""
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 3
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434/v1"
    raw_dir: Path = Path("data/raw")
    rejected_dir: Path = Path("data/rejected")
    output_dir: Path = Path("output")


def load_settings() -> Settings:
    """Build settings from the environment. Secrets are never read from code."""
    load_dotenv()
    env = os.environ
    return Settings(
        database_url=env.get("DATABASE_URL") or DEFAULT_DATABASE_URL,
        llm_provider=(env.get("LLM_PROVIDER") or "anthropic").lower(),
        llm_model=env.get("LLM_MODEL", ""),
        llm_fallback_model=env.get("LLM_FALLBACK_MODEL", ""),
        llm_timeout_seconds=float(env.get("LLM_TIMEOUT_SECONDS") or 60),
        llm_max_retries=int(env.get("LLM_MAX_RETRIES") or 3),
        anthropic_api_key=env.get("ANTHROPIC_API_KEY", ""),
        openai_api_key=env.get("OPENAI_API_KEY", ""),
        gemini_api_key=env.get("GEMINI_API_KEY", ""),
        ollama_base_url=env.get("OLLAMA_BASE_URL") or "http://localhost:11434/v1",
    )
