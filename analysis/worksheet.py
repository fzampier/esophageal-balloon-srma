#!/usr/bin/env python3
"""Build the adjudication workbook the reviewers' output is settled in.

    python3 analysis/worksheet.py                    # -> adjudication.xlsx
    python3 analysis/worksheet.py --out review.xlsx

The workbook is triaged. Two reviewers disagreeing 5,787 times across 33 studies
does not mean 5,787 human rulings: most differences are one reviewer recording a
table row the other missed, or the two of them naming the same measurement
differently. Sending all of it row-by-row buries the ~250 contested readings the
pooled analyses actually depend on.

Every difference diff.py reports therefore gets exactly one disposition:

    sheet 1  the is_longest_reported_mortality flag, one ruling per study
    sheet 2  cohort relations — which reports share patients, which is primary
    panel    conflicts on quantities the analysis pools -> sheet 3, row by row
    R1-R3    everything else -> a proposed resolution rule on sheet 4, ratified
             or rejected as a class, audited through the sample on sheet 5
    sheet 6  the unfiltered reference: every difference and where it went

Matching is imported from diff.py rather than reimplemented. The worksheet and the
disagreement report must never be able to pair endpoints differently — an
adjudicator working from a worksheet that disagrees with the diff has no way to
tell which one is lying.
"""

import argparse
import csv
import json
import math
import random
import re
import sys
from collections import Counter
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diff as D  # noqa: E402  - shared matching, deliberately not duplicated

ROOT = Path(__file__).resolve().parent.parent
GLM, DEEPSEEK = ROOT / "extraction" / "glm", ROOT / "extraction" / "deepseek"
MANIFEST = ROOT / "studies" / "manifest.csv"

HEAD = PatternFill("solid", fgColor="1F3864")
CONFLICT = PatternFill("solid", fgColor="F8CBAD")
ONESIDED = PatternFill("solid", fgColor="FFE699")
AGREE = PatternFill("solid", fgColor="E2EFDA")
HEADFONT = Font(color="FFFFFF", bold=True, size=10)

DECISION = '"GLM,DeepSeek,Both wrong,Not reported,Unclear"'
RATIFY = '"Ratify,Amend,Reject"'
VERDICT = '"Rule holds,Rule fails here — escalate"'


# ---------------------------------------------------------------------- triage

# Fields that name or classify a measurement rather than state what was measured.
# Two models never pick the same words; a difference here is vocabulary, not data.
NAMING_LEAVES = {
    "label_verbatim",
    "name_verbatim",
    "name",
    "label",
    "contrast",
    "contrast_type",
    "type",
    "arm_label",
    "arm_label_a",
    "arm_label_b",
    "test_used",
    "definition_verbatim",
    "role",
    "description",
    "measure_type",
}

# Sections whose conflicting values feed a pooled estimate or decide poolability.
ANALYSIS_SECTIONS = ("outcomes", "arms", "design", "eligibility")


def leaf_of(field):
    return re.sub(r"\[[^\]]*\]", "", field).rsplit(".", 1)[-1]


def section_of(field):
    return field.split(".")[0].split("[")[0]


def triage(field, kind):
    """Assign one difference its disposition.

    `panel`  — both reviewers read a quantity the analysis pools and read it
               differently. The only class that needs a row-by-row human ruling.
    `cohort` — cohort identity and primacy. Settled wholesale by the sheet-2
               rulings, so the field-level rows are not worked individually.
    `R1`     — one reviewer recorded a value, the other nothing. An omission,
               settled by verifying the recorded quote against the paper.
    `R2`     — both recorded the same thing under different names or labels.
               Vocabulary, resolved by mapping; no numeric content changes.
    `R3`     — conflicting readings on fields no pooled estimate reads.
    """
    if section_of(field) == "extraction_flags" or leaf_of(field) == "is_primary_report":
        return "cohort"
    if kind == "one_sided":
        return "R1"
    if leaf_of(field) in NAMING_LEAVES:
        return "R2"
    if ".measurement_context" in field:
        return "R3"  # free text about how something was measured, not a reading
    if section_of(field) in ANALYSIS_SECTIONS or field.startswith("population.n_total"):
        return "panel"
    return "R3"


