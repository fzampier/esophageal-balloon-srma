# Subgroup analyses of the primary outcome.
#
# Protocol: "Subgroup analyses may be considered based on ARDS versus non-ARDS
# populations, obesity, timing of esophageal balloon initiation and differing
# transpulmonary pressure targets." The four classifications are the ones made
# once at extraction and reconciled (subgroup_flags.*), so this is a filter,
# not a re-read.
#
# With a handful of trials these are exploratory. For each variable the output
# gives the pooled RR within each level (DL, pooled only with at least 2
# studies), and, for variables where a study belongs to exactly one level, a
# test for subgroup differences from a random-effects meta-regression on the
# level. ptp_target_subgroups is multi-valued: a study applying two strategies
# counts in both levels, so levels are not independent and no difference test
# is run.

UNCLASSIFIED <- c("unclear", "not_reported")

SUBGROUP_VARS <- c(ards_subgroup = "ARDS vs non-ARDS", obesity_subgroup = "Obesity",
                   timing_subgroup = "Timing of balloon initiation",
                   ptp_target_subgroups = "Transpulmonary pressure target")

run_subgroups <- function(cfg, inv, studies) {
  out <- list()
  for (stratum in c("Randomised trials", "Observational studies")) {
    d <- inv[inv$pooled & inv$stratum == stratum, ]
    if (nrow(d) < 2) next
    d <- merge(d, studies[, c("study_id", names(SUBGROUP_VARS))], by = "study_id")
    for (v in names(SUBGROUP_VARS)) {
      multi <- v == "ptp_target_subgroups"
      lv <- if (multi) unique(trimws(unlist(strsplit(d[[v]], ";")))) else unique(d[[v]])
      lv <- lv[!is.na(lv) & lv != ""]
      for (l in lv) {
        member <- if (multi) vapply(strsplit(d[[v]], ";"), function(s) l %in% trimws(s), logical(1)) else d[[v]] %in% l
        x <- d[member, ]
        row <- data.frame(stratum = stratum, variable = SUBGROUP_VARS[[v]], level = l, k = nrow(x),
                          studies = paste(x$study_id, collapse = "; "), RR = NA, ci_low = NA, ci_high = NA,
                          I2 = NA, test_for_difference_p = NA, note = "")
        if (nrow(x) >= 2) {
          es <- escalc("RR", ai = ev_i, n1i = n_i, ci = ev_c, n2i = n_c, data = x)
          f <- rma(yi, vi, data = es, method = "DL")
          row[, c("RR", "ci_low", "ci_high", "I2")] <- c(exp(f$b[1]), exp(f$ci.lb), exp(f$ci.ub), f$I2)
        } else row$note <- "single study: not pooled"
        out[[paste(stratum, v, l)]] <- row
      }
      # A study the classification could not place ("unclear", "not_reported")
      # is listed in its own row but is not a subgroup: it stays out of the test.
      classified <- setdiff(lv, UNCLASSIFIED)
      if (!multi && length(classified) >= 2) {
        x <- d[d[[v]] %in% classified, ]
        es <- escalc("RR", ai = ev_i, n1i = n_i, ci = ev_c, n2i = n_c, data = x)
        mr <- try(rma(yi, vi, mods = ~ factor(x[[v]]), data = es, method = "DL"), silent = TRUE)
        if (!inherits(mr, "try-error")) {
          out[[paste(stratum, v, "test")]] <- data.frame(
            stratum = stratum, variable = SUBGROUP_VARS[[v]], level = "(test for subgroup differences)",
            k = nrow(x), studies = "", RR = NA, ci_low = NA, ci_high = NA, I2 = NA,
            test_for_difference_p = mr$QMp,
            note = "random-effects meta-regression on level, unclassified studies excluded; exploratory")
        }
      } else if (!multi) {
        out[[paste(stratum, v, "test")]] <- data.frame(
          stratum = stratum, variable = SUBGROUP_VARS[[v]], level = "(no difference test)", k = nrow(d),
          studies = "", RR = NA, ci_low = NA, ci_high = NA, I2 = NA, test_for_difference_p = NA,
          note = "fewer than two classified levels")
      } else if (multi) {
        out[[paste(stratum, v, "test")]] <- data.frame(
          stratum = stratum, variable = SUBGROUP_VARS[[v]], level = "(no difference test)", k = nrow(d),
          studies = "", RR = NA, ci_low = NA, ci_high = NA, I2 = NA, test_for_difference_p = NA,
          note = "multi-valued: a study can sit in two levels, so levels are not independent")
      }
    }
  }
  res <- if (length(out)) do.call(rbind, out) else data.frame()
  write_out(cfg, "subgroups_mortality", "subgroups.csv", res)
  res
}
