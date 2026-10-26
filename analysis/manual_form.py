#!/usr/bin/env python3
"""Blank extraction workbooks for the authors' duplicate manual extraction (D83).

    python analysis/manual_form.py 2026-10-19            # one workbook per extractor
    python analysis/manual_form.py 2026-10-19 --desktop  # also copy them to the Desktop

Each extractor gets a byte-identical blank workbook with his name in the file name. Only
facts that were never a reading of a paper are filled in: the bibliography, DOI links, the
reports that share patients and which one is primary (the authors' decisions, C16, C19,
C23), and which reports are analysed (A4). No arm label, timepoint, outcome name or value
comes from the model extraction: the two authors' extractions must be independent of it
and of each other, because the model extraction is now the concordance check (as D51 made
the models' screen a check on the authors' screen).

What is extracted is what the paper uses: study characteristics (studies.csv, Table 1,
subgroups), baseline by arm (baseline.csv), the six clinical outcomes (the pooled analyses),
the physiologic measures of the descriptive tables (D14, D15) and reported effect estimates
(adjusted mortality, within-patient differences). Risk of bias is not re-extracted: every
judgment is already the senior author's.

Row 1 of each data sheet is the heading in plain words; row 2 (grey) is the machine name
the import script reads. Dropdowns show plain labels; the hidden sheet Lists maps each label
to its schema code.
"""

import argparse
import csv
import shutil
import sys
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "extraction" / "manual" / "forms"
EXTRACTORS = ["Anton", "Michal"]

GREY = Font(color="808080", italic=True, size=8)
BOLD = Font(bold=True)
HEAD_FILL = PatternFill("solid", fgColor="DDEBF7")
PREFILL_FILL = PatternFill("solid", fgColor="F2F2F2")
WRAP = Alignment(wrap_text=True, vertical="top")

