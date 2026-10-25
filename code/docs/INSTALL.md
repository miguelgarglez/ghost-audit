# Installing Ghost Audit

Ghost Audit 1.0.1 is a deterministic deletion-audit tool. It ships as a CLI (`ghost-audit`) and a read-only stdio MCP
server (`ghost-audit-mcp`). It needs **Python 3.9 or later** and nothing else: no dependencies, no network and no LLM
at runtime.

| Channel | Status | Command |
|---|---|---|
| npm (`ghost-audit-mcp`) | **published, 1.0.1** (2026-10-25, <https://www.npmjs.com/package/ghost-audit-mcp>) | `npx -y ghost-audit-mcp` |
| GitHub (pip / pipx / uvx) | works (public repository) | `pipx install git+https://github.com/miguelgarglez/ghost-audit` |
| Source / hackathon `code.zip` | available | `python3 code/ghost_cli.py ...` |
| PyPI (`ghost-audit`) | **not published** | - |

## 1. npm / npx

The npm package bundles the Python sources and calls `python3` (it looks for `python3`, then `python`; on Windows
`py -3`). To use a different interpreter, set `GHOST_AUDIT_PYTHON=/path/to/python3`. If no Python 3.9+ is found, it
exits with code 127 and prints an explanation.

```bash
npx -y ghost-audit-mcp --dataset ./dataset          # MCP server on stdio (for an MCP client, see section 4)
npx -y -p ghost-audit-mcp ghost-audit demo          # CLI: audit the bundled synthetic demo
npm install -g ghost-audit-mcp                      # global install: `ghost-audit` and `ghost-audit-mcp`
ghost-audit demo
```

Uninstall with `npm uninstall -g ghost-audit-mcp`.

## 2. Python, from GitHub

```bash
pipx install git+https://github.com/miguelgarglez/ghost-audit     # isolated install of both commands
pip install git+https://github.com/miguelgarglez/ghost-audit      # into the current environment
uvx --from git+https://github.com/miguelgarglez/ghost-audit ghost-audit demo   # one-off run, no install
```

Uninstall with `pipx uninstall ghost-audit` or `pip uninstall ghost-audit`.

These commands need the GitHub repository to be public; until then, use npm or the hackathon `code.zip`. A PyPI release (`pip install ghost-audit`) has not been published.

## 3. From source or the hackathon `code.zip`

```bash
git clone https://github.com/miguelgarglez/ghost-audit && cd ghost-audit
python3 code/ghost_cli.py demo                       # same CLI as `ghost-audit`
python3 code/ghost_mcp.py --dataset ./dataset        # same server as `ghost-audit-mcp`
python3 code/main.py                                 # competition entry point: dataset/ -> output.csv
pip install .                                        # optional: install the console scripts from the checkout
```

## 4. MCP client configuration

Claude Desktop (`claude_desktop_config.json`), Cursor (`~/.cursor/mcp.json` or `.cursor/mcp.json`) and any other client
that accepts `mcpServers` JSON:

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

With a pip/pipx install, use `"command": "ghost-audit-mcp"` with `"args": ["--dataset", "/absolute/path/to/dataset"]`.
The server provides 10 read-only tools, plus resources and prompts. See [AGENT_GUIDE.md](AGENT_GUIDE.md).

## 5. Quick start with the synthetic demo

```bash
ghost-audit demo                         # writes a fictional dataset to a temp dir and audits it
ghost-audit demo --dir ./ghost-demo      # keep the files
ghost-audit --dataset ./ghost-demo explain demo_02
ghost-audit --dataset ./ghost-demo queue --sort confidence_v2
ghost-audit --dataset ./ghost-demo ingest-report
```

The demo uses an invented company (Lumen Lane Books) and invented people with `example.org` addresses. It shows one
case for each of these outcomes:

- a truly COMPLETE deletion;
- leftovers found through an old e-mail hash, plus an active sync and a restorable backup;
- replica drift;
- a request that is not a deletion (NOT_APPLICABLE);
- an unknown subject (INCOMPLETE + manual review);
- screenshot-only evidence with no OCR available (an unparsed file, then manual review).

## 6. Options and environment

| Option | Env var | Default |
|---|---|---|
| `--dataset DIR` | `GHOST_DATASET` | `<repo>/dataset` in a checkout, otherwise `./dataset` |
| `--ocr-cache DIR` | `GHOST_OCR_CACHE` | `ghost/ocr_cache` (present only in the hackathon `code.zip`) |
| - | `GHOST_AUDIT_PYTHON` (npm only) | `python3` |

Published packages contain **no OCR cache**, because the cache transcribes the challenge images. In that case,
screenshots and PDFs are read with `pdftotext` and `tesseract` when they are installed. Otherwise they appear in
`ingest-report` as unparsed, and any case left without grounded evidence is flagged `needs_manual_review`.

## 7. Requirements

- Python 3.9 to 3.13 (tested on 3.9 and 3.13), on Linux, macOS or Windows.
- Node 18 or later, for the npm channel only.
- Optional: `pdftotext` (poppler) and `tesseract` (with Pillow), to read new screenshots and PDFs.

## 8. Troubleshooting

- **MCP tools fail with `internal error: [Errno 2] No such file or directory ...`.** The server was started without a
  dataset. Pass `--dataset DIR` (or set `GHOST_DATASET=DIR`) when launching it, for example
  `npx -y ghost-audit-mcp --dataset ./dataset`. Outside a checkout the default is `./dataset` in the working directory.
  Create a synthetic one with `ghost-audit demo --dir d`.
- **Exit code 127 from `npx`.** The Node shim found no Python 3.9 or later. Install Python, or point
  `GHOST_AUDIT_PYTHON` at an interpreter.
- **`import ghost` resolves to another package.** The Python package uses the top-level module name `ghost`, which can
  clash with an unrelated `ghost` package in a shared environment. Prefer `pipx` or `uvx`, which install into an
  isolated environment, or use the npm package.

## 9. Verified after publishing (2026-10-25)

In an empty directory, against the public npm registry: `npx -y -p ghost-audit-mcp ghost-audit demo` ran the synthetic
demo, and `npx -y ghost-audit-mcp` completed an MCP handshake (server `ghost-audit` 1.0.0, 10 tools). The published
tarball has 23 files (sha256 `af912244babee78826b528299d7b62a6a2a8e7b74a9dc064fa36712eb66a8c03`).
