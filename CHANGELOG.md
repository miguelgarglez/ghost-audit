# Changelog

## 1.0.1 - 2026-10-25

Metadata and repository release; no change to the audit logic.

- The GitHub repository is now public: npm `homepage`, `repository` and `bugs` links resolve, and the
  GitHub-based Python installs (pip / pipx / uvx) work.
- The public repository no longer contains challenge-derived data: the OCR cache, `output.csv` and the raw agent trace
  were removed (history rewritten). Dataset-dependent test modules skip with an explicit message when `dataset/` is absent.
- README rewritten as a case study, with links to the product and technical videos (v1.0.1 release assets).
- Version bumped to 1.0.1 (npm, Python package, `describe`/MCP server version).

## 1.0.0 - 2026-10-25

First public release (MIT). Published to npm as `ghost-audit-mcp@1.0.0` on 2026-10-25
(<https://www.npmjs.com/package/ghost-audit-mcp>), verified from the public registry with `npx`. PyPI: not published.

- Packaging: npm package `ghost-audit-mcp`, which bundles the Python sources and provides the bins `ghost-audit-mcp`
  and `ghost-audit`. A Python package `ghost-audit` (console scripts `ghost-audit` and `ghost-audit-mcp`) can be
  installed from GitHub with pip, pipx or uvx once the repository is public. It is not on PyPI. All artefacts are built from one source tree
  by `code/tools/build_registry.py`.
- New `ghost-audit demo [--dir DIR]`: a bundled synthetic dataset (fictional company) that covers the COMPLETE,
  INCOMPLETE-with-leftovers, replica-drift, NOT_APPLICABLE, unknown-subject and no-OCR cases.
- New global options `--dataset DIR` and `--ocr-cache DIR` (env `GHOST_DATASET`, `GHOST_OCR_CACHE`) for the CLI and the
  MCP server. Outside a checkout, the dataset defaults to `./dataset`.
- The OCR cache is excluded from published packages. A screenshot or PDF that yields no text is now reported in the
  ingest report (`unparsed_files`) instead of being skipped silently.
- `verify` returns a clear error when the competition validator is not available (installed package).
- MIT `LICENSE`, `docs/INSTALL.md`, MCP client snippets, and 3 new demo tests.
- Unchanged: the audit logic and `output.csv` (byte-identical, sha256 `ea64cff4...`).
