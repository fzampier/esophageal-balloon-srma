#!/usr/bin/env python3
"""Run both LLM reviewers over the study corpus.

    python3 analysis/extract.py --all
    python3 analysis/extract.py --study talmor_2008 --reviewer glm
    python3 analysis/extract.py --all --dry-run     # payload sizes, no API calls

Both reviewers receive byte-identical instructions and byte-identical input. The
payload for a study is built exactly once and the same object is posted to each
model, because a difference in what the reviewers were shown is indistinguishable
afterwards from a difference in how they read it. See CLAUDE.md.

The paper is delivered two ways at once: the column-sorted text layer, which is
faithful for prose and for numbers but loses table geometry, and rendered page
images, which preserve the geometry. Neither alone is sufficient — pdftotext-style
column bleed binds numbers to the wrong row label, and image-only reading
fabricates digits.

record_meta.extractor, .model, .extraction_date, .source_file and .schema_version
are stamped by this runner rather than trusted from the model. They are facts
about the run, not readings of the paper; a model cannot reliably report its own
identity. Everything else is the model's own output, unaltered.

API keys are read from ~/Desktop/api.md and never written to disk or logs.
"""

import argparse
import base64
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import fitz  # PyMuPDF
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "extraction" / "schema"
MANIFEST = ROOT / "studies" / "manifest.csv"
KEYFILE = Path.home() / "Desktop" / "api.md"

SCHEMA_VERSION = "1.2.0"

REVIEWERS = {
    "glm": {
        "model": "glm-5.3-flash",
        "url": "https://api.z.ai/api/paas/v4/chat/completions",
        "key": "z.ai",
        "extra": {"reasoning_effort": "low"},
        "max_tokens": 131072,
    },
    "deepseek": {
        "model": "deepseek-v4-flash-vision-exp",
        "url": "https://api.deepseek.com/chat/completions",
        "key": "deepseek",
        "extra": {"response_format": {"type": "json_object"}},
        "max_tokens": 131072,
    },
    # Local reviewers (D78): llama-server on this machine, one model loaded at a
    # time, so reports without an open licence never leave it. Launch lines are in
    # extraction/<reviewer>/RUN.md. A local call takes many minutes, hence the long
    # timeout and one study at a time (llama-server runs with -np 1). A local model
    # can end its turn before the record is complete; see continue_reply().
    "qwen27b": {
        "model": "Qwen3.8-27B-UD-Q4_K_XL",
        "url": "http://127.0.0.1:8081/v1/chat/completions",
        "key": None,
        "extra": {"chat_template_kwargs": {"enable_thinking": False}},
        "max_tokens": 131072,
        "timeout": 10800,
        "workers": 1,
    },
    "gemma26b": {
        "model": "gemma-4-26B-A4B-it-qat-UD-Q4_K_XL",
        "url": "http://127.0.0.1:8082/v1/chat/completions",
        "key": None,
        "extra": {"chat_template_kwargs": {"enable_thinking": False}},
        "max_tokens": 131072,
        "timeout": 10800,
        "workers": 1,
    },
}

PAIRS = {"both": ["glm", "deepseek"], "local": ["qwen27b", "gemma26b"]}


def api_key(cfg, keys):
    """The bearer token for a reviewer; local servers take any."""
    return keys[cfg["key"]] if cfg["key"] else "local"


# --------------------------------------------------------------------------- keys


def load_keys():
    """Parse ~/Desktop/api.md into {provider: key}. Values are never logged."""
    if not KEYFILE.exists():
        sys.exit(f"key file not found: {KEYFILE}")
    blocks = [b.strip() for b in re.split(r"\n\s*\n", KEYFILE.read_text()) if b.strip()]
    keys, i = {}, 0
    while i < len(blocks) - 1:
        name, val = blocks[i].strip(), blocks[i + 1].strip()
        if len(val) > 20 and " " not in val:
            keys[name.lower()] = val
            i += 2
        else:
            i += 1
    return keys


# ------------------------------------------------------------------------ payload


def read_manifest():
    """study_id -> pdf filename, from studies/manifest.csv."""
    with MANIFEST.open() as fh:
        return {r["study_id"]: r["filename"] for r in csv.DictReader(fh)}


def render_pdf(pdf_path, dpi):
    """Return (sorted text layer, [base64 png per page])."""
    doc = fitz.open(pdf_path)
    text_pages, images = [], []
    for i in range(doc.page_count):
        page = doc[i]
        text_pages.append(f"--- page {i + 1} ---\n{page.get_text('text', sort=True)}")
        images.append(base64.b64encode(page.get_pixmap(dpi=dpi).tobytes("png")).decode())
    doc.close()
    return "\n\n".join(text_pages), images


