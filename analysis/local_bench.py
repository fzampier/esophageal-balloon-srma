#!/usr/bin/env python3
"""Score local extractions against the reconciled records (D78 benchmark).

    python analysis/local_bench.py zhao_2025 tang_2026          # both local reviewers
    python analysis/local_bench.py --reviewers qwen27b zhao_2025

The benchmark set is the reports under a Creative Commons licence: they were extracted by
the hosted pair, adjudicated and corrected, so their reconciled records are the reference.
Two tiers per reviewer and study:

1. Key outcomes: the clinical outcome rows the pooled analyses read (mortality, duration of
   ventilation, ventilator-free days, ICU and hospital stay, barotrauma), flattened by
   flatten.py from each side and matched on domain, timepoint and arm role. A row is right
   when every number in it (events, total, value, dispersion, n) equals the reference.
2. Analysis fields: diff.py's identity-paired comparison, restricted to analysis/scope.py.

Only the reconciled record is the reference; a local reading that differs from it is counted
as a difference, not judged here.
"""

import argparse
import csv
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diff  # noqa: E402
import scope  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RECONCILED = ROOT / "extraction" / "reconciled"
DERIVED = ROOT / "analysis" / "derived"

KEY_DOMAINS = (
    "mortality",
    "duration_mechanical_ventilation",
    "ventilator_free_days",
    "icu_length_of_stay",
    "hospital_length_of_stay",
    "barotrauma",
)
ROW_KEY = ("domain", "measure_type", "timepoint_type", "timepoint_days", "timepoint_hours", "arm_role")
NUMBERS = ("events", "total", "value", "dispersion_type", "dispersion", "dispersion_low",
           "dispersion_high", "n")


def num(x):
    """Compare numbers as numbers (12 == 12.0), everything else as text."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return (x or "").strip()


def key_rows(outcomes_csv, study_id):
    rows = defaultdict(list)
    with open(outcomes_csv, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["study_id"] == study_id and r["domain"] in KEY_DOMAINS:
                rows[tuple(r[k] for k in ROW_KEY)].append(tuple(num(r[k]) for k in NUMBERS))
    return rows


def flatten_one(src_dir, study_id, out_dir):
    """flatten.py over a directory holding only this study's local record."""
    one = Path(out_dir) / "src"
    one.mkdir(parents=True, exist_ok=True)
    (one / f"{study_id}.json").write_bytes((src_dir / f"{study_id}.json").read_bytes())
    res = subprocess.run(
        [sys.executable, str(ROOT / "analysis" / "flatten.py"), "--src", str(one), "--out", str(out_dir)],
        capture_output=True, text=True, encoding="utf-8",
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip()[-400:])
    return Path(out_dir) / "outcomes.csv"


def score(reviewer, study_id, show):
    src = ROOT / "extraction" / reviewer
    if not (src / f"{study_id}.json").exists():
        print(f"  {reviewer:9s} {study_id}: no record")
        return None
    ref = key_rows(DERIVED / "outcomes.csv", study_id)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            got = key_rows(flatten_one(src, study_id, tmp), study_id)
        except RuntimeError as exc:
            print(f"  {reviewer:9s} {study_id}: flatten failed: {exc}")
            got = {}
    right = sum(1 for k, v in ref.items() if sorted(got.get(k, []), key=str) == sorted(v, key=str))
    missing = [k for k in ref if k not in got]
    wrong = [k for k in ref if k in got and sorted(got[k], key=str) != sorted(ref[k], key=str)]
    extra = [k for k in got if k not in ref]

    rows, _, _ = diff.compare(study_id, src, RECONCILED)
    in_scope = [r for r in rows if scope.in_scope(r["field"])]
    conflicts = sum(r["kind"] == "conflict" for r in in_scope)
    # diff.py cannot pair array elements whose identity fields are null (an effect estimate
    # with no type), so an element present on both sides comes out as two one-sided rows
    # with the same value: 148 such rows when zhao_2025's reconciled record is compared with
    # itself. Cancel those pairs here rather than change diff.py's pairing.
    a_only = Counter((r["field"], r["glm"]) for r in in_scope
                     if r["kind"] == "one_sided" and r["deepseek"] == "<absent>")
    b_only = Counter((r["field"], r["deepseek"]) for r in in_scope
                     if r["kind"] == "one_sided" and r["glm"] == "<absent>")
    same = a_only & b_only
    one_sided = sum((a_only - same).values()) + sum((b_only - same).values())

    print(f"  {reviewer:9s} {study_id}: key outcome rows {right}/{len(ref)} right "
          f"({len(wrong)} wrong, {len(missing)} missing, {len(extra)} extra); "
          f"analysis fields: {conflicts} conflicting, {one_sided} one-sided")
    if show:
        for k in wrong:
            print(f"      wrong   {k[0]} {k[2]} {k[3] or k[4]} {k[5]}: got {got[k]} ref {ref[k]}")
        for k in missing:
            print(f"      missing {k[0]} {k[2]} {k[3] or k[4]} {k[5]}: ref {ref[k]}")
        for k in extra:
            print(f"      extra   {k[0]} {k[2]} {k[3] or k[4]} {k[5]}: got {got[k]}")
    return right, len(ref)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("study_id", nargs="+")
    ap.add_argument("--reviewers", nargs="+", default=["qwen27b", "gemma26b"])
    ap.add_argument("--quiet", action="store_true", help="totals only, no row detail")
    args = ap.parse_args()
    for rv in args.reviewers:
        tot_right = tot = 0
        for sid in args.study_id:
            res = score(rv, sid, not args.quiet)
            if res:
                tot_right += res[0]
                tot += res[1]
        if tot:
            print(f"{rv}: key outcome rows right {tot_right}/{tot}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