# label shown in the dropdown -> schema code
LISTS = {
    "design": [
        ("Randomized trial", "rct"), ("Cluster randomized trial", "cluster_rct"),
        ("Prospective cohort", "prospective_cohort"), ("Retrospective cohort", "retrospective_cohort"),
        ("Case-control", "case_control"), ("Physiologic crossover (each patient under several conditions)",
                                            "crossover_physiologic"),
        ("Before-after, single group", "before_after_single_group"), ("Other", "other")],
    "structure": [
        ("Parallel groups (different patients per arm)", "parallel_arms"),
        ("Within-patient (the same patients under each condition)", "within_patient_crossover"),
        ("Single group, no comparison", "single_group_no_comparison")],
    "yesno": [("Yes", "yes"), ("No", "no")],
    "ards": [("All patients had ARDS", "all_ards"), ("Some patients had ARDS", "mixed_some_ards"),
             ("No patient had ARDS", "no_ards"), ("Not reported", "not_reported")],
    "calibration": [
        ("Occlusion test (Baydur, static)", "baydur_occlusion_test"),
        ("Dynamic occlusion test", "dynamic_occlusion_test"), ("Minimal volume", "minimal_volume"),
        ("Best compliance / pressure-volume filling", "best_compliance_or_pressure_volume_filling"),
        ("Manufacturer's recommended volume", "manufacturer_recommended_volume"),
        ("Fixed volume, no validation", "fixed_volume_no_validation"),
        ("Signal quality check only", "signal_quality_verification_only"),
        ("Not reported", "not_reported"), ("Other", "other")],
    "peep_strategy": [
        ("End-expiratory PL kept in a positive range", "end_expiratory_ptp_positive_range"),
        ("End-expiratory PL set to zero", "end_expiratory_ptp_zero"),
        ("End-inspiratory PL limit", "end_inspiratory_ptp_limit"),
        ("Driving PL target", "driving_ptp_target"),
        ("Combined end-expiratory and end-inspiratory", "combined_end_expiratory_and_end_inspiratory"),
        ("Other", "other"), ("Not reported", "not_reported")],
    "comparator": [
        ("ARDSNet low PEEP/FiO2 table", "ardsnet_low_peep_fio2_table"),
        ("ARDSNet high PEEP/FiO2 table", "ardsnet_high_peep_fio2_table"),
        ("Empirical high PEEP", "empirical_high_peep"),
        ("Clinician's discretion / usual care", "clinician_discretion_usual_care"),
        ("Best-compliance PEEP", "best_compliance_peep"), ("Decremental PEEP trial", "decremental_peep_trial"),
        ("Fixed PEEP", "fixed_peep"), ("ZEEP", "zeep"), ("No comparator", "no_comparator"),
        ("Other", "other"), ("Not reported", "not_reported")],
    "ards_sub": [("ARDS only", "ards_only"), ("Mixed", "mixed"), ("No ARDS", "non_ards"), ("Unclear", "unclear")],
    "obesity_sub": [("Obese population", "obese_population"), ("Mixed BMI", "mixed_bmi"),
                    ("Non-obese population", "non_obese_population"), ("Unclear", "unclear")],
    "timing_sub": [("Early (balloon within 48 h)", "early_within_48h"), ("Late (after 48 h)", "late_after_48h"),
                   ("Mixed", "mixed"), ("Unclear", "unclear")],
    "pl_target_sub": [
        ("Absolute end-expiratory PL, positive", "absolute_positive_end_expiratory_ptp"),
        ("Absolute end-expiratory PL, zero", "absolute_zero_end_expiratory_ptp"),
        ("Elastance-derived PL", "elastance_derived"), ("Driving PL", "driving_ptp"),
        ("Other", "other"), ("Unclear", "unclear")],
    "role": [("Balloon-guided (intervention)", "intervention"), ("Comparator", "comparator"),
             ("Reference condition (within-patient)", "reference_condition"),
             ("Single group", "single_group"),
             ("Alternative titration condition", "alternative_titration_condition"),
             ("Other condition", "other_condition")],
    "value_type": [("Mean", "mean"), ("Median", "median")],
    "spread": [("SD", "sd"), ("SE", "se"), ("IQR", "iqr"), ("Range", "range"), ("95% CI", "ci95"),
               ("None given", "none")],
    "clinical": [("Mortality", "mortality"),
                 ("Duration of mechanical ventilation", "duration_mechanical_ventilation"),
                 ("Ventilator-free days", "ventilator_free_days"),
                 ("ICU length of stay", "icu_length_of_stay"),
                 ("Hospital length of stay", "hospital_length_of_stay"),
                 ("Barotrauma", "barotrauma")],
    "reported": [("Reported", "reported"), ("Not reported in this report", "not_reported")],
    "measure": [("PaO2/FiO2", "pao2_fio2"), ("Oxygenation index", "oxygenation_index"),
                ("Respiratory-system compliance", "compliance_rs"),
                ("Respiratory-system elastance", "elastance_rs"),
                ("Plateau pressure", "plateau_pressure"), ("Driving pressure (airway)", "driving_pressure"),
                ("End-expiratory transpulmonary pressure", "pl_end_expiratory"),
                ("Driving transpulmonary pressure", "pl_driving"),
                ("PEEP set", "peep_set"), ("Total PEEP (only if no set PEEP reported)", "peep_total")],
    "phys_time": [("Baseline (before titration)", "baseline"),
                  ("First measurement after titration", "first_after"),
                  ("Latest reported", "latest"), ("Other timepoint", "other")],
    "effect_type": [("Odds ratio", "odds_ratio"), ("Hazard ratio", "hazard_ratio"), ("Risk ratio", "risk_ratio"),
                    ("Risk difference", "risk_difference"), ("Mean difference", "mean_difference"),
                    ("Standardized mean difference", "standardised_mean_difference"), ("Other", "other")],
    "effect_for": [("Mortality", "mortality")] + [(lab, code) for lab, code in [
        ("PaO2/FiO2", "pao2_fio2"), ("Oxygenation index", "oxygenation_index"),
        ("Respiratory-system compliance", "compliance_rs"), ("Respiratory-system elastance", "elastance_rs"),
        ("Plateau pressure", "plateau_pressure"), ("Driving pressure (airway)", "driving_pressure"),
        ("End-expiratory transpulmonary pressure", "pl_end_expiratory"),
        ("Driving transpulmonary pressure", "pl_driving"), ("PEEP set", "peep_set")]] + [
        ("Other clinical outcome", "other_clinical")],
    "done": [("Done", "done"), ("In progress", "in_progress")],
}