def instruction_block():
    """The prompt given to the model, plus the three reference documents.

    EXTRACTION_PROMPT.md opens with a note addressed to the operator; everything
    after the first horizontal rule is what the model is meant to receive.
    """
    prompt = (SCHEMA_DIR / "EXTRACTION_PROMPT.md").read_text()
    _, _, model_prompt = prompt.partition("\n---\n")
    return "\n\n".join(
        [
            model_prompt.strip(),
            "# JSON Schema (extraction-schema.json)\n\n```json\n"
            + (SCHEMA_DIR / "extraction-schema.json").read_text().strip()
            + "\n```",
            "# Codebook (CODEBOOK.md)\n\n" + (SCHEMA_DIR / "CODEBOOK.md").read_text().strip(),
            "# Known cohort clusters (COHORTS.md)\n\n"
            + (ROOT / "studies" / "COHORTS.md").read_text().strip(),
        ]
    )


def build_payload(study_id, pdf_rel, dpi):
    """One payload, posted unchanged to both reviewers."""
    text, images = render_pdf(ROOT / pdf_rel, dpi)
    content = [
        {"type": "text", "text": instruction_block()},
        {
            "type": "text",
            "text": (
                f"# The study report\n\n"
                f"study_id: {study_id}\n"
                f"source_file: {pdf_rel}\n\n"
                f"The report follows twice: first its extracted text layer, then one "
                f"rendered image per page. The text is authoritative for wording; the "
                f"images are authoritative for table and figure layout. Where the text "
                f"layer has interleaved two print columns, trust the image.\n\n"
                f"## Text layer\n\n{text}"
            ),
        },
        {"type": "text", "text": "## Rendered pages"},
    ]
    for b64 in images:
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
        )
    return content, len(images)


# --------------------------------------------------------------------------- call


def post(url, key, body, timeout=1800, attempts=4):
    """POST with backoff on transient failures. Raises on permanent ones."""
    data = json.dumps(body).encode()
    for attempt in range(attempts):
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body_txt = exc.read()[:400].decode("utf8", "replace")
            transient = exc.code in (429, 500, 502, 503, 504)
            if not transient or attempt == attempts - 1:
                raise RuntimeError(f"HTTP {exc.code}: {body_txt}") from None
            time.sleep(5 * 2**attempt)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == attempts - 1:
                raise RuntimeError(f"{type(exc).__name__}: {exc}") from None
            time.sleep(5 * 2**attempt)
    raise RuntimeError("unreachable")


def parse_record(raw):
    """Pull a JSON object out of a model response that may be fenced or prefaced."""
    txt = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", txt, re.S)
    if fence:
        txt = fence.group(1).strip()
    start, end = txt.find("{"), txt.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in response")
    return json.loads(txt[start : end + 1])


# ------------------------------------------------------------------------- repair


def _validator():
    return Draft202012Validator(json.loads((SCHEMA_DIR / "extraction-schema.json").read_text()))


def schema_errors(record):
    """[(path_as_list, message, failing_subschema)] for every schema violation."""
    return [
        (list(e.absolute_path), e.message, e.schema)
        for e in _validator().iter_errors(record)
    ]


def _fmt_path(parts):
    out = ""
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
    return out


def _get(node, parts):
    for p in parts:
        try:
            node = node[p]
        except (KeyError, IndexError, TypeError):
            return None
    return node


def _set(node, parts, value):
    for p in parts[:-1]:
        node = node[p]
    node[parts[-1]] = value


