#!/usr/bin/env python3
"""Report field-level disagreements between the two independent extractors.

    python3 analysis/diff.py                    # every study both extractors covered
    python3 analysis/diff.py talmor_2008        # one study
    python3 analysis/diff.py --csv disagreements.csv

Quotes are shown beneath each disagreement so the adjudicator can usually resolve
it without reopening the PDF. Provenance text itself is compared loosely: two
extractors quoting the same fact from different sentences is not a disagreement,
so quote differences are reported only where the values also differ.

Differences are split into `conflict` — both reviewers read the field and read it
differently — and `one_sided`, where one recorded a value and the other recorded
nothing. Only conflicts are contested readings; one-sided differences are usually
an omission by one reviewer. An omitted key and an explicit null both mean "not
reported" and are treated as the same state, and a one-sided difference is
reported once at the root of the subtree rather than once per leaf beneath it.
The headline agreement figure remains the unmodified raw leaf-level statistic, so
the reclassification cannot make agreement look better than it is.
"""

import argparse
import csv
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GLM_DIR = ROOT / "extraction" / "glm"
DEEPSEEK_DIR = ROOT / "extraction" / "deepseek"

# Differences here are expected and carry no meaning for agreement.
IGNORED_PREFIXES = ("record_meta",)
PROVENANCE_LEAVES = ("quote", "location")


def flatten(node, path="", out=None):
    out = {} if out is None else out
    if isinstance(node, dict):
        if not node:
            out[path] = "{}"
        for key, child in sorted(node.items()):
            flatten(child, f"{path}.{key}" if path else key, out)
    elif isinstance(node, list):
        if not node:
            out[path] = "[]"
        for i, child in enumerate(node):
            flatten(child, f"{path}[{i}]", out)
    else:
        out[path] = node
    return out


def is_provenance_leaf(path: str) -> bool:
    return path.rsplit(".", 1)[-1] in PROVENANCE_LEAVES


def value_path_for(path: str) -> str:
    """Map a provenance leaf back to the object that owns it."""
    return path.rsplit(".provenance.", 1)[0] if ".provenance." in path else path


def show(v):
    if v is None:
        return "null"
    if isinstance(v, str) and len(v) > 160:
        return v[:157] + "..."
    return json.dumps(v) if isinstance(v, (bool, int, float)) else str(v)


def quote_for(flat, owner_path):
    q = flat.get(f"{owner_path}.provenance.quote")
    loc = flat.get(f"{owner_path}.provenance.location")
    if q is None and loc is None:
        return None
    return f"{show(q)}  [{show(loc)}]"


def quote_of(owner):
    """Provenance carried by the object a differing value belongs to."""
    if not isinstance(owner, dict):
        return ""
    prov = owner.get("provenance")
    if not isinstance(prov, dict):
        return ""
    q, loc = prov.get("quote"), prov.get("location")
    if q is None and loc is None:
        return ""
    return f"{show(q)}  [{show(loc)}]"


NARRATIVE_LEAVES = (
    "general_notes",
    "overlap_evidence",
    "unclear_items",
    "classification_notes",
    "derivation",
    "figure_only_data",
)


def is_narrative(path):
    """Free-text annotation, not an extracted value.

    Two models never write the same sentence, so comparing their prose measures
    nothing about whether they read the paper the same way.
    """
    leaf = re.sub(r"\[[^\]]*\]", "", path).rsplit(".", 1)[-1]
    return leaf in NARRATIVE_LEAVES


MISSING = object()


def is_blank(v):
    """True when a reviewer recorded nothing here.

    An omitted optional key and an explicit null both mean 'not reported' — the
    codebook admits no third state — so they must not count as a disagreement.
    An object whose every leaf is blank is likewise nothing recorded.
    """
    if v is MISSING or v is None:
        return True
    if isinstance(v, dict):
        return all(is_blank(x) for k, x in v.items() if k != "provenance")
    if isinstance(v, list):
        return all(is_blank(x) for x in v)
    return False


