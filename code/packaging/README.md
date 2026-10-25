# Ghost Audit

Ghost Audit checks whether data-deletion requests that were reported as **completed** really were. It reads a deletion
dataset (requests, identities, policy rules, system activity and evidence files), reconstructs each subject's state at
the audit time, and returns a verified status plus a FINDING / REMEDIATION / VERIFICATION plan. Every step cites the
evidence file that proves it.

- Deterministic: no LLM and no network at runtime, so the same input always gives the same output.
- Standard library only. Needs Python 3.9 or later. No dependencies.
- Read-only. It never modifies your data.
- Two interfaces: the `ghost-audit` CLI and the `ghost-audit-mcp` MCP server (stdio, JSON-RPC 2.0) for AI agents.
- Fail-safe. When a subject or a piece of evidence cannot be resolved, the case is marked `INCOMPLETE` with
  `needs_manual_review` and a reason. It is never silently passed.

## Install

From npm. The package bundles the Python sources, so you need `python3` 3.9 or later on PATH:

```bash
npx -y ghost-audit-mcp                         # start the MCP server on stdio
npx -y -p ghost-audit-mcp ghost-audit demo     # run the CLI on the bundled synthetic demo
npm install -g ghost-audit-mcp                 # or install globally: `ghost-audit` and `ghost-audit-mcp` commands
```

With Python tools, straight from GitHub (nothing beyond Python 3.9+ is needed):

```bash
pipx install git+https://github.com/miguelgarglez/ghost-audit      # or: pip install git+https://github.com/miguelgarglez/ghost-audit
uvx --from git+https://github.com/miguelgarglez/ghost-audit ghost-audit demo
```

A PyPI release (`pip install ghost-audit`) is planned but **not published yet**.

## Quick start

```bash
ghost-audit demo --dir ./ghost-demo            # write the demo dataset and audit it
ghost-audit --dataset ./ghost-demo explain demo_02
ghost-audit --dataset ./ghost-demo queue       # review queue, highest priority first
ghost-audit --dataset ./ghost-demo ingest-report
ghost-audit --dataset /path/to/your/dataset summary
```

`--dataset` defaults to `./dataset` (it can also be set with `GHOST_DATASET`). Output is JSON when piped and markdown
on a terminal. You can force either with `--format json|md|jsonl`.

## MCP client configuration

Claude Desktop (`claude_desktop_config.json`), Cursor (`.cursor/mcp.json`) and other clients that accept `mcpServers`:

```json
{
  "mcpServers": {
    "ghost-audit": {
      "command": "ghost-audit-mcp",
      "args": ["--dataset", "/absolute/path/to/dataset"]
    }
  }
}
```

Through npm (no global install needed):

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

All tools are read-only: summary, cases, case, explain, evidence, queue, what-if and ingest report.

## Screenshots and PDFs

Text is taken from screenshot and PDF evidence through an OCR cache (`--ocr-cache DIR` or `GHOST_OCR_CACHE`), or from
`pdftotext` and `tesseract` if they are installed. The published packages ship **no** OCR cache. If a file yields no
text, it is listed in `ingest-report` as unparsed, and any case left without grounded evidence is flagged
`needs_manual_review`.

## About `confidence_v2`

`confidence_v2` is a ranking signal for prioritising reviews. It is **not** a calibrated probability.

## License

MIT. Copyright (c) 2026 Miguel García González. Source, design notes and evaluation:
https://github.com/miguelgarglez/ghost-audit
