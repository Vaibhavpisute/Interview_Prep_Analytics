from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from conftest import SAMPLE_RAW, make_row
from ipa.db import dim_category, dim_date, dim_question, dim_session, fact_answers, load_log
from ipa.ingest import (
    answer_key,
    date_row,
    ingest_directory,
    question_hash,
    read_rows,
    validate_rows,
)
from ipa.schemas import RawRecord, ScoreResult, normalise_category


def count(engine, table) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


# ---------- Pydantic validation ----------


def test_valid_record_is_normalised():
    rec = RawRecord.model_validate(make_row(category="data modeling", job_title="  Data Engineer "))
    assert rec.category == "Data Modelling"
    assert rec.job_title == "Data Engineer"
    assert rec.session_date.isoformat() == "2026-07-06"


@pytest.mark.parametrize(
    "bad",
    [
        {"category": "Java"},
        {"answer": ""},
        {"answer": "   "},
        {"duration_sec": -3},
        {"duration_sec": 0},
        {"duration_sec": 99999},
        {"session_date": "31/02/2026"},
        {"question": "Too short"},
        {"session_id": ""},
    ],
)
def test_invalid_records_are_rejected(bad):
    with pytest.raises(ValidationError):
        RawRecord.model_validate(make_row(**bad))


def test_normalise_category_aliases():
    assert normalise_category("Behavioral") == "Behavioural"
    assert normalise_category("  etl ") == "ETL"
    with pytest.raises(ValueError):
        normalise_category("Cooking")


def test_score_result_is_strict():
    ok = ScoreResult.model_validate(
        {"score": 7, "strengths": ["a"], "improvements": ["b"], "improved_answer": "c"}
    )
    assert ok.score == 7
    for bad in (
        {"score": 11, "strengths": ["a"], "improvements": ["b"], "improved_answer": "c"},
        {"score": 5, "strengths": [], "improvements": ["b"], "improved_answer": "c"},
        {"score": 5, "strengths": ["  "], "improvements": ["b"], "improved_answer": "c"},
        {"score": 5, "strengths": ["a"], "improvements": ["b"]},
    ):
        with pytest.raises(ValidationError):
            ScoreResult.model_validate(bad)


def test_validate_rows_splits_good_and_bad_with_reasons():
    good, bad = validate_rows([make_row(1), make_row(2, category="Java"), {"_raw": "x"}])
    assert len(good) == 1
    assert [b["row_number"] for b in bad] == [2, 3]
    assert "category" in bad[0]["reason"]


# ---------- hashing helpers ----------


def test_hashes_ignore_case_and_spacing():
    assert question_hash("How  are YOU?") == question_hash("how are you?")
    assert answer_key("S1", "Q one two three", "a  b") == answer_key("S1", "q ONE two three", "a b")
    assert answer_key("S1", "Q", "a") != answer_key("S2", "Q", "a")


def test_date_row_week_start_is_monday():
    row = date_row(__import__("datetime").date(2026, 7, 9))  # a Thursday
    assert row["date_id"] == 20260709
    assert row["week_start"].isoformat() == "2026-07-06"
    assert row["day_of_week"] == 4


# ---------- loading ----------


