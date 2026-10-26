# Ventilator-free days (VFD).
#
# Protocol: "Days alive and free of mechanical ventilation will similarly be
# analyzed if at least 3 trials are available."
#
# Decided with Fernando, 2026-09-15, because every trial reports VFD as a median
# with an IQR whose lower bound is 0 (deaths score zero), and windows differ
# (28 vs 60 days):
#
#   Primary presentation - a table of the medians and IQRs as reported, per arm,
#   with the window and any between-group estimate the paper gives. No pooling.
#
#   Sensitivity analysis - medians and IQRs converted to means and SDs (mean:
#   Luo et al. 2018; SD: Wan et al. 2014), pooled as a mean difference with
#   DerSimonian-Laird random effects when at least 3 trials contribute. Both
#   estimators assume a roughly symmetric distribution, which VFD is not; the
#   converted values are derived, labelled as such, and the pooled estimate is a
#   sensitivity analysis only. Trials with different windows are pooled together
#   here and the window is shown beside each.
#
# Which entry feeds the analysis, when a study has several, and any denominator
# the extracted entry lacks, come from analysis/meta/selection.csv - with the
# quote that justifies it - never from a guess in code.

luo_mean <- function(q1, m, q3, n) {
  w <- 0.7 + 0.39 / n
  w * (q1 + q3) / 2 + (1 - w) * m
}

wan_sd <- function(q1, q3, n) {
  (q3 - q1) / (2 * qnorm((0.75 * n - 0.125) / (n + 0.25)))
}

select_entry <- function(rows, sel, analysis, sid) {
  s <- sel[sel$analysis == analysis & sel$study_id == sid, ]
  names_here <- unique(rows$name_verbatim)
  if (nrow(s)) {
    pick <- rows[rows$name_verbatim == s$outcome_name[1], ]
    if (!nrow(pick)) stop(sid, ": selection.csv names '", s$outcome_name[1],
                          "', which is not among its ", analysis, " entries: ",
                          paste(names_here, collapse = " | "), call. = FALSE)
    return(list(rows = pick, sel = s[1, ]))
  }
  if (length(names_here) > 1)
    stop(sid, " has ", length(names_here), " ", analysis, " entries (",
         paste(names_here, collapse = " | "),
         "); add a row to analysis/meta/selection.csv saying which one is analysed", call. = FALSE)
  list(rows = rows, sel = NULL)
}

