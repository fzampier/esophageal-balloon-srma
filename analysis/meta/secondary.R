# Secondary clinical outcomes: durations and lengths of stay (continuous) and
# barotrauma (dichotomous).
#
# Protocol: continuous outcomes as mean differences, dichotomous as risk ratios,
# random effects; RCTs and observational studies separately. The protocol sets
# a 3-study minimum only for mortality and VFD; the same minimum is applied here
# for consistency, and fewer studies are tabulated, not pooled.
#
# Continuous outcomes are reported in mixed forms. Decisions, recorded in the
# outputs:
#   - Units are harmonised to days at analysis time (hours / 24). Extraction
#     records the paper's unit; conversion happens once, here.
#   - Primary analysis: every study, with medians/IQRs converted (mean: Luo
#     2018; SD: Wan 2014), medians/ranges converted (mean: Luo 2018; SD: Wan
#     2014), and mean/95% CI converted to an SD by the t distribution. Each
#     converted value is labelled with its method.
#   - Sensitivity 1: only studies that reported mean and SD for both arms.
#   - Sensitivity 2: excluding entries selection.csv marks exclude_in_sensitivity
#     (durations among survivors or weaned subjects; stays truncated at a day).
#   - Studies reporting a central value with no dispersion are tabulated only.

to_days <- function(x, unit) {
  u <- tolower(trimws(ifelse(is.na(unit), "", unit)))
  ifelse(u %in% c("h", "hr", "hrs", "hour", "hours"), x / 24, x)
}

convert_arm <- function(a, n) {
  vt <- a$value_type; dt <- a$dispersion_type; u <- a$unit
  m <- to_days(a$value, u); sd <- to_days(a$dispersion, u)
  lo <- to_days(a$dispersion_low, u); hi <- to_days(a$dispersion_high, u)
  none <- list(mean = NA, sd = NA, method = NA)
  if (is.na(m) || is.na(n)) return(modifyList(none, list(method = "not convertible: value or n missing")))
  vt <- ifelse(is.na(vt), "", vt); dt <- ifelse(is.na(dt), "", dt)
  if (vt == "mean" && dt == "sd" && !is.na(sd)) return(list(mean = m, sd = sd, method = "as reported"))
  if (vt == "mean" && dt == "ci95" && !is.na(lo) && !is.na(hi))
    return(list(mean = m, sd = sqrt(n) * (hi - lo) / (2 * qt(0.975, n - 1)), method = "derived: SD from 95% CI"))
  if (vt == "median" && dt == "iqr" && !is.na(lo) && !is.na(hi))
    return(list(mean = luo_mean(lo, m, hi, n), sd = wan_sd(lo, hi, n), method = "derived: Luo/Wan from median, IQR"))
  if (vt == "median" && dt == "range" && !is.na(lo) && !is.na(hi)) {
    w <- 4 / (4 + n^0.75)
    return(list(mean = w * (lo + hi) / 2 + (1 - w) * m,
                sd = (hi - lo) / (2 * qnorm((n - 0.375) / (n + 0.25))),
                method = "derived: Luo/Wan from median, range"))
  }
  modifyList(none, list(method = paste0("not convertible: ", vt, " ", dt)))
}

study_frame <- function(tb) {
  st <- poolable_reports(tb$studies)
  st$stratum <- design_stratum(st$study_design)
  st
}

exclusion_reason <- function(s, ta) {
  if (s$comparison_structure != "parallel_arms") return("no concurrent comparator")
  if (s$pool_role == "secondary report - not pooled") return("secondary report of a cohort; its primary report is used")
  if (!ta$ok) return("no single intervention and comparator arm")
  if (s$stratum == "Other designs - not pooled") return(paste("design", s$study_design))
  ""
}

forest_png <- function(cfg, key, tag, fit, x, cols, title, xlab, transf = NULL) {
  k <- nrow(x)
  lo <- min(fit$yi - 1.96 * sqrt(fit$vi)); hi <- max(fit$yi + 1.96 * sqrt(fit$vi))
  w <- max(hi - lo, 1e-6)
  xpos <- lo - c(1.9, 1.2, 0.55) * w
  png(file.path(cfg$out, key, paste0("forest_", tag, ".png")), width = 2000, height = 380 + 110 * k, res = 200)
  args <- list(x = fit, ilab = cols, ilab.xpos = xpos, xlim = c(lo - 2.7 * w, hi + 1.3 * w), cex = 0.75,
               header = c("Study", "Estimate [95% CI]"), xlab = xlab,
               mlab = sprintf("DL random effects (I2 = %.0f%%)", fit$I2))
  if (!is.null(transf)) args$atransf <- transf
  do.call(forest, args)
  text(xpos, k + 2, c("Pes-guided", "Control", "Note"), cex = 0.75, font = 2)
  title(title, cex.main = 0.85)
  stamp_plot(cfg)
  dev.off()
}

