---
name: bmad-integration-review
description: 'Stacked-PR projects only: rebuild a disposable cumulative integration branch, generate-if-missing and run epic-level plus project-level/cross-epic acceptance tests, then invoke bmad-review with only its adversarial lens and ask a human to triage its findings. Use when the user says "run integration review", "review the merged stack", or after an epic lands.'
---

# Integration Review Workflow

**Goal:** Prove that everything built so far — across every story and every epic, not just one
story's own diff — actually composes into a working, requirements-satisfying whole, and catch the
class of defect that only exists at the seam between stories or epics before it's mistaken for
"done."

**Your Role:** First act as a build/test engineer: rebuild the stack fresh, generate any missing
cumulative tests, run them for real, and STOP on any failure rather than rationalizing it away.
Then act only as a review orchestrator: invoke the canonical `bmad-review` skill, preserve its
findings unchanged, and leave significance decisions and corrective action under human control.

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
paths those tests cover actually run; the delegated review covers requirements and composed
behavior that the test suite may not exercise.

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
  <action>Resolve the live default-branch head and compare it against the base SHA the stack was last
  cascaded onto (from the cascade report, integration PR body, or validation evidence). This is a
  gate, not a recommendation: reviewing a stack whose base has moved reviews code that is about to
  change, and its per-layer evidence no longer describes any tree that will exist after merge.</action>

<check if="the live default-branch head differs from the recorded cascade base, or no base SHA was recorded">
  HALT and run **bmad-rebase-cascade** first, then re-validate every layer, then return here. Do not
  proceed on the strength of a prior run's pass counts — they were earned against a base that no
  longer exists. Only skip when the recorded base SHA equals the live default-branch head.
</check>
</step>

<critical>
**Default-branch freshness invariant.** Per-layer and integration validation are only valid against
the exact default-branch commit the stack was cascaded onto. The default branch keeps moving while
reviews are in flight, so that evidence goes stale on someone else's merge, not on any change of
yours.

Therefore: **re-cascade onto the current default-branch head, and re-validate, before stack review,
before submitting or refreshing PR bodies, and before merging.** Treat a cascade as expiring the
moment the default branch advances past the base it recorded.

Enforce it mechanically rather than by memory:
- Record the default-branch base SHA the cascade ran against in the cascade report, the integration
  PR body, and any validation evidence artifact.
- Before review/submit/merge, compare that recorded SHA against the live default-branch head. If they
  differ, the stack is stale: re-cascade and re-validate before proceeding.
- Never present per-layer green earned on a stale base as current evidence, and never restate a prior
  run's pass counts as if they still hold.

This is a standing invariant, not a one-time fix. A stack held open across many reviews will need
this repeatedly; that recurring cost is a reason to land lower layers promptly rather than hold the
whole chain open.
</critical>

<critical>
The cumulative integration branch is the single authoritative green gate — the **only** branch
whose checks must be fully green. Its entire
purpose is to prove one claim: *once every branch in this stack is merged into the default branch in
the order its PRs describe, the resulting combined changeset passes.*

An individual mid-stack layer is NOT required to be green. A layer routinely fails its own checks for
a purely logical reason — it uses a dependency, module, fixture, migration, or config that a LATER
layer in the same stack introduces. That is expected and correct in a stacked chain, not a defect.

Never restructure the stack to chase a mid-stack green: do not move dependency declarations to earlier
layers, do not pull version/pin bumps forward, do not reorder layers, and do not add skips or guards
purely to silence such a failure. All of that rewrites already-reviewed layers, invalidates approvals,
and changes what reviewers agreed to, in exchange for a signal that was never the gate.

Before "fixing" any failing check, classify it:

- **Later-layer dependency** (the thing it needs arrives further up the stack) → leave it alone; the
  integration branch is where it must pass. Note it in the PR body so reviewers aren't surprised.
- **Genuine defect** (real bug, flaky/non-deterministic test, test coupled to ambient developer
  config, failure that would persist even with the whole stack merged) → fix it, at its owning layer.
- **Partial-merge hazard** (the layer would break the default branch the moment it merges on its own,
  in the order its PR describes) → fix it, at the earliest layer that can carry the fix.

The first class never justifies touching the stack. The other two do.

The partial-merge class is narrow and easy to over-apply, so establish it with evidence, never with
a hunch. It only exists when the stack merges into the default branch incrementally, PR by PR, while
review is still in progress — the usual case, because reviews land at different times. Apply this
exact test:

1. Confirm the stack is based directly on the default branch, so merging a layer makes the default
   branch's tree *equal to that layer's tree* rather than a merge of divergent work.
2. Check out that layer in a **pristine worktree** and run the default branch's own required checks
   with the exact commands CI uses.
3. If they fail, the default branch will fail the same way the moment that PR merges.

A red check on a layer nobody has merged is a cosmetic signal. A red check on the default branch
blocks every other team working in the repository, and no feature flag protects against it — a flag
gates runtime behavior, not an import, a lockfile, a migration, or a build step. That is the whole
distinction: mid-stack green was never the gate, but the default branch always is.

