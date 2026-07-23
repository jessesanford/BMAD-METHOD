---
name: bmad-rebase-cascade
description: 'Stacked-PR projects only: refresh the whole story-branch stack against upstream, cascading a rebase through every branch in order. Use when the user says "rebase the stack", "cascade rebase the stories", or after an epic lands and the stack may be stale.'
---

# Rebase Cascade Workflow

**Goal:** Keep a stacked-PR story-branch chain fresh against the real upstream, without ever
force-rewriting the default branch or silently resolving a conflict that needs a human decision.

**Your Role:** Developer performing routine stack maintenance — mechanical, not creative. There is
exactly one correct outcome (every branch replays cleanly on the newly-rebased branch before it); a
conflict anywhere means STOP and hand it to a human, not improvise a resolution.

**This skill only applies to stacked-PR projects** — repos that have adopted a "one branch + one PR
per story, chained as a stack" convention instead of landing every story on one long-lived branch. If
your project doesn't use that convention, there's nothing here to cascade; see
[When this skill doesn't apply](#when-this-skill-doesnt-apply) in Step 1.

## Conventions

- Bare paths (e.g. `checklist.md`) resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory (where `customize.toml` lives).
- `{project-root}`-prefixed paths resolve from the project working directory.
- `{skill-name}` resolves to the skill directory's basename.

## On Activation

### Step 1: Resolve the Workflow Block

Run: `python3 {project-root}/_bmad/scripts/resolve_customization.py --skill {skill-root} --key workflow`

**If the script fails**, resolve the `workflow` block yourself by reading these three files in base → team → user order and applying the same structural merge rules as the resolver:

1. `{skill-root}/customize.toml` — defaults
2. `{project-root}/_bmad/custom/{skill-name}.toml` — team overrides
3. `{project-root}/_bmad/custom/{skill-name}.user.toml` — personal overrides

Any missing file is skipped. Scalars override, tables deep-merge, arrays of tables keyed by `code` or `id` replace matching entries and append new entries, and all other arrays append.

### Step 2: Execute Prepend Steps

Execute each entry in `{workflow.activation_steps_prepend}` in order before proceeding.

### Step 3: Load Persistent Facts

Treat every entry in `{workflow.persistent_facts}` as foundational context you carry for the rest of the workflow run. Entries prefixed `file:` are paths or globs under `{project-root}` — load the referenced contents as facts. All other entries are facts verbatim.

### Step 4: Load Config

Load config from `{project-root}/_bmad/bmm/config.yaml` and resolve:

- `project_name`, `user_name`
- `communication_language`, `user_skill_level`
- `date` as system-generated current datetime
- YOU MUST ALWAYS SPEAK OUTPUT in your Agent communication style with the config `{communication_language}`
- Language MUST be tailored to `{user_skill_level}`

### Step 5: Greet the User

Greet `{user_name}`, speaking in `{communication_language}`.

### Step 6: Execute Append Steps

Execute each entry in `{workflow.activation_steps_append}` in order.

Activation is complete. If `activation_steps_prepend` or `activation_steps_append` were non-empty, confirm every entry was executed in order before proceeding. Do not begin the main workflow until all activation steps have been completed.

<workflow>

<step n="1" goal="Confirm the stacked-PR convention is in use">
  <action>Look for the project's stacked-branching rule: `{project-root}/.agents/rules/story-branching-stacked-prs.mdc` (or its `.cursor/rules/` / `.claude/rules/` projection). Read it if found — it defines the exact branch-naming and stacking convention this workflow must respect.</action>

<check if="no such rule file exists">
  <a id="when-this-skill-doesnt-apply"></a>
  HALT: "This project doesn't use the stacked-PR branching convention (no story-branching-stacked-prs rule found) — every story already lands on one shared branch, so there's no separate story-branch stack to cascade-rebase. Nothing to do here."
</check>
</step>

<step n="2" goal="Resolve the upstream-of-record and fetch it">
  <action>Determine the rebase remote: run `git remote get-url upstream` — if it succeeds, the rebase remote is `upstream` (the canonical/shared repo a fork stays in sync with); otherwise it's `origin`. Never push to `upstream` in this workflow, only fetch from it.</action>
  <action>Determine the repo's real default branch: `git symbolic-ref --short refs/remotes/origin/HEAD` (strip the `origin/` prefix), falling back to `main` if that's empty.</action>
  <action>Fetch it fresh: `git fetch <rebase-remote> <default-branch>`.</action>
  <action>Report which remote and branch you resolved to the user before doing anything destructive.</action>
</step>

<step n="3" goal="Fast-forward the local default branch — never rewrite it">
  <action>`git checkout <default-branch>` then `git merge --ff-only <rebase-remote>/<default-branch>`.</action>
  <action>Record the branch's pre-merge tip as `old_base` and its post-merge tip as `new_base`.</action>

<check if="the fast-forward fails (local default branch has diverged — commits exist on it that aren't in `<rebase-remote>/<default-branch>`)">
  HALT immediately. Do NOT force, rebase, reset, or merge the default branch any other way — a
  diverged default branch usually means something landed there that shouldn't have (e.g. a test
  merge committed directly to it instead of to a disposable branch). Report the divergent commits
  (`git log <rebase-remote>/<default-branch>..<default-branch> --oneline`) and ask the user how they
  want to reconcile it before proceeding.