# Fields that identify which thing an array element is, rather than what was
# measured about it. Used to pair elements across reviewers.
IDENTITY_FIELDS = (
    "label",
    "arm_label",
    "arm_label_a",
    "arm_label_b",
    "domain",
    "name_verbatim",
    "name",
    "contrast_type",
    "contrast",
    "type",
    "timepoint",
    "measure_type",
    "adjusted",
    "label_verbatim",
    "role",
    "sequence_position",
)
FUZZY_THRESHOLD = 0.75


def _norm(v):
    """Reduce a value to a comparable token: lowercased alphanumerics."""
    if isinstance(v, dict):
        v = v.get("label") or v.get("value") or v.get("name") or v.get("verbatim") or ""
    if v is None:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(v).lower()).strip()


def elapsed_minutes(tp):
    """A timepoint's elapsed time in whole minutes, or None if it has none.

    Reviewers encode one moment several ways — `hours: 72` against `days: 3`,
    `fixed_day` against `minutes_hours_after_intervention`, 0.08 h against
    0.083 h — so identity has to rest on the elapsed time, not the encoding.
    """
    if not isinstance(tp, dict):
        return None
    hours, days = tp.get("hours"), tp.get("days")
    if isinstance(hours, (int, float)) and not isinstance(hours, bool):
        return round(hours * 60)
    if isinstance(days, (int, float)) and not isinstance(days, bool):
        return round(days * 24 * 60)
    if tp.get("type") == "baseline":
        return 0
    return None


def timepoint_token(tp):
    """Canonical, readable name for a timepoint: `baseline`, `5min`, `72h`, `28d`.

    Timepoints without an elapsed time (discharge, end of study, crossover
    condition) are named by their type.
    """
    m = elapsed_minutes(tp)
    if m is None:
        return _norm(tp.get("type")) if isinstance(tp, dict) else _norm(tp)
    if m == 0:
        return "baseline"
    if m < 60:
        return f"{m}min"
    if m < 7 * 24 * 60 or m % (24 * 60):
        return f"{m / 60:g}h"
    return f"{m // (24 * 60)}d"


def element_key(el):
    """Everything that says which thing an element is.

    Timepoint is part of identity: the codebook asks for one outcome entry per
    timepoint, so a baseline row and a 72-hour row of the same measurement are
    two things. Without it the two pair by order of appearance, and a reviewer
    who recorded only the 72-hour row is reported as contradicting the other's
    baseline.
    """
    if not isinstance(el, dict):
        return _norm(el)
    parts = [
        f"{k}={timepoint_token(el[k]) if k == 'timepoint' else _norm(el.get(k))}"
        for k in IDENTITY_FIELDS
        if k in el and not is_blank(el.get(k))
    ]
    return " | ".join(parts)


def hard_key(el):
    """What must agree before two elements can be the same thing at all.

    Loose similarity alone pairs a transpulmonary-power row with a PaCO2 row and
    reports the difference as a disagreement. Requiring the categorical field to
    match first confines fuzzy matching to genuine near-misses of wording.
    """
    if not isinstance(el, dict):
        return ""
    for k in ("domain", "contrast_type", "type", "role"):
        if k in el and not is_blank(el.get(k)):
            return f"{k}={_norm(el[k])}"
    return ""


def name_of(el):
    """The element's own name, which is what near-miss matching should compare."""
    if not isinstance(el, dict):
        return _norm(el)
    for k in ("name_verbatim", "name", "label_verbatim", "label", "contrast"):
        if k in el and not is_blank(el.get(k)):
            return _norm(el[k])
    return ""


def _short(key, index):
    """A readable stand-in for an array index in a reported path.

    The timepoint is appended after truncation, never cut by it: two rows that
    differ only in when they were measured must not read as the same field.
    """
    if not key:
        return str(index)
    pairs = [p.split("=", 1) for p in key.split(" | ") if "=" in p]
    tp = next((v for k, v in pairs if k == "timepoint" and v), "")
    label = " ".join(v for k, v in pairs if v and k != "timepoint")[:48].strip()
    label = f"{label} @{tp}" if tp and label else label or tp
    return label or str(index)


