# Interview Prep Analytics

[![CI](https://github.com/Vaibhavpisute/Interview_Prep_Analytics/actions/workflows/ci.yml/badge.svg)](https://github.com/Vaibhavpisute/Interview_Prep_Analytics/actions/workflows/ci.yml)

A small data engineering project that turns my **mock-interview practice** into data:
it loads practice answers, scores them with an LLM, finds problems, and builds an Excel report
and a dashboard so I can see what to practise next.

> **This is a practice and analytics tool only.** It does not listen to live interviews, does not
> answer questions in real time, and has no overlay, hidden window or "stealth" feature. It does
> not help during a real interview. It only looks at answers I already wrote or recorded while
> practising.

## The problem

When I practise for Data Engineer interviews I get answers of very different quality and I forget
what I practised. I wanted to answer simple questions with data:

- Which categories (SQL, Python, Spark, ETL, Data Modelling, Behavioural) are my weakest?
- Is my score getting better week by week?
- Do I speak too fast or too slow, and how many filler words ("um", "basically") do I use?
- Did one bad session hide in the average? Did the LLM scorer misbehave?

It is also a portfolio project: it shows an ETL pipeline, a star schema, SQL views, an LLM
workflow with validation and retries, data quality checks, automation, tests and CI.

## Architecture

```mermaid
flowchart LR
    A["data/raw<br/>JSON + CSV files"] --> B["Validate<br/>Pydantic v2"]
    B -- bad rows --> R["data/rejected<br/>reason per row"]
    B -- good rows --> C[("PostgreSQL / SQLite<br/>star schema")]
    C --> D["LLM scoring<br/>Claude / OpenAI / Gemini / Ollama / fake"]
    D -- "score, strengths,<br/>improvements" --> C
    C --> E["SQL views"]
    C --> F["Checks<br/>4 rules"]
    F --> G[("anomalies table")]
    E --> H["Excel report"]
    G --> H
    E --> I["Streamlit dashboard"]
    G --> I
    C --> J["CSV export / DBI"] --> K["R analysis"]
```

More detail is in [docs/architecture.md](docs/architecture.md).

## Star schema

![Star schema](docs/erd.png)

- `fact_answers`: one row per answer, with the speech metrics (words per minute, filler counts)
  and the LLM result (score, strengths, improvements, improved answer, model, attempts).
- `dim_session`, `dim_question`, `dim_category`, `dim_date`: the dimensions.
- Support tables: `load_log` (which files were loaded), `llm_cache` (hash of prompt to result),
  `generated_questions`, and `anomalies`.
- SQL views (in [`src/ipa/sql/views.sql`](src/ipa/sql/views.sql)): score trend per category per
  week, weakest categories, average pace and filler rate per week, question repeat rate.

## How to run

You need Python 3.11 or newer. The demo needs **no API key and no database server**.

```bash
git clone https://github.com/Vaibhavpisute/Interview_Prep_Analytics.git
cd Interview_Prep_Analytics
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -e .

ipa demo --reset          # whole pipeline on the sample data, with a fake LLM
streamlit run src/ipa/dashboard/app.py   # dashboard (reads data/demo.db after the demo)
```

The demo creates `data/demo.db` (SQLite), loads the sample files, "scores" them with the
deterministic fake LLM, runs the checks and writes `output/weekly_report.xlsx` and CSV exports.

### With Docker and PostgreSQL

```bash
docker compose up                       # PostgreSQL + the demo
docker compose --profile dashboard up   # also the dashboard on http://localhost:8501
```

### With a real LLM (Claude, OpenAI, Gemini or Ollama)

Copy `.env.example` to `.env` and fill in only what you use. API keys are read from `.env` and
are never stored in the code.

| Provider | Setting in `.env` | Default model |
|---|---|---|
| Anthropic Claude (default) | `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=...` | `claude-sonnet-5-5` (fallback `claude-haiku-5-5`) |
| OpenAI | `LLM_PROVIDER=openai`, `OPENAI_API_KEY=...` | `gpt-4o-mini` (fallback `gpt-4o`) |
| Google Gemini | `LLM_PROVIDER=gemini`, `GEMINI_API_KEY=...` | `gemini-2.5-flash` (fallback `gemini-2.5-flash-lite`) |
| Ollama / any OpenAI-compatible server | `LLM_PROVIDER=ollama`, `OLLAMA_BASE_URL=http://localhost:11434/v1`, `LLM_MODEL=llama3.1` | `llama3.1` |

Set `LLM_MODEL` and `LLM_FALLBACK_MODEL` to use other models. Then:

```bash
ipa init-db                                  # tables + views (SQLite by default, see DATABASE_URL)
ipa ingest                                   # load new files from data/raw (skips loaded files)
ipa generate --jd data/jd/sample_jd.txt --n 10   # practice questions from a job description
ipa score                                    # score answers that have no valid score yet
ipa check                                    # anomaly and data quality checks
ipa weekly-report                            # output/weekly_report.xlsx
ipa export                                   # CSV files for the R analysis
```

`ipa generate` writes a practice sheet to `data/generated/`. Fill in `answer` and `duration_sec`,
copy it to `data/raw/` and run `ipa ingest`. Add `--provider fake` to any LLM command to stay
offline.

### Run the R analysis

```bash
ipa demo --reset
DATABASE_URL=sqlite:///data/demo.db ipa export     # PowerShell: $env:DATABASE_URL=...
Rscript -e "renv::restore()"                       # first time only
Rscript r/anomaly_stats.R                          # output/r_anomalies.csv + docs/r_charts/
```

A 30-minute walk through the code is in [docs/STUDY_GUIDE.md](docs/STUDY_GUIDE.md).

### Use PostgreSQL instead of SQLite

```bash
docker compose up -d db
export DATABASE_URL=postgresql+psycopg://ipa:ipa@localhost:5432/ipa   # PowerShell: $env:DATABASE_URL=...
ipa init-db && ipa ingest && ipa score --provider fake && ipa check
```

## Features

- **ETL.** Reads JSON and CSV, validates every row with Pydantic, writes bad rows to
  `data/rejected/` with the reason, and loads good rows into the star schema.
  Loads are **idempotent** (each answer has a content hash, so re-running never creates duplicates)
  and **incremental** (`load_log` stores the file name and hash, loaded files are skipped).
- **LLM workflow.** One client for Anthropic Claude, OpenAI, Gemini and OpenAI-compatible servers.
  It generates job-specific questions and scores answers with **strict JSON**
  (`score` 1-10, `strengths[]`, `improvements[]`, `improved_answer`) validated by Pydantic.
  It has timeouts, retries with exponential backoff, one fallback model, a response cache in the
  database and a clear error when the JSON is still invalid after the retries.
- **Speech metrics.** Words per minute and filler words, ported from my earlier TypeScript
  prototype with the same "so" and "like" rules.
- **Checks.** Four rules (five rule codes) write to the `anomalies` table:

  | Rule code | What it finds |
  |---|---|
  | `score_drop` | An answer scored more than 2 standard deviations below the rolling average of the earlier answers in the same category (previous 10 answers, at least 5 needed). The session is flagged. |
  | `llm_validation_failed`, `llm_retry` | LLM output that never became valid JSON, or needed more than one attempt. |
  | `pace_outlier` | Speaking pace below 90 or above 190 words per minute. |
  | `duplicate_answer` | The same answer text appears more than once. |

- **Automation.** `ipa weekly-report` builds an Excel file with the sheets *Summary*, *Trends*,
  *Weak Areas* and *Anomalies* and conditional formatting. A GitHub Actions workflow
  ([`weekly-report.yml`](.github/workflows/weekly-report.yml)) runs it every Monday on the sample
  data and uploads the Excel file as a build artifact.
- **Dashboard.** Streamlit app with score trends, weak areas, pace and fillers, and an anomalies
  table, with filters for category, date range and anomaly rule.
- **R analysis.** [`r/anomaly_stats.R`](r/anomaly_stats.R) re-computes the score-drop rule in R
  (rolling mean and standard deviation per category, z < -2) from PostgreSQL (DBI + RPostgres) or
  from the CSV exports, and draws ggplot2 charts. A test proves that **Python and R flag the same
  sessions**.

## Sample outputs

These come from the committed **made-up sample data**: 30 practice sessions, 96 answers, 6
categories, plus 4 deliberately invalid rows.

**`ipa demo`** (this image is rendered from the real console output of the command)

![ipa demo](docs/screenshots/cli_demo.png)

**Streamlit dashboard** (`docs/screenshots/dashboard.png`)

![Dashboard](docs/screenshots/dashboard.png)

**Weekly Excel report**: [docs/sample_weekly_report.xlsx](docs/sample_weekly_report.xlsx)
(sheets Summary, Trends, Weak Areas, Anomalies).

**R charts** (made by `r/anomaly_stats.R` in CI, saved from the build artifact)

| Score trend per category | Pace distribution |
|---|---|
| ![Score trend](docs/r_charts/score_trend_by_category.png) | ![Pace](docs/r_charts/pace_distribution.png) |

![Score anomalies](docs/r_charts/score_anomalies.png)

**CI**: all jobs pass (Python 3.11 and 3.12, PostgreSQL demo, Docker compose, R).

![CI green](docs/screenshots/ci_green.png)

## Tests and coverage

```bash
pytest --cov=ipa --cov-report=term-missing    # or: make test
ruff check . && ruff format --check .         # or: make lint
```

- **110 pytest tests** (109 run in the Python jobs, 1 Python-vs-R parity test runs in the R job)
  and **5 testthat tests** in `r/tests/`.
- **99% line coverage** of the `ipa` package (the full report is in
  [docs/coverage.txt](docs/coverage.txt); CI fails below 80%). The thin Streamlit page
  `dashboard/app.py` is excluded, its data logic is tested.
- Tests never call a real API: they use a deterministic **fake LLM** and `httpx.MockTransport`
  for the provider request/response shapes.
- Covered: Pydantic validation and rejected rows, idempotent and incremental loads, speech metrics
  (the TypeScript test cases are ported), JSON repair and retry logic, backoff and fallback,
  the cache, anomaly rules, SQL views, the Excel report (sheet names and row counts) and the CLI.

## Project layout

| Folder / file | What it does |
|---|---|
| `src/ipa/` | The Python package: `ingest`, `llm/`, `workflow`, `checks`, `report`, `export`, `cli`, `demo`, `dashboard/` |
| `src/ipa/sql/views.sql` | The analytics views (PostgreSQL and SQLite compatible) |
| `data/raw/` | Sample practice files (made up). `data/jd/` has a sample job description |
| `r/` | R analysis, testthat tests; `renv.lock` pins the R packages |
| `tests/` | pytest tests |
| `scripts/` | Sample data generator, ERD and screenshot helpers |
| `docs/` | Architecture, ERD, screenshots, R charts, sample report, coverage |
| `.github/workflows/` | CI and the weekly report |
| `Dockerfile`, `docker-compose.yml` | App and PostgreSQL |

## What I learned

- **Idempotent loads are mostly about keys.** A content hash per answer plus a `load_log` with
  file hashes made reruns safe, and testing "run it twice" caught real mistakes.
- **LLM output must be treated as untrusted input.** Pydantic validation, a repair retry, a
  fallback model and a cache made the scoring step predictable. A fake LLM that sometimes returns
  bad JSON let me test those paths without any API.
- **Portable SQL needs care.** The views run on SQLite and PostgreSQL, but things like
  `ROUND(double precision, n)` differ, so I round in pandas. Running the demo against a real
  PostgreSQL service in CI found this kind of problem early.
- **Define a rule once, then test it twice.** The score-drop rule exists in Python and in R.
  Writing the exact definition (window, minimum history, sample standard deviation, zero
  variance) and adding a parity test showed where the two implementations could drift.
- **Good evidence is automated.** The screenshots, ERD, coverage report and R charts are all
  generated by scripts or CI, so they match the code.

## Limits

- The sample data is made up and the fake LLM scores with simple text rules (length, numbers, an
  example, filler words). The numbers show that the pipeline works, not how good an answer is.
  Use a real provider for real feedback.
- I have not run `docker compose` on my own machine; it is built and run in CI on every push.
