"""Database schema (star schema + support tables) and connection helpers.

Uses SQLAlchemy Core so the same code runs on PostgreSQL and on SQLite (used in tests).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib import resources
from pathlib import Path

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

metadata = MetaData()

dim_category = Table(
    "dim_category",
    metadata,
    Column("category_id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(64), nullable=False, unique=True),
)

dim_date = Table(
    "dim_date",
    metadata,
    Column("date_id", Integer, primary_key=True, autoincrement=False),  # yyyymmdd
    Column("full_date", Date, nullable=False, unique=True),
    Column("year", Integer, nullable=False),
    Column("month", Integer, nullable=False),
    Column("iso_week", Integer, nullable=False),
    Column("week_start", Date, nullable=False),  # Monday of the ISO week
    Column("day_of_week", Integer, nullable=False),  # 1 = Monday
)

dim_session = Table(
    "dim_session",
    metadata,
    Column("session_id", Integer, primary_key=True, autoincrement=True),
    Column("session_key", String(128), nullable=False, unique=True),  # id from the source file
    Column("job_title", String(128), nullable=False),
    Column("date_id", Integer, ForeignKey("dim_date.date_id"), nullable=False),
    Column("source_file", String(256), nullable=False),
)

dim_question = Table(
    "dim_question",
    metadata,
    Column("question_id", Integer, primary_key=True, autoincrement=True),
    Column("question_hash", String(64), nullable=False, unique=True),
    Column("question_text", Text, nullable=False),
    Column("category_id", Integer, ForeignKey("dim_category.category_id"), nullable=False),
)

fact_answers = Table(
    "fact_answers",
    metadata,
    Column("answer_id", Integer, primary_key=True, autoincrement=True),
    # Hash of session + question + answer: makes loads idempotent.
    Column("answer_key", String(64), nullable=False, unique=True),
    Column("session_id", Integer, ForeignKey("dim_session.session_id"), nullable=False),
    Column("question_id", Integer, ForeignKey("dim_question.question_id"), nullable=False),
    Column("category_id", Integer, ForeignKey("dim_category.category_id"), nullable=False),
    Column("date_id", Integer, ForeignKey("dim_date.date_id"), nullable=False),
    Column("answer_text", Text, nullable=False),
    Column("duration_sec", Float, nullable=False),
    # speech metrics
    Column("word_count", Integer, nullable=False),
    Column("wpm", Integer, nullable=False),
    Column("filler_count", Integer, nullable=False),
    Column("fillers_json", Text, nullable=False),
    # LLM scoring (null until `ipa score` runs)
    Column("score", Integer),
    Column("strengths_json", Text),
    Column("improvements_json", Text),
    Column("improved_answer", Text),
    Column("llm_provider", String(32)),
    Column("llm_model", String(96)),
    Column("llm_attempts", Integer),
    Column("llm_valid", Integer),  # 1 ok, 0 failed validation, null not scored yet
    Column("llm_error", Text),
    Column("scored_at", DateTime),
)

load_log = Table(
    "load_log",
    metadata,
    Column("load_id", Integer, primary_key=True, autoincrement=True),
    Column("file_name", String(256), nullable=False),
    Column("file_hash", String(64), nullable=False),
    Column("loaded_at", DateTime, nullable=False),
    Column("rows_read", Integer, nullable=False),
    Column("rows_loaded", Integer, nullable=False),
    Column("rows_rejected", Integer, nullable=False),
    UniqueConstraint("file_name", "file_hash", name="uq_load_log_file"),
)

llm_cache = Table(
    "llm_cache",
    metadata,
    Column("prompt_hash", String(64), primary_key=True),
    Column("provider", String(32), nullable=False),
    Column("model", String(96), nullable=False),
    Column("response_text", Text, nullable=False),
    Column("created_at", DateTime, nullable=False),
)

generated_questions = Table(
    "generated_questions",
    metadata,
    Column("gq_id", Integer, primary_key=True, autoincrement=True),
    Column("question_hash", String(64), nullable=False, unique=True),
    Column("question_text", Text, nullable=False),
    Column("category", String(64), nullable=False),
    Column("what_good_looks_like", Text, nullable=False),
    Column("jd_hash", String(64), nullable=False),
    Column("created_at", DateTime, nullable=False),
)

anomalies = Table(
    "anomalies",
    metadata,
    Column("anomaly_id", Integer, primary_key=True, autoincrement=True),
    Column("rule", String(48), nullable=False),
    Column("severity", String(16), nullable=False),
    Column("answer_id", Integer, ForeignKey("fact_answers.answer_id")),
    Column("session_id", Integer, ForeignKey("dim_session.session_id")),
    Column("category", String(64)),
    Column("metric_value", Float),
    Column("detail", Text, nullable=False),
    Column("detected_at", DateTime, nullable=False),
)

VIEW_NAMES = [
    "v_score_trend_by_category",
    "v_weakest_categories",
    "v_weekly_delivery",
    "v_question_repeat",
    "v_repeat_rate_summary",
]


def make_engine(url: str) -> Engine:
    """Create an engine. `postgresql://` is mapped to the psycopg (v3) driver."""
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, future=True)


@lru_cache(maxsize=1)
def _views_sql() -> list[str]:
    raw = resources.files("ipa").joinpath("sql/views.sql").read_text(encoding="utf-8")
    return [stmt.strip() for stmt in raw.split(";") if stmt.strip()]


def create_views(engine: Engine) -> None:
    with engine.begin() as conn:
        for name in VIEW_NAMES:
            conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
        for stmt in _views_sql():
            conn.execute(text(stmt))
    log.debug("Created %d analytics views", len(VIEW_NAMES))


def init_db(engine: Engine, reset: bool = False) -> None:
    """Create all tables and views. With reset=True, drop the project's tables first."""
    if reset:
        with engine.begin() as conn:
            for name in VIEW_NAMES:
                conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
        metadata.drop_all(engine)
        log.warning("Dropped all project tables")
    metadata.create_all(engine)
    create_views(engine)
    log.info("Database ready (%s)", engine.dialect.name)
