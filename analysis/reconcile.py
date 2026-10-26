#!/usr/bin/env python3
"""Merge the two reviewers' records into extraction/reconciled/, by the ledger.

    python3 analysis/reconcile.py                   # status of all 33; write what is complete
    python3 analysis/reconcile.py --study dong_2023 --open   # list what blocks one study
    python3 analysis/reconcile.py --export-open open.csv     # every open in-scope difference
    python3 analysis/reconcile.py --provisional DIR          # write every merge, complete or not

Scope is Option B, chosen by the co-authors on 2026-09-15: every difference on a
field the analysis reads (analysis/scope.py) must be settled before a record is
written; differences elsewhere are not adjudicated. The merge walks the two
records exactly as diff.py does — same element pairing, same arm-label
alignment, same paths — so every difference it meets is a row diff.py reports,
and each is settled, in this order, by:

  1. a human ruling in extraction/adjudication/rulings.csv on that exact
     difference (`unclear` settles nothing);
  2. a study ruling: the mortality flag follows the reviewer chosen on sheet 1,
     and cohort fields follow extraction/adjudication/cohorts.csv;
  3. a model ruling on that exact difference, then an element ruling — whose
     representation of an outcome or arm stands — covering it. An element
     ruling never settles a panel conflict; those stay with the co-authors;
  4. two mechanical rules that change no content — readings identical but for
     whitespace or case, and `arms[].label`, which is an internal identifier
     (the other reviewer's references were already translated to it);
  5. outside the analysis scope, GLM's reading, unadjudicated and declared so in
     record_meta.adjudication_notes — reviewer 1 is the base, nothing more.

After the merge, the study's post-merge edits in extraction/adjudication/edits.csv
are applied in order (analysis/edits.py): corrections a human wrote that no
choice between the two readings could express — a number fixed inside an entry
only one reviewer has, a contrast both missed, an entry both recorded that the
paper does not support. An edit whose path no longer resolves blocks the study.

Anything in scope that none of these settles is open, and a record with an open
difference is not written. Neither is one that fails validate.py. Whoever
settled a record's differences is named in record_meta.model: the record must
never imply a human pass that did not happen.
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diff as D  # noqa: E402  - the merge must pair elements exactly as the diff does
import edits as E  # noqa: E402  - post-merge corrections written by a human
import rulings as R  # noqa: E402
import validate as V  # noqa: E402
import worksheet as W  # noqa: E402  - triage decides which conflicts stay with humans
from scope import in_scope, normalize  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GLM, DEEPSEEK = ROOT / "extraction" / "glm", ROOT / "extraction" / "deepseek"
OUT = ROOT / "extraction" / "reconciled"
COHORTS = R.LEDGER / "cohorts.csv"
SCHEMA_VERSION = "1.2.0"

MISSING = D.MISSING
COHORT_FIELDS = {
    "extraction_flags.cohort_id",
    "extraction_flags.overlapping_cohort_with",
    "citation.is_primary_report",
}
ABSENT_WORDS = {"n/a", "na", "none", "not reported", "not applicable", "-"}


def coerce(text, like):
    """Read a typed adjudicated value as the type the reviewers recorded."""
    t = text.strip()
    if t.lower() in ABSENT_WORDS:
        return MISSING
    samples = [x for x in like if x is not MISSING and x is not None]
    if any(isinstance(x, bool) for x in samples):
        return t.lower() in ("true", "yes", "1")
    if any(isinstance(x, (int, float)) for x in samples):
        try:
            f = float(t)
            return int(f) if f == int(f) and not any(isinstance(x, float) for x in samples) else f
        except ValueError:
            return t
    return t


class Resolver:
    """Settles one study's differences and remembers how each was settled."""

    def __init__(self, study_id, field_rulings, study_rulings, cohorts):
        self.sid = study_id
        self.log = []
        ours = [r for r in field_rulings if r["study_id"] == study_id]
        # A study's model rulings come from its latest verdict only: re-running
        # the pass on a study replaces the earlier verdict rather than mixing
        # with it. Older lines stay in the ledger as history.
        model_sources = [r["source"] for r in ours if r["adjudicator_type"] == "model"]
        latest = model_sources[-1] if model_sources else None
        ours = [r for r in ours if r["adjudicator_type"] != "model" or r["source"] == latest]
        mine = [r for r in ours if r["decision"] != "unclear"]
        self.human, self.model = {}, {}
        for r in mine:
            if r["kind"] == "element":
                continue
            book = self.human if r["adjudicator_type"] == "human" else self.model
            book[R.row_key(r["study_id"], r["field"], r["kind"], r["glm"], r["deepseek"])] = r
        # An element ruling says whose representation of one outcome or arm
        # stands, and settles every difference beneath it. Longest target first,
        # so a ruling on a nested element beats one on its parent; humans first.
        # Among rulings on the same element, the later line wins, as for leaf
        # rulings: a correction is a new line, never an edit of an old one.
        self.elements = [r for _, r in sorted(
            ((i, r) for i, r in enumerate(mine) if r["kind"] == "element"),
            key=lambda ir: (ir[1]["adjudicator_type"] != "human", -len(ir[1]["field"]), -ir[0]),
        )]
        unclear = [r for r in ours if r["decision"] == "unclear" and r["kind"] != "element"]
        self.unclear_human = {R.row_key(r["study_id"], r["field"], r["kind"], r["glm"], r["deepseek"])
                              for r in unclear if r["adjudicator_type"] == "human"}
        self.unclear_model = {R.row_key(r["study_id"], r["field"], r["kind"], r["glm"], r["deepseek"])
                              for r in unclear if r["adjudicator_type"] == "model"}
        self.mortality = next(
            (r for r in reversed(study_rulings)
             if r["study_id"] == study_id and r["topic"] == "mortality_flag"
             and r["decision"] in ("glm", "deepseek")),
            None,
        )
        self.cohort = cohorts.get(study_id) if settled(cohorts.get(study_id)) else None

    def record(self, path, kind, action, basis, adjudicator="", adjudicator_type="human"):
        self.log.append({
            "field": path, "kind": kind, "action": action, "basis": basis,
            "adjudicator": adjudicator, "adjudicator_type": adjudicator_type,
            "in_scope": in_scope(path),
        })

    def apply(self, r, path, kind, a, b):
        if r["decision"] == "value" and not r["value"].strip():
            # 'Both wrong' with nothing written is a reservation, not a reading:
            # it settles nothing, and it must not blank the field.
            self.record(path, kind, "open", f"value ruling {r['ruling_id']} gives no value",
                        r["adjudicator"], r["adjudicator_type"])
            return "open", None
        action = {"glm": "a", "deepseek": "b", "value": "value",
                  "not_reported": "absent"}[r["decision"]]
        value = coerce(r["value"], (a, b)) if action == "value" else None
        if action == "value" and value is MISSING:
            action = "absent"
        what = "element ruling" if r["kind"] == "element" else "ruling"
        self.record(path, kind, action, f"{what} {r['ruling_id']}", r["adjudicator"],
                    r["adjudicator_type"])
        return action, value

    def element_ruling(self, path, kind):
        # Panel conflicts are contested readings the co-authors rule on row by
        # row; choosing a representation wholesale must not decide them.
        if kind == "conflict" and W.triage(path, kind) == "panel":
            return None
        return next(
            (r for r in self.elements
             if path == r["field"] or path.startswith((r["field"] + ".", r["field"] + "["))),
            None,
        )

    def __call__(self, path, kind, a, b):
        """Return (action, value): action is a | b | value | absent | open.

        Precedence: a human ruling on this difference; the study's human
        mortality and cohort rulings; a model ruling on this difference; an
        element ruling covering it; content-neutral rules; out of scope.
        """
        key = R.row_key(self.sid, path, kind, D._disp(a), D._disp(b))
        if key in self.human:
            return self.apply(self.human[key], path, kind, a, b)

        n = normalize(path)
        if n == "outcomes.is_longest_reported_mortality" and self.mortality:
            action = "a" if self.mortality["decision"] == "glm" else "b"
            self.record(path, kind, action, f"study ruling {self.mortality['ruling_id']}",
                        self.mortality["adjudicator"])
            return action, None

        if n in COHORT_FIELDS and self.cohort:
            self.record(path, kind, "cohort", f"cohorts.csv ({self.cohort['basis']})",
                        self.cohort["adjudicator"])
            return "cohort", None  # overwritten wholesale after the merge

        if key in self.model:
            return self.apply(self.model[key], path, kind, a, b)
        # Someone looked at exactly this difference and could not settle it. An
        # element ruling above it must not settle it for them by default.
        if in_scope(path) and (key in self.unclear_human or key in self.unclear_model):
            who = "returned Unclear" if key in self.unclear_human else "model returned unclear"
            self.record(path, kind, "open", who)
            return "open", None
        er = self.element_ruling(path, kind)
        if er:
            return self.apply(er, path, kind, a, b)

        if isinstance(a, str) and isinstance(b, str) and \
                re.sub(r"\s+", "", a).lower() == re.sub(r"\s+", "", b).lower():
            self.record(path, kind, "a", "rule: formatting-only difference", "rule")
            return "a", None

        if n == "arms.label" and kind == "conflict":
            self.record(path, kind, "a", "rule: arm label is an identifier", "rule")
            return "a", None

        if not in_scope(path):
            self.record(path, kind, "a", "out of scope: GLM reading, not adjudicated", "")
            return "a", None

        self.record(path, kind, "open", "no ruling")
        return "open", None


