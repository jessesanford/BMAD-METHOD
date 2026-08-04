---
name: bmad-extend-pr-stack
description: 'Stacked-PR projects only: fold newly prepared story PRs into an existing upstream stack, recalculate layer numbering/titles, rebuild every affected PR body with the canonical BMAD template, and retire superseded validation/draft paths. Use when the user says "extend the existing stack", "fold these PRs into the larger stack", "recalculate the stack bodies", or "add these stories to the current PR stack".'
---

# Extend Existing PR Stack Workflow

**Goal:** Take one or more newly prepared PRs that belong behind an already-open stacked PR chain and
turn them into a true continuation of that stack rather than a side stack. Every affected PR must use
the canonical BMAD stacked-PR rendering, the numbering must be recalculated across the full open
component chain, superseded drafts must be retired once replacements are live, and the integration PR
must represent the complete stack against the correct base.

**Your Role:** Stacked-PR stack curator. Deterministic tooling owns topology checks, canonical body
rendering, and exact GitHub mutations; the LLM decides which PRs belong to the continuing chain,
which existing content must be preserved, what has been superseded, and whether the observed diff/base
problem is a PR metadata issue or a real branch-topology issue.

## Conventions

- Bare paths resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory.
- `{project-root}` resolves to the project root.
- `{skill-name}` resolves to `bmad-extend-pr-stack`.

## On Activation

1. Resolve customization:
   `python3 {project-root}/_bmad/scripts/resolve_customization.py --skill {skill-root} --key workflow`.
   On failure, merge `customize.toml`, `{project-root}/_bmad/custom/{skill-name}.toml`, then
   `{project-root}/_bmad/custom/{skill-name}.user.toml`: scalars override, tables deep-merge,
   keyed arrays-of-tables replace/append, other arrays append.
2. Execute `{workflow.activation_steps_prepend}` in order and load `{workflow.persistent_facts}`;
   `file:` entries resolve under `{project-root}`.
3. Load `{project-root}/_bmad/bmm/config.yaml`; resolve `user_name`, `communication_language`,
   `user_skill_level`, and the current datetime. Communicate in the configured language.
4. Greet `{user_name}`, then execute `{workflow.activation_steps_append}` in order.

<workflow>

<step n="1" goal="Identify the real stack that must be extended">
  <action>Require the live GitHub PR chain, not guesswork from branch names alone. Enumerate the
  current component PR stack in merge order, its planning/root PR if present, and any newly created
  continuation PRs the user says belong behind that chain.</action>
  <action>Prefer the project's canonical stacked-PR renderer and submission tooling over hand-written
  prose. Use `src/bmm-skills/ship/bmad-submit-prs/scripts/submit_pr_stack.py` as the
  source of truth for stack titles, merge gates, graphs, navigation, and integration PR bodies.</action>
  <action>Fail closed if the proposed "new stories" are actually a separate branch line, target a
  different repository, or cannot be placed unambiguously after the current final open component PR.</action>
  <critical>Do not create a parallel mini-stack when the user's intent is to extend an existing
  upstream chain. The correct outcome is one enlarged ordered stack with recalculated `[N/X]` labels
  across every still-relevant component PR.</critical>
</step>

<step n="2" goal="Determine whether the problem is metadata or graph topology">
  <action>Check the actual ancestry of the component heads and the integration/evidence branch before
  rewriting anything. Distinguish:
  1. a true branch-topology problem that requires rebasing/cascading, from
  2. a GitHub PR metadata problem (wrong base branch, stale numbering, stale bodies, wrong draft set)
  with an already-correct commit graph.</action>
  <action>If the stack already descends from the current upstream default branch, do not churn branch
  SHAs just to "freshen" the stack. Fix the PR metadata instead. If the graph is stale, route to the
  project's rebase/cascade workflow first and only continue once the source or PR-ready stack is
  structurally correct.</action>
  <action>Record the exact default-base SHA, the first component branch merge-base with that default
  branch, every predecessor relationship, and whether the integration branch descends from the final
  component head.</action>
