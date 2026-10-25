# Compliance matrix - every requirement of `problem_statement.md`, `README.md` and `AGENTS.md`

Legend: **Where** = file/function that implements it. **Test** = automated check (`make test`, `code/tests/test_regression.py`), the structural
validator (`code/evaluation/validate_output.py`, abbreviated *V*), the evaluation harness (*E*) or *manual* (reviewed, with the evidence stated).
Referenced test names are checked to exist by `test_compliance_matrix_references_existing_tests`.

## A. `problem_statement.md`

| # | Requirement | Where it is met | Test |
|---|---|---|---|
| A1 | One output row per request in `deletion_requests.csv` (518) | `code/main.py::run` | V (row count, id set); `test_output_is_valid_and_deterministic` |
| A2 | Columns `case_id,deletion_status,steps_json`, in that order | `ghost/io.py::write_output` | V (header) |
| A3 | Rows sorted by `case_id` | `write_output` (`sorted`) | V |
| A4 | `deletion_status` in COMPLETE / INCOMPLETE / NOT_APPLICABLE | `audit.py::plan` | V |
| A5 | NOT_APPLICABLE has `steps_json = []` and covers requests that do not authorise deletion (access, correction, export, opt-out) | `intent.py::authorizes_deletion`; `plan` | `test_non_deletion_requests`, `test_multilingual_and_implicit_deletion`, `test_agrees_with_privacy_intake_events`, V |
| A6 | `steps_json` is a valid JSON array; each step has exactly step_no, phase, system_id, resource_id, operation, state, evidence_id | `model.py::Step.as_json` | V (field set) |
| A7 | Steps numbered consecutively from 1 | `io.py::steps_json` | V |
| A8 | Phase order FINDING -> REMEDIATION -> VERIFICATION | `audit.py::plan` | V; `test_plan_order_disable_flow_and_block_restore_first` |
| A9 | Allowed operation/state pairs only (15 pairs), no invented phases | `policy.py` operation tables | `test_operation_table_matches_problem_statement` (parses the statement's table), V |
| A10 | Within a phase: operation-table order, then `system_id`, then `resource_id` ascending | `plan` sort key `(OP_RANK, system, rid)` | V (intra-phase ordering), `test_exact_rows` |
| A11 | COMPLETE: only evidence-backed verification steps | `plan` | V (COMPLETE has only VERIFICATION, evidence ids valid) |
| A12 | INCOMPLETE: findings + complete remediation plan + post-remediation checks | `plan` | V (every remediated record has a finding and a verification) |
| A13 | Findings and COMPLETE verification cite a file_id / activity_id / audit_id | `Finding.file_id`, `plan` | V (id exists in manifest/activity/audit log) |
| A14 | Remediation and future verification of INCOMPLETE use `evidence_id = ""` | `plan` | V |
| A15 | Resolve current and historical identifiers | `dataset.py::Directory`, `world.py` | `test_exact_rows` (e.g. `case_10/12/29`), synthetic X1 |
| A16 | Reconstruct the state at `audit_at`; ignore later evidence; reported statuses are claims | `audit.py::state_of`, `eligible` | `test_late_snapshot_is_never_cited`, V (no evidence after audit_at), synthetic X2 |
| A17 | Apply `deletion_rules.csv` to every relevant record; do not delete retained / redacted / anonymized / tombstone data | `policy.py` | `test_every_deletion_rule_category_is_handled`, `test_retained_audit_record_is_verified_present_never_deleted`, `test_tombstone_is_redacted_not_deleted` |
| A18 | Tombstone keeps the stable reference, removes name/e-mail/phone/hash | `evidence.py::from_collection` (`deleted_customer_references`), `policy.py` | `test_tombstone_is_redacted_not_deleted`, synthetic X3 |
| A19 | COMPLETE only if every copy treated, retained records available, no active flow / restore path | `audit.py::plan` (`violates`) | synthetic X5/X7; samples `case_26-28` in `test_exact_rows` |
| A20 | Report every violation and recreation risk visible in evidence | `plan` (all linked resources) | `test_exact_rows`, synthetic X5/X6 |
| A21 | Disable flows and block restores before changing dependent records; downstream before source | `policy.REMEDIATION_OPS` order | `test_plan_order_disable_flow_and_block_restore_first`, synthetic X5 |
| A22 | Verification covers every remediated record / process | `plan` | V |
| A23 | Use `evidence_files.csv` to locate files; do not infer membership from the manifest alone; match by identifiers, hashes, resource ids, timestamps | `evidence.py::Manifest`, `world.py` | manual: membership is decided only by identifiers/ids inside file contents |
| A24 | Images/PDFs are ordinary artefacts - extract facts | `media.py` (cached OCR, page-type rules) | E (screenshot cases), manual review of every image |
| A25 | SQL evidence: compare primary/replicas, replication drift | `evidence.py::parse_sql`, `policy.worst_state` | `test_exact_rows` (`case_16`), synthetic X6 |
| A26 | Never cite organizer-only truth or evidence captured after `audit_at` | `eligible`, `ghost/` never reads labels | V; `test_runtime_does_not_read_labels_or_organizer_files` |
| A27 | Evaluation report: status, step, field, sequence accuracy, >= 3 own cases, failures/improvements | `evaluation/main.py --reports` | `test_report_contents_required_by_readme` |
| A28 | Usage report for the final run: providers, models, calls, tokens, totals, cost | `code/evaluation/usage_report.md` | `test_report_contents_required_by_readme` |

## B. `README.md`

| # | Requirement | Where it is met | Test |
|---|---|---|---|
| B1 | Read inputs from `dataset/`, write `output.csv` in the repo root | `main.py`, `dataset.py::DATASET`, `ROOT` | `test_output_is_valid_and_deterministic` |
| B2 | Runnable with `python3 code/main.py` | `code/main.py` | `test_output_is_valid_and_deterministic` |
| B3 | Final `output.csv` has exact header and 518 rows sorted | see A1-A3 | V |
| B4 | `code.zip` built by `zip -r code.zip README.md code -x ...` | `package.sh` (portable: `code/tools/package.py`) | `test_packaging_uses_the_documented_zip_command`, `test_code_zip_rules` |
| B5 | No dataset/, venvs, caches, nested archives, secrets, real personal data in `code.zip` | `package.sh` guard, `.gitignore` | `test_code_zip_rules`, `test_no_secrets_or_personal_data_in_shipped_text` |
| B6 | Reports in `code/evaluation/` (and mirrored in `evaluation/`); metrics written in Markdown (raw `.csv/.json/.jsonl/.sql/.log` are ignored by the code judge) | `evaluation/*.md`; `audit_trail.jsonl` is a convenience copy, all conclusions are also in `.md` | `test_report_contents_required_by_readme` |
| B7 | Prompts kept in source/Markdown | no prompts: the agent uses no LLM (see `usage_report.md`) | manual |
| B8 | README with clear setup and run instructions | `README.md` (Quick start) | manual; `make all` verified |
| B9 | `log.txt` follows the `AGENTS.md` turn format, uploaded as the transcript | `log.txt` | `test_log_txt_entries_have_tool_and_model` |
| B10 | `evaluation_report.md` / `usage_report.md` / `README.md` inside `code.zip` | `package.sh` | `test_code_zip_rules` |
| B11 | Output findings cite valid evidence ids; future steps use `""` | see A13/A14 | V |

## C. `AGENTS.md`

| # | Requirement | Where it is met | Test |
|---|---|---|---|
| C1 | Log file `log.txt` at the repo root, append-only, UTF-8 / `\n`, resolved relative to `AGENTS.md` | `log.txt` | `test_log_txt_entries_have_tool_and_model` |
| C2 | `SESSION START` entry with tool, model, repo root, branch, worktree, parent agent, language, time remaining | first entry of `log.txt` | manual |
| C3 | Per-turn entries: prompt verbatim, response summary, actions, context with exactly one `tool=` and one `model=` | `log.txt` | `test_log_txt_entries_have_tool_and_model` |
| C4 | Never log secrets or real personal data | `log.txt` holds prompts only | `test_no_secrets_or_personal_data_in_shipped_text` |
| C5 | §3.2 greeting to the user at session start | **gap found in this review**: the greeting was missing at session start; it is delivered at the top of the next reply and recorded in this turn's log entry | manual |
| C6 | §3.3 time remaining when an end time is supplied | the user supplied ~2026-10-25 13:30 UTC; remaining time is stated in this turn's log entry and reply | manual |
| C7 | §4.3 use only participant-facing data under `dataset/`; no organizer files, golden answers or labels | runtime reads only `dataset/*.csv` and `dataset/evidence`; `sample_output.csv` is read only by `evaluation/` scripts | `test_runtime_does_not_read_labels_or_organizer_files` |
| C8 | §4.4 no secrets committed; credentials via environment | no credentials needed; none stored | `test_no_secrets_or_personal_data_in_shipped_text` |
| C9 | §4.1 submission URL answers | not asked in this repo task; URL is `https://www.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists` | manual |
| C10 | §6.7 runnable from terminal; deterministic ordering and JSON serialisation | `json.dumps(..., separators)`, sorted plans | `test_output_is_valid_and_deterministic` |
| C11 | §6.7 clean run produces exactly one valid row per case | `make all` | V |
| C12 | §6.7 README with setup and run instructions | `README.md` | manual |
| C13 | §6.9 submission items: `code.zip`, `output.csv`, `chat_transcript` (= `log.txt`); AI interview material | `SUBMISSION.md`, `docs/DECISIONS.md` | manual |
| C14 | §8 cross-platform: resolve paths relative to the repo; do not assume bash | `pathlib` everywhere; `make`/`package.sh` optional; `python3 code/tools/package.py` is the portable packager | manual (no shell calls in `ghost/`) |
| C15 | §8 nested `AGENTS.md`: none | n/a | manual |
| C16 | `agent_trace_instructions.md` (agent-trace.zip) | **out of scope for the repo**: it requires the harness session files; `SUBMISSION.md` tells the user what to collect | manual |

## D. Gaps found by this review and what was done

| Gap | Resolution |
|---|---|
| C5: greeting not shown at session start | cannot be retroactive; shown now (top of this reply) and logged |
| C6: time remaining not computed | stated now (see reply / log entry) |
| `log.txt` first three entries share one timestamp (written in one go) | entries are valid ISO-8601; later entries carry the actual time. Not rewritten (append-only rule) |
| No portable packager for Windows (README gives a PowerShell variant) | `code/tools/package.py` added |
| Single-command run | `make all` (tests, run, validate, reports, ceiling analysis, `code.zip`); `python3 code/main.py` for the output alone |
| Operation table only checked by validator | `test_operation_table_matches_problem_statement` parses the statement itself |