def merge(a, b, path, resolve, in_list=False):
    """Build the reconciled node for one path. Mirrors diff.walk exactly.

    A reading ruled absent is null in an object but is dropped from a list: the
    schema admits `null` for a field, never as an element of `countries`.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        keys = list(a) + [k for k in b if k not in a]
        out, took_b = {}, False
        deferred = []
        for k in keys:
            child = f"{path}.{k}" if path else k
            if k == "provenance" or k.endswith("_provenance") or D.is_narrative(child):
                deferred.append(k)
                continue
            v, side = merge(a.get(k, MISSING), b.get(k, MISSING), child, resolve)
            if v is not MISSING:
                out[k] = v
            if side == "b" and not isinstance(v, (dict, list)):
                took_b = True
        # Provenance and free-text annotation belong to whichever reading the
        # object's own values came from; a DeepSeek number keeps DeepSeek's quote.
        src, other = (b, a) if took_b else (a, b)
        for k in deferred:
            v = src.get(k, MISSING)
            if v is MISSING:
                v = other.get(k, MISSING)
            if v is not MISSING:
                out[k] = v
        return {k: out[k] for k in keys if k in out}, "b" if took_b else "a"

    if isinstance(a, list) and isinstance(b, list):
        out = []
        for label, ea, eb in D.match_elements(a, b):
            v, _ = merge(ea, eb, f"{path}[{label}]", resolve, in_list=True)
            if v is not MISSING:
                out.append(v)
        return out, "a"

    a_blank, b_blank = D.is_blank(a), D.is_blank(b)
    if (a_blank and b_blank) or (not a_blank and not b_blank and a == b):
        return (a if a is not MISSING else b), "a"
    kind = "one_sided" if a_blank != b_blank else "conflict"
    action, value = resolve(path, kind, a, b)
    if action == "b":
        return b, "b"
    if action == "value":
        return value, "b" if (b == value) else "a"
    if action == "absent":
        return (MISSING if in_list else None), "a"
    return a, "a"  # a, cohort (overwritten later), open (provisional only)


def settled_by(entry):
    """human | model | rule | unadjudicated | open, for one resolution-log entry."""
    if entry["action"] == "open":
        return "open"
    if entry["basis"].startswith("rule"):
        return "rule"
    if entry["basis"].startswith("out of scope"):
        return "unadjudicated"
    return "model" if entry["adjudicator_type"] == "model" else "human"


def load_cohorts(ledger=R.LEDGER):
    """{study_id: row} for every study cohorts.csv names, settled or pending.

    A pending study is blocked outright, even where its two reviewers agree: a
    report whose overlap is unsettled could be pooled beside the report it
    duplicates, and agreement between two reviewers who both missed an overlap
    proves nothing.
    """
    return {r["study_id"]: r for r in R.read_csv(ledger / COHORTS.name)}


def settled(row):
    return bool(row) and row.get("status") == "settled"


def apply_cohort(rec, row):
    flags = rec.setdefault("extraction_flags", {})
    flags["cohort_id"] = row["cohort_id"] or None
    flags["overlapping_cohort_with"] = [s for s in row["overlapping_cohort_with"].split(";") if s.strip()]
    rec.setdefault("citation", {})["is_primary_report"] = row["is_primary_report"].lower() == "true"


def reconcile_study(sid, field_rulings, study_rulings, cohorts, glm=GLM, deepseek=DEEPSEEK,
                    edits=(), apply=True):
    """Merge one study. `edits` are its post-merge edits (edits.csv); apply=False
    returns the merge before them, which is what edits.py checks paths against.
    Raises edits.EditError when an edit no longer applies."""
    a = json.loads((glm / f"{sid}.json").read_text())
    b_raw = json.loads((deepseek / f"{sid}.json").read_text())
    b, _ = D.align_arm_labels(a, b_raw)
    meta_a = a.pop("record_meta")
    b.pop("record_meta", None)

    resolve = Resolver(sid, field_rulings, study_rulings, cohorts)
    merged, _ = merge(a, b, "", resolve)
    if resolve.cohort:
        apply_cohort(merged, resolve.cohort)
    mine = [e for e in edits if e["study_id"] == sid]
    if apply and mine:
        E.apply_edits(merged, mine)  # EditError propagates: a stale edit blocks the study
        for e in mine:
            resolve.record(e["path"], "edit", e["op"], f"edit {e['edit_id']}",
                           e["adjudicator"], e["adjudicator_type"])

    log = resolve.log
    open_rows = [x for x in log if x["action"] == "open"]
    adjudicators = sorted({f"{x['adjudicator']} ({settled_by(x)})" for x in log
                           if x["adjudicator"] and settled_by(x) in ("human", "model")})
    by = Counter(settled_by(x) for x in log if x["kind"] != "edit")
    notes = (
        f"Reconciled within the analysis scope (analysis/scope.py; Option B, "
        f"co-author decision 2026-09-15). {len(log)} reviewer differences: "
        f"{sum(1 for x in log if x['in_scope'])} in scope. Settled by human rulings "
        f"{by['human']}, model rulings {by['model']}, content-neutral rules {by['rule']}; "
        f"{by['unadjudicated']} outside scope were not adjudicated and carry GLM's reading. "
        f"Every ruling: extraction/adjudication/rulings.csv."
        + (f" {len(mine)} post-merge edit(s) by "
           f"{', '.join(sorted({e['adjudicator'] for e in mine}))}: extraction/adjudication/edits.csv."
           if apply and mine else "")
    )
    merged = {
        "record_meta": {
            "study_id": sid,
            "extractor": "reconciled",
            "model": "; ".join(adjudicators) or "rules only (no field needed a ruling)",
            "extraction_date": date.today().isoformat(),
            "source_file": meta_a.get("source_file"),
            "schema_version": SCHEMA_VERSION,
            "adjudication_notes": notes,
        },
        **merged,
    }
    return merged, log, open_rows


def validation_errors(rec, sid):
    schema = json.loads(V.SCHEMA_PATH.read_text())
    errors = [
        f"{'.'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in Draft202012Validator(schema).iter_errors(rec)
    ]
    errs, _ = V.cross_field_checks(rec, Path(f"{sid}.json"))
    return errors + errs


def duplicate_measurements(rec):
    """Arm-level readings recorded twice in one record.

    Accepting both reviewers' one-sided elements can double a measurement when
    they structured it differently — one entry per timepoint against one
    crossover entry holding both conditions — and a doubled row would be pooled
    twice. Reported, never silently collapsed.

    The measurement's name is part of the reading: two different rows of one
    table often print the same summary (beitler_2019: prone positioning and ECMO
    both 1/102 vs 3/98), and flagging them pushed adjudicators to drop real data.
    IQR bounds count too, since medians coincide far more often than intervals.
    """
    seen, dups = {}, []
    for i, o in enumerate(rec.get("outcomes") or []):
        for d in o.get("data") or []:
            if d.get("value") is None and d.get("events") is None:
                continue
            k = (o.get("domain"), D._norm(o.get("name_verbatim")), D.timepoint_token(o.get("timepoint")),
                 d.get("arm_label"), d.get("value"), d.get("dispersion"), d.get("dispersion_low"),
                 d.get("dispersion_high"), d.get("events"), d.get("total"))
            if k in seen and seen[k] != i:
                dups.append((o.get("name_verbatim"), k))
            seen.setdefault(k, i)
    return dups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--study", action="append", help="limit to these study_ids")
    ap.add_argument("--open", action="store_true", help="list each open difference")
    ap.add_argument("--export-open", type=Path, help="write open in-scope differences to CSV")
    ap.add_argument("--provisional", type=Path,
                    help="write every merged record here, complete or not (never the reconciled dir)")
    ap.add_argument("--log", type=Path, help="write the full resolution log to CSV")
    ap.add_argument("--glm", type=Path, default=GLM, help=argparse.SUPPRESS)
    ap.add_argument("--deepseek", type=Path, default=DEEPSEEK, help=argparse.SUPPRESS)
    ap.add_argument("--ledger", type=Path, default=R.LEDGER, help=argparse.SUPPRESS)
    ap.add_argument("--out", type=Path, default=OUT, help=argparse.SUPPRESS)
    args = ap.parse_args()
    glm, deepseek, out = args.glm, args.deepseek, args.out

    if args.provisional and args.provisional.resolve() == out.resolve():
        sys.exit("--provisional must not point at the reconciled directory")

    field_rulings, study_rulings = R.load(args.ledger)
    cohorts = load_cohorts(args.ledger)
    edits = E.load(args.ledger)
    ids = args.study or sorted({p.stem for p in glm.glob("*.json")} & {p.stem for p in deepseek.glob("*.json")})

    written, blocked, all_open, all_log = [], [], [], []
    print(f"{'study':<22}{'diffs':>6}{'scope':>7}{'ruled':>7}{'rule':>6}{'open':>6}{'edits':>6}  status")
    for sid in ids:
        try:
            rec, log, open_rows = reconcile_study(sid, field_rulings, study_rulings, cohorts, glm,
                                                  deepseek, edits)
            edit_error = None
        except E.EditError as err:
            rec, log, open_rows = reconcile_study(sid, field_rulings, study_rulings, cohorts, glm,
                                                  deepseek, edits, apply=False)
            edit_error = str(err)
        errors = validation_errors(rec, sid)
        dups = duplicate_measurements(rec)
        n_edits = sum(1 for x in log if x["kind"] == "edit")
        log = [x for x in log if x["kind"] != "edit"]
        scope_n = sum(1 for x in log if x["in_scope"])
        ruled = sum(1 for x in log if x["in_scope"] and not x["basis"].startswith(("rule", "out of scope"))
                    and x["action"] != "open")
        ruleb = sum(1 for x in log if x["in_scope"] and x["basis"].startswith("rule"))
        if open_rows:
            status = "open"
        elif edit_error:
            status = "edit failed"
        elif sid in cohorts and not settled(cohorts[sid]):
            status = "cohort pending"
        elif errors:
            status = f"invalid ({len(errors)})"
        elif dups:
            status = f"duplicate readings ({len(dups)})"
        else:
            status = "complete"
        print(f"{sid:<22}{len(log):>6}{scope_n:>7}{ruled:>7}{ruleb:>6}{len(open_rows):>6}{n_edits:>6}  {status}")
        if edit_error:
            print(f"      EDIT ERROR {edit_error}")
        if args.open:
            for x in open_rows:
                print(f"      {x['kind']:<10} {x['basis']:<17} {x['field']}")
            for e in errors[:10]:
                print(f"      ERROR {e}")
            for name, k in dups:
                print(f"      DUPLICATE {name}: {k}")
        for x in open_rows:
            all_open.append({"study_id": sid, **x})
        for x in log:
            all_log.append({"study_id": sid, **x})

        if args.provisional:
            args.provisional.mkdir(parents=True, exist_ok=True)
            (args.provisional / f"{sid}.json").write_text(
                json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
        if status == "complete":
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{sid}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
            written.append(sid)
        else:
            blocked.append(sid)
            stale = out / f"{sid}.json"
            if stale.exists():
                stale.unlink()  # a record that is no longer complete must not linger

    # Overlap is a relation between records, so it is checked across everything
    # now in the reconciled directory, not record by record.
    problems = V.cohort_consistency(
        [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out.glob("*.json"))]
    ) if out.exists() else []
    for p in problems:
        print(f"  COHORT ERROR  {p}")
    print(f"\n{len(written)} written to {out}, {len(blocked)} blocked; "
          f"{len(all_open)} open in-scope difference(s)")
    if args.export_open:
        rows = {(r["study_id"], r["field"], i): r for i, r in enumerate(all_open)}
        diffs = {(d["study_id"], d["field"]): d for sid in {r["study_id"] for r in all_open}
                 for d in D.compare(sid, glm, deepseek)[0]}
        with args.export_open.open("w", newline="", encoding="utf-8") as fh:
            cols = ["study_id", "field", "kind", "basis", "glm", "deepseek", "glm_quote", "deepseek_quote"]
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
            w.writeheader()
            for k, r in rows.items():
                w.writerow({**diffs.get(k[:2], {}), **r})
        print(f"wrote {len(rows)} open difference(s) to {args.export_open}")
    if args.log:
        with args.log.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["study_id", "field", "kind", "action", "basis",
                                               "adjudicator", "in_scope"], lineterminator="\n")
            w.writeheader()
            w.writerows(all_log)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
