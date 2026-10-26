"""Search update (A8): parse the librarian's re-run, deduplicate it, and find
the records that can be new since the original search of 4 February 2026.

The re-run is the whole strategy from inception, not a date-limited update, and
the February RIS files are not in the repo. "New" is therefore approximated from
what each database exports, erring towards screening too much:

- MEDLINE: the Ovid segment (DB field) is dated by entry. Segments from
  "February Week 01 2026" on, and the daily/in-process/epub/PubMed-not-MEDLINE
  files dated September 2026, are new.
- Embase and CENTRAL: accession numbers rise with load date. A record is new if
  its accession number is at or above the lowest one carried by any record
  published in 2026 (each numbering scheme separately), since nothing published
  in 2026 was loaded before 2026.
- Any database: publication year 2025 or later.

Checks that the re-run still finds every included report in studies/manifest.csv
(by DOI, else title): the 33 of February when it was first run, 37 since D42.

    python analysis/search_update.py [--dir studies/search/update_2026-09-29]
"""
import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "studies" / "search" / "update_2026-09-29"
FILES = {"MEDLINE": "MEDLINE_Sep2026.ris", "Embase": "Embase_Sep2026.ris",
         "CENTRAL": "CENTRAL_Sep2026.ris"}
NEW_MEDLINE_SEGMENT = re.compile(
    r"February Week 01 2026|July Week 05 2026|September 28, 2026")


