# Two analyses added on Bram Rochwerg's review of 22 September 2026.
#
# (1) Adjusted mortality estimates in the observational studies (D32). The
#     observational pool (mortality.R) uses crude counts. Every adjusted effect
#     estimate the pooled observational studies report for mortality is listed
#     beside it, as extracted. They are not pooled: the studies adjust for
#     different covariates on different scales (a Cox hazard ratio, an odds
#     ratio of survival), and one study reports none.
#
# (2) Meta-regression of the primary outcome on baseline PaO2/FiO2 in the
#     randomised trials (D35): exploratory, not pre-specified. The study-level
#     covariate is the n-weighted average of the two arms' reported central
#     values, mixing means and medians as the papers report them; the output
#     names each study's summary type. BMI is not regressed: only three of the
#     five trials report it, within 22-25 kg/m2 (D35).

run_adjusted_observational <- function(cfg, tb, inv) {
  ids <- inv$study_id[inv$pooled & inv$stratum == "Observational studies"]
  e <- tb$effects[tb$effects$study_id %in% ids & tb$effects$domain == "mortality" &
                    truthy(tb$effects$effect_adjusted) & !is.na(tb$effects$effect_value), ]
  keep <- c("study_id", "name_verbatim", "timepoint_label", "effect_type", "effect_value",
            "effect_ci_low", "effect_ci_high", "effect_p_value", "effect_p_verbatim",
            "adjustment_covariates", "test_used")
  e <- e[order(e$study_id), keep]
  none <- setdiff(ids, e$study_id)
  if (length(none))
    e <- rbind(e, data.frame(study_id = none, name_verbatim = "no adjusted mortality estimate reported",
                             timepoint_label = NA, effect_type = NA, effect_value = NA,
                             effect_ci_low = NA, effect_ci_high = NA, effect_p_value = NA,
                             effect_p_verbatim = NA, adjustment_covariates = NA, test_used = NA))
  e$direction <- ifelse(grepl("surviv", e$name_verbatim, ignore.case = TRUE),
                        "estimate is for survival: above 1 favours the balloon",
                        ifelse(is.na(e$effect_value), "", "estimate is for death: below 1 favours the balloon"))
  e$crude_count_row_in_pool <- e$study_id %in% ids
  write_out(cfg, "mortality", "adjusted_observational.csv", e)
  e
}

run_pf_metaregression <- function(cfg, tb, inv) {
  b <- read.csv(file.path(cfg$data, "baseline.csv"), stringsAsFactors = FALSE,
                na.strings = c("", "NA"), encoding = "UTF-8")
  d <- inv[inv$pooled & inv$stratum == "Randomised trials", ]
  pf <- do.call(rbind, lapply(d$study_id, function(sid) {
    x <- b[b$study_id == sid & b$arm_role %in% c("intervention", "comparator") &
             !is.na(b$pao2_fio2_baseline_value), ]
    data.frame(study_id = sid,
               pf = if (nrow(x) == 2) sum(x$pao2_fio2_baseline_value * x$n) / sum(x$n) else NA,
               pf_summary = paste(unique(x$pao2_fio2_baseline_value_type), collapse = "/"))
  }))
  d <- merge(d, pf, by = "study_id")
  d <- d[!is.na(d$pf), ]
  es <- escalc("RR", ai = ev_i, n1i = n_i, ci = ev_c, n2i = n_c, data = d, slab = d$study_id)
  rows <- list()
  for (m in c("DL", "REML")) {
    f <- try(rma(yi, vi, mods = ~ pf, data = es, method = m), silent = TRUE)
    if (inherits(f, "try-error")) next
    rows[[m]] <- data.frame(model = m, k = f$k,
                            rr_ratio_per_50_mmHg = exp(50 * f$b[2]),
                            ci_low = exp(50 * f$ci.lb[2]), ci_high = exp(50 * f$ci.ub[2]),
                            p = f$pval[2], tau2 = f$tau2, R2 = f$R2,
                            note = "exploratory, not pre-specified; study-level covariate (ecological)")
  }
  res <- do.call(rbind, rows)
  write_out(cfg, "subgroups_mortality", "metaregression_pf.csv", res)
  write_out(cfg, "subgroups_mortality", "metaregression_pf_data.csv",
            transform(es[, c("study_id", "pf", "pf_summary", "ev_i", "n_i", "ev_c", "n_c")],
                      RR = exp(es$yi)))

  png(file.path(cfg$out, "subgroups_mortality", "metaregression_pf.png"),
      width = 1600, height = 1200, res = 200)
  f <- rma(yi, vi, mods = ~ pf, data = es, method = "DL")
  regplot(f, transf = exp, xlab = "Baseline PaO2/FiO2 (mmHg), study level",
          ylab = "Risk ratio, mortality", label = TRUE, labsize = 0.7,
          legend = FALSE)
  title("Exploratory meta-regression (not pre-specified), randomised trials", cex.main = 0.85)
  stamp_plot(cfg)
  dev.off()
  res
}
