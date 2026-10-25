'use strict';
// Locates python3 >= 3.9 and runs one of the bundled Python entry points with stdio inherited (MCP-safe).
const { spawn, spawnSync } = require('child_process');
const path = require('path');

function findPython() {
  const cands = process.env.GHOST_AUDIT_PYTHON ? [process.env.GHOST_AUDIT_PYTHON]
    : (process.platform === 'win32' ? ['py', 'python', 'python3'] : ['python3', 'python']);
  for (const c of cands) {
    const args = c === 'py' ? ['-3', '-c'] : ['-c'];
    const r = spawnSync(c, [...args, 'import sys;print(sys.version_info[0]*100+sys.version_info[1])'], { encoding: 'utf8' });
    if (r.status === 0 && parseInt(r.stdout, 10) >= 309) return { cmd: c, pre: c === 'py' ? ['-3'] : [] };
  }
  return null;
}

module.exports = function run(script) {
  const py = findPython();
  if (!py) {
    process.stderr.write('ghost-audit: Python 3.9+ was not found on PATH. Install it from https://www.python.org/downloads/ ' +
      'or set GHOST_AUDIT_PYTHON to a python3 executable.\n');
    process.exit(127);
  }
  const child = spawn(py.cmd, [...py.pre, path.join(__dirname, '..', 'py', script), ...process.argv.slice(2)],
    { stdio: 'inherit', env: Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8', PYTHONDONTWRITEBYTECODE: '1' }) });
  child.on('error', (e) => { process.stderr.write('ghost-audit: failed to start Python: ' + e.message + '\n'); process.exit(127); });
  child.on('exit', (code, signal) => process.exit(code === null ? (signal ? 1 : 0) : code));
  for (const s of ['SIGINT', 'SIGTERM']) process.on(s, () => child.kill(s));
};