def timepoints_compatible(x, y):
    """False only when both elements carry an elapsed time and the times differ."""
    if not (isinstance(x, dict) and isinstance(y, dict)):
        return True
    mx, my = elapsed_minutes(x.get("timepoint")), elapsed_minutes(y.get("timepoint"))
    return mx is None or my is None or mx == my


def sizes_compatible(x, y):
    """False when both arms report how many patients they hold and the counts differ.

    grasso_2012's `Conventional ventilation` (n=14, both groups) is not its
    `ECMO — Conventional ventilation` (n=7), however alike the names; pairing
    them relabelled one group's numbers as the other's.
    """
    if not (isinstance(x, dict) and isinstance(y, dict)):
        return True
    for key in ("n_analysed", "n_randomised"):
        vx, vy = (x.get(key) or {}), (y.get(key) or {})
        if isinstance(vx, dict) and isinstance(vy, dict):
            nx, ny = vx.get("value"), vy.get("value")
            if isinstance(nx, (int, float)) and isinstance(ny, (int, float)) and nx != ny:
                return False
    return True


def match_elements(a, b):
    """Pair up two reviewers' array elements by what they are, not by position.

    Reviewers do not enumerate a paper's outcomes in the same order, and rarely
    find the same number of them. Comparing outcomes[3] against outcomes[3]
    therefore compares two unrelated measurements and reports the difference as a
    disagreement — on the first full corpus run that artefact accounted for the
    large majority of all differences reported. Elements are paired on their
    identifying fields instead: exactly first, then by closest match above a
    similarity threshold, and whatever is left over is genuinely one-sided.

    Yields (label, a_element_or_MISSING, b_element_or_MISSING).
    """
    if not all(isinstance(x, dict) for x in (*a, *b)):
        # Scalar lists: order is the only identity available.
        for i in range(max(len(a), len(b))):
            yield (
                str(i),
                a[i] if i < len(a) else MISSING,
                b[i] if i < len(b) else MISSING,
            )
        return

    keys_a = [element_key(x) for x in a]
    keys_b = [element_key(x) for x in b]
    unused_b = set(range(len(b)))
    pairs, matched_a = [], set()

    for i, ka in enumerate(keys_a):  # exact identity
        for j in sorted(unused_b):
            if ka and ka == keys_b[j]:
                pairs.append((i, j))
                matched_a.add(i)
                unused_b.discard(j)
                break

    # Nearest remaining, within the same category unless the names are identical.
    # Fuzzy matching exists for near-misses of wording, so two guards keep it
    # from inventing pairs:
    # elements measured at different elapsed times are never paired, and a name
    # the other reviewer also used verbatim may only pair with that exact name —
    # otherwise, once `EITP @baseline` has taken its twin, `EITP @1h` settles
    # for `EETP` and reports two different pressures as a disagreement.
    names_a = {name_of(x) for x in a} - {""}
    names_b = {name_of(x) for x in b} - {""}
    candidates = []
    for i, ka in enumerate(keys_a):
        if i in matched_a or not ka:
            continue
        for j in unused_b:
            na, nb = name_of(a[i]), name_of(b[j])
            if not na or not nb or not timepoints_compatible(a[i], b[j]) \
                    or not sizes_compatible(a[i], b[j]):
                continue
            # The same verbatim name classified two ways (an arm one reviewer
            # calls the intervention and the other an alternative condition) is
            # one element with a classification conflict, not two elements.
            if hard_key(a[i]) != hard_key(b[j]) and na != nb:
                continue
            if na != nb and (na in names_b or nb in names_a):
                continue
            score = SequenceMatcher(None, na, nb).ratio()
            if score > FUZZY_THRESHOLD:
                same_tp = timepoint_token(a[i].get("timepoint")) == timepoint_token(
                    b[j].get("timepoint")
                )
                candidates.append((-int(same_tp), -score, i, j))
    for _, _, i, j in sorted(candidates):  # best first, so order of listing cannot decide
        if i in matched_a or j not in unused_b:
            continue
        pairs.append((i, j))
        matched_a.add(i)
        unused_b.discard(j)

    for i, j in sorted(pairs):
        yield _short(keys_a[i], i), a[i], b[j]
    for i in range(len(a)):
        if i not in matched_a:
            yield _short(keys_a[i], i), a[i], MISSING
    for j in sorted(unused_b):
        yield _short(keys_b[j], j), MISSING, b[j]


