# Agent guide: Ghost Audit as a tool (CLI + MCP)

Ghost Audit has no fixed UI. It is designed so that **a person talks to their own agent** (Cursor, Claude, Codex, Grok…)
and the agent queries the audit and builds whatever artefact or UI the conversation needs.
Everything is **read-only, deterministic, offline and LLM-free**: data comes from the `Auditor` and the dataset, and `output.csv`
is never written (only `python3 code/main.py` does that, and it is unchanged).

## 1. Two interfaces, same capabilities

| CLI (`python3 code/ghost_cli.py …`) | MCP tool | Purpose |
|---|---|---|
| `describe` | `ghost_describe` | Capabilities, glossary (statuses, phases, operations) and known limits |
| `summary [--include-samples]` | `ghost_summary` | Status distribution, claimed→verified, false COMPLETE, top risks, manual review |
| `cases --status --claimed --risk KIND --needs-review --q TEXT --limit N` | `ghost_list_cases` | Filter cases (compact rows) |
| `case CASE_ID [--mask]` | `ghost_get_case` | Full context: request, subject and identifier history, resources with rule/proof/link/activities, plan, review signals, why claimed and verified differ |
| `explain CASE_ID [--mask]` | `ghost_explain_case` | Deterministic markdown narrative (8-15 lines) with citations `[evidence_XX, activity_XXXX]` |
| `evidence EVIDENCE_ID [--lines N] [--grep TEXT]` | `ghost_get_evidence` | File metadata and a text excerpt (file or OCR cache); never binaries |
| `queue [--limit N] [--sort priority\|confidence_v2]` | `ghost_review_queue` | Prioritised review queue with reasons, score and `confidence_v2` |
| `whatif [--min-pick-confidence X] [--min-confidence-v2 Y]` | `ghost_whatif` | In-memory re-run with another tie-break threshold; which cases would change. With `Y`: which cases a `confidence_v2` review threshold would route to a human |
| `verify [--path output.csv]` | `ghost_verify_output` | Structural validation of `output.csv` + sha256 |
| `ingest-report` | `ghost_ingest_report` | Ingestion diagnostics: files that could not be parsed, ignored lines, skipped rows, cases degraded to manual review (see `ROBUSTNESS.md`) |

- Format: `--format json|md|jsonl` (`--json` shortcut). JSON by default when stdout is not a TTY, markdown when it is.
- Exit codes: `0` ok, `1` verification failed, `2` usage error, `3` not found. Errors are JSON `{"error", "hint"}`.
- `--mask` masks e-mails, phones and names (the data is fictitious, but this is the pattern a real DPO would use).
- By default only the 518 evaluation cases are considered; `--include-samples` adds the public ones (`case_23` can always be queried by id).

### Review signals
- `confidence`: minimum, over the linked resources, of `1/k` when an account was picked among `k` same-day candidates (the same quantity thresholded by `MIN_PICK_CONFIDENCE = 0.30`).
- `confidence_v2` + `confidence_reasons`: review-priority signal in [0, 1] combining link strength and inferred states. It is not a probability; see [CONFIDENCE.md](CONFIDENCE.md).
- `needs_manual_review`: ambiguous link, withheld pick, unresolvable subject, or degraded input (see `ROBUSTNESS.md`).
- The queue also contains **false COMPLETE** cases (the workflow says done, but an active risk remains). An *inferred* state (no evidence captured before `audit_at`) only adds score.

## 2. Connecting the MCP server

A stdio server (JSON-RPC 2.0, one message per line) built on the standard library. Generic MCP client configuration:

```json
{
  "mcpServers": {
    "ghost-audit": {
      "command": "npx",
      "args": ["-y", "ghost-audit-mcp", "--dataset", "/absolute/path/to/dataset"]
    }
  }
}
```

From a checkout, use `"command": "python3"` with `"args": ["/path/to/repo/code/ghost_mcp.py"]`. With a pipx/pip install,
use `"command": "ghost-audit-mcp"`. All install routes are covered in [INSTALL.md](INSTALL.md).

