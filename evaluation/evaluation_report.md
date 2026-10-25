# Evaluation report

All numbers are produced by `python3 code/evaluation/main.py --reports` (deterministic; no LLM involved).

## 1. Accuracy against the public samples (`dataset/sample_output.csv`)

The 32 samples are two families: `case_01`-`case_10` are hand-built, screenshot/PDF-centred stories with their own resource-id style (legacy), `case_11`-`case_32` follow the generator used for the 518 evaluation requests. We report both.

| Metric | all 32 | case_11-32 |
|---|---:|---:|
| Case-level `deletion_status` accuracy | 100.0% | 100.0% |
| Row exact match (status + every step + evidence id) | 90.6% | 90.9% |
| Step precision (exact tuple) | 100.0% | 100.0% |
| Step recall (exact tuple) | 98.4% | 98.8% |
| Step F1 | 99.2% | 99.4% |
| Step F1 ignoring evidence_id | 99.2% | 99.4% |
| evidence_id accuracy on matched steps | 100.0% | 100.0% |
| Sequence accuracy (exact ordered list of steps) | 90.6% | 90.9% |
| Sequence accuracy (LCS ratio) | 98.4% | 98.8% |

Field-level accuracy (position-aligned steps; a missing or extra step counts as wrong):

| Field | all 32 | case_11-32 |
|---|---:|---:|
| `phase` | 98.4% | 98.8% |
| `system_id` | 95.9% | 96.3% |
| `resource_id` | 95.9% | 96.3% |
| `operation` | 95.9% | 96.3% |
| `state` | 95.9% | 96.3% |
| `evidence_id` | 97.9% | 98.8% |

## 2. Output validation

`python3 code/evaluation/validate_output.py` on the final `output.csv`: **all structural checks passed** (518 rows sorted by case_id, valid JSON arrays, consecutive step numbers, phase order, allowed operation/state pairs, intra-phase ordering, evidence ids exist and are not later than `audit_at`, future steps carry an empty evidence id, NOT_APPLICABLE rows are `[]`).

## 3. Additional evaluation cases written by us

`code/evaluation/custom_cases.py` builds a fictional mini-dataset and runs the real agent on it (`GHOST_DATASET` override).

| Case | Behaviour tested | Result |
|---|---|---|
| X1 | request with the OLD e-mail; analytics record keyed only by the old e-mail hash | PASS |
| X2 | leftover record only in a snapshot captured after audit_at is ignored | PASS |
| X3 | tombstone keeps the stable user reference but still exposes direct identifiers -> REDACT; audit record retained | PASS |
| X4 | access/export request that says 'do not delete' -> NOT_APPLICABLE, `[]` | PASS |
| X5 | active connector + restorable backup -> disable flow first, then block restore | PASS |
| X6 | profile removed on primary but present on the read replica (replication drift) | PASS |
| X7 | everything already compliant -> COMPLETE with verification-only steps | PASS |

## 4. Failures found and improvements made

