# Codebook

Coding rules for `extraction-schema.json` v1.1.0. Binding on both LLM reviewers
and on human adjudicators. See `CHANGELOG.md` before using a record extracted
against an earlier version.

## Five rules that override everything else

1. **Record what the paper reports, not what it implies.** If a study reports
   median and IQR, record median and IQR. Conversion to mean and SD happens once,
   downstream, in the analysis — never at extraction.
2. **Every non-null value carries a verbatim quote and a location** wherever the
   schema provides a provenance slot. The quote is copied text, not a paraphrase.
   A table cell is quoted as its row label and contents, e.g.
   `"28-day mortality — 17/61 (27.9%) vs 24/60 (40.0%)"`. A few fields are bare
   scalars with no slot — note any inference behind them in `unclear_items`.
3. **Not reported is `null`.** A reported zero is `0`. These are different facts
   and must never be collapsed.
4. **Anything you calculate sets `derived: true` and explains itself** in
   `derivation`. Computing a percentage from a fraction is derivation. Summing
   two subgroups into a total is derivation.
5. **When uncertain, say so.** Put the field name and the reason in
   `extraction_flags.unclear_items` rather than guessing. An honest gap costs a
   minute of adjudication; a confident wrong number can survive to publication.

## Identifiers

`study_id` is `firstauthor_year`, lowercase, ASCII, no punctuation except the
underscores: `talmor_2008`, `beitler_2019`, `baedorf_kassis_2016`. Two papers by
the same first author in the same year get a trailing letter: `wang_2019a`,
`wang_2019b`. The file name is `<study_id>.json` in the extractor's directory.

## Eligibility

Assess each of the eight criteria on its own. `include_final` is true only when
no criterion is `no`. A criterion coded `unclear` does not by itself exclude the
study — it routes to adjudication.

The criterion that decides most borderline cases is
`balloon_guided_peep_titration`. Esophageal pressure must have *changed
ventilator management*. A study that measures esophageal pressure and reports it
descriptively, without a rule linking it to PEEP, fails this criterion even if
the measurements are extensive.

## Design and comparison structure

`study_design` describes how the study was run. `comparison_structure` describes
where the comparison lives, and it drives which outcome fields you populate.

| comparison_structure | Meaning | Populate |
|---|---|---|
| `parallel_arms` | Different patients, different strategies | `data[]` per arm, `effect_estimates[]` |
| `within_patient_crossover` | Same patients under two or more strategies, including simple before/after protocols | `data[]` per condition **and** `paired_effects[]` |
| `single_group_no_comparison` | One strategy, described but not contrasted | one `data[]` entry |

A physiologic study that measures compliance at conventional PEEP and again at
esophageal-guided PEEP in the same twenty patients is
`within_patient_crossover`, not `single_group_no_comparison`. The paired
difference is the whole point of the study and must be captured.

Crossovers here routinely run to three, four, or six conditions. `arms[]` takes
as many as the study applies, with `sequence_position` recording the order.
`paired_effects[]` takes every contrast the paper reports — a k-condition study
admits k(k−1)/2 of them — plus any omnibus test across all conditions, coded
`contrast_type: "omnibus"`. Recording only the contrast you judge most important
guarantees the other reviewer records a different one.

**Effect estimates are likewise plural.** Record every between-group estimate the
paper reports, adjusted and unadjusted alike. A bare P value with no effect
measure is an entry with `type: null` and only `p_value` or `p_value_verbatim`
filled; it is not a reason to leave the array empty.

## Arms

Give each arm a short stable `label` — `pes_guided`, `ardsnet_low_peep`,
`conventional_peep`, `baseline` — and put the paper's own wording in
`label_verbatim`. Every `outcomes[].data[].arm_label` must match one of these
exactly. For within-patient designs the arms are conditions, and `role` is
`reference_condition` for the pre or conventional state.

## The intervention section

This section carries the heterogeneity that will dominate the discussion, so it
deserves the most care.

