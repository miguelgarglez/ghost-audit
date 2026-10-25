# Publish checklist: Ghost Audit 1.0.0 - DONE

Target: **npm only**. Status: **published** on 2026-10-25 (about 13:57 Europe/Madrid) from the maintainer's npm account.
PyPI (`ghost-audit`) is **not published**. The token was supplied through a secure form, used through a temporary
`.npmrc` that referenced `${NPM_TOKEN}`, and was never printed, logged or committed.

| Step | Result |
|---|---|
| Name check before publishing | `ghost-audit-mcp` free (registry 404) |
| Tarball | `ghost-audit-mcp-1.0.0.tgz`, 23 files, sha256 `af912244babee78826b528299d7b62a6a2a8e7b74a9dc064fa36712eb66a8c03` |
| `npm publish --access public` | OK, `ghost-audit-mcp@1.0.0` |
| Package page | <https://www.npmjs.com/package/ghost-audit-mcp> |
| `npx -y -p ghost-audit-mcp ghost-audit demo` (empty dir, public registry) | OK, synthetic demo audited |
| `npx -y ghost-audit-mcp` MCP handshake (initialize, tools/list) | OK, server `ghost-audit` 1.0.0, 10 tools |
| GitHub installs (`pip`/`pipx`/`uvx git+https://github.com/miguelgarglez/ghost-audit`) | not available yet: repository is private until the hackathon evaluation ends |
| PyPI | not published |

## Known limitation in 1.0.0
Calling an MCP tool on a server started without a dataset returns `internal error: [Errno 2] No such file ...`.
Start the server with `--dataset DIR` (or `GHOST_DATASET`). Documented in `docs/INSTALL.md`, section 8. A clearer
message is a candidate for the next release; 1.0.0 is not changed after publishing.