# a continuous measure takes six columns: value, mean/median, spread type, spread, low, high
def cont(label, key):
    return [(f"{label}", f"{key}_value", None), (f"{label}: mean or median", f"{key}_value_type", "value_type"),
            (f"{label}: spread type", f"{key}_spread_type", "spread"), (f"{label}: SD or SE", f"{key}_spread", None),
            (f"{label}: low (IQR, range, CI)", f"{key}_low", None), (f"{label}: high", f"{key}_high", None)]


STUDY_COLS = (
    [("Study design", "study_design", "design"), ("Comparison structure", "comparison_structure", "structure"),
     ("Randomized?", "randomised", "yesno"), ("Countries", "countries", None),
     ("Setting (type of ICU, hospital)", "setting", None),
     ("Patients enrolled (n)", "n_enrolled", None), ("Patients analysed (n)", "n_analysed", None),
     ("ARDS in the population", "ards_status", "ards"),
     ("Balloon calibration method", "calibration_method", "calibration"),
     ("How PEEP was set with the balloon", "peep_strategy", "peep_strategy"),
     ("PL target, lower bound (cm H2O)", "ptp_target_low", None),
     ("PL target, upper bound (cm H2O)", "ptp_target_high", None),
     ("End-inspiratory PL limit (cm H2O)", "ptp_inspiratory_limit", None),
     ("Comparator 1", "comparator_1", "comparator"), ("Comparator 2 (if any)", "comparator_2", "comparator"),
     ("Subgroup: ARDS", "ards_subgroup", "ards_sub"), ("Subgroup: obesity", "obesity_subgroup", "obesity_sub"),
     ("Subgroup: timing of the balloon", "timing_subgroup", "timing_sub"),
     ("Subgroup: PL target type", "ptp_target_subgroup", "pl_target_sub")]
    + cont("Age, whole study (y)", "age") + cont("BMI, whole study", "bmi")
    + [("Women, whole study (n)", "sex_female_n", None), ("Of how many patients (n)", "sex_female_total", None)]
    + cont("PaO2/FiO2 at baseline, whole study", "pao2_fio2") + cont("APACHE II, whole study", "apache_ii")
    + cont("SOFA, whole study", "sofa")
    + [("Pages / tables used", "pages", None), ("Notes", "notes", None)])

ARM_COLS = (
    [("Report", "study_id", "report"), ("Arm or condition, as the paper names it", "arm_label", None),
     ("Role", "arm_role", "role"), ("Patients in this arm (n)", "n", None)]
    + cont("Age (y)", "age") + cont("BMI", "bmi") + cont("PaO2/FiO2 at baseline", "pao2_fio2")
    + cont("APACHE II", "apache_ii") + cont("SOFA", "sofa")
    + [("Women (n)", "sex_female_n", None), ("Pages / tables used", "pages", None), ("Notes", "notes", None)])

CLIN_COLS = [
    ("Report", "study_id", "report"), ("Outcome", "domain", "clinical"),
    ("Reported?", "reported", "reported"), ("Outcome as the paper defines it", "definition", None),
    ("Timepoint as the paper states it", "timepoint_label", None),
    ("Timepoint in days (if a fixed day)", "timepoint_days", None),
    ("Longest mortality follow-up in this report?", "is_longest_reported_mortality", "yesno"),
    ("Arm or condition, as the paper names it", "arm_label", None), ("Role", "arm_role", "role"),
    ("Events (n)", "events", None), ("Out of (n)", "total", None),
    ("Value", "value", None), ("Mean or median", "value_type", "value_type"),
    ("Spread type", "spread_type", "spread"), ("SD or SE", "spread", None),
    ("Low (IQR, range, CI)", "low", None), ("High", "high", None),
    ("Patients behind this value (n)", "n", None), ("Unit", "unit", None),
    ("Page / table", "pages", None), ("Notes", "notes", None)]

