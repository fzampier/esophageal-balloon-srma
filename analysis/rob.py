#!/usr/bin/env python3
"""Risk-of-bias pass: two independent model reviewers, the tools' own algorithms,
and a comparison for the authors to adjudicate (D26).

    python analysis/rob.py run --all                 # both reviewers, every assigned study
    python analysis/rob.py run --study talmor_2008 --reviewer glm
    python analysis/rob.py run --all --dry-run       # payload sizes only
    python analysis/rob.py check                     # schema, completeness, algorithm
    python analysis/rob.py diff                      # GLM vs DeepSeek, question by question
    python analysis/rob.py worksheet [OUT.xlsx]      # adjudication workbook

The set-up mirrors data extraction: one payload per study, built once and posted
unchanged to both reviewers (the same page images and text layer as
`extract.py`, the same instruction file, the same tool text). Which tool and
which results each study gets is fixed in `risk_of_bias/assignments.csv`, not
chosen by the models.

RoB 2 domain judgements are computed here from the signalling answers with the
published algorithms, transcribed from the VBA of the developers' Excel tools
(ROB2_IRPG_beta_v9.xlsm, parallel; ROB2_crossover V2, crossover), and shown
beside the reviewer's own judgement. The NIH tools have no algorithm: their
Good/Fair/Poor rating is a judgement and stays one.

No judgement stands on a model's word alone: every answer goes to the authors
in the worksheet, agreements included.
"""

import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract  # noqa: E402  (render_pdf, post, parse_record, REVIEWERS, keys)

ROOT = Path(__file__).resolve().parent.parent
ROB = ROOT / "risk_of_bias"
SCHEMA = ROB / "schema" / "rob-schema.json"
PROMPT = ROB / "schema" / "ROB_PROMPT.md"
ASSIGN = ROB / "assignments.csv"
REVIEWER_IDS = ["glm", "deepseek"]
SCHEMA_VERSION = "1.0.0"

TOOL_TEXT = {
    "rob2_parallel": ["RoB2_cribsheet_2019-08-22.txt"],
    "rob2_crossover": ["RoB2_crossover_cribsheet_2021-03-18.txt", "RoB2_cribsheet_2019-08-22.txt"],
    "nih_cohort": ["NIH_observational_cohort.txt"],
    "nih_before_after": ["NIH_before_after.txt"],
}
TOOL_NAME = {
    "rob2_parallel": "Cochrane RoB 2 for individually randomized parallel-group trials (22 August 2019)",
    "rob2_crossover": "Cochrane RoB 2 for randomized crossover trials (18 March 2021); for the questions it "
                      "says are 'as for parallel group trials', the parallel-group elaboration follows it",
    "nih_cohort": "NIH (NHLBI) Quality Assessment Tool for Observational Cohort and Cross-Sectional Studies",
    "nih_before_after": "NIH (NHLBI) Quality Assessment Tool for Before-After (Pre-Post) Studies With No Control Group",
}

Q_PARALLEL = ["1.1", "1.2", "1.3", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7",
              "3.1", "3.2", "3.3", "3.4", "4.1", "4.2", "4.3", "4.4", "4.5", "5.1", "5.2", "5.3"]
Q_CROSSOVER = Q_PARALLEL[:3] + ["S.1", "S.2", "S.3"] + Q_PARALLEL[3:] + ["5.4"]
NIH_ITEMS = {"nih_cohort": 14, "nih_before_after": 12}
DOMAINS = {"rob2_parallel": ["D1", "D2", "D3", "D4", "D5"],
           "rob2_crossover": ["D1", "DS", "D2", "D3", "D4", "D5"]}


# ------------------------------------------------------------------- assignments


def read_assignments():
    """study_id -> {"tool": ..., "results": [row, ...]}"""
    out = {}
    with ASSIGN.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            a = out.setdefault(r["study_id"], {"tool": r["tool"], "results": []})
            if a["tool"] != r["tool"]:
                sys.exit(f"{r['study_id']}: two tools in assignments.csv")
            a["results"].append(r)
    return out


# ---------------------------------------------------------------------- payload


def instruction_block(tool):
    _, _, model_prompt = PROMPT.read_text(encoding="utf-8").partition("\n---\n")
    parts = [model_prompt.strip()]
    for f in TOOL_TEXT[tool]:
        parts.append(f"# Tool text: {f}\n\n" + (ROB / "tools" / f).read_text(encoding="utf-8").strip())
    parts.append("# JSON Schema (rob-schema.json)\n\n```json\n" + SCHEMA.read_text(encoding="utf-8").strip() + "\n```")
    return "\n\n".join(parts)