pool_rows <- function(label, stratum, es, min_k = 3) {
  k <- sum(!is.na(es$yi))
  if (k < min_k)
    return(list(fit = NULL, rows = data.frame(stratum = stratum, analysis = label, k = k, model = "not pooled (k < 3)",
                                              estimate = NA, ci_low = NA, ci_high = NA, tau2 = NA, I2 = NA, Q_p = NA)))
  f <- rma(yi, vi, data = es, method = "DL")
  fk <- rma(yi, vi, data = es, method = "DL", test = "knha")
  fr <- rma(yi, vi, data = es, method = "REML")
  rows <- do.call(rbind, lapply(list(list("DL", f), list("DL + HKSJ", fk), list("REML", fr)), function(m)
    data.frame(stratum = stratum, analysis = label, k = k, model = m[[1]], estimate = m[[2]]$b[1],
               ci_low = m[[2]]$ci.lb, ci_high = m[[2]]$ci.ub, tau2 = m[[2]]$tau2, I2 = m[[2]]$I2, Q_p = m[[2]]$QEp)))
  list(fit = f, rows = rows)
}

run_continuous <- function(cfg, tb, key, domain, title) {
  sel <- read.csv(file.path(cfg$root, "analysis", "meta", "selection.csv"), stringsAsFactors = FALSE)
  st <- study_frame(tb)
  o <- tb$outcomes[tb$outcomes$domain == domain, ]
  inv <- list()
  for (sid in unique(o$study_id)) {
    s <- st[st$study_id == sid, ]
    picked <- select_entry(o[o$study_id == sid, ], sel, key, sid)
    r <- picked$rows; ta <- two_arm_rows(r)
    n_i <- if (ta$ok) ta$intervention$n else NA; n_c <- if (ta$ok) ta$comparator$n else NA
    n_source <- "extracted"
    if (ta$ok && (is.na(n_i) || is.na(n_c)) && !is.null(picked$sel) && !is.na(picked$sel$n_intervention)) {
      n_i <- picked$sel$n_intervention; n_c <- picked$sel$n_comparator
      n_source <- paste("selection.csv:", picked$sel$n_source)
    }
    ci <- if (ta$ok) convert_arm(ta$intervention, n_i) else list(mean = NA, sd = NA, method = NA)
    cc <- if (ta$ok) convert_arm(ta$comparator, n_c) else list(mean = NA, sd = NA, method = NA)
    shown <- function(a) if (!ta$ok || !nrow(a)) NA else paste0(a$value_type, " ", a$value, " ",
      ifelse(is.na(a$dispersion_low), ifelse(is.na(a$dispersion), "", paste(a$dispersion_type, a$dispersion)),
             paste0(a$dispersion_type, " ", a$dispersion_low, " to ", a$dispersion_high)), " ", a$unit)
    inv[[sid]] <- data.frame(
      study_id = sid, design = s$study_design, stratum = s$stratum, outcome = r$name_verbatim[1],
      reported_intervention = shown(ta$intervention), n_intervention = n_i,
      reported_comparator = shown(ta$comparator), n_comparator = n_c, n_source = n_source,
      dropped_arms = ta$dropped_arms,
      mean_i = ci$mean, sd_i = ci$sd, method_i = ci$method, mean_c = cc$mean, sd_c = cc$sd, method_c = cc$method,
      exclude_in_sensitivity = !is.null(picked$sel) && identical(picked$sel$exclude_in_sensitivity, "yes"),
      used = exclusion_reason(s, ta) == "", not_used_because = exclusion_reason(s, ta), stringsAsFactors = FALSE)
  }
  if (!length(inv)) return(invisible(NULL))
  d <- do.call(rbind, inv)
  d$convertible <- !is.na(d$mean_i) & !is.na(d$mean_c) & !is.na(d$sd_i) & !is.na(d$sd_c)
  d$not_used_because[d$used & !d$convertible] <- paste("tabulated only:", d$method_i[d$used & !d$convertible])
  d <- d[order(d$stratum, !d$used, d$study_id), ]
  write_out(cfg, key, "study_inventory.csv", d)

  pooled <- list()
  for (stratum in c("Randomised trials", "Observational studies")) {
    x <- d[d$used & d$convertible & d$stratum == stratum, ]
    tag <- if (stratum == "Randomised trials") "rct" else "observational"
    if (!nrow(x)) next
    sets <- list(
      "primary: all, converted where needed" = rep(TRUE, nrow(x)),
      "sensitivity: mean/SD as reported only" = x$method_i == "as reported" & x$method_c == "as reported",
      "sensitivity: excluding conditional or truncated entries" = !x$exclude_in_sensitivity)
    for (label in names(sets)) {
      xs <- x[sets[[label]], ]
      if (!nrow(xs)) { pooled[[paste(tag, label)]] <- data.frame(stratum = stratum, analysis = label, k = 0,
        model = "not pooled (k < 3)", estimate = NA, ci_low = NA, ci_high = NA, tau2 = NA, I2 = NA, Q_p = NA); next }
      es <- escalc("MD", m1i = mean_i, sd1i = sd_i, n1i = n_intervention, m2i = mean_c, sd2i = sd_c,
                   n2i = n_comparator, data = xs, slab = xs$study_id)
      p <- pool_rows(label, stratum, es)
      pooled[[paste(tag, label)]] <- p$rows
      if (!is.null(p$fit) && startsWith(label, "primary"))
        forest_png(cfg, key, tag, p$fit, xs,
                   cbind(sprintf("%.1f (%.1f)", xs$mean_i, xs$sd_i), sprintf("%.1f (%.1f)", xs$mean_c, xs$sd_c),
                         ifelse(xs$method_i == "as reported" & xs$method_c == "as reported", "", "converted")),
                   paste(title, "(days) -", stratum), "Mean difference, days")
    }
  }
  summ <- if (length(pooled)) do.call(rbind, pooled) else data.frame()
  write_out(cfg, key, "pooled.csv", summ)
  list(inventory = d, pooled = summ)
}

