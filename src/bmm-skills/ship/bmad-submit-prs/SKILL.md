---
name: bmad-submit-prs
description: 'Submit a validated PR-ready branch stack as ordered, reviewer-friendly traditional pull requests with upstream-hosted heads, predecessor bases, explicit merge gates, stack maps, and durable cross-links. Use when the user says "submit the stacked PRs", "open the PR stack", or "publish the PR-ready branches".'
---

# Submit Stacked PRs Workflow

**Goal:** Submit a PR-ready stack as ordered GitHub pull requests. The first component targets the
canonical default branch, each later component targets its immediately previous PR-ready branch,
component heads live in the target repository, and the permanent-draft combined validation PR may
use an exact fork-hosted evidence head. Explicit reviewer gates preserve incremental merge order.

**Your Role:** Stacked-PR release operator. The LLM explains intent, risk, and
review guidance using the upstream template. Deterministic tooling validates refs and permissions,
publishes exact branch tips, creates or updates PRs idempotently, and cross-links the completed stack.

## Conventions

- Bare paths resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory.
- `{project-root}` resolves to the project root.
- `{skill-name}` resolves to `bmad-submit-prs`.

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

<step n="1" goal="Establish a traditional upstream-hosted GitHub stack">
  <action>Require a clean worktree, immutable target SHAs, the ordered PR-ready layers with the
  planning layer first, and a fresh fetch of every candidate remote. Require a published integration
  evidence branch whose exact commit descends from the final layer and contains a committed validation
  report. The report must prove every integration/functional command exited successfully, record the
  exact commands and counts, successful distribution builds with artifact hashes, and an explicit
  prefix-by-prefix partial-merge result. A prose assertion that tests passed is not evidence.</action>
  <action>Resolve and confirm the target, component-publish, and evidence roles independently.
  Component target/publish URLs must resolve to one repository; evidence may resolve to that
  repository or a same-network fork. In the standard fork topology use `upstream` for both component
  roles and `origin` for evidence. Show all repository identities, the exact default-base SHA, and
  ordered base/head pairs. Never silently fall back from upstream components to fork heads.</action>
  <action>Ask whether to submit automatically or generate a manual submission package, recommending
  automatic submission by default. Confirm the choice before creating any PR. Both modes use the same
  titles, upstream template or fallback template, body content, ordering, and stack navigation.</action>
  <critical>Use traditional component topology: the first PR base is the confirmed default base; each later PR base is
  the immediately previous `remote_branch`. Publish exact component heads only to the target
  repository; cross-repository component heads are forbidden. The combined validation PR alone may
  use the exact fork evidence head.
  These are initial review bases. After each predecessor merges, retarget/restack the next PR onto
  the default base and cascade all dependent branches before it can merge; otherwise GitHub would merge it
  into the predecessor branch. The combined validation PR alone also targets the default base and remains
  permanently draft.</critical>
  <action>Create a run directory beneath the Git directory:
  `bmad-submit-prs/&lt;UTC timestamp&gt;/`. Persist the manifest, rendered bodies, preflight report,
  and submission journal there.</action>
</step>

<step n="2" goal="Adopt the upstream review contract">
  <action>Discover the upstream PR template from the fetched default branch, including
  `.github/PULL_REQUEST_TEMPLATE.md`, `.github/pull_request_template.md`,
  `docs/PULL_REQUEST_TEMPLATE.md`, `docs/pull_request_template.md`,
  `PULL_REQUEST_TEMPLATE.md`, `pull_request_template.md`, or Markdown templates beneath
  `.github/PULL_REQUEST_TEMPLATE/` case-insensitively. If multiple templates apply, choose the closest feature template
  and record the choice.</action>
  <action>If none exists, use `references/pr-111-fallback-template.md`, derived from PR #111. Its
  seven level-two sections must each occur exactly once and in this exact order: Summary; Motivation
  and context; Changes; Testing; Risk, rollout, and compatibility; Reviewer guidance; Checklist.
  Record `template_source` as `bmad-submit-prs:pr-111-fallback`. A target-repository template remains
  authoritative whenever one applies.</action>
  <action>Choose a human-readable feature name and 1-4 succinct feature keywords without checking
  uniqueness; project titles as `<prefix>(stacked-pr: <keywords> [N/X]): <subject>`. Write a feature
  summary and body per layer.
  The plan explains feature, split, order, validation, and reviewer path. Implementation PRs link it
  and state scope, prerequisite, validation, and risk without repeating the design.</action>
  <action>Every body must link the published integration branch and immutable validation report,
  state the exact test result and built artifacts, and explain why each dependency-ordered partial
  merge is safe. When safety relies on a feature flag, name it, prove it defaults disabled, and state
  that the disabled path does not import or initialize the gated runtime.</action>
  <action>Start every body with a warning that lists and links each PR that must already be merged.
  The first PR identifies itself as the planning PR and must merge first. Explain that reviewers must
  refresh Files changed after prerequisites merge and stop if prerequisite changes remain. Squash or
  rebase merges require the release operator to restack remaining heads before review.</action>
  <action>Create a separate combined-stack validation body from the integration evidence branch.
  It must say **DO NOT MERGE**, explain that its only purpose is to run target-repository GitHub checks
  against the complete integrated tree, link every component PR in order, and direct code review and
  merging back to those component PRs. Distinguish committed local evidence from GitHub check status.</action>
