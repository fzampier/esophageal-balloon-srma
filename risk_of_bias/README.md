# Risk of bias

Two-model first pass, author adjudication (decisions D26, D36, D37).

| Path | What |
|---|---|
| `assignments.csv` | Which tool each study gets, and for RoB 2 which results. Fixed by the review, not by the models. |
| `schema/ROB_PROMPT.md` | The instruction both reviewers receive, byte for byte (below the first rule). |
| `schema/rob-schema.json` | Record format: RoB 2 answers per result, or NIH items plus rating. |
| `tools/` | Tool text given to the models (RoB 2 cribsheets, NIH tools with guidance) and the source documents under `tools/source/`. RoB 2 documents © the RoB 2 Development Group, CC BY-NC-ND 4.0, unmodified; NIH tools are US-government works. |
| `glm/`, `deepseek/` | One record per study per reviewer. |
| `raw/` | Each reviewer's raw response, and any JSON-syntax repair beside it. |
| `adjudication/` | Workbooks for the authors; returned workbooks go in `adjudication/returned/`. |

```bash
PYTHONUTF8=1 python analysis/rob.py run --all        # both reviewers
PYTHONUTF8=1 python analysis/rob.py check            # schema, missing questions, algorithm gaps
PYTHONUTF8=1 python analysis/rob.py complete         # ask for questions a reviewer left out (same rule for both)
PYTHONUTF8=1 python analysis/rob.py diff             # agreement, and every domain where they differ
PYTHONUTF8=1 python analysis/rob.py worksheet        # -> adjudication/rob_adjudication_<date>.xlsx
PYTHONUTF8=1 python analysis/rob.py final            # rulings -> judgements.csv (settled/proposed) + rob2_summary.png
```

RoB 2 domain judgements are recomputed from the answers with the published algorithms. Those were
transcribed from the VBA of the developers' Excel tools, and `tests/test_pipeline.py` checks them.
Tool versions: RoB 2 of 22 August 2019 (parallel), RoB 2 for crossover trials of 18 March 2021. The
NIH tools were downloaded from nhlbi.nih.gov on 28 September 2026.

Rulings: `adjudication/recommendations_*.csv` and `adjudication/proposals_*.csv` hold Claude's proposals, each
checked on the page. A file becomes settled when the PI accepts it and it is listed in `SETTLED` in
`analysis/rob.py`. RoB 2 judgements on which the two reviewers agreed were confirmed by the PI in bulk
(28 September 2026). `judgements.csv` is the table for GRADE; every row says who settled it.

Known and harmless: `rob.py check` reports two GLM records (mauri_2024, obi_2018) with one extra key beside a
domain judgement (`support_note`, `support`). The judgement and rationale are complete, and the model output is kept
as returned.