</step>

<step n="3" goal="Recompute canonical titles and bodies across the full open stack">
  <action>Create a run directory beneath the Git directory:
  `bmad-extend-pr-stack/&lt;UTC timestamp&gt;/`. Store exported live PR metadata, cleaned title inputs,
  rendered markdown, update manifests, and reconciliation notes there.</action>
  <action>Extract or reconstruct each PR's preserved semantic body content (`Summary`, `Motivation and context`,
  `Changes`, `Testing`, `Risk, rollout, and compatibility`, `Reviewer guidance`, `Checklist`) while
  treating the stack wrapper as disposable. Never hand-maintain merge gates, graph nodes, navigation
  tables, or layer counts.</action>
  <action>Normalize raw title inputs before rendering. If a continuation PR was already published with
  a temporary stack label or nested `stacked-pr(...)` wrapper, strip it back to the clean underlying
  title before re-rendering so the canonical renderer does not double-stack the prefix.</action>
  <action>Re-render:
  1. every newly added continuation PR,
  2. every still-open earlier component PR whose numbering/navigation must now reference the enlarged stack,
  3. the combined validation/integration PR body.</action>
  <critical>When the open component stack grows from `X` layers to `Y`, update all affected open
  component PRs so each body claims the same stack size and navigation graph. Updating only the new
  PRs is incorrect and leaves reviewers with contradictory merge gates.</critical>
</step>

<step n="4" goal="Apply the stack extension live without losing reviewer context">
  <action>Update live GitHub PR titles and bodies in place, preserving each PR's core reviewer-facing
  content but replacing the stack wrapper, merge-warning block, numbering, prerequisites, graph, and
  navigation with the canonical BMAD render.</action>
  <action>Ensure every component PR states partial-merge safety in terms of the full recalculated
  stack, including any required feature-flag default-off guarantee when that is the basis for safe
  incremental merges.</action>
  <action>Update the combined validation PR so it links the full component chain, stays draft, and
  clearly says it is for full-stack CI/evidence only and not for code review or merging.</action>
  <action>If the integration PR's GitHub base branch is what makes the diff look partial, retarget it
  to the true default branch instead of rewriting a graph that is already correct.</action>
</step>

<step n="5" goal="Retire superseded drafts and alternative review paths">
  <action>Identify drafts or audit PRs that are now superseded by the enlarged canonical stack or by
  the current integration/validation PR. Close them with an explanation pointing reviewers at the
  surviving canonical PR(s).</action>
  <action>Never close the active component stack or the canonical validation PR as "superseded". Only
  retire PRs whose purpose has been fully replaced and whose continued presence would distract or
  confuse reviewers.</action>
  <action>If fork-side review-only drafts exist in addition to upstream component PRs, retire the fork
  drafts once the upstream stack is correct and cross-linked.</action>
</step>

<step n="6" goal="Prove the enlarged stack is coherent">
  <action>Verify every component PR in the live stack has:
  - the expected `[N/X]` numbering,
  - the expected prerequisite warning,
  - the full enlarged graph/navigation,
  - the intended feature keywords / title projection,
  - the correct GitHub base/head relationship,
  - the explicit partial-merge safety statement.</action>
  <action>Verify the integration PR targets the correct default branch, points at the intended
  evidence/integration head, stays draft, and presents the full stack diff against the default branch.</action>
  <action>Report which PRs were updated, which superseded drafts were closed, whether any rebase was
  actually required, and where the rendered artifacts and update manifest were stored.</action>
  <action>When the canonical integration/validation head changed, direct the user to refresh the
  clean anchor branch with `bmad-stack-working-branch` before starting new interim work. The default
  start point for fresh Copilot/BMAD issue work should follow the full stack culmination, not a stale
  prep branch or one component PR-ready branch.</action>
  <action>Run the resolved `{workflow.on_complete}` when non-empty.</action>
</step>

</workflow>
