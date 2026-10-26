# Shared loading and shaping for the meta-analyses.
#
# Everything here reads the CSVs flatten.py writes. By default that is
# analysis/derived/, built from extraction/reconciled/ -- the only data allowed
# to produce results. --provisional points at analysis/derived/provisional/,
# merges with unsettled differences resolved to GLM's reading, which exist only
# so this code can be written and tested before adjudication finishes. Every
# output made from them is stamped PROVISIONAL and written to a separate tree.

suppressPackageStartupMessages(library(metafor))

parse_args <- function(args = commandArgs(trailingOnly = TRUE)) {
  provisional <- "--provisional" %in% args
  root <- normalizePath(file.path(dirname(sys_script()), "..", ".."), mustWork = TRUE)
  data <- if (provisional) file.path(root, "analysis", "derived", "provisional", "csv")
          else file.path(root, "analysis", "derived")
  out <- if (provisional) file.path(root, "analysis", "derived", "provisional", "results")
         else file.path(root, "analysis", "results")
  list(root = root, data = data, out = out, provisional = provisional,
       stamp = if (provisional) "PROVISIONAL - unadjudicated merge, NOT FOR RESULTS" else "reconciled data")
}

sys_script <- function() {
  f <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE))
  if (length(f)) f[1] else file.path(getwd(), "analysis", "meta", "run.R")
}

load_tables <- function(cfg) {
  need <- c("studies.csv", "outcomes.csv", "effects.csv", "paired.csv")
  missing <- need[!file.exists(file.path(cfg$data, need))]
  if (length(missing))
    stop("no flattened data in ", cfg$data, " (missing ", paste(missing, collapse = ", "),
         "). Run analysis/flatten.py first.", call. = FALSE)
  rd <- function(f) read.csv(file.path(cfg$data, f), stringsAsFactors = FALSE,
                             na.strings = c("", "NA"), encoding = "UTF-8")
  list(studies = rd("studies.csv"), outcomes = rd("outcomes.csv"),
       effects = rd("effects.csv"), paired = rd("paired.csv"))
}

truthy <- function(x) !is.na(x) & toupper(as.character(x)) %in% c("TRUE", "1", "YES")

# One report per cohort. A report sharing patients with another contributes only
# if it is the cohort's primary report; secondary reports (sarge_2021 for
# EPVent-2, baedorf_kassis_2016 for EPVent-1) are listed, never pooled, so no
# patient is counted twice. The exception flatten.py mentions -- a secondary
# report of an outcome the primary lacks -- is decided per analysis, by name.
poolable_reports <- function(studies) {
  in_cohort <- !is.na(studies$cohort_id) & studies$cohort_id != ""
  studies$pool_role <- ifelse(!in_cohort, "independent",
                              ifelse(truthy(studies$is_primary_report), "primary of cohort",
                                     "secondary report - not pooled"))
  studies
}

# Protocol: "Analysis will be separated between RCT and observational studies."
design_stratum <- function(design) {
  ifelse(design == "rct", "Randomised trials",
         ifelse(design %in% c("prospective_cohort", "retrospective_cohort", "case_control"),
                "Observational studies", "Other designs - not pooled"))
}

# Two-arm contrast from arm-level rows: the intervention arm against the
# comparator arm, by the roles recorded in arms[]. Studies with further arms
# (alternative titration conditions) contribute only this contrast, and the
# dropped arms are reported.
two_arm_rows <- function(rows) {
  iv <- rows[rows$arm_role == "intervention", , drop = FALSE]
  cc <- rows[rows$arm_role == "comparator", , drop = FALSE]
  other <- setdiff(unique(rows$arm_label), c(iv$arm_label, cc$arm_label))
  list(ok = nrow(iv) == 1 && nrow(cc) == 1, intervention = iv, comparator = cc,
       dropped_arms = paste(other, collapse = "; "))
}

stamp_plot <- function(cfg) {
  if (cfg$provisional)
    mtext(cfg$stamp, side = 3, line = 0.2, col = "red", font = 2, cex = 0.9)
}

write_out <- function(cfg, analysis, name, df) {
  d <- file.path(cfg$out, analysis)
  dir.create(d, recursive = TRUE, showWarnings = FALSE)
  write.csv(df, file.path(d, name), row.names = FALSE, na = "")
  file.path(d, name)
}