PHYS_COLS = [
    ("Report", "study_id", "report"), ("Measure", "measure", "measure"), ("Reported?", "reported", "reported"),
    ("Measure as the paper names it", "name_verbatim", None),
    ("Timepoint", "timepoint", "phys_time"), ("Timepoint as the paper states it", "timepoint_label", None),
    ("Hours after titration (if stated)", "timepoint_hours", None),
    ("Arm or condition, as the paper names it", "arm_label", None), ("Role", "arm_role", "role"),
    ("Value", "value", None), ("Mean or median", "value_type", "value_type"),
    ("Spread type", "spread_type", "spread"), ("SD or SE", "spread", None),
    ("Low (IQR, range, CI)", "low", None), ("High", "high", None),
    ("Patients behind this value (n)", "n", None), ("Unit", "unit", None),
    ("Page / table", "pages", None), ("Notes", "notes", None)]

EFFECT_COLS = [
    ("Report", "study_id", "report"), ("Effect for", "effect_for", "effect_for"),
    ("Outcome as the paper names it", "name_verbatim", None), ("Timepoint as stated", "timepoint_label", None),
    ("Compared: first arm or condition", "arm_label_a", None),
    ("Compared: second arm or condition", "arm_label_b", None),
    ("Effect type", "effect_type", "effect_type"), ("Estimate", "effect_value", None),
    ("95% CI low", "ci_low", None), ("95% CI high", "ci_high", None), ("P value as printed", "p_value", None),
    ("Adjusted?", "adjusted", "yesno"), ("Adjusted for", "covariates", None),
    ("Page / table", "pages", None), ("Notes", "notes", None)]

INSTRUCTIONS = """\
Extraction form: esophageal pressure-guided PEEP systematic review
Two authors extract every report independently. Do not consult each other, the earlier dataset workbook, or any model output. Disagreements are resolved afterwards.

Getting the papers
- The Reports sheet lists all 36 reports with a DOI link. Please download each paper and its supplement through your own library access.
- Some data sit only in a supplement: Boesing 2022 and Tonelli 2023 give demographics in Additional file 1; Maia 2026 gives mortality in Table 4S of its supplement. Rowley 2021 and Gluck 1995 have no DOI (see the Reports sheet).
- Gluck 1995 and Thind 2022 are included but not analysed: fill only their row on the Study sheet.

What to record
- Record what the paper reports, as printed: no unit conversion, no median-to-mean conversion, no summing of arms into an overall value.
- If the paper gives a value only by arm, leave the whole-study columns blank (they are not calculated from arms).
- Medians stay medians: choose "Median" and give the IQR or range as low/high.
- Blank means not reported. On the Clinical outcomes and Physiology sheets, if the paper does not report an outcome or measure at all, keep one row for it and choose "Not reported in this report".
- Give the page or table for every value (for example "p 4, Table 2" or "Suppl Table 4S").
- Every row with numbers needs the arm or condition name as the paper uses it and its Role; the Role is how the two extractions are matched.

The sheets
1. Reports: the list of reports, with DOI, notes and a Done column for your own tracking.
2. Study: one row per report (design, population, how PEEP was set, comparator, subgroup codes, whole-study baseline).
3. Arms: one row per arm or condition (baseline characteristics by arm). Four blank rows per report; insert more if needed.
4. Clinical outcomes: mortality, duration of ventilation, ventilator-free days, ICU and hospital stay, barotrauma. One row per arm, outcome and timepoint. Record every timepoint reported, and mark the longest mortality follow-up. Copy a row (insert below) for each further arm or timepoint.
5. Physiology: PaO2/FiO2 (or oxygenation index), respiratory-system compliance or elastance, plateau and driving pressure, end-expiratory and driving transpulmonary pressure, PEEP set. For each arm or condition, three timepoints: baseline (before titration), the first measurement after titration, and the latest reported. Record compliance and elastance separately as printed; give total PEEP only where no set PEEP is reported.
6. Effects: effect estimates the paper reports: adjusted mortality (odds or hazard ratios, with what they were adjusted for) and, in within-patient studies, the paired differences between conditions for the physiologic measures.

Subgroup codes (Study sheet)
- ARDS: ARDS only / mixed / no ARDS, for the population studied.
- Obesity: "obese population" when enrolment required obesity or the reported mean or median BMI is at least 30 kg/m2; otherwise mixed BMI or non-obese.
- Timing: early if the balloon guided PEEP within 48 h of intubation, ICU admission or ARDS onset, late if after 48 h. "Unclear" when timing is given only from enrolment (in a trial the intervention always starts at enrolment).
- PL target type: what the balloon-guided arm targeted: absolute end-expiratory PL (positive or zero), elastance-derived PL (airway pressure x lung/respiratory-system elastance), or driving PL. A study using two strategies on the same patients: choose one and note the other.

Not needed: risk of bias (every judgment has already been made by F.G.Z.), funding, enrolment dates, catheter placement details.
Questions: Fernando.
"""


