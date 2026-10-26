# Physiologic outcomes: descriptive tables, no pooling (DECISIONS.md D13-D16, D18).
#
#   D13  described, not meta-analysed
#   D14  oxygenation (PaO2/FiO2; oxygenation index), respiratory-system compliance
#        or elastance, plateau and driving pressure, end-expiratory and driving
#        transpulmonary pressure, PEEP applied
#   D15  per study: baseline, first measurement after titration, latest reported
#   D16  within-patient studies: Pes-guided vs the conventional reference
#        condition; alternative strategies in a separate table
#   D18  compliance and elastance kept apart; PEEP applied = set PEEP, total PEEP
#        only where a study reports no set PEEP
#
# Which extracted outcome is which measure comes from analysis/meta/measure_map.csv,
# built from both extractors' outcome names and reviewed by hand. A physiologic
# outcome whose name is not in the map is listed in unmapped_names.csv rather
# than silently dropped: add it to the map.
#
# Differences shown are arithmetic (Pes-guided minus comparator/reference) on the
# values as reported, only where both are the same summary statistic. They are
# descriptive, computed here, and labelled so; reported P values and paired
# contrasts are shown as the papers give them.

MEASURE_LABELS <- c(
  pao2_fio2 = "PaO2/FiO2", oxygenation_index = "Oxygenation index",
  compliance_rs = "Respiratory-system compliance", elastance_rs = "Respiratory-system elastance",
  plateau_pressure = "Plateau pressure", driving_pressure = "Driving pressure",
  pl_end_expiratory = "End-expiratory transpulmonary pressure",
  pl_driving = "Transpulmonary driving pressure", peep_set = "PEEP applied (set)",
  peep_total = "PEEP applied (total, no set PEEP reported)")

PHYSIO_DOMAINS <- c("oxygenation", "respiratory_mechanics", "transpulmonary_pressure", "ventilator_settings")

elapsed_min <- function(type, hours, days) {
  ifelse(!is.na(hours), hours * 60, ifelse(!is.na(days), days * 1440, ifelse(type %in% "baseline", 0, NA)))
}

fmt_value <- function(r) {
  if (is.null(r) || !nrow(r)) return(NA_character_)
  apply(r, 1, function(x) {
    v <- x[["value"]]; if (is.na(v)) return(NA_character_)
    disp <- if (!is.na(x[["dispersion_low"]])) sprintf(" (%s %s to %s)", x[["dispersion_type"]], x[["dispersion_low"]], x[["dispersion_high"]])
            else if (!is.na(x[["dispersion"]])) sprintf(" (%s %s)", x[["dispersion_type"]], x[["dispersion"]]) else ""
    paste0(ifelse(is.na(x[["value_type"]]), "", paste0(x[["value_type"]], " ")), trimws(v), disp,
           ifelse(is.na(x[["n"]]), "", paste0(", n ", trimws(x[["n"]]))),
           ifelse(is.na(x[["unit"]]), "", paste0(" ", x[["unit"]])))
  })
}

# D15: baseline, first after titration, latest. Timepoints with no elapsed time
# (crossover condition, end of study, not reported) cannot be ordered and are
# all kept, marked "unordered".
select_timepoints <- function(rows) {
  rows$elapsed <- elapsed_min(rows$timepoint_type, rows$timepoint_hours, rows$timepoint_days)
  ord <- rows[!is.na(rows$elapsed), ]
  keep <- rep(FALSE, nrow(rows)); role <- rep("unordered", nrow(rows))
  if (nrow(ord)) {
    base <- min(ord$elapsed[ord$elapsed == 0], Inf)
    post <- ord$elapsed[ord$elapsed > 0]
    first <- if (length(post)) min(post) else NA
    last <- if (length(post)) max(post) else NA
    for (i in seq_len(nrow(rows))) {
      e <- rows$elapsed[i]
      if (is.na(e)) next
      if (e == 0) { keep[i] <- TRUE; role[i] <- "baseline" }
      else if (!is.na(first) && e == first) { keep[i] <- TRUE; role[i] <- if (first == last) "first = latest" else "first after titration" }
      else if (!is.na(last) && e == last) { keep[i] <- TRUE; role[i] <- "latest" }
    }
  }
  keep[is.na(rows$elapsed)] <- TRUE
  rows$timepoint_shown_as <- role
  rows[keep, , drop = FALSE]
}

