# Skill test registration

Python test suites under `src/bmm-skills/**/scripts/tests/` do not run automatically.
Each suite must be invoked by an npm script that is reachable from `quality`.

`npm run validate:test-registration` enforces this. It fails when a `test_*.py` file
exists under a skill's `scripts/tests/` directory but no npm script references its path.

## Why this check exists

A test suite that is present but unregistered is worse than one that is missing.
It appears in the diff, reads as coverage during review, and reports nothing —
so a regression it would have caught ships with a green build. The failure is silent
by construction, which is exactly the kind of problem a check should make loud.

## Adding a suite

1. Put the file at `src/bmm-skills/<phase>/<skill>/scripts/tests/test_<subject>.py`.
2. Add or extend a per-skill npm script in `package.json`:

   ```json
   "test:<skill>": "uv run --python 3.11 src/bmm-skills/<phase>/<skill>/scripts/tests/test_<subject>.py"
   ```

   Suites are run with `uv` so that each file's PEP 723 inline dependency header is
   honoured. Do not invoke them through a generic pytest runner — that ignores the
   header and resolves the wrong dependencies.

3. Chain additional files in the same skill's script with `&&`.
4. Ensure the script is reachable from `quality`, either directly or via an aggregate
   script such as `test:stacked-pr`.

## Related conventions

When adding a whole skill rather than a suite:

- Place it under the directory for its phase: `agents/`, `plan/`, `ship/`, or `v6-shims/`.
  Skills are discovered by the presence of `SKILL.md`; no manifest edit is needed.
- Add a row to `src/bmm-skills/module-help.csv` and set the `phase` column to match the
  directory (`plan`, `ship`, `anytime`).
- Give the skill a menu code that no existing row already uses.
- Run `npm run validate:skills` to check the skill's own structure.