def reports():
    man = {r["study_id"]: r for r in csv.DictReader(open(ROOT / "studies" / "manifest.csv", encoding="utf-8"))}
    st = {r["study_id"]: r for r in csv.DictReader(open(ROOT / "analysis" / "derived" / "studies.csv", encoding="utf-8"))}
    cohort_label = {"epvent1": "EPVent-1", "epvent2": "EPVent-2", "mannheim_boesing": "Mannheim (Boesing)"}
    notes = {
        "boesing_2022": "Demographics in Additional file 1 (supplement).",
        "tonelli_2023": "Demographics in Additional file 1 (supplement).",
        "maia_2026": "Four-arm trial; mortality in supplement Table 4S.",
        "rowley_2021": "Respiratory Care 2021; no DOI.",
        "gluck_1995": "Critical Care Medicine 1995; no DOI.",
    }
    rows = []
    for sid in sorted(man, key=lambda s: (man[s]["first_author"].lower(), man[s]["year"], s)):
        m, s = man[sid], st.get(sid, {})
        analysed = s.get("analysed", "True") in ("True", "true", "1")
        coh = ""
        if s.get("cohort_id"):
            coh = (f"Shares patients with other reports ({cohort_label.get(s['cohort_id'], s['cohort_id'])}); "
                   + ("primary report" if s.get("is_primary_report") == "True" else "secondary report"))
        rows.append({
            "study_id": sid, "author": m["first_author"], "year": m["year"], "journal": m["journal"],
            "title": m["title"], "doi": m["doi"].strip(),
            "scope": "All sheets" if analysed else "Study sheet only (listed, not analysed)",
            "note": notes.get(sid, ""), "cohort": coh})
    return rows


def header(ws, cols, widths=None):
    for j, (label, machine, _) in enumerate(cols, 1):
        c = ws.cell(row=1, column=j, value=label)
        c.font, c.fill, c.alignment = BOLD, HEAD_FILL, WRAP
        ws.cell(row=2, column=j, value=machine).font = GREY
        ws.column_dimensions[get_column_letter(j)].width = (widths or {}).get(machine, max(12, min(30, len(label) + 2)))
    ws.row_dimensions[1].height = 45
    ws.freeze_panes = "B3"


def validate(ws, cols, first_row, last_row, ranges):
    for j, (_, _, lst) in enumerate(cols, 1):
        if not lst:
            continue
        dv = DataValidation(type="list", formula1=ranges[lst], allow_blank=True)
        dv.error, dv.errorTitle = "Please choose from the list.", "Not in the list"
        col = get_column_letter(j)
        dv.add(f"{col}{first_row}:{col}{last_row}")
        ws.add_data_validation(dv)


