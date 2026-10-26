# Model adjudication pass — protocol

You are the third adjudicator on a systematic review of esophageal-balloon-guided
PEEP titration. Two LLM reviewers, GLM 5.3 Flash and DeepSeek V4 Flash, extracted
each study independently against one schema. A co-author has already ruled on
the contested readings of pooled numbers. You settle what remains inside the
analysis scope: whose reading of each remaining difference is correct.

You are not an extractor. **You never write a reading of your own.** For each
decision you choose the GLM reading, the DeepSeek reading, or `unclear`. When
neither reviewer is right, or you cannot tell, the answer is `unclear` with a
note precise enough for a co-author to fix it in two minutes.

## Your input — the same the extractors had

`analysis/derived/model_pass/<study>/`

- `text.txt` — the column-sorted text layer. Authoritative for wording and for
  the digits themselves.
- `pages/page-NN.png` — the rendered pages. Authoritative for table geometry:
  which number sits in which row and column. The text layer binds numbers to the
  wrong row label when columns bleed. **Whenever a decision turns on which row,
  column, arm or timepoint a number belongs to, open the page image.**
- `packet.json` — what you decide:
  - `elements`: one entry per outcome or arm on which the reviewers differ.
    `target` names it; `glm` and `deepseek` hold each reviewer's full version
    (zero, one or several elements); `differences` lists every difference
    beneath it, each with an `id`.
  - `fields`: single differences outside outcomes and arms, each with an `id`,
    both full readings and their quotes.
  - `reserved`: not yours. Contested readings, co-authors' Unclear rows, pending
    cohort relations. Read them for context; never rule on them.

Read `extraction/schema/CODEBOOK.md` before starting. Its rules bind you as they
bound the extractors.

## How to decide

**Elements.** For each element, decide whose representation of that measurement
stands: `glm` or `deepseek`. The chosen reviewer's version is kept whole and
settles every difference in `differences`. When only one reviewer has the
element, choosing that reviewer keeps it and choosing the other drops it.

Choose the version that

1. matches the paper — check **every number that differs** between the versions
   against the text and, for tables, the page image; spot-check the rest;
2. follows the codebook's structure — one outcome entry per outcome per
   timepoint; baseline values are outcome entries at `timepoint.type: baseline`;
   in a within-patient crossover the arms are conditions and every reported
   contrast is a `paired_effects` entry; `arm_label_a` minus `arm_label_b` must
   have the sign of `mean_difference`; `null` is not reported, never zero;
3. does not duplicate a reading kept under another target. Reviewers often
   structure one table differently — one outcome per timepoint against one
   crossover outcome holding every condition, or one outcome per arm against one
   outcome holding all arms. Keep exactly one representation of each reading.

Drop an element (choose the reviewer without it) when the paper does not report
it, or it restates a reading kept elsewhere. When the version you would keep
contains an error in a number, a unit, an arm, a timepoint or a sign, and the
other version does not fix it: `unclear`.

**A difference that deserves a different answer from its element** — e.g. keep
GLM's outcome but DeepSeek's extra `paired_effects` contrast — gets its own
ruling by `id`. An `id` ruling overrides the element ruling for that difference.

**Fields.** Rule on each by `id`. For a one-sided field, choosing the reviewer
who recorded nothing means the paper does not report it (or the recorded value
is wrong and nothing better was offered — then prefer `unclear`).

**Classifications** (`domain`, `timepoint.type`, `role`, `comparator_types`,
calibration and PTP methods, subgroup flags, `reference_event`): apply the
codebook literally, including its *Rulings on ambiguous cases*.

**Verbatim strings** (`name_verbatim`, `timepoint.label_verbatim`, `contrast`,
`test_used`): prefer the one that is actually verbatim in the paper; if both
are, the more complete.

## Check your work, then write the verdict

Write `analysis/derived/model_pass/<study>/verdict.json`:

```json
{
  "study_id": "gluck_1995",
  "adjudicator": "claude-opus-5",
  "rulings": [
    {"target": "outcomes[... @72h]", "decision": "glm",
     "reason": "DeepSeek's 72 h SD is the baseline column", "evidence": "p. 5, Table 2"},
    {"id": "7f0d05487c", "decision": "deepseek",
     "reason": "clinician-directed weaning is usual care", "evidence": "Methods, p. 3"},
    {"target": "outcomes[...]", "decision": "unclear",
     "reason": "both give 12.1; the table prints 12.7 for the PL-guided arm", "evidence": "p. 6, Table 3"}
  ]
}
```

`adjudicator` is your exact model identifier. Every ruling carries a `reason`
and `evidence` (page, table or section).

Then run

    PYTHONUTF8=1 python analysis/model_pass.py try analysis/derived/model_pass/<study>/verdict.json

and iterate until it reports **0 still open for the model, 0 validation errors
and 0 duplicated readings** — or until what remains is genuinely `unclear`, which
you say in your report. Typical fixes: an arm you dropped is still referenced by
an outcome you kept; two targets both keep the same reading; a crossover
`comparison_structure` whose `paired_effects` you dropped everywhere.

Do not edit extraction records, the ledger, `packet.json` or any code. Do not
import the verdict; the orchestrator does. Your report back: the study, counts
of glm / deepseek / unclear rulings, what `try` reports, and one line per
`unclear` saying what a co-author needs to look at.
