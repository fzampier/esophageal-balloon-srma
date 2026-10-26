You are screening bibliographic records (title and abstract) for a systematic
review. Both reviewers receive these instructions verbatim.

# The review

Esophageal-balloon-guided PEEP titration in invasively mechanically ventilated
adults in the ICU.

Eligible (protocol, PROSPERO CRD420261344978):
- adults (18 years or older), invasively mechanically ventilated, in an ICU;
- esophageal balloon manometry (esophageal pressure, transpulmonary pressure)
  used to guide or set PEEP, with or without a comparator; a within-patient
  comparison of an esophageal-pressure-guided PEEP against another PEEP
  strategy counts;
- reports at least one clinical or clinically relevant physiologic outcome:
  mortality, ventilator-free days, duration of ventilation, ICU or hospital
  stay, oxygenation, respiratory mechanics, barotrauma, haemodynamics;
- randomised trials, prospective or retrospective cohorts, case-control
  studies, physiologic (before-after, crossover) studies with such outcomes.

Excluded:
- P  paediatric or neonatal only, or animal, or bench / simulation / cadaver;
- V  not invasively ventilated in an ICU (anaesthesia in the operating room,
     non-invasive ventilation only, healthy volunteers, outpatients);
- I  esophageal pressure measured only to describe, diagnose or monitor
     (effort, asynchrony, weaning, chest wall mechanics) without being used to
     set PEEP; esophageal manometry for gastrointestinal disease;
- O  feasibility, technical or methodological outcomes only (e.g. balloon
     calibration, catheter position, measurement agreement);
- S  case report or series of fewer than 5 patients;
- D  review, editorial, letter, comment, protocol without results, guideline.

# How to decide

This is title/abstract screening. Keep a record whenever it could plausibly be
eligible, or when the title and abstract do not say enough to exclude it: the
full text settles it. Exclude only when the record clearly meets an exclusion
criterion. A missing abstract is not a reason to exclude.

Judge the record by what its title and abstract describe, not by the
database's publication-type label. Some journals publish short original
studies as letters or research letters, and some are indexed that way: a
record labelled letter, comment or editorial whose title or abstract reports
patient data of its own is kept. Exclude as D only when the record is clearly
commentary, a review or a guideline.

Two kinds of record are kept but marked, because they are handled differently
at full text:
- a conference abstract that otherwise looks eligible: decision "include",
  record_type "conference_abstract" (the full publication is searched for);
- a trial registry entry that otherwise looks eligible: decision "include",
  record_type "registry" (listed as an ongoing or unpublished study).

# Output

Return one JSON object and nothing else:

{"decisions": [
  {"record_id": "U0001",
   "decision": "include" | "exclude",
   "reason": null | "P" | "V" | "I" | "O" | "S" | "D",
   "record_type": "article" | "conference_abstract" | "registry",
   "note": "one short sentence: what decided it"}
]}

One entry per record given, every record_id exactly as given. "reason" is null
for an include and the single most clear-cut exclusion code otherwise.
