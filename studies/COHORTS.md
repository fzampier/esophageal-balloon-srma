# Cohort overlap in the candidate pool

Reports that share patients must contribute to a pooled estimate at most once.
Each cluster below gets one `cohort_id`; every record in it carries that label,
and exactly one record sets `citation.is_primary_report: true`.

These clusters come from a first pass over the 33 PDFs and are **provisional**.
Confirm each during extraction against registration number, enrolment window,
and participating centres, then record the evidence in
`extraction_flags.overlap_evidence`.

## Confirmed from the text

**`epvent2`** — `beitler_2019` (primary, JAMA, n=200) and `sarge_2021`, which
states it is a post hoc reanalysis of EPVent-2 including all 200 participants.

**`epvent1`** — `talmor_2008` (primary, NEJM, n=61, NCT00127491) and
`baedorf_kassis_2016`, which states that 56 patients from a previous study were
analysed.

## Suspected — verify before pooling

**`mannheim_boesing`** — `boesing_2022` (n=40, DRKS00017449, enrolled July 2019
to February 2021) and `boesing_2024b` (n=55, the **same registration**
DRKS00017449, enrolled July 2019 to June 2023) share a centre, a registration and
a supine/prone esophageal-pressure-guided protocol; the later sample very likely
subsumes the earlier one. `boesing_2024a` is the same group's VV-ECMO study
(n=20, DRKS00013967 — a different registration), so it is probably a distinct
cohort. *Corrected 2026-09-15:* an earlier version of this paragraph had the two
2024 papers swapped (2024a described as the n=55 prone study, 2024b as the ECMO
study); GLM followed it and linked `boesing_2022` to the ECMO paper.

**`mgh_lung_rescue`** — `fumagalli_2019` and `florio_2020` remain to be checked
against each other. `pirrone_2016` has been **removed from this cluster**; see
below.

**`angers`** — `pavlovsky_2024` and `chean_2025` come from the same Angers ICU
with substantially shared authorship. Pavlovsky additionally states that 10 of
its COVID-ARDS patients were published earlier, in a paper not present in this
folder. **Settled 2026-09-19: not a cluster.** Asked directly, the authors confirmed that no
patient was included in both studies (C23). Each report is
its own cohort and its own primary.

## Refuted on evidence

**`pirrone_2016` is not part of `mgh_lung_rescue`.** It was grouped there on the
strength of shared institution, authors, and an obesity population. Running the
three checks the codebook requires refutes all three grounds:

- Registration differs. Pirrone is NCT02105220 / IRB 2013P001413; Fumagalli is
  NCT02503241 / IRB 2015P001515.
- Enrolment does not overlap. Fumagalli enrolled April 2016 to July 2017, after
  Pirrone was published.
- The eligibility criteria are contradictory. Pirrone *excludes* ARDS; Fumagalli
  *requires* it. Florio requires ARDS plus BMI above 40 and states that none of
  its patients participated in any ongoing MGH ARDS interventional trial.

This is worth keeping visible as a worked example: a cluster asserted from author
names and institution did not survive the registration-and-dates check. Treat
every cluster above the same way until confirmed.

## Considered and judged unlikely

`scaramuzzo_2020` and `mauri_2024` share Italian investigators but differ in
population (controlled ventilation versus pressure support) and period. Recorded
here so the negative is documented rather than assumed.

## Eligibility questions raised by the same pass

Not overlap, but flagged during identification and to be settled at full-text
screening:

- `thind_2022` — MIMIC-III retrospective analysis in which EPVent-2 PEEP
  protocols were applied only in simulation. No intervention was delivered, so
  it likely fails `balloon_guided_peep_titration`.
- `gluck_1995` — esophageal manometry used to guide *weaning*, not PEEP
  titration.
- `tonelli_2023` — index population is acute exacerbation of interstitial lung
  disease (n=10) with 10 matched ARDS controls.
- `soroksky_2015` — n=6 uncontrolled before/after; meets the n≥5 threshold but
  sits at the case-series boundary.
- `wang_2018` — uncorrected galley proof headed 2018; the final published year
  may be 2019. Confirm before citing.
