# Rolling z-score anomaly logic. This mirrors score_drop_flags() in src/ipa/checks.py,
# so keep the two definitions in sync (a test proves they flag the same sessions).
#
# Rule: inside each category, order answers by (session date, answer_id). For every answer take
# the previous `window` scores (not including the answer itself). If there are at least
# `min_history` of them, compute their mean and sample standard deviation (n - 1).
# z = (score - mean) / sd. An answer is flagged when z < threshold. sd == 0 gives no z-score.

rolling_zscores <- function(df, window = 10, min_history = 5, threshold = -2) {
  stopifnot(all(c("category", "session_date", "answer_id", "score") %in% names(df)))
  df <- df[!is.na(df$score), , drop = FALSE]
  if (nrow(df) == 0) {
    df$roll_mean <- numeric(0)
    df$roll_sd <- numeric(0)
    df$z <- numeric(0)
    df$flagged <- logical(0)
    return(df)
  }
  df <- df[order(df$category, df$session_date, df$answer_id), , drop = FALSE]
  out <- lapply(split(df, df$category), function(g) {
    n <- nrow(g)
    g$roll_mean <- NA_real_
    g$roll_sd <- NA_real_
    for (i in seq_len(n)) {
      prev <- g$score[max(1, i - window):(i - 1)]
      if (i > 1 && length(prev) >= min_history) {
        g$roll_mean[i] <- mean(prev)
        g$roll_sd[i] <- stats::sd(prev)
      }
    }
    sd_ok <- ifelse(!is.na(g$roll_sd) & g$roll_sd > 0, g$roll_sd, NA_real_)
    g$z <- (g$score - g$roll_mean) / sd_ok
    g$flagged <- !is.na(g$z) & g$z < threshold
    g
  })
  res <- do.call(rbind, out)
  rownames(res) <- NULL
  res[order(res$session_date, res$answer_id), , drop = FALSE]
}
