---
name: bmad-integration-review
description: 'Stacked-PR projects only: rebuild a disposable cumulative integration branch from the story-branch stack, generate-if-missing and run epic-level plus project-level/cross-epic acceptance tests against it, then run one antagonistic requirements-driven review of the composed result. Use when the user says "run integration review", "review the merged stack", or after an epic lands and you want to validate everything built so far actually works together.'
---

# Integration Review Workflow

**Goal:** Prove that everything built so far — across every story and every epic, not just one
story's own diff — actually composes into a working, requirements-satisfying whole, and catch the
class of defect that only exists at the seam between stories or epics before it's mistaken for
"done."

**Your Role:** You are two roles in sequence. First a build/test engineer: mechanical, no
creativity — rebuild the stack fresh, generate any missing cumulative tests, run them for real, and
STOP on any failure rather than rationalizing it away. Then, only once every test passes, an
antagonistic requirements reviewer: assume guilty until proven innocent by the actual merged code,
not by what the story files claim.

**This skill only applies to stacked-PR projects** — repos that have adopted a "one branch + one PR
per story, chained as a stack" convention instead of landing every story on one long-lived branch.
If your project doesn't use that convention, there's no separate stack to rebuild or review here;
see [When this skill doesn't apply](#when-this-skill-doesnt-apply) in Step 1.

**Why this exists as a separate pass from the per-story `bmad-code-review`:** a per-story review
only ever sees that one story's own diff — it structurally cannot catch a gap that only exists
*between* stories or epics (e.g. one story adds a processor module, and a later story that's
supposed to wire it into the live pipeline never actually does; or a config flag one epic
introduces is set but nothing in a later epic ever reads it). Each story's review looks clean in
isolation while the feature stays broken end-to-end. Passing functional tests prove the happy
paths those tests cover actually run; the review step catches everything a test suite doesn't
happen to exercise — acceptance criteria not implemented, requirements documented but skipped, and
cross-story/cross-epic quality issues.

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

- `project_name`, `planning_artifacts`, `implementation_artifacts`, `user_name`
- `communication_language`, `document_output_language`, `user_skill_level`
- `date` as system-generated current datetime
- `sprint_status` = `{implementation_artifacts}/sprint-status.yaml`
- `project_context` = `**/project-context.md` (load if exists)
- YOU MUST ALWAYS SPEAK OUTPUT in your Agent communication style with the config `{communication_language}`

### Step 5: Greet the User

Greet `{user_name}`, speaking in `{communication_language}`.

### Step 6: Execute Append Steps

Execute each entry in `{workflow.activation_steps_append}` in order.

Activation is complete. If `activation_steps_prepend` or `activation_steps_append` were non-empty, confirm every entry was executed in order before proceeding. Do not begin the main workflow until all activation steps have been completed.

<workflow>

<step n="1" goal="Confirm the stacked-PR convention and scope this run">
  <action>Look for the project's stacked-branching rule: `{project-root}/.agents/rules/story-branching-stacked-prs.mdc` (or its `.cursor/rules/` / `.claude/rules/` projection). Read it if found.</action>

<check if="no such rule file exists">
  <a id="when-this-skill-doesnt-apply"></a>
  HALT: "This project doesn't use the stacked-PR branching convention (no story-branching-stacked-prs rule found) — every story already lands on one shared branch, so there's no separate stack to rebuild or review here. A plain `bmad-code-review` of the current branch is the right tool instead."
</check>

  <action>Determine scope: read `{sprint_status}` and, for every epic present, mechanically enumerate whether every `{epic}-<number>-*` story key is `done`. The highest-numbered epic that is fully done (by that enumeration, across the whole file, not just recently-touched stories) is `{scope_epic}` — this run validates everything from epic 1 through `{scope_epic}`, inclusive.</action>

<check if="no epic is fully done yet">
  HALT: "No epic is fully `done` yet in sprint-status — there's nothing cumulative to validate. Finish at least one epic's stories first."
</check>

  <action>Tell the user which epic you resolved as `{scope_epic}` and confirm before proceeding (a single default "yes, that's right" prompt is enough — this is a scope confirmation, not a design decision).</action>
</step>

<step n="2" goal="Make sure the stack is fresh before validating it">
  <action>Strongly recommend running the **bmad-rebase-cascade** skill first if it hasn't been run very recently — validating a stale, unrebased stack produces a review of code that's about to change anyway. If the user confirms the stack is already fresh (e.g. `bmad-rebase-cascade` just ran), proceed without re-running it.</action>
</step>

<step n="3" goal="Rebuild a disposable cumulative integration branch">
  <action>Enumerate every `feat/*/story-*` branch: `git branch -a --list 'feat/*/story-*'`, sorted by epic number then story number, filtered to epic number `<= {scope_epic}`.</action>
  <action>Resolve the stack's true base: `git merge-base` of the FIRST (lowest epic.story) filtered branch against the repo's default branch.</action>
  <action>Create a fresh, local-only, never-pushed branch — never reuse a name from a previous run: `git checkout -b integration/epic-{scope_epic}-review-<timestamp> <that base commit>`.</action>
  <action>Merge every filtered branch into it, in stack order (lowest epic.story first): `git merge --no-ff <branch> -m "merge: <branch> into integration/epic-{scope_epic}-review-<timestamp>"`.</action>

<check if="any merge conflicts">
  `git merge --abort`. Do NOT attempt to resolve it yourself — a conflict here means the stack
  itself is no longer cleanly stacked (e.g. a fix landed on one branch without a follow-up rebase
  of the branches after it). HALT and report the branch and conflicting file list; recommend
  running `bmad-rebase-cascade` (or `bmad-correct-course` if the divergence is structural, not just
  stale) before retrying this skill.
</check>

  <action>Nothing here is pushed anywhere, and the branch is not deleted afterward — it's left in place so a human (or the next step) can inspect exactly what was tested and reviewed.</action>
</step>

<step n="4" goal="Generate-if-missing and run cumulative acceptance tests">
  <action>This step produces and runs TWO distinct categories of tests, both committed only on the
  disposable integration branch, never on any story branch:</action>
  - **Epic-level tests**: one per epic from 1 through `{scope_epic}`, proving that epic's own
    acceptance criteria hold end-to-end — not just asserted individually per story in isolation.
  - **Project-level / cross-epic tests**: a smaller, standing suite proving the PRD's overall
    goals/success criteria and any requirement or user journey that only makes sense once two or
    more epics compose together (e.g. "a value produced by an early epic is consumed correctly by a
    later epic's output path"). No amount of per-epic testing alone can catch this class of gap,
    since no single epic's own acceptance-criteria list would ever call it out.

  <action>Read the epics/acceptance-criteria file(s) under `{planning_artifacts}` for every epic 1
  through `{scope_epic}`, every story file under those epics in `{implementation_artifacts}`, and
  the full PRD under `{planning_artifacts}` for its overall goals and any cross-epic requirement or
  user journey.</action>
  <action>Inventory existing functional/acceptance-level suites already in the repo (distinct from
  per-story unit tests) — look for `tests/integration/`, `tests/e2e/`, or equivalently-named
  directories per this project's actual conventions for epic-level coverage, and a separate
  `tests/acceptance/`, `tests/project/`, or equivalently-named suite for project-level/cross-epic
  coverage; check each story's own File List / dev notes for what test types it already added.
  Do not invent a new convention if one already exists.</action>
  <action>For every epic in scope with no such cumulative test, generate one now, directly on the
  integration branch. For every PRD-level cross-epic requirement/user journey with no existing
  project-level test, generate one in the project-level suite, exercising the real code paths
  across the epics it spans (not mocked at the seam). Follow the spirit of the
  `bmad-qa-generate-e2e-tests` skill (use the project's existing test framework and patterns; happy
  path + explicit edge cases from the acceptance criteria; readable, minimal, no over-engineered
  fixtures) but skip that skill's own "ask the user what to test" step — derive scope yourself from
  the epics'/PRD's actual text, since this pass is meant to run without per-test user input.</action>
  <action>Commit the generated tests on the integration branch — one commit for any epic-level
  tests (`test(epic-{scope_epic}): add cumulative acceptance tests`), and, if any were generated, a
  separate commit for project-level tests (`test: add project-level cross-epic acceptance tests`).
  This branch is local-only and disposable, so these commits never need pushing.</action>
  <action>Run the full functional/acceptance suite (existing + newly generated, both categories)
  using this project's real test command(s) — detect the ecosystem rather than guessing (JS via
  `bun`/`npm`/`yarn`/`pnpm`; Python via `pytest`/`poetry`; `cargo test`; `go test`; whichever
  applies).</action>

<check if="any test fails, in either category">
  Do NOT treat this as a soft signal, and do NOT attempt to fix it yourself in this step — a
  functional break spanning multiple stories or epics is exactly the kind of "significant change"
  `bmad-correct-course` exists to triage, same as a CRITICAL/HIGH finding in the next step. HALT:
  report which test(s) failed, in which category, and their output; recommend
  `bmad-correct-course`. Leave the integration branch in place for inspection.
</check>

  <action>Once every test passes, proceed to Step 5. Keep a short summary (suite(s) run, pass
  counts, which tests were newly generated in each category) — the review step uses it as
  corroborating evidence.</action>
</step>

<step n="5" goal="Run one antagonistic, requirements-driven review of the merged result">
  <action>Prefer running this as a separate subagent/task with a clean context, if your runtime
  exposes one, rather than reusing this same conversation — an unbiased reviewer with no stake in
  the build/test steps just completed catches more than a continuation would. If no such capability
  is available, perform the review yourself in this same session instead of skipping it.</action>
  <action>The review must:</action>
  - Read the FULL PRD, epics/acceptance-criteria file, architecture document, and feature spec
    under `{planning_artifacts}` — scoped to epics 1 through `{scope_epic}`.
  - Read every story file in `{implementation_artifacts}` for stories in that scope.
  - Take the functional-test summary from Step 4 as corroborating evidence — tests passing narrows
    what's already proven; it does NOT excuse skipping the read-level review of everything the
    tests don't cover.
  - Inspect the ACTUAL merged code on the integration branch — not just story files' claims — to
    verify each acceptance criterion is genuinely satisfied, including criteria that only make
    sense when multiple stories/epics compose together.
  - Be adversarial: assume guilty until proven innocent by the code; do not pad the report with
    trivia.
  - Classify every finding as CRITICAL / HIGH / MEDIUM / LOW with file/line evidence and a quote
    proving the gap — the same rigor as `bmad-code-review`, but scoped to the whole merged stack
    (epics 1–`{scope_epic}`) instead of one story's diff.
  - Write the findings to `{planning_artifacts}/integration-review-epic-{scope_epic}-{date}.md`
    (or `{implementation_artifacts}/` if `planning_artifacts` is unavailable) with a summary table
    of finding counts by severity.
</step>

<step n="6" goal="Branch on the outcome — never auto-fix a cross-story/cross-epic gap">
<check if="0 CRITICAL and 0 HIGH findings">
  Report success: every epic through `{scope_epic}` is now validated as a cumulative whole — the
  merged code was rebuilt fresh, its functional/acceptance tests pass (both epic-level and
  project-level/cross-epic), and the antagonistic review found nothing CRITICAL or HIGH. Leave the
  integration branch in place (local-only, never pushed) for reference.
</check>

<check if="1+ CRITICAL or 1+ HIGH findings">
  Do NOT attempt to auto-fix — a cross-story or cross-epic integration gap is exactly the kind of
  "significant change" `bmad-correct-course` exists to triage (it may need new fix-stories, not a
  blind patch). Report the findings-report path and a one-line summary of the CRITICAL/HIGH counts,
  and recommend running `bmad-correct-course` next. Leave the integration branch in place — it's
  the exact artifact the findings report and functional tests were run against, so
  `bmad-correct-course` (or a human) can inspect it directly.
</check>
</step>

<step n="7" goal="Workflow Completion">
  <action>Summarize: `{scope_epic}` (and everything beneath it), the integration branch name, the
  functional-test summary, and the review outcome.</action>
  <action>Report workflow completion to user with personalized message: "Integration review
  complete, {user_name}!"</action>
  <action>Run: `python3 {project-root}/_bmad/scripts/resolve_customization.py --skill {skill-root} --key workflow.on_complete` — if the resolved value is non-empty, follow it as the final terminal instruction before exiting.</action>
</step>

</workflow>