def material(a, b):
    """True when two numeric readings differ by more than 5%."""
    try:
        x, y = float(a), float(b)
    except (TypeError, ValueError):
        return False
    return abs(x - y) > 0.051 * max(1e-9, abs(x))


AUDIT_FRACTION = 0.05
AUDIT_MIN = 12


def audit_sample(rows):
    """Deterministic stratified sample of the rule-resolved rows.

    5% per rule (at least AUDIT_MIN), seeded on the rule and pool size so the
    same corpus always yields the same sample — a returned workbook's audit can
    be located in a rebuilt one. Panel rows are excluded: they are adjudicated
    row by row already, so auditing them would double-count the work.
    """
    out = []
    for rule in ("R1", "R2", "R3"):
        pool = sorted(
            (r for r in rows if r["disposition"] == rule),
            key=lambda r: (r["study_id"], r["field"]),
        )
        if not pool:
            continue
        k = min(len(pool), max(AUDIT_MIN, math.ceil(AUDIT_FRACTION * len(pool))))
        rng = random.Random(f"audit:{rule}:{len(pool)}")
        out += rng.sample(pool, k)
    return sorted(out, key=lambda r: (r["disposition"], r["study_id"], r["field"]))


RULES = {
    "R1": (
        "One-sided completeness gaps",
        "every `one_sided` difference: one reviewer recorded a value with its "
        "provenance, the other recorded nothing",
        "The recorded value is accepted after its quote is verified against the "
        "paper; a value whose quote cannot be verified escalates to row-by-row "
        "review. Verification may be done by a model pass — record_meta.model on "
        "the reconciled record names whoever adjudicated.",
    ),
    "R2": (
        "Nomenclature and labeling",
        "conflicts on names, labels, contrast/type vocabulary and verbatim "
        "strings (outcome names, arm labels, test names)",
        "Resolved by mapping both reviewers' wording onto one canonical "
        "vocabulary; where both name the same table row, the more complete "
        "verbatim string is kept. This rule never changes numeric content.",
    ),
    "R3": (
        "Conflicts outside the pooled analyses",
        "conflicting readings on fields no pooled estimate reads: baseline "
        "characteristic detail, intervention narrative, feasibility notes",
        "Settled against the two quotes by a model pass recorded in "
        "record_meta.model; any row the model marks uncertain escalates to "
        "row-by-row review. Audited through the sheet-5 sample.",
    ),
}


def rules_rows(rows):
    n = Counter(r["disposition"] for r in rows)
    return [
        {
            "rule": rid,
            "name": name,
            "applies_to": applies,
            "rows_resolved": n[rid],
            "resolution": resolution,
        }
        for rid, (name, applies, resolution) in RULES.items()
    ]


# ------------------------------------------------------------------------- data


def load_manifest():
    with MANIFEST.open() as fh:
        return {r["study_id"]: r for r in csv.DictReader(fh)}


def study_ids():
    return sorted({p.stem for p in GLM.glob("*.json")} & {p.stem for p in DEEPSEEK.glob("*.json")})


def triaged_rows(manifest):
    """Every difference diff.py reports, each carrying its disposition."""
    rows = []
    for sid in study_ids():
        diffs, _, _ = D.compare(sid, GLM, DEEPSEEK)
        for r in diffs:
            rows.append(
                {
                    "study_id": sid,
                    "author": manifest.get(sid, {}).get("first_author", ""),
                    "field": r["field"],
                    "kind": r["kind"],
                    "disposition": triage(r["field"], r["kind"]),
                    "glm": r["glm"],
                    "deepseek": r["deepseek"],
                    "glm_quote": r["glm_quote"],
                    "deepseek_quote": r["deepseek_quote"],
                }
            )
    return rows


def panel_rows(rows):
    section_rank = {"outcomes": 0, "arms": 1, "design": 2, "eligibility": 3}
    out = [
        {**r, "material": "YES" if material(r["glm"], r["deepseek"]) else ""}
        for r in rows
        if r["disposition"] == "panel"
    ]
    return sorted(
        out,
        key=lambda r: (
            section_rank.get(section_of(r["field"]), 9),
            r["material"] != "YES",
            r["study_id"],
            r["field"],
        ),
    )


def outcome_name(o):
    if not isinstance(o, dict):
        return ""
    return o.get("name_verbatim") or o.get("name") or ""


