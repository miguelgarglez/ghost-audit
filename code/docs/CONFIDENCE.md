# confidence_v2: how certain is each answer?

**Short version:** use `confidence_v2` to decide **which cases a human looks at first**. It is a review-priority signal,
**not a calibrated probability**. Its weights were set by hand, not fitted to data. The public samples contain only 3 wrong rows
out of 32, which is far too few to back a claim like "99% accurate". `output.csv` never depends on it; a test proves this.

## Where it appears

| Surface | Fields |
|---|---|
| `ghost/agent.py` review signals (CLI `case`, `explain`, `cases`, `queue`; MCP `ghost_get_case`, `ghost_explain_case`, `ghost_list_cases`, `ghost_review_queue`) | `confidence_v2`, `confidence_reasons`; the original `confidence` (link tie-break `1/k`) is kept unchanged for compatibility |
| `audit_trail.jsonl` (written by `code/evaluation/main.py --reports`) | `confidence_v2`, `confidence_reasons` per request |
| `queue --sort confidence_v2` / `ghost_review_queue(sort="confidence_v2")` | least certain first. With the default `priority` sort, `confidence_v2` breaks ties |
| `whatif --min-confidence-v2 Y` / `ghost_whatif(min_confidence_v2=Y)` | which cases a review threshold would route to a human; a routing simulation only, with statuses and steps unchanged |

## Method

Every resource in a case was tied to the subject by some signal. Each signal gets a fixed reliability, chosen from what it means:

| How the resource was linked | Weight |
|---|---:|
| Pick among `k` equally plausible same-day candidates (tie) | `1/k` |
| Direct identifier in the evidence (e-mail, hash, phone, user id) | 0.995 |
| Data-catalog row / `LINK_RESOURCE_IDENTITY` activity | 0.99 |
| Indirect: closed-world elimination, event clock, identifier history | 0.97 |
| Multiplier when the state at `audit_at` is inferred (no evidence captured before `audit_at`) | x0.93 |

The case value is the product over its resources, so every extra uncertain link can only lower it (monotone). Notes override the product:

* `subject not resolvable`, `degraded input` and `audit failed` give 0;
* a withheld pick caps the value at 0.25.

A request that does not authorise deletion has no resources and gets 0.97, the reliability of the intent classifier alone.
`confidence_reasons` lists every discount. Code: `code/ghost/confidence.py`.

## What the experiment showed

These figures come from the exploration run before integration. The sample table is recomputed on every
`python3 code/evaluation/main.py --reports` run (section 6 of `evaluation_report.md`).

**Reliability on the public samples (32 cases, 29 exact rows):**

| confidence_v2 bin | Samples | Mean confidence_v2 | Exact rows |
|---|---:|---:|---:|
| [0.80, 0.90) | 9 | 0.863 | 77.8% |
| [0.90, 0.95) | 12 | 0.928 | 100% |
| [0.95, 0.98) | 3 | 0.967 | 66.7% |
| [0.98, 1.00] | 8 | 0.985 | 100% |

Expected calibration error is 0.083. The original `confidence` was 1.0 on all 32 samples, so it could not separate anything (ECE 0.094).

**Coverage vs accuracy (samples):**

| Threshold | Coverage | Exact rows |
|---|---:|---:|
| all | 100% | 90.6% |
| >= 0.90 | 72% | 95.7% |
| >= 0.95 | 34% | 90.9% |
| >= 0.98 | 25% | 100% |

**Stability under perturbation (all 550 cases).** The experiment ran 30 times, each run randomly dropping a small share of activity rows
and evidence observations. It measured how often a case's status and steps changed against the unperturbed run:

* cases with confidence_v2 >= 0.98 kept their answer in **97.6%** of runs;
* cases below 0.95 changed in **46%** of runs, against 12.5% for those at or above 0.95.

The ordering is real: low values mark answers that rest on fragile evidence.

## Limits

* **Not calibrated.** The bins are non-monotone: [0.95, 0.98) holds 3 samples with 2 correct. With 3 errors, any accuracy
  claim has a confidence interval wider than the differences between bins.
* **Hand-set weights.** They encode an ordering of evidence strength (identifier > catalog > indirect > tie). They were not tuned to
  the samples, which would be overfitting with 32 cases.
* **Blind to some failures.** All 3 sample errors come from a retained audit-record id that no file contains (see
  `limits_and_ceiling.md`). No link signal can see that kind of omission.
* **Perturbation is a proxy.** Stability under dropped rows is not correctness. It shows which answers depend on few observations.

## Future work: an LLM only as a tie-breaker (design, not shipped)

An optional language model could help with the one place the rules cannot decide: a tie among `k` grounded same-day candidates.
The design is:

* **opt-in and off by default**: no network call unless explicitly enabled;
* **constrained**: it may only choose among candidates the deterministic engine already grounded in evidence, and it can never
  add a resource, an id or a step;
* **logged**: prompt, candidates, answer and model id are recorded in the audit trail;
* **safe on failure**: a timeout, refusal or invalid answer falls back to the current deterministic behaviour (pick, or withhold below
  `MIN_PICK_CONFIDENCE`), with the case flagged for review.

No such component ships in `code.zip`, and nothing in the delivery calls an LLM.
