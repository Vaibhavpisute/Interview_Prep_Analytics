"""Generate the made-up sample practice data in data/raw (deterministic, no personal data).

Run:  python scripts/make_sample_data.py
Each question is practised up to four times and the answers get better over time, with a few
deliberate bad days, speed outliers, duplicates and invalid rows so every check has something
to find.
"""

from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"

# (category, question, keyword, five sentences: direct answer, detail, reason, example, trade-off)
BANK: list[tuple[str, str, str, list[str]]] = [
    (
        "SQL",
        "How would you find the second highest salary in each department using SQL?",
        "a subquery",
        [
            "I would use a window function.",
            "I rank salaries inside each department with DENSE_RANK ordered by salary descending.",
            "Then I filter for rank two in an outer query, because window functions cannot be "
            "used directly in a WHERE clause.",
            "For example, on a table with 2 million employees this ran in under 3 seconds with "
            "an index on department and salary.",
            "DENSE_RANK handles ties correctly, which a plain LIMIT and OFFSET would not.",
        ],
    ),
    (
        "SQL",
        "Explain the difference between a clustered and a non-clustered index.",
        "an index",
        [
            "A clustered index decides the physical order of the rows, so a table can have "
            "only one.",
            "A non-clustered index is a separate structure that stores the key and a pointer back "
            "to the row.",
            "Because of that, a clustered index is fast for range scans, while non-clustered "
            "indexes help selective lookups.",
            "For example, an orders table clustered on order date made monthly reports 4 times "
            "faster, and a non-clustered index on customer id served the customer lookups.",
            "The trade-off is that every extra index slows down inserts and uses more storage.",
        ],
    ),
    (
        "SQL",
        "How do you debug a slow SQL query in production?",
        "an index",
        [
            "I start with the execution plan, not with guesses.",
            "I look for full table scans, large sorts and row estimates that are far from the "
            "real counts.",
            "This matters because stale statistics are a common reason why the planner picks a "
            "bad join order.",
            "For example, one report took 14 minutes, and after refreshing statistics and adding "
            "a composite index it took 40 seconds.",
            "I also check locking and the load on the server before I change any query.",
        ],
    ),
    (
        "SQL",
        "What is the difference between INNER JOIN, LEFT JOIN and FULL OUTER JOIN, and when "
        "would you use each?",
        "a join",
        [
            "INNER JOIN keeps only the rows that match on both sides.",
            "LEFT JOIN keeps every row from the left table and fills missing matches with NULL.",
            "FULL OUTER JOIN keeps unmatched rows from both sides, because sometimes I need to "
            "see what is missing on either side.",
            "For example, when I reconciled 500 thousand payments against bank records, a full "
            "outer join showed 1,200 rows that existed in only one system.",
            "I avoid it for normal reporting since it is easy to double count.",
        ],
    ),
    (
        "Python",
        "How do you handle memory when processing a CSV file that is larger than RAM in Python?",
        "pandas",
        [
            "I process the file in chunks instead of loading it all at once.",
            "With pandas I use the chunksize option, aggregate each chunk and combine the small "
            "results at the end.",
            "This works because most reports need only totals, not every raw row in memory.",
            "For example, a 12 GB log file was reduced to a 40 MB summary on a laptop with 8 GB "
            "of RAM.",
            "For repeated work I would convert the file to Parquet, which is smaller and faster "
            "to read.",
        ],
    ),
    (
        "Python",
        "What is the difference between a generator and a list, and when would you use a "
        "generator?",
        "a list",
        [
            "A list stores all its items in memory, while a generator produces one item at a time.",
            "I use a generator when the data is large or when I only need to loop over it once.",
            "That keeps memory flat, because only the current item exists at any moment.",
            "For example, reading a 5 million line file with a generator used about 30 MB instead "
            "of 2 GB.",
            "The trade-off is that a generator can be used only once and has no length or index.",
        ],
    ),
    (
        "Python",
        "How do you write unit tests for a data transformation function?",
        "pytest",
        [
            "I keep the function pure so it takes a DataFrame and returns a DataFrame.",
            "Then I write small pytest cases with tiny hand-made inputs and the exact expected "
            "output.",
            "I include edge cases such as empty input, nulls and duplicates, because those break "
            "pipelines first.",
            "For example, a test with 6 rows caught a bug where nulls were silently dropped in a "
            "join.",
            "I run the tests in CI on every push so a broken change never reaches production.",
        ],
    ),
    (
        "Python",
        "Explain how you would make an API ingestion script resilient to failures.",
        "a retry",
        [
            "I add timeouts, retries with exponential backoff and clear logging.",
            "I only retry errors that can recover, such as timeouts, 429 and 5xx responses.",
            "This is important because blind retries on a 400 error only waste time and hide the "
            "real bug.",
            "For example, a nightly job that failed about twice a week became stable after "
            "adding 3 retries with a 2 second backoff.",
            "I also make the load idempotent so a rerun never creates duplicates.",
        ],
    ),
    (
        "Spark",
        "What causes data skew in a Spark join and how do you fix it?",
        "a broadcast",
        [
            "Skew happens when a few keys hold most of the rows, so one task does most of "
            "the work.",
            "I find it in the Spark UI by looking for tasks that run far longer than the others.",
            "I fix it by salting the hot keys or by broadcasting the smaller table, because that "
            "spreads the work evenly.",
            "For example, salting a customer key with 20 buckets reduced a join from 50 minutes "
            "to 9 minutes.",
            "Adaptive query execution can also split skewed partitions, but I still check the "
            "plan.",
        ],
    ),
    (
        "Spark",
        "Explain the difference between narrow and wide transformations in Spark.",
        "a shuffle",
        [
            "A narrow transformation needs data from only one input partition, like map or filter.",
            "A wide transformation needs data from many partitions, like groupBy or join, and "
            "causes a shuffle.",
            "This matters because a shuffle moves data over the network and is the most "
            "expensive part of a job.",
            "For example, moving a filter before a join cut the shuffled data from 300 GB to "
            "40 GB.",
            "So I try to use narrow steps first and keep the number of wide steps small.",
        ],
    ),
    (
        "Spark",
        "When would you use repartition versus coalesce?",
        "a partition",
        [
            "I use repartition to increase partitions or to spread data evenly, and coalesce to "
            "reduce partitions cheaply.",
            "Repartition does a full shuffle, while coalesce merges existing partitions without "
            "one.",
            "I use coalesce before writing, because thousands of tiny files slow down every "
            "later read.",
            "For example, writing 4,000 small files was changed to 64 files of about 128 MB each, "
            "and downstream reads became 3 times faster.",
            "The trade-off is that coalesce can leave uneven partitions.",
        ],
    ),
    (
        "Spark",
        "How do you choose between a broadcast join and a sort merge join?",
        "a broadcast",
        [
            "I use a broadcast join when one side is small enough to fit in executor memory.",
            "It avoids shuffling the large table, while a sort merge join shuffles and sorts both "
            "sides.",
            "That is why broadcast is much faster for a large fact table joined to a small "
            "dimension.",
            "For example, broadcasting a 50 MB dimension against a 2 TB fact table cut a job from "
            "35 to 6 minutes.",
            "If the small side grows too big, I switch back to sort merge to avoid memory errors.",
        ],
    ),
    (
        "ETL",
        "How do you make an ETL pipeline idempotent?",
        "an upsert",
        [
            "I make sure that running the same load twice gives the same result.",
            "I use natural keys or content hashes with an upsert, and I load each batch inside "
            "one transaction.",
            "This is useful because jobs fail and get rerun, and duplicates in a fact table are "
            "hard to clean up.",
            "For example, after I added a hash key, a rerun of 80 thousand rows created zero "
            "duplicates.",
            "I also keep a load log, so I know which files were already processed.",
        ],
    ),
    (
        "ETL",
        "How would you design incremental loads for a table without a reliable updated-at column?",
        "a full load",
        [
            "I would compare row hashes between the source and the target.",
            "I compute a hash of the business columns, then insert new keys, update changed "
            "hashes and flag missing keys.",
            "I prefer this to a full reload because it is cheaper and it gives me an audit "
            "trail of changes.",
            "For example, on a 3 million row customer table only about 15 thousand rows "
            "changed each day, so the load dropped from 25 to 3 minutes.",
            "If the source supports change data capture, I would use that instead.",
        ],
    ),
    (
        "ETL",
        "What data quality checks do you add to a pipeline?",
        "a check",
        [
            "I check completeness, uniqueness, validity and freshness.",
            "Examples are row counts against the source, not-null and unique keys, allowed value "
            "ranges and the latest load time.",
            "I send bad rows to a rejected table with the reason, because silent drops hide "
            "problems.",
            "For example, a check on the order total caught a currency bug that affected 2 "
            "percent of rows before the dashboard was updated.",
            "Critical checks stop the pipeline and the others raise an alert.",
        ],
    ),
    (
        "ETL",
        "How do you handle late arriving data in a batch pipeline?",
        "a reload",
        [
            "I design the pipeline to reprocess a sliding window, not only the latest day.",
            "Each run reloads the last 3 days by partition, and the load is idempotent so "
            "reprocessing is safe.",
            "This works because late records usually arrive within a short and known delay.",
            "For example, mobile events arrived up to 36 hours late, and the 3 day window "
            "recovered 99.8 percent of them.",
            "Anything later goes to a backfill job that I run on demand.",
        ],
    ),
    (
        "Data Modelling",
        "What is the difference between a star schema and a snowflake schema?",
        "a dimension",
        [
            "A star schema has a central fact table joined to flat, denormalised dimensions.",
            "A snowflake schema splits those dimensions into several related tables.",
            "I usually choose the star schema because queries need fewer joins and are easier "
            "for analysts to write.",
            "For example, a sales model with 5 flat dimensions ran dashboard queries in under 2 "
            "seconds.",
            "Snowflaking saves some storage and avoids repeated values, but it makes queries "
            "slower and more complex.",
        ],
    ),
    (
        "Data Modelling",
        "How would you model a slowly changing dimension Type 2?",
        "a dimension",
        [
            "I keep history by adding a new row each time a tracked attribute changes.",
            "Each row has a surrogate key, valid from and valid to dates and a current flag.",
            "I use a surrogate key because the business key repeats across versions.",
            "For example, when a customer moved city, old orders still joined to the old city "
            "and new orders joined to the new one.",
            "The trade-off is a bigger dimension and a more careful load process.",
        ],
    ),
    (
        "Data Modelling",
        "When would you denormalize a table?",
        "a table",
        [
            "I denormalise when read speed matters more than storage and update simplicity.",
            "That is common in reporting layers, where the same joins run again and again.",
            "I only do it after measuring, because denormalised data can drift out of sync.",
            "For example, copying the product category into a fact table removed one join and "
            "cut a report from 12 to 5 seconds.",
            "I keep the normalised source of truth and rebuild the denormalised table from it.",
        ],
    ),
    (
        "Data Modelling",
        "How do you choose the grain of a fact table?",
        "a fact table",
        [
            "I choose the lowest level of detail that the business questions need.",
            "I write the grain as one sentence, for example one row per answer in a session.",
            "I do this first because every measure and dimension must be true at that grain.",
            "For example, an order table at order level could not answer product questions, so "
            "we moved to order line level and added 1 key.",
            "A fine grain is flexible, and I can always aggregate it up later.",
        ],
    ),
    (
        "Behavioural",
        "Tell me about a time a data pipeline you owned failed in production.",
        "a fix",
        [
            "A nightly sales pipeline failed because a source system changed a column type.",
            "I found it from the alert, stopped the downstream refresh and told the finance team "
            "that the numbers would be late.",
            "I did this because a wrong dashboard is worse than a late one.",
            "For example, I fixed the cast in 40 minutes, reran the load, and the report was "
            "ready before 9 am.",
            "Afterwards I added a schema check and a runbook, and the same problem did not come "
            "back.",
        ],
    ),
    (
        "Behavioural",
        "Describe a disagreement with a teammate and how you resolved it.",
        "a talk",
        [
            "A teammate and I disagreed about using a full reload or an incremental load.",
            "I listened to the concerns first, then suggested we test both on a copy of the data.",
            "I proposed a test because it moved the discussion from opinions to numbers.",
            "For example, the incremental load was 8 times faster, and we agreed to use it with "
            "a weekly full check.",
            "We stayed on good terms, and the team kept the benchmark script for later decisions.",
        ],
    ),
    (
        "Behavioural",
        "Tell me about a time you had to learn a new tool quickly.",
        "a course",
        [
            "I had to learn dbt in two weeks to take over a reporting project.",
            "I read the documentation, built a small model of my own and asked a teammate to "
            "review it.",
            "That worked because a real example teaches faster than reading alone.",
            "For example, by the end of week two I had moved 12 SQL scripts into tested dbt "
            "models.",
            "I also wrote a short guide so the next person could start faster.",
        ],
    ),
    (
        "Behavioural",
        "How do you prioritise when stakeholders send you conflicting requests?",
        "a list",
        [
            "I bring the stakeholders together and make the trade-offs visible.",
            "I estimate effort and impact for each request and share one ranked list.",
            "I do this because most conflicts come from hidden assumptions, not from bad "
            "intentions.",
            "For example, two teams asked for different reports in the same week, and a "
            "15 minute call showed one report could cover 80 percent of both needs.",
            "If they still disagree, I ask my manager to decide, and I document the decision.",
        ],
    ),
]

