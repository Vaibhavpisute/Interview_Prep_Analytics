"""LLM workflows: generate practice questions and score answers."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from ipa.db import dim_category, dim_question, fact_answers, generated_questions
from ipa.ingest import question_hash, sha256_text
from ipa.llm.client import LLMClient
from ipa.llm.prompts import questions_prompt, score_prompt
from ipa.llm.providers import LLMError
from ipa.schemas import GeneratedQuestion, QuestionList, ScoreResult
from ipa.speech import SpeechMetrics

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def generate_questions(
    engine: Engine, client: LLMClient, jd_text: str, n: int
) -> list[GeneratedQuestion]:
    """Ask the LLM for n job-specific questions and remember them in generated_questions."""
    result = client.complete_json(questions_prompt(jd_text, n), QuestionList)
    questions = result.value.questions[:n]
    jd_hash = sha256_text(jd_text)
    with engine.begin() as conn:
        for q in questions:
            qh = question_hash(q.question)
            if conn.execute(
                select(generated_questions.c.gq_id).where(generated_questions.c.question_hash == qh)
            ).first():
                continue
            conn.execute(
                insert(generated_questions).values(
                    question_hash=qh,
                    question_text=q.question,
                    category=q.category,
                    what_good_looks_like=q.what_good_looks_like,
                    jd_hash=jd_hash,
                    created_at=_now(),
                )
            )
    log.info("Generated %d questions (cached=%s)", len(questions), result.cached)
    return questions


def write_practice_sheet(questions: list[GeneratedQuestion], jd_text: str, out_dir: Path) -> Path:
    """Write a fill-in sheet: add your answer and duration, then copy it into data/raw/."""
    out_dir.mkdir(parents=True, exist_ok=True)
    title = jd_text.splitlines()[0].replace("Job title:", "").strip() if jd_text.strip() else ""
    rows = [
        {
            "session_id": f"PRACTICE-{sha256_text(jd_text)[:6]}",
            "session_date": datetime.now().date().isoformat(),
            "job_title": title[:100] or "Data Engineer",
            "category": q.category,
            "question": q.question,
            "answer": "",
            "duration_sec": 0,
        }
        for q in questions
    ]
    path = out_dir / f"practice_{sha256_text(jd_text)[:8]}.json"
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


@dataclass
class ScoreSummary:
    scored: int = 0
    failed: int = 0
    cached: int = 0
    retried: int = 0

    @property
    def total(self) -> int:
        return self.scored + self.failed


def score_pending(
    engine: Engine, client: LLMClient, limit: int | None = None, rescore: bool = False
) -> ScoreSummary:
    """Score every answer without a valid score (or all answers with rescore=True)."""
    q = (
        select(
            fact_answers.c.answer_id,
            fact_answers.c.answer_text,
            fact_answers.c.duration_sec,
            fact_answers.c.word_count,
            fact_answers.c.wpm,
            fact_answers.c.filler_count,
            dim_question.c.question_text,
            dim_category.c.name.label("category"),
        )
        .join(dim_question, dim_question.c.question_id == fact_answers.c.question_id)
        .join(dim_category, dim_category.c.category_id == fact_answers.c.category_id)
        .order_by(fact_answers.c.answer_id)
    )
    if not rescore:
        q = q.where(fact_answers.c.score.is_(None))
    if limit:
        q = q.limit(limit)
    with engine.connect() as conn:
        rows = conn.execute(q).mappings().all()
    summary = ScoreSummary()
    for row in rows:
        metrics = SpeechMetrics(
            words=row["word_count"],
            duration_sec=int(row["duration_sec"] + 0.5),
            words_per_minute=row["wpm"],
            filler_count=row["filler_count"],
        )
        prompt = score_prompt(row["question_text"], row["category"], row["answer_text"], metrics)
        try:
            res = client.complete_json(prompt, ScoreResult)
        except LLMError as exc:
            summary.failed += 1
            log.error("Answer %s could not be scored: %s", row["answer_id"], exc)
            values = {
                "score": None,
                "llm_valid": 0,
                "llm_error": str(exc)[:500],
                "llm_provider": client.provider.name,
                "llm_model": client.model,
                "llm_attempts": exc.attempts or 1,
                "scored_at": _now(),
            }
        else:
            summary.scored += 1
            summary.cached += res.cached
            summary.retried += res.attempts > 1
            v = res.value
            values = {
                "score": v.score,
                "strengths_json": json.dumps(v.strengths),
                "improvements_json": json.dumps(v.improvements),
                "improved_answer": v.improved_answer,
                "llm_provider": client.provider.name,
                "llm_model": res.model,
                "llm_attempts": res.attempts,
                "llm_valid": 1,
                "llm_error": None,
                "scored_at": _now(),
            }
        with engine.begin() as conn:
            conn.execute(
                update(fact_answers)
                .where(fact_answers.c.answer_id == row["answer_id"])
                .values(**values)
            )
    log.info(
        "Scoring done: scored=%d failed=%d cached=%d retried=%d",
        summary.scored, summary.failed, summary.cached, summary.retried,
    )  # fmt: skip
    return summary
