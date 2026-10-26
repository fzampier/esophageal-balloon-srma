# Adjudication ledger

Everything that settles a difference between the two reviewers, and who settled
it. `analysis/reconcile.py` reads only these files; nothing is decided anywhere
else.

| File | Contents |
|---|---|
| `rulings.csv` | One line per ruling on a field-level difference, keyed by study, field, kind and both reviewers' readings as `diff.py` prints them. `decision` is `glm`, `deepseek`, `value` (both wrong; the reading is in `value`), `not_reported`, or `unclear` (settles nothing). `adjudicator_type` separates human from model rulings; a human ruling always wins. |
| `study_rulings.csv` | Rulings about a whole study: which reviewer's `is_longest_reported_mortality` choice stands, and the sheet-2 cohort rulings as returned. |
| `cohorts.csv` | Final cohort assignment for every study whose overlap was disputed. `pending` blocks the study from `extraction/reconciled/` even where its reviewers agree. |
| `edits.csv` | Post-merge edits (C17): corrections a human wrote into a merged record where no choice between the two readings could express them — a number fixed inside an entry only one reviewer has, a contrast both missed, an entry both recorded that the paper does not support. One line per edit: `set`, `drop` or `add` on a path in diff.py's label syntax, with the adjudicator, the follow-up row it answers and the page it rests on. `reconcile.py` applies them after every ruling; an edit whose path no longer resolves blocks the study. `python analysis/edits.py check|show|import`. |
| `returned/` | The returned workbooks, byte for byte. Each ledger line's `source` carries the workbook's sha256 and build stamp. |

Add rulings with `analysis/rulings.py import <workbook> --adjudicator "<name>"`,
never by hand-editing a line that is already there: a correction is a new line,
so the history of what was ruled stays readable. Among human rulings on the same
difference or the same element, the later line wins (C18). A "Both wrong" with no
value written is recorded as Unclear (C20). The PI's passes of 2026-09-15 are
`returned/adjudication_followup_Fernando_pass1..5_*.xlsx`; Anton's return is
`returned/done_adjudication_followup_Anton_2026-09-15.xlsx`. `analysis/rulings.py check`
lists rulings whose difference no longer exists in the current diff.

Scope is Option B (co-author decision, 2026-09-15): only differences on fields
listed in `analysis/scope.py` must be settled. Differences elsewhere carry GLM's
reading unadjudicated, and each reconciled record says so in
`record_meta.adjudication_notes`.

Renames: `soo_ping_chow_2025` became `chow_2025` on 2026-09-15. Ledger lines
carry the new id; `model_pass/soo_ping_chow_2025.json` keeps its original name
and bytes because ledger `source` entries identify it by that name and hash.

`proposed/` holds edit specs written for `edits.py import` but not yet imported: proposals awaiting the PI, each row of DECISIONS.md naming its file. Importing one appends it to `edits.csv`; the spec stays as the record of what was proposed.