def assignment_block(study_id, a):
    lines = [f"# Your assignment\n\nstudy_id: {study_id}\ntool: {a['tool']} — {TOOL_NAME[a['tool']]}"]
    if a["tool"].startswith("rob2"):
        lines.append("\nResults to assess (one element of `results` each, in this order):")
        for r in a["results"]:
            lines.append(f"- result_id `{r['result_id']}` — {r['outcome']}: {r['result_as_analysed']}")
        lines.append(
            "\nThe comparison is the esophageal-pressure-guided strategy (experimental) against the "
            "comparator strategy as the report defines it.")
    else:
        lines.append(f"\nAnswer items 1 to {NIH_ITEMS[a['tool']]} for the study as a whole.")
    return "\n".join(lines)


def build_payload(study_id, a, pdf_rel, dpi):
    text, images = extract.render_pdf(ROOT / pdf_rel, dpi)
    content = [
        {"type": "text", "text": instruction_block(a["tool"])},
        {"type": "text", "text": assignment_block(study_id, a)},
        {"type": "text", "text": (
            f"# The study report\n\nstudy_id: {study_id}\nsource_file: {pdf_rel}\n\n"
            "The report follows twice: first its extracted text layer, then one rendered image per "
            "page. The text is authoritative for wording; the images are authoritative for table and "
            "figure layout. Where the text layer has interleaved two print columns, trust the image.\n\n"
            f"## Text layer\n\n{text}")},
        {"type": "text", "text": "## Rendered pages"},
    ]
    for b64 in images:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    return content, len(images)


def load_keys():
    """extract.py reads ~/Desktop/api.md; on a OneDrive-redirected Desktop it is elsewhere."""
    for cand in [Path.home() / "Desktop" / "api.md", Path.home() / "OneDrive" / "Desktop" / "api.md"]:
        if cand.exists():
            extract.KEYFILE = cand
            return extract.load_keys()
    sys.exit("api.md not found on the Desktop")


# ------------------------------------------------------------------------- run


def validator():
    return Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))


def assess_one(reviewer, study_id, a, pdf_rel, content, keys, max_tokens):
    cfg = extract.REVIEWERS[reviewer]
    body = {"model": cfg["model"], "messages": [{"role": "user", "content": content}],
            "max_tokens": min(max_tokens, cfg["max_tokens"]), "temperature": 0, **cfg["extra"]}
    resp = extract.post(cfg["url"], extract.api_key(cfg, keys), body, timeout=cfg.get("timeout", 1800))
    choice = resp["choices"][0]
    text = choice["message"].get("content") or ""
    if choice.get("finish_reason") == "length":
        raise RuntimeError("response truncated at max_tokens")
    if not text.strip():
        raise RuntimeError(f"empty content (finish_reason={choice.get('finish_reason')})")
    raw = ROB / "raw" / reviewer / f"{study_id}.txt"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(text, encoding="utf-8")
    try:
        rec = extract.parse_record(text)
    except ValueError:
        # Broken JSON syntax (an unescaped quote inside a support quote, usually).
        # Same repair for either reviewer: send the text back, ask for it re-emitted
        # as valid JSON with nothing changed. The broken original stays in raw/.
        fix = {"model": cfg["model"], "max_tokens": min(max_tokens, cfg["max_tokens"]), "temperature": 0,
               "messages": [{"role": "user", "content": (
                   "The text below was meant to be one JSON object but is not valid JSON. Return the "
                   "same object as valid JSON: fix syntax only (escape inner quotes, add missing commas "
                   "or brackets); do not change, add or drop any value. Output only the JSON.\n\n" + text)}],
               **cfg["extra"]}
        text = extract.post(cfg["url"], extract.api_key(cfg, keys), fix,
                            timeout=cfg.get("timeout", 1800))["choices"][0]["message"].get("content") or ""
        raw.with_suffix(".repaired.txt").write_text(text, encoding="utf-8")
        rec = extract.parse_record(text)
    rec["record_meta"] = {"study_id": study_id, "reviewer": reviewer, "model": cfg["model"],
                          "assessment_date": date.today().isoformat(), "source_file": pdf_rel,
                          "schema_version": SCHEMA_VERSION}
    out = ROB / reviewer / f"{study_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return resp.get("usage", {}), out, problems(rec, a)