**Calibration.** `baydur_occlusion_test` is the occlusion manoeuvre comparing
airway and esophageal pressure swings during spontaneous or manual effort.
`dynamic_occlusion_test` is the equivalent in passive patients. `minimal_volume`
means the smallest filling volume giving a stable signal.
`best_compliance_or_pressure_volume_filling` covers filling chosen from a
balloon pressure–volume curve. `fixed_volume_no_validation` is a stated filling
volume with no occlusion test and no acceptance criterion — common in older
obesity reports. `signal_quality_verification_only` is the converse: no filling
calibration reported, but validity checked by cardiac oscillations or a gastric
pressure rise, with the detail in `position_verification`. Reserve
`not_reported` for silence on both. When the paper describes a method without
naming it, code the mechanism and put the description in `method_verbatim`.

**Transpulmonary pressure computation.** This is the sharpest methodological
divide in this literature and the axis of the `ptp_target_subgroups`.

- `absolute_pes_subtraction` — PL = Paw − Pes. The Talmor and EPVent approach.
- `elastance_derived` — PL = Paw × (E_lung / E_respiratory_system).
- `release_derived` — PL from airway pressure at zero flow after PEEP release.
- `both_absolute_and_elastance` — the paper reports and uses both.

**PEEP target.** Record the numeric rule, not a summary of it. EPVent's target
of end-expiratory transpulmonary pressure between 0 and 10 cmH₂O becomes
`strategy: end_expiratory_ptp_positive_range`, low `0`, high `10`, with the
protocol sentence quoted.

**Timing.** `hours_from_reference` plus `reference_event` feed the timing
subgroup. If the paper says only "on admission to ICU", set
`reference_event: icu_admission`, leave `hours_from_reference.value` null, and
put the phrase in `timing_verbatim`.

## Outcomes

One entry per outcome **per timepoint**. A trial reporting mortality at 28 and 60
days produces two outcome entries, not one entry with two timepoints.

**Mortality.** Exactly one mortality entry per study sets
`is_longest_reported_mortality: true` — the longest available follow-up, which is
what the protocol pools. Rank by actual duration: 1 year beats 90 days beats 60
days beats 28 days. For the fixed-day versus discharge tiebreak, see the ruling
under *Rulings on ambiguous cases*.

**Ventilator-free days.** Definitions vary in ways that matter — the window (28
versus 60 days) and the handling of death (usually zero, occasionally excluded).
Always fill `definition_verbatim`. Record the window in
`timepoint.days`.

**Physiologic outcomes.** Use `respiratory_mechanics` for compliance, elastance,
driving pressure, and plateau pressure; `transpulmonary_pressure` for end-
expiratory, end-inspiratory, and driving transpulmonary pressure;
`esophageal_pressure` for the measured pleural surrogate itself, which is a
distinct quantity; `oxygenation` for PaO₂/FiO₂, PaO₂, and oxygenation index;
`lung_volume` for EELV and functional residual capacity; `dead_space` for Vd:Vt;
and `ventilator_settings` for delivered PEEP, FiO₂, tidal volume, and rate —
those describe the titration rather than the patient's response.

Keep `ventilator_free_days`, `icu_free_days`, and `organ_failure_free_days`
separate from their length-of-stay counterparts. They run in opposite directions,
and coding a free-days outcome as a length of stay would invert the sign of the
effect downstream. Put the measurement conditions in `measurement_context` — whether the patient was paralysed, and how
long each PEEP level was held, determine whether two studies are measuring the
same thing.

**Effect estimates.** Transcribe only. If the paper reports no between-group
estimate, leave `effect_estimates` empty; the meta-analysis will compute one
from the arm-level data.

## Feasibility and safety

`complications_explicitly_none: true` requires a negative statement covering the
balloon, the manometry, or the strategy as a whole — a general "no adverse events
in either group" qualifies. Silence is `null`. This distinction is what separates
a study that looked and found nothing from a study that did not look.

`n_measurement_not_obtainable` covers a balloon placed successfully that yielded
no usable signal — inadequate sedation, artefact. That is neither a placement
failure nor a protocol deviation.

Record only complications attributable to the balloon or its placement here.
Barotrauma from ventilation belongs in `outcomes` under the `barotrauma` domain.

## Subgroup flags

These are your classification, made once, so the analysis can filter rather than
re-read. Each must be defensible from data recorded elsewhere in the record; put
the reasoning for any borderline call in `classification_notes`.

- `obesity_subgroup` is `obese_population` when enrolment required obesity or the
  reported mean or median BMI is at least 30 kg/m².
- `timing_subgroup` uses 48 hours from the reference event as the cut.
- `ptp_target_subgroups` follows `intervention.ptp_computation.method` and the
  target strategy together. It is an array: a study applying two titration
  strategies to the same patients contributes to both subgroups.