def walk(a, b, path="", pa=None, pb=None):
    """Yield (path, kind, a_value, b_value) for each real difference.

    Two kinds, because they need different work from the adjudicator:

    `conflict`  — both reviewers recorded something and the two readings differ.
                  This is the disagreement the dual-reviewer design exists to
                  surface, and every one must be settled.
    `one_sided` — one reviewer recorded a value, the other recorded nothing.
                  Usually an omission rather than a contested reading; settled by
                  checking whether the paper reports it at all.

    A one-sided difference is emitted once, at the root of the subtree, instead of
    once per leaf beneath it. One reviewer filling a five-field measurement object
    that the other left null is one finding, not five, and counting it five times
    buries the genuine conflicts.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key == "provenance" or key.endswith("_provenance"):
                continue  # compared only through the value it supports
            yield from walk(
                a.get(key, MISSING), b.get(key, MISSING),
                f"{path}.{key}" if path else key, a, b,
            )
        return
    if isinstance(a, list) and isinstance(b, list):
        for label, ea, eb in match_elements(a, b):
            yield from walk(ea, eb, f"{path}[{label}]", pa, pb)
        return

    if is_provenance_leaf(path) or is_narrative(path):
        return
    a_blank, b_blank = is_blank(a), is_blank(b)
    if a_blank and b_blank:
        return
    if a_blank != b_blank:
        yield path, "one_sided", a, b, pa, pb
        return
    if a != b:
        yield path, "conflict", a, b, pa, pb


def _disp(v):
    if v is MISSING:
        return "<absent>"
    if isinstance(v, (dict, list)):
        return show(json.dumps(v, ensure_ascii=False))
    return show(v)


ARM_REFERENCE_KEYS = ("arm_label", "arm_label_a", "arm_label_b")


def align_arm_labels(a, b):
    """Translate b's arm-label vocabulary into a's, and report the mapping.

    `arms[].label` is free-form, so two reviewers routinely give the same arm
    different names — `conventional` against `ardsnet_low_peep`. That is one
    disagreement, but every `data[].arm_label` and `paired_effects[].arm_label_*`
    beneath it then differs too, and because arm_label is also how data rows are
    identified, the mismatch stops the rows themselves from pairing and drags the
    numbers under them into the conflict count as well.

    Arms are matched on role, sequence and verbatim label, then b's *references*
    are rewritten to a's names. `arms[].label` itself is deliberately left alone,
    so the naming disagreement is still reported once, where it belongs.
    """
    mapping = {}
    for _, ea, eb in match_elements(a.get("arms") or [], b.get("arms") or []):
        if isinstance(ea, dict) and isinstance(eb, dict):
            la, lb = ea.get("label"), eb.get("label")
            if la and lb and la != lb:
                mapping[lb] = la
    if not mapping:
        return b, mapping

    def rewrite(node):
        if isinstance(node, dict):
            return {
                k: (mapping.get(v, v) if k in ARM_REFERENCE_KEYS and isinstance(v, str)
                    else rewrite(v))
                for k, v in node.items()
            }
        if isinstance(node, list):
            return [rewrite(x) for x in node]
        return node

    aligned = rewrite(b)
    aligned["arms"] = b.get("arms")  # keep the naming disagreement visible
    return aligned, mapping


def compare(study_id, dir_a=None, dir_b=None):
    """Returns (rows, n_comparable, n_raw_leaf_disagreements)."""
    dir_a, dir_b = dir_a or GLM_DIR, dir_b or DEEPSEEK_DIR
    a_path, b_path = dir_a / f"{study_id}.json", dir_b / f"{study_id}.json"
    a, b = json.loads(a_path.read_text()), json.loads(b_path.read_text())
    b, _arm_map = align_arm_labels(a, b)
    for rec in (a, b):
        for prefix in IGNORED_PREFIXES:
            rec.pop(prefix, None)
    fa, fb = flatten(a), flatten(b)

    # Raw leaf-level count, kept so the headline agreement figure stays comparable
    # with the unmodified statistic and nothing looks quietly improved.
    raw = sum(
        1
        for p in set(fa) | set(fb)
        if not is_provenance_leaf(p) and fa.get(p, "<absent>") != fb.get(p, "<absent>")
    )

    rows = []
    for path, kind, va, vb, pa, pb in walk(a, b):
        rows.append(
            {
                "study_id": study_id,
                "field": path,
                "kind": kind,
                "glm": _disp(va),
                "deepseek": _disp(vb),
                "glm_quote": quote_of(va) or quote_of(pa),
                "deepseek_quote": quote_of(vb) or quote_of(pb),
            }
        )

    comparable = len({p for p in set(fa) | set(fb) if not is_provenance_leaf(p)})
    return rows, comparable, raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("study_id", nargs="?", help="single study to compare")
    ap.add_argument("--csv", help="write all disagreements to this CSV")
    ap.add_argument("--dir-a", type=Path, default=GLM_DIR, help="reviewer 1 directory")
    ap.add_argument("--dir-b", type=Path, default=DEEPSEEK_DIR, help="reviewer 2 directory")
    args = ap.parse_args()

    dir_a, dir_b = args.dir_a, args.dir_b

    if args.study_id:
        ids = [args.study_id]
    else:
        glm_ids = {p.stem for p in dir_a.glob("*.json")}
        deepseek_ids = {p.stem for p in dir_b.glob("*.json")}
        only_glm, only_deepseek = glm_ids - deepseek_ids, deepseek_ids - glm_ids
        for sid in sorted(only_glm):
            print(f"note: {sid} extracted by {dir_a.name} only")
        for sid in sorted(only_deepseek):
            print(f"note: {sid} extracted by {dir_b.name} only")
        ids = sorted(glm_ids & deepseek_ids)

    if not ids:
        print("No studies extracted by both reviewers yet.")
        return 0

    all_rows = []
    totals = {"conflict": 0, "one_sided": 0}
    for sid in ids:
        rows, n_fields, raw = compare(sid, dir_a, dir_b)
        all_rows += rows
        conflicts = [r for r in rows if r["kind"] == "conflict"]
        one_sided = [r for r in rows if r["kind"] == "one_sided"]
        totals["conflict"] += len(conflicts)
        totals["one_sided"] += len(one_sided)
        agreement = (n_fields - raw) / n_fields if n_fields else 1.0
        print(
            f"\n{sid}  —  {len(conflicts)} conflict(s), {len(one_sided)} one-sided, "
            f"across {n_fields} fields  (raw leaf agreement {agreement:.1%})"
        )
        # Contested readings first; they are the ones that need a judgement.
        for r in conflicts + one_sided:
            marker = "!" if r["kind"] == "conflict" else " "
            print(f"  {marker} {r['field']}")
            print(f"      glm:     {r['glm']}")
            if r["glm_quote"]:
                print(f"               {r['glm_quote']}")
            print(f"      deepseek:{r['deepseek']}")
            if r["deepseek_quote"]:
                print(f"               {r['deepseek_quote']}")

    if len(ids) > 1:
        print(
            f"\n{len(ids)} studies — {totals['conflict']} conflict(s), "
            f"{totals['one_sided']} one-sided difference(s)"
        )

    if args.csv:
        out = Path(args.csv)
        with out.open("w", newline="") as fh:
            w = csv.DictWriter(
                fh,
                fieldnames=["study_id", "field", "kind", "glm", "deepseek",
                            "glm_quote", "deepseek_quote"],
            )
            w.writeheader()
            w.writerows(all_rows)
        print(f"\nWrote {len(all_rows)} disagreement(s) to {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
