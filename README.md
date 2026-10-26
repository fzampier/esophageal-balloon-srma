# Esophageal Pressure–Guided PEEP Titration in Mechanically Ventilated Adults: A Systematic Review and Meta-Analysis

Code, model instructions, extraction records, adjudication ledgers and analysis
outputs of the review.

- Authors: Anton Nikouline, Michal Chwalek, Imran Hudda, Oleksa Rewa, Janek Senaratne, Katie O'Hearn, J. Dayre McNally, Bram Rochwerg, Fernando G. Zampieri
- Protocol: PROSPERO CRD420261344978
- Repository: https://github.com/fzampier/esophageal-balloon-srma
- License: code under MIT; everything else under CC BY 4.0 (`LICENSE`)
- Exported 2026-10-26 from commit a58d250 of the authors' working repository.
  `MANIFEST.csv` lists every file with its size in bytes and its SHA-256.

## How the data were produced

Two large language models extracted data from each of the 36 included
reports independently, with identical instructions and identical input: the
report's pages rendered as images (120 dpi) and its column-sorted text layer.
Each model filled the same JSON schema and quoted the text supporting each value.

Differences between the two records were adjudicated only in the fields that
enter the analysis (listed in `analysis/scope.py`). In every other field the
reconciled record carries the first model's (GLM's) reading, unadjudicated, and
says so in `record_meta.adjudication_notes`. Two authors ruled on contested
numerical readings of pooled quantities, on readings returned as unclear and on
reports sharing patients. A third model ruled on the remaining differences; it
could only choose one of the two readings or return `unclear`. Where neither
reading was right, the senior author entered the value from the report as a
logged post-merge edit citing the page. Only reconciled records reach the
analysis.

Risk of bias was assessed the same way: the same two models, one prompt and one
input for both, and author adjudication of every judgement. RoB 2 was used for
randomized results and the NIH quality assessment tools for non-randomized
studies; RoB 2 domain judgements are computed from the signalling answers with
the published algorithms (`analysis/rob.py`).

## Models

| Role | Model |
|---|---|
| Extraction, reviewer 1 | GLM 5.3 Flash (`glm-5.3-flash`, Z.ai API) |
| Extraction, reviewer 2 | DeepSeek V4 Flash with vision (`deepseek-v4-flash-vision-exp`, DeepSeek API) |
| Risk of bias; title/abstract screening of the September 2026 search update | the same two models, same arrangement |
| Third-model adjudication | Claude Opus 5 (`claude-opus-5`); Claude Opus 5.5 (`claude-opus-5-5`) for the reports added by the search update |

Each ruling in `extraction/adjudication/rulings.csv` names its adjudicator and
whether it is a person or a model (`adjudicator_type`).

## Contents

| Path | Contents |
|---|---|
| `extraction/schema/` | `EXTRACTION_PROMPT.md` (the extraction instruction), `extraction-schema.json` (JSON Schema, version 1.2.0), `CODEBOOK.md` (coding rules), `template.json`, `CHANGELOG.md` |
| `studies/COHORTS.md` | Notes on reports sharing patients; appended to the extraction instruction by `analysis/extract.py` |
| `extraction/glm/`, `extraction/deepseek/` | Each model's record for each report (36 and 36 files), as returned; `record_meta` is stamped by the runner |
| `extraction/reconciled/` | The adjudicated records (36), the only input to the analysis |
| `extraction/adjudication/` | `rulings.csv` (2406 rulings on field-level differences), `study_rulings.csv` and `cohorts.csv` (study-level and cohort rulings), `edits.csv` (180 post-merge edits), `model_pass/` (the third model's verdict files), `MODEL_PASS.md` (the third model's instruction), `README.md` (column definitions) |
| `studies/manifest.csv` | Each report's `study_id`, citation, DOI and PDF file name |
| `studies/listed_not_analysed.csv` | Reports included at full-text screening that answer no outcome of the review |
| `studies/search/SCREENING_PROMPT.md` | Instruction for the two-model title/abstract screen of the September 2026 search update |
| `risk_of_bias/` | `schema/ROB_PROMPT.md` and `schema/rob-schema.json`, `assignments.csv` (tool and results per study, fixed by the authors), `glm/` and `deepseek/` (parsed answers), `raw/` (raw responses),`adjudication/` (proposals the authors accepted), `judgements.csv` (final judgements, each with who settled it), `rob2_summary.png` |
| `analysis/` | Python pipeline: validation, diff, reconciliation, flattening, agreement, risk of bias, and the runners that call the model APIs |
| `analysis/meta/` | R meta-analyses (metafor) and their two lookup tables |
| `analysis/derived/` | Analysis tables written by `flatten.py` from the reconciled records |
| `analysis/results/` | Pooled estimates, study inventories, forest plots, physiology tables, agreement statistics (metafor 5.2.1, run 2026-10-24 15:00:40) |
| `tests/` | Regression tests on two synthetic records (`fixtures/`; neither is a real study) |
| `docs/manuscript/make_table1.py` | Builds Table 1 from `analysis/derived/` |