def timepoint_of(o):
    if not isinstance(o, dict):
        return ""
    tp = o.get("timepoint") or {}
    return str(tp.get("label_verbatim") or tp.get("hours") or tp.get("type") or "")


def mortality_rows(manifest):
    """The flag the protocol's primary pooled analysis reads."""
    out = []
    for sid in study_ids():
        g = json.loads((GLM / f"{sid}.json").read_text())
        d = json.loads((DEEPSEEK / f"{sid}.json").read_text())

        def flagged(rec):
            for o in rec.get("outcomes") or []:
                if o.get("is_longest_reported_mortality"):
                    return f"{outcome_name(o)} @ {timepoint_of(o) or '?'}"
            return ""

        fg, fd = flagged(g), flagged(d)
        if not fg and not fd:
            status = "neither — no mortality outcome reported"
        elif fg and fd:
            gt, dt = set(fg.lower().split()), set(fd.lower().split())
            status = "agree" if gt & dt else "DIFFERENT OUTCOME CHOSEN"
        else:
            status = f"FLAG SET BY {'GLM' if fg else 'DEEPSEEK'} ONLY"
        out.append(
            {
                "study_id": sid,
                "author": manifest.get(sid, {}).get("first_author", ""),
                "year": manifest.get(sid, {}).get("year", ""),
                "glm": fg,
                "deepseek": fd,
                "status": status,
            }
        )
    return out


def cohort_rows(manifest):
    out = []
    for sid in study_ids():
        g = json.loads((GLM / f"{sid}.json").read_text())
        d = json.loads((DEEPSEEK / f"{sid}.json").read_text())

        def bits(rec):
            ef = rec.get("extraction_flags") or {}
            cit = rec.get("citation") or {}
            return (
                ef.get("cohort_id") or "",
                ", ".join(ef.get("overlapping_cohort_with") or []),
                "yes" if cit.get("is_primary_report") else "",
            )

        cg, og, pg = bits(g)
        cd, od, pd = bits(d)
        if not any((cg, cd, og, od)):
            continue
        out.append(
            {
                "study_id": sid,
                "author": manifest.get(sid, {}).get("first_author", ""),
                "glm_cohort": cg,
                "glm_overlaps": og,
                "glm_primary": pg,
                "deepseek_cohort": cd,
                "deepseek_overlaps": od,
                "deepseek_primary": pd,
                "status": "agree" if (cg, pg) == (cd, pd) else "DISAGREE",
            }
        )
    return out


# -------------------------------------------------------------------- rendering


def write_sheet(
    wb,
    title,
    columns,
    rows,
    widths,
    decisions=True,
    status_col="status",
    decision_headers=("DECISION", "Adjudicated value", "Adjudicator notes"),
    decision_options=DECISION,
    decision_widths=(14, 22, 34),
):
    ws = wb.create_sheet(title)
    headers = list(columns) + (list(decision_headers) if decisions else [])
    ws.append(headers)
    for cell in ws[1]:
        cell.fill, cell.font = HEAD, HEADFONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for r in rows:
        ws.append([r.get(c, "") for c in columns] + ([""] * len(decision_headers) if decisions else []))

    status_idx = columns.index(status_col) + 1 if status_col in columns else None
    for row in ws.iter_rows(min_row=2):
        if status_idx:
            val = str(row[status_idx - 1].value or "")
            fill = (CONFLICT if "CONFLICT" in val.upper() or "DISAGREE" in val.upper()
                    or "DIFFERENT" in val.upper() or val == "YES"
                    or "ONLY" in val.upper() and "neither" not in val
                    else AGREE if val in ("AGREE", "agree") else ONESIDED if val else None)
            if fill:
                row[status_idx - 1].fill = fill
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    if decisions:
        first = len(columns) + 1
        for i, w in enumerate(decision_widths[: len(decision_headers)]):
            ws.column_dimensions[get_column_letter(first + i)].width = w
        dv = DataValidation(type="list", formula1=decision_options, allow_blank=True)
        ws.add_data_validation(dv)
        col = get_column_letter(first)
        dv.add(f"{col}2:{col}{max(ws.max_row, 2)}")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    return ws


