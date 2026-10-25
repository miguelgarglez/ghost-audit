# The 12 decisions that shape Ghost Audit (interview prep)

Each entry: **decision**, **why**, **alternatives rejected**, **evidence it was right** (all reproducible with `make all`).

## 1. Deterministic rules and parsers, no LLM at run time
*Why:* the task is exact logic - identity resolution over hashes, state at a cut-off, a policy table and a strictly ordered plan. Rules give byte-for-byte reproducible output, zero cost and an explanation per step.
*Rejected:* an LLM agent reading evidence (non-deterministic ordering and ids, token cost, hallucinated resource ids - fatal when the grader compares ids); a hybrid LLM tie-breaker (nothing to break ties with: the data lacks the signal, see #9).
*Evidence:* 100% status accuracy and 99.2% step F1 on the 32 samples, run time ~1 s, `test_output_is_valid_and_deterministic`.

## 2. One normalised observation type for every evidence format
*Why:* JSON exports, snapshot bundles, SQL replicas, seven log dialects, text ledgers, PDFs and screenshots all say "at time t, resource R of system S was in state X and mentioned identifiers I". Everything downstream (linking, time reconstruction, policy) works on `Obs` only, so a new format is one parser, not a new pipeline.
*Rejected:* per-format decision code; loading everything into one SQL database (loses time/copy semantics, harder to explain).
*Evidence:* 94 evidence files parse into ~3k observations; `ParserDialects` tests parse every log line of every file.

## 3. Exact matching on a complete identity set (current + historical)
*Why:* the request e-mail may be old; records may carry only an old e-mail hash or phone. The directory expands every person to all e-mails, phones, hashes (including SHA-256 of each e-mail) from `user_audit_log`. Look-alike decoys (`g.silva76.jr@...`) must never match.
*Rejected:* fuzzy/edit-distance matching (merges decoys into real people - a false deletion finding); name matching (shared names).
*Evidence:* zero identifier collisions between people; synthetic case X1 (old e-mail + hash-only record).

## 4. State is "latest observation that existed at audit_at"; later evidence only links ids
*Why:* statuses and later snapshots are claims; a record re-created by sync after `audit_at` must not change the answer. An observation counts only if the **file** was captured and the **record** was true at or before `audit_at`. Later files are still used to learn which person a resource id belongs to (an id belongs to a person forever), never for state and never cited.
*Rejected:* using the newest snapshot (the designed trap); ignoring later files entirely (loses the id of ~100 accounts/audit records that only a later file ties to a person).
*Evidence:* `case_23` cites `evidence_92`, not the later duplicate `evidence_02`; synthetic X2.

## 5. Replication drift: any exposed copy means exposed
*Why:* primary and replica tables disagree; a person remains reachable if any copy still holds them. Each copy is its own observation, combined by "worst state wins" per kind.
*Rejected:* trusting the primary; picking the latest `updated_at` (identical across copies in a snapshot).
*Evidence:* `case_16` (profile only on the read replica) reproduced exactly; synthetic X6.

## 6. Policy as a table of (category, observed state) -> operations, with safe defaults
*Why:* `deletion_rules.csv` gives the required treatment per category; the code maps it to finding/remediation/verification operations. Retained audit and legal records and tombstone references are *verified present/redacted*, never deleted. Unobserved resources get only unconditional defaults: a deleted account is ABSENT (a live one would carry identifiers), a retained audit record stays PRESENT.
*Rejected:* deleting everything linked (breaks retention rules); inferring other kinds' states without evidence.
*Evidence:* all sample goldens; synthetic X3/X7; `test_retained_audit_record_is_verified_present_never_deleted`.

## 7. Plan order and evidence citation as pure functions of the findings
*Why:* the grader preserves array order. Findings, remediation (disable flows, block restores, then delete/redact/anonymize), verification (absent, present, redacted, anonymized, flow disabled, restore blocked), each sorted by system then resource. Findings and COMPLETE verification cite the single evidence file of the request; future steps carry `""`. A validator enforces the contract.
*Rejected:* ordering by discovery time; per-step evidence hunting (one eligible file per request in this dataset).
*Evidence:* `validate_output.py` (every structural rule), `test_plan_order_...`.

## 8. Closed-world elimination for ids that carry no identifier
*Why:* a fully deleted account or an anonymised analytics copy carries no identifier, yet the answer needs its id. The activity stream is a closed world: each worker event belongs to one request; same reported day, result mapped from the workflow claim (COMPLETE->SUCCESS, PARTIALLY_COMPLETE->PARTIAL, NOT_STARTED->FAILED), minus ids already tied to someone else, leaves few candidates.
*Rejected:* guessing; embedding similarity; inventing placeholder ids; pattern tricks on legacy ids (`account_00N` = case number) that do not generalise.
*Evidence:* unconserved-event check: 280 unlinked requests vs 280 unclaimed events; accounts recovered 88% under hidden-link masking.

## 9. Shared event clock, learned from data, to break the remaining ties
*Why:* tied candidates were a coin flip (155 ambiguous audits). Analysis of worker timestamps showed that all events of one request are stamped from one clock: the seconds-of-minute offset between the account event and each other event depends only on event type. The offsets are *learned* from requests whose account is tied by an identifier (no constants hard-coded) and used as a filter.
*Rejected:* order-based tricks (record order in some bundles); leaving ties; LLM tie-breaking. Picks with <1/3 chance are withheld and flagged.
*Evidence:* `case_03` fixed (two people, two anonymised copies), ambiguous audits 155 -> 18, hide-the-link recovery ~88%; `test_shared_event_clock_resolves_analytics_tie`.

## 10. Honest ceiling: omit what no file contains, document the proof
*Why:* ~17% of requests miss their retained-audit-record id in every file (no unlinked audit ids exist, archival events are noise, ids are not a function of the person). Inventing an id can only add a false step; omission costs recall once. `limits_and_ceiling.md` proves this from data and estimates the achievable ceiling (F1 about 98%).
*Rejected:* padding output with plausible ids; claiming 100%.
*Evidence:* `ceiling_analysis.py` (reproducible), samples `case_18/20`.

## 11. Fail safe, never fail silent, on unseen input
*Why:* with no LLM, an input format nobody planned for could crash a rules engine or, worse, produce a confident wrong answer. Every file, row and case is isolated. Unknown input is reported in an ingest report (`ghost_cli.py ingest-report` / MCP `ghost_ingest_report`). A case that cannot be grounded gets `INCOMPLETE` + `[]` + `needs_manual_review`, never `COMPLETE` without proof and never an invented id.
*Rejected:* aborting the whole run on the first bad file (one corrupt log would lose 518 answers); "best guess" merging of look-alike identities (+tag aliases, homoglyphs), since deleting or clearing the wrong person is worse than asking a human.
*Evidence:* `code/tools/stress.py`: 29 adversarial scenarios (corrupt/empty/truncated/missing files, unknown dialects and categories, BOM/CRLF/reordered CSVs, odd dates, 0/1/10x cases). All survive with a valid `output.csv`, and semantics-preserving ones are byte-identical. Details and limits: [`ROBUSTNESS.md`](ROBUSTNESS.md).

## 12. Confidence as a review-priority signal, not a probability
*Why:* the original `confidence` (1/k for same-day ties) was 1.0 on every sample, so it could not separate anything. `confidence_v2` multiplies hand-set link reliabilities (identifier 0.995 > catalog 0.99 > indirect 0.97 > tie 1/k, x0.93 for inferred states). It sits only in auxiliary fields (CLI/MCP, `audit_trail.jsonl`, queue sorting, `whatif`), and `output.csv` is unchanged.
*Rejected:* fitting weights to 32 samples with 3 errors (overfitting); advertising a "99%" figure; letting an LLM decide (kept as an opt-in tie-break design, not shipped).
*Evidence:* on the samples, >= 0.90 covers 72% at 95.7% exact and >= 0.98 covers 25% at 100%. Under perturbation, >= 0.98 is stable in 97.6% of re-runs, while < 0.95 changes in 46%. See [`CONFIDENCE.md`](CONFIDENCE.md).

### Also worth knowing
* Screenshots/PDFs: cached OCR (3x upscale) plus page-type rules and the data-catalog `LINK_RESOURCE_IDENTITY` rows, not a vision model - deterministic, inspectable, and the cache means the audit needs no OCR tools.
* Subjects with two requests keep resources first evidenced inside `[requested_at-3d, audit_at]`, which separates e.g. an export that existed in June from the statement that appeared in July.
* Evaluation: samples, a structural validator, seven synthetic cases, a self-supervised masking test and 54 tests (27 regression + 13 agent CLI/MCP + 4 robustness + 7 confidence + 3 demo; all 54 pass in the full repo, 51 pass + 3 skip from code.zip alone), plus the 29-scenario stress harness - because the hidden answers are unavailable, every claim is backed by a check we can run.

## 13. Publish as a package: npm first, one source tree, no challenge data
*Why:* reviewers and agent users should be able to try the tool with one command, and MCP clients usually launch servers through `npx`. The npm package `ghost-audit-mcp` bundles the same Python sources with a small Node shim that finds Python 3.9+ (or fails with a clear message). The Python package installs from GitHub with pip, pipx or uvx once the repository is public. PyPI is not published (no account). Every artefact is built from `code/` by `code/tools/build_registry.py`, so the hackathon zip, the wheel and the npm tarball cannot drift apart.
*Excluded from published packages:* `dataset/` and `ghost/ocr_cache/` (the cache transcribes challenge screenshots and PDFs). Without the cache, media goes through `pdftotext` or `tesseract` when installed. Otherwise it is reported as unparsed, and the affected cases fall to `needs_manual_review` (decision 11). A synthetic demo dataset (`ghost-audit demo`) replaces the challenge data for first-time users.
*Still no LLM in the main path:* the published tool must be deterministic, offline and auditable. The same input gives the same plan, and every step cites a file. An LLM could only reorder grounded candidates (decision 12), so the extra cost, non-determinism and data exposure are not worth it for a 1.0.
*Evidence:* clean-venv installs of the wheel and sdist (Python 3.9 and 3.13), plus a clean npm-prefix install and `npx` of the tarball, each run `ghost-audit demo` and an MCP handshake (`initialize`, `tools/list`, `tools/call`); `code/tests/test_demo.py`.
*Outcome (2026-10-25):* `ghost-audit-mcp@1.0.0` was published to npm (MIT, 23 files, tarball sha256 `af912244...`) and verified from the public registry in an empty directory: `npx -y -p ghost-audit-mcp ghost-audit demo` works and `npx -y ghost-audit-mcp` answers the MCP handshake (server 1.0.0, 10 tools). The GitHub repository stays private until the hackathon evaluation ends.
