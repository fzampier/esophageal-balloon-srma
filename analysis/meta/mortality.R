# Primary analysis: mortality at the longest reported follow-up.
#
# Protocol: "Mortality at the longest available follow-up will be analysed using
# a random effects model with a DerSimonian-Laird tau estimator if at least 3
# trials report this outcome." RCTs and observational studies are pooled
# separately, never together.
#
# Inputs, exactly as extracted and adjudicated:
#   - the outcome entry flagged is_longest_reported_mortality, one per study;
#   - parallel-arm studies only: a within-patient or single-group study has no
#     concurrent comparator for mortality and is listed, not pooled;
#   - the intervention arm against the comparator arm, events over total.
#
# Effect measure: risk ratio (the protocol allows RR or OR; RR is primary here,
# OR is reported as a sensitivity analysis). Zero cells get the 0.5 continuity
# correction only where needed (metafor's default). Hartung-Knapp-Sidik-Jonkman
# intervals are reported alongside DL as a sensitivity analysis: with few trials
# the DL interval is known to be too narrow. REML is reported as a sensitivity
# analysis for the between-study variance: with few trials DL often truncates
# tau2 to zero (decision D22). None of these sensitivity analyses is
# pre-specified in the protocol, and the output says so.

run_mortality <- function(cfg, tb) {
  st <- poolable_reports(tb$studies)
  st$stratum <- design_stratum(st$study_design)
  o <- tb$outcomes[truthy(tb$outcomes$is_longest_reported_mortality), ]

  rows <- list()
  for (sid in unique(o$study_id)) {
    s <- st[st$study_id == sid, ]
    r <- o[o$study_id == sid, ]
    base <- data.frame(study_id = sid, design = s$study_design, stratum = s$stratum,
                       structure = s$comparison_structure, pool_role = s$pool_role,
                       outcome = r$name_verbatim[1],
                       timepoint = paste0(r$timepoint_type[1],
                                          ifelse(is.na(r$timepoint_days[1]), "",
                                                 paste0(" ", r$timepoint_days[1], " d"))),
                       stringsAsFactors = FALSE)
    ta <- two_arm_rows(r)
    reason <- ""
    if (s$comparison_structure != "parallel_arms") {
      reason <- "no concurrent comparator (within-patient or single-group design)"
    } else if (s$pool_role == "secondary report - not pooled") {
      reason <- "secondary report of a cohort; its primary report is pooled"
    } else if (!ta$ok) {
      reason <- "no single intervention and comparator arm"
    } else if (s$stratum == "Other designs - not pooled") {
      reason <- paste("design", s$study_design, "is neither RCT nor observational cohort")
    } else if (any(is.na(c(ta$intervention$events, ta$intervention$total,
                           ta$comparator$events, ta$comparator$total)))) {
      reason <- "events or denominator missing"
    }
    base$ev_i <- if (ta$ok) ta$intervention$events else NA
    base$n_i <- if (ta$ok) ta$intervention$total else NA
    base$ev_c <- if (ta$ok) ta$comparator$events else NA
    base$n_c <- if (ta$ok) ta$comparator$total else NA
    base$dropped_arms <- ta$dropped_arms
    base$pooled <- reason == ""
    base$not_pooled_because <- reason
    rows[[sid]] <- base
  }
  inv <- do.call(rbind, rows)
  inv <- inv[order(inv$stratum, !inv$pooled, inv$study_id), ]
  write_out(cfg, "mortality", "study_inventory.csv", inv)

  summaries <- list()
  for (stratum in c("Randomised trials", "Observational studies")) {
    d <- inv[inv$pooled & inv$stratum == stratum, ]
    k <- nrow(d)
    if (k == 0) next
    es <- escalc("RR", ai = ev_i, n1i = n_i, ci = ev_c, n2i = n_c, data = d,
                 slab = d$study_id)
    tag <- if (stratum == "Randomised trials") "rct" else "observational"
    if (k < 3) {
      summaries[[tag]] <- data.frame(stratum = stratum, k = k, model = "not pooled (k < 3, per protocol)",
                                     measure = "RR", estimate = NA, ci_low = NA, ci_high = NA,
                                     tau2 = NA, I2 = NA, Q_p = NA)
      next
    }
    fits <- list(
      "DL (primary)" = rma(yi, vi, data = es, method = "DL"),
      "DL + HKSJ (sensitivity, not pre-specified)" = rma(yi, vi, data = es, method = "DL", test = "knha"),
      "REML (sensitivity, not pre-specified)" = rma(yi, vi, data = es, method = "REML")
    )
    es_or <- escalc("OR", ai = ev_i, n1i = n_i, ci = ev_c, n2i = n_c, data = d)
    fits[["OR, DL (sensitivity, not pre-specified)"]] <- rma(yi, vi, data = es_or, method = "DL")
    for (m in names(fits)) {
      f <- fits[[m]]
      summaries[[paste(tag, m)]] <- data.frame(
        stratum = stratum, k = k, model = m, measure = if (grepl("^OR", m)) "OR" else "RR",
        estimate = exp(f$b[1]), ci_low = exp(f$ci.lb), ci_high = exp(f$ci.ub),
        tau2 = f$tau2, I2 = f$I2, Q_p = f$QEp)
    }

    png(file.path(cfg$out, "mortality", paste0("forest_", tag, ".png")),
        width = 2000, height = 350 + 110 * k, res = 200)
    f <- fits[["DL (primary)"]]
    forest(f, atransf = exp, at = log(c(0.1, 0.25, 0.5, 1, 2, 4)),
           ilab = cbind(paste0(d$ev_i, "/", d$n_i), paste0(d$ev_c, "/", d$n_c), d$timepoint),
           ilab.xpos = c(-5.2, -4.2, -3.0), xlim = c(-8.5, 4), cex = 0.75,
           header = c("Study", "RR [95% CI]"),
           mlab = sprintf("DL random effects (I2 = %.0f%%, tau2 = %.3f)", f$I2, f$tau2))
    text(c(-5.2, -4.2, -3.0), k + 2, c("Pes-guided", "Control", "Follow-up"), cex = 0.75, font = 2)
    title(paste("Mortality at longest follow-up -", stratum), cex.main = 0.9)
    stamp_plot(cfg)
    dev.off()
  }
  summ <- if (length(summaries)) do.call(rbind, summaries) else data.frame()
  write_out(cfg, "mortality", "pooled.csv", summ)
  list(inventory = inv, pooled = summ)
}
