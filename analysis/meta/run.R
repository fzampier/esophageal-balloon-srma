# Run the meta-analyses.
#
#   Rscript analysis/meta/run.R                  # reconciled data -> analysis/results/
#   Rscript analysis/meta/run.R --provisional    # development only -> analysis/derived/provisional/results/
#
# Reconciled data come from `python analysis/flatten.py` (extraction/reconciled/
# -> analysis/derived/). Provisional data come from
#   python analysis/reconcile.py --provisional analysis/derived/provisional/records
#   python analysis/flatten.py --src analysis/derived/provisional/records --out analysis/derived/provisional/csv

here <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE))[1])
source(file.path(here, "common.R"))
source(file.path(here, "mortality.R"))
source(file.path(here, "vfd.R"))
source(file.path(here, "secondary.R"))
source(file.path(here, "subgroups.R"))
source(file.path(here, "physiology.R"))
source(file.path(here, "obesity.R"))
source(file.path(here, "adjusted_metareg.R"))

cfg <- parse_args()
tb <- load_tables(cfg)
dir.create(cfg$out, recursive = TRUE, showWarnings = FALSE)
cat("data:", cfg$data, "\n", "stamp:", cfg$stamp, "\n\n")
writeLines(c(cfg$stamp, paste("data:", cfg$data), paste("run:", format(Sys.time())),
             paste("metafor", as.character(packageVersion("metafor")))),
           file.path(cfg$out, "RUN_INFO.txt"))

m <- run_mortality(cfg, tb)
cat("== Mortality at longest follow-up\n")
print(m$inventory[, c("study_id", "stratum", "timepoint", "ev_i", "n_i", "ev_c", "n_c",
                      "pooled", "not_pooled_because")], row.names = FALSE)
cat("\n")
print(transform(m$pooled, estimate = round(estimate, 2), ci_low = round(ci_low, 2),
                ci_high = round(ci_high, 2), tau2 = round(tau2, 3), I2 = round(I2, 1),
                Q_p = round(Q_p, 3)), row.names = FALSE)

v <- run_vfd(cfg, tb)
cat("\n== Ventilator-free days (primary: table, not pooled)\n")
print(v$table[, c("study_id", "window_days", "intervention", "n_intervention", "comparator",
                  "n_comparator", "in_table", "not_used_because")], row.names = FALSE)
cat("\n-- sensitivity: converted means\n")
print(v$pooled, row.names = FALSE)

show <- function(title, res) {
  cat(sprintf("\n== %s\n", title))
  if (is.null(res)) { cat("  no data\n"); return(invisible()) }
  print(res$inventory[, intersect(c("study_id", "stratum", "outcome", "used", "not_used_because"),
                                  names(res$inventory))], row.names = FALSE)
  print(res$pooled[, c("stratum", "analysis", "k", "model")], row.names = FALSE)
}
show("Duration of mechanical ventilation", run_continuous(cfg, tb, "duration_mv", "duration_mechanical_ventilation", "Duration of mechanical ventilation"))
show("ICU length of stay", run_continuous(cfg, tb, "icu_los", "icu_length_of_stay", "ICU length of stay"))
show("Hospital length of stay", run_continuous(cfg, tb, "hospital_los", "hospital_length_of_stay", "Hospital length of stay"))
show("Barotrauma", run_binary(cfg, tb, "barotrauma", "barotrauma", "Barotrauma"))

sg <- run_subgroups(cfg, m$inventory, tb$studies)
cat("\n== Subgroups of the primary outcome (exploratory)\n")
print(sg[, c("stratum", "variable", "level", "k", "note")], row.names = FALSE)

ph <- run_physiology(cfg, tb)
cat("\n== Physiology (descriptive; D13-D16)\n")
print(ph$coverage, row.names = FALSE)
cat("rows: parallel", nrow(ph$parallel), " within (Pes vs reference)", nrow(ph$within),
    " alternatives", nrow(ph$alternatives), " unmapped names", nrow(ph$unmapped), "\n")

ob <- run_obesity(cfg, tb, m$inventory, sg, ph)
cat("
== Obesity (evidence map; no new pooled estimate; D23)
")
print(ob$counts, row.names = FALSE)
print(transform(ob$mortality[, c("stratum", "obesity_class", "row", "k", "RR", "ci_low", "ci_high")],
                RR = round(RR, 2), ci_low = round(ci_low, 2), ci_high = round(ci_high, 2)), row.names = FALSE)
cat("physiology rows in obese populations:", paste(names(ob$physiology_rows), ob$physiology_rows, collapse = ", "), "
")

adj <- run_adjusted_observational(cfg, tb, m$inventory)
cat("\n== Adjusted mortality estimates, observational studies (listed, not pooled; D32)\n")
print(adj[, c("study_id", "name_verbatim", "effect_type", "effect_value", "effect_ci_low", "effect_ci_high")], row.names = FALSE)

mr <- run_pf_metaregression(cfg, tb, m$inventory)
cat("\n== Meta-regression on baseline PaO2/FiO2, RCTs (exploratory; D35)\n")
print(mr, row.names = FALSE)