</check>
</step>

<step n="4" goal="Enumerate the current story-branch stack">
  <action>List every stacked story branch: `git branch -a --list 'feat/*/story-*'`, sorted by epic number then story number (this is the exact chain order the stack was built in — each branch is based on the one before it, and an epic's first story is based on the previous epic's last story, not on the default branch).</action>

<check if="no such branches exist">
  HALT: "No `feat/*/story-*` branches found — nothing to cascade. (The default branch itself is
  already fast-forwarded to `<rebase-remote>/<default-branch>`.)"
</check>

  <action>Show the resolved stack order to the user before proceeding, so they can sanity-check it matches their mental model of the stack.</action>
</step>

<step n="5" goal="Cascade-rebase every branch, threading old/new tips forward">
  <action>Starting from `prev_old = old_base`, `prev_new = new_base` (Step 3), process each branch in stack order:</action>

  1. Record its current tip: `branch_old = $(git rev-parse <branch>)`.
  2. `git rebase --onto <prev_new> <prev_old> <branch>` — this replays only the commits unique to
     `<branch>` (relative to its own previous parent state, `prev_old`) onto the freshly-rebased
     `prev_new`.
  3. Record its new tip: `branch_new = $(git rev-parse <branch>)`.
  4. Set `prev_old = branch_old`, `prev_new = branch_new` for the next branch in the stack.

<check if="any rebase in this loop conflicts">
  `git rebase --abort` immediately. Do NOT attempt to resolve the conflict yourself — per this
  workflow's whole design, a conflict here means the stack itself needs a human look (e.g. a fix
  landed on one branch without a follow-up rebase of the branches after it), not an autonomous
  resolution. Report the branch and the conflicting file list (`git diff --name-only
  --diff-filter=U` before aborting), and STOP the workflow here — do not continue cascading the
  remaining branches on top of an unresolved gap.
</check>

  <action>Track a running table of `branch | old SHA | new SHA | status` as you go — you'll present this at the end.</action>
</step>

<step n="6" goal="Push the rebased branches back to origin">
  <action>For every branch that was successfully rebased in Step 5, best-effort push it: `git push --force-with-lease origin <branch>`. Never push to `upstream`.</action>
  <action>A push failure (offline, permissions, no `gh` auth) must NOT be treated as a workflow failure — the rebase itself already succeeded locally. Log it clearly in the final report as "rebased locally, not yet pushed" so the user can push it manually.</action>
</step>

<step n="7" goal="Report the outcome">
  <action>Present the full `branch | old SHA | new SHA | status` table from Step 5, plus the push result from Step 6 for each branch.</action>
  <action>If everything cascaded and pushed cleanly, tell the user the stack is fresh and safe to continue implementation or run `bmad-integration-review` on.</action>
  <action>Report workflow completion to user with personalized message: "Rebase cascade complete, {user_name}!"</action>
  <action>Run: `python3 {project-root}/_bmad/scripts/resolve_customization.py --skill {skill-root} --key workflow.on_complete` — if the resolved value is non-empty, follow it as the final terminal instruction before exiting.</action>
</step>

</workflow>