def repair_record(reviewer, record, keys, source_text=None, rounds=2):
    """Ask the model to fix only the shapes the validator rejected.

    Extraction failures here are almost always structural — a bare string where
    the schema wants a value-plus-provenance object — rather than misreadings, so
    the repair asks for corrected values at the failing paths only and leaves the
    rest of the record untouched. The identical procedure runs for both reviewers;
    repairing one and not the other would bias the agreement statistics exactly as
    an altered prompt would.

    The source text is supplied alongside, because some failures can only be fixed
    by going back to the paper: a provenance quote rejected as too short needs a
    genuine longer excerpt, and a model asked to lengthen it without the report in
    front of it simply echoes the value back.

    Returns (record, remaining_errors).
    """
    cfg = REVIEWERS[reviewer]
    for _ in range(rounds):
        errors = schema_errors(record)
        if not errors:
            return record, []
        listing = "\n\n".join(
            f"path: {_fmt_path(p)}\nproblem: {msg}\n"
            f"current value: {json.dumps(_get(record, p), ensure_ascii=False)[:600]}\n"
            f"required shape: {json.dumps(sub, ensure_ascii=False)[:600]}"
            for p, msg, sub in errors[:40]
        )
        source = (
            f"The report you extracted follows, for looking up any quote you need.\n\n"
            f"{source_text}\n\n" if source_text else ""
        )
        ask = (
            "Your extraction failed JSON Schema validation at the paths below. Each is a "
            "structural problem, not a disagreement about what the paper reports.\n\n"
            f"{source}"
            f"{listing}\n\n"
            "Return a single JSON object whose keys are the paths exactly as written above "
            "and whose values are the corrected values for those paths. Preserve every "
            "reading, quote and location you already recorded — change only the shape so it "
            "satisfies the required schema. Where the schema wants a value-and-provenance "
            "object and you supplied a bare value, wrap it and give the quote that supports "
            "that specific value — reuse your existing quote only if it genuinely does; "
            "otherwise quote the passage or table cell that does. Never invent a quote: if "
            "the report does not state the value, set it to null rather than fabricate "
            "support. Where a quote is rejected as too short, return a longer verbatim "
            "excerpt copied from the report that contains the value in context — not the "
            "bare value again. Do not add paths that are not listed. Output only the JSON "
            "object."
        )
        body = {
            "model": cfg["model"],
            "messages": [{"role": "user", "content": ask}],
            "max_tokens": 16000,
            "temperature": 0,
            **cfg["extra"],
        }
        try:
            resp = post(cfg["url"], api_key(cfg, keys), body,
                        timeout=max(600, cfg.get("timeout", 0)))
            patch = parse_record(resp["choices"][0]["message"]["content"] or "")
        except Exception:  # noqa: BLE001 - a failed repair leaves the errors visible
            break
        by_path = {_fmt_path(p): p for p, _, _ in errors}
        for key, value in patch.items():
            parts = by_path.get(key.strip())
            if parts:
                try:
                    _set(record, parts, value)
                except (KeyError, IndexError, TypeError):
                    pass
    return record, schema_errors(record)


def continue_reply(reviewer, content, text, keys, max_tokens, raw, rounds=3):
    """Let a local model finish a reply it ended early.

    On 19 October Qwen3.8-27B ended its turn about 34,000 tokens into zhao_2025's record,
    in the middle of a quote (finish_reason "stop", not "length"), twice, identically.
    The same model is given its own unfinished reply as the start of its answer
    (assistant prefill in llama-server) and continues it, with nothing else changed,
    until the text parses or `rounds` continuations have been tried. Each step is saved
    to `raw`. Only local reviewers get this; both get it alike.
    """
    cfg = REVIEWERS[reviewer]
    for _ in range(rounds):
        try:
            parse_record(text)
            return text
        except (ValueError, json.JSONDecodeError):
            pass
        body = {
            "model": cfg["model"],
            "messages": [{"role": "user", "content": content},
                         {"role": "assistant", "content": text}],
            "max_tokens": min(max_tokens, cfg["max_tokens"]),
            "temperature": 0,
            **cfg["extra"],
        }
        resp = post(cfg["url"], api_key(cfg, keys), body, timeout=cfg.get("timeout", 1800))
        choice = resp["choices"][0]
        piece = choice["message"].get("content") or ""
        if piece.startswith(text):  # a server that echoes the prefill
            piece = piece[len(text):]
        if not piece.strip():
            break
        text += piece
        raw.write_text(text, encoding="utf-8")
        if choice.get("finish_reason") == "length":
            break
    return text


def extract_one(reviewer, study_id, pdf_rel, content, keys, max_tokens, repair=True,
                source_text=None, resume=False):
    cfg = REVIEWERS[reviewer]
    # The raw reply is kept before parsing: a reply that is not valid JSON is otherwise lost.
    raw = ROOT / "extraction" / reviewer / "raw" / f"{study_id}.txt"
    raw.parent.mkdir(parents=True, exist_ok=True)
    resp = {}
    if resume and raw.exists():
        text = raw.read_text(encoding="utf-8")
    else:
        body = {
            "model": cfg["model"],
            "messages": [{"role": "user", "content": content}],
            "max_tokens": min(max_tokens, cfg["max_tokens"]),
            "temperature": 0,
            **cfg["extra"],
        }
        resp = post(cfg["url"], api_key(cfg, keys), body, timeout=cfg.get("timeout", 1800))
        choice = resp["choices"][0]
        finish = choice.get("finish_reason")
        text = choice["message"].get("content") or ""
        if finish == "length":
            raise RuntimeError(
                f"response truncated at max_tokens ({body['max_tokens']}); "
                f"record would be incomplete"
            )
        if not text.strip():
            raise RuntimeError(f"empty content (finish_reason={finish})")
        raw.write_text(text, encoding="utf-8")
    if cfg["key"] is None:
        text = continue_reply(reviewer, content, text, keys, max_tokens, raw)
    record = parse_record(text)
    record.setdefault("record_meta", {})
    record["record_meta"].update(
        {
            "study_id": study_id,
            "extractor": reviewer,
            "model": cfg["model"],
            "extraction_date": date.today().isoformat(),
            "source_file": pdf_rel,
            "schema_version": SCHEMA_VERSION,
        }
    )
    remaining = schema_errors(record)
    if remaining and repair:
        record, remaining = repair_record(
            reviewer, record, keys, source_text=source_text
        )

    out = ROOT / "extraction" / reviewer / f"{study_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return resp.get("usage", {}), out, remaining


