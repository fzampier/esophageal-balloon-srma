#!/usr/bin/env python3
"""Flatten reconciled extraction records into analysis-ready CSVs.

    python3 analysis/flatten.py                 # writes into analysis/derived/

Produces four tables:

  studies.csv    one row per study — characteristics and subgroup flags
  baseline.csv   one row per study x arm — Table 1 characteristics reported by arm
  outcomes.csv   one row per study x outcome x arm — the meta-analysis input
  effects.csv    one row per reported between-group estimate
  paired.csv     one row per within-patient paired contrast or omnibus test

Reads only extraction/reconciled/. Records still awaiting adjudication are
deliberately excluded: nothing reaches the analysis until a human has signed off.

A record coded `include_final: false` is left out, unless
studies/listed_not_analysed.csv names it: a report the screeners included at
full text although its extraction coded an eligibility criterion "no" (decision
A4). It gets a studies.csv row with `analysed` false and the reason, and no row
in the other three tables, so it is listed among the included studies and can
reach no pooled estimate and no physiology table.
"""

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "extraction" / "reconciled"
OUT = ROOT / "analysis" / "derived"
LISTED = ROOT / "studies" / "listed_not_analysed.csv"


def g(node, *keys, default=None):
    """Walk nested dicts tolerantly; any missing or null link yields default."""
    cur = node
    for k in keys:
        if not isinstance(cur, dict) or cur.get(k) is None:
            return default
        cur = cur[k]
    return cur


def stat(node, field="value"):
    return node.get(field) if isinstance(node, dict) else None


def hours(node):
    """A time from `continuousStat` in hours. Papers give it in days or hours and the
    record keeps the unit as printed; the column is named in hours, so convert here."""
    v = stat(node)
    if v is None:
        return None
    unit = (stat(node, "unit") or "").strip().lower()
    if unit in ("d", "day", "days"):
        return v * 24
    if unit in ("h", "hr", "hrs", "hour", "hours", ""):
        return v
    raise ValueError(f"timing unit not understood: {unit!r}")


STUDY_COLUMNS = [
    "study_id", "first_author", "year", "journal", "doi", "trial_acronym",
    "is_primary_report", "report_type", "cohort_id", "overlapping_cohort_with",
    "study_design", "comparison_structure", "randomised", "centers", "n_centers",
    "countries", "setting", "enrolment_start", "enrolment_end", "industry_funded",
    "n_enrolled", "n_analysed", "age", "age_type", "bmi", "bmi_type",
    "sex_female_n", "sex_female_total", "ards_status", "n_ards",
    "pao2_fio2_baseline", "apache_ii", "sofa",
    "calibration_method", "ptp_method", "peep_strategy",
    "ptp_target_low", "ptp_target_high", "ptp_inspiratory_limit",
    "comparator_types", "timing_reference_event", "timing_hours",
    "ards_subgroup", "obesity_subgroup", "timing_subgroup", "ptp_target_subgroups",
    "n_placement_attempted", "n_placement_successful", "complications_none_stated",
    "n_arms", "n_outcomes", "contact_author_needed",
    "analysed", "not_analysed_because",
]

OUTCOME_COLUMNS = [
    "study_id", "comparison_structure", "domain", "name_verbatim",
    "is_primary_outcome", "measure_type", "timepoint_type", "timepoint_days",
    "timepoint_hours", "timepoint_label", "is_longest_reported_mortality",
    "arm_label", "arm_role", "events", "total", "value_type", "value",
    "dispersion_type", "dispersion", "dispersion_low", "dispersion_high", "n",
    "unit", "derived",
    "within_arm_change", "within_arm_change_p",
]

EFFECT_COLUMNS = [
    "study_id", "domain", "name_verbatim", "timepoint_label", "measure_type",
    "effect_type", "effect_value", "effect_ci_low", "effect_ci_high",
    "effect_p_value", "effect_p_verbatim", "effect_adjusted",
    "adjustment_covariates", "test_used",
]

PAIRED_COLUMNS = [
    "study_id", "domain", "name_verbatim", "timepoint_label", "contrast_type",
    "contrast", "arm_label_a", "arm_label_b",
    "mean_difference", "sd_of_difference", "se_of_difference",
    "ci_low", "ci_high", "n_pairs", "p_value", "p_value_verbatim", "test_used",
    "unit", "derived",
]


