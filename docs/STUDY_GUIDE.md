# Study guide: Interview Prep Analytics (30 minutes)

Plan: 5 min big picture, 8 min ETL, 7 min LLM logic, 7 min anomaly rules and R, 3 min questions.
Open each file next to this guide. Line numbers are from the current `main`; search for the
function name if they have moved.

## 0. Big picture (5 min)

Practice answers (JSON/CSV in `data/raw/`) -> validate -> star schema in PostgreSQL/SQLite ->
LLM scores each answer -> checks write an `anomalies` table -> Excel report, Streamlit dashboard,
and an R script that repeats one rule. Run it with `ipa demo --reset` (fake LLM, no key).

Say this in one sentence: "It is a practice-only analytics pipeline; I focused on safe loads,
validated LLM output and tested data-quality rules."

Key numbers: 30 sessions, 96 answers, 6 categories, 4 bad rows rejected, 16 anomalies,
110 pytest + 5 testthat tests, 99% coverage.

## 1. ETL: `src/ipa/schemas.py` and `src/ipa/ingest.py` (8 min)

**Validation (`schemas.py`)**
- `RawRecord` (line 34): the contract for one row. Read the `Field(...)` lines 37-43:
  `duration_sec` must be above 0 and at most 1800, `question` at least 10 characters,
  `answer` at least 3. `normalise_category` maps "Behavioral" and "Data Modeling" to the
  standard names and rejects unknown ones like "Java".
- Pydantic v2 raises `ValidationError` with a list of errors. We do not stop the load; we collect them.

**Rejected rows (`ingest.py`)**
- `validate_rows` (line 85): loops over rows, returns `(good, bad)`. A bad item keeps
  `row_number`, the `reason` text and the original `row`.
- `write_rejected` (line 101): writes `data/rejected/<file>.rejected.jsonl`. If a file becomes clean
  later, the old rejected file is deleted. An unreadable file (broken JSON) is also reported, not a crash.

**Star schema (`db.py`, `_DimCache` at `ingest.py:127`)**
- Dimensions are "get or create": look up by key, insert if missing, remember the id in a
  dictionary so a file with 100 rows does not run 100 identical lookups.
- `dim_date` stores `week_start` (the Monday) so weekly views need no date functions.

**Idempotent loads (`answer_key`, line 70, used in `load_records`, line 196)**
- Every answer gets `sha256(session_id + question hash + normalised answer)`.
- `fact_answers.answer_key` is `UNIQUE`. Before inserting, `load_records` checks the key
  (lines 201-205) and skips existing ones. Run the same data twice and you get 0 new rows.
- Same row in two different files is still loaded once.

**Incremental loads (`ingest_directory`, line 226)**
- `load_log` has `(file_name, file_hash)` unique. Lines 236-241: if the pair is already there,
  the file is skipped. A new file is loaded. A file with the same name but new content is loaded,
  and idempotency adds only the new rows.
- The whole file load and its `load_log` row are inside one `with engine.begin()` transaction
  (line 235), so a crash cannot leave a half-loaded file marked as done.

Try it: `ipa ingest` twice and watch "skipped (already loaded)".

## 2. LLM retry, fallback and cache: `src/ipa/llm/` (7 min)

**Providers (`providers.py`)**
- All providers have one method: `complete(system, prompt, model) -> text`. Anthropic, OpenAI,
  Gemini and Ollama (any OpenAI-compatible server) differ only in URL, headers and JSON shape.
- `_post` (line 48) sorts errors: timeouts, connection errors, HTTP 429 and 5xx become
  `RetryableError` (line 57). Other 4xx (bad key, unknown model) are normal `LLMError`: retrying cannot help.
- API keys go in headers, never in the URL, and never in logs.

**The client (`client.py`)**
- `_call_with_backoff` (line 100): retries only `RetryableError`, waiting `backoff * 2**retry`
  (line 110): 1s, 2s, 4s. After the last try it raises `LLMTransportError`.
- `complete_json` (line 117) is the main flow:
  1. Cache lookup (lines 120-129). Key = hash of provider + model + prompt (`cache_key`, line 96).
     A hit costs zero API calls (`attempts=0`).
  2. `models = [primary, fallback]` (line 131). For each model, try up to `max_retries + 1` times (line 137).
  3. If the reply is not valid JSON or fails the Pydantic schema, build a repair prompt
     (`repair_prompt`, line 160) that includes the error, and ask again.
  4. If transport fails or JSON stays bad, move to the fallback model (line 166).
  5. On success, store the raw valid text in `llm_cache` (line 163). Invalid answers are never cached.
  6. If everything fails: `LLMValidationError` "did not return valid JSON after N attempts" or
     `LLMTransportError`, with `error.attempts` set.
- `parse_json_loose` (`prompts.py`) removes code fences, text around the JSON and trailing commas
  before parsing.

