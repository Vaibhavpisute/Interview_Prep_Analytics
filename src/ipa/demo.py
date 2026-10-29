"""`ipa demo`: the full pipeline on sample data with the offline fake LLM."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table

from ipa import checks, export, ingest, report, workflow
from ipa.config import load_settings
from ipa.db import init_db, make_engine
from ipa.llm.client import DbResponseCache, LLMClient
from ipa.llm.fake import FakeProvider
from ipa.report import collect


def run_demo(
    console: Console, db_url: str, reset: bool, raw_dir: Path, jd: Path, out_dir: Path
) -> dict[str, object]:
    settings = load_settings()
    engine = make_engine(db_url)
    console.rule("[bold]Interview Prep Analytics demo (fake LLM, no API key)")

    console.print("[bold]1/7[/bold] Create tables and SQL views")
    init_db(engine, reset=reset)

    console.print(
        "[bold]2/7[/bold] Ingest sample files (validate, reject bad rows, load star schema)"
    )
    summary = ingest.ingest_directory(engine, raw_dir, settings.rejected_dir)
    t = Table(show_header=True)
    for col in ("File", "Read", "Loaded", "Rejected", "Status"):
        t.add_column(col)
    for f in summary.files:
        t.add_row(
            f.file_name, str(f.rows_read), str(f.rows_loaded), str(f.rows_rejected),
            "skipped" if f.skipped else "loaded",
        )  # fmt: skip
    console.print(t)

    client = LLMClient(
        FakeProvider(), max_retries=settings.llm_max_retries, cache=DbResponseCache(engine),
        backoff_seconds=0.0,
    )  # fmt: skip

    console.print("[bold]3/7[/bold] Generate practice questions from the job description")
    questions = workflow.generate_questions(engine, client, jd.read_text(encoding="utf-8"), 6)
    for i, q in enumerate(questions, start=1):
        console.print(f"   {i}. [cyan]{q.category}[/cyan]: {q.question}")

    console.print("[bold]4/7[/bold] Score answers (strict JSON, validated, retries, cache)")
    scores = workflow.score_pending(engine, client)
    console.print(
        f"   scored={scores.scored} failed={scores.failed} "
        f"from_cache={scores.cached} needed_retry={scores.retried}"
    )

    console.print("[bold]5/7[/bold] Run anomaly and data-quality checks")
    found = checks.run_checks(engine)
    for rule, n in sorted(found.by_rule.items()):
        console.print(f"   {rule:<24}{n}")

    console.print("[bold]6/7[/bold] Build the weekly Excel report and CSV exports")
    xlsx = report.build_report(engine, out_dir / "weekly_report.xlsx")
    export.export_csv(engine, out_dir / "export")
    console.print(f"   {xlsx}")

    console.print("[bold]7/7[/bold] Results")
    data = collect(engine)
    weak = Table(title="Weakest categories (average score)")
    for col in ("Rank", "Category", "Avg score", "Answers"):
        weak.add_column(col)
    for r in data["weak"].itertuples():
        weak.add_row(str(r.rank), r.category, f"{r.avg_score:.2f}", str(r.answers))
    console.print(weak)
    console.print(
        f"Done: {summary.rows_loaded} answers loaded, {found.total} anomalies, "
        f"report at {xlsx}. Dashboard: streamlit run src/ipa/dashboard/app.py"
    )
    return {"loaded": summary.rows_loaded, "anomalies": found.total, "report": xlsx}
