# Robustness to unseen and malformed input

Ghost Audit uses no LLM at run time, so the obvious question is: what happens with inputs it has never seen?
The design answer is **fail safe, never fail silent**:

1. **The run never crashes because of one odd file, row or case.** Isolation is per evidence file (`ghost/evidence.py::load_all`),
   per CSV row (`ghost/dataset.py::read_csv`, `ghost/world.py`) and per case (`ghost/audit.py::Auditor.prepare/plan`).
2. **`output.csv` is always valid**: one row per request, sorted, accepted by `code/evaluation/validate_output.py`.
3. **What cannot be audited is never guessed.** The case is answered `INCOMPLETE` with `steps_json=[]`: no invented ids,
   and never `COMPLETE` without proof. It is flagged `needs_manual_review` with a `review_reasons` entry (CLI `case` /
   MCP `ghost_get_case`, `queue`).
4. **Every degradation is reported.** `code/main.py` prints `[ingest] ...` lines on stderr. `python3 code/ghost_cli.py ingest-report`
   (MCP: `ghost_ingest_report`) returns them as JSON: unparsed files, repaired files, CSV issues, ignored rows and log lines,
   and degraded cases. On the shipped dataset the report is empty and `output.csv` is byte-identical to the version without these guards.

## Adversarial harness

`python3 code/tools/stress.py` (about 40 s; `--only NAME`, `--json`, `--keep`) copies `dataset/` to a temporary directory,
applies one perturbation and runs the real pipeline with `GHOST_DATASET` pointing at the copy. It checks:

* exit code 0;
* the validator passes;
* a scenario-specific expectation:
  * `same`: byte-identical to the unperturbed run;
  * `diag`: the oddity appears as an `[ingest]` diagnostic;
  * `review`: the affected case is flagged `needs_manual_review`.

`code/tests/test_robustness.py` runs every scenario except the 10x-volume one inside the unit-test suite.

| # | Scenario | Expectation | What the system does |
|---|---|---|---|
| 1 | Request e-mail upper-cased and padded with spaces | same | Identifiers are trimmed and lower-cased before matching |
| 2 | `+tag` alias of a known address | diag | Not merged (it is a different mailbox). The case is unresolvable and goes to review with a hint naming the candidate `user_id` |
| 3 | Unicode homoglyph in the e-mail | diag | Not merged. Review reason "possible homoglyph" |
| 4 | Phones re-formatted (`+3461…`, `+34-6128-…`) | same | Phones are compared on digits only |
| 5 | Identity chain of 3+ e-mail changes | diag | Every historical value resolves to the same `user_id`. Records already owned by the subject's earlier request are not reassigned, so the new request is flagged |
| 6 | Duplicate request rows | diag | First row kept, duplicate reported |
| 7 | Request whose e-mail matches no user | review | `INCOMPLETE`, `[]`, "subject not resolvable" |
| 8 | Known user with no evidence at all | diag | `INCOMPLETE`, `[]`, "no grounded resource or check found". Never `COMPLETE` without proof |
| 9 | Unknown data category in `deletion_rules.csv`; a known category's rule row removed | diag | Drift between the table and the compiled policy is reported; known categories keep their compiled treatment |
| 10 | Unknown `reported_status` values | diag | Reported. The claim was never used as proof anyway |
| 11 | Empty evidence files (JSON, SQL, log, txt) | diag | Skipped as `unparsed_files`; the rest of the run continues |
| 12 | Randomly corrupted bytes in evidence | diag | Invalid UTF-8 replaced and reported; unparseable files skipped |
| 13 | Truncated evidence files | diag | Same as 12 |
| 14 | Evidence file listed in the manifest but missing on disk | diag | Skipped and reported |
| 15 | Evidence with an unknown extension | diag | "no parser for '.xyz' files" |
| 16 | Log lines in an unknown dialect, JSON lines, binary garbage | diag | Counted as ignored lines per file. Known lines are still parsed |
| 17 | Dates as `+00:00` offsets and `YYYY-MM-DD HH:MM:SS` | same | Canonicalised to UTC (`ghost/timeutil.py`). Naive times are taken as UTC |
| 18 | Unparseable dates (`next tuesday`) | review | No reliable cut-off, so `INCOMPLETE`, `[]`, review |
| 19 | Evidence captured after `audit_at` | (valid) | Never used for state, never cited (already a core rule) |
| 20 | Missing `audit_at` | review | As 18 |
| 21 | CSVs with reversed column order plus extra columns | same | Columns read by name; extras ignored |
| 22 | UTF-8 BOM and CRLF line endings | same | `utf-8-sig`; the csv module handles CRLF |
| 23 | Missing `request_text` column | review | Filled empty and reported. Deletion is not authorised without text (`NOT_APPLICABLE`, nothing touched) and the case is flagged |
| 24 | 0 requests | (valid) | Header-only `output.csv` |
| 25 | 1 request | (valid) | Works |
| 26 | 10x requests (5,180) | (valid) | About 23 s end to end, roughly linear |
| 27 | JSON evidence with extra and `null` fields | (valid) | Unknown keys ignored; nulls treated as absent identifiers |
| 28 | `case_id` values padded with spaces | same | Ids trimmed |
| 29 | `user_audit_log.csv` missing | diag | Treated as empty (current identities only) and reported |

## What it cannot do (honest limits)

* **A completely new evidence layout or log dialect is not understood.** It is detected (unparsed file, ignored lines) and reported,
  but its content cannot change a decision until a parser is added. An affected case can therefore lose findings: the report says
  which files were skipped, but it does not yet mark every case that might have depended on them.
* **New data categories need a policy entry.** Treatments are compiled from `deletion_rules.csv` into `ghost/policy.py`. An unknown
  category is reported, not guessed.
* **New identity semantics are not inferred.** A `+tag` alias, a homoglyph or a typo is never merged automatically: merging the
  wrong person is worse than asking a human. The review reason names the candidate.
* **New request phrasing** in a language without deletion keywords defaults to deletion only if the text has no negation,
  access/export or opt-out cue (`ghost/intent.py`). A truly novel non-deletion request could be misclassified; the
  `CLASSIFY_REQUEST` events in the activity stream are the safety net.
* Robustness is not accuracy: the harness proves the system degrades safely, not that a degraded answer is correct.

## How to extend

* **New evidence format:** write `parse_<name>(file_id, captured_at, data) -> List[Obs]` in `ghost/evidence.py` and dispatch it in
  `load_all`. Everything downstream (linking, state at `audit_at`, policy, plan) works on `Obs` and needs no change.
* **New log dialect:** add a branch to `ghost/logparse.py::parse_line` that returns the same flat dict (`ts`, `trace_id`, `event`, key/values).
* **New data category or rule:** add the category to `ghost/model.py` and its `(action, violating state)` to `ghost/policy.py::_BY_KIND`;
  `test_every_deletion_rule_category_is_handled` checks the table is covered.
* **New adversarial case:** add a `@scenario("name", "same"|"diag"|"review")` function to `code/tools/stress.py`.
