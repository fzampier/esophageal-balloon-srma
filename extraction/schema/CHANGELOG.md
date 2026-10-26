# Schema changelog

`record_meta.schema_version` must equal the schema's own version. The validator
enforces this, so a record built against an older schema fails loudly instead of
claiming false currency.

**Both reviewers must be given the same schema version.** If the schema changes
between the two extraction runs, re-run the earlier reviewer. Otherwise the
disagreements `diff.py` reports are partly artefacts of the form, not of the
reading — and there is no way to tell which is which after the fact.

## 1.2.0 — 2026-08-30

Reviewer change. No field definitions were touched — this version exists so that
records carry an honest account of who produced them.

**Breaking**

- `record_meta.extractor` enum is now `["glm", "deepseek", "human",
  "reconciled"]`. The two extracting reviewers are GLM 5.3 Flash
  (`extraction/glm/`) and DeepSeek V4 Flash with vision
  (`extraction/deepseek/`), replacing Claude and GPT 5.6. No production record
  had been written against the old enum, so nothing needed migrating.

**Clarified**

- `record_meta.model` on a reconciled record names the model or person that
  performed the adjudication. Adjudication is no longer assumed to be human, and
  a reconciled record must not imply a human pass that did not happen.

**Why the reviewers changed how the paper is delivered.** `deepseek-v4-flash`
rejects images outright; `deepseek-v4-flash-vision-exp` accepts them, as does
`glm-5.3-flash`. Because both reviewers must receive identical input, the choice
had to be made once for both rather than per model: both now receive the same
rendered page images together with the same column-sorted text layer. Sending one
reviewer images and the other text would contaminate the agreement statistics
exactly as an altered prompt would.

## 1.1.0 — 2026-07-31

Revisions from two pilot extractions against real papers.

**Breaking**

- `outcomes[].effect_estimate` (object) is now `outcomes[].effect_estimates`
  (array). Talmor reports both an unadjusted and an APACHE-II-adjusted risk
  ratio for 28-day mortality, and the adjusted one carries the significant
  result. A single slot forced the adjusted analysis into free text.
- `outcomes[].measurement_context` is a `stringField` object rather than a bare
  string.
- `extraction_flags.cohort_id` added and required.

**Added**

- `design.stopped_early`, `stopping_rule`, `planned_sample_size`,
  `sample_size_calculation`. EPVent-1 stopped at a prespecified interim boundary
  after 61 of a planned 200 patients — the dominant interpretive fact about the
  anchor trial, previously unrecordable.
- `design.location_provenance`, so `countries` inferred from a city name can say
  so.
- `population.sex_male`, `predicted_body_weight_kg`, and `additional_baseline[]`
  — a general slot for reported baseline characteristics without a dedicated
  field. Roughly half of Talmor's Table 1 had no home.
- `outcomes[].data[].within_arm_change`, for a paired within-arm change reported
  in a parallel-group trial.
- `feasibility.n_measurement_not_obtainable` and its reasons, distinct from
  failed placement.
- `outcome.domain`: `esophageal_pressure`, `ventilator_settings`, `dead_space`,
  `icu_free_days`, `organ_failure_free_days`.
- `timepoint.type`: `duration_until_event`, `repeated_measures`.
- `balloon_calibration.method`: `signal_quality_verification_only`.

**Clarified in the codebook** — each was a live disagreement risk between the two
reviewers: whether baseline physiologic values belong in `outcomes` (they do);
the mortality-ranking tiebreak; what `complications_explicitly_none` requires;
`adults_18_plus` when no explicit age criterion is stated; what
`is_primary_outcome` marks; when `baseline_by_arm` is the primary source; and
that `timing_subgroup` is `unclear` when timing is known only relative to
enrolment.

## 1.0.0 — 2026-07-31

Initial schema. Superseded before any production extraction; do not use.