| # | Failure observed (against samples / consistency checks) | Fix |
|---|---|---|
| 1 | First version scored 19/32 exact rows: absent accounts and retained audit records were dropped from verification when the evidence file never listed them | Resources tied to the subject but unobserved at `audit_at` get a *policy default*: a deleted account is ABSENT (a live one would carry identifiers) and a retained audit/legal record stays PRESENT |
| 2 | A subject with two requests (different audit times) leaked resources of the later request into the earlier one | Multi-request subjects keep only resources first evidenced inside `[requested_at-3d, audit_at]` |
| 3 | Legacy-style worker events were being claimed by regular cases (and vice versa) | Legacy events (`Target resource:` wording, stamped minutes from `reported_at`) are matched first, only to cases without evidence |
| 4 | Account ids of fully-deleted people are not in any evidence file by identifier | Closed-world elimination on the activity stream: same reported day, result mapped from the workflow claim (COMPLETE->SUCCESS, PARTIALLY_COMPLETE->PARTIAL, NOT_STARTED->FAILED), minus ids already tied to someone else |
| 5 | Intent classifier first marked 'verify my deletion' follow-ups as non-deletion | Default is *deletion*; NOT_APPLICABLE requires an explicit keep/negation, a marketing-only opt-out, or a pure non-deletion ask without a deletion verb; cross-checked with the privacy-intake `CLASSIFY_REQUEST` events (10/10 agree) |
| 6 | Screenshots/PDFs gave no usable text | OCR with 3x upscaling cached under `code/ghost/ocr_cache/`; slash/space artefacts repaired; pages mapped to resource kinds; catalog `LINK_RESOURCE_IDENTITY` rows give the explicit person link |
| 7 | 155 audits had an id picked by chance among same-day candidates (`case_03` was wrong: two people, two anonymised analytics copies, no identifier) | **Shared event clock**: the worker events of one request keep a constant seconds-of-minute offset per event type (learned from requests whose account is tied by an identifier). Candidates are filtered by that offset against the request's other events (and, for analytics, against the account event). Ambiguous audits fell from 155 to 18; hide-the-link experiment recovers ~88% of accounts (`masking_test.py`) |
| 8 | Screenshot stories consumed same-day account events of log-based requests (`case_255` lost its account) | Media-primary requests are excluded from the regular same-day pool |
| 9 | Legacy samples `case_01/02/04/07` missed the account snapshot citation, the PDF owner and the legacy analytics copy | Latest account-snapshot citation for evidence-less requests; single-subject PDFs carry their owner's identifiers; legacy analytics event stamped at `reported_at` |
| 10 | Guesses with a <1/3 chance add noise | Picks among four or more equally plausible candidates are withheld and flagged (`MIN_PICK_CONFIDENCE`) |


## 5. Known limits

* **Ids that are in no file.** About 86 of 508 deletion requests lack their retained-audit-record verification step: the id never appears with an identifier anywhere (collectors report `dropped=0`; the 196 archival events are unrelated noise). This is proven in `limits_and_ceiling.md`; we omit the step rather than invent an id. The same applies to a handful of screenshot stories without an account id.
* **Ties.** 18 requests have an account id that is a coin-flip among same-day candidates (no timed peer to apply the event clock to); they are flagged `ambiguous` in `audit_trail.jsonl`.
* **Estimated ceiling for a data-only solver:** step F1 about 98%, status accuracy about 100% (derivation in `limits_and_ceiling.md`); we are within about a point of it on the public samples.
* **Screenshots** are read with OCR; layout-specific parsing is generic (resource-id regexes, page-type keywords) but a new console design would need a mapping entry.
* Public samples are only 32 requests (22 representative of the evaluation generator), so the accuracy above is an optimistic estimate.
* No LLM is used: all decisions are rule-based and reproducible.


## 6. confidence_v2: a review-priority signal (not a probability)

`confidence_v2` (`code/ghost/confidence.py`) multiplies hand-set per-resource link reliabilities (tie 1/k, direct identifier 0.995, catalog/LINK 0.99, indirect 0.97, x0.93 for an inferred state) and drops to 0 for unresolvable or degraded cases. It only orders the review queue; it never changes `output.csv`. On the public samples:

| confidence_v2 | Samples | Coverage | Exact rows |
|---|---:|---:|---:|
| >= 0.00 | 32 | 100.0% | 90.6% |
| >= 0.90 | 23 | 71.9% | 95.7% |
| >= 0.95 | 11 | 34.4% | 90.9% |
| >= 0.98 | 8 | 25.0% | 100.0% |

With only 3 wrong rows in 32 samples this is a sanity check, not a calibration: it does not support a '99%' claim. Weights were fixed by hand, not fitted. A separate perturbation experiment (results quoted in `code/docs/CONFIDENCE.md`) found that cases with confidence_v2 >= 0.98 keep their answer in 97.6% of perturbed re-runs, while cases below 0.95 change in 46%.