def build_stamp():
    """Identify the run this workbook was built from.

    Copies come back edited, from several people, at different times. A workbook
    that cannot say which extraction it describes cannot be merged back safely.
    """
    import subprocess
    from datetime import date

    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip() or "unknown"
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - a stamp is best-effort, never fatal
        sha, dirty = "unknown", ""
    n = len(study_ids())
    return (
        f"Built {date.today().isoformat()} from commit {sha}"
        f"{' (uncommitted changes present)' if dirty else ''}, "
        f"schema 1.2.0, {n} studies x 2 reviewers."
    )


def write_readme(wb, counts):
    ws = wb.create_sheet("Start here", 0)
    ws.column_dimensions["A"].width = 104
    lines = [
        ("Esophageal balloon SR/MA — adjudication workbook", True),
        ("", False),
        (build_stamp(), False),
        ("NOT YET ADJUDICATED. Nothing here has been signed off, and no record has", False),
        ("been reconciled. Do not use any of it for analysis until it has.", False),
        ("", False),
        ("Two independent LLM reviewers extracted all 33 studies against schema 1.2.0:", False),
        ("GLM 5.3 Flash and DeepSeek V4 Flash (vision). Both saw identical input — the same", False),
        ("rendered page images and the same column-sorted text. Neither has been corrected.", False),
        ("", False),
        (f"They differ in {counts['total']} places. You are NOT being asked to work", False),
        (f"{counts['total']} rows: most differences are one reviewer recording a table row", False),
        ("the other missed, or the same measurement under two names. Each difference has", False),
        ("been triaged once, and the reference sheet shows where every one of them went.", False),
        ("", False),
        ("What is asked of you, in order", True),
        ("", False),
        (f"  1. Mortality flag     33 studies, {counts['mortality_open']} needing a ruling.", False),
        ("     The protocol's primary pooled analysis reads exactly one mortality outcome", False),
        ("     per study. Settle these first; nothing downstream matters if they are wrong.", False),
        ("", False),
        (f"  2. Cohort relations   {counts['cohort']} studies carrying a cohort or overlap claim,", False),
        (f"     {counts['cohort_open']} in dispute. Reports sharing patients must contribute to a pooled", False),
        ("     estimate at most once. Three provisional clusters in COHORTS.md did NOT", False),
        ("     survive extraction — check each against registration number, enrolment", False),
        ("     window and centres, not author lists.", False),
        ("", False),
        (f"  3. Data conflicts     {counts['panel']} contested readings on quantities the analysis", False),
        ("     pools — the only sheet worked row by row. Both reviewers read the field", False),
        ("     and read it differently; the two quotes resolve most rows without the PDF.", False),
        ("     Material = YES marks numeric readings more than 5% apart.", False),
        ("", False),
        (f"  4. Rules to ratify    the remaining {counts['ruled']} differences fall into three", False),
        ("     classes with a proposed resolution rule each. Ratify, amend or reject the", False),
        ("     rules — a ratified rule resolves its whole class; a rejected one sends its", False),
        ("     class back to row-by-row review. This vote, plus the audit, is what lets", False),
        ("     the methods section claim complete-record reconciliation.", False),
        ("", False),
        (f"  5. Audit sample       {counts['audit']} rule-resolved rows, drawn 5% per rule with a", False),
        ("     fixed seed so the sample is reproducible. Check each row's quotes and say", False),
        ("     whether the rule's outcome would have been right. A rule that fails its", False),
        ("     audit is treated as rejected.", False),
        ("", False),
        (f"  6. Reference          all {counts['total']} differences with their dispositions.", False),
        ("     Nothing is worked here; it is the audit trail showing that no difference", False),
        ("     was dropped, and where each one went.", False),
        ("", False),
        ("How to adjudicate", True),
        ("  Read the two quotes first. They resolve most rows without reopening the PDF.", False),
        ("  Put your ruling in DECISION (GLM / DeepSeek / Both wrong / Not reported / Unclear).", False),
        ("  If both reviewers are wrong, type the correct reading in 'Adjudicated value'.", False),
        ("", False),
        ("  Record what the paper reports. No unit conversion, no median-to-mean, no", False),
        ("  imputation. Absent data is blank, never 0 and never 'not reported'.", False),
        ("", False),
        ("Known caveats", True),
        ("  Coverage differs more than accuracy: most differences are one reviewer finding", False),
        ("  something the other missed, not the two of them reading a number differently.", False),
        ("  Where both quote the same table row but assign different timepoints, the numbers", False),
        ("  are usually right and the column mapping is wrong. Check the column header.", False),
    ]
    for text, bold in lines:
        ws.append([text])
        if bold:
            ws.cell(ws.max_row, 1).font = Font(bold=True, size=12 if ws.max_row == 1 else 11)
    ws.sheet_view.showGridLines = False
    return ws


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "adjudication.xlsx"))
    args = ap.parse_args()

    manifest = load_manifest()
    rows = triaged_rows(manifest)
    panel = panel_rows(rows)
    rules = rules_rows(rows)
    audit = audit_sample(rows)
    mort = mortality_rows(manifest)
    coh = cohort_rows(manifest)

    wb = Workbook()
    wb.remove(wb.active)

    n_ruled = sum(1 for r in rows if r["disposition"] in RULES)
    write_readme(
        wb,
        {
            "total": len(rows),
            "panel": len(panel),
            "ruled": n_ruled,
            "audit": len(audit),
            "cohort": len(coh),
            "cohort_open": sum(1 for r in coh if r["status"] != "agree"),
            "mortality_open": sum(1 for r in mort if r["status"] not in
                                  ("agree", "neither — no mortality outcome reported")),
        },
    )

    write_sheet(
        wb, "1. Mortality flag",
        ["study_id", "author", "year", "glm", "deepseek", "status"],
        sorted(mort, key=lambda r: (r["status"] in ("agree", "neither — no mortality outcome reported"),
                                    r["study_id"])),
        [22, 14, 7, 40, 40, 34],
    )
    write_sheet(
        wb, "2. Cohort relations",
        ["study_id", "author", "glm_cohort", "glm_overlaps", "glm_primary",
         "deepseek_cohort", "deepseek_overlaps", "deepseek_primary", "status"],
        sorted(coh, key=lambda r: (r["status"] == "agree", r["study_id"])),
        [22, 14, 18, 26, 11, 18, 26, 13, 12],
    )
    write_sheet(
        wb, "3. Data conflicts",
        ["study_id", "author", "field", "material", "glm", "deepseek",
         "glm_quote", "deepseek_quote"],
        panel,
        [20, 13, 46, 9, 26, 26, 48, 48],
        status_col="material",
    )
    write_sheet(
        wb, "4. Rules to ratify",
        ["rule", "name", "applies_to", "rows_resolved", "resolution"],
        rules,
        [6, 30, 44, 12, 80],
        status_col=None,
        decision_headers=("RATIFY", "Amendment / notes"),
        decision_options=RATIFY,
        decision_widths=(12, 44),
    )
    write_sheet(
        wb, "5. Audit sample",
        ["disposition", "study_id", "field", "kind", "glm", "deepseek",
         "glm_quote", "deepseek_quote"],
        audit,
        [10, 20, 42, 10, 24, 24, 46, 46],
        status_col=None,
        decision_headers=("VERDICT", "Notes"),
        decision_options=VERDICT,
        decision_widths=(26, 34),
    )
    write_sheet(
        wb, "6. Reference — all differences",
        ["study_id", "author", "field", "kind", "disposition", "glm", "deepseek",
         "glm_quote", "deepseek_quote"],
        sorted(rows, key=lambda r: (r["disposition"] != "panel", r["study_id"], r["field"])),
        [20, 13, 46, 11, 12, 28, 28, 48, 48],
        decisions=False,
        status_col="kind",
    )

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    wb.save(args.out)
    n = Counter(r["disposition"] for r in rows)
    print(f"wrote {args.out}")
    print(f"  differences {len(rows):5d}   panel {n['panel']}   "
          f"R1 {n['R1']}   R2 {n['R2']}   R3 {n['R3']}   cohort-field {n['cohort']}")
    print(f"  panel material numeric: {sum(1 for r in panel if r['material'] == 'YES')}")
    print(f"  audit sample: {len(audit)}")
    print(f"  mortality rulings open: "
          f"{sum(1 for r in mort if r['status'] not in ('agree', 'neither — no mortality outcome reported'))}"
          f"   cohort rulings open: {sum(1 for r in coh if r['status'] != 'agree')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
