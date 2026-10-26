#!/usr/bin/env python3
"""Regression tests for the extraction pipeline.

    python3 tests/test_pipeline.py

Two synthetic records in tests/fixtures/ stand in for the two comparison
structures: smoke_1999 is a parallel-arm trial, haze_2016 a within-patient
crossover that also declares cohort overlap. Neither is a real study.

The tests assert that valid records pass, that each cross-field rule fires on a
record broken in exactly one way, and that the flattener preserves arm-level and
paired data.
"""

import copy
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
VALIDATE = ROOT / "analysis" / "validate.py"
DIFF = ROOT / "analysis" / "diff.py"
FLATTEN = ROOT / "analysis" / "flatten.py"
WORKSHEET = ROOT / "analysis" / "worksheet.py"

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


def run(*args):
    return subprocess.run(
        [sys.executable, *[str(a) for a in args]], capture_output=True, text=True
    )


def break_record(base, mutate, study_id, workdir, rename=True):
    """Write a copy of `base` broken in exactly one way, as <study_id>.json.

    rename=False leaves record_meta.study_id untouched, which is how the
    filename-mismatch case is constructed.
    """
    rec = copy.deepcopy(base)
    mutate(rec)
    if rename:
        rec["record_meta"]["study_id"] = study_id
    (workdir / f"{study_id}.json").write_text(json.dumps(rec, indent=1))