run_vfd <- function(cfg, tb) {
  sel <- read.csv(file.path(cfg$root, "analysis", "meta", "selection.csv"), stringsAsFactors = FALSE)
  st <- poolable_reports(tb$studies)
  st$stratum <- design_stratum(st$study_design)
  o <- tb$outcomes[tb$outcomes$domain == "ventilator_free_days", ]

  out <- list()
  for (sid in unique(o$study_id)) {
    s <- st[st$study_id == sid, ]
    picked <- select_entry(o[o$study_id == sid, ], sel, "vfd", sid)
    r <- picked$rows
    ta <- two_arm_rows(r)
    n_i <- if (ta$ok) ta$intervention$n else NA
    n_c <- if (ta$ok) ta$comparator$n else NA
    n_source <- "extracted"
    if (ta$ok && (is.na(n_i) || is.na(n_c)) && !is.null(picked$sel)) {
      n_i <- picked$sel$n_intervention; n_c <- picked$sel$n_comparator
      n_source <- paste("selection.csv:", picked$sel$n_source)
    }
    reason <- ""
    if (s$comparison_structure != "parallel_arms") reason <- "no concurrent comparator"
    else if (s$pool_role == "secondary report - not pooled") reason <- "secondary report of a cohort; its primary report is used"
    else if (!ta$ok) reason <- "no single intervention and comparator arm"
    else if (s$stratum == "Other designs - not pooled") reason <- paste("design", s$study_design)

    eff <- tb$effects[tb$effects$study_id == sid & tb$effects$name_verbatim == r$name_verbatim[1], ]
    reported <- if (nrow(eff)) paste(apply(eff, 1, function(e) {
      paste0(ifelse(is.na(e["effect_type"]), "", e["effect_type"]), " ",
             ifelse(is.na(e["effect_value"]), "", e["effect_value"]),
             ifelse(is.na(e["effect_ci_low"]), "", paste0(" (", e["effect_ci_low"], " to ", e["effect_ci_high"], ")")),
             ifelse(is.na(e["effect_p_verbatim"]), ifelse(is.na(e["effect_p_value"]), "", paste0(" p=", e["effect_p_value"])),
                    paste0(" p ", e["effect_p_verbatim"])))
    }), collapse = "; ") else ""

    fmt <- function(a) if (!nrow(a)) NA else
      paste0(a$value_type, " ", a$value,
             ifelse(is.na(a$dispersion_low), ifelse(is.na(a$dispersion), "", paste0(" (", a$dispersion_type, " ", a$dispersion, ")")),
                    paste0(" [", a$dispersion_low, " to ", a$dispersion_high, "] ", a$dispersion_type)))
    out[[sid]] <- data.frame(
      study_id = sid, design = s$study_design, stratum = s$stratum, pool_role = s$pool_role,
      outcome = r$name_verbatim[1], window_days = r$timepoint_days[1],
      intervention = if (ta$ok) fmt(ta$intervention) else NA, n_intervention = n_i,
      comparator = if (ta$ok) fmt(ta$comparator) else NA, n_comparator = n_c, n_source = n_source,
      reported_between_group = reported,
      vt_i = if (ta$ok) ta$intervention$value_type else NA, m_i = if (ta$ok) ta$intervention$value else NA,
      lo_i = if (ta$ok) ta$intervention$dispersion_low else NA, hi_i = if (ta$ok) ta$intervention$dispersion_high else NA,
      dt_i = if (ta$ok) ta$intervention$dispersion_type else NA, sd_i_rep = if (ta$ok) ta$intervention$dispersion else NA,
      vt_c = if (ta$ok) ta$comparator$value_type else NA, m_c = if (ta$ok) ta$comparator$value else NA,
      lo_c = if (ta$ok) ta$comparator$dispersion_low else NA, hi_c = if (ta$ok) ta$comparator$dispersion_high else NA,
      dt_c = if (ta$ok) ta$comparator$dispersion_type else NA, sd_c_rep = if (ta$ok) ta$comparator$dispersion else NA,
      in_table = reason == "", not_used_because = reason, stringsAsFactors = FALSE)
  }
  d <- do.call(rbind, out)
  d <- d[order(d$stratum, !d$in_table, d$study_id), ]

  # Shown in the table but not converted and pooled (D46): the reported medians
  # cannot count deaths as zero, so a converted SD would understate the spread.
  NOT_CONVERTED <- c(maia_2026 = paste("medians cannot count deaths as zero (PEEP-table arm 6/12 deaths,",
                                       "median 21); not converted (D46)"))
  d$sensitivity_note <- ifelse(d$study_id %in% names(NOT_CONVERTED), NOT_CONVERTED[d$study_id], "")
  table_cols <- c("study_id", "design", "outcome", "window_days", "intervention", "n_intervention",
                  "comparator", "n_comparator", "n_source", "reported_between_group",
                  "in_table", "not_used_because", "sensitivity_note")
  write_out(cfg, "vfd", "vfd_table_primary.csv", d[, table_cols])

  # Sensitivity: convert and pool, per stratum.
  conv <- function(vt, m, lo, hi, dt, sd_rep, n) {
    if (is.na(vt) || is.na(m) || is.na(n)) return(c(NA, NA, "not convertible"))
    if (vt == "mean" && !is.na(dt) && dt == "sd") return(c(m, sd_rep, "as reported"))
    if (vt == "median" && !is.na(dt) && dt == "iqr" && !is.na(lo) && !is.na(hi))
      return(c(luo_mean(lo, m, hi, n), wan_sd(lo, hi, n), "derived: Luo mean, Wan SD from median/IQR"))
    c(NA, NA, paste("not convertible:", vt, dt))
  }
  pooled <- list()
  for (stratum in c("Randomised trials", "Observational studies")) {
    x <- d[d$in_table & d$stratum == stratum & !d$study_id %in% names(NOT_CONVERTED), ]
    if (!nrow(x)) next
    ci <- t(mapply(conv, x$vt_i, x$m_i, x$lo_i, x$hi_i, x$dt_i, x$sd_i_rep, x$n_intervention))
    cc <- t(mapply(conv, x$vt_c, x$m_c, x$lo_c, x$hi_c, x$dt_c, x$sd_c_rep, x$n_comparator))
    x$mean_i <- as.numeric(ci[, 1]); x$sd_i <- as.numeric(ci[, 2]); x$conversion_i <- ci[, 3]
    x$mean_c <- as.numeric(cc[, 1]); x$sd_c <- as.numeric(cc[, 2]); x$conversion_c <- cc[, 3]
    ok <- !is.na(x$mean_i) & !is.na(x$mean_c)
    tag <- if (stratum == "Randomised trials") "rct" else "observational"
    write_out(cfg, "vfd", paste0("converted_", tag, ".csv"),
              x[, c("study_id", "window_days", "n_intervention", "mean_i", "sd_i", "conversion_i",
                    "n_comparator", "mean_c", "sd_c", "conversion_c")])
    k <- sum(ok)
    if (k < 3) {
      pooled[[tag]] <- data.frame(stratum = stratum, k = k, analysis = "sensitivity: converted means",
                                  model = "not pooled (k < 3)", MD = NA, ci_low = NA, ci_high = NA,
                                  tau2 = NA, I2 = NA, Q_p = NA)
      next
    }
    x <- x[ok, ]
    es <- escalc("MD", m1i = mean_i, sd1i = sd_i, n1i = n_intervention,
                 m2i = mean_c, sd2i = sd_c, n2i = n_comparator, data = x, slab = x$study_id)
    f <- rma(yi, vi, data = es, method = "DL")
    fk <- rma(yi, vi, data = es, method = "DL", test = "knha")
    fr <- rma(yi, vi, data = es, method = "REML")
    for (m in list(list("DL", f), list("DL + HKSJ", fk), list("REML", fr)))
      pooled[[paste(tag, m[[1]])]] <- data.frame(
        stratum = stratum, k = k, analysis = "sensitivity: converted means", model = m[[1]],
        MD = m[[2]]$b[1], ci_low = m[[2]]$ci.lb, ci_high = m[[2]]$ci.ub,
        tau2 = m[[2]]$tau2, I2 = m[[2]]$I2, Q_p = m[[2]]$QEp)

    png(file.path(cfg$out, "vfd", paste0("forest_sensitivity_", tag, ".png")),
        width = 2000, height = 380 + 110 * k, res = 200)
    forest(f, ilab = cbind(sprintf("%.1f (%.1f)", x$mean_i, x$sd_i), sprintf("%.1f (%.1f)", x$mean_c, x$sd_c),
                           paste0(x$window_days, " d")),
           ilab.xpos = c(-38, -26, -16), xlim = c(-60, 45), cex = 0.75,
           header = c("Study", "MD [95% CI]"),
           mlab = sprintf("DL random effects (I2 = %.0f%%)", f$I2))
    text(c(-38, -26, -16), k + 2, c("Pes-guided", "Control", "Window"), cex = 0.75, font = 2)
    title(paste("Ventilator-free days, converted means - SENSITIVITY -", stratum), cex.main = 0.85)
    mtext("means/SDs derived from medians/IQRs (Luo, Wan); VFD is zero-inflated", side = 1, line = 3.5, cex = 0.65)
    stamp_plot(cfg)
    dev.off()
  }
  write_out(cfg, "vfd", "pooled_sensitivity.csv", if (length(pooled)) do.call(rbind, pooled) else data.frame())
  list(table = d[, table_cols], pooled = if (length(pooled)) do.call(rbind, pooled) else data.frame())
}
