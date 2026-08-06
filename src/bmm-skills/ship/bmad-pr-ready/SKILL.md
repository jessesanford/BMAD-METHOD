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

<step n="1" goal="Establish immutable source and target topology">
  <action>Require a clean worktree and the project's stacked-branching rule. Fetch the canonical
  upstream default branch and every origin source head. Never rewrite a source implementation
  branch or publish one to upstream.</action>
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
  <action>`_bmad/**` and `_bmad-output/**` are exclusively this repo's local BMAD process
  machinery (planning/implementation-artifact scratch state). They are **never** legitimate upstream
  content under any circumstance. Exclude them from every PR-ready layer unconditionally — this
  exclusion is absolute and does NOT fall under the "already exists in the upstream base" carve-out
  below. If a prior PR-ready build ever leaked one of these paths into an already-published
  `-pr-ready` branch or the upstream default branch itself, that is a bug to fix by removing it now,
  not a precedent to preserve. Also exclude orchestration logs, generated review reports, and source
  prompts/specs that are not intended upstream changes, using the same unconditional rule.</action>
  <action>`.agents/**`, `.claude/**`, `.cursor/**` are treated differently: some target repos
  intentionally vendor agent-guidance projections upstream. For these paths only, exclude newly
  introduced content but never delete a matching path that already exists in the upstream base.</action>
  <action>Before dropping any excluded path, search the tracked (non-excluded) source tree for real
  inbound references to it — imports, build/config file paths, doc links, or other content that is
  used by or points at a file under `_bmad/**` or `_bmad-output/**`. If such a reference exists, the
  referenced artifact is not disposable process scratch; relocate it into an appropriate
  non-underscore-prefixed location that matches the project's established convention (e.g. `docs/`,
  or a source directory the referencing code/doc already lives in), update the referencing path(s)
  accordingly, and only then drop the rest of the excluded directory. Record every relocation
  (old path, new path, referencing file) in the manifest/report.</action>
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
  <action>After the dry run and human-visible review are clean, rerun the builder with `--apply
  --push`. Only sanitized PR-ready feature-head hosting is authorized on upstream independently of
  final PR creation or readiness; dry-run submission requires these exact remote heads.</action>
  <action>The builder must create timestamped local safety refs for replaced targets and use exact
  force-with-lease protection. Validate every effective `remote.upstream.pushurl` against the
  canonical fetch repository. In a fork release, the manifest base/publish remote must be `upstream`
  and its source remote must be `origin`; verify every source tip on origin, and never publish
  PR-ready heads to origin or a fork. Single-repository projects may use one repository for all
  roles. Refuse to replace a target used
  by any open or closed PR without explicit
  human authorization; this workflow provides no automatic bypass.</action>
  <check if="any lease, push, remote-SHA verification, push-URL, or existing-PR-head check fails">
    HALT needs-attention. Do not continue to release validation or submission with stale upstream
    heads.
  </check>
</step>

<critical>
Mid-stack layers are not required to pass their own checks. A layer that fails only because it needs
something a LATER layer in the same stack introduces is behaving correctly for a stacked chain. The
integration/validation branch is the single authoritative green gate — it proves the stack passes once
merged in order.

Do not reorder layers, move dependency declarations earlier, pull pin/version bumps forward, or add
skips solely to chase a mid-stack green. That rewrites reviewed layers and discards approvals to
chase a signal that was never the gate. Fix a failing check only when it is a genuine defect — one
that would still fail with the entire stack merged — and fix it at its owning layer.

One narrow exception: if the layer would break the DEFAULT BRANCH the moment its own PR merges, then
pulling a declaration or pin bump earlier is the correct fix, not the prohibited one. This arises when
the stack sits directly on the default branch, so merging a layer makes the default branch's tree
equal to that layer's tree. Prove it before acting — check that layer out in a pristine worktree and
run the default branch's own required checks with the exact commands CI uses — and record which class
you invoked in the PR body and the cascade report. A feature flag does not cover this case: it gates
runtime behavior, not an import, a lockfile, a migration, or a build step. Mid-stack green was never
the gate; the default branch always is.
</critical>

<step n="6" goal="Validate the PR-ready stack as the submitted product">
  <action>Verify every target ends in `-pr-ready`, forms one ancestry chain from upstream, contains no
  newly introduced excluded path, and matches the sanitized source deltas plus declared overlays.</action>
  <action>Additionally assert, as a hard unconditional invariant regardless of any prior history:
  `git ls-tree -r --name-only &lt;target&gt;` contains zero paths matching `_bmad/**` or `_bmad-output/**`
  for every PR-ready target in the stack. This check has no "already existed upstream" exception —
  fail closed and report the offending branch/path if it ever matches, rather than treating a past
  leak as acceptable baseline.</action>
  <action>Define strict argv-only tests, builds/artifact globs, and default/disabled feature-flag
  checks in an evidence config. Run `python3 {skill-root}/scripts/produce_validation_evidence.py
  &lt;applied-report&gt; &lt;config&gt; --repo {project-root} --branch &lt;evidence-branch&gt;`.
  The producer validates every prefix in a detached worktree, hashes final artifacts, commits its
  report, and emits the `integration_evidence` fragment required by `bmad-submit-prs`.</action>
  <action>Any stale target, command, parsed test summary, artifact, ancestry, or ref-lease failure
  blocks completion without updating the evidence branch. Correct the owning source story, rebuild
  the PR-ready stack, and rerun the producer; use `--expected-old &lt;full-SHA&gt;` to replace an
  existing evidence ref exactly.</action>
  <action>Report source/target SHAs, commit-boundary decisions, exclusions, safety refs, push results,
  manifest/report paths, and integration outcome.</action>
  <action>Clarify that `-pr-ready` branches are upstream review artifacts, not the default starting
  point for new interim issue work. When the stack is still unmerged, direct the user to
  `bmad-stack-working-branch` for the clean local anchor branch that should seed future work.</action>
  <action>Curating these branches produces new commits, so any dependent repository that pins this
  one by revision is now behind. Report which pointers must move to the current canonical head while
  this stack is unmerged and unreleased, and never point a dependent at a `-pr-ready` branch as if it
  were the stack head. Follow the pointer-sync contract in `bmad-stack-working-branch`.</action>
  <action>Run the resolved `{workflow.on_complete}` when non-empty.</action>
</step>

</workflow>
