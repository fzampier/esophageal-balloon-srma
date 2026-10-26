#!/usr/bin/env python3
"""The third-model adjudication pass over open in-scope differences.

    python3 analysis/model_pass.py build [--study ID ...]     # input + packet per study
    python3 analysis/model_pass.py show ID [--element N] [--quotes]   # read a packet
    python3 analysis/model_pass.py try VERDICT.json           # dry-run a verdict file
    python3 analysis/model_pass.py import VERDICT.json ...    # record verdicts as model rulings

The adjudicating model must be neither extractor — no reviewer settles its own
disagreements — and it reads the paper exactly as the extractors did: build
renders the column-sorted text layer and 120-dpi page images with
analysis/extract.py's own render_pdf, into analysis/derived/model_pass/<study>/.

The packet holds only what the model may decide. Differences inside an outcome or
arm are grouped under that element, because the unit of judgement is whose
representation of a measurement stands — two reviewers who structured one table
differently disagree in hundreds of rows but on one question. Panel conflicts
(contested readings on pooled quantities), Anton's Unclear rows, and cohort
fields of a pending cohort are listed as reserved: they are the co-authors'.

The model chooses between the two readings, or returns `unclear`. It never
writes a reading of its own; anything neither reviewer got right goes to a
human. Procedure and output format: extraction/adjudication/MODEL_PASS.md.
"""

import argparse
import base64
import csv
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diff as D  # noqa: E402
import reconcile as RC  # noqa: E402
import rulings as R  # noqa: E402
import worksheet as W  # noqa: E402
from scope import in_scope  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "analysis" / "derived" / "model_pass"
VERDICTS = R.LEDGER / "model_pass"
DPI = 120
ELEMENT_ARRAYS = ("outcomes", "arms")
DECISIONS = {"glm", "deepseek", "unclear"}


def row_id(sid, field, kind, glm, deepseek):
    return hashlib.sha1(f"{sid}|{field}|{kind}|{glm}|{deepseek}".encode()).hexdigest()[:10]


def element_of(field):
    """`outcomes[x @72h].data[a].value` -> `outcomes[x @72h]`; None outside outcomes/arms."""
    for key in ELEMENT_ARRAYS:
        if field.startswith(key + "["):
            return field[: field.index("]") + 1]
    return None


def open_rows(sid):
    """(open log entries with their full readings, loaded records) for one study."""
    field_rulings, study_rulings = R.load()
    cohorts = RC.load_cohorts()
    _, log, _ = RC.reconcile_study(sid, field_rulings, study_rulings, cohorts)
    wanted = {x["field"] for x in log if x["action"] == "open"}

    a = json.loads((RC.GLM / f"{sid}.json").read_text())
    b, _ = D.align_arm_labels(a, json.loads((RC.DEEPSEEK / f"{sid}.json").read_text()))
    a.pop("record_meta")
    b.pop("record_meta", None)
    pending_cohort = sid in cohorts and not RC.settled(cohorts[sid])

    rows = []
    for path, kind, va, vb, pa, pb in D.walk(a, b):
        if path not in wanted:
            continue
        gd, dd = D._disp(va), D._disp(vb)
        entry = next((x for x in log if x["field"] == path and x["kind"] == kind
                      and x["action"] == "open"), None)
        if entry is None:
            continue
        reserved = None
        if entry["basis"] == "returned Unclear":  # a co-author's; the model's own unclears stay open to it
            reserved = "returned Unclear by a co-author"
        elif kind == "conflict" and W.triage(path, kind) == "panel":
            reserved = "panel conflict: contested reading, co-authors rule row by row"
        elif pending_cohort and RC.normalize(path) in RC.COHORT_FIELDS:
            reserved = "cohort relation pending with the co-authors"
        rows.append({
            "id": row_id(sid, path, kind, gd, dd),
            "field": path, "kind": kind,
            "glm": None if va is D.MISSING else va,
            "deepseek": None if vb is D.MISSING else vb,
            "glm_display": gd, "deepseek_display": dd,
            "glm_quote": D.quote_of(va) or D.quote_of(pa),
            "deepseek_quote": D.quote_of(vb) or D.quote_of(pb),
            "reserved": reserved,
        })
    return rows, a, b