In `EXTRACTION_PROMPT.md` and `ROB_PROMPT.md` the text above the first horizontal
rule is a note to the operator and was not sent to the models.

## Running it

Python 3.13 with `requirements.txt`, and R 4.6.1 with metafor. On Windows, set
`PYTHONUTF8=1` first.

```bash
python -m pip install -r requirements.txt
Rscript -e 'install.packages("metafor")'

python tests/test_pipeline.py                    # regression tests
python analysis/validate.py extraction/reconciled # schema and cross-field rules
python analysis/diff.py                          # GLM vs DeepSeek differences
python analysis/reconcile.py                     # ledgers + edits -> extraction/reconciled/
python analysis/flatten.py                       # reconciled -> analysis/derived/
Rscript analysis/meta/run.R                      # analysis/derived/ -> analysis/results/
python analysis/rob.py check                     # risk-of-bias records: schema, completeness, algorithm
python analysis/rob.py diff                      # risk-of-bias agreement between the two models
python docs/manuscript/make_table1.py            # Table 1 -> docs/manuscript/table1.md
```

`reconcile.py` stamps the day it runs into `record_meta.extraction_date`; that is
the only expected difference from the deposited records.

These need inputs that are not deposited: `analysis/extract.py`,
`analysis/model_pass.py build` and `analysis/rob.py run|complete` read the PDFs
and call the model APIs (keys from a local file, `~/Desktop/api.md`);
`analysis/screen.py` and `analysis/search_update.py` read the bibliographic
database exports; `analysis/rulings.py import` reads the workbooks returned by
the adjudicators; `analysis/rob.py run` also needs the RoB 2 and NIH tool texts
in `risk_of_bias/tools/` (file names in `analysis/rob.py`).

## Left out, and why

- **The reports.** The PDFs, and the page images and text layers rendered from
  them, are copyrighted. `studies/manifest.csv` gives each report's citation and
  DOI. The records do contain the short verbatim quotations the models gave as
  support for each value or answer.
- **Search and screening exports.** The MEDLINE, Embase and CENTRAL exports, the
  deduplicated record lists, the screening-platform exports and the models'
  screening answers, which are keyed to those records.
- **Risk-of-bias tool texts.** Third-party documents, given to the models
  verbatim: the RoB 2 cribsheet of 22 August 2019 (Sterne JAC et al. RoB 2: a
  revised tool for assessing risk of bias in randomised trials. BMJ
  2019;366:l4898), the RoB 2 cribsheet for crossover trials of 18 March 2021
  (riskofbias.info), and the NHLBI Study Quality Assessment Tools for
  observational cohort studies and for before-after studies with no control
  group (nhlbi.nih.gov, accessed 28 September 2026).
- **Workbooks.** Adjudication worksheets and the workbooks the adjudicators
  returned. Every decision in them is a line of the ledgers; the `source` and
  `basis` columns name the workbook a ruling came from, with its SHA-256.
- **Correspondence, the protocol document, drafts and reports, and the authors'
  internal decisions log.** Codes such as `C17`, `D23` or `B8` in code comments,
  ledgers and notes are entries of that log.
- **Manuscript, supplement and figure builders** (`make_manuscript.py`,
  `make_supplement.py`, `make_figures.R`): they read the manuscript text and the
  screening exports.
- **An exploratory analysis not reported in the paper.**

## Reading the files

- `record_meta.source_file` names the PDF as stored in the authors' repository.
- Free-text notes written by the human adjudicators are left out of
  `rulings.csv`, `study_rulings.csv` and `cohorts.csv`; the third model's notes
  are kept. Each human ruling still names its adjudicator, decision and source.
- In `risk_of_bias/judgements.csv`, `by` gives the initials of the author who
  settled each judgement and the date (`analysis/rob.py final` writes a longer
  form).
- Ledger lines for `crognier_2025` refer to a report extracted after the search
  update and then excluded at full text; its records are not deposited and
  `reconcile.py` ignores those lines.
- `extraction/adjudication/model_pass/soo_ping_chow_2025.json` is the verdict
  file for `chow_2025` under its earlier name; ledger lines identify it by name
  and hash.
- `studies/COHORTS.md` has been edited since the first extraction run; the file
  records its own corrections.
- `analysis/rob.py final` (judgements and figure) also needs matplotlib.