run_binary <- function(cfg, tb, key, domain, title) {
  sel <- read.csv(file.path(cfg$root, "analysis", "meta", "selection.csv"), stringsAsFactors = FALSE)
  st <- study_frame(tb)
  o <- tb$outcomes[tb$outcomes$domain == domain, ]
  inv <- list()
  for (sid in unique(o$study_id)) {
    s <- st[st$study_id == sid, ]
    picked <- select_entry(o[o$study_id == sid, ], sel, key, sid)
    r <- picked$rows; ta <- two_arm_rows(r)
    reason <- exclusion_reason(s, ta)
    ev <- if (ta$ok) c(ta$intervention$events, ta$intervention$total, ta$comparator$events, ta$comparator$total) else rep(NA, 4)
    if (reason == "" && any(is.na(ev))) reason <- "events or denominator missing"
    if (reason == "" && ev[1] == 0 && ev[3] == 0) reason <- "no events in either arm: no information for a ratio"
    inv[[sid]] <- data.frame(study_id = sid, design = s$study_design, stratum = s$stratum,
                             outcome = r$name_verbatim[1], timepoint = r$timepoint_type[1],
                             ev_i = ev[1], n_i = ev[2], ev_c = ev[3], n_c = ev[4], dropped_arms = ta$dropped_arms,
                             used = reason == "", not_used_because = reason, stringsAsFactors = FALSE)
  }
  if (!length(inv)) return(invisible(NULL))
  d <- do.call(rbind, inv)
  d <- d[order(d$stratum, !d$used, d$study_id), ]
  write_out(cfg, key, "study_inventory.csv", d)
  pooled <- list()
  for (stratum in c("Randomised trials", "Observational studies")) {
    x <- d[d$used & d$stratum == stratum, ]
    if (!nrow(x)) next
    tag <- if (stratum == "Randomised trials") "rct" else "observational"
    es <- escalc("RR", ai = ev_i, n1i = n_i, ci = ev_c, n2i = n_c, data = x, slab = x$study_id)
    p <- pool_rows("primary: risk ratio", stratum, es)
    rows <- p$rows
    rows[, c("estimate", "ci_low", "ci_high")] <- exp(rows[, c("estimate", "ci_low", "ci_high")])
    pooled[[tag]] <- rows
    if (!is.null(p$fit))
      forest_png(cfg, key, tag, p$fit, x, cbind(paste0(x$ev_i, "/", x$n_i), paste0(x$ev_c, "/", x$n_c), x$outcome),
                 paste(title, "-", stratum), "Risk ratio (log scale)", transf = exp)
  }
  summ <- if (length(pooled)) do.call(rbind, pooled) else data.frame()
  write_out(cfg, key, "pooled.csv", summ)
  list(inventory = d, pooled = summ)
}
