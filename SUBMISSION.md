# Submission - Ghost Audit (HackerRank Orchestrate October'26, "Ghost Data Still Exists")

## What to upload (three items)

| HackerRank slot | File in this repo |
|---|---|
| `code.zip` | `code.zip` (built by `./package.sh`: `README.md` + `code/`, includes `code/evaluation/evaluation_report.md` and `usage_report.md`; no dataset, venvs, caches or nested archives) |
| `output.csv` | `output.csv` (518 rows, sorted, validated) |
| `chat_transcript` | `log.txt` (the chat transcript) |

`agent-trace.zip` holds the raw agent session exports (submission artefact; not kept in the public repository). Two videos are delivered as separate files, not inside `code.zip`: `ghostaudit-launch-v5.mp4` (product, 97.6 s) and `ghostaudit-tech-v4-en.mp4` (technical, 219 s). The tool is also published on npm as `ghost-audit-mcp@1.0.0` (<https://www.npmjs.com/package/ghost-audit-mcp>); PyPI is not published. The HackerRank submission window closed before upload; the challenge dataset, `output.csv` and the OCR cache are not redistributed in this public repository.
One command regenerates everything: `make all`.

## The approach in plain words

**Problem.** A deletion workflow says "Complete" while copies of the person survive in search indexes, support tickets, analytics, exports, replicas and backups - and sync jobs or restores can recreate them.

**What I built.** A deterministic audit agent. For each request it (1) checks the person really asked for deletion, (2) works out every identifier the person ever had (old e-mails, hashes, phones),
(3) reads all evidence formats (JSON, SQL replicas, seven log dialects, text ledgers, PDFs, screenshots) into one common shape, (4) reconstructs what each record looked like at the audit cut-off,
(5) applies `deletion_rules.csv`, and (6) emits ordered findings -> remediation -> verification with evidence ids.

**Key ideas to explain in the interview**

1. *Claims are not proof.* `reported_status` and activity results are never used as state; only evidence captured at or before `audit_at` is.
2. *Identity graph.* Matching is exact on normalised e-mails/hashes/phones/user ids, including historical ones, so look-alike decoys do not match and old-address-only records do.
3. *Time travel.* Later snapshots (a record re-created by sync, an account that "reappears") are ignored for state and never cited.
4. *Replication drift.* Several copies of a record: if any copy still exposes the person, the record is exposed.
5. *Policy is data.* Delete / redact / anonymize / retain / tombstone / block-restore follow the rules table; audit and legal records are verified present, never deleted; tombstones keep only the stable `user_id`.
6. *Safe order.* Disable data flows and block restores first, then delete/redact/anonymize, then verify - sorted by the statement's operation order, then `system_id`, `resource_id`.
7. *Resource ids without identifiers.* A fully-deleted account or an anonymised analytics record carries no identifier. I find them by closed-world matching on the activity stream (same reported day, result mapped from the workflow claim, minus ids already tied to someone else).
   Ties are broken by a *shared event clock*: the seconds-of-minute offsets between a request's worker events are constant per event type (learned from requests whose account is tied by an identifier). Hiding links on purpose and recovering them gives ~88% right; 18 requests remain a coin-flip and are flagged `ambiguous`.
8. *No LLM at run time.* Rules, parsers and cached OCR. Zero tokens, zero cost, byte-for-byte reproducible; a roadmap item is a VLM fallback behind the same policy core.

**Product framing.** Ghost Audit is a privacy/DPO tool: the per-request audit trail (`audit_trail.jsonl`) shows who the subject is, which evidence proves each state, which rule applies and how each resource was linked; `audit_summary.md` gives the management view
(64% of "Complete" deletions were not complete; 113 active data flows; 26 open restore paths).

## Results

* Public samples case_11-32 (same generator as the hidden set): status accuracy 100%, step F1 99.4%. All 32 samples: status 100%, step F1 99.2%, 29/32 rows exact. The 3 misses are proven unfixable from the data (`limits_and_ceiling.md`).
* Estimated achievable ceiling for a data-only solver: step F1 about 98%, status about 100%.
* Evaluation requests: 355 INCOMPLETE, 153 COMPLETE, 10 NOT_APPLICABLE.
* Validator passes; 7/7 extra synthetic cases pass; 54 tests (27 regression + 13 agent CLI/MCP + 4 robustness + 7 confidence + 3 demo) pass in the full repo; from code.zip alone 51 pass and 3 skip cleanly (repo-only artifacts); 29/29 adversarial stress scenarios pass (`code/docs/ROBUSTNESS.md`); every spec requirement is traced in `code/evaluation/compliance_matrix.md`.

## Ambiguities I resolved (and how)

| Ambiguity | Decision |
|---|---|
| Does "verify my deletion" count as a deletion request? | Yes - default is deletion; NOT_APPLICABLE only with explicit keep/negation, marketing-only opt-out, or a pure non-deletion ask (matches the 10 privacy-intake `CLASSIFY_REQUEST` events) |
| Evidence captured after `audit_at` that names the same person | Used only to *link* resource ids (an id belongs to a person forever), never for state and never cited |
| Account never shown with an identifier | Deleted accounts are ABSENT by default (a live profile would carry identifiers); id from activity elimination |
| Retained audit record not found in the evidence file | Verified PRESENT if its id is known from any evidence; otherwise omitted (no invented ids) |
| Expired exports / person with two requests | Resources are tied to the request whose window `[requested_at-3d, audit_at]` first evidences them |
| Which evidence id | The one evidence file of the case that proves the state (one eligible file per request in this dataset); future steps use `""` |
| Report location | `code/evaluation/` (as the starter README requires), mirrored to `evaluation/` |

## Interview cheat-sheet (30 minutes)

1. Architecture: the mermaid diagram in `README.md` (parsers -> `Obs` -> links -> state at `audit_at` -> policy -> plan).
2. Walk one case end to end: `case_23` (`audit_trail.jsonl`): old claim NOT_STARTED, export + search remain, audit record retained, account absent.
3. Read `code/docs/DECISIONS.md` (13 decisions, alternatives rejected; #11 is robustness to unseen input, see `code/docs/ROBUSTNESS.md`; #12 is `confidence_v2`, a review-priority signal and not a probability, see `code/docs/CONFIDENCE.md`) and `code/evaluation/limits_and_ceiling.md`.
4. Be ready to discuss: the tie problem and why I did not invent ids; why zero LLM; how I validated (samples, validator, synthetic cases, closed-world conservation 280 unlinked requests vs 280 unclaimed events, hide-the-link recovery ~88%); roadmap.
