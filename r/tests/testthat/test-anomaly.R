library(testthat)

repo_root <- normalizePath(file.path(test_path(), "..", "..", ".."))
source(file.path(repo_root, "r", "anomaly_lib.R"))

make_frame <- function(scores, category = "SQL", start_id = 1) {
  n <- length(scores)
  data.frame(
    answer_id = seq(start_id, length.out = n),
    category = category,
    session_key = paste0("S", seq(start_id, length.out = n)),
    session_date = as.Date("2026-07-01") + seq_len(n) - 1,
    score = scores,
    stringsAsFactors = FALSE
  )
}

test_that("a big fall after enough history is flagged", {
  res <- rolling_zscores(make_frame(c(8, 9, 8, 9, 8, 9, 8, 2)))
  expect_equal(res$flagged, c(rep(FALSE, 7), TRUE))
  expect_lt(res$z[8], -2)
})

test_that("rolling mean uses only earlier answers", {
  res <- rolling_zscores(make_frame(c(8, 9, 8, 9, 8, 9, 8, 2)))
  expect_equal(res$roll_mean[6], mean(c(8, 9, 8, 9, 8)))
  expect_true(is.na(res$roll_mean[5]))  # only 4 earlier answers
})

test_that("minimum history, zero variance and small dips are not flagged", {
  expect_false(any(rolling_zscores(make_frame(c(8, 9, 8, 9, 1)))$flagged))
  expect_false(any(rolling_zscores(make_frame(c(rep(8, 6), 1)))$flagged))
  expect_false(any(rolling_zscores(make_frame(c(5, 9, 5, 9, 5, 9, 5, 4)))$flagged))
})

test_that("categories are independent and missing scores are ignored", {
  both <- rbind(make_frame(c(8, 9, 8, 9, 8, 9, 8, 2)),
                make_frame(c(2, 3, 2, 3, 2, 3), "Spark", 100))
  res <- rolling_zscores(both)
  expect_equal(unique(res$category[res$flagged]), "SQL")
  with_na <- rolling_zscores(make_frame(c(8, 9, NA, 8, 9, 8, 9, 8, 2)))
  expect_equal(nrow(with_na), 8)
  expect_equal(sum(with_na$flagged), 1)
  expect_equal(nrow(rolling_zscores(make_frame(c(NA_real_, NA_real_)))), 0)
})

test_that("Python and R flag the same sessions", {
  py_file <- file.path(repo_root, "output", "export", "anomalies.csv")
  session_file <- file.path(repo_root, "output", "export", "dim_session.csv")
  r_file <- file.path(repo_root, "output", "r_anomalies.csv")
  have_files <- all(file.exists(c(py_file, session_file, r_file)))
  if (!have_files && !nzchar(Sys.getenv("IPA_REQUIRE_PARITY"))) {
    skip("Run `ipa demo` and `Rscript r/anomaly_stats.R` first")
  }
  expect_true(have_files, info = "export and R output files must exist in CI")

  py <- read.csv(py_file, stringsAsFactors = FALSE)
  sessions <- read.csv(session_file, stringsAsFactors = FALSE)
  py_drops <- py[py$rule == "score_drop", ]
  py_keys <- sort(unique(sessions$session_key[match(py_drops$session_id, sessions$session_id)]))
  r_keys <- sort(unique(read.csv(r_file, stringsAsFactors = FALSE)$session_key))

  expect_gt(length(py_keys), 0)
  expect_equal(r_keys, py_keys)
  expect_equal(sort(read.csv(r_file)$answer_id), sort(py_drops$answer_id))
})
