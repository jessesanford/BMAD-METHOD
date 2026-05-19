---
title: Module Preflight Requirements
description: How modules declare tool dependencies and validate them before skill activation
sidebar:
  order: 5
---

Modules can declare the external tools and runtimes they depend on, and validate those dependencies at skill activation time using a POSIX shell preflight script. This catches missing or broken tools before a skill tries to use them — avoiding silent failures or confusing mid-workflow errors.

## Requirements Schema

Declare dependencies in your `module.yaml` under a `requirements` array:

```yaml
requirements:
  - tool: python3
    min_version: "3.11"
    required: true
    stdlib_modules: [json, tomllib, pathlib]
    why: "resolve_customization.py uses tomllib (3.11+) and json"
  - tool: gh
    min_version: "2.0"
    required: true
    auth_check: "gh auth status or GH_TOKEN/GITHUB_TOKEN env var"
    why: "GitHub Projects sync requires authenticated gh CLI"
  - tool: jq
    required: false
    why: "JSON processing fallback when Python unavailable"
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `tool` | yes | CLI tool name (must be on `$PATH`) |
| `min_version` | no | Minimum version as `major.minor` |
| `required` | yes | `true` = failure halts activation; `false` = warn only |
| `stdlib_modules` | no | Python stdlib modules to import-check (Python tools only) |
| `auth_check` | no | Description of how authentication is verified |
| `why` | no | Human-readable explanation of why this dependency exists |

The `requirements` section is declarative metadata — it documents what the module needs but is not executed by the installer. Runtime validation is handled by the preflight script.

## Preflight Script

Create a `preflight.sh` at your module root (alongside `module.yaml`). This script runs before any skill in the module activates.

### Rules

- **POSIX shell only** (`#!/bin/sh`) — no bashisms. The whole point is detecting broken runtimes, so the checker itself must have zero dependencies beyond a POSIX shell.
- **Structured output** — one `CHECK:` line per requirement, one final `PREFLIGHT:` summary line.
- **Exit codes** — `0` if all required checks pass, `1` if any required check fails. Warnings (optional tools missing) don't cause failure.

### Output Format

```
PREFLIGHT: module={module-code}
CHECK: tool={name} status={OK|FAIL|WARN} [version={ver}] [reason="{msg}"] required={true|false} [why="{msg}"]
...
PREFLIGHT: status={OK|FAIL} failures={n} warnings={n}
```

### Example Output

```
PREFLIGHT: module=gh-projects
CHECK: tool=python3 status=OK version=3.12 required=true
CHECK: tool=python3:json status=FAIL reason="ModuleNotFoundError: No module named 'json'" required=true
CHECK: tool=gh status=OK version=2.92 required=true
CHECK: tool=gh:auth status=OK method=token required=true
CHECK: tool=jq status=OK required=false
PREFLIGHT: status=FAIL failures=1 warnings=0
```

### Implementation Patterns

**Version comparison** — parse `major.minor` from tool output and compare numerically:

```sh
version_gte() {
  major1="${1%%.*}"; minor1="${1#*.}"
  major2="${2%%.*}"; minor2="${2#*.}"
  [ "$major1" -gt "$major2" ] && return 0
  [ "$major1" -eq "$major2" ] && [ "$minor1" -ge "$minor2" ] && return 0
  return 1
}
```

**Python module checks** — capture the exit code before piping through `tail`, since pipes mask the exit code:

```sh
check_python_module() {
  mod="$1"
  err=$(python3 -c "import $mod" 2>&1)
  rc=$?
  err=$(echo "$err" | tail -1)
  if [ $rc -ne 0 ]; then
    echo "CHECK: tool=python3:$mod status=FAIL reason=\"$err\" required=true"
    failures=$((failures + 1))
    return 1
  fi
  echo "CHECK: tool=python3:$mod status=OK required=true"
}
```

**Auth checks** — check environment variables first (for CI/codespace environments), then fall back to CLI auth commands:

```sh
check_gh_auth() {
  if [ -n "$GH_TOKEN" ] || [ -n "$GITHUB_TOKEN" ]; then
    echo "CHECK: tool=gh:auth status=OK method=token required=true"
    return 0
  fi
  gh auth status >/dev/null 2>&1
  if [ $? -ne 0 ]; then
    echo "CHECK: tool=gh:auth status=FAIL reason=\"not authenticated\" required=true"
    failures=$((failures + 1))
    return 1
  fi
  echo "CHECK: tool=gh:auth status=OK method=gh-auth required=true"
}
```

See `src/gh-projects-skills/preflight.sh` for a complete reference implementation.

## Skill Integration

### Step 0 in SKILL.md

Add a preflight step before the skill's normal activation sequence:

```markdown
### Step 0: Preflight Check

Run: `sh {project-root}/_bmad-modules/{module-code}/preflight.sh`

Parse the output. If the final `PREFLIGHT:` line shows `status=FAIL`:
- Report each `CHECK:` line with `status=FAIL` to the user, including the `reason` field
- **Halt activation.** Do not proceed to Step 1.

If `status=OK` (even with warnings), proceed normally.
If the preflight script is not found, skip this step.
```

### Wiring via customize.toml

Add the preflight to `activation_steps_prepend` so it runs before any workflow-resolved steps:

```toml
[workflow]

activation_steps_prepend = [
  "Run preflight check: `sh {project-root}/_bmad-modules/{module-code}/preflight.sh` — halt on FAIL status",
]
```

## Error Handling Guidance

- If the preflight script itself is missing, skills should skip Step 0 and proceed normally. Not all modules need preflight checks.
- Skills should report each failed check clearly, including the `reason` and `why` fields, so the user knows exactly what to install or fix.
- Never silently fall back to degraded behavior — the whole purpose of preflight is surfacing problems upfront.