def cmd_run(args):
    assign = read_assignments()
    manifest = extract.read_manifest()
    ids = sorted(assign) if args.all else args.study
    if not ids:
        sys.exit("give --all or --study")
    bad = [s for s in ids if s not in assign]
    if bad:
        sys.exit(f"not in assignments.csv: {', '.join(bad)}")
    # "local" is the pair run on this machine for the reports without an open licence (D78).
    reviewers = extract.PAIRS.get(args.reviewer, [args.reviewer])
    keys = load_keys() if not args.dry_run and any(extract.REVIEWERS[r]["key"] for r in reviewers) else {}
    workers = min([args.workers] + [extract.REVIEWERS[r].get("workers", args.workers) for r in reviewers])

    def run(study_id):
        pdf_rel = f"studies/fulltext/{manifest[study_id]}"
        todo = [r for r in reviewers if args.force or not (ROB / r / f"{study_id}.json").exists()]
        if not todo:
            return study_id, ["skip (already assessed)"]
        content, n_pages = build_payload(study_id, assign[study_id], pdf_rel, args.dpi)
        if args.dry_run:
            return study_id, [f"{assign[study_id]['tool']}: {n_pages} pages, "
                              f"payload {len(json.dumps(content)) / 1e6:.1f} MB"]
        lines = []
        for reviewer in todo:
            t0 = time.time()
            try:
                usage, out, probs = assess_one(reviewer, study_id, assign[study_id], pdf_rel,
                                               content, keys, args.max_tokens)
                status = "ok  " if not probs else f"PROBLEMS({len(probs)})"
                lines.append(f"{reviewer:9s} {status} {time.time() - t0:5.0f}s  "
                             f"in={usage.get('prompt_tokens', '?')} out={usage.get('completion_tokens', '?')}")
                lines += [f"          ! {p}" for p in probs[:4]]
            except Exception as exc:  # noqa: BLE001
                lines.append(f"{reviewer:9s} FAIL {time.time() - t0:5.0f}s  {exc}")
        return study_id, lines

    fails = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(run, s) for s in ids]):
            sid, lines = fut.result()
            print(f"\n{sid}")
            for ln in lines:
                print(f"  {ln}")
                fails += ("FAIL" in ln) or ("PROBLEMS" in ln)
            sys.stdout.flush()
    print(f"\n{len(ids)} studies, {fails} with failures or problems")
    return 1 if fails else 0


# ------------------------------------------------------------------- algorithms
#
# Transcribed from the developers' Excel tools (VBA SugButton*_Click). A missing or
# out-of-branch answer returns None ("algorithm cannot be applied"), which the
# worksheet shows rather than guessing.

Y, N_, NI = {"Y", "PY"}, {"N", "PN"}, {"NI"}


def d1(a):
    q1, q2, q3 = a.get("1.1"), a.get("1.2"), a.get("1.3")
    if q2 in Y:
        if q1 in Y | NI:
            return "Low" if q3 in N_ | NI else "Some concerns" if q3 in Y else None
        if q1 in N_:
            return "Some concerns" if q3 in Y | N_ | NI else None
        return None
    if q2 in NI:
        return "Some concerns" if q3 in N_ | NI else "High" if q3 in Y else None
    if q2 in N_:
        return "High"
    return None


def ds(a):
    s1, s2, s3 = a.get("S.1"), a.get("S.2"), a.get("S.3")
    if s3 in Y:
        if s1 in Y:
            return "Low"
        if s1 in N_ | NI:
            return "Low" if s2 in Y else "Some concerns" if s2 in N_ | NI else None
        return None
    if s3 in NI:
        return "Some concerns"
    if s3 in N_:
        return "High"
    return None


def d2(a):
    q = {k: a.get(f"2.{k}") for k in range(1, 8)}
    if q[1] in N_ and q[2] in N_:
        p1 = "Low"
    elif q[1] in Y | NI or q[2] in Y | NI:
        if q[3] in N_:
            p1 = "Low"
        elif q[3] in NI:
            p1 = "Some concerns"
        elif q[3] in Y:
            if q[4] in N_:
                p1 = "Some concerns"
            elif q[4] in Y | NI:
                p1 = "Some concerns" if q[5] in Y else "High" if q[5] in N_ | NI else None
            else:
                p1 = None
        else:
            p1 = None
    else:
        p1 = None
    if q[6] in Y:
        p2 = "Low"
    elif q[6] in N_ | NI:
        p2 = "Some concerns" if q[7] in N_ else "High" if q[7] in Y | NI else None
    else:
        p2 = None
    if p1 is None or p2 is None:
        return None
    if p1 == p2 == "Low":
        return "Low"
    return "High" if "High" in (p1, p2) else "Some concerns"


