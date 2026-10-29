"""ETL: raw JSON/CSV files -> validated rows -> star schema.

* Validation: bad rows go to data/rejected/<file>.rejected.jsonl with the reason.
* Incremental: a file is skipped when (file name, content hash) is already in load_log.
* Idempotent: each answer has a content hash (answer_key); existing keys are never re-inserted.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import insert, select
from sqlalchemy.engine import Connection, Engine

from ipa.db import dim_category, dim_date, dim_question, dim_session, fact_answers, load_log
from ipa.schemas import RawRecord
from ipa.speech import compute_speech_metrics

log = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".json", ".csv"}


@dataclass
class FileResult:
    file_name: str
    skipped: bool = False
    rows_read: int = 0
    rows_loaded: int = 0
    rows_rejected: int = 0


@dataclass
class IngestSummary:
    files: list[FileResult] = field(default_factory=list)

    @property
    def rows_loaded(self) -> int:
        return sum(f.rows_loaded for f in self.files)

    @property
    def rows_rejected(self) -> int:
        return sum(f.rows_rejected for f in self.files)

    @property
    def files_skipped(self) -> int:
        return sum(1 for f in self.files if f.skipped)


def sha256_text(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def question_hash(question: str) -> str:
    return sha256_text(" ".join(question.lower().split()))


def answer_key(session_id: str, question: str, answer: str) -> str:
    return sha256_text(session_id, question_hash(question), " ".join(answer.split()))


def read_rows(path: Path) -> list[dict[str, Any]]:
    """Read raw rows from a JSON (list of objects) or CSV file."""
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("JSON file must contain a list of objects")
        return [r if isinstance(r, dict) else {"_raw": r} for r in data]
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def validate_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[RawRecord], list[dict[str, Any]]]:
    good: list[RawRecord] = []
    bad: list[dict[str, Any]] = []
    for i, row in enumerate(rows, start=1):
        try:
            good.append(RawRecord.model_validate(row))
        except ValidationError as exc:
            reason = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'row'}: {e['msg']}" for e in exc.errors()
            )
            bad.append({"row_number": i, "reason": reason, "row": row})
    return good, bad


def write_rejected(rejected_dir: Path, file_name: str, bad: list[dict[str, Any]]) -> Path | None:
    """Write rejected rows for one source file (replaces any earlier file for it)."""
    target = rejected_dir / f"{file_name}.rejected.jsonl"
    if not bad:
        target.unlink(missing_ok=True)
        return None
    rejected_dir.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        for item in bad:
            fh.write(json.dumps(item, default=str, ensure_ascii=False) + "\n")
    return target


def date_row(d: date) -> dict[str, Any]:
    iso = d.isocalendar()
    return {
        "date_id": int(d.strftime("%Y%m%d")),
        "full_date": d,
        "year": d.year,
        "month": d.month,
        "iso_week": iso.week,
        "week_start": d - timedelta(days=d.weekday()),
        "day_of_week": iso.weekday,
    }


class _DimCache:
    """Looks up or creates dimension rows, remembering ids within one load."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn
        self.category: dict[str, int] = {}
        self.dates: set[int] = set()
        self.sessions: dict[str, int] = {}
        self.questions: dict[str, int] = {}

    def category_id(self, name: str) -> int:
        if name not in self.category:
            found = self.conn.execute(
                select(dim_category.c.category_id).where(dim_category.c.name == name)
            ).scalar()
            if found is None:
                res = self.conn.execute(insert(dim_category).values(name=name))
                found = res.inserted_primary_key[0]
            self.category[name] = int(found)
        return self.category[name]

    def date_id(self, d: date) -> int:
        row = date_row(d)
        if row["date_id"] not in self.dates:
            exists = self.conn.execute(
                select(dim_date.c.date_id).where(dim_date.c.date_id == row["date_id"])
            ).scalar()
            if exists is None:
                self.conn.execute(insert(dim_date).values(**row))
            self.dates.add(row["date_id"])
        return row["date_id"]

    def session_id(self, rec: RawRecord, source_file: str) -> int:
        if rec.session_id not in self.sessions:
            found = self.conn.execute(
                select(dim_session.c.session_id).where(dim_session.c.session_key == rec.session_id)
            ).scalar()
            if found is None:
                res = self.conn.execute(
                    insert(dim_session).values(
                        session_key=rec.session_id,
                        job_title=rec.job_title,
                        date_id=self.date_id(rec.session_date),
                        source_file=source_file,
                    )
                )
                found = res.inserted_primary_key[0]
            self.sessions[rec.session_id] = int(found)
        return self.sessions[rec.session_id]

    def question_id(self, rec: RawRecord) -> int:
        qh = question_hash(rec.question)
        if qh not in self.questions:
            found = self.conn.execute(
                select(dim_question.c.question_id).where(dim_question.c.question_hash == qh)
            ).scalar()
            if found is None:
                res = self.conn.execute(
                    insert(dim_question).values(
                        question_hash=qh,
                        question_text=rec.question,
                        category_id=self.category_id(rec.category),
                    )
                )
                found = res.inserted_primary_key[0]
            self.questions[qh] = int(found)
        return self.questions[qh]


