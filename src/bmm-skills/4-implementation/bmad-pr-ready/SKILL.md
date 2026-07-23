---
name: bmad-pr-ready
description: 'Stacked-PR projects only: curate minimal sanitized `-pr-ready` branches from completed feature branches. Use when the user says "create PR-ready branches", "clean the stack for upstream", or "prepare the story branches for review".'
---

# PR-Ready Stack Workflow

**Goal:** Produce a new reviewer-focused `-pr-ready` stack without rewriting its implementation
branches. Each layer keeps only upstream-relevant content, has the fewest meaningful commits, and is
proven content-equivalent to its source after declared exclusions.

**Your Role:** Upstream review curator. The LLM decides semantic commit boundaries and writes concise
reviewer-facing messages; deterministic scripts collect evidence, build trees, validate ancestry,
exclude local process artifacts, create safety refs, and push with exact leases.

## Conventions

- Bare paths resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory.
- `{project-root}` resolves to the project root.
- `{skill-name}` resolves to `bmad-pr-ready`.

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

<step n="1" goal="Establish immutable source and target topology">
  <action>Require a clean worktree and the project's stacked-branching rule. Fetch the upstream
  default branch and the fork remote. Never rewrite a source implementation branch.</action>
  <action>Enumerate the planning branch separately when it is parallel to Story 1. Enumerate story
  branches in stack order and record each branch's real source parent and exact tip SHA.</action>
  <action>Create a run directory beneath the repository Git directory:
  `bmad-pr-ready/&lt;UTC timestamp&gt;/`. Store evidence, manifest, overlays, and reports there so
  no branch is contaminated.</action>
  <check if="the stack is conflicted, a source parent is not an ancestor, or source scope is ambiguous">
    HALT before creating refs. Report the exact branch/topology defect.
  </check>
</step>

<step n="2" goal="Decide the smallest honest commit structure">
  <action>Collect immutable commit/file evidence with
  `uv run {skill-root}/scripts/collect_stack_evidence.py --help`.</action>
  <action>Default to one commit per story. Preserve multiple commits only when each has a genuinely
  different purpose, is independently coherent to review, and carries a useful dependency boundary.
  Squash fixups, review corrections, generated lock synchronization, status updates, and development
  narration into the implementation commit they complete.</action>
  <action>Record every keep/squash decision and rationale in the manifest described by
  `references/manifest-schema.md`. This judgment belongs to the LLM, never a subject-line heuristic.</action>
</step>

<step n="3" goal="Remove local process machinery without losing upstream code">
  <action>Exclude newly introduced `_bmad/**`, `_bmad-output/**`, `.agents/**`, `.claude/**`,
  `.cursor/**`, orchestration logs, generated review reports, and source prompts/specs that are not
  intended upstream changes. Never delete a matching path that already exists in the upstream base.</action>
  <action>If the planning layer would become empty, write one concise upstream-facing design document
  in the run directory and add it as a manifest overlay under the project's established docs convention.</action>
  <action>Make the planning PR-ready branch the stack root when it will be submitted first. Build Story
  1 on that clean planning layer even if the source planning branch was parallel to Story 1.</action>
</step>

<step n="4" goal="Prove the proposed stack before updating refs">
  <action>Run
  `uv run {skill-root}/scripts/build_pr_ready_stack.py &lt;manifest&gt; --dry-run -o &lt;report&gt;`.
  Resolve every source-tip, ancestry, excluded-path, patch-application, or tree-equivalence failure;
  never bypass a validator.</action>
  <action>Inspect each proposed diff from its previous PR-ready layer, its commit messages, package
  versions/lockfiles, and the complete proposed file inventory as an upstream reviewer would.</action>
</step>

<step n="5" goal="Create and optionally publish the PR-ready refs">
  <action>After the dry run and human-visible review are clean, rerun the builder with `--apply`.
  Add `--push` only when the user authorized updates to the configured fork remote.</action>
  <action>The builder must create timestamped safety refs for replaced targets and use exact
  force-with-lease protection. Never push to the upstream remote in this workflow.</action>
</step>

<step n="6" goal="Validate the PR-ready stack as the submitted product">
  <action>Verify every target ends in `-pr-ready`, forms one ancestry chain from upstream, contains no
  newly introduced excluded path, and matches the sanitized source deltas plus declared overlays.</action>
  <action>Build a fresh disposable cumulative branch from the PR-ready stack and run the repository's
  full integration/acceptance suite. A failing test blocks completion and routes to correction on the
  owning source story branch, followed by a fresh PR-ready rebuild.</action>
  <action>Report source/target SHAs, commit-boundary decisions, exclusions, safety refs, push results,
  manifest/report paths, and integration outcome.</action>
  <action>Run the resolved `{workflow.on_complete}` when non-empty.</action>
</step>

</workflow>