def build(sid):
    manifest = {r["study_id"]: r for r in csv.DictReader(
        (ROOT / "studies" / "manifest.csv").open(encoding="utf-8"))}
    pdf_rel = f"studies/fulltext/{manifest[sid]['filename']}"
    out = WORK / sid
    # Overwrite in place: a verdict being written in this directory must survive
    # a rebuild, and OneDrive holds locks on directories it is syncing.
    (out / "pages").mkdir(parents=True, exist_ok=True)
    for stale in (out / "pages").glob("page-*.png"):
        stale.unlink()

    import extract as E  # PyMuPDF; the extractors' own renderer, not a copy of it

    text, images = E.render_pdf(ROOT / pdf_rel, DPI)
    (out / "text.txt").write_text(text, encoding="utf-8")
    for i, png in enumerate(images, start=1):
        (out / "pages" / f"page-{i:02d}.png").write_bytes(base64.b64decode(png))

    rows, a, b = open_rows(sid)
    groups = {}
    for key in ELEMENT_ARRAYS:
        for label, ea, eb in D.match_elements(a.get(key) or [], b.get(key) or []):
            g = groups.setdefault(f"{key}[{label}]", {"glm": [], "deepseek": []})
            if ea is not D.MISSING:
                g["glm"].append(ea)
            if eb is not D.MISSING:
                g["deepseek"].append(eb)

    elements, fields, reserved = {}, [], []
    for r in rows:
        if r["reserved"]:
            reserved.append({k: r[k] for k in ("id", "field", "kind", "glm_display",
                                               "deepseek_display", "reserved")})
            continue
        target = element_of(r["field"])
        if target:
            e = elements.setdefault(target, {"target": target, **groups.get(target, {}),
                                             "differences": []})
            e["differences"].append({k: r[k] for k in ("id", "field", "kind", "glm_display",
                                                        "deepseek_display")})
        else:
            fields.append({k: r[k] for k in ("id", "field", "kind", "glm", "deepseek",
                                             "glm_quote", "deepseek_quote")})

    packet = {
        "study_id": sid,
        "source_file": pdf_rel,
        "input": {
            "text": str((out / "text.txt").relative_to(ROOT)),
            "pages": len(images),
            "page_images": str((out / "pages").relative_to(ROOT)),
            "dpi": DPI,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        },
        "arm_labels": {"glm": [x.get("label") for x in a.get("arms") or []],
                       "deepseek": [x.get("label") for x in b.get("arms") or []]},
        "elements": list(elements.values()),
        "fields": fields,
        "reserved": reserved,
    }
    (out / "packet.json").write_text(json.dumps(packet, indent=1, ensure_ascii=False), encoding="utf-8")
    n_diff = sum(len(e["differences"]) for e in elements.values())
    print(f"{sid:<22} pages {len(images):>2}  elements {len(elements):>4} ({n_diff} differences)  "
          f"fields {len(fields):>3}  reserved {len(reserved):>3}")
    return packet


def ledger_lines(verdict_path):
    """Translate a verdict file into rulings.csv lines, or raise ValueError."""
    raw = Path(verdict_path).read_bytes()
    v = json.loads(raw)
    sid, adjudicator = v["study_id"], v.get("adjudicator", "")
    if not adjudicator or any(x in adjudicator.lower() for x in ("glm", "deepseek")):
        raise ValueError("adjudicator must name the third model, and it must not be an extractor")
    packet = json.loads((WORK / sid / "packet.json").read_text(encoding="utf-8"))
    rows, _, _ = open_rows(sid)
    by_id = {r["id"]: r for r in rows}
    targets = {e["target"] for e in packet["elements"]}
    digest = hashlib.sha256(raw).hexdigest()[:16]
    source = (f"model_pass/{sid}.json sha256:{digest}; input text sha256:"
              f"{packet['input']['text_sha256'][:16]} dpi {packet['input']['dpi']}")

    lines, problems = [], []
    for i, ru in enumerate(v.get("rulings") or []):
        decision = ru.get("decision")
        if decision not in DECISIONS:
            problems.append(f"ruling {i}: decision {decision!r} is not one of {sorted(DECISIONS)}")
            continue
        note = " | ".join(x for x in (ru.get("reason"), ru.get("evidence")) if x)
        common = {"study_id": sid, "decision": decision, "value": "", "note": note,
                  "adjudicator": adjudicator, "adjudicator_type": "model",
                  "basis": "model pass", "source": source, "recorded": date.today().isoformat()}
        if "target" in ru:
            if ru["target"] not in targets:
                problems.append(f"ruling {i}: element {ru['target']!r} is not in the packet")
                continue
            lines.append({**common, "ruling_id": R.ruling_id(sid, ru["target"], adjudicator, digest),
                          "field": ru["target"], "kind": "element", "glm": "", "deepseek": "",
                          "source_field": ru["target"]})
        elif "id" in ru:
            r = by_id.get(ru["id"])
            if r is None or r["reserved"]:
                problems.append(f"ruling {i}: id {ru['id']!r} is "
                                f"{'reserved for the co-authors' if r else 'not an open difference'}")
                continue
            lines.append({**common, "ruling_id": R.ruling_id(sid, r["field"], r["id"], adjudicator, digest),
                          "field": r["field"], "kind": r["kind"], "glm": r["glm_display"],
                          "deepseek": r["deepseek_display"], "source_field": r["field"]})
        else:
            problems.append(f"ruling {i}: needs a target (element) or an id (field)")
    if problems:
        raise ValueError("\n".join(problems))
    return sid, lines, raw


