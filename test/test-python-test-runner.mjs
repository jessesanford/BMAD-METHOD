import assert from 'node:assert/strict';
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { delimiter, dirname, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { discoverPythonTests, runPythonTests } from '../tools/run-python-tests.mjs';

function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'bmad-python-runner-'));
  mkdirSync(join(root, 'src', 'nested'), { recursive: true });
  writeFileSync(join(root, 'src', 'test_z.py'), '');
  writeFileSync(join(root, 'src', 'nested', 'test_a.py'), '');
  writeFileSync(join(root, 'src', 'nested', 'helper.py'), '');
  return root;
}

test('discovers nested Python tests once in stable path order', () => {
  const root = fixture();
  assert.deepEqual(discoverPythonTests(join(root, 'src')), [join(root, 'src', 'nested', 'test_a.py'), join(root, 'src', 'test_z.py')]);
});

test('rejects symlinks instead of silently skipping test paths', (context) => {
  const root = fixture();
  try {
    symlinkSync(join(root, 'src', 'test_z.py'), join(root, 'src', 'test_link.py'));
  } catch (error) {
    if (error.code === 'EPERM') {
      context.skip('symlinks require elevated privileges');
      return;
    }
    throw error;
  }
  assert.throws(() => discoverPythonTests(join(root, 'src')), /does not support symlinks/);
});

test('runs every discovered file separately through the upstream uv and pytest convention', () => {
  const root = fixture();
  const calls = [];
  const status = runPythonTests({
    repositoryRoot: root,
    spawn(command, args, options) {
      calls.push([command, args, options]);
      return { status: 0 };
    },
    log() {},
  });
  assert.equal(status, 0);
  assert.deepEqual(calls[0].slice(0, 2), ['uv', ['--version']]);
  assert.deepEqual(
    calls.slice(1).map(([, args]) => args.at(-1)),
    [join(root, 'src', 'nested', 'test_a.py'), join(root, 'src', 'test_z.py')],
  );
  for (const [command, args, options] of calls.slice(1)) {
    assert.equal(command, 'uv');
    assert.deepEqual(args.slice(0, -1), [
      'run',
      '--python',
      '3.11',
      '--with',
      'pytest>=8',
      'python',
      '-m',
      'pytest',
      '--override-ini=addopts=',
      '-q',
    ]);
    assert.equal(options.env.PYTEST_ADDOPTS, '');
  }
});

test('fails for empty discovery, missing uv, launch errors, and child failures', () => {
  const empty = mkdtempSync(join(tmpdir(), 'bmad-python-runner-empty-'));
  mkdirSync(join(empty, 'src'));
  assert.equal(runPythonTests({ repositoryRoot: empty, error() {} }), 1);

  const root = fixture();
  assert.equal(
    runPythonTests({
      repositoryRoot: root,
      spawn: () => ({ error: new Error('ENOENT'), status: null }),
      error() {},
    }),
    1,
  );

  let call = 0;
  assert.equal(
    runPythonTests({
      repositoryRoot: root,
      spawn: () => (++call === 1 ? { status: 0 } : { error: new Error('launch failed'), status: null }),
      log() {},
      error() {},
    }),
    1,
  );

  call = 0;
  assert.equal(
    runPythonTests({
      repositoryRoot: root,
      spawn: () => ({ status: ++call === 2 ? 2 : 0 }),
      log() {},
    }),
    1,
  );
  assert.equal(call, 3);
});

test('the CLI entrypoint executes every discovered test', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
  const bin = mkdtempSync(join(tmpdir(), 'bmad-fake-uv-'));
  const log = join(bin, 'calls.jsonl');
  const fake = join(bin, 'fake-uv.cjs');
  writeFileSync(
    fake,
    "require('node:fs').appendFileSync(process.env.BMAD_UV_LOG, JSON.stringify({ args: process.argv.slice(2), addopts: process.env.PYTEST_ADDOPTS }) + '\\n'); if (process.argv.some((arg) => arg.endsWith(process.env.BMAD_UV_FAIL || '\\0'))) process.exit(2);\n",
  );
  if (process.platform === 'win32') {
    writeFileSync(join(bin, 'uv.cmd'), `@node "${fake}" %*\r\n`);
  } else {
    writeFileSync(join(bin, 'uv'), `#!/usr/bin/env node\nrequire(${JSON.stringify(fake)});\n`);
    chmodSync(join(bin, 'uv'), 0o755);
  }

  const options = {
    cwd: root,
    encoding: 'utf8',
    env: {
      ...process.env,
      BMAD_UV_LOG: log,
      PATH: `${bin}${delimiter}${process.env.PATH ?? ''}`,
      PYTEST_ADDOPTS: 'src',
    },
  };
  const result = spawnSync(process.execPath, [join(root, 'tools', 'run-python-tests.mjs')], options);
  assert.equal(result.status, 0, result.stderr);
  const calls = readFileSync(log, 'utf8').trim().split('\n').map(JSON.parse);
  assert.equal(calls.length, discoverPythonTests(join(root, 'src')).length + 1);
  assert.deepEqual(calls[0].args, ['--version']);
  assert.ok(calls.slice(1).every((call) => call.addopts === ''));
  options.env.BMAD_UV_FAIL = 'test_memlog.py';
  assert.equal(spawnSync(process.execPath, [join(root, 'tools', 'run-python-tests.mjs')], options).status, 1);
});

test('keeps local quality and CI wired to the same Python gate', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
  const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
  const workflow = readFileSync(join(root, '.github', 'workflows', 'quality.yaml'), 'utf8');
  assert.match(pkg.scripts.quality, /\bnpm run test:python\b/);
  assert.doesNotMatch(pkg.scripts.quality, /test:python\s*(?:\|\||;)/);
  assert.match(workflow, /astral-sh\/setup-uv@v6/);
  assert.match(workflow, /run: npm run test:python/);
  assert.doesNotMatch(workflow, /continue-on-error:\s*true/);
});
