#!/usr/bin/env Rscript
# Per-category rolling mean / standard deviation of scores, z-score anomalies (z < -2) and charts.
#
# Data source:
#   * PostgreSQL via DBI + RPostgres when IPA_PG_HOST is set
#     (also IPA_PG_PORT, IPA_PG_DB, IPA_PG_USER, IPA_PG_PASSWORD)
#   * otherwise the CSV files written by `ipa export` (folder IPA_EXPORT_DIR, default output/export)
#
# Output: output/r_anomalies.csv (override with IPA_R_OUT) and ggplot2 charts in
# docs/r_charts/ (override with IPA_R_CHARTS).
#
# Run from the repository root:  Rscript r/anomaly_stats.R

suppressPackageStartupMessages({
  library(dplyr)
  library(readr)
  library(ggplot2)
})

script_dir <- local({
  args <- commandArgs(trailingOnly = FALSE)
  file_arg <- sub("^--file=", "", args[grep("^--file=", args)])
  if (length(file_arg) == 1) dirname(normalizePath(file_arg)) else "r"
})
source(file.path(script_dir, "anomaly_lib.R"))

out_csv <- Sys.getenv("IPA_R_OUT", "output/r_anomalies.csv")
chart_dir <- Sys.getenv("IPA_R_CHARTS", "docs/r_charts")
tables <- c("fact_answers", "dim_session", "dim_category", "dim_date")

read_tables <- function() {
  pg_host <- Sys.getenv("IPA_PG_HOST", "")
  if (nzchar(pg_host)) {
    message("Reading tables from PostgreSQL at ", pg_host)
    con <- DBI::dbConnect(
      RPostgres::Postgres(),
      host = pg_host,
      port = as.integer(Sys.getenv("IPA_PG_PORT", "5432")),
      dbname = Sys.getenv("IPA_PG_DB", "ipa"),
      user = Sys.getenv("IPA_PG_USER", "ipa"),
      password = Sys.getenv("IPA_PG_PASSWORD", "ipa")
    )
    on.exit(DBI::dbDisconnect(con), add = TRUE)
    return(setNames(lapply(tables, function(t) DBI::dbReadTable(con, t)), tables))
  }
  export_dir <- Sys.getenv("IPA_EXPORT_DIR", "output/export")
  message("Reading CSV exports from ", export_dir)
  setNames(lapply(tables, function(t) {
    path <- file.path(export_dir, paste0(t, ".csv"))
    if (!file.exists(path)) stop("Missing ", path, ". Run `ipa export` first.", call. = FALSE)
    suppressMessages(read_csv(path, show_col_types = FALSE))
  }), tables)
}

tabs <- read_tables()
answers <- tabs$fact_answers |>
  select(answer_id, session_id, category_id, date_id, score, wpm) |>
  inner_join(select(tabs$dim_session, session_id, session_key), by = "session_id") |>
  inner_join(rename(select(tabs$dim_category, category_id, name), category = name),
             by = "category_id") |>
  inner_join(select(tabs$dim_date, date_id, full_date, week_start), by = "date_id") |>
  mutate(session_date = as.Date(full_date), week_start = as.Date(week_start),
         score = as.numeric(score))

scored <- rolling_zscores(as.data.frame(answers), window = 10, min_history = 5, threshold = -2)
flagged <- scored |>
  filter(flagged) |>
  arrange(session_date, answer_id) |>
  transmute(session_key, answer_id, category, session_date, score,
            roll_mean = round(roll_mean, 3), roll_sd = round(roll_sd, 3), z = round(z, 3))

dir.create(dirname(out_csv), recursive = TRUE, showWarnings = FALSE)
write_csv(flagged, out_csv)
message(sprintf("%d answers flagged in %d sessions -> %s",
                nrow(flagged), n_distinct(flagged$session_key), out_csv))

# ---- charts ----------------------------------------------------------------------------------
dir.create(chart_dir, recursive = TRUE, showWarnings = FALSE)
theme_set(theme_minimal(base_size = 12))

weekly <- scored |>
  group_by(week_start, category) |>
  summarise(avg_score = mean(score), .groups = "drop")

p_trend <- ggplot(weekly, aes(week_start, avg_score, colour = category)) +
  geom_line(linewidth = 0.9) +
  geom_point(size = 1.6) +
  scale_y_continuous(limits = c(1, 10), breaks = 1:10) +
  labs(title = "Average score per week by category", x = "Week", y = "Average score (1-10)",
       colour = "Category", caption = "Practice sessions, made-up sample data")
ggsave(file.path(chart_dir, "score_trend_by_category.png"), p_trend,
       width = 9, height = 5, dpi = 130)

p_pace <- ggplot(filter(answers, !is.na(wpm)), aes(wpm)) +
  geom_histogram(binwidth = 10, fill = "#4C78A8", colour = "white") +
  geom_vline(xintercept = c(90, 190), linetype = "dashed", colour = "#E45756") +
  labs(title = "Speaking pace distribution", subtitle = "Dashed lines: outlier limits (90 and 190 wpm)",
       x = "Words per minute", y = "Answers")
ggsave(file.path(chart_dir, "pace_distribution.png"), p_pace, width = 8, height = 4.5, dpi = 130)

p_flags <- ggplot(scored, aes(session_date, score)) +
  geom_line(aes(group = category), colour = "grey70") +
  geom_point(aes(colour = flagged, size = flagged)) +
  scale_colour_manual(values = c(`FALSE` = "#4C78A8", `TRUE` = "#E45756"),
                      labels = c("normal", "z < -2")) +
  scale_size_manual(values = c(`FALSE` = 1.4, `TRUE` = 3), guide = "none") +
  facet_wrap(~category) +
  labs(title = "Scores per category with z-score anomalies", x = "Session date", y = "Score",
       colour = NULL)
ggsave(file.path(chart_dir, "score_anomalies.png"), p_flags, width = 10, height = 6, dpi = 130)
message("Charts written to ", chart_dir)