# --------------------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="every study in the manifest")
    ap.add_argument("--study", action="append", default=[], help="study_id (repeatable)")
    ap.add_argument("--reviewer", choices=[*REVIEWERS, *PAIRS], default="both",
                    help="one reviewer, 'both' (GLM + DeepSeek) or 'local' (Qwen 27B + Gemma 26B)")
    ap.add_argument("--dpi", type=int, default=120, help="page render resolution")
    ap.add_argument("--max-tokens", type=int, default=100000)
    ap.add_argument("--workers", type=int, default=3, help="studies in flight at once")
    ap.add_argument("--force", action="store_true", help="re-extract existing records")
    ap.add_argument("--no-repair", action="store_true",
                    help="skip the schema-shape repair round (applied to both reviewers alike)")
    ap.add_argument("--dry-run", action="store_true", help="report payload sizes only")
    ap.add_argument("--resume", action="store_true",
                    help="local reviewers: continue the saved raw reply instead of starting over")
    args = ap.parse_args()

    manifest = read_manifest()
    if args.all:
        study_ids = sorted(manifest)
    elif args.study:
        study_ids = args.study
    else:
        ap.error("give --all or --study")

    unknown = [s for s in study_ids if s not in manifest]
    if unknown:
        sys.exit(f"not in manifest: {', '.join(unknown)}")

    reviewers = PAIRS.get(args.reviewer, [args.reviewer])
    needs_key = [r for r in reviewers if REVIEWERS[r]["key"]]
    keys = load_keys() if needs_key and not args.dry_run else {}
    if not args.dry_run:
        missing = [r for r in needs_key if REVIEWERS[r]["key"] not in keys]
        if missing:
            sys.exit(f"no API key for: {', '.join(missing)}")
    workers = min([args.workers] + [REVIEWERS[r].get("workers", args.workers) for r in reviewers])

    def run(study_id):
        pdf_rel = f"studies/fulltext/{manifest[study_id]}"
        todo = [
            r
            for r in reviewers
            if args.force or not (ROOT / "extraction" / r / f"{study_id}.json").exists()
        ]
        if not todo:
            return study_id, ["skip (already extracted)"]
        content, n_pages = build_payload(study_id, pdf_rel, args.dpi)
        source_text, _ = render_pdf(ROOT / pdf_rel, args.dpi)
        if args.dry_run:
            mb = len(json.dumps(content)) / 1e6
            return study_id, [f"{n_pages} pages, payload {mb:.1f} MB"]
        lines = []
        for reviewer in todo:
            t0 = time.time()
            try:
                usage, out, remaining = extract_one(
                    reviewer, study_id, pdf_rel, content, keys, args.max_tokens,
                    repair=not args.no_repair, source_text=source_text, resume=args.resume,
                )
                status = "ok  " if not remaining else f"INVALID({len(remaining)})"
                lines.append(
                    f"{reviewer:9s} {status} {time.time() - t0:5.0f}s  "
                    f"in={usage.get('prompt_tokens', '?')} out={usage.get('completion_tokens', '?')}  "
                    f"{out.relative_to(ROOT)}"
                )
                for path, msg, _ in remaining[:3]:
                    lines.append(f"          ! {_fmt_path(path)}: {msg[:110]}")
            except Exception as exc:  # noqa: BLE001 - reported per study, run continues
                lines.append(f"{reviewer:9s} FAIL {time.time() - t0:5.0f}s  {exc}")
        return study_id, lines

    failures = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run, s): s for s in study_ids}
        for fut in as_completed(futures):
            study_id, lines = fut.result()
            print(f"\n{study_id}")
            for line in lines:
                print(f"  {line}")
                failures += ("FAIL" in line) or ("INVALID" in line)
            sys.stdout.flush()

    print(f"\n{len(study_ids)} studies, {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