def try_verdict(path):
    try:
        sid, lines, _ = ledger_lines(path)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"verdict rejected:\n{exc}")
        return 1
    field_rulings, study_rulings = R.load()
    cohorts = RC.load_cohorts()
    rec, log, open_ = RC.reconcile_study(sid, field_rulings + lines, study_rulings, cohorts)
    rows, _, _ = open_rows(sid)
    reserved = {r["field"] for r in rows if r["reserved"]}
    mine = [x for x in open_ if x["field"] not in reserved]
    errors = RC.validation_errors(rec, sid)
    dups = RC.duplicate_measurements(rec)
    unclear = sum(1 for x in lines if x["decision"] == "unclear")
    print(f"{sid}: {len(lines)} ruling(s), {unclear} unclear")
    print(f"  still open for the model:  {len(mine)}")
    for x in mine[:40]:
        print(f"    {x['kind']:<10} {x['field']}")
    print(f"  reserved for co-authors:   {len([x for x in open_ if x['field'] in reserved])}")
    print(f"  validation errors:         {len(errors)}")
    for e in errors[:20]:
        print(f"    {e}")
    print(f"  duplicated readings:       {len(dups)}")
    for name, k in dups[:20]:
        print(f"    {name}: {k}")
    return 0 if not (mine or errors or dups) else 2


def import_verdicts(paths):
    field_rulings, study_rulings = R.load()
    for path in paths:
        sid, lines, raw = ledger_lines(path)
        digest = hashlib.sha256(raw).hexdigest()[:16]
        if any(digest in r["source"] for r in field_rulings):
            print(f"{path}: already imported")
            continue
        VERDICTS.mkdir(parents=True, exist_ok=True)
        (VERDICTS / f"{sid}.json").write_bytes(raw)
        field_rulings += lines
        print(f"{sid}: {len(lines)} model ruling(s) recorded")
    R.write_csv(R.RULINGS, R.RULING_COLUMNS, field_rulings)
    return 0


def compact(node, quotes):
    """An element without nulls, empty lists or (unless asked) provenance."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "provenance" or k.endswith("_provenance"):
                if quotes and isinstance(v, dict) and v.get("quote"):
                    out["quote"] = f"{v['quote']}  [{v.get('location')}]"
                continue
            v = compact(v, quotes)
            if v not in (None, [], {}):
                out[k] = v
        return out
    if isinstance(node, list):
        return [compact(x, quotes) for x in node]
    return node


def show(sid, which, quotes):
    packet = json.loads((WORK / sid / "packet.json").read_text(encoding="utf-8"))
    print(f"{sid}  arms: glm {packet['arm_labels']['glm']}  deepseek {packet['arm_labels']['deepseek']}")
    for i, e in enumerate(packet["elements"]):
        if which is not None and i not in which:
            continue
        print()
        print(f"[{i}] target: {e['target']}")
        for d in e["differences"]:
            print(f"    diff {d['id']} {d['kind']:<9} {d['field'][len(e['target']):] or '(whole element)'}")
            print(f"         glm: {d['glm_display'][:140]}")
            print(f"         ds:  {d['deepseek_display'][:140]}")
        for side in ("glm", "deepseek"):
            for el in e.get(side) or []:
                print(f"  {side.upper()}: {json.dumps(compact(el, quotes), ensure_ascii=False)}")
    if which is None:
        for f in packet["fields"]:
            print()
            print(f"field {f['id']} {f['kind']} {f['field']}")
            print(f"  GLM: {json.dumps(f['glm'], ensure_ascii=False)}  {f['glm_quote']}")
            print(f"  DS:  {json.dumps(f['deepseek'], ensure_ascii=False)}  {f['deepseek_quote']}")
        for r in packet["reserved"]:
            print()
            print(f"reserved (not yours) {r['field']}: {r['glm_display']} | "
                  f"{r['deepseek_display']} — {r['reserved']}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--study", action="append")
    t = sub.add_parser("try")
    t.add_argument("verdict")
    sh = sub.add_parser("show", help="print a study's packet compactly")
    sh.add_argument("study")
    sh.add_argument("--element", type=int, action="append", help="only these element indices")
    sh.add_argument("--quotes", action="store_true", help="include each reading's quote")
    i = sub.add_parser("import")
    i.add_argument("verdicts", nargs="+")
    args = ap.parse_args()
    if args.cmd == "build":
        for sid in args.study or W.study_ids():
            build(sid)
        return 0
    if args.cmd == "show":
        return show(args.study, set(args.element) if args.element else None, args.quotes) or 0
    if args.cmd == "try":
        return try_verdict(args.verdict)
    return import_verdicts(args.verdicts)


if __name__ == "__main__":
    sys.exit(main())
