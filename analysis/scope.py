#!/usr/bin/env python3
"""The analysis scope: every record field that reaches analysis/derived/.

Reconciliation covers these fields and no others (Option B, chosen by the
co-authors 2026-09-15). A difference between the reviewers outside this set is
reported in the agreement statistics but never adjudicated, and the analysis
cannot read it: tests/test_pipeline.py blanks every out-of-scope field in the
fixtures and requires flatten.py to produce byte-identical CSVs. Adding a column
to flatten.py without adding its field here fails that test.

    python3 analysis/scope.py        # list the fields

Paths are written with array indices removed, as `outcomes.data.value`. A path
is in scope when it names one of these fields, lies beneath one, or is an array
element or object whose subtree contains one — a one-sided outcome is reported
as the single row `outcomes[...]`, and it carries in-scope values.
"""

import re
import sys

ANALYSIS_FIELDS = (
    # identity
    "record_meta.study_id",
    # studies.csv
    "citation.first_author",
    "citation.year",
    "citation.journal",
    "citation.doi",
    "citation.trial_acronym",
    "citation.is_primary_report",
    "citation.report_type",
    "extraction_flags.cohort_id",
    "extraction_flags.overlapping_cohort_with",
    "extraction_flags.contact_author_needed",
    "design.study_design",
    "design.comparison_structure",
    "design.randomised",
    "design.centers",
    "design.n_centers.value",
    "design.countries",
    "design.setting.value",
    "design.enrolment_start",
    "design.enrolment_end",
    "design.industry_funded",
    "population.n_enrolled.value",
    "population.n_analysed.value",
    "population.age.value",
    "population.age.value_type",
    "population.bmi.value",
    "population.bmi.value_type",
    "population.sex_female.n",
    "population.sex_female.total",
    "population.ards_status",
    "population.n_ards.n",
    "population.pao2_fio2_baseline.value",
    "population.apache_ii.value",
    "population.sofa.value",
    "intervention.balloon_calibration.method",
    "intervention.ptp_computation.method",
    "intervention.peep_titration_target.strategy",
    "intervention.peep_titration_target.end_expiratory_ptp_target_low_cmh2o.value",
    "intervention.peep_titration_target.end_expiratory_ptp_target_high_cmh2o.value",
    "intervention.peep_titration_target.end_inspiratory_ptp_limit_cmh2o.value",
    "intervention.timing.reference_event",
    "intervention.timing.hours_from_reference.value",
    "comparator.comparator_types",
    "subgroup_flags.ards_subgroup",
    "subgroup_flags.obesity_subgroup",
    "subgroup_flags.timing_subgroup",
    "subgroup_flags.ptp_target_subgroups",
    "feasibility.n_placement_attempted.value",
    "feasibility.n_placement_successful.value",
    "feasibility.complications_explicitly_none",
    # inclusion filter; include_final must agree with its criteria
    "eligibility.include_final",
    "eligibility.criteria",
    # baseline.csv: Table 1 by arm (B8). Papers that report baseline by arm leave
    # the cohort-level fields above null by design, so this block is the record.
    "population.baseline_by_arm.arm_label",
    "population.baseline_by_arm.n.value",
    "population.baseline_by_arm.age.value_type",
    "population.baseline_by_arm.age.value",
    "population.baseline_by_arm.age.dispersion_type",
    "population.baseline_by_arm.age.dispersion",
    "population.baseline_by_arm.age.dispersion_low",
    "population.baseline_by_arm.age.dispersion_high",
    "population.baseline_by_arm.bmi.value_type",
    "population.baseline_by_arm.bmi.value",
    "population.baseline_by_arm.bmi.dispersion_type",
    "population.baseline_by_arm.bmi.dispersion",
    "population.baseline_by_arm.bmi.dispersion_low",
    "population.baseline_by_arm.bmi.dispersion_high",
    "population.baseline_by_arm.pao2_fio2_baseline.value_type",
    "population.baseline_by_arm.pao2_fio2_baseline.value",
    "population.baseline_by_arm.pao2_fio2_baseline.dispersion_type",
    "population.baseline_by_arm.pao2_fio2_baseline.dispersion",
    "population.baseline_by_arm.pao2_fio2_baseline.dispersion_low",
    "population.baseline_by_arm.pao2_fio2_baseline.dispersion_high",
    "population.baseline_by_arm.apache_ii.value_type",
    "population.baseline_by_arm.apache_ii.value",
    "population.baseline_by_arm.apache_ii.dispersion_type",
    "population.baseline_by_arm.apache_ii.dispersion",
    "population.baseline_by_arm.apache_ii.dispersion_low",
    "population.baseline_by_arm.apache_ii.dispersion_high",
    "population.baseline_by_arm.sofa.value_type",
    "population.baseline_by_arm.sofa.value",
    "population.baseline_by_arm.sofa.dispersion_type",
    "population.baseline_by_arm.sofa.dispersion",
    "population.baseline_by_arm.sofa.dispersion_low",
    "population.baseline_by_arm.sofa.dispersion_high",
    "population.baseline_by_arm.sex_female.n",
    "population.baseline_by_arm.sex_female.total",
    # arms: n_arms, and arm_role for every outcome row
    "arms.label",
    "arms.role",
    # outcomes.csv
    "outcomes.domain",
    "outcomes.name_verbatim",
    "outcomes.is_primary_outcome",
    "outcomes.measure_type",
    "outcomes.timepoint.type",
    "outcomes.timepoint.days",
    "outcomes.timepoint.hours",
    "outcomes.timepoint.label_verbatim",
    "outcomes.is_longest_reported_mortality",
    "outcomes.data.arm_label",
    "outcomes.data.events",
    "outcomes.data.total",
    "outcomes.data.value_type",
    "outcomes.data.value",
    "outcomes.data.dispersion_type",
    "outcomes.data.dispersion",
    "outcomes.data.dispersion_low",
    "outcomes.data.dispersion_high",
    "outcomes.data.n",
    "outcomes.data.unit",
    "outcomes.data.derived",
    "outcomes.data.within_arm_change.value",
    "outcomes.data.within_arm_change.p_value",
    "outcomes.data.within_arm_change.p_value_verbatim",
    # effects.csv
    "outcomes.effect_estimates.type",
    "outcomes.effect_estimates.value",
    "outcomes.effect_estimates.ci_low",
    "outcomes.effect_estimates.ci_high",
    "outcomes.effect_estimates.p_value",
    "outcomes.effect_estimates.p_value_verbatim",
    "outcomes.effect_estimates.adjusted",
    "outcomes.effect_estimates.adjustment_covariates",
    "outcomes.effect_estimates.test_used",
    # paired.csv
    "outcomes.paired_effects.contrast_type",
    "outcomes.paired_effects.contrast",
    "outcomes.paired_effects.arm_label_a",
    "outcomes.paired_effects.arm_label_b",
    "outcomes.paired_effects.mean_difference",
    "outcomes.paired_effects.sd_of_difference",
    "outcomes.paired_effects.se_of_difference",
    "outcomes.paired_effects.ci_low",
    "outcomes.paired_effects.ci_high",
    "outcomes.paired_effects.n_pairs",
    "outcomes.paired_effects.p_value",
    "outcomes.paired_effects.p_value_verbatim",
    "outcomes.paired_effects.test_used",
    "outcomes.paired_effects.unit",
    "outcomes.paired_effects.derived",
)


def normalize(path):
    """`outcomes[mortality @28d].data[pes guided].value` -> `outcomes.data.value`."""
    return re.sub(r"\[[^\]]*\]", "", path)


def in_scope(path):
    n = normalize(path)
    return any(f == n or f.startswith(n + ".") or n.startswith(f + ".") for f in ANALYSIS_FIELDS)


if __name__ == "__main__":
    print("\n".join(ANALYSIS_FIELDS))
    sys.exit(0)