def d3(a):
    q1, q2, q3, q4 = (a.get(f"3.{k}") for k in range(1, 5))
    if q1 in Y:
        return "Low"
    if q1 in N_ | NI:
        if q2 in Y:
            return "Low"
        if q2 in N_ | NI:
            if q3 in N_:
                return "Low"
            if q3 in Y | NI:
                return "Some concerns" if q4 in N_ else "High" if q4 in Y | NI else None
    return None


def d4(a):
    q1, q2, q3, q4, q5 = (a.get(f"4.{k}") for k in range(1, 6))
    if q1 in Y or q2 in Y:
        return "High"
    if q1 not in N_ | NI:
        return None
    # 4.2 N/PN and 4.2 NI walk the same 4.3-4.5 branch; only the benign ending differs.
    if q2 in N_:
        base = "Low"
    elif q2 in NI:
        base = "Some concerns"
    else:
        return None
    if q3 in N_:
        return base
    if q3 in Y | NI:
        if q4 in N_:
            return base
        if q4 in Y | NI:
            return "Some concerns" if q5 in N_ else "High" if q5 in Y | NI else None
    return None


def d5(a, crossover=False):
    keys = ["5.2", "5.3"] + (["5.4"] if crossover else [])
    vals = [a.get(k) for k in keys]
    q51 = a.get("5.1")
    if any(v is None for v in vals) or q51 is None:
        return None
    if all(v in N_ for v in vals):
        return "Low" if q51 in Y else "Some concerns" if q51 in N_ | NI else None
    if any(v in Y for v in vals):
        return "High"
    return "Some concerns"


def algorithm(tool, answers):
    """Domain -> suggested judgement (or None), plus the overall."""
    resp = {k: (v or {}).get("response") for k, v in answers.items()}
    out = {"D1": d1(resp), "D2": d2(resp), "D3": d3(resp), "D4": d4(resp),
           "D5": d5(resp, crossover=(tool == "rob2_crossover"))}
    if tool == "rob2_crossover":
        out["DS"] = ds(resp)
    vals = [out[d] for d in DOMAINS[tool]]
    if None in vals:
        out["overall"] = None
    elif "High" in vals:
        out["overall"] = "High"
    elif all(v == "Low" for v in vals):
        out["overall"] = "Low"
    else:
        out["overall"] = "Some concerns"
    return out


# ----------------------------------------------------------------------- checks


def problems(rec, a):
    """Schema errors, missing questions, wrong result list; [] when clean."""
    out = [f"schema: {'.'.join(map(str, e.absolute_path)) or '(root)'}: {e.message[:120]}"
           for e in validator().iter_errors(rec)]
    if rec.get("tool") != a["tool"]:
        out.append(f"tool {rec.get('tool')!r}, assigned {a['tool']!r}")
    if a["tool"].startswith("rob2"):
        want = [r["result_id"] for r in a["results"]]
        got = [r.get("result_id") for r in rec.get("results") or []]
        if got != want:
            out.append(f"results {got}, assigned {want}")
        qs = Q_CROSSOVER if a["tool"] == "rob2_crossover" else Q_PARALLEL
        for r in rec.get("results") or []:
            miss = [q for q in qs if q not in (r.get("answers") or {})]
            if miss:
                out.append(f"{r.get('result_id')}: unanswered {', '.join(miss)}")
            alg = algorithm(a["tool"], r.get("answers") or {})
            gaps = [d for d in DOMAINS[a["tool"]] if alg[d] is None]
            if gaps:
                out.append(f"{r.get('result_id')}: algorithm not applicable in {', '.join(gaps)} "
                           f"(an answer outside the branch, e.g. NA where the question applies)")
    else:
        items = (rec.get("nih") or {}).get("items") or {}
        miss = [str(i) for i in range(1, NIH_ITEMS[a["tool"]] + 1) if str(i) not in items]
        if miss:
            out.append(f"unanswered items {', '.join(miss)}")
    return out


