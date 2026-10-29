from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from ipa.db import fact_answers, init_db
from ipa.ingest import ingest_directory

REPO = Path(__file__).resolve().parent.parent
SAMPLE_RAW = REPO / "data" / "raw"


@pytest.fixture
def engine() -> Engine:
    """A fresh in-memory SQLite database with all tables and views."""
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    init_db(eng)
    return eng


def make_row(i: int = 1, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "session_id": f"T{i:03d}",
        "session_date": "2026-07-06",
        "job_title": "Data Engineer",
        "category": "SQL",
        "question": f"How would you tune slow query number {i}?",
        "answer": f"I would read the execution plan {i} and add an index because scans are slow. "
        "For example, 10 seconds became 1 second.",
        "duration_sec": 10,
    }
    row.update(overrides)
    return row


@pytest.fixture
def write_json(tmp_path: Path) -> Callable[..., Path]:
    def _write(name: str, rows: list[dict[str, Any]], folder: Path | None = None) -> Path:
        target = (folder or tmp_path / "raw") / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(rows), encoding="utf-8")
        return target

    return _write


@pytest.fixture
def loaded_engine(engine: Engine, tmp_path: Path) -> Engine:
    """The committed sample data loaded into an in-memory database (not scored yet)."""
    ingest_directory(engine, SAMPLE_RAW, tmp_path / "rejected")
    return engine


def set_scores(engine: Engine, scores: dict[int, int | None]) -> None:
    with engine.begin() as conn:
        for answer_id, score in scores.items():
            conn.execute(
                update(fact_answers)
                .where(fact_answers.c.answer_id == answer_id)
                .values(score=score, llm_valid=1, llm_attempts=1)
            )