WEAK_TEMPLATES = [
    "Um, so basically I would, like, just use {kw} and, uh, it works, you know.",
    "Uh, I think, like, you just use {kw} and basically that is it.",
    "So, um, actually I am not sure, I would probably use {kw} or something.",
    "Hmm, I mean, it is kind of simple, you just use {kw} and, um, check it.",
]
JOB_TITLES = ["Data Engineer", "Senior Data Engineer", "Analytics Engineer"]
START = date(2026, 7, 6)  # a Monday
N_WEEKS = 10
SESSION_SIZES = [3, 3, 3, 3, 4] * 6  # 30 sessions, 96 answers


STAGES = [1, 2, 3, 4]  # the four practice rounds of every question


def stage_text(q_index: int, stage: int, rng: random.Random) -> str:
    """0 = weak (bad day), 1..4 = four different, increasingly complete answers."""
    _, _, kw, sentences = BANK[q_index]
    if stage == 0:
        return rng.choice(WEAK_TEMPLATES).format(kw=kw)
    picks = {1: [0, 1, 2], 2: [0, 1, 2, 3], 3: [0, 1, 2, 3, 4], 4: [0, 1, 3, 4]}[stage]
    return " ".join(sentences[i] for i in picks)


def build_rows(seed: int = 7) -> list[dict[str, object]]:
    rng = random.Random(seed)
    items: list[tuple[float, int, int]] = []  # (time key, question index, stage)
    for qi in range(len(BANK)):
        # Each question is practised four times; later practice overlaps a little in time.
        keys = sorted(rng.uniform(0, 34) + i * 12 for i in range(len(STAGES)))
        for stage, key in zip(STAGES, keys, strict=True):
            items.append((key, qi, stage))
    items.sort()
    total = sum(SESSION_SIZES)
    items = items[:total]

    # Deliberate bad days: a few late answers fall back to the weak style.
    late = [i for i, it in enumerate(items) if i > total * 0.55]
    for i in rng.sample(late, k=6):
        key, qi, _ = items[i]
        items[i] = (key, qi, 0)

    rows: list[dict[str, object]] = []
    pos = 0
    for s_idx, size in enumerate(SESSION_SIZES):
        week, slot = divmod(s_idx, 3)
        d = START + timedelta(weeks=week, days=[0, 2, 4][slot])
        title = JOB_TITLES[(s_idx // 3) % 3]
        for _ in range(size):
            _, qi, stage = items[pos]
            pos += 1
            category, question, _kw, _ = BANK[qi]
            answer = stage_text(qi, stage, rng)
            words = len(answer.split())
            wpm = min(170, max(105, rng.gauss(135, 16)))
            rows.append(
                {
                    "session_id": f"S{s_idx + 1:03d}",
                    "session_date": d.isoformat(),
                    "job_title": title,
                    "category": category,
                    "question": question,
                    "answer": answer,
                    "duration_sec": max(4, round(words / wpm * 60)),
                }
            )

    # Pace outliers: one too fast (> 190 wpm), two too slow (< 90 wpm).
    for idx, target in [(40, 215), (63, 70), (81, 78)]:
        words = len(str(rows[idx]["answer"]).split())
        rows[idx]["duration_sec"] = max(2, round(words / target * 60))
    # Duplicate answers: the same text pasted again in a later session.
    for src, dst in [(30, 77), (52, 90)]:
        rows[dst]["question"] = rows[src]["question"]
        rows[dst]["category"] = rows[src]["category"]
        rows[dst]["answer"] = rows[src]["answer"]
        rows[dst]["duration_sec"] = rows[src]["duration_sec"]
    return rows


def write_files(rows: list[dict[str, object]]) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    by_month: dict[str, list[dict[str, object]]] = {}
    for r in rows:
        by_month.setdefault(str(r["session_date"])[:7], []).append(r)
    months = sorted(by_month)
    # July and September as JSON, August as CSV (with a few invalid rows added).
    for m in months:
        if m == "2026-08":
            continue
        (RAW / f"sessions_{m.replace('-', '_')}.json").write_text(
            json.dumps(by_month[m], indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    fields = list(rows[0])
    bad_rows = [
        {**rows[0], "session_id": "S900", "category": "Java"},  # unknown category
        {**rows[1], "session_id": "S901", "answer": ""},  # empty answer
        {**rows[2], "session_id": "S902", "duration_sec": -5},  # negative duration
        {**rows[3], "session_id": "S903", "session_date": "31/02/2026"},  # impossible date
    ]
    with (RAW / "sessions_2026_08.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(by_month["2026-08"])
        w.writerows(bad_rows)


if __name__ == "__main__":
    data = build_rows()
    write_files(data)
    print(f"wrote {len(data)} answers in {len({r['session_id'] for r in data})} sessions")