def load(reviewer, study_id):
    p = ROB / reviewer / f"{study_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def cmd_check(args):
    assign = read_assignments()
    n_bad = 0
    for sid in sorted(assign):
        for rv in REVIEWER_IDS:
            rec = load(rv, sid)
            if rec is None:
                print(f"{sid:20s} {rv:9s} missing")
                n_bad += 1
                continue
            probs = problems(rec, assign[sid])
            print(f"{sid:20s} {rv:9s} {'ok' if not probs else f'{len(probs)} problem(s)'}")
            for p in probs:
                print(f"    ! {p}")
            n_bad += bool(probs)
    print(f"\n{n_bad} record(s) missing or with problems")
    return 1 if n_bad else 0


# ----------------------------------------------------------------------- complete


def missing_questions(rec, a):
    """{result_id: [question keys unanswered or NA where the algorithm needs them]}"""
    out = {}
    if not a["tool"].startswith("rob2"):
        items = (rec.get("nih") or {}).get("items") or {}
        miss = [str(i) for i in range(1, NIH_ITEMS[a["tool"]] + 1) if str(i) not in items]
        return {"study": miss} if miss else {}
    qs = Q_CROSSOVER if a["tool"] == "rob2_crossover" else Q_PARALLEL
    for r in rec.get("results") or []:
        miss = [q for q in qs if q not in (r.get("answers") or {})]
        if miss:
            out[r["result_id"]] = miss
    return out


def cmd_complete(args):
    """Ask a reviewer to answer only the questions it left out.

    The rule that triggers it (a required question absent from the record) is the
    same for both reviewers, the request is the same, and the paper goes with it,
    so the procedure cannot favour either. Answers already given are never sent
    back for revision: NA where the algorithm needed an answer stays as the
    reviewer gave it and goes to the authors.
    """
    assign = read_assignments()
    manifest = extract.read_manifest()
    keys = load_keys()
    for sid in sorted(assign):
        a = assign[sid]
        for rv in REVIEWER_IDS:
            rec = load(rv, sid)
            if rec is None:
                continue
            miss = missing_questions(rec, a)
            if not miss:
                continue
            pdf_rel = f"studies/fulltext/{manifest[sid]}"
            content, _ = build_payload(sid, a, pdf_rel, args.dpi)
            ask = ("Your assessment above left these questions unanswered. Answer only them, "
                   "under the same rules, as one JSON object of the form "
                   '{"<result_id>": {"<question key>": {"response": ..., "support": ..., "location": ...}}}'
                   " (for an NIH tool use result_id \"study\" and the item numbers). Missing: "
                   + json.dumps(miss) + ". Output only the JSON.")
            content = content + [{"type": "text", "text": ask}]
            cfg = extract.REVIEWERS[rv]
            body = {"model": cfg["model"], "messages": [{"role": "user", "content": content}],
                    "max_tokens": 32000, "temperature": 0, **cfg["extra"]}
            try:
                patch = extract.parse_record(
                    extract.post(cfg["url"], keys[cfg["key"]], body)["choices"][0]["message"].get("content") or "")
            except Exception as exc:  # noqa: BLE001
                print(f"{sid} {rv}: completion failed: {exc}")
                continue
            filled = []
            for rid, qs in miss.items():
                got = patch.get(rid) or {}
                for q in qs:
                    if q in got:
                        if rid == "study":
                            rec["nih"]["items"][q] = got[q]
                        else:
                            el = next(r for r in rec["results"] if r["result_id"] == rid)
                            el["answers"][q] = got[q]
                        filled.append(f"{rid}:{q}")
            rec["record_meta"]["completed_questions"] = filled
            (ROB / rv / f"{sid}.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n",
                                                  encoding="utf-8")
            print(f"{sid} {rv}: filled {len(filled)} of {sum(len(v) for v in miss.values())}: {', '.join(filled)}")
    return 0


# ------------------------------------------------------------------------- diff