</step>

<step n="3" goal="Create a fail-closed submission manifest">
  <action>Write the schema in `references/submission-manifest.md`. Record the target repository and
  remote,   default base and its exact SHA, per-layer predecessor bases, target-repository branch names, and
  exact local `tip` SHAs. Record mandatory `evidence_remote` and `evidence_repository` fields and
  include the required structured
  `integration_evidence`; unsupported prose claims
  are not a substitute.</action>
  <action>Run
  `uv run {skill-root}/scripts/submit_pr_stack.py &lt;manifest&gt; --dry-run --output &lt;journal&gt;`.
  Review titles, bases, heads, SHAs, bodies, table, and graph; add `--verbose` for sanitized commands
  and per-layer progress.</action>
  <critical>In fork-to-upstream topology, this first canonical dry run is input to origin review
  only. Create a complete namespaced origin component stack plus a separate permanent-draft
  **DO NOT MERGE** integration-proof PR at the exact evidence SHA. Audit heads, bases, bodies,
  evidence SHA, and draft state live on origin, then stop for human review.</critical>
  <action>Continue upstream only after the human explicitly says
  `approve origin review for upstream submission`. Bind the canonical live origin audit receipt,
  its SHA-256, and the audited pre-review manifest/journal hashes in `origin_review`. Use
  `prepare_upstream_submission.py --mode prepare` to create a new run directory and copy every
  source body before regenerating the manifest and dry-run journal. Seal it with `--mode seal`.
  The submitter must re-query every live origin PR and reject drift. Never reuse the
  pre-origin-review package or stale receipts.</action>
  <check if="authentication, push permission, target SHA, ancestry, upstream remote identity, or an existing PR conflicts">
    Report the exact failed invariant before branch publication or PR creation. Ask the user to
    correct upstream state or stop safely; never choose another target or silently flatten the stack.
  </check>
  <action>If a layer's head has a CLOSED PR that cannot legitimately be reused (for example GitHub
  permanently refuses to reopen a PR whose head branch was force-pushed after closing), do not
  silently work around it. Confirm with the human that the old PR should remain closed as historical
  record and that a fresh PR should be created for that head, then record its number in that layer's
  manifest `superseded_prs`. Never add a number to that list to bypass a routine "PR already exists"
  conflict — only for a confirmed unreopenable-PR case.</action>
</step>