tp_label <- function(r) paste0(r$timepoint_type,
  ifelse(!is.na(r$timepoint_hours), paste0(" ", r$timepoint_hours, " h"),
         ifelse(!is.na(r$timepoint_days), paste0(" ", r$timepoint_days, " d"), "")),
  ifelse(is.na(r$timepoint_label), "", paste0(" [", r$timepoint_label, "]")))

computed_diff <- function(a, b) {
  if (!nrow(a) || !nrow(b) || nrow(a) != 1 || nrow(b) != 1) return(NA_real_)
  if (is.na(a$value) || is.na(b$value) || !identical(a$value_type, b$value_type)) return(NA_real_)
  # Round to the precision the papers reported, so 12.6 - 12.7 reads -0.1.
  decimals <- function(x) { s <- sub("^[^.]*\\.?", "", format(x, scientific = FALSE, trim = TRUE)); nchar(s) }
  round(as.numeric(a$value) - as.numeric(b$value), max(decimals(a$value), decimals(b$value)))
}

run_physiology <- function(cfg, tb) {
  map <- read.csv(file.path(cfg$root, "analysis", "meta", "measure_map.csv"), stringsAsFactors = FALSE)
  map <- map[map$measure != "not_tabulated", c("study_id", "name_verbatim", "measure")]
  st <- poolable_reports(tb$studies)
  o <- tb$outcomes

  phys <- o[o$domain %in% PHYSIO_DOMAINS, ]
  known <- read.csv(file.path(cfg$root, "analysis", "meta", "measure_map.csv"), stringsAsFactors = FALSE)
  unmapped <- unique(phys[!paste(phys$study_id, phys$name_verbatim) %in% paste(known$study_id, known$name_verbatim),
                          c("study_id", "domain", "name_verbatim")])
  write_out(cfg, "physiology", "unmapped_names.csv", unmapped)
  if (nrow(unmapped)) warning(nrow(unmapped), " physiologic outcome name(s) not in measure_map.csv; see unmapped_names.csv", call. = FALSE)

  # merge() returns tied rows in an order that differs between machines; keep the extraction's order (outcomes.csv)
  o$input_row <- seq_len(nrow(o))
  o <- merge(o, map, by = c("study_id", "name_verbatim"))
  o <- merge(o, st[, c("study_id", "comparison_structure", "pool_role", "study_design")],
             by = "study_id", suffixes = c("", ".st"))
  o <- o[order(o$input_row), ]
  skipped <- unique(o[o$pool_role == "secondary report - not pooled", c("study_id", "pool_role")])
  write_out(cfg, "physiology", "reports_not_tabulated.csv", skipped)
  o <- o[o$pool_role != "secondary report - not pooled", ]

  # D18: total PEEP only where a study reports no set PEEP.
  has_set <- unique(o$study_id[o$measure == "peep_set"])
  o <- o[!(o$measure == "peep_total" & o$study_id %in% has_set), ]
  o$measure_label <- MEASURE_LABELS[o$measure]

  eff <- tb$effects; pe <- tb$paired
  parallel <- list(); within <- list(); alternatives <- list()

  for (key in unique(paste(o$study_id, o$measure))) {
    rows <- o[paste(o$study_id, o$measure) == key, ]
    sid <- rows$study_id[1]; structure <- rows$comparison_structure[1]
    rows <- select_timepoints(rows)

    if (structure == "parallel_arms") {
      for (tk in unique(paste(rows$name_verbatim, rows$timepoint_type, rows$timepoint_hours, rows$timepoint_days, rows$timepoint_label))) {
        r <- rows[paste(rows$name_verbatim, rows$timepoint_type, rows$timepoint_hours, rows$timepoint_days, rows$timepoint_label) == tk, ]
        iv <- r[r$arm_role %in% "intervention", ]; cc <- r[r$arm_role %in% "comparator", ]
        other_arms <- paste(setdiff(unique(r$arm_label), c(iv$arm_label, cc$arm_label)), collapse = "; ")
        e <- eff[eff$study_id == sid & eff$name_verbatim == r$name_verbatim[1] &
                   (is.na(eff$timepoint_label) | eff$timepoint_label %in% r$timepoint_label[1]), ]
        reported <- if (nrow(e)) paste(unique(na.omit(ifelse(!is.na(e$effect_value),
                          paste0(e$effect_type, " ", e$effect_value), NA), e$effect_p_verbatim)), collapse = "; ") else ""
        pvals <- if (nrow(e)) paste(unique(na.omit(c(e$effect_p_verbatim, as.character(e$effect_p_value)))), collapse = "; ") else ""
        parallel[[length(parallel) + 1]] <- data.frame(
          study_id = sid, design = r$study_design[1], measure = r$measure_label[1], outcome = r$name_verbatim[1],
          timepoint = tp_label(r[1, ]), shown_as = r$timepoint_shown_as[1],
          pes_guided = paste(fmt_value(iv), collapse = " | "), comparator = paste(fmt_value(cc), collapse = " | "),
          computed_difference_pes_minus_comparator = computed_diff(iv, cc),
          reported_between_group = reported, reported_p = pvals, other_arms_not_shown = other_arms,
          stringsAsFactors = FALSE)
      }
    } else if (structure == "within_patient_crossover") {
      # An arm with no recorded role is neither guessed into Pes-guided nor into
      # reference: it is shown with the alternatives, flagged.
      rows$group <- ifelse(is.na(rows$arm_role) | rows$arm_role == "", "role not recorded",
                    ifelse(rows$arm_role == "intervention", "Pes-guided",
                    ifelse(rows$arm_role %in% c("reference_condition", "comparator"), "reference", "alternative")))
      for (i in seq_len(nrow(rows))) {
        r <- rows[i, ]
        contrast <- ""
        if (r$group == "Pes-guided") {
          refs <- rows[rows$group == "reference" & rows$name_verbatim == r$name_verbatim, "arm_label"]
          p <- pe[pe$study_id == sid & pe$name_verbatim == r$name_verbatim &
                    ((pe$arm_label_a %in% r$arm_label & pe$arm_label_b %in% refs) |
                     (pe$arm_label_b %in% r$arm_label & pe$arm_label_a %in% refs)), ]
          if (nrow(p)) contrast <- paste(unique(paste0(p$arm_label_a, " - ", p$arm_label_b,
                ifelse(is.na(p$mean_difference), "", paste0(": MD ", p$mean_difference)),
                ifelse(is.na(p$p_value_verbatim), ifelse(is.na(p$p_value), "", paste0(", p ", p$p_value)),
                       paste0(", p ", p$p_value_verbatim)))), collapse = "; ")
        }
        out <- data.frame(
          study_id = sid, design = r$study_design, measure = r$measure_label, outcome = r$name_verbatim,
          condition = r$arm_label, group = r$group, timepoint = tp_label(r), shown_as = r$timepoint_shown_as,
          value = fmt_value(r), reported_paired_contrast_vs_reference = contrast, stringsAsFactors = FALSE)
        if (r$group %in% c("alternative", "role not recorded")) alternatives[[length(alternatives) + 1]] <- out
        else within[[length(within) + 1]] <- out
      }
    }
  }
  bind <- function(x) if (length(x)) do.call(rbind, x) else data.frame()
  par_df <- bind(parallel); win_df <- bind(within); alt_df <- bind(alternatives)
  if (nrow(par_df)) par_df <- par_df[order(par_df$measure, par_df$study_id, par_df$shown_as), ]
  if (nrow(win_df)) win_df <- win_df[order(win_df$measure, win_df$study_id, win_df$group != "Pes-guided"), ]
  if (nrow(alt_df)) alt_df <- alt_df[order(alt_df$measure, alt_df$study_id), ]
  write_out(cfg, "physiology", "parallel_arm_studies.csv", par_df)
  write_out(cfg, "physiology", "within_patient_pes_vs_reference.csv", win_df)
  write_out(cfg, "physiology", "within_patient_alternative_strategies.csv", alt_df)

  cov <- if (nrow(o)) as.data.frame.matrix(table(unique(o[, c("measure_label", "comparison_structure", "study_id")])[, 1:2])) else data.frame()
  if (nrow(o)) {
    u <- unique(o[, c("measure_label", "comparison_structure", "study_id")])
    cov <- aggregate(study_id ~ measure_label + comparison_structure, data = u, FUN = length)
    names(cov)[3] <- "studies"
  }
  write_out(cfg, "physiology", "coverage.csv", cov)
  list(parallel = par_df, within = win_df, alternatives = alt_df, coverage = cov, unmapped = unmapped)
}