def rows():
    """One row per question (RoB 2) or item (NIH), both reviewers side by side."""
    assign = read_assignments()
    q_rows, j_rows = [], []
    for sid in sorted(assign):
        a = assign[sid]
        recs = {rv: load(rv, sid) for rv in REVIEWER_IDS}
        if a["tool"].startswith("rob2"):
            qs = Q_CROSSOVER if a["tool"] == "rob2_crossover" else Q_PARALLEL
            for res in a["results"]:
                rid = res["result_id"]
                per = {}
                for rv, rec in recs.items():
                    el = next((r for r in (rec or {}).get("results") or [] if r.get("result_id") == rid), {})
                    per[rv] = el
                for q in qs:
                    ans = {rv: (per[rv].get("answers") or {}).get(q) or {} for rv in REVIEWER_IDS}
                    q_rows.append({
                        "study_id": sid, "tool": a["tool"], "result_id": rid, "item": q,
                        **{f"{rv}_response": ans[rv].get("response") for rv in REVIEWER_IDS},
                        **{f"{rv}_support": ans[rv].get("support") for rv in REVIEWER_IDS},
                        **{f"{rv}_location": ans[rv].get("location") for rv in REVIEWER_IDS},
                    })
                algs = {rv: algorithm(a["tool"], per[rv].get("answers") or {}) for rv in REVIEWER_IDS}
                for d in DOMAINS[a["tool"]] + ["overall"]:
                    own = {rv: ((per[rv].get("domains") or {}).get(d) if d != "overall" else per[rv].get("overall")) or {}
                           for rv in REVIEWER_IDS}
                    j_rows.append({
                        "study_id": sid, "tool": a["tool"], "result_id": rid, "domain": d,
                        **{f"{rv}_algorithm": algs[rv][d] for rv in REVIEWER_IDS},
                        **{f"{rv}_judgement": own[rv].get("judgement") for rv in REVIEWER_IDS},
                        **{f"{rv}_rationale": own[rv].get("rationale") for rv in REVIEWER_IDS},
                    })
        else:
            for i in range(1, NIH_ITEMS[a["tool"]] + 1):
                ans = {rv: (((recs[rv] or {}).get("nih") or {}).get("items") or {}).get(str(i)) or {}
                       for rv in REVIEWER_IDS}
                q_rows.append({
                    "study_id": sid, "tool": a["tool"], "result_id": "study", "item": str(i),
                    **{f"{rv}_response": ans[rv].get("response") for rv in REVIEWER_IDS},
                    **{f"{rv}_support": ans[rv].get("support") for rv in REVIEWER_IDS},
                    **{f"{rv}_location": ans[rv].get("location") for rv in REVIEWER_IDS},
                })
            nih = {rv: (recs[rv] or {}).get("nih") or {} for rv in REVIEWER_IDS}
            j_rows.append({
                "study_id": sid, "tool": a["tool"], "result_id": "study", "domain": "rating",
                **{f"{rv}_algorithm": None for rv in REVIEWER_IDS},
                **{f"{rv}_judgement": nih[rv].get("rating") for rv in REVIEWER_IDS},
                **{f"{rv}_rationale": nih[rv].get("rationale") for rv in REVIEWER_IDS},
            })
    return q_rows, j_rows


def coarse(v):
    """RoB 2 answers collapse Y/PY and N/PN for 'direction' agreement."""
    return {"PY": "Y", "PN": "N"}.get(v, v)


def cmd_diff(args):
    q_rows, j_rows = rows()
    exact = Counter(r["glm_response"] == r["deepseek_response"] for r in q_rows)
    direc = Counter(coarse(r["glm_response"]) == coarse(r["deepseek_response"]) for r in q_rows)
    print(f"questions/items: {len(q_rows)}; exact agreement {exact[True]}/{len(q_rows)}; "
          f"same direction (Y=PY, N=PN) {direc[True]}/{len(q_rows)}")
    jd = [r for r in j_rows]
    alg_agree = sum(r["glm_algorithm"] == r["deepseek_algorithm"] for r in jd if r["domain"] != "rating")
    n_alg = sum(r["domain"] != "rating" for r in jd)
    own_agree = sum(r["glm_judgement"] == r["deepseek_judgement"] for r in jd)
    print(f"RoB 2 domain judgements by algorithm: {alg_agree}/{n_alg} agree; "
          f"reviewers' own judgements (incl. NIH ratings): {own_agree}/{len(jd)} agree")
    by = defaultdict(list)
    for r in jd:
        if r["glm_judgement"] != r["deepseek_judgement"] or r["glm_algorithm"] != r["deepseek_algorithm"]:
            by[(r["study_id"], r["result_id"])].append(
                f"{r['domain']}: glm {r['glm_judgement']} [alg {r['glm_algorithm']}] vs "
                f"deepseek {r['deepseek_judgement']} [alg {r['deepseek_algorithm']}]")
    for k, v in sorted(by.items()):
        print(f"\n{k[0]} / {k[1]}")
        for x in v:
            print(f"  {x}")
    return 0


# -------------------------------------------------------------------- worksheet