<step n="4" goal="Submit or update the stack in dependency order">
  <check if="the user chose manual submission">
    Run the script with `--manual` and a dedicated `--rendered-dir`. It must create numbered
    `NN-title.txt` and `NN-body.md` files, `SUBMIT.md`, `manual-links.json`, and the journal without
    creating or editing any PR. It also renders `integration-title.txt` and `integration-body.md`;
    `SUBMIT.md` creates that PR last with `--draft`. It gives exact web and `gh` submission instructions,
    base/head pairs, and review order. Tell the user where the package, instructions, manifest, and
    journal live.
    After each PR is created, record its number/URL in `manual-links.json` and rerun with
    `--manual-links` before creating the next PR, so every merge gate lists linked prerequisites while
    future nodes stay Pending. If that file is absent or incomplete, discover and validate existing
    component PRs by exact target-owned head/base/SHA and emit navigation-comment instructions instead of
    duplicate create commands; fail closed with precise reconciliation instructions on any conflict.
    Each rerun emits edit commands for existing PRs and a draft-create command only for the next
    contiguous layer. After creating the draft integration PR, rerun once more so its
    URL is added to every component body before any component is marked ready. Then skip the
    automatic-submission actions below.
  </check>
  <check if="the user chose automatic submission">
  <action>After origin review approval and approval of the regenerated human-visible upstream dry
  run, run the script with `--apply`. In fork topology the script rejects apply unless
  `origin_review` binds the exact audited origin receipt and approval phrase. Stop again for human
  review of the regenerated package, require `approve regenerated upstream dry run`, and validate
  its apply request with `prepare_upstream_submission.py --mode validate-apply`.
  Pass both `--approved-dry-run-journal` and `--approved-apply-request` to automatic or manual
  submission. The submitter independently revalidates that sealed request and current source/PR-ready
  placement immediately before mutation, submits the reviewed title/body bytes exactly, and places live
  PR navigation in comments rather than rewriting approved bodies. It then preflights
  all remote and GitHub invariants before side effects, publishes exact SHAs to the publish remote with
  force-with-lease, and creates every PR against its per-layer base. Create new PRs as drafts so
  none becomes reviewable before its warning and links are complete.</action>
  <action>Reuse an open PR only when head and base match; refuse closed, duplicate, or mismatched state.
  Persist after each success. Retry transient reads and idempotent writes with bounded backoff, but
  leave ambiguous creates to an idempotent rerun that reconciles remote state from the journal.</action>
  <action>During sequential creation, prior PR titles and graph nodes are clickable and future nodes
  are marked pending. Explain stacked PRs with a link to `https://www.stacking.dev/`. After all PRs
  exist, preserve every approved body and add one marker comment per PR with the complete linked graph
  and ordered table, verify the target base has not moved, then mark PRs ready unless the manifest
  requests drafts.
  Do not create duplicate navigation comments on retry.</action>
  <action>After every component PR exists, create or update one combined-stack validation PR from the
  exact integration evidence branch to the target default branch. Validate its remote, repository,
  fork network, owner, branch, and SHA. Keep it draft permanently, link it
  from every component PR, and fail if an existing validation PR is ready, closed, moved, or mismatched.
  In fork topology do not copy the evidence branch to the target repository; use its cross-repository
  head after the equivalent origin evidence PR has been approved.</action>
  <check if="branch publication or PR submission fails after side effects begin">
    Persist the journal and show every branch and PR already created. Ask the user to retry the same
    upstream topology or stop safely. Never close, delete, retarget, or rewrite partial results
    without separate approval.
  </check>
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

<step n="5" goal="Prove the reviewer experience and hand off safely">
  <action>Query every submitted PR and verify: expected repository, exact head SHA, expected base,
  upstream head repository owner for every component, correct per-layer base and open/draft state, planning link,
  complete navigation graph, explicit
  linked prerequisite warning, integration branch and immutable report links, exact test/build evidence,
  and feature-flag safety statement. Each component diff must contain only its predecessor-relative
  layer.</action>
  <action>Verify the combined-stack validation PR is open and draft with the validated evidence owner at the exact integration commit,
  links every component PR, contains the immutable evidence links, and says not to merge. Report its
  GitHub checks as pending, passing, or failing from live data; never infer CI success from local tests.</action>
  <action>For automatic submission, report the planning PR first, then a table of every PR number,
  clickable URL, base/head branch, source SHA, and status. For manual submission, do not invent a PR
  summary; report the package, instructions, title/body files, manifest, links file, and journal paths.</action>
  <action>Explain merge order: land PRs strictly from 1 through N. After each merge, refresh later PRs
  only after retargeting/restacking the next PR onto the default base and cascading all dependent heads. Without
  that step GitHub would merge the next PR into the predecessor branch. Refresh later PRs so GitHub
  recalculates their diffs, and stop if any prerequisite changes remain. Never delete publish-remote
  head branches until their PRs merge or close.</action>
  <action>If the stack remains unmerged after submission, recommend `bmad-stack-working-branch` as
  the default way to provision or refresh the clean local anchor branch for new interim work. Do not
  suggest starting fresh issue work from `main` or directly on a review-only `-pr-ready` branch.</action>
  <action>Before declaring the stack submitted, verify that every dependent repository that pins this
  one by revision points at the head just published, for as long as this stack is unmerged and
  unreleased. Submission is the last checkpoint before reviewers and downstream builds consume the
  stack, so a pointer left on a superseded commit means they validate something the stack no longer
  contains. Follow the pointer-sync contract in `bmad-stack-working-branch`.</action>
  <action>Run the resolved `{workflow.on_complete}` when non-empty.</action>
</step>

</workflow>