## Cohort overlap

The most consequential fields in this record are
`extraction_flags.cohort_id` and `extraction_flags.overlapping_cohort_with`.

Overlap in this pool is not a rare edge case. A first pass over the 33 candidate
PDFs found five clusters of reports that share patients — see
`studies/COHORTS.md`. Pooling two reports of one cohort double-counts those
patients and falsely narrows the confidence interval.

`cohort_id` is a short lowercase label for the *underlying group of patients*,
not for the paper: `epvent1`, `epvent2`, `mgh_lung_rescue`. Every report of that
cohort carries the same label, and exactly one of them sets
`citation.is_primary_report: true`. `overlapping_cohort_with` then lists the
other reports by `study_id`.

Before leaving `overlapping_cohort_with` empty, check three things: the trial
registration number, the enrolment dates, and the participating centres. Any
match is evidence of overlap. Record what you compared in `overlap_evidence`,
even when you conclude there is none — a stated negative is a finding; silence
is not.

The validator enforces the relation across a directory of records: overlap must
be declared in both directions, both records must carry the same `cohort_id`,
and each cohort must have exactly one primary report.

## Rulings on ambiguous cases

Each of these was a genuine coin-flip in pilot extraction, meaning two competent
reviewers would split on it and `diff.py` would report a disagreement that is
really an underspecified form. Follow the ruling even where you would have chosen
otherwise; consistency between the reviewers matters more than the choice.

**Baseline values belong in `outcomes`.** Record pre-intervention physiologic
measurements as outcome entries with `timepoint.type: "baseline"`. They are the
comparator for every later measurement. A trial reporting eighteen Table 2 rows
at baseline and at 72 hours yields thirty-six entries, not eighteen.

**Mortality ranking.** Choose `is_longest_reported_mortality` by actual elapsed
duration. When a fixed day and a discharge-based outcome are both reported and
their durations are not comparable, prefer the fixed day. Hospital mortality
outranks ICU mortality only when neither has a fixed-day alternative. State the
choice in `subgroup_flags.classification_notes`.

**`complications_explicitly_none`** is `true` only when the paper makes a
negative statement covering the balloon, the manometry, or the strategy as a
whole. A general "no adverse events in either group" qualifies; silence does not.

**`adults_18_plus`** where no explicit age criterion is stated but the setting is
plainly an adult ICU: code `yes` and add the field to `unclear_items`. Do not
code `no` — that would exclude the study on an inference.

**`is_primary_outcome`** marks the specific entry matching the paper's stated
endpoint, timepoint included. If the endpoint is PaO₂:FiO₂ at 72 hours, only the
72-hour entry is primary; the baseline entry is not.

**`timing_subgroup` is `unclear` when timing is known only relative to
enrolment.** In a randomised trial the intervention always begins at enrolment,
so `reference_event: enrolment` carries no information about how early in the
illness the balloon was placed. Reserve `early_within_48h` and `late_after_48h`
for intervals measured from intubation, ICU admission, or ARDS onset.

**Logical implications are derivations.** If ARDS is an exclusion criterion, then
`n_ards` is `0` with `derived: true` and the criterion quoted as the derivation —
not `null`, because the paper does tell you, and not a bare `0`, because it never
prints one. The same applies to any count a protocol rules out by construction.

**`reports_eligible_outcome` for physiologic studies.** The protocol admits
physiologic studies reporting "clinically relevant" outcomes without defining the
term. Treat as clinically relevant: respiratory system or lung compliance,
driving pressure, transpulmonary pressure, oxygenation, lung volume, dead space,
and haemodynamics. Code `yes` on any of these even where no mortality, ventilator-
free days, or length of stay is reported.

**`is_primary_report` versus `cohort_id`.** `is_primary_report` is about the
cohort, not the research programme. One group publishing several distinct
datasets produces several primary reports; they simply carry different
`cohort_id` values, or none.

## Adjudication

`diff.py` reports every field where the two extractors disagree. The human
adjudicator writes the resolved record to `extraction/reconciled/<study_id>.json`
with `record_meta.extractor: "reconciled"` and a note in
`record_meta.adjudication_notes` describing what was disputed and why the
resolution was chosen. Quotes make this fast: in most disagreements one
extractor's quote plainly does not support its value.
