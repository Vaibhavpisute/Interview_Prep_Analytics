-- Analytics views. Written to run unchanged on PostgreSQL and SQLite.
-- Rounding is done in Python/pandas because ROUND(double precision, n) does not exist in PostgreSQL.

-- 1. Score trend per category over time (one row per week and category).
CREATE VIEW v_score_trend_by_category AS
SELECT
    d.week_start                 AS week_start,
    c.name                       AS category,
    AVG(f.score * 1.0)           AS avg_score,
    MIN(f.score)                 AS min_score,
    MAX(f.score)                 AS max_score,
    COUNT(*)                     AS answers
FROM fact_answers f
JOIN dim_date d     ON d.date_id = f.date_id
JOIN dim_category c ON c.category_id = f.category_id
WHERE f.score IS NOT NULL
GROUP BY d.week_start, c.name;

-- 2. Weakest categories first.
CREATE VIEW v_weakest_categories AS
SELECT
    c.name                       AS category,
    AVG(f.score * 1.0)           AS avg_score,
    COUNT(*)                     AS answers,
    SUM(CASE WHEN f.score <= 5 THEN 1 ELSE 0 END) AS low_scores
FROM fact_answers f
JOIN dim_category c ON c.category_id = f.category_id
WHERE f.score IS NOT NULL
GROUP BY c.name
ORDER BY AVG(f.score * 1.0) ASC;

-- 3. Average pace and filler rate (fillers per 100 words) per week.
CREATE VIEW v_weekly_delivery AS
SELECT
    d.week_start                 AS week_start,
    AVG(f.wpm * 1.0)             AS avg_wpm,
    100.0 * SUM(f.filler_count) / NULLIF(SUM(f.word_count), 0) AS fillers_per_100_words,
    COUNT(*)                     AS answers
FROM fact_answers f
JOIN dim_date d ON d.date_id = f.date_id
GROUP BY d.week_start;

-- 4. How often each question was practised.
CREATE VIEW v_question_repeat AS
SELECT
    q.question_id                AS question_id,
    q.question_text              AS question_text,
    c.name                       AS category,
    COUNT(*)                     AS times_asked,
    COUNT(DISTINCT f.session_id) AS sessions,
    MIN(d.full_date)             AS first_asked,
    MAX(d.full_date)             AS last_asked
FROM fact_answers f
JOIN dim_question q ON q.question_id = f.question_id
JOIN dim_category c ON c.category_id = q.category_id
JOIN dim_date d     ON d.date_id = f.date_id
GROUP BY q.question_id, q.question_text, c.name;

-- 5. Question repeat rate: share of distinct questions that were practised more than once.
CREATE VIEW v_repeat_rate_summary AS
SELECT
    COUNT(*)                                          AS distinct_questions,
    SUM(CASE WHEN times_asked > 1 THEN 1 ELSE 0 END)  AS repeated_questions,
    100.0 * SUM(CASE WHEN times_asked > 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0)
                                                      AS repeat_rate_pct
FROM v_question_repeat