def parse_ris(path):
    text = path.read_text(encoding="utf-8", errors="replace").replace("\r", "")
    records, cur = [], None
    for line in text.split("\n"):
        m = re.match(r"^([A-Z][A-Z0-9])  -\s?(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if key == "TY":
            cur = {}
        if key == "ER":
            records.append(cur)
            cur = None
            continue
        if cur is not None:
            cur.setdefault(key, []).append(val)
    return records


def first(rec, *keys):
    for k in keys:
        for v in rec.get(k, []):
            if v:
                return v
    return ""


def norm_doi(s):
    s = s.lower().strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = s.replace("%28", "(").replace("%29", ")").replace("%2f", "/")
    return s.rstrip(".")


def norm_title(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def surname(a):
    """'Rowley, Daniel D' / 'Rowley D.D.' / 'Rowley DD' -> 'rowley'."""
    a = a.split(",")[0].strip() if "," in a else a.strip().split(" ")[0]
    return re.sub(r"[^a-z]", "", a.lower())


def year_of(rec):
    m = re.match(r"(\d{4})", first(rec, "Y1", "PY"))
    return int(m.group(1)) if m else None


def to_row(db, rec):
    pmid = first(rec, "ID") if db == "MEDLINE" else ""
    c3 = " ".join(rec.get("C3", []))
    m = re.search(r"PUBMED (\d+)", c3)
    if m:
        pmid = m.group(1)
    m = re.search(r"NCT\d{8}", c3 + " " + first(rec, "AN"))
    nct = m.group(0) if m else ""
    doi = norm_doi(first(rec, "DO"))
    return {
        "database": db,
        "segment": first(rec, "DB"),
        "accession": first(rec, "ID", "AN"),
        "pmid": pmid,
        "nct": nct,
        "doi": doi,
        "year": year_of(rec) or "",
        "title": first(rec, "T1", "TI"),
        "authors": "; ".join(rec.get("A1", [])[:6]),
        "first_author": surname(first(rec, "A1")),
        "journal": first(rec, "JF", "JA", "T3"),
        "pub_type": "; ".join(rec.get("PT", []) or rec.get("M3", [])),
        "abstract": " ".join(rec.get("N2", [])),
    }


def accession_key(db, acc):
    """(scheme, number) so thresholds compare like with like."""
    if db == "CENTRAL" and acc.startswith("CN-"):
        return "CN", int(acc[3:])
    if acc.startswith("NCT"):
        return "NCT", int(acc[3:])
    if acc.isdigit():
        return f"{db}-{len(acc)}", int(acc)
    return None


def flag_new(rows):
    floors = {}
    for r in rows:
        k = accession_key(r["database"], r["accession"])
        if k and r["year"] == 2026 and r["database"] != "MEDLINE":
            floors[k[0]] = min(floors.get(k[0], k[1]), k[1])
    for r in rows:
        why = []
        if r["year"] and r["year"] >= 2025:
            why.append("year>=2025")
        if r["database"] == "MEDLINE" and NEW_MEDLINE_SEGMENT.search(r["segment"]):
            why.append("medline_segment")
        k = accession_key(r["database"], r["accession"])
        if r["database"] != "MEDLINE" and k and k[0] in floors and k[1] >= floors[k[0]]:
            why.append("accession")
        r["possibly_new"] = "; ".join(why)
    return floors


def ambiguous_dois(rows):
    """DOIs carried by records with different titles. A journal supplement gives one
    DOI to every conference abstract in it; matching on that DOI merged up to 20
    different abstracts into one record (audit, 29 September 2026)."""
    titles = defaultdict(set)
    for r in rows:
        if r["doi"]:
            titles[r["doi"]].add(norm_title(r["title"])[:40])
    return {d for d, t in titles.items() if len(t) > 1}


def deduplicate(rows):
    """Group by DOI (unless several titles share it), then PMID (CENTRAL's C3
    carries it), then trial registration number (registry records only carry it),
    then normalised title + year + first author's surname (a reply letter can carry
    the title of the article it answers). MEDLINE first, so the kept copy is the one
    with the richest metadata."""
    order = {"MEDLINE": 0, "Embase": 1, "CENTRAL": 2}
    rows.sort(key=lambda r: order[r["database"]])
    bad_doi = ambiguous_dois(rows)
    seen, kept = {}, []
    for r in rows:
        keys = []
        if r["doi"] and r["doi"] not in bad_doi:
            keys.append("doi:" + r["doi"])
        if r["pmid"]:
            keys.append("pmid:" + r["pmid"])
        if r["nct"]:
            keys.append("nct:" + r["nct"])
        t = norm_title(r["title"])
        if len(t) > 30:
            keys.append(f"ti:{t}:{r['year']}:{r['first_author']}")
        hit = next((seen[k] for k in keys if k in seen), None)
        if hit is None:
            r["also_in"] = ""
            kept.append(r)
            hit = r
        else:
            if r["database"] not in hit["also_in"] and r["database"] != hit["database"]:
                hit["also_in"] = "; ".join(filter(None, [hit["also_in"], r["database"]]))
            # a duplicate that looks new makes the kept copy look new too
            if r["possibly_new"] and not hit["possibly_new"]:
                hit["possibly_new"] = r["possibly_new"] + " (via duplicate)"
        for k in keys:
            seen.setdefault(k, hit)
    return kept


def recall_check(kept):
    by_doi = {r["doi"]: r for r in kept if r["doi"]}
    by_title = {norm_title(r["title"])[:60]: r for r in kept}
    out = []
    with open(ROOT / "studies" / "manifest.csv", encoding="utf-8") as f:
        for m in csv.DictReader(f):
            doi = norm_doi(m["doi"] or "")
            hit = by_doi.get(doi) if doi.startswith("10.") else None
            how = "doi" if hit else ""
            if not hit:
                hit = by_title.get(norm_title(m["title"])[:60])
                how = "title" if hit else ""
            out.append((m["study_id"], how, hit["database"] if hit else ""))
    return out


def assign_ids(kept, previous):
    """Record ids are what the screening answers are keyed on. A record kept before
    keeps its id (matched on database and accession); records the corrected dedup
    separates get ids after the last one used."""
    old = {}
    if previous.exists():
        with open(previous, encoding="utf-8") as f:
            old = {(r["database"], r["accession"]): r["record_id"] for r in csv.DictReader(f)}
    used = set(old.values())
    nxt = max((int(i[1:]) for i in used), default=0) + 1
    for r in kept:
        rid = old.get((r["database"], r["accession"]))
        if rid is None:
            rid, nxt = f"U{nxt:04d}", nxt + 1
        r["record_id"] = rid
    missing = used - {r["record_id"] for r in kept}
    if missing:
        raise SystemExit(f"{len(missing)} earlier record ids no longer kept, e.g. {sorted(missing)[:5]}")


FIELDS = ["record_id", "database", "also_in", "segment", "accession", "pmid", "nct", "doi",
          "year", "possibly_new", "title", "authors", "journal", "pub_type", "abstract"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    args = ap.parse_args()

    rows = []
    for db, name in FILES.items():
        recs = parse_ris(args.dir / name)
        print(f"{db:8s} {len(recs):5d} records")
        rows += [to_row(db, r) for r in recs]
    print(f"{'total':8s} {len(rows):5d}")
    floors = flag_new(rows)
    print("accession floors (lowest carried by a 2026 record):", floors)

    kept = deduplicate(rows)
    assign_ids(kept, args.dir / "records_dedup.csv")
    new = [r for r in kept if r["possibly_new"]]
    print(f"after deduplication {len(kept)}; possibly new since 4 Feb 2026: {len(new)}")
    counts = defaultdict(int)
    for r in new:
        counts[r["database"]] += 1
    print("  possibly new by database kept:", dict(counts))

    for name, subset in (("records_dedup.csv", kept), ("possibly_new.csv", new)):
        with open(args.dir / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(subset)

    rc = recall_check(kept)
    missing = [s for s, how, _ in rc if not how]
    print(f"included reports found again: {len(rc) - len(missing)}/{len(rc)}")
    for s in missing:
        print("  not matched automatically:", s)


if __name__ == "__main__":
    main()
