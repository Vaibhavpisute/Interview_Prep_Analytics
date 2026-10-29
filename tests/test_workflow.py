from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from conftest import make_row
from ipa.db import fact_answers, generated_questions, llm_cache
from ipa.ingest import ingest_directory
from ipa.llm.client import DbResponseCache, LLMClient
from ipa.llm.fake import FakeProvider
from ipa.llm.providers import LLMTransportError, RetryableError
from ipa.workflow import generate_questions, score_pending, write_practice_sheet


def fake_client(engine, **kw) -> LLMClient:
    return LLMClient(
        FakeProvider(), cache=DbResponseCache(engine), sleep=lambda s: None, max_retries=2, **kw
    )


def test_generate_questions_stores_and_dedupes(engine, tmp_path):
    jd = tmp_path / "jd.txt"
    jd.write_text("Job title: Data Engineer\nSpark and SQL experience.", encoding="utf-8")
    text = jd.read_text(encoding="utf-8")
    qs = generate_questions(engine, fake_client(engine), text, 8)
    assert len(qs) == 8
    with engine.connect() as conn:
        stored = conn.execute(select(func.count()).select_from(generated_questions)).scalar_one()
    assert stored <= 8 and stored >= 6
    again = generate_questions(engine, fake_client(engine), text, 8)  # from cache
    assert [q.question for q in again] == [q.question for q in qs]
    with engine.connect() as conn:
        assert (
            conn.execute(select(func.count()).select_from(generated_questions)).scalar_one()
            == stored
        )

    sheet = write_practice_sheet(qs, text, tmp_path / "gen")
    rows = json.loads(sheet.read_text(encoding="utf-8"))
    assert len(rows) == 8 and rows[0]["answer"] == "" and rows[0]["job_title"] == "Data Engineer"


def test_score_pending_scores_everything_and_stores_json(loaded_engine):
    summary = score_pending(loaded_engine, fake_client(loaded_engine))
    assert summary.total == 96 and summary.scored + summary.failed == 96
    assert summary.failed >= 1  # the fake never answers some prompts validly
    with loaded_engine.connect() as conn:
        row = conn.execute(select(fact_answers).where(fact_answers.c.llm_valid == 1).limit(1)).one()
        failed = conn.execute(select(fact_answers).where(fact_answers.c.llm_valid == 0)).first()
    assert 1 <= row.score <= 10
    assert json.loads(row.strengths_json) and json.loads(row.improvements_json)
    assert row.llm_provider == "fake" and row.llm_attempts >= 1
    assert failed.score is None and "valid JSON" in failed.llm_error and failed.llm_attempts > 1


def test_score_pending_only_scores_new_answers_and_supports_limit(engine, tmp_path, write_json):
    write_json("a.json", [make_row(i) for i in range(1, 6)])
    ingest_directory(engine, tmp_path / "raw", tmp_path / "rej")
    client = fake_client(engine)
    assert score_pending(engine, client, limit=2).total == 2
    rest = score_pending(engine, client)
    assert rest.total == 3
    assert score_pending(engine, client).total == 0
    assert score_pending(engine, client, rescore=True).total == 5


def test_second_run_is_served_from_the_cache(engine, tmp_path, write_json):
    write_json("a.json", [make_row(i) for i in range(1, 4)])
    ingest_directory(engine, tmp_path / "raw", tmp_path / "rej")
    client = fake_client(engine)
    score_pending(engine, client)
    again = score_pending(engine, client, rescore=True)
    assert again.cached == 3
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(llm_cache)).scalar_one() >= 3


def test_transport_failure_marks_answer_invalid_but_continues(engine, tmp_path, write_json):
    write_json("a.json", [make_row(1), make_row(2)])
    ingest_directory(engine, tmp_path / "raw", tmp_path / "rej")

    class Down(FakeProvider):
        def complete(self, system, prompt, model):
            raise RetryableError("service unavailable")

    client = LLMClient(Down(), sleep=lambda s: None, max_retries=1)
    summary = score_pending(engine, client)
    assert (summary.scored, summary.failed) == (0, 2)
    with engine.connect() as conn:
        rows = conn.execute(select(fact_answers.c.llm_valid, fact_answers.c.llm_error)).all()
    assert all(r.llm_valid == 0 and "failed" in r.llm_error for r in rows)
    with pytest.raises(LLMTransportError):
        client.complete_json("x", __import__("ipa.schemas", fromlist=["QuestionList"]).QuestionList)


def test_failed_answers_are_retried_on_next_run(engine, tmp_path, write_json):
    write_json("a.json", [make_row(1)])
    ingest_directory(engine, tmp_path / "raw", tmp_path / "rej")

    class Down(FakeProvider):
        def complete(self, system, prompt, model):
            raise RetryableError("down")

    score_pending(engine, LLMClient(Down(), sleep=lambda s: None, max_retries=0))
    summary = score_pending(engine, fake_client(engine))
    assert summary.total == 1
    with engine.connect() as conn:
        row = conn.execute(select(fact_answers.c.llm_valid, fact_answers.c.llm_error)).one()
    assert row.llm_valid in (0, 1)
    if row.llm_valid == 1:
        assert row.llm_error is None