def main():
    base = json.loads((FIXTURES / "smoke_1999.json").read_text())
    pair = json.loads((FIXTURES / "haze_2016.json").read_text())

    print("valid fixtures")
    r = run(VALIDATE, FIXTURES)
    check("both fixtures validate cleanly", r.returncode == 0, r.stdout + r.stderr)

    print("\ncross-field rules fire")
    breakages = {
        "bad_arm_1999": lambda x: x["outcomes"][0]["data"][1].update(arm_label="typo"),
        "two_flags_1999": lambda x: x["outcomes"][0].update(
            is_longest_reported_mortality=True
        ),
        "no_flag_1999": lambda x: x["outcomes"][1].update(
            is_longest_reported_mortality=False
        ),
        "bad_elig_1999": lambda x: x["eligibility"]["criteria"]["icu_setting"].update(
            met="no"
        ),
        "no_denom_1999": lambda x: x["outcomes"][0]["data"][0].update(total=None),
        "no_deriv_1999": lambda x: x["population"]["age"].update(derived=True),
        "bad_enum_1999": lambda x: x["intervention"]["ptp_computation"].update(
            method="made_up"
        ),
    }

    with tempfile.TemporaryDirectory() as tmp:
        broken = Path(tmp)
        for sid, mutate in breakages.items():
            break_record(base, mutate, sid, broken)
        # Filename mismatch: the record still calls itself smoke_1999.
        break_record(base, lambda x: None, "wrong_name_1999", broken, rename=False)

        for sid in [*breakages, "wrong_name_1999"]:
            r = run(VALIDATE, broken / f"{sid}.json")
            check(f"{sid} rejected", r.returncode != 0, r.stdout)

    print("\nwarnings, not errors")
    with tempfile.TemporaryDirectory() as tmp:
        soft = Path(tmp)
        rec = copy.deepcopy(base)
        rec["record_meta"]["study_id"] = "no_prov_1999"
        rec["population"]["bmi"]["provenance"] = None
        (soft / "no_prov_1999.json").write_text(json.dumps(rec, indent=1))
        r = run(VALIDATE, soft / "no_prov_1999.json")
        check(
            "missing provenance warns but does not fail",
            r.returncode == 0 and "warn" in r.stdout,
            r.stdout,
        )

    print("\nversion discipline")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        stale = copy.deepcopy(base)
        stale["record_meta"]["schema_version"] = "1.0.0"
        (d / "smoke_1999.json").write_text(json.dumps(stale, indent=1))
        r = run(VALIDATE, d / "smoke_1999.json")
        check(
            "record built against an older schema is rejected",
            r.returncode != 0 and "schema_version" in r.stdout,
            r.stdout,
        )

    print("\nmulti-contrast structures")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        rec = copy.deepcopy(pair)
        # A six-condition crossover reporting several contrasts and an omnibus test.
        rec["arms"].append(
            {
                "label": "zeep",
                "label_verbatim": "ZEEP",
                "role": "injury_probe_condition",
                "sequence_position": 3,
                "n_randomised": None,
                "n_analysed": None,
                "peep_applied_cmh2o": None,
            }
        )
        out = rec["outcomes"][0]
        out["paired_effects"].append(
            {
                "contrast_type": "pairwise",
                "contrast": "zeep minus conventional_peep",
                "arm_label_a": "zeep",
                "arm_label_b": "conventional_peep",
                "mean_difference": -8.0,
                "n_pairs": 20,
                "p_value": None,
                "p_value_verbatim": "<0.0001",
                "provenance": {"quote": "Crs fell by 8.0 at ZEEP, p<0.0001", "location": "Table 2"},
            }
        )
        out["paired_effects"].append(
            {
                "contrast_type": "omnibus",
                "contrast": "across all three conditions",
                "p_value": 0.003,
                "test_used": "repeated-measures ANOVA",
                "provenance": {"quote": "RM-ANOVA across conditions, p=0.003", "location": "Results"},
            }
        )
        out["effect_estimates"] = [
            {"type": "mean_difference", "value": 11.0, "adjusted": False,
             "provenance": {"quote": "unadjusted difference 11.0", "location": "Table 3"}},
            {"type": "mean_difference", "value": 9.4, "adjusted": True,
             "adjustment_covariates": "APACHE II",
             "provenance": {"quote": "adjusted difference 9.4", "location": "Table 3"}},
            {"type": None, "p_value": 0.04,
             "provenance": {"quote": "P=0.04 between groups", "location": "Table 3"}},
        ]
        (d / "haze_2016.json").write_text(json.dumps(rec, indent=1))
        r = run(VALIDATE, d / "haze_2016.json")
        check("multi-contrast crossover validates", r.returncode == 0, r.stdout)

        bad = copy.deepcopy(rec)
        bad["outcomes"][0]["paired_effects"][1]["arm_label_a"] = "not_an_arm"
        (d / "haze_2016.json").write_text(json.dumps(bad, indent=1))
        r = run(VALIDATE, d / "haze_2016.json")
        check(
            "paired_effects arm reference is checked",
            r.returncode != 0 and "not_an_arm" in r.stdout,
            r.stdout,
        )

        with tempfile.TemporaryDirectory() as tmp2:
            o = Path(tmp2)
            (d / "haze_2016.json").write_text(json.dumps(rec, indent=1))
            shutil.copy(FIXTURES / "smoke_1999.json", d)
            r = run(FLATTEN, "--src", d, "--out", o)
            import csv as _csv

            eff = list(_csv.DictReader((o / "effects.csv").open()))
            prd = list(_csv.DictReader((o / "paired.csv").open()))
            check("all effect estimates flattened", len(eff) == 5, len(eff))
            check(
                "adjusted and unadjusted both kept",
                {e["effect_adjusted"] for e in eff} >= {"True", "False"},
                {e["effect_adjusted"] for e in eff},
            )
            check("all paired contrasts flattened", len(prd) == 3, len(prd))
            check(
                "omnibus test preserved",
                any(p["contrast_type"] == "omnibus" for p in prd),
                [p["contrast_type"] for p in prd],
            )
            check(
                "thresholded p-value preserved verbatim",
                any(p["p_value_verbatim"] == "<0.0001" for p in prd),
                [p["p_value_verbatim"] for p in prd],
            )

    print("\ncohort consistency across a directory")
    cohort_cases = {
        "asymmetric overlap rejected": lambda a, b: b["extraction_flags"].update(
            overlapping_cohort_with=[]
        ),
        "mismatched cohort_id rejected": lambda a, b: b["extraction_flags"].update(
            cohort_id="different_cohort"
        ),
        "two primaries in one cohort rejected": lambda a, b: b["citation"].update(
            is_primary_report=True
        ),
        "overlap without cohort_id rejected": lambda a, b: (
            a["extraction_flags"].update(cohort_id=None),
            b["extraction_flags"].update(cohort_id=None),
        ),
        "overlap with an absent study rejected": lambda a, b: a[
            "extraction_flags"
        ].update(overlapping_cohort_with=["ghost_2001"]),
    }
    for name, mutate in cohort_cases.items():
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            x, y = copy.deepcopy(base), copy.deepcopy(pair)
            mutate(x, y)
            (d / "smoke_1999.json").write_text(json.dumps(x, indent=1))
            (d / "haze_2016.json").write_text(json.dumps(y, indent=1))
            r = run(VALIDATE, d)
            check(name, r.returncode != 0, r.stdout)

    print("\ndiff")
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "a", Path(tmp) / "b"
        a.mkdir()
        b.mkdir()
        shutil.copy(FIXTURES / "smoke_1999.json", a)
        rec = copy.deepcopy(base)
        rec["record_meta"]["extractor"] = "deepseek"
        rec["record_meta"]["model"] = "deepseek-v4-flash-vision-exp"
        rec["population"]["bmi"]["value"] = 30.5
        rec["population"]["age"]["provenance"]["quote"] = "Mean age 54 (SD 17) years."
        (b / "smoke_1999.json").write_text(json.dumps(rec, indent=1))

        r = run(DIFF, "--dir-a", a, "--dir-b", b)
        check("value disagreement reported", "population.bmi.value" in r.stdout, r.stdout)
        check(
            "record_meta differences ignored",
            "record_meta" not in r.stdout,
            r.stdout,
        )
        check(
            "quote-only difference ignored where values agree",
            "population.age" not in r.stdout,
            r.stdout,
        )
        check(
            "value disagreement classed as a conflict",
            "1 conflict(s)" in r.stdout,
            r.stdout,
        )

    # An omitted key and an explicit null both mean "not reported"; treating them
    # as a disagreement floods the worksheet with differences nobody can act on.
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "a", Path(tmp) / "b"
        a.mkdir()
        b.mkdir()
        x, y = copy.deepcopy(base), copy.deepcopy(base)
        y["record_meta"]["extractor"] = "deepseek"
        x["population"]["predicted_body_weight_kg"] = None
        y["population"].pop("predicted_body_weight_kg", None)
        (a / "smoke_1999.json").write_text(json.dumps(x, indent=1))
        (b / "smoke_1999.json").write_text(json.dumps(y, indent=1))
        r = run(DIFF, "--dir-a", a, "--dir-b", b)
        check(
            "absent and null treated as the same state",
            "0 conflict(s), 0 one-sided" in r.stdout,
            r.stdout,
        )

    # One reviewer filling a multi-field object the other left null is one
    # finding, not one per leaf beneath it.
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "a", Path(tmp) / "b"
        a.mkdir()
        b.mkdir()
        x, y = copy.deepcopy(base), copy.deepcopy(base)
        y["record_meta"]["extractor"] = "deepseek"
        x["population"]["bmi"] = {
            "value": 31.2,
            "value_type": "mean",
            "dispersion": 4.1,
            "dispersion_type": "sd",
            "unit": "kg/m2",
            "derived": False,
            "derivation": None,
            "provenance": {"quote": "BMI 31.2 (4.1)", "location": "Table 1"},
        }
        y["population"]["bmi"] = None
        (a / "smoke_1999.json").write_text(json.dumps(x, indent=1))
        (b / "smoke_1999.json").write_text(json.dumps(y, indent=1))
        r = run(DIFF, "--dir-a", a, "--dir-b", b)
        check(
            "one-sided object collapsed to a single row",
            "0 conflict(s), 1 one-sided" in r.stdout,
            r.stdout,
        )

    # Reviewers do not enumerate outcomes in the same order. Comparing by
    # position compares unrelated measurements; on the first corpus run that
    # artefact produced the large majority of all reported differences.
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "a", Path(tmp) / "b"
        a.mkdir()
        b.mkdir()
        x, y = copy.deepcopy(base), copy.deepcopy(base)
        y["record_meta"]["extractor"] = "deepseek"
        y["outcomes"] = list(reversed(y["outcomes"]))
        (a / "smoke_1999.json").write_text(json.dumps(x, indent=1))
        (b / "smoke_1999.json").write_text(json.dumps(y, indent=1))
        r = run(DIFF, "--dir-a", a, "--dir-b", b)
        check(
            "reordered outcomes pair by identity, not position",
            "0 conflict(s), 0 one-sided" in r.stdout,
            r.stdout,
        )

    # Two reviewers naming the same arm differently is one disagreement, not one
    # per measurement recorded against that arm.
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "a", Path(tmp) / "b"
        a.mkdir()
        b.mkdir()
        x, y = copy.deepcopy(base), copy.deepcopy(base)
        y["record_meta"]["extractor"] = "deepseek"
        old = y["arms"][0]["label"]
        new = "renamed_by_the_other_reviewer"
        y["arms"][0]["label"] = new
        for out in y["outcomes"]:
            for d in out.get("data") or []:
                if d.get("arm_label") == old:
                    d["arm_label"] = new
        (a / "smoke_1999.json").write_text(json.dumps(x, indent=1))
        (b / "smoke_1999.json").write_text(json.dumps(y, indent=1))
        r = run(DIFF, "--dir-a", a, "--dir-b", b)
        check(
            "arm renaming reported once, not once per measurement",
            "1 conflict(s)" in r.stdout and "arms[" in r.stdout,
            r.stdout,
        )

    # Near-miss matching must not reach across categories: pairing a mechanical-power
    # row with a PaCO2 row invents a numeric disagreement that does not exist.
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "a", Path(tmp) / "b"
        a.mkdir()
        b.mkdir()
        x, y = copy.deepcopy(base), copy.deepcopy(base)
        y["record_meta"]["extractor"] = "deepseek"
        x["outcomes"] = [copy.deepcopy(x["outcomes"][0])]
        y["outcomes"] = [copy.deepcopy(x["outcomes"][0])]
        x["outcomes"][0]["domain"] = "oxygenation"
        x["outcomes"][0]["name_verbatim"] = "PaO2"
        y["outcomes"][0]["domain"] = "hemodynamics"
        y["outcomes"][0]["name_verbatim"] = "PaP"
        (a / "smoke_1999.json").write_text(json.dumps(x, indent=1))
        (b / "smoke_1999.json").write_text(json.dumps(y, indent=1))
        r = run(DIFF, "--dir-a", a, "--dir-b", b)
        check(
            "similar names in different domains are not paired",
            "0 conflict(s), 2 one-sided" in r.stdout,
            r.stdout,
        )

    def outcome_at(src, name, timepoint, values):
        """A continuous outcome at one timepoint, with one value per arm."""
        out = copy.deepcopy(src)
        out.update(domain="transpulmonary_pressure", name_verbatim=name,
                   measure_type="continuous", timepoint=timepoint,
                   is_longest_reported_mortality=False, effect_estimates=[])
        for d, v in zip(out["data"], values):
            d.update(events=None, total=None, value=v, value_type="mean", unit="cmH2O")
        return out

    # One outcome entry per timepoint: a reviewer who recorded only the 72-hour
    # row does not contradict the other reviewer's baseline row. On the corpus
    # the timepoint was invisible to pairing, and dong_2023's 72-hour readings
    # were reported as conflicting with the same table's baseline column.
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "a", Path(tmp) / "b"
        a.mkdir()
        b.mkdir()
        x, y = copy.deepcopy(base), copy.deepcopy(base)
        y["record_meta"]["extractor"] = "deepseek"
        src = base["outcomes"][0]
        x["outcomes"] = [
            outcome_at(src, "Pes end-expiratory", {"type": "fixed_day", "hours": 72}, [14.6, 16.6]),
        ]
        y["outcomes"] = [
            outcome_at(src, "Pes end-expiratory", {"type": "baseline"}, [17.5, 16.5]),
            outcome_at(src, "Pes end-expiratory",
                       {"type": "minutes_hours_after_intervention", "days": 3}, [14.6, 16.6]),
        ]
        (a / "smoke_1999.json").write_text(json.dumps(x, indent=1))
        (b / "smoke_1999.json").write_text(json.dumps(y, indent=1))
        r = run(DIFF, "--dir-a", a, "--dir-b", b)
        check(
            "timepoint is part of identity: 72 h pairs with 3 days, not with baseline",
            "1 conflict(s)" in r.stdout and "@72h].timepoint.type" in r.stdout
            and any(ln.endswith("@baseline]") for ln in r.stdout.splitlines())
            and "].data[" not in r.stdout,
            r.stdout,
        )

    # Fuzzy matching is for wording near-misses. When the other reviewer used the
    # exact name elsewhere, a leftover element must not settle for a similar one:
    # soroksky_2015's EITP at 1 h was paired with EETP once EITP had been taken.
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "a", Path(tmp) / "b"
        a.mkdir()
        b.mkdir()
        x, y = copy.deepcopy(base), copy.deepcopy(base)
        y["record_meta"]["extractor"] = "deepseek"
        src = base["outcomes"][0]
        x["outcomes"] = [
            outcome_at(src, "EITP pressure", {"type": "baseline"}, [13.7, 13.7]),
            outcome_at(src, "EITP pressure", {"type": "minutes_hours_after_intervention", "hours": 1},
                       [16.8, 16.8]),
        ]
        y["outcomes"] = [
            outcome_at(src, "EITP pressure", {"type": "crossover_condition"}, [13.7, 13.7]),
            outcome_at(src, "EETP pressure", {"type": "crossover_condition"}, [1.5, -0.3]),
        ]
        (a / "smoke_1999.json").write_text(json.dumps(x, indent=1))
        (b / "smoke_1999.json").write_text(json.dumps(y, indent=1))
        r = run(DIFF, "--dir-a", a, "--dir-b", b)
        check(
            "a name the other reviewer used verbatim is not fuzzy-paired with a lookalike",
            ".name_verbatim" not in r.stdout and "].data[" not in r.stdout
            and any(ln.endswith("@1h]") for ln in r.stdout.splitlines()),
            r.stdout,
        )

    # The same verbatim arm classified two ways is one arm with a role conflict.
    # Splitting it into two one-sided arms unpairs every measurement recorded
    # against it (fumagalli_2019's PEEPINCREMENTAL condition).
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "a", Path(tmp) / "b"
        a.mkdir()
        b.mkdir()
        x, y = copy.deepcopy(base), copy.deepcopy(base)
        y["record_meta"]["extractor"] = "deepseek"
        y["arms"][1]["role"] = "alternative_titration_condition"
        (a / "smoke_1999.json").write_text(json.dumps(x, indent=1))
        (b / "smoke_1999.json").write_text(json.dumps(y, indent=1))
        r = run(DIFF, "--dir-a", a, "--dir-b", b)
        check(
            "identical arm names pair across a role disagreement",
            "1 conflict(s), 0 one-sided" in r.stdout and ".role" in r.stdout,
            r.stdout,
        )

    # grasso_2012: `Conventional ventilation` (n=14, both groups) was paired with
    # `ECMO — Conventional ventilation` (n=7) on name similarity, and every
    # measurement under it was relabelled to the wrong group.
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "a", Path(tmp) / "b"
        a.mkdir()
        b.mkdir()
        x, y = copy.deepcopy(base), copy.deepcopy(base)
        y["record_meta"]["extractor"] = "deepseek"
        x["arms"][1]["label_verbatim"] = "ECMO control group"
        y["arms"][1]["label"] = "all_controls"
        y["arms"][1]["label_verbatim"] = "control group"
        y["arms"][1]["n_analysed"]["value"] = 62
        for out in y["outcomes"]:
            for d in out.get("data") or []:
                if d.get("arm_label") == "ardsnet_control":
                    d["arm_label"] = "all_controls"
        (a / "smoke_1999.json").write_text(json.dumps(x, indent=1))
        (b / "smoke_1999.json").write_text(json.dumps(y, indent=1))
        sys.path.insert(0, str(ROOT / "analysis"))
        import diff as DF

        check(
            "arms of different size are not paired on a similar name",
            DF.align_arm_labels(x, y)[1] == {},
            DF.align_arm_labels(x, y)[1],
        )

    print("\nworksheet triage")
    sys.path.insert(0, str(ROOT / "analysis"))
    import worksheet as W

    # Every difference gets exactly one disposition. The panel sheet is only for
    # contested readings on quantities the analysis pools; everything else is
    # resolved by a ratified rule so co-authors are not sent thousands of rows.
    check(
        "one-sided gaps fall to rule R1",
        W.triage("population.age", "one_sided") == "R1",
        W.triage("population.age", "one_sided"),
    )
    check(
        "naming conflicts fall to rule R2",
        W.triage("outcomes[mortality].name_verbatim", "conflict") == "R2",
        W.triage("outcomes[mortality].name_verbatim", "conflict"),
    )
    check(
        "numeric outcome conflicts go to the panel",
        W.triage("outcomes[mortality].data[pes guided].value", "conflict") == "panel",
        W.triage("outcomes[mortality].data[pes guided].value", "conflict"),
    )
    check(
        "unit conflicts go to the panel",
        W.triage("outcomes[crs].data[control].unit", "conflict") == "panel",
        W.triage("outcomes[crs].data[control].unit", "conflict"),
    )
    check(
        "baseline-detail conflicts fall to rule R3",
        W.triage("population.baseline_by_arm[pes guided].pao2_fio2_baseline.value", "conflict") == "R3",
        W.triage("population.baseline_by_arm[pes guided].pao2_fio2_baseline.value", "conflict"),
    )
    check(
        "free-text measurement context is not contested data",
        W.triage("outcomes[cardiac output].measurement_context.value", "conflict") == "R3",
        W.triage("outcomes[cardiac output].measurement_context.value", "conflict"),
    )
    check(
        "cohort fields are settled on sheet 2, not by a rule",
        W.triage("extraction_flags.cohort_id", "conflict") == "cohort",
        W.triage("extraction_flags.cohort_id", "conflict"),
    )

    fake = [
        {"study_id": f"s{i}", "field": "population.x", "kind": "one_sided",
         "disposition": "R1", "glm": "1", "deepseek": "<absent>",
         "glm_quote": "", "deepseek_quote": ""}
        for i in range(200)
    ] + [
        {"study_id": "s0", "field": "outcomes[x].data[a].value", "kind": "conflict",
         "disposition": "panel", "glm": "1", "deepseek": "2",
         "glm_quote": "", "deepseek_quote": ""}
    ]
    s1, s2 = W.audit_sample(fake), W.audit_sample(fake)
    check("audit sample is deterministic across builds", s1 == s2)
    check(
        "audit sample drawn only from rule-resolved rows",
        s1 and all(r["disposition"] != "panel" for r in s1),
        len(s1),
    )

    print("\nworksheet")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "wb.xlsx"
        r = run(WORKSHEET, "--out", out)
        check("worksheet builds", r.returncode == 0 and out.exists(), r.stdout + r.stderr)
        if out.exists():
            try:
                from openpyxl import load_workbook

                wb = load_workbook(out)
                names = wb.sheetnames
                check(
                    "triaged sheet order: rulings, conflicts, rules, audit, reference",
                    names == ["Start here", "1. Mortality flag", "2. Cohort relations",
                              "3. Data conflicts", "4. Rules to ratify",
                              "5. Audit sample", "6. Reference — all differences"],
                    names,
                )
                # The reference must carry every difference the diff reports, or a
                # curated sheet could drop one and nobody would see it go.
                import subprocess as _sp

                d = _sp.run(
                    [sys.executable, str(DIFF)], capture_output=True, text=True
                ).stdout
                total = int(
                    [ln for ln in d.splitlines() if "studies —" in ln][-1]
                    .split("—")[1].split("conflict")[0].strip()
                ) + int(
                    [ln for ln in d.splitlines() if "studies —" in ln][-1]
                    .split(",")[1].split("one-sided")[0].strip()
                )
                ref = wb["6. Reference — all differences"]
                check(
                    "reference sheet carries every reported difference",
                    ref.max_row - 1 == total,
                    f"sheet={ref.max_row - 1} diff={total}",
                )
                # Sheet 3 must equal the panel dispositions on the reference sheet:
                # a conflict cannot need a ruling and also be resolved by a rule.
                head = [c.value for c in ref[1]]
                di = head.index("disposition")
                n_panel = sum(
                    1 for row in ref.iter_rows(min_row=2) if row[di].value == "panel"
                )
                conflicts = wb["3. Data conflicts"]
                check(
                    "panel sheet matches the panel dispositions exactly",
                    conflicts.max_row - 1 == n_panel,
                    f"sheet={conflicts.max_row - 1} panel={n_panel}",
                )
                check(
                    "panel sheet is a workload, not a dump",
                    0 < conflicts.max_row - 1 < 600,
                    conflicts.max_row - 1,
                )
                rules = wb["4. Rules to ratify"]
                check(
                    "each rule states how many rows it resolves",
                    rules.max_row - 1 >= 3,
                    rules.max_row - 1,
                )
            except ImportError:
                check("openpyxl available", False, "openpyxl not installed")

    print("\nflatten")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        r = run(FLATTEN, "--src", FIXTURES, "--out", out)
        check("flatten succeeds", r.returncode == 0, r.stdout + r.stderr)
        check(
            "shared cohort grouped and ranked primary-first",
            "smoke_cohort" in r.stdout
            and r.stdout.index("smoke_1999               primary")
            < r.stdout.index("haze_2016                secondary"),
            r.stdout,
        )

        import csv

        outcomes = list(csv.DictReader((out / "outcomes.csv").open()))
        paired = list(csv.DictReader((out / "paired.csv").open()))
        studies = list(csv.DictReader((out / "studies.csv").open()))

        check("one row per study x outcome x arm", len(outcomes) == 8, len(outcomes))
        check("paired contrast preserved", len(paired) == 1, len(paired))
        check("both studies present", len(studies) == 2, len(studies))
        check(
            "arm roles resolved from arms[]",
            {o["arm_role"] for o in outcomes}
            == {"intervention", "comparator", "reference_condition"},
            {o["arm_role"] for o in outcomes},
        )
        crs = [o for o in outcomes if o["study_id"] == "haze_2016"]
        check(
            "crossover conditions kept distinct",
            sorted(o["value"] for o in crs) == ["31.0", "42.0"],
            [o["value"] for o in crs],
        )

    # Reconciliation covers the analysis scope only (Option B). That is honest
    # only if the analysis cannot read anything outside it: blank every
    # out-of-scope field and the derived CSVs must not change by a byte.
    print("\nanalysis scope")
    import scope as SC

    def blank_out_of_scope(node, path=""):
        if isinstance(node, dict):
            for k in node:
                child = f"{path}.{k}" if path else k
                if SC.in_scope(child):
                    blank_out_of_scope(node[k], child)
                else:
                    node[k] = None
        elif isinstance(node, list):
            for x in node:
                blank_out_of_scope(x, path)

    with tempfile.TemporaryDirectory() as tmp:
        full, blanked = Path(tmp) / "full", Path(tmp) / "blanked"
        for d in (full, blanked):
            (d / "src").mkdir(parents=True)
        for f in FIXTURES.glob("*.json"):
            rec = json.loads(f.read_text())
            (full / "src" / f.name).write_text(json.dumps(rec))
            blank_out_of_scope(rec)
            (blanked / "src" / f.name).write_text(json.dumps(rec))
        r1 = run(FLATTEN, "--src", full / "src", "--out", full / "out")
        r2 = run(FLATTEN, "--src", blanked / "src", "--out", blanked / "out")
        tables = sorted(p.name for p in (full / "out").glob("*.csv"))
        same = [
            (full / "out" / t).read_bytes() == (blanked / "out" / t).read_bytes()
            for t in tables
        ]
        check(
            "flatten reads nothing outside analysis/scope.py",
            r1.returncode == 0 and r2.returncode == 0 and len(tables) == 5 and all(same),
            dict(zip(tables, same)) or r2.stdout + r2.stderr,
        )
        check("scope is not vacuous", not SC.in_scope("population.additional_baseline[x].count"))
        check("per-arm baseline is in scope, its annotation is not (B8)",
              SC.in_scope("population.baseline_by_arm[pes guided].age.value")
              and SC.in_scope("population.baseline_by_arm[pes guided]")
              and not SC.in_scope("population.baseline_by_arm[pes guided].age.unit"))

    print("\nreconcile")
    RECONCILE = ROOT / "analysis" / "reconcile.py"
    import rulings as RU

    def reconcile_case(mutate_b, rulings_rows, cohorts_rows=(), edits_rows=()):
        """Run reconcile.py on smoke_1999 against a DeepSeek copy altered by mutate_b."""
        tmp = Path(tempfile.mkdtemp())
        dirs = {k: tmp / k for k in ("glm", "deepseek", "ledger", "out")}
        for d in dirs.values():
            d.mkdir()
        if edits_rows:
            import edits as ED
            RU.write_csv(dirs["ledger"] / "edits.csv", ED.COLUMNS,
                         [{c: row.get(c, "") for c in ED.COLUMNS} for row in edits_rows])
        x, y = copy.deepcopy(base), copy.deepcopy(base)
        y["record_meta"].update(extractor="deepseek", model="deepseek-v4-flash-vision-exp")
        mutate_b(y)
        (dirs["glm"] / "smoke_1999.json").write_text(json.dumps(x, indent=1))
        (dirs["deepseek"] / "smoke_1999.json").write_text(json.dumps(y, indent=1))
        RU.write_csv(dirs["ledger"] / "rulings.csv", RU.RULING_COLUMNS, [
            {c: row.get(c, "") for c in RU.RULING_COLUMNS} for row in rulings_rows
        ])
        if cohorts_rows:
            cols = ["study_id", "status", "cohort_id", "overlapping_cohort_with",
                    "is_primary_report", "basis", "adjudicator", "note"]
            RU.write_csv(dirs["ledger"] / "cohorts.csv", cols,
                         [{c: row.get(c, "") for c in cols} for row in cohorts_rows])
        r = run(RECONCILE, "--glm", dirs["glm"], "--deepseek", dirs["deepseek"],
                "--ledger", dirs["ledger"], "--out", dirs["out"], "--open")
        path = dirs["out"] / "smoke_1999.json"
        rec = json.loads(path.read_text()) if path.exists() else None
        shutil.rmtree(tmp, ignore_errors=True)
        return r, rec

    def bmi_quote(y):
        y["population"]["bmi"]["value"] = 30.5
        y["population"]["bmi"]["provenance"] = {"quote": "BMI 30.5 (8)", "location": "Table 1"}

    r, rec = reconcile_case(bmi_quote, [])
    check("an open in-scope difference blocks the record", rec is None and "open" in r.stdout,
          r.stdout + r.stderr)

    ruling = {"ruling_id": "t1", "study_id": "smoke_1999", "field": "population.bmi.value",
              "kind": "conflict", "glm": "31.0", "deepseek": "30.5", "decision": "deepseek",
              "adjudicator": "A. Tester", "adjudicator_type": "human"}
    r, rec = reconcile_case(bmi_quote, [ruling])
    check("a ruled difference is written", rec is not None, r.stdout + r.stderr)
    if rec:
        check("the ruled reading is taken", rec["population"]["bmi"]["value"] == 30.5,
              rec["population"]["bmi"])
        check("its quote travels with it",
              rec["population"]["bmi"]["provenance"]["quote"] == "BMI 30.5 (8)",
              rec["population"]["bmi"]["provenance"])
        check("the adjudicator is named, and the record says it is reconciled",
              rec["record_meta"]["model"] == "A. Tester (human)"
              and rec["record_meta"]["extractor"] == "reconciled",
              rec["record_meta"])

    def out_of_scope(y):
        y["comparator"]["comparator_verbatim"]["value"] = "a different sentence"

    r, rec = reconcile_case(out_of_scope, [])
    check("an out-of-scope difference does not block, and keeps GLM's reading",
          rec is not None
          and rec["comparator"]["comparator_verbatim"]["value"] == base["comparator"]["comparator_verbatim"]["value"]
          and "not adjudicated" in rec["record_meta"]["adjudication_notes"],
          r.stdout + r.stderr)
    if rec:
        check("a record settled by no one does not name an adjudicator",
              "rules only" in rec["record_meta"]["model"], rec["record_meta"]["model"])

    def extra_country(y):
        y["design"]["countries"] = ["United States", "Canada"]

    absent = {"ruling_id": "t2", "study_id": "smoke_1999", "field": "design.countries[1]",
              "kind": "one_sided", "glm": "<absent>", "deepseek": "Canada",
              "decision": "not_reported", "adjudicator": "A. Tester", "adjudicator_type": "human"}
    r, rec = reconcile_case(extra_country, [absent])
    check("a list element ruled not reported is dropped, not nulled",
          rec is not None and rec["design"]["countries"] == ["United States"],
          (rec or {}).get("design", r.stdout + r.stderr))
    accept = dict(absent, ruling_id="t3", decision="deepseek")
    r, rec = reconcile_case(extra_country, [accept])
    check("a one-sided reading ruled correct is kept",
          rec is not None and rec["design"]["countries"] == ["United States", "Canada"],
          (rec or {}).get("design", r.stdout + r.stderr))

    r, rec = reconcile_case(lambda y: None, [], [{"study_id": "smoke_1999", "status": "pending"}])
    check("a study whose cohort is pending is blocked even when the reviewers agree",
          rec is None and "cohort pending" in r.stdout, r.stdout + r.stderr)

    model = dict(ruling, ruling_id="t4", decision="glm", adjudicator="some-model",
                 adjudicator_type="model")
    r, rec = reconcile_case(bmi_quote, [ruling, model])
    # One measurement, two representations: GLM one outcome holding both arms,
    # DeepSeek one outcome per arm (boesing_2024b). A ruling on whose
    # representation stands must settle every row beneath it and leave one copy
    # of each reading, not both.
    def split_per_arm(y):
        mort = y["outcomes"][1]
        one, two = copy.deepcopy(mort), copy.deepcopy(mort)
        one["data"], two["data"] = [mort["data"][0]], [mort["data"][1]]
        two["effect_estimates"] = []
        y["outcomes"][1:2] = [one, two]

    probe = reconcile_case(split_per_arm, [])[0].stdout
    label = next((ln[ln.index("outcomes["):].split("]")[0] + "]" for ln in probe.splitlines()
                  if "180 day mortality" in ln), "")
    element = {"ruling_id": "t5", "study_id": "smoke_1999", "field": label, "kind": "element",
               "decision": "glm", "adjudicator": "some-model", "adjudicator_type": "model"}
    r2, rec2 = reconcile_case(split_per_arm, [element])
    check("an element ruling settles every difference beneath it",
          rec2 is not None, probe + r2.stdout + r2.stderr)
    if rec2:
        m180 = [o for o in rec2["outcomes"] if o["name_verbatim"] == "180-day mortality"]
        check("and leaves one copy of each reading",
              len(m180) == 1 and len(m180[0]["data"]) == 2,
              [(len(o["data"]), len(o["effect_estimates"])) for o in m180])
        check("and names the model as a model",
              "some-model (model)" in rec2["record_meta"]["model"], rec2["record_meta"]["model"])

    def split_and_misread(y):
        split_per_arm(y)
        y["outcomes"][1]["data"][0]["events"] = 13

    r2, rec2 = reconcile_case(split_and_misread, [element])
    check("an element ruling never settles a contested reading (panel conflict)",
          rec2 is None and ".events" in r2.stdout, r2.stdout + r2.stderr)

    # An adjudicator who returns one difference unclear inside an element they
    # otherwise settled must leave that difference open, not have the element
    # ruling settle it by default.
    one_diff = next((ln[ln.index("outcomes["):].strip() for ln in probe.splitlines()
                     if "180 day mortality" in ln and "].data[" in ln), "")
    unclear_inside = {"ruling_id": "t6", "study_id": "smoke_1999", "field": one_diff,
                      "kind": "one_sided", "glm": "", "deepseek": "<absent>", "decision": "unclear",
                      "adjudicator": "some-model", "adjudicator_type": "model"}
    import reconcile as RCN
    import diff as DF

    x, y = copy.deepcopy(base), copy.deepcopy(base)
    split_per_arm(y)
    yb, _ = DF.align_arm_labels(x, y)
    xm = copy.deepcopy(x)
    xm.pop("record_meta")
    yb.pop("record_meta", None)
    rows = [(p, k, va, vb) for p, k, va, vb, _, _ in DF.walk(xm, yb) if p == one_diff]
    if rows:
        p, k, va, vb = rows[0]
        unclear_inside.update(glm=DF._disp(va), deepseek=DF._disp(vb))
    later = {**element, "ruling_id": "e-later", "decision": "glm", "adjudicator": "PI",
             "adjudicator_type": "human", "source": "row 2", "recorded": "2026-09-16"}
    r3, rec3 = reconcile_case(split_per_arm, [element, later])
    check("a later element ruling on the same element supersedes the earlier one",
          rec3 is not None and rec3["outcomes"][0]["data"] == base["outcomes"][0]["data"]
          and (rec2 is None or rec3["outcomes"][0]["data"] != rec2["outcomes"][0]["data"]),
          r3.stdout + r3.stderr)

    r2, rec2 = reconcile_case(split_per_arm, [element, unclear_inside])
    check("an unclear ruling inside an element keeps that difference open",
          bool(rows) and rec2 is None and "model returned unclear" in r2.stdout,
          r2.stdout + r2.stderr)

    drec = copy.deepcopy(base)
    twin = copy.deepcopy(drec["outcomes"][0])
    twin["name_verbatim"] = "Pneumothorax"
    twin["domain"] = drec["outcomes"][0]["domain"]
    drec["outcomes"].append(twin)
    check("rows that merely share numbers are not duplicates",
          RCN.duplicate_measurements(drec) == [], RCN.duplicate_measurements(drec))
    drec["outcomes"].append(copy.deepcopy(drec["outcomes"][0]))
    check("the same measurement recorded twice is a duplicate",
          len(RCN.duplicate_measurements(drec)) == 2, RCN.duplicate_measurements(drec))

    check("a model ruling never overrides a human ruling on the same difference",
          rec is not None and rec["population"]["bmi"]["value"] == 30.5,
          (rec or {}).get("population", r.stdout))

    print("\nedits")
    first = base["outcomes"][0]
    import diff as DF
    elabel = DF._short(DF.element_key(first), 0)  # the label diff.py reports, e.g. 'mortality 28 day mortality dichotomous @28d'
    who = {"adjudicator": "Test Person", "adjudicator_type": "human", "source": "row 1", "recorded": "2026-09-15"}
    edits = [
        {"edit_id": "e1", "study_id": "smoke_1999", "op": "set", "value": "17",
         "path": f"outcomes[{elabel}].data[pes guided].events", **who},
        {"edit_id": "e2", "study_id": "smoke_1999", "op": "add", "value": json.dumps(
            {"type": "risk_ratio", "value": 0.5, "ci_low": 0.2, "ci_high": 0.9}),
         "path": f"outcomes[{elabel}].effect_estimates", **who},
        {"edit_id": "e3", "study_id": "smoke_1999", "op": "drop",
         "path": f"outcomes[{elabel}].data[ardsnet control].derived", **who},
    ]
    r, rec = reconcile_case(lambda y: None, [], edits_rows=edits)
    d = {x["arm_label"]: x for x in (rec or {"outcomes": [{"data": []}]})["outcomes"][0]["data"]}
    check("set, add and drop edits are applied to the merged record by label",
          rec is not None and d.get("pes_guided", {}).get("events") == 17
          and rec["outcomes"][0]["effect_estimates"][-1]["type"] == "risk_ratio"
          and "derived" not in d.get("ardsnet_control", {"derived": 1}),
          r.stdout + r.stderr)
    check("the record's notes count the edits and name who made them",
          rec is not None and "3 post-merge edit(s) by Test Person" in rec["record_meta"]["adjudication_notes"],
          (rec or {}).get("record_meta"))
    stale = [{**edits[0], "path": "outcomes[no such outcome @9d].data[pes guided].events"}]
    r, rec = reconcile_case(lambda y: None, [], edits_rows=stale)
    check("an edit whose path no longer resolves blocks the study",
          rec is None and "edit failed" in r.stdout and "EDIT ERROR" in r.stdout, r.stdout + r.stderr)
    ambiguous = [{**edits[0], "path": "outcomes[0].data[pes guided].events"}]
    r, rec = reconcile_case(lambda y: None, [], edits_rows=ambiguous)
    check("a bare index still resolves when no label matches",
          rec is not None and {x["arm_label"]: x for x in rec["outcomes"][0]["data"]}["pes_guided"]["events"] == 17,
          r.stdout + r.stderr)

    # --- risk of bias: RoB 2 algorithms, walked by hand against the developers' VBA ---
    sys.path.insert(0, str(ROOT / "analysis"))
    import rob
    cases = [
        (rob.d1, {"1.1": "Y", "1.2": "Y", "1.3": "N"}, "Low"),
        (rob.d1, {"1.1": "N", "1.2": "PY", "1.3": "N"}, "Some concerns"),
        (rob.d1, {"1.1": "Y", "1.2": "NI", "1.3": "PY"}, "High"),
        (rob.d1, {"1.1": "Y", "1.2": "PN", "1.3": "N"}, "High"),
        (rob.d2, {"2.1": "N", "2.2": "PN", "2.6": "Y"}, "Low"),
        (rob.d2, {"2.1": "Y", "2.2": "Y", "2.3": "Y", "2.4": "Y", "2.5": "N", "2.6": "Y"}, "High"),
        (rob.d2, {"2.1": "Y", "2.2": "Y", "2.3": "NI", "2.6": "PN", "2.7": "N"}, "Some concerns"),
        (rob.d3, {"3.1": "N", "3.2": "N", "3.3": "Y", "3.4": "Y"}, "High"),
        (rob.d3, {"3.1": "N", "3.2": "PN", "3.3": "PN"}, "Low"),
        (rob.d4, {"4.1": "N", "4.2": "N", "4.3": "Y", "4.4": "Y", "4.5": "PN"}, "Some concerns"),
        (rob.d4, {"4.1": "N", "4.2": "NI", "4.3": "N"}, "Some concerns"),
        (rob.d4, {"4.1": "NI", "4.2": "PY"}, "High"),
        (rob.d4, {"4.1": "N", "4.2": "N", "4.3": "NA"}, None),
        (rob.d5, {"5.1": "NI", "5.2": "PN", "5.3": "N"}, "Some concerns"),
        (rob.d5, {"5.1": "Y", "5.2": "PN", "5.3": "N"}, "Low"),
        (rob.d5, {"5.1": "Y", "5.2": "PY", "5.3": "N"}, "High"),
        (rob.ds, {"S.1": "N", "S.2": "Y", "S.3": "Y"}, "Low"),
        (rob.ds, {"S.1": "Y", "S.3": "PN"}, "High"),
    ]
    bad = [(f.__name__, a, want, f(a)) for f, a, want in cases if f(a) != want]
    check("RoB 2 domain algorithms match the published flowcharts", not bad, bad)
    check("crossover domain 5 counts 5.4",
          rob.d5({"5.1": "Y", "5.2": "N", "5.3": "N", "5.4": "PY"}, crossover=True) == "High")

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
