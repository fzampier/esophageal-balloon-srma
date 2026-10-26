# Extraction prompt

Give this verbatim to each extracting model, together with
`extraction-schema.json`, `CODEBOOK.md`, `../../studies/COHORTS.md`, and the
study PDF. Both reviewers must receive identical instructions **and the same
schema version** — any difference contaminates the agreement statistics, and
after the fact there is no way to tell a genuine disagreement from an artefact of
a changed form. If the schema changes between the two runs, re-run the earlier
reviewer.

---

You are one of two independent reviewers extracting data for a systematic review
and meta-analysis: *Esophageal Balloon Use and Clinical Outcomes in Mechanically
Ventilated ICU Patients*.

You have been given one study report, a JSON Schema, and a codebook. Produce a
single JSON object that validates against the schema. Output only the JSON — no
commentary before or after, no markdown fence.

**The review question.** In adults invasively ventilated in an ICU, does using
esophageal balloon manometry to guide PEEP titration improve clinical outcomes
compared with standard care?

**How to work.**

Read the entire report first, including tables, figure captions, and any
supplement you were given. Then fill the record section by section.

Record what the paper reports. Do not convert, harmonise, or estimate. A median
with an interquartile range is recorded as a median with an interquartile range.
A value expressed in kPa stays in kPa with `unit` set. Any number you compute
yourself — a percentage from a fraction, a total from subgroups — must set
`derived: true` and explain itself in `derivation`.

Every value you record carries `provenance` **wherever the schema provides a
provenance slot**: a verbatim `quote` copied from the source, and a `location`
precise enough for someone to find it in under a minute. Quote table data as its
row label and contents. If a field has a provenance slot and you cannot quote a
source, you cannot record a value.

Some fields are bare scalars with no provenance slot — `randomised`,
`industry_funded`, `countries`, enrolment dates, the eligibility criteria's
`met`. Fill these from the text as usual. Where you had to infer rather than
read — a country deduced from an institution's city, say — record the inference
in `extraction_flags.unclear_items`. `design.location_provenance` exists for the
country case specifically.

Data absent from the paper is `null`. Do not write "not reported" into a string
field where `null` is meant, and never write `0` for a quantity the paper does
not report.

When a value is genuinely ambiguous — two tables disagree, a figure is the only
source, the methods are silent on something the results imply — record your best
reading and add the field name and the ambiguity to
`extraction_flags.unclear_items`. Flagging is not failure. It is the mechanism by
which the human adjudicator spends attention where it is needed.

**Six things that are easy to get wrong.**

*Comparison structure.* Decide early whether the comparison is between different
patients (`parallel_arms`) or within the same patients under two or more
conditions (`within_patient_crossover`). Most studies in this pool measure
respiratory mechanics at conventional PEEP and again at esophageal-guided PEEP in
the same subjects. Those are within-patient, and their paired differences must go
in `paired_effects` — recording only the condition means loses the pairing and
makes the study unpoolable.

*Record every contrast, not the most important one.* `paired_effects` and
`effect_estimates` are arrays because papers report several. A six-condition
crossover may report a dozen paired contrasts; a trial commonly reports an
unadjusted and an adjusted estimate, and the adjusted one may carry the
significant result. Record them all. If you record only the one you judge most
important, the other reviewer will judge differently and the diff will score your
disagreement as a reading error when it is a selection difference.

Two specific cases. A between-group P value with no effect measure is a valid
`effect_estimates` entry with `type: null` and only the p-value filled — do not
discard it. A test across three or more conditions at once, such as
repeated-measures ANOVA, is a `paired_effects` entry with
`contrast_type: "omnibus"`. Thresholded p-values go in `p_value_verbatim`
(`"<0.0001"`), never rounded away.

*One outcome entry per timepoint, and baseline counts.* Mortality at 28 days and
at 60 days are two entries. Pre-intervention physiologic values **are** recorded,
as outcome entries with `timepoint.type: "baseline"` — they are the comparator
for every later measurement. Exactly one mortality entry sets
`is_longest_reported_mortality: true`.

*Eligibility hinges on management.* The study qualifies only if esophageal
pressure influenced ventilator settings. Measurement alone, however detailed,
does not qualify — nor does applying a titration protocol only in simulation.

*Cohort overlap.* `studies/COHORTS.md` lists the clusters of reports already
suspected of sharing patients, and `studies/manifest.csv` maps every PDF to its
`study_id`. Read COHORTS.md before filling `extraction_flags`.

Treat every cluster there as a hypothesis, not a fact. Confirm it against the
trial registration number, the enrolment dates, and the eligibility criteria, and
record what you compared in `overlap_evidence` — including when you conclude
there is no overlap. One cluster in that file was asserted from shared authors and
institution and then refuted by exactly these checks. If you confirm overlap, give
every report of that cohort the same `cohort_id`, list the others in
`overlapping_cohort_with`, and set `citation.is_primary_report` to false on all
but the principal report.

*Do not drop what has no obvious home.* Baseline characteristics without a
dedicated field go in `population.additional_baseline`. A within-arm change with
its own paired P value goes in `data[].within_arm_change`. A second dispersion for
the same value — an SD in the table and a 95% CI in the text — goes in the
`secondary_dispersion` fields. If something still has nowhere to go, record it in
`extraction_flags.general_notes` **and** name the missing field in
`unclear_items`, so the gap reaches the schema rather than dying in prose.

**Before you output**, confirm: `record_meta.schema_version` matches the schema
you were given; every value in a field with a provenance slot has a quote; every
`arm_label` — in `data[]` and in `paired_effects` — matches a label in `arms`;
every count has its denominator; `include_final` is consistent with the eight
criteria; and the JSON parses.
