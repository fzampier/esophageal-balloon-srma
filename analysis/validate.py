#!/usr/bin/env python3
"""Validate extraction records against the schema and the rules it cannot express.

    python3 analysis/validate.py extraction/glm
    python3 analysis/validate.py extraction/glm/talmor_2008.json

Exits non-zero if any record has an error. Warnings do not fail the run.
"""

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "extraction" / "schema" / "extraction-schema.json"

# Fields that hold a value alongside optional provenance.
VALUE_KEYS = ("value", "n", "events", "mean_difference")


def iter_records(target: Path):
    if target.is_file():
        yield target
    else:
        yield from sorted(target.glob("*.json"))


def missing_provenance(node, path=""):
    """Yield paths of objects that carry a non-null value but no provenance."""
    if isinstance(node, dict):
        if "provenance" in node:
            has_value = any(node.get(k) is not None for k in VALUE_KEYS)
            if has_value and not node.get("provenance"):
                yield path or "<root>"
        for key, child in node.items():
            yield from missing_provenance(child, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, child in enumerate(node):
            yield from missing_provenance(child, f"{path}[{i}]")


def undeclared_derivations(node, path=""):
    if isinstance(node, dict):
        if node.get("derived") and not node.get("derivation"):
            yield path or "<root>"
        for key, child in node.items():
            yield from undeclared_derivations(child, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, child in enumerate(node):
            yield from undeclared_derivations(child, f"{path}[{i}]")


def cross_field_checks(rec, path: Path):
    """Rules the schema cannot express. Returns (errors, warnings)."""
    errors, warnings = [], []

    study_id = rec.get("record_meta", {}).get("study_id")
    if study_id and study_id != path.stem:
        errors.append(f"study_id '{study_id}' does not match filename '{path.stem}'")

    arm_labels = {a.get("label") for a in rec.get("arms", [])}
    for i, out in enumerate(rec.get("outcomes", [])):
        for j, d in enumerate(out.get("data", []) or []):
            label = d.get("arm_label")
            if label not in arm_labels:
                errors.append(
                    f"outcomes[{i}].data[{j}].arm_label '{label}' is not declared in arms[]"
                )
        for j, pe in enumerate(out.get("paired_effects", []) or []):
            for key in ("arm_label_a", "arm_label_b"):
                label = pe.get(key)
                if label is not None and label not in arm_labels:
                    errors.append(
                        f"outcomes[{i}].paired_effects[{j}].{key} '{label}' "
                        "is not declared in arms[]"
                    )
            if pe.get("contrast_type") == "pairwise" and not (
                pe.get("arm_label_a") and pe.get("arm_label_b")
            ):
                warnings.append(
                    f"outcomes[{i}].paired_effects[{j}] is pairwise but does not name "
                    "both arms; the contrast cannot be resolved automatically"
                )

    # Eligibility must agree with its own criteria.
    crit = rec.get("eligibility", {}).get("criteria", {})
    any_failed = any(c.get("met") == "no" for c in crit.values() if isinstance(c, dict))
    include = rec.get("eligibility", {}).get("include_final")
    if include and any_failed:
        errors.append("include_final is true but at least one criterion is coded 'no'")
    if include is False and not rec["eligibility"].get("primary_exclusion_reason"):
        errors.append("excluded record has no primary_exclusion_reason")

    # Exactly one mortality outcome carries the longest-follow-up flag.
    mortality = [o for o in rec.get("outcomes", []) if o.get("domain") == "mortality"]
    flagged = [o for o in mortality if o.get("is_longest_reported_mortality")]
    if mortality and len(flagged) == 0:
        errors.append(
            "mortality reported but no outcome sets is_longest_reported_mortality"
        )
    if len(flagged) > 1:
        errors.append(
            f"{len(flagged)} outcomes set is_longest_reported_mortality; exactly one is allowed"
        )

    structure = rec.get("design", {}).get("comparison_structure")
    if structure == "within_patient_crossover":
        if not any(o.get("paired_effects") for o in rec.get("outcomes", [])):
            warnings.append(
                "within_patient_crossover but no outcome carries paired_effects; "
                "the paired contrast is what makes these studies poolable"
            )
    if structure == "single_group_no_comparison":
        for i, out in enumerate(rec.get("outcomes", [])):
            if len(out.get("data", []) or []) > 1:
                warnings.append(
                    f"outcomes[{i}] has multiple data entries in a single-group study"
                )

    # Dichotomous outcomes need a denominator to be poolable.
    for i, out in enumerate(rec.get("outcomes", [])):
        if out.get("measure_type") == "dichotomous":
            for j, d in enumerate(out.get("data", []) or []):
                if d.get("events") is not None and d.get("total") is None:
                    errors.append(f"outcomes[{i}].data[{j}] has events but no total")

    for p in missing_provenance(rec):
        warnings.append(f"value without provenance at {p}")
    for p in undeclared_derivations(rec):
        errors.append(f"derived: true without derivation at {p}")

    flags = rec.get("extraction_flags", {})
    if not flags.get("overlapping_cohort_with") and not flags.get("overlap_evidence"):
        warnings.append(
            "overlapping_cohort_with is empty and overlap_evidence is null; "
            "record what was checked before concluding no overlap"
        )
    if flags.get("overlapping_cohort_with") and not flags.get("cohort_id"):
        errors.append(
            "overlap is declared but cohort_id is null; shared cohorts need a "
            "shared label so the analysis can count them once"
        )

    return errors, warnings


def cohort_consistency(records):
    """Checks that only make sense across a whole directory of records.

    Overlap is a symmetric relation: if A says it shares patients with B, B must
    say so too, and both must carry the same cohort_id. An asymmetric
    declaration means one extractor spotted the overlap and the other did not,
    which is exactly the state that leads to double-counting.
    """
    problems = []
    by_id = {r.get("record_meta", {}).get("study_id"): r for r in records}

    for sid, rec in by_id.items():
        flags = rec.get("extraction_flags", {})
        for other in flags.get("overlapping_cohort_with") or []:
            if other not in by_id:
                problems.append(
                    f"{sid}: declares overlap with '{other}', which is not in this directory"
                )
                continue
            other_flags = by_id[other].get("extraction_flags", {})
            if sid not in (other_flags.get("overlapping_cohort_with") or []):
                problems.append(
                    f"{sid} declares overlap with {other}, but {other} does not declare it back"
                )
            a, b = flags.get("cohort_id"), other_flags.get("cohort_id")
            if a and b and a != b:
                problems.append(
                    f"{sid} and {other} overlap but carry different cohort_id "
                    f"('{a}' vs '{b}')"
                )

    # Within a cohort, exactly one report should be the primary.
    cohorts = {}
    for sid, rec in by_id.items():
        cid = rec.get("extraction_flags", {}).get("cohort_id")
        if cid:
            cohorts.setdefault(cid, []).append(sid)
    for cid, members in cohorts.items():
        if len(members) < 2:
            continue
        primaries = [
            s for s in members if by_id[s].get("citation", {}).get("is_primary_report")
        ]
        if len(primaries) != 1:
            problems.append(
                f"cohort '{cid}' has {len(primaries)} primary report(s) among "
                f"{', '.join(sorted(members))}; exactly one is expected"
            )
    return problems


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    schema = json.loads(SCHEMA_PATH.read_text())
    validator = Draft202012Validator(schema)
    target = Path(sys.argv[1])

    files = list(iter_records(target))
    if not files:
        print(f"No JSON records found under {target}")
        return 1

    total_errors = 0
    parsed = []
    for path in files:
        try:
            rec = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            print(f"\n{path.name}\n  ERROR  malformed JSON: {exc}")
            total_errors += 1
            continue
        parsed.append(rec)

        errors = [
            f"{'.'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
            for e in validator.iter_errors(rec)
        ]
        cross_errors, warnings = cross_field_checks(rec, path)
        errors += cross_errors

        if errors or warnings:
            print(f"\n{path.name}")
            for e in errors:
                print(f"  ERROR  {e}")
            for w in warnings:
                print(f"  warn   {w}")
        else:
            print(f"{path.name}: ok")
        total_errors += len(errors)

    if len(parsed) > 1:
        problems = cohort_consistency(parsed)
        if problems:
            print("\ncohort consistency")
            for p in problems:
                print(f"  ERROR  {p}")
            total_errors += len(problems)

    print(f"\n{len(files)} record(s) checked, {total_errors} error(s)")
    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
