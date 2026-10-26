#!/usr/bin/env python3
"""Agreement between the two extractors, and how their differences were settled.

    python3 analysis/agreement.py            # -> analysis/results/agreement/

Option B (DECISIONS.md B1) reconciles only the fields that reach the analysis
and promises to report agreement on the rest. This is that report, in four
parts, each a CSV plus a summary.md for the supplement:

1. Raw leaf-level agreement (per_study.csv). Every leaf of the two records
   compared at the same path, array positions included. Two reviewers listing
   the same outcomes in a different order disagree here, so this figure
   understates agreement; it is the headline precisely because nothing
   (element pairing, vocabulary alignment) can flatter it. diff.py computes it.

2. Matched agreement (per_study.csv, by_scope.csv, coverage.csv), reported as two
   separate questions: reading (where both reviewers recorded a value, was it the
   same?) and coverage (did both record the entry at all?). Mixed into one
   figure, coverage swamps reading, because an outcome only one reviewer
   extracted carries every value beneath it. Outcomes and arms paired by
   identity exactly as diff.py pairs them, arm labels aligned; each leaf that at
   least one reviewer filled counts as agreement, conflict (both filled,
   different), or one-sided (one filled). Free-text annotation and provenance
   are excluded, as in diff.py. Split inside and outside the analysis scope.

3. Chance-corrected agreement on study-level categorical fields (kappa.csv):
   Cohen's kappa over all studies, "not recorded" counted as a category.

4. Resolution (resolution.csv): how every in-scope difference was settled -
   human, model, content-neutral rule - or is still open, from the same
   resolution log reconcile.py writes. This part changes as rulings arrive, so
   the summary is stamped with the commit and date.
"""

import collections
import csv
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diff as D  # noqa: E402
import edits as E  # noqa: E402
import reconcile as RC  # noqa: E402
import rulings as R  # noqa: E402
import worksheet as W  # noqa: E402
from scope import in_scope  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "analysis" / "results" / "agreement"

KAPPA_FIELDS = [
    ("design.study_design", "Study design"),
    ("design.comparison_structure", "Comparison structure"),
    ("design.randomised", "Randomised"),
    ("population.ards_status", "ARDS status"),
    ("intervention.balloon_calibration.method", "Balloon calibration method"),
    ("intervention.ptp_computation.method", "Transpulmonary pressure computation"),
    ("intervention.peep_titration_target.strategy", "PEEP titration strategy"),
    ("intervention.timing.reference_event", "Timing reference event"),
    ("subgroup_flags.ards_subgroup", "ARDS subgroup"),
    ("subgroup_flags.obesity_subgroup", "Obesity subgroup"),
    ("subgroup_flags.timing_subgroup", "Timing subgroup"),
    ("eligibility.include_final", "Included"),
    ("citation.is_primary_report", "Primary report"),
]


def leaves(node):
    """Non-blank scalar leaves of a subtree, provenance and annotation excluded."""
    if isinstance(node, dict):
        return sum(leaves(v) for k, v in node.items()
                   if k != "provenance" and not k.endswith("_provenance") and not D.is_narrative(k))
    if isinstance(node, list):
        return sum(leaves(v) for v in node)
    return 0 if D.is_blank(node) else 1


def tally(a, b, path, counts):
    """Mirror diff.walk, counting leaves instead of yielding differences."""
    if isinstance(a, dict) and isinstance(b, dict):
        for key in set(a) | set(b):
            if key == "provenance" or key.endswith("_provenance"):
                continue
            tally(a.get(key, D.MISSING), b.get(key, D.MISSING), f"{path}.{key}" if path else key, counts)
        return
    if isinstance(a, list) and isinstance(b, list):
        for label, ea, eb in D.match_elements(a, b):
            tally(ea, eb, f"{path}[{label}]", counts)
        return
    if D.is_provenance_leaf(path) or D.is_narrative(path):
        return
    scope = "in scope" if in_scope(path) else "out of scope"
    a_blank, b_blank = D.is_blank(a), D.is_blank(b)
    if a_blank and b_blank:
        return
    if a_blank != b_blank:
        counts[scope]["one_sided"] += leaves(b if a_blank else a)
    elif isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
        counts[scope]["conflict"] += max(leaves(a), leaves(b), 1)
    elif a == b:
        counts[scope]["agree"] += 1
    else:
        counts[scope]["conflict"] += 1