def build(path, reps, built):
    wb = Workbook()
    ins = wb.active
    ins.title = "Instructions"
    ins.column_dimensions["A"].width = 130
    for i, line in enumerate(INSTRUCTIONS.splitlines(), 1):
        c = ins.cell(row=i, column=1, value=line)
        c.alignment = Alignment(wrap_text=True)
        if i == 1 or line in ("Getting the papers", "What to record", "The sheets", "Subgroup codes (Study sheet)"):
            c.font = BOLD
    ins.cell(row=len(INSTRUCTIONS.splitlines()) + 2, column=1, value=f"Form built {built}.").font = GREY

    # hidden vocabularies: label, code per list
    lists = wb.create_sheet("Lists")
    ranges, col = {}, 1
    all_lists = dict(LISTS, report=[(r["study_id"], r["study_id"]) for r in reps])
    for name, items in all_lists.items():
        lists.cell(row=1, column=col, value=name).font = BOLD
        lists.cell(row=1, column=col + 1, value=f"{name}_code").font = GREY
        for i, (lab, code) in enumerate(items, 2):
            lists.cell(row=i, column=col, value=lab)
            lists.cell(row=i, column=col + 1, value=code)
        L = get_column_letter(col)
        ranges[name] = f"=Lists!${L}$2:${L}${len(items) + 1}"
        col += 2
    lists.sheet_state = "hidden"

    rep = wb.create_sheet("Reports")
    rcols = [("Report", "study_id", None), ("First author", "author", None), ("Year", "year", None),
             ("Journal", "journal", None), ("Title", "title", None), ("DOI link", "doi", None),
             ("What to extract", "scope", None), ("Note", "note", None),
             ("Shares patients with other reports", "cohort", None), ("My progress", "progress", "done"),
             ("My notes", "my_notes", None)]
    header(rep, rcols, {"title": 50, "note": 40, "cohort": 40, "journal": 24, "scope": 24, "doi": 34})
    for i, r in enumerate(reps, 3):
        for j, (_, key, _) in enumerate(rcols, 1):
            if key in ("progress", "my_notes"):
                continue
            c = rep.cell(row=i, column=j, value=r[key])
            c.fill = PREFILL_FILL
            if key == "doi" and r["doi"]:
                c.hyperlink, c.style = f"https://doi.org/{r['doi']}", "Hyperlink"
    validate(rep, rcols, 3, len(reps) + 2, ranges)

    st = wb.create_sheet("Study")
    scols = [("Report", "study_id", None)] + STUDY_COLS
    header(st, scols)
    for i, r in enumerate(reps, 3):
        st.cell(row=i, column=1, value=r["study_id"]).fill = PREFILL_FILL
    validate(st, scols, 3, len(reps) + 2, ranges)

    analysed = [r for r in reps if r["scope"] == "All sheets"]

    arms = wb.create_sheet("Arms")
    header(arms, ARM_COLS)
    row = 3
    for r in analysed:
        for _ in range(4):
            arms.cell(row=row, column=1, value=r["study_id"])
            row += 1
    validate(arms, ARM_COLS, 3, row + 200, ranges)

    clin = wb.create_sheet("Clinical outcomes")
    header(clin, CLIN_COLS)
    row = 3
    for r in analysed:
        for lab, _ in LISTS["clinical"]:
            clin.cell(row=row, column=1, value=r["study_id"])
            clin.cell(row=row, column=2, value=lab)
            row += 1
    validate(clin, CLIN_COLS, 3, row + 1500, ranges)

    phys = wb.create_sheet("Physiology")
    header(phys, PHYS_COLS)
    row = 3
    for r in analysed:
        for lab, _ in LISTS["measure"]:
            phys.cell(row=row, column=1, value=r["study_id"])
            phys.cell(row=row, column=2, value=lab)
            row += 1
    validate(phys, PHYS_COLS, 3, row + 3000, ranges)

    eff = wb.create_sheet("Effects")
    header(eff, EFFECT_COLS)
    validate(eff, EFFECT_COLS, 3, 600, ranges)

    wb.move_sheet("Lists", offset=len(wb.sheetnames))
    wb.active = wb.sheetnames.index("Instructions")
    wb.save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("date", nargs="?", default=date.today().isoformat())
    ap.add_argument("--desktop", action="store_true", help="copy the workbooks to the Desktop")
    args = ap.parse_args()
    reps = reports()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in EXTRACTORS:
        p = OUT_DIR / f"Esophageal_balloon_SRMA_extraction_form_{name}_{args.date}.xlsx"
        build(p, reps, args.date)
        print(f"wrote {p.relative_to(ROOT)}")
        if args.desktop:
            for desk in (Path.home() / "OneDrive" / "Desktop", Path.home() / "Desktop"):
                if desk.exists():
                    shutil.copy2(p, desk / p.name)
                    print(f"copied to {desk / p.name}")
                    break
    return 0


if __name__ == "__main__":
    sys.exit(main())
