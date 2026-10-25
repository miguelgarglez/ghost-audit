# Usage report - final full-dataset run

Run that produced `output.csv`: `python3 code/main.py` over all 518 requests in `dataset/deletion_requests.csv`.

| Item | Value |
|---|---|
| Model providers / model names | **none** - the audit agent is fully deterministic (rules + parsers); no LLM, embedding or hosted API is called at run time |
| Model calls | 0 |
| Input tokens / output tokens | 0 / 0 |
| Total tokens / average per request | 0 / 0 |
| Estimated cost (total / per request) | USD 0.00 / USD 0.00 |
| Per-model and overall totals | not applicable (no models); overall = 0 tokens, USD 0.00 |
| Run time | about 1 second on one CPU core, no network, no API keys or environment variables required |

## Why no LLM

The task is dominated by exact, auditable logic: identity resolution over hashes and historical identifiers, reconstruction of record state at a cut-off time,
policy lookup (`deletion_rules.csv`) and a strictly ordered plan. Doing this with rules gives byte-for-byte reproducible output, zero marginal cost and an explanation
for every step (`audit_trail.jsonl`). Where an LLM could genuinely help - reading arbitrary new screenshot layouts or free-text requests in unseen languages - the
agent uses cached OCR plus keyword rules instead (see `code/ghost/media.py`, `code/ghost/intent.py`); both are documented limits in `evaluation_report.md`.

## Development-time AI assistance (not part of the run above)

The solution was developed with a coding agent (Cursor Cloud Agent, Claude Sonnet 5.5) following `AGENTS.md`; the session transcript is `log.txt`.
Token counts for that interactive session are not exposed by the harness and are therefore not reported here. Screenshots were inspected visually by the
agent during development to design the OCR page mappings; no labels from the hidden answers were available or used.
The agent-facing CLI (`code/ghost_cli.py`) and MCP server (`code/ghost_mcp.py`) added later are read-only wrappers over the same deterministic audit: they call no LLM or network either.
Later phases (robustness harness, `confidence_v2`, release packaging, the npm release of `ghost-audit-mcp@1.0.0`, videos and final
packaging) were done by a Grok Bot orchestrator ("Engy") and its executor subagents; that harness does not expose a model identifier,
so none is claimed. Codex and Cursor cloud agents were not used for this final work. None of these tools run at audit time.