def cmd_worksheet(args):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    q_rows, j_rows = rows()
    # Claude's proposals for split domains (checked on the page), shown, never pre-filled as rulings.
    recs = {}
    for f in sorted((ROB / "adjudication").glob("recommendations_*.csv")):
        for r in csv.DictReader(f.open(encoding="utf-8")):
            recs[(r["study_id"], r["result_id"], r["domain"])] = f"{r['recommendation']} — {r['basis']}"
    for r in j_rows:
        r["recommendation"] = recs.get((r["study_id"], r["result_id"], r["domain"]))
    out = Path(args.out) if args.out else ROB / "adjudication" / f"rob_adjudication_{date.today().isoformat()}.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    for line in [
        "Risk of bias: two model reviewers (GLM, DeepSeek), same instructions and same paper (D26).",
        "Sheet 'judgements': one row per result and domain (RoB 2) or per study (NIH rating).",
        "  *_algorithm = the tool's published algorithm applied to that reviewer's answers; *_judgement = the reviewer's own.",
        "Sheet 'questions': every signalling question / NIH item, with each reviewer's quote and page.",
        "Yellow = the reviewers differ. Every row, agreed or not, needs an author's ruling: no judgement stands on a model alone.",
        "Fill 'ruling' (for RoB 2 domains: Low / Some concerns / High; NIH: Good / Fair / Poor) and 'ruling_note'.",
    ]:
        ws.append([line])
    yellow = PatternFill("solid", fgColor="FFF2CC")
    bold = Font(bold=True)

    def sheet(name, data, keycols):
        s = wb.create_sheet(name)
        cols = list(data[0].keys()) if data else []
        cols += [c for c in ["recommendation", "ruling", "ruling_note"] if c not in cols]
        s.append(cols)
        for c in s[1]:
            c.font = bold
        for r in data:
            s.append([r.get(c) for c in cols])
            differ = any(r.get(f"glm_{k}") != r.get(f"deepseek_{k}") for k in keycols)
            if differ:
                for c in s[s.max_row]:
                    c.fill = yellow
        for col in s.columns:
            s.column_dimensions[col[0].column_letter].width = 14
        for c in s[1]:
            if "support" in str(c.value) or "rationale" in str(c.value):
                s.column_dimensions[c.column_letter].width = 60
        for row in s.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        s.freeze_panes = "E2"

    sheet("judgements", j_rows, ["algorithm", "judgement"])
    sheet("questions", q_rows, ["response"])
    wb.save(out)
    print(f"wrote {out.relative_to(ROOT)} ({len(j_rows)} judgement rows, {len(q_rows)} question rows)")
    return 0


# ------------------------------------------------------------------------ final

# The PI's rulings: which proposal files he accepted, and whether agreed judgements
# were confirmed in bulk. A file listed here is settled; any other proposal file
# under adjudication/ is shown as proposed until it is added.
SETTLED = {
    "recommendations_2026-09-28.csv": 'Fernando ("2 accept all", 28 September 2026)',
    "proposals_2026-09-28b.csv": 'Fernando ("1 yes, 2 yes": D38 and the three RoB 2 corrections, 28 September 2026)',
    "proposals_2026-09-29.csv": 'Fernando ("Yes", 29 September 2026)',
    "proposals_2026-09-29b.csv": 'Fernando ("all yes. reviewed", audit of 29 September 2026)',
}
BULK_AGREED = 'Fernando (agreed RoB 2 judgements confirmed in bulk, "3 go ahead", 28 September 2026)'
# Studies from the search update (D42) were assessed after that bulk confirmation;
# their agreed judgements stay proposed until the PI confirms them (None = not yet).
UPDATE_STUDIES = {"crognier_2025", "maia_2026", "tang_2026", "weller_2026"}
BULK_AGREED_UPDATE = 'Fernando (agreed judgements of the four update reports confirmed, "Yes", 29 September 2026)'


