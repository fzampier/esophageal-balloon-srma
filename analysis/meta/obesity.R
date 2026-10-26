# Obesity: what the included evidence can and cannot say.
#
# The protocol names obesity as a possible subgroup, and subgroups.R runs it on
# the primary outcome. That answers little here: no randomised trial reporting
# mortality enrolled an obese population (three are non-obese by BMI; the EPVent
# primary reports give no BMI, though the EPVent-2 secondary analysis, Sarge
# 2021, does: median 31.1 and 28.3 kg/m2 in its two APACHE-II strata), so among
# trials there is no obese level to compare. The obese evidence sits in the observational comparisons and in the
# within-patient physiologic studies.
#
# This module therefore adds no new pooled estimate. It lays the same results
# out by obesity class (D23):
#   evidence_map.csv          every included report: design, obesity class, BMI
#                             as reported, and what it contributes
#   mortality_by_obesity.csv  the study-level risk ratios of the primary outcome
#                             and the pooled rows of subgroups.R, by design
#                             stratum and obesity class
#   physiology_*.csv          the D14 tables restricted to obese populations
#
# The classification is subgroup_flags.obesity_subgroup, made once at
# extraction and reconciled: obese_population if enrolment required obesity or
# the mean BMI is at least 30 kg/m2. It is a study-level label. Three reports
# test BMI against the treatment effect within a study, none as an estimate by
# BMI class that could be pooled: Sarge 2021 (EPVent-2, 60-day mortality,
# interaction P = 0.74), Mauri 2024 and Chean 2025 (physiology). They are
# reported in the text (D30); reported_subgroups is not yet in scope (B9).

OBESITY_LABEL <- c(obese_population = "Obese population", mixed_bmi = "Mixed BMI",
                   non_obese_population = "Non-obese population", unclear = "BMI not reported")

run_obesity <- function(cfg, tb, inv, sg, ph) {
  s <- poolable_reports(tb$studies)
  s$stratum <- design_stratum(s$study_design)
  s$obesity_class <- ifelse(s$obesity_subgroup %in% names(OBESITY_LABEL),
                            OBESITY_LABEL[s$obesity_subgroup], "BMI not reported")
  s$bmi_as_reported <- ifelse(is.na(s$bmi) | s$bmi == "", "",
                              paste(s$bmi_type, s$bmi, "kg/m2"))
  phys_ids <- unique(c(ph$parallel$study_id, ph$within$study_id, ph$alternatives$study_id))
  s$mortality_pooled <- s$study_id %in% inv$study_id[inv$pooled]
  s$mortality_listed <- s$study_id %in% inv$study_id[!inv$pooled]
  s$physiology_tabulated <- s$study_id %in% phys_ids
  map <- s[order(match(s$obesity_subgroup, names(OBESITY_LABEL)), s$stratum, s$study_id),
           c("study_id", "obesity_class", "bmi_as_reported", "stratum", "study_design",
             "comparison_structure", "n_analysed", "ards_status", "pool_role",
             "mortality_pooled", "mortality_listed", "physiology_tabulated")]
  write_out(cfg, "obesity", "evidence_map.csv", map)

  counts <- as.data.frame(table(obesity_class = s$obesity_class, stratum = s$stratum),
                          responseName = "reports")
  counts <- counts[counts$reports > 0, ]
  write_out(cfg, "obesity", "evidence_counts.csv", counts)

  # Primary outcome, study by study, beside the pooled rows subgroups.R produced.
  d <- inv[inv$pooled, ]
  d <- merge(d, s[, c("study_id", "obesity_class", "bmi_as_reported")], by = "study_id")
  es <- escalc("RR", ai = ev_i, n1i = n_i, ci = ev_c, n2i = n_c, data = d)
  study_rows <- data.frame(
    stratum = d$stratum, obesity_class = d$obesity_class, row = d$study_id,
    bmi_as_reported = d$bmi_as_reported, k = 1,
    events_pes = d$ev_i, n_pes = d$n_i, events_comparator = d$ev_c, n_comparator = d$n_c,
    RR = exp(es$yi), ci_low = exp(es$yi - qnorm(0.975) * sqrt(es$vi)),
    ci_high = exp(es$yi + qnorm(0.975) * sqrt(es$vi)), I2 = NA, note = "", stringsAsFactors = FALSE)
  g <- sg[sg$variable == "Obesity", ]
  pooled_rows <- data.frame(
    stratum = g$stratum,
    obesity_class = ifelse(g$level %in% names(OBESITY_LABEL), OBESITY_LABEL[g$level], ""),
    row = ifelse(!g$level %in% names(OBESITY_LABEL), g$level,
                 ifelse(g$k >= 2, "pooled (DL)", "single study: not pooled")),
    bmi_as_reported = "", k = g$k, events_pes = NA, n_pes = NA, events_comparator = NA,
    n_comparator = NA, RR = g$RR, ci_low = g$ci_low, ci_high = g$ci_high, I2 = g$I2,
    note = ifelse(is.na(g$test_for_difference_p), g$note,
                  paste0("test for subgroup differences p = ", signif(g$test_for_difference_p, 2), "; ", g$note)),
    stringsAsFactors = FALSE)
  mort <- rbind(study_rows, pooled_rows)
  mort <- mort[order(mort$stratum != "Randomised trials", mort$obesity_class == "",
                     mort$obesity_class, mort$row %in% c("pooled (DL)", "single study: not pooled"), mort$row), ]
  write_out(cfg, "obesity", "mortality_by_obesity.csv", mort)

  obese <- s$study_id[s$obesity_subgroup %in% "obese_population"]
  keep <- function(df) if (nrow(df)) df[df$study_id %in% obese, , drop = FALSE] else df
  write_out(cfg, "obesity", "physiology_parallel_arm_obese.csv", keep(ph$parallel))
  write_out(cfg, "obesity", "physiology_within_patient_pes_vs_reference_obese.csv", keep(ph$within))
  write_out(cfg, "obesity", "physiology_within_patient_alternatives_obese.csv", keep(ph$alternatives))

  list(map = map, counts = counts, mortality = mort,
       physiology_rows = c(parallel = nrow(keep(ph$parallel)), within = nrow(keep(ph$within)),
                           alternatives = nrow(keep(ph$alternatives))))
}
