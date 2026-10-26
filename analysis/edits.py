#!/usr/bin/env python3
"""Post-merge edits: corrections a human writes into a reconciled record.

A ruling in rulings.csv chooses between the two reviewers' readings of one
field. It cannot write a corrected number into an entry only one reviewer has,
add a contrast the paper prints and both reviewers missed, or drop an entry
both reviewers recorded. Those corrections live here, in
extraction/adjudication/edits.csv, one line per edit, and reconcile.py applies
them to the merged record after every ruling has been applied and before the
record is validated. Each line names the adjudicator, the workbook row or
question it answers and the page of the paper it rests on, and every reconciled
record's adjudication notes count the edits made to it.

    python3 analysis/edits.py check                       # every edit still applies cleanly
    python3 analysis/edits.py import spec.json --adjudicator "Name"   # append edits from a spec
    python3 analysis/edits.py show [study_id]             # list edits in plain words

Paths use the same identity syntax as diff.py's reports: array elements are
addressed by their label, never by position —
`outcomes[oxygenation ph continuous @30min].data[no ecmo higher peep].value`.
A label must match exactly one element of the list, or the edit fails and the
study is blocked; an edit that no longer applies is an error, never silently
skipped.

Operations:
  set   path names a field (existing or new) on an object; value is JSON.
  drop  path names a field or an array element; it is removed.
  add   path names an array; value (JSON) is appended to it.

Only humans write edits (adjudicator_type is always `human`). A model that finds
an entry wrong returns it unclear for a person to settle, as MODEL_PASS.md says.
"""

import argparse
import copy
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import diff as D  # noqa: E402
import rulings as R  # noqa: E402

EDITS = R.LEDGER / "edits.csv"
COLUMNS = ["edit_id", "study_id", "path", "op", "value", "note",
           "adjudicator", "adjudicator_type", "basis", "source", "recorded"]
OPS = ("set", "drop", "add")


class EditError(Exception):
    pass


def load(ledger=R.LEDGER):
    path = ledger / EDITS.name
    return R.read_csv(path) if path.exists() else []


def parse_path(path):
    """`outcomes[pH @30min].data[arm].value` -> [('key','outcomes'), ('elem','pH @30min'), ...]"""
    steps, i, n = [], 0, len(path)
    while i < n:
        if path[i] == ".":
            i += 1
            continue
        if path[i] == "[":
            j = path.index("]", i)
            steps.append(("elem", path[i + 1:j]))
            i = j + 1
            continue
        j = i
        while j < n and path[j] not in ".[":
            j += 1
        steps.append(("key", path[i:j]))
        i = j
    return steps


def labels_of(lst):
    return [D._short(D.element_key(el), i) for i, el in enumerate(lst)]


def locate(rec, path):
    """Return (parent, key) for the node `path` names; key is a dict key or a list index.

    The final step may name a field that does not exist yet (for `set`); every
    earlier step must resolve to exactly one node.
    """
    steps = parse_path(path)
    node, parent, key = rec, None, None
    for k, (kind, name) in enumerate(steps):
        last = k == len(steps) - 1
        if kind == "key":
            if not isinstance(node, dict):
                raise EditError(f"{path}: '{name}' reached inside a non-object")
            if name not in node:
                if last:
                    return node, name
                raise EditError(f"{path}: no field '{name}'")
            parent, key, node = node, name, node[name]
        else:
            if not isinstance(node, list):
                raise EditError(f"{path}: '[{name}]' applied to a non-list")
            labels = labels_of(node)
            hits = [i for i, lab in enumerate(labels) if lab == name]
            if not hits and name.isdigit() and int(name) < len(node):
                hits = [int(name)]
            if len(hits) != 1:
                raise EditError(f"{path}: label '[{name}]' matches {len(hits)} element(s); "
                                f"have {labels}")
            parent, key, node = node, hits[0], node[hits[0]]
    return parent, key