BASELINE_STATS = ("age", "bmi", "pao2_fio2_baseline", "apache_ii", "sofa")
STAT_LEAVES = ("value_type", "value", "dispersion_type", "dispersion",
               "dispersion_low", "dispersion_high")
BASELINE_COLUMNS = (
    ["study_id", "arm_label", "arm_role", "n"]
    + [f"{f}_{leaf}" for f in BASELINE_STATS for leaf in STAT_LEAVES]
    + ["sex_female_n", "sex_female_total"]
)


def baseline_rows(rec):
    """Table 1 by arm. Cohort-level characteristics stay in studies.csv; a paper
    that reports baseline by arm only has them here and null there (B8)."""
    roles = {a.get("label"): a.get("role") for a in rec.get("arms") or []}
    for arm in g(rec, "population", "baseline_by_arm", default=[]) or []:
        row = {
            "study_id": g(rec, "record_meta", "study_id"),
            "arm_label": arm.get("arm_label"),
            "arm_role": roles.get(arm.get("arm_label")),
            "n": stat(arm.get("n")),
            "sex_female_n": stat(arm.get("sex_female"), "n"),
            "sex_female_total": stat(arm.get("sex_female"), "total"),
        }
        for f in BASELINE_STATS:
            for leaf in STAT_LEAVES:
                row[f"{f}_{leaf}"] = stat(arm.get(f), leaf)
        yield row


def study_row(rec):
    age, bmi = rec["population"].get("age"), rec["population"].get("bmi")
    return {
        "study_id": g(rec, "record_meta", "study_id"),
        "first_author": g(rec, "citation", "first_author"),
        "year": g(rec, "citation", "year"),
        "journal": g(rec, "citation", "journal"),
        "doi": g(rec, "citation", "doi"),
        "trial_acronym": g(rec, "citation", "trial_acronym"),
        "is_primary_report": g(rec, "citation", "is_primary_report"),
        "report_type": g(rec, "citation", "report_type"),
        "cohort_id": g(rec, "extraction_flags", "cohort_id"),
        "overlapping_cohort_with": "; ".join(
            g(rec, "extraction_flags", "overlapping_cohort_with", default=[]) or []
        ),
        "study_design": g(rec, "design", "study_design"),
        "comparison_structure": g(rec, "design", "comparison_structure"),
        "randomised": g(rec, "design", "randomised"),
        "centers": g(rec, "design", "centers"),
        "n_centers": stat(g(rec, "design", "n_centers")),
        "countries": "; ".join(g(rec, "design", "countries", default=[]) or []),
        "setting": stat(g(rec, "design", "setting")),
        "enrolment_start": g(rec, "design", "enrolment_start"),
        "enrolment_end": g(rec, "design", "enrolment_end"),
        "industry_funded": g(rec, "design", "industry_funded"),
        "n_enrolled": stat(g(rec, "population", "n_enrolled")),
        "n_analysed": stat(g(rec, "population", "n_analysed")),
        "age": stat(age), "age_type": stat(age, "value_type"),
        "bmi": stat(bmi), "bmi_type": stat(bmi, "value_type"),
        "sex_female_n": stat(g(rec, "population", "sex_female"), "n"),
        "sex_female_total": stat(g(rec, "population", "sex_female"), "total"),
        "ards_status": g(rec, "population", "ards_status"),
        "n_ards": stat(g(rec, "population", "n_ards"), "n"),
        "pao2_fio2_baseline": stat(g(rec, "population", "pao2_fio2_baseline")),
        "apache_ii": stat(g(rec, "population", "apache_ii")),
        "sofa": stat(g(rec, "population", "sofa")),
        "calibration_method": g(rec, "intervention", "balloon_calibration", "method"),
        "ptp_method": g(rec, "intervention", "ptp_computation", "method"),
        "peep_strategy": g(rec, "intervention", "peep_titration_target", "strategy"),
        "ptp_target_low": stat(
            g(rec, "intervention", "peep_titration_target", "end_expiratory_ptp_target_low_cmh2o")
        ),
        "ptp_target_high": stat(
            g(rec, "intervention", "peep_titration_target", "end_expiratory_ptp_target_high_cmh2o")
        ),
        "ptp_inspiratory_limit": stat(
            g(rec, "intervention", "peep_titration_target", "end_inspiratory_ptp_limit_cmh2o")
        ),
        "comparator_types": "; ".join(g(rec, "comparator", "comparator_types", default=[]) or []),
        "timing_reference_event": g(rec, "intervention", "timing", "reference_event"),
        "timing_hours": hours(g(rec, "intervention", "timing", "hours_from_reference")),
        "ards_subgroup": g(rec, "subgroup_flags", "ards_subgroup"),
        "obesity_subgroup": g(rec, "subgroup_flags", "obesity_subgroup"),
        "timing_subgroup": g(rec, "subgroup_flags", "timing_subgroup"),
        "ptp_target_subgroups": "; ".join(g(rec, "subgroup_flags", "ptp_target_subgroups", default=[]) or []),
        "n_placement_attempted": stat(g(rec, "feasibility", "n_placement_attempted")),
        "n_placement_successful": stat(g(rec, "feasibility", "n_placement_successful")),
        "complications_none_stated": g(rec, "feasibility", "complications_explicitly_none"),
        "n_arms": len(rec.get("arms", [])),
        "n_outcomes": len(rec.get("outcomes", [])),
        "contact_author_needed": g(rec, "extraction_flags", "contact_author_needed"),
    }


