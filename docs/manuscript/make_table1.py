#!/usr/bin/env python3
"""Manuscript Table 1: randomised trials and parallel-group cohorts.

    PYTHONUTF8=1 python docs/manuscript/make_table1.py      # -> docs/manuscript/table1.md

Values as reported, from analysis/derived/studies.csv and baseline.csv. Arms are
shown balloon-guided / comparator; nothing is averaged across arms (B8).
"""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "analysis" / "derived"
ORDER = ["talmor_2008", "wang_2018", "obi_2018", "beitler_2019", "wang_2020", "sun_2024", "zhao_2025", "maia_2026", "tang_2026",
         "florio_2020", "dong_2023", "estoos_2023"]
DESIGN = {"rct": "RCT", "retrospective_cohort": "Retrospective cohort", "prospective_cohort": "Prospective cohort"}
ARDS = {"all_ards": "ARDS", "mixed_some_ards": "Acute lung injury/ARDS", "no_ards": "No ARDS (tracheostomy, weaning)"}
COMP = {"empirical_high_peep": "Empirical high PEEP-FiO2", "ardsnet_low_peep_fio2_table": "Low PEEP-FiO2 table",
        "ardsnet_high_peep_fio2_table": "High PEEP-FiO2 table", "clinician_discretion_usual_care": "Usual care",
        "best_compliance_peep": "Best static compliance", "fixed_peep": "Fixed PEEP",
        "other": "Other"}
TARGET = {"end_expiratory_ptp_positive_range": "End-expiratory PL", "driving_ptp_target": "Driving PL",
          "combined_end_expiratory_and_end_inspiratory": "End-expiratory and end-inspiratory PL", "other": "Other"}
CALIB = {"not_reported": "NR", "dynamic_occlusion_test": "Occlusion test", "baydur_occlusion_test": "Occlusion test",
         "signal_quality_verification_only": "Signal check only", "fixed_volume_no_validation": "Fixed volume, no validation"}


def fmt(r, var):
    v = r.get(f"{var}_value")
    if not v:
        return "NR"
    t, disp = r.get(f"{var}_value_type"), r.get(f"{var}_dispersion_type")
    if disp in ("sd", "se") and r.get(f"{var}_dispersion"):
        s = f"{v} ({r[f'{var}_dispersion']})"
    elif disp in ("iqr", "range", "ci95") and r.get(f"{var}_dispersion_low"):
        tag = {"ci95": "95% CI ", "range": "range "}.get(disp, "")
        s = f"{v} ({tag}{r[f'{var}_dispersion_low']}–{r[f'{var}_dispersion_high']})"
    else:
        s = v
    return s + ("ᵐ" if t == "median" else "")


def main():
    st = {r["study_id"]: r for r in csv.DictReader((D / "studies.csv").open(encoding="utf-8"))}
    bl = {}
    for r in csv.DictReader((D / "baseline.csv").open(encoding="utf-8")):
        bl.setdefault(r["study_id"], {})[r["arm_role"]] = r
    lines = ["| Study | Design | Population | n (balloon / comparator) | Age, y | PaO2/FiO2 | Balloon calibration | "
             "PEEP set by | PL target, cm H2O | Comparator |", "|" + "---|" * 10]
    for sid in ORDER:
        s, b = st[sid], bl.get(sid, {})
        i, c = b.get("intervention", {}), b.get("comparator", {})
        n = f"{i.get('n') or '?'} / {c.get('n') or '?'}" if b else s["n_analysed"]
        lo, hi = s["ptp_target_low"], s["ptp_target_high"]
        tgt = f"{lo} to {hi}" if lo or hi else "NR"
        if s["ptp_inspiratory_limit"]:
            tgt += f"; end-insp. ≤ {s['ptp_inspiratory_limit']}"
        comp = "; ".join(COMP.get(x, x) for x in s["comparator_types"].split("; ") if x)
        lines.append(" | ".join([
            f"| {s['first_author']} {s['year']}", DESIGN.get(s["study_design"], s["study_design"]),
            ARDS.get(s["ards_status"], s["ards_status"]), n,
            f"{fmt(i, 'age')} / {fmt(c, 'age')}" if b else "NR",
            f"{fmt(i, 'pao2_fio2_baseline')} / {fmt(c, 'pao2_fio2_baseline')}" if b else "NR",
            CALIB.get(s["calibration_method"], s["calibration_method"]),
            TARGET.get(s["peep_strategy"], s["peep_strategy"]), tgt, comp]) + " |")
    out = ROOT / "docs" / "manuscript" / "table1.md"
    out.write_text(
        "# Table 1. Randomized trials and parallel-group cohorts\n\n" + "\n".join(lines) +
        "\n\nValues are mean (SD) or medianᵐ (IQR) as reported, balloon-guided / comparator. NR, not reported; "
        "PL, transpulmonary pressure. Wang 2020 enrolled patients on venovenous ECMO; its comparator was the ELSO "
        "lung-rest strategy. Dong 2023 had a third, driving-pressure arm, and Maia 2026 had EIT and best-compliance arms, not shown. "
        "Source: analysis/derived/studies.csv, baseline.csv.\n", encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