def get(rec, dotted):
    cur = rec
    for k in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def cohen_kappa(pairs):
    n = len(pairs)
    if not n:
        return None, None
    po = sum(1 for x, y in pairs if x == y) / n
    ca, cb = collections.Counter(x for x, _ in pairs), collections.Counter(y for _, y in pairs)
    pe = sum(ca[c] * cb[c] for c in set(ca) | set(cb)) / (n * n)
    return po, (None if pe == 1 else (po - pe) / (1 - pe))


def pct(x, n):
    return f"{100 * x / n:.1f}%" if n else "—"


def stamp():
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "extraction"], cwd=ROOT,
                                    capture_output=True, text=True).stdout.strip())
    except Exception:  # noqa: BLE001
        sha, dirty = "unknown", False
    return f"{date.today().isoformat()}, commit {sha}{' (uncommitted ledger changes)' if dirty else ''}"


def write(name, rows, cols):
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / name).open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main():
    ids = W.study_ids()
    per_study, scope_tot = [], collections.defaultdict(collections.Counter)
    raw_diff = raw_n = 0
    for sid in ids:
        diffs, comparable, raw = D.compare(sid)
        raw_n += comparable
        raw_diff += raw
        a = json.loads((D.GLM_DIR / f"{sid}.json").read_text())
        b, _ = D.align_arm_labels(a, json.loads((D.DEEPSEEK_DIR / f"{sid}.json").read_text()))
        a.pop("record_meta", None)
        b.pop("record_meta", None)
        counts = collections.defaultdict(collections.Counter)
        tally(a, b, "", counts)
        row = {"study_id": sid, "raw_leaves": comparable, "raw_disagreements": raw,
               "raw_agreement": f"{(comparable - raw) / comparable:.3f}" if comparable else "",
               "reported_differences": len(diffs),
               "conflicts": sum(1 for d in diffs if d["kind"] == "conflict"),
               "one_sided": sum(1 for d in diffs if d["kind"] == "one_sided")}
        for scope in ("in scope", "out of scope"):
            c = counts[scope]
            filled = c["agree"] + c["conflict"] + c["one_sided"]
            key = scope.replace(" ", "_")
            row[f"{key}_filled_leaves"] = filled
            row[f"{key}_agreement"] = f"{c['agree'] / filled:.3f}" if filled else ""
            scope_tot[scope].update(c)
        per_study.append(row)
    write("per_study.csv", per_study, list(per_study[0]))

    by_scope = []
    for scope in ("in scope", "out of scope"):
        c = scope_tot[scope]
        filled = c["agree"] + c["conflict"] + c["one_sided"]
        by_scope.append({"scope": scope, "filled_leaves": filled, "agree": c["agree"],
                         "conflict": c["conflict"], "one_sided": c["one_sided"],
                         "agreement": f"{c['agree'] / filled:.3f}" if filled else "",
                         "reading_agreement": f"{c['agree'] / (c['agree'] + c['conflict']):.3f}"
                         if c["agree"] + c["conflict"] else "",
                         "conflict_share": f"{c['conflict'] / filled:.3f}" if filled else "",
                         "one_sided_share": f"{c['one_sided'] / filled:.3f}" if filled else ""})
    all_c = scope_tot["in scope"] + scope_tot["out of scope"]
    all_filled = all_c["agree"] + all_c["conflict"] + all_c["one_sided"]
    by_scope.append({"scope": "all", "filled_leaves": all_filled, "agree": all_c["agree"],
                     "conflict": all_c["conflict"], "one_sided": all_c["one_sided"],
                     "agreement": f"{all_c['agree'] / all_filled:.3f}",
                     "reading_agreement": f"{all_c['agree'] / (all_c['agree'] + all_c['conflict']):.3f}",
                     "conflict_share": f"{all_c['conflict'] / all_filled:.3f}",
                     "one_sided_share": f"{all_c['one_sided'] / all_filled:.3f}"})
    write("by_scope.csv", by_scope, list(by_scope[0]))

    # Coverage: whole entries each reviewer recorded, paired as diff.py pairs them.
    cov = collections.Counter()
    for sid in ids:
        a = json.loads((D.GLM_DIR / f"{sid}.json").read_text())
        b, _ = D.align_arm_labels(a, json.loads((D.DEEPSEEK_DIR / f"{sid}.json").read_text()))
        for label, ea, eb in D.match_elements(a.get("arms") or [], b.get("arms") or []):
            cov[("arms", "both" if ea is not D.MISSING and eb is not D.MISSING
                 else "GLM only" if eb is D.MISSING else "DeepSeek only")] += 1
        for _, oa, ob in D.match_elements(a.get("outcomes") or [], b.get("outcomes") or []):
            which = "both" if oa is not D.MISSING and ob is not D.MISSING else "GLM only" if ob is D.MISSING else "DeepSeek only"
            cov[("outcome entries", which)] += 1
            if which == "both":
                for part in ("data", "paired_effects", "effect_estimates"):
                    for _, xa, xb in D.match_elements(oa.get(part) or [], ob.get(part) or []):
                        w = "both" if xa is not D.MISSING and xb is not D.MISSING else "GLM only" if xb is D.MISSING else "DeepSeek only"
                        cov[(f"{part} within shared outcomes", w)] += 1
    cov_rows = []
    for level in ("arms", "outcome entries", "data within shared outcomes",
                  "paired_effects within shared outcomes", "effect_estimates within shared outcomes"):
        n = sum(cov[(level, w)] for w in ("both", "GLM only", "DeepSeek only"))
        cov_rows.append({"level": level, "total": n, "both": cov[(level, "both")],
                         "glm_only": cov[(level, "GLM only")], "deepseek_only": cov[(level, "DeepSeek only")]})
    write("coverage.csv", cov_rows, list(cov_rows[0]))

    # Differences by triage class, as reported to the co-authors.
    triaged = W.triaged_rows(W.load_manifest())
    classes = collections.Counter((("in scope" if in_scope(r["field"]) else "out of scope"), r["kind"])
                                  for r in triaged)
    diff_rows = [{"scope": s, "kind": k, "differences": n} for (s, k), n in sorted(classes.items())]
    write("differences_by_class.csv", diff_rows, ["scope", "kind", "differences"])

    kappa_rows = []
    recs = {sid: (json.loads((D.GLM_DIR / f"{sid}.json").read_text()),
                  json.loads((D.DEEPSEEK_DIR / f"{sid}.json").read_text())) for sid in ids}
    for dotted, label in KAPPA_FIELDS:
        pairs = []
        for g, d in recs.values():
            x, y = get(g, dotted), get(d, dotted)
            pairs.append((("not recorded" if x is None else str(x)), ("not recorded" if y is None else str(y))))
        po, k = cohen_kappa(pairs)
        kappa_rows.append({"field": dotted, "label": label, "studies": len(pairs),
                           "percent_agreement": f"{po:.3f}", "kappa": "" if k is None else f"{k:.2f}",
                           "categories_used": len({v for p in pairs for v in p})})
    write("kappa.csv", kappa_rows, list(kappa_rows[0]))

    field_rulings, study_rulings = R.load()
    cohorts = RC.load_cohorts()
    res = collections.Counter()
    edits = collections.Counter()
    for sid in ids:
        _, log, _ = RC.reconcile_study(sid, field_rulings, study_rulings, cohorts, edits=E.load())
        for x in log:
            if x["kind"] == "edit":
                # A post-merge edit is a correction written into the record, not
                # a difference settled; counted apart so it cannot pad the table.
                edits[x["adjudicator"]] += 1
                continue
            who = RC.settled_by(x)
            name = x["adjudicator"] if who in ("human", "model") else ""
            res[("in scope" if x["in_scope"] else "out of scope", who, name)] += 1
    res_rows = [{"scope": s, "settled_by": w, "adjudicator": n, "differences": c}
                for (s, w, n), c in sorted(res.items())]
    write("resolution.csv", res_rows, ["scope", "settled_by", "adjudicator", "differences"])

    stale = sum(1 for r in field_rulings if r["adjudicator_type"] == "human") and \
        subprocess.run([sys.executable, str(ROOT / "analysis" / "rulings.py"), "check"],
                       cwd=ROOT, capture_output=True, text=True).stdout.splitlines()[0]

    ins, outs = by_scope[0], by_scope[1]
    in_res = [r for r in res_rows if r["scope"] == "in scope"]
    total_in = sum(r["differences"] for r in in_res)
    lines = [
        "# Inter-extractor agreement",
        "",
        f"Generated by `analysis/agreement.py` — {stamp()}. Extractions: GLM 5.3 Flash and DeepSeek V4",
        f"Flash (vision), {len(ids)} studies, identical input and schema 1.2.0.",
        "",
        "## Headline: raw leaf-level agreement",
        "",
        f"Of {raw_n:,} leaves compared at identical paths, {raw_n - raw_diff:,} matched: "
        f"**{pct(raw_n - raw_diff, raw_n)}**. Conservative by construction — array order counts.",
        "",
        "## Matched agreement (outcomes and arms paired by identity)",
        "",
        "Two different questions, kept apart because they give very different answers.",
        "",
        "**Reading** — where both reviewers recorded a value, did they record the same one?",
        "",
        "| Scope | Values both recorded | Same | Different |",
        "|---|---|---|---|",
    ]
    for r in by_scope:
        both = r["agree"] + r["conflict"]
        lines.append(f"| {r['scope']} | {both:,} | {pct(r['agree'], both)} | {pct(r['conflict'], both)} |")
    lines += [
        "",
        "**Coverage** — did both reviewers record the entry at all? \"Only\" includes entries the",
        "reviewers structured differently (one outcome per condition against one holding every",
        "condition), not only omissions.",
        "",
        "| Entries | Total | Both | GLM only | DeepSeek only |",
        "|---|---|---|---|---|",
    ]
    for r in cov_rows:
        lines.append(f"| {r['level']} | {r['total']:,} | {pct(r['both'], r['total'])} | "
                     f"{pct(r['glm_only'], r['total'])} | {pct(r['deepseek_only'], r['total'])} |")
    lines += [
        "",
        "At value level, counting every value inside an entry only one reviewer recorded:",
        "",
        "| Scope | Values recorded by ≥1 reviewer | Same | Different | One reviewer only |",
        "|---|---|---|---|---|",
    ]
    for r in by_scope:
        n = r["filled_leaves"]
        lines.append(f"| {r['scope']} | {n:,} | {pct(r['agree'], n)} | {pct(r['conflict'], n)} | {pct(r['one_sided'], n)} |")
    lines += [
        "",
        "The last column is dominated by whole outcome entries one reviewer did not extract:",
        "each carries every value beneath it.",
        "",
        "## Reported differences",
        "",
        "| Scope | Conflicts | One-sided |",
        "|---|---|---|",
    ]
    for s in ("in scope", "out of scope"):
        lines.append(f"| {s} | {classes[(s, 'conflict')]:,} | {classes[(s, 'one_sided')]:,} |")
    lines += [
        "",
        f"## Categorical fields (Cohen's kappa, {len(ids)} studies)",
        "",
        "| Field | % agreement | κ |",
        "|---|---|---|",
    ]
    for r in kappa_rows:
        lines.append(f"| {r['label']} | {100 * float(r['percent_agreement']):.0f}% | {r['kappa'] or '—'} |")
    lines += [
        "",
        f"## How the {total_in:,} in-scope differences were settled (as of this run)",
        "",
        "| Settled by | Adjudicator | Differences | Share |",
        "|---|---|---|---|",
    ]
    for r in in_res:
        lines.append(f"| {r['settled_by']} | {r['adjudicator'] or '—'} | {r['differences']:,} | {pct(r['differences'], total_in)} |")
    lines += [
        "",
        f"Outside scope: {outs['filled_leaves']:,} filled leaves; differences there are not adjudicated",
        "(Option B) and reconciled records carry GLM's reading, marked as such.",
        "",
        "Post-merge edits (extraction/adjudication/edits.csv), corrections a human wrote into the merged",
        "records where neither reviewer's reading could be chosen: "
        + (", ".join(f"{n} by {who}" for who, n in sorted(edits.items())) or "none") + ".",
        "",
        f"Ledger check: {stale or 'no human rulings'}.",
    ]
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