**Strict result (`schemas.py`, `ScoreResult`, line ~52)**: score 1-10, non-empty `strengths`
and `improvements`, `improved_answer`.

**Workflow (`workflow.py`, `score_pending`)**: scores answers where `score IS NULL`. On failure it
saves `llm_valid = 0`, the error and the attempts, and continues. The next run retries them.

**Fake LLM (`llm/fake.py`)**: deterministic. Some prompts return bad JSON on the first try,
some never return valid JSON. That lets tests and the demo exercise retry, repair and failure
with no network.

## 3. The 4 anomaly rules: `src/ipa/checks.py` (7 min, plus R)

Constants at lines 31-35: window 10, minimum history 5, z threshold -2, pace limits 90 and 190.

1. **`score_drop`** (`score_drop_flags`, line 63; `detect_anomalies` line 95). Per category,
   sorted by date: `shift(1)` (line 78) so the current score is not in its own baseline, then
   `rolling(window=10, min_periods=5)`, `std(ddof=1)` (line 81). `z = (score - mean) / sd`; flag
   if `z < -2`. If sd is 0 there is no z, so no flag. The session of a flagged answer is flagged.
2. **LLM output** - `llm_validation_failed` (`llm_valid == 0`) and `llm_retry`
   (`llm_attempts > 1`). Shows how reliable the scorer is.
3. **`pace_outlier`** (line 146): `wpm < 90` or `wpm > 190`.
4. **`duplicate_answer`** (lines 162-179): normalise text (lowercase, no punctuation, single
   spaces), hash it, flag every later copy and point to the first one.

`run_checks` (line 183) deletes and rewrites the `anomalies` table, so it is safe to run again.

**The R script**
- `r/anomaly_lib.R`, `rolling_zscores` (line 9): the same rule. Line 25 takes the previous `window`
  scores, lines 32-33 compute `z` and the flag. R's `sd()` is also n-1, which is why the results match.
- `r/anomaly_stats.R`: reads Postgres with DBI + RPostgres if `IPA_PG_HOST` is set, otherwise the
  CSVs from `ipa export`. Joins at lines 58-61, calls the function at line 65, writes
  `output/r_anomalies.csv` (line 73) and three ggplot2 charts (`ggsave`, lines 91-110).
- Result on the sample data: both flag **S019, S020, S021, S022, S024** (5 answers).
  Tests: `r/tests/testthat/test-anomaly.R` and `tests/test_r_parity.py`.
- On Windows R is in `%LOCALAPPDATA%\Programs\R\R-4.6.1\bin`. Run `ipa demo --reset`, then
  `DATABASE_URL=sqlite:///data/demo.db ipa export`, then `Rscript r/anomaly_stats.R`.
  Remember: `ipa demo` uses `data/demo.db`, not `data/ipa.db`.

## 4. Eight likely interview questions

1. **What does idempotent mean here, and how did you do it?**
   Running a load twice gives the same result. Each answer has a content hash with a unique
   constraint, so existing rows are skipped. A `load_log` with file hashes also skips loaded files.
2. **Idempotent vs incremental?**
   Incremental means only process new data (skip files already in `load_log`). Idempotent means
   re-processing never creates duplicates. I use both: incremental for speed, idempotent for safety.
3. **How do you handle bad data?**
   Pydantic validates each row. Bad rows go to a rejected file with the row number, reason and
   original data; good rows still load. Nothing is dropped silently.
4. **Why a star schema?**
   Analytics queries are simple joins from one fact table to small dimensions, easy to aggregate
   by category, week or session. The grain is one row per answer.
5. **How do you make LLM output reliable?**
   Treat it as untrusted input: strict JSON schema with Pydantic, a repair retry that shows the
   error, exponential backoff for timeouts/429/5xx, a fallback model, a cache, and a clear error
   after the retries. Failures are stored (`llm_valid = 0`) and reported as anomalies.
6. **Why cache, and what do you cache?**
   Same prompt gives the same result without cost or latency. The key is a hash of provider,
   model and prompt; only valid responses are stored.
7. **Explain the score-drop rule. Why `shift(1)`?**
   Per category, compare each score to the mean and standard deviation of the previous 10 scores
   (at least 5). Flag when z is below -2. Shifting by one keeps the current score out of its own
   baseline; otherwise a big drop would hide itself by inflating the standard deviation.
8. **How do you know the Python and R versions agree? How do you test without a real API?**
   Same written rule (window, minimum history, sample sd, zero-variance case) and a parity test
   that compares flagged sessions; CI runs it with R on every push. For the LLM, tests use a
   deterministic fake and `httpx.MockTransport`, so no real API is ever called.

## Good to know (honest limits)

- The sample data is made up and the fake LLM scores by simple text rules, so scores show the
  pipeline works, not real answer quality.
- With one or two answers per week, the weekly trend is noisy; z-scores need history (5+ answers).
- This is a practice tool: no live-interview features.
