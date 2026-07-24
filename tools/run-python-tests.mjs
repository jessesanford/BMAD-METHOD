import { readdirSync } from 'node:fs';
import { dirname, relative, resolve, sep } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

export function discoverPythonTests(sourceRoot) {
  const tests = [];

  function discover(directory) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = resolve(directory, entry.name);
      if (entry.isSymbolicLink()) {
        throw new Error(`Python test discovery does not support symlinks: ${path}`);
      }
      if (entry.isDirectory()) discover(path);
      else if (entry.isFile() && /^test_.*\.py$/.test(entry.name)) tests.push(path);
    }
  }

  discover(sourceRoot);
  return tests.sort();
}

const repositoryPath = (path) => relative(root, path).split(sep).join('/');

export function runPythonTests({ repositoryRoot = root, spawn = spawnSync, log = console.log, error = console.error } = {}) {
  const tests = discoverPythonTests(resolve(repositoryRoot, 'src'));
  if (tests.length === 0) {
    error('No Python tests found beneath src');
    return 1;
  }

  const probe = spawn('uv', ['--version'], {
    encoding: 'utf8',
    shell: false,
  });
  if (probe.error || probe.status !== 0) {
    error(`uv is required to run Python tests: ${probe.error?.message ?? probe.stderr?.trim() ?? 'not available'}`);
    return 1;
  }

  let failed = false;
  for (const test of tests) {
    const display = relative(repositoryRoot, test).split(sep).join('/');
    log(`[python-test] ${display}`);
    const result = spawn(
      'uv',
      ['run', '--python', '3.11', '--with', 'pytest>=8', 'python', '-m', 'pytest', '--override-ini=addopts=', '-q', test],
      {
        cwd: repositoryRoot,
        env: { ...process.env, PYTEST_ADDOPTS: '' },
        stdio: 'inherit',
        shell: false,
      },
    );
    if (result.error) {
      error(`Could not launch ${display}: ${result.error.message}`);
      failed = true;
    } else if (result.status !== 0) {
      failed = true;
    }
  }
  return failed ? 1 : 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  process.exit(runPythonTests());
}