def outcome_rows(rec):
    sid = g(rec, "record_meta", "study_id")
    structure = g(rec, "design", "comparison_structure")
    roles = {a.get("label"): a.get("role") for a in rec.get("arms", [])}
    rows = []
    for out in rec.get("outcomes", []):
        tp = out.get("timepoint") or {}
        base = {
            "study_id": sid,
            "comparison_structure": structure,
            "domain": out.get("domain"),
            "name_verbatim": out.get("name_verbatim"),
            "is_primary_outcome": out.get("is_primary_outcome"),
            "measure_type": out.get("measure_type"),
            "timepoint_type": tp.get("type"),
            "timepoint_days": tp.get("days"),
            "timepoint_hours": tp.get("hours"),
            "timepoint_label": tp.get("label_verbatim"),
            "is_longest_reported_mortality": out.get("is_longest_reported_mortality"),
        }
        for d in out.get("data") or []:
            change = d.get("within_arm_change") or {}
            row = dict(base)
            row.update(
                {
                    "arm_label": d.get("arm_label"),
                    "arm_role": roles.get(d.get("arm_label")),
                    "events": d.get("events"),
                    "total": d.get("total"),
                    "value_type": d.get("value_type"),
                    "value": d.get("value"),
                    "dispersion_type": d.get("dispersion_type"),
                    "dispersion": d.get("dispersion"),
                    "dispersion_low": d.get("dispersion_low"),
                    "dispersion_high": d.get("dispersion_high"),
                    "n": d.get("n"),
                    "unit": d.get("unit"),
                    "derived": d.get("derived"),
                    "within_arm_change": change.get("value"),
                    "within_arm_change_p": change.get("p_value")
                    or change.get("p_value_verbatim"),
                }
            )
            rows.append(row)
    return rows


def outcome_context(rec, out):
    return {
        "study_id": g(rec, "record_meta", "study_id"),
        "domain": out.get("domain"),
        "name_verbatim": out.get("name_verbatim"),
        "timepoint_label": (out.get("timepoint") or {}).get("label_verbatim"),
    }


def effect_rows(rec):
    """One row per reported between-group estimate.

    Kept out of outcomes.csv because an outcome can carry several — commonly an
    unadjusted and an adjusted estimate — and folding them into the arm-level
    table would duplicate every arm row per estimate.
    """
    rows = []
    for out in rec.get("outcomes", []):
        for e in out.get("effect_estimates") or []:
            row = outcome_context(rec, out)
            row.update(
                {
                    "measure_type": out.get("measure_type"),
                    "effect_type": e.get("type"),
                    "effect_value": e.get("value"),
                    "effect_ci_low": e.get("ci_low"),
                    "effect_ci_high": e.get("ci_high"),
                    "effect_p_value": e.get("p_value"),
                    "effect_p_verbatim": e.get("p_value_verbatim"),
                    "effect_adjusted": e.get("adjusted"),
                    "adjustment_covariates": e.get("adjustment_covariates"),
                    "test_used": e.get("test_used"),
                }
            )
            rows.append(row)
    return rows