def apply_one(rec, edit):
    op = edit["op"]
    if op not in OPS:
        raise EditError(f"{edit['edit_id']}: unknown op '{op}'")
    value = json.loads(edit["value"]) if edit.get("value", "") != "" else None
    parent, key = locate(rec, edit["path"])
    if op == "set":
        if not isinstance(parent, dict):
            raise EditError(f"{edit['path']}: set needs a field on an object")
        parent[key] = value
    elif op == "drop":
        if isinstance(parent, list):
            del parent[key]
        elif key in parent:
            del parent[key]
        else:
            raise EditError(f"{edit['path']}: nothing to drop")
    else:  # add
        if isinstance(parent, dict) and parent.get(key) is None:
            parent[key] = []  # a reviewer may have left the array out altogether
        target = parent[key]
        if not isinstance(target, list):
            raise EditError(f"{edit['path']}: add needs an array")
        target.append(value)


def apply_edits(rec, edits):
    """Apply a study's edits in ledger order, in place. Raises EditError on the first failure."""
    for e in edits:
        apply_one(rec, e)


def edit_id(*parts):
    return R.ruling_id(*parts)


def merged_record(sid):
    """The current merge for one study, before edits, as reconcile.py builds it."""
    import reconcile as RC
    field_rulings, study_rulings = R.load()
    cohorts = RC.load_cohorts()
    rec, _, _ = RC.reconcile_study(sid, field_rulings, study_rulings, cohorts, apply=False)
    return rec


def check():
    import reconcile as RC
    edits = load()
    bad = 0
    corpus = {p.stem for p in RC.GLM.glob("*.json")} & {p.stem for p in RC.DEEPSEEK.glob("*.json")}
    for sid in sorted({e["study_id"] for e in edits}):
        if sid not in corpus:
            # a report excluded after extraction (crognier_2025, D50): its edits stay in the ledger
            print(f"  {sid:<20} {sum(e['study_id'] == sid for e in edits)} edit(s) kept; study no longer extracted")
            continue
        rec = merged_record(sid)
        for e in [x for x in edits if x["study_id"] == sid]:
            try:
                apply_one(rec, e)
            except EditError as err:
                bad += 1
                print(f"  {sid:<20} {e['edit_id']}  {err}")
    print(f"{len(edits)} edit(s), {bad} no longer apply")
    return 1 if bad else 0


def import_spec(path, adjudicator):
    """Append edits from a JSON spec: [{study_id, path, op, value, note, source, basis}].

    Every edit is applied to the current merge before anything is written, so a
    spec with one bad path writes nothing.
    """
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    existing = load()
    today = date.today().isoformat()
    new = []
    for item in spec:
        row = {
            "edit_id": edit_id(item["study_id"], item["path"], item["op"],
                               json.dumps(item.get("value")), adjudicator, item.get("source", "")),
            "study_id": item["study_id"], "path": item["path"], "op": item["op"],
            "value": "" if "value" not in item else json.dumps(item["value"], ensure_ascii=False),
            "note": item.get("note", ""), "adjudicator": adjudicator, "adjudicator_type": "human",
            "basis": item.get("basis", ""), "source": item.get("source", ""), "recorded": today,
        }
        if any(x["edit_id"] == row["edit_id"] for x in existing + new):
            raise SystemExit(f"duplicate edit: {row['study_id']} {row['op']} {row['path']}")
        new.append(row)
    for sid in sorted({e["study_id"] for e in new}):
        rec = merged_record(sid)
        for e in [x for x in existing + new if x["study_id"] == sid]:
            try:
                apply_one(rec, e)
            except EditError as err:
                raise SystemExit(f"{sid}: {err}\n  nothing written")
    R.write_csv(EDITS, COLUMNS, existing + new)
    print(f"appended {len(new)} edit(s) to {EDITS} ({len(existing) + len(new)} total)")
    return 0


def show(sid=None):
    for e in load():
        if sid and e["study_id"] != sid:
            continue
        v = f" = {e['value']}" if e["value"] else ""
        print(f"{e['study_id']:<20} {e['op']:<4} {e['path']}{v}\n"
              f"{'':<20}      {e['note']}  [{e['adjudicator']}; {e['source']}; {e['basis']}]")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    p = sub.add_parser("import")
    p.add_argument("spec", type=Path)
    p.add_argument("--adjudicator", required=True)
    p = sub.add_parser("show")
    p.add_argument("study_id", nargs="?")
    args = ap.parse_args()
    if args.cmd == "check":
        return check()
    if args.cmd == "import":
        return import_spec(args.spec, args.adjudicator)
    return show(args.study_id)


if __name__ == "__main__":
    sys.exit(main())