def test_load_json_and_csv_into_star_schema(engine, tmp_path, write_json):
    raw = tmp_path / "raw"
    write_json("a.json", [make_row(1), make_row(2, category="Spark")])
    with (raw / "b.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(make_row(3)))
        w.writeheader()
        w.writerow(make_row(3, category="ETL", session_date="2026-07-15"))
    summary = ingest_directory(engine, raw, tmp_path / "rej")
    assert summary.rows_loaded == 3
    assert count(engine, fact_answers) == 3
    assert count(engine, dim_session) == 3
    assert count(engine, dim_question) == 3
    assert count(engine, dim_category) == 3
    assert count(engine, dim_date) == 2
    with engine.connect() as conn:
        row = conn.execute(select(fact_answers).where(fact_answers.c.answer_id == 1)).one()
    assert row.word_count > 5 and row.wpm > 0 and row.score is None


def test_bad_rows_go_to_rejected_file_with_reason(engine, tmp_path, write_json):
    raw = tmp_path / "raw"
    write_json("a.json", [make_row(1), make_row(2, duration_sec=-1)])
    summary = ingest_directory(engine, raw, tmp_path / "rej")
    assert (summary.rows_loaded, summary.rows_rejected) == (1, 1)
    lines = (tmp_path / "rej" / "a.json.rejected.jsonl").read_text(encoding="utf-8").splitlines()
    item = json.loads(lines[0])
    assert item["row_number"] == 2
    assert "duration_sec" in item["reason"]
    assert item["row"]["session_id"] == "T002"


def test_load_is_idempotent_and_incremental(engine, tmp_path, write_json):
    raw = tmp_path / "raw"
    write_json("a.json", [make_row(1), make_row(2)])
    first = ingest_directory(engine, raw, tmp_path / "rej")
    assert first.rows_loaded == 2

    # same file again: skipped because it is in load_log
    second = ingest_directory(engine, raw, tmp_path / "rej")
    assert second.files_skipped == 1 and second.rows_loaded == 0
    assert count(engine, fact_answers) == 2

    # a new file is loaded, the old one is still skipped
    write_json("b.json", [make_row(3)])
    third = ingest_directory(engine, raw, tmp_path / "rej")
    assert [f.skipped for f in third.files] == [True, False]
    assert count(engine, fact_answers) == 3
    assert count(engine, load_log) == 2


def test_changed_file_adds_only_new_rows(engine, tmp_path, write_json):
    raw = tmp_path / "raw"
    write_json("a.json", [make_row(1)])
    ingest_directory(engine, raw, tmp_path / "rej")
    write_json("a.json", [make_row(1), make_row(2)])  # same name, new content
    summary = ingest_directory(engine, raw, tmp_path / "rej")
    assert summary.rows_loaded == 1
    assert count(engine, fact_answers) == 2


def test_same_rows_in_two_files_are_not_duplicated(engine, tmp_path, write_json):
    raw = tmp_path / "raw"
    write_json("a.json", [make_row(1)])
    write_json("copy_of_a.json", [make_row(1)])
    summary = ingest_directory(engine, raw, tmp_path / "rej")
    assert summary.rows_loaded == 1
    assert count(engine, fact_answers) == 1


def test_unreadable_files_are_reported_not_fatal(engine, tmp_path, write_json):
    raw = tmp_path / "raw"
    write_json("good.json", [make_row(1)])
    (raw / "broken.json").write_text("{not json", encoding="utf-8")
    (raw / "object.json").write_text('{"a": 1}', encoding="utf-8")
    summary = ingest_directory(engine, raw, tmp_path / "rej")
    assert summary.rows_loaded == 1
    assert (tmp_path / "rej" / "broken.json.rejected.jsonl").exists()
    assert (tmp_path / "rej" / "object.json.rejected.jsonl").exists()


def test_empty_folder_loads_nothing(engine, tmp_path):
    (tmp_path / "raw").mkdir()
    assert ingest_directory(engine, tmp_path / "raw", tmp_path / "rej").files == []


def test_rejected_file_is_removed_when_file_becomes_clean(engine, tmp_path, write_json):
    raw = tmp_path / "raw"
    write_json("a.json", [make_row(1, category="Java")])
    ingest_directory(engine, raw, tmp_path / "rej")
    assert (tmp_path / "rej" / "a.json.rejected.jsonl").exists()
    write_json("a.json", [make_row(1)])
    ingest_directory(engine, raw, tmp_path / "rej")
    assert not (tmp_path / "rej" / "a.json.rejected.jsonl").exists()


def test_read_rows_json_list_with_non_objects(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('[{"a": 1}, 5]', encoding="utf-8")
    assert read_rows(p) == [{"a": 1}, {"_raw": 5}]


def test_committed_sample_data_loads_as_documented(engine, tmp_path):
    summary = ingest_directory(engine, SAMPLE_RAW, tmp_path / "rej")
    assert summary.rows_loaded == 96
    assert summary.rows_rejected == 4
    assert count(engine, dim_session) == 30
    assert count(engine, dim_category) == 6
    # running again changes nothing
    again = ingest_directory(engine, Path(SAMPLE_RAW), tmp_path / "rej")
    assert again.rows_loaded == 0 and count(engine, fact_answers) == 96
