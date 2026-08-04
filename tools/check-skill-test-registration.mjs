/**
 * Fails when a skill test suite exists on disk but is not invoked by any npm script.
 *
 * An unregistered suite is worse than a missing one: it looks like coverage, passes
 * review, and never runs. This check makes that state loud instead of silent.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = join(fileURLToPath(new URL('.', import.meta.url)), '..');
const skillsRoot = join(repoRoot, 'src', 'bmm-skills');
const guide = 'tools/skill-test-registration.md';

function findTestFiles(dir) {
  const found = [];
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return found;
  }
  for (const entry of entries) {
    if (entry === 'node_modules' || entry === '__pycache__') continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      found.push(...findTestFiles(full));
    } else if (/^test_.*\.py$/.test(entry) && full.includes(`${join('scripts', 'tests')}`)) {
      found.push(full);
    }
  }
  return found;
}

const pkg = JSON.parse(readFileSync(join(repoRoot, 'package.json'), 'utf8'));
const allScripts = Object.values(pkg.scripts ?? {}).join('\n');

const testFiles = findTestFiles(skillsRoot).sort();
const unregistered = testFiles.map((file) => relative(repoRoot, file).split('\\').join('/')).filter((rel) => !allScripts.includes(rel));

if (unregistered.length > 0) {
  console.error('Unregistered skill test suites found:\n');
  for (const rel of unregistered) {
    console.error(`  ${rel}`);
  }
  console.error(
    `\nEach suite above exists but no npm script runs it, so it never executes in CI.\n` +
      `Add it to an existing per-skill test script in package.json, or create one, and\n` +
      `make sure that script is reachable from "quality".\n\n` +
      `See ${guide} for the conventions.\n`,
  );
  process.exit(1);
}

console.log(`Skill test registration: ${testFiles.length} suite(s) checked, all registered.`);