When this class is confirmed, pulling a declaration or pin bump earlier is the correct fix rather
than the prohibited one, because it is no longer buying a green checkmark — it is keeping the default
branch releasable. Say so explicitly in the PR body and in the cascade report, so reviewers can see
which class was invoked and check the reasoning.
</critical>

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
  `bmad-correct-course` exists to triage. HALT: report which test(s) failed, in which category, and
  their output; recommend `bmad-correct-course`. Leave the integration branch in place for
  inspection.
</check>

  <action>Once every test passes, proceed to Step 5. Keep a short summary (suite(s) run, pass
  counts, which tests were newly generated in each category) — the review step uses it as
  corroborating evidence.</action>
</step>

<step n="5" goal="Delegate the cumulative review to canonical bmad-review">
  <action>Set `{review_report}` to
  `{planning_artifacts}/integration-review-epic-{scope_epic}-{date}.md`, or the equivalent path
  under `{implementation_artifacts}` only if `planning_artifacts` is unavailable.</action>
  <action>Invoke `skill:bmad-review lenses=adversarial` exactly once. Pass the following inputs
  without adding or restating review-lens instructions:</action>
  - **content:** the checked-out cumulative integration branch from its resolved base through HEAD,
    including the actual composed code.
  - **also_consider:** the full PRD, architecture document, feature spec, epics/acceptance-criteria
    material for epics 1 through `{scope_epic}`, every in-scope story file, and the unmodified
    functional-test summary from Step 4. Identify their resolved paths rather than summarizing
    their requirements for the reviewer.
  - **pre-resolved customization:** `report_path = {review_report}` and `output_format = "both"` so
    the persisted report includes the canonical findings JSON used for the integrity check.
  <action>Capture the canonical findings JSON array returned by `bmad-review`. Preserve the array
  exactly as returned: same finding order, fields, and values. Do not add severity, priority,
  ranking, identifiers, summaries, classifications, deduplication, or rewritten wording.</action>

<check if="the skill invocation fails, its announced lens plan is not exactly adversarial, or it does not return a valid JSON array">
  HALT. Report the invocation or lens-selection failure. Do not synthesize findings, infer a clean
  result, continue to human triage, or invoke corrective work.
</check>

<check if="{review_report} is missing or unreadable, or its canonical findings JSON is not identical to the returned array">
  HALT. Report the report-integrity failure and retain both outputs for diagnosis. Do not rewrite
  either output to make the check pass, continue to human triage, or invoke corrective work.
</check>
</step>

<step n="6" goal="Branch on the outcome — never auto-fix a cross-story/cross-epic gap">
<check if="the canonical findings array is []">
  Report success: every epic through `{scope_epic}` is now validated as a cumulative whole — the
  merged code was rebuilt fresh, its functional/acceptance tests pass (both epic-level and
  project-level/cross-epic), and canonical `bmad-review` returned `[]`. Leave the integration branch
  in place (local-only, never pushed) for reference.
</check>

<check if="the canonical findings array is non-empty">
  Present the findings unchanged and report `{review_report}`. Ask the human which findings, if any,
  represent significant cross-story or cross-epic gaps; array positions may be used only to select
  findings and must not be inserted into or otherwise alter them. HALT and wait for the human's
  selection. Do not decide significance yourself.
</check>

<check if="the human selects no findings as significant">
  Report that the cumulative tests passed and the human selected no significant cross-story or
  cross-epic gaps. Leave the report and local-only integration branch in place.
</check>

<check if="the human selects one or more findings as significant">
  Do not fix any finding or modify a story branch. Invoke `skill:bmad-correct-course`, passing the
  selected canonical findings unchanged together with `{review_report}`, the integration branch
  name, `{scope_epic}`, and the functional-test summary. State explicitly that the human selected
  these as significant cross-story/cross-epic gaps and that corrective planning and approval remain
  human-controlled. Leave the integration branch in place for inspection.
</check>
</step>

<step n="7" goal="Workflow Completion">
  <action>Summarize: `{scope_epic}` (and everything beneath it), the integration branch name, the
  functional-test summary, and the review outcome.</action>
  <action>If this run leaves behind the new best cumulative stack branch and the stack is still not in
  `main`, recommend refreshing the clean local anchor branch with `bmad-stack-working-branch` before
  any new Copilot/BMAD issue work begins.</action>
  <action>The integration branch is the head dependent repositories usually pin, so rebuilding it
  moves that pointer target. When another repository pins this one by revision, report that it must
  be repointed at the rebuilt integration head while this stack is unmerged and unreleased, and that
  any evidence embedding the old revision must be regenerated rather than edited. Follow the
  pointer-sync contract in `bmad-stack-working-branch`.</action>
  <action>Report workflow completion to user with personalized message: "Integration review
  complete, {user_name}!"</action>
  <action>Run: `python3 {project-root}/_bmad/scripts/resolve_customization.py --skill {skill-root} --key workflow.on_complete` — if the resolved value is non-empty, follow it as the final terminal instruction before exiting.</action>
</step>

</workflow>