def load_records(conn: Connection, records: list[RawRecord], source_file: str) -> int:
    """Insert records that are not in the warehouse yet. Returns the number of new answers."""
    cache = _DimCache(conn)
    loaded = 0
    for rec in records:
        key = answer_key(rec.session_id, rec.question, rec.answer)
        if conn.execute(
            select(fact_answers.c.answer_id).where(fact_answers.c.answer_key == key)
        ).first():
            continue
        m = compute_speech_metrics(rec.answer, rec.duration_sec)
        conn.execute(
            insert(fact_answers).values(
                answer_key=key,
                session_id=cache.session_id(rec, source_file),
                question_id=cache.question_id(rec),
                category_id=cache.category_id(rec.category),
                date_id=cache.date_id(rec.session_date),
                answer_text=rec.answer,
                duration_sec=rec.duration_sec,
                word_count=m.words,
                wpm=m.words_per_minute,
                filler_count=m.filler_count,
                fillers_json=json.dumps(m.fillers, sort_keys=True),
            )
        )
        loaded += 1
    return loaded


def ingest_directory(engine: Engine, raw_dir: Path, rejected_dir: Path) -> IngestSummary:
    summary = IngestSummary()
    files = sorted(p for p in raw_dir.glob("*") if p.suffix.lower() in SUPPORTED_SUFFIXES)
    if not files:
        log.warning("No .json or .csv files found in %s", raw_dir)
    for path in files:
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        result = FileResult(file_name=path.name)
        summary.files.append(result)
        with engine.begin() as conn:
            seen = conn.execute(
                select(load_log.c.load_id).where(
                    load_log.c.file_name == path.name, load_log.c.file_hash == file_hash
                )
            ).first()
            if seen:
                result.skipped = True
                log.info("Skipping %s (already loaded)", path.name)
                continue
            try:
                rows = read_rows(path)
            except (ValueError, OSError) as exc:  # includes json.JSONDecodeError
                log.error("Cannot read %s: %s", path.name, exc)
                write_rejected(
                    rejected_dir,
                    path.name,
                    [{"row_number": 0, "reason": f"unreadable file: {exc}", "row": {}}],
                )
                result.rows_rejected = 1
                continue
            good, bad = validate_rows(rows)
            write_rejected(rejected_dir, path.name, bad)
            result.rows_read = len(rows)
            result.rows_rejected = len(bad)
            result.rows_loaded = load_records(conn, good, path.name)
            conn.execute(
                insert(load_log).values(
                    file_name=path.name,
                    file_hash=file_hash,
                    loaded_at=datetime.now(UTC).replace(tzinfo=None),
                    rows_read=result.rows_read,
                    rows_loaded=result.rows_loaded,
                    rows_rejected=result.rows_rejected,
                )
            )
        log.info(
            "Loaded %s: read=%d loaded=%d rejected=%d",
            path.name,
            result.rows_read,
            result.rows_loaded,
            result.rows_rejected,
        )
    return summary