def cmd_final(args):
    """Merge rulings into risk_of_bias/judgements.csv and draw the summary figure."""
    _, j_rows = rows()
    props = {}
    for f in sorted((ROB / "adjudication").glob("*.csv")):
        if not f.name.startswith(("recommendations_", "proposals_")):
            continue
        for r in csv.DictReader(f.open(encoding="utf-8")):
            props[(r["study_id"], r["result_id"], r["domain"])] = (
                r["recommendation"], r["basis"], SETTLED.get(f.name), f.name)
    out_rows = []
    for r in j_rows:
        key = (r["study_id"], r["result_id"], r["domain"])
        if key in props:
            val, basis, by, src = props[key]
            status = "settled" if by else "proposed"
            by = by or "Claude; awaiting Fernando"
        elif (r["glm_judgement"] == r["deepseek_judgement"] and r["domain"] != "rating"
              # both reviewers' answers must also give that judgement by the algorithm:
              # a reviewer can state a judgement its own answers do not support
              and all(r[f"{rv}_algorithm"] in (None, r["glm_judgement"]) for rv in REVIEWER_IDS)):
            by = BULK_AGREED_UPDATE if r["study_id"] in UPDATE_STUDIES else BULK_AGREED
            val, basis, src = r["glm_judgement"], "both reviewers agree", ""
            status, by = ("settled", by) if by else ("proposed", "both reviewers agree; awaiting Fernando")
        else:
            val, basis, src, status, by = None, "open: reviewers differ and no ruling", "", "open", ""
        out_rows.append({"study_id": r["study_id"], "tool": r["tool"], "result_id": r["result_id"],
                         "domain": r["domain"], "judgement": val, "status": status, "by": by,
                         "basis": basis, "source": src,
                         "glm": r["glm_judgement"], "deepseek": r["deepseek_judgement"]})
    out = ROB / "judgements.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0]))
        w.writeheader()
        w.writerows(out_rows)
    c = Counter(r["status"] for r in out_rows)
    print(f"wrote {out.relative_to(ROOT)}: {dict(c)}")
    figure(out_rows)
    return 0


def figure(out_rows):
    """Traffic-light summary: RoB 2 results by domain; NIH ratings beside."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colour = {"Low": "#2e7d32", "Some concerns": "#f9a825", "High": "#c62828",
              "Good": "#2e7d32", "Fair": "#f9a825", "Poor": "#c62828", None: "#bdbdbd"}
    mark = {"Low": "+", "Some concerns": "–", "High": "×", "Good": "+", "Fair": "–", "Poor": "×", None: "?"}
    rob2 = defaultdict(dict)
    for r in out_rows:
        if r["tool"].startswith("rob2"):
            rob2[(r["study_id"], r["result_id"])][r["domain"]] = (r["judgement"], r["status"])
    cols = ["D1", "DS", "D2", "D3", "D4", "D5", "overall"]
    keys = sorted(rob2, key=lambda k: (k[1] != "mortality", k[1], k[0]))
    fig, ax = plt.subplots(figsize=(7.5, 0.34 * len(keys) + 1.2))
    for i, k in enumerate(keys):
        for jx, d in enumerate(cols):
            if d not in rob2[k]:
                continue
            val, st = rob2[k][d]
            ax.scatter(jx, -i, s=260, color=colour[val], edgecolors="black" if st == "proposed" else "none",
                       linewidths=1.5)
            ax.text(jx, -i, mark[val], ha="center", va="center", color="white", fontsize=9, weight="bold")
    ax.set_yticks([-i for i in range(len(keys))], [f"{s.split('_')[0].title()} {s.split('_')[-1]} — {r}" for s, r in keys])
    ax.set_xticks(range(len(cols)), ["D1", "S", "D2", "D3", "D4", "D5", "Overall"])
    ax.set_xlim(-0.6, len(cols) - 0.4)
    ax.tick_params(length=0)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_title("RoB 2 by result (outlined = proposed, awaiting ruling)", fontsize=9)
    fig.tight_layout()
    p = ROB / "rob2_summary.png"
    fig.savefig(p, dpi=200)
    print(f"wrote {p.relative_to(ROOT)}")


# ------------------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--all", action="store_true")
    r.add_argument("--study", action="append", default=[])
    r.add_argument("--reviewer", choices=[*extract.REVIEWERS, *extract.PAIRS], default="both")
    r.add_argument("--dpi", type=int, default=120)
    r.add_argument("--max-tokens", type=int, default=100000)
    r.add_argument("--workers", type=int, default=3)
    r.add_argument("--force", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    sub.add_parser("check")
    c = sub.add_parser("complete")
    c.add_argument("--dpi", type=int, default=120)
    sub.add_parser("diff")
    sub.add_parser("final")
    w = sub.add_parser("worksheet")
    w.add_argument("out", nargs="?")
    args = ap.parse_args()
    return {"run": cmd_run, "check": cmd_check, "complete": cmd_complete, "diff": cmd_diff,
            "worksheet": cmd_worksheet, "final": cmd_final}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