It implements `initialize`, `ping`, `tools/list`, `tools/call`, `resources/list`, `resources/templates/list`, `resources/read`,
`prompts/list` and `prompts/get`; notifications are accepted without a reply. Every tool carries `annotations.readOnlyHint = true`
and returns `structuredContent` alongside the JSON text. The audit is loaded once (lazily, ~0.5 s) and cached.

- **Resources**: `ghost://summary`, `ghost://queue`, `ghost://describe`, templates `ghost://case/{case_id}` and `ghost://evidence/{evidence_id}`.
- **Prompts**: `review_deletion_case(case_id)` → case card; `executive_brief` → one-page DPO brief; `triage_queue(limit)` → interactive triage.

Quick test without a client:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"ghost_summary","arguments":{}}}' | python3 code/ghost_mcp.py
```

## 3. Example conversations

> **Person:** How many deletion requests were closed without actually being done?
> **Agent:** calls `ghost_summary` → "163 of 254 COMPLETE are actually INCOMPLETE; the most frequent risk is SEARCH_INDEX_ENTRY (270 cases)". Offers a chart.

> **Person:** Show me ingrid.dahl's case.
> **Agent:** `ghost_list_cases(q="ingrid.dahl")` → `ghost_get_case("case_33")` → `ghost_get_evidence("evidence_18", grep="1842")`. Shows the card: export `export_1842` is still PRESENT and `rule_09` requires DELETE; the proof is the statement `NST-2026-0718-1842`.

> **Person:** What should I review first today?
> **Agent:** `ghost_review_queue(limit=10)` and, for the first item, `ghost_explain_case`. Proposes the human decision (confirm the account picked among same-day candidates).

> **Person:** What if we were more conservative with tie-breaks?
> **Agent:** `ghost_whatif(0.6)` → 35 cases would withhold the account pick and move to manual review; `output.csv` does not change.

## 4. Artefact / dynamic-UI patterns (built by the agent, not by us)

| Artefact | Tools | Suggested content |
|---|---|---|
| **Risk dashboard** | `ghost_summary` | KPIs (cases, false COMPLETE, manual review), bars per risk category, system × risk heatmap |
| **Review queue** | `ghost_review_queue`, `ghost_explain_case` | Table sortable by score, reason chips, side panel with the narrative and an "open evidence" button |
| **Case card** | `ghost_get_case`, `ghost_get_evidence` | Claimed vs verified header, resource table (state, rule, violation, proof), plan as a per-phase checklist, confidence box |
| **DPO brief** | `ghost_summary`, `ghost_review_queue`, `ghost_verify_output` | One page: figures, systems with most violations, top cases, actions; `output.csv` sha256 as a seal |
| **Threshold simulator** | `ghost_whatif` | `min_pick_confidence` slider → cases moving to manual review |
| **Data-quality panel** | `ghost_ingest_report` | Unparsed files, ignored lines, degraded cases |

Rules for the agent: always cite the ids returned by the tools (`case_`, `evidence_`, `activity_`, `audit_`, `rule_`); never invent
fields; use `--mask`/`mask=true` if the artefact will be shared.

## 5. Honest limits
- Ceiling of ~98% step F1 on the samples (`code/evaluation/limits_and_ceiling.md`); some `audit_record` ids appear in no file and their verification is omitted.
- Confidence only models link ambiguity among same-day candidates; it is not a calibrated probability for the whole case.
- `whatif` only varies `MIN_PICK_CONFIDENCE` (re-runs the audit in memory, ~1 s) or simulates a `confidence_v2` routing threshold.
- `confidence_v2` orders review work; with 3 wrong rows in 32 samples it cannot support an accuracy percentage.
- Screenshots and PDFs are read from the OCR cache (`code/ghost/ocr_cache`).
- Robustness to unseen inputs and its limits: `code/docs/ROBUSTNESS.md`.
