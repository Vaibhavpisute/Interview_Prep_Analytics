"""Command line interface: `ipa <command>`."""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from pandas.errors import DatabaseError
from rich.console import Console
from rich.table import Table
from sqlalchemy.engine import Engine

from ipa import checks, export, ingest, report, workflow
from ipa.config import Settings, load_settings
from ipa.db import init_db, make_engine
from ipa.llm.client import DbResponseCache, LLMClient
from ipa.llm.providers import LLMError, build_provider
from ipa.logging_setup import setup_logging

app = typer.Typer(
    help="Interview Prep Analytics: practice-session ETL, LLM scoring, checks and reports. "
    "A practice tool only; it does not help during live interviews.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
log = logging.getLogger("ipa.cli")

DEMO_DB_URL = "sqlite:///data/demo.db"


@app.callback()
def main(
    log_level: str = typer.Option("WARNING", "--log-level", help="DEBUG, INFO, WARNING, ERROR"),
) -> None:
    setup_logging(log_level)


def _engine(settings: Settings) -> Engine:
    return make_engine(settings.database_url)


def _client(settings: Settings, engine: Engine, provider: str | None) -> LLMClient:
    name = (provider or settings.llm_provider).lower()
    prov = build_provider(name, settings)
    return LLMClient(
        prov,
        model=settings.llm_model if name == settings.llm_provider else "",
        fallback_model=settings.llm_fallback_model if name == settings.llm_provider else "",
        max_retries=settings.llm_max_retries,
        cache=DbResponseCache(engine),
    )


def _fail(message: str) -> None:
    console.print(f"[bold red]Error:[/bold red] {message}")
    raise typer.Exit(code=1)


@app.command("init-db")
def init_db_cmd(reset: bool = typer.Option(False, help="Drop all project tables first.")) -> None:
    """Create the tables and SQL views."""
    settings = load_settings()
    init_db(_engine(settings), reset=reset)
    console.print(f"Database ready: {settings.database_url.split('@')[-1]}")


@app.command("ingest")
def ingest_cmd(
    raw_dir: Path = typer.Option(Path("data/raw"), help="Folder with .json and .csv files."),
    rejected_dir: Path = typer.Option(Path("data/rejected"), help="Where bad rows are written."),
) -> None:
    """Validate and load new practice files into the star schema."""
    engine = _engine(load_settings())
    init_db(engine)
    summary = ingest.ingest_directory(engine, raw_dir, rejected_dir)
    _print_ingest(summary)


def _print_ingest(summary: ingest.IngestSummary) -> None:
    table = Table(title="Ingest")
    for col in ("File", "Read", "Loaded", "Rejected", "Status"):
        table.add_column(col)
    for f in summary.files:
        status = "skipped (already loaded)" if f.skipped else "loaded"
        table.add_row(
            f.file_name, str(f.rows_read), str(f.rows_loaded), str(f.rows_rejected), status
        )
    console.print(table)


@app.command("generate")
def generate_cmd(
    jd: Path = typer.Option(..., "--jd", help="Text file with the job description."),
    n: int = typer.Option(10, "--n", min=1, max=50, help="How many questions."),
    provider: str | None = typer.Option(None, help="anthropic, openai, gemini, ollama, fake"),
    out_dir: Path = typer.Option(Path("data/generated"), help="Where the practice sheet goes."),
) -> None:
    """Generate job-specific practice questions from a job description."""
    if not jd.is_file():
        _fail(f"Job description file not found: {jd}")
    settings = load_settings()
    engine = _engine(settings)
    init_db(engine)
    jd_text = jd.read_text(encoding="utf-8")
    try:
        questions = workflow.generate_questions(
            engine, _client(settings, engine, provider), jd_text, n
        )
    except LLMError as exc:
        _fail(str(exc))
        return
    sheet = workflow.write_practice_sheet(questions, jd_text, out_dir)
    table = Table(title=f"{len(questions)} practice questions")
    table.add_column("#")
    table.add_column("Category")
    table.add_column("Question", overflow="fold")
    for i, q in enumerate(questions, start=1):
        table.add_row(str(i), q.category, q.question)
    console.print(table)
    console.print(f"Practice sheet written to {sheet}. Fill in answer and duration_sec, then")
    console.print("copy it to data/raw/ and run `ipa ingest`.")


@app.command("score")
def score_cmd(
    provider: str | None = typer.Option(None, help="anthropic, openai, gemini, ollama, fake"),
    limit: int | None = typer.Option(None, help="Score at most this many answers."),
    rescore: bool = typer.Option(False, help="Score all answers again."),
) -> None:
    """Score answers that have no valid score yet (strict JSON, validated)."""
    settings = load_settings()
    engine = _engine(settings)
    try:
        client = _client(settings, engine, provider)
    except LLMError as exc:
        _fail(str(exc))
        return
    summary = workflow.score_pending(engine, client, limit=limit, rescore=rescore)
    console.print(
        f"Scored {summary.scored}, failed {summary.failed}, from cache {summary.cached}, "
        f"needed a retry {summary.retried}."
    )


@app.command("check")
def check_cmd() -> None:
    """Run the anomaly and data-quality rules and fill the anomalies table."""
    engine = _engine(load_settings())
    summary = checks.run_checks(engine)
    table = Table(title="Anomalies")
    table.add_column("Rule")
    table.add_column("Count", justify="right")
    for rule, n in sorted(summary.by_rule.items()):
        table.add_row(rule, str(n))
    table.add_row("[bold]total[/bold]", f"[bold]{summary.total}[/bold]")
    console.print(table)


@app.command("weekly-report")
def weekly_report_cmd(
    out: Path = typer.Option(Path("output/weekly_report.xlsx"), help="Excel file to write."),
) -> None:
    """Build the Excel report (Summary, Trends, Weak Areas, Anomalies)."""
    path = report.build_report(_engine(load_settings()), out)
    console.print(f"Report written to {path}")


@app.command("export")
def export_cmd(
    out_dir: Path = typer.Option(Path("output/export"), help="Folder for the CSV files."),
) -> None:
    """Export the fact and dimension tables to CSV (for the R analysis)."""
    try:
        paths = export.export_csv(_engine(load_settings()), out_dir)
    except DatabaseError:
        _fail(
            "The tables were not found. Check DATABASE_URL: `ipa demo` uses "
            "sqlite:///data/demo.db, so run `DATABASE_URL=sqlite:///data/demo.db ipa export`."
        )
        return
    console.print(f"Exported {len(paths)} tables to {out_dir}")


@app.command("demo")
def demo_cmd(
    reset: bool = typer.Option(False, help="Drop and recreate the demo tables first."),
    db: str = typer.Option(DEMO_DB_URL, help="Database URL for the demo (never your real one)."),
    raw_dir: Path = typer.Option(Path("data/raw")),
    jd: Path = typer.Option(Path("data/jd/sample_jd.txt")),
    out_dir: Path = typer.Option(Path("output")),
) -> None:
    """Run the whole pipeline on the sample data with the offline FAKE LLM (no API key)."""
    from ipa.demo import run_demo

    if logging.getLogger().level > logging.INFO:  # keep the demo output readable
        logging.getLogger("ipa").setLevel(logging.CRITICAL)
    run_demo(console, db, reset, raw_dir, jd, out_dir)


if __name__ == "__main__":  # pragma: no cover
    app()