def paired_rows(rec):
    rows = []
    for out in rec.get("outcomes", []):
        for pe in out.get("paired_effects") or []:
            row = outcome_context(rec, out)
            row.update(
                {
                    "contrast_type": pe.get("contrast_type"),
                    "contrast": pe.get("contrast"),
                    "arm_label_a": pe.get("arm_label_a"),
                    "arm_label_b": pe.get("arm_label_b"),
                    "mean_difference": pe.get("mean_difference"),
                    "sd_of_difference": pe.get("sd_of_difference"),
                    "se_of_difference": pe.get("se_of_difference"),
                    "ci_low": pe.get("ci_low"),
                    "ci_high": pe.get("ci_high"),
                    "n_pairs": pe.get("n_pairs"),
                    "p_value": pe.get("p_value"),
                    "p_value_verbatim": pe.get("p_value_verbatim"),
                    "test_used": pe.get("test_used"),
                    "unit": pe.get("unit"),
                    "derived": pe.get("derived"),
                }
            )
            rows.append(row)
    return rows


def write(path, columns, rows):
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"{path}: {len(rows)} row(s)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=SRC, help="directory of reconciled records")
    ap.add_argument("--out", type=Path, default=OUT, help="output directory for CSVs")
    ap.add_argument("--listed", type=Path, default=LISTED,
                    help="CSV of excluded-by-extraction reports listed as included but not analysed")
    args = ap.parse_args()
    src, out = args.src, args.out
    listed = ({r["study_id"]: r["not_analysed_because"]
               for r in csv.DictReader(args.listed.open(encoding="utf-8"))}
              if args.listed.exists() else {})

    out.mkdir(parents=True, exist_ok=True)
    records = [json.loads(p.read_text()) for p in sorted(src.glob("*.json"))]
    if not records:
        print(f"No reconciled records in {src}.")
        return 0

    included = [r for r in records if g(r, "eligibility", "include_final")]
    listed_only = [r for r in records
                   if not g(r, "eligibility", "include_final")
                   and g(r, "record_meta", "study_id") in listed]
    excluded = len(records) - len(included) - len(listed_only)
    for sid in sorted(set(listed) - {g(r, "record_meta", "study_id") for r in listed_only}):
        print(f"warning: {sid} is in {args.listed.name} but is not an excluded reconciled record")

    study_rows = [dict(study_row(r), analysed=True) for r in included]
    study_rows += [dict(study_row(r), analysed=False,
                        not_analysed_because=listed[g(r, "record_meta", "study_id")])
                   for r in listed_only]
    write(out / "studies.csv", STUDY_COLUMNS, study_rows)
    write(out / "baseline.csv", BASELINE_COLUMNS,
          [row for r in included + listed_only for row in baseline_rows(r)])
    write(out / "outcomes.csv", OUTCOME_COLUMNS,
          [row for r in included for row in outcome_rows(r)])
    write(out / "effects.csv", EFFECT_COLUMNS,
          [row for r in included for row in effect_rows(r)])
    write(out / "paired.csv", PAIRED_COLUMNS,
          [row for r in included for row in paired_rows(r)])

    print(f"\n{len(included)} study/studies included and analysed, "
          f"{len(listed_only)} included but listed only, {excluded} excluded.")

    cohorts = {}
    for r in included:
        cid = g(r, "extraction_flags", "cohort_id")
        if cid:
            cohorts.setdefault(cid, []).append(r)

    shared = {cid: rs for cid, rs in cohorts.items() if len(rs) > 1}
    if shared:
        print("\nShared cohorts — each contributes to a pooled estimate at most once:")
        for cid, rs in sorted(shared.items()):
            print(f"  {cid}")
            for r in sorted(rs, key=lambda x: not g(x, "citation", "is_primary_report")):
                role = "primary" if g(r, "citation", "is_primary_report") else "secondary"
                n = stat(g(r, "population", "n_analysed"))
                print(f"    {g(r, 'record_meta', 'study_id'):<24} {role:<10} n={n}")
        print("\n  Use the primary report for pooling unless the secondary reports an "
              "outcome the primary does not.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
