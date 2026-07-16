---
name: bmad-submit-prs
description: 'Submit a validated PR-ready branch stack as ordered, reviewer-friendly pull requests with one target base, fork-hosted heads, explicit merge gates, stack maps, and durable cross-links. Use when the user says "submit the stacked PRs", "open the PR stack", or "publish the PR-ready branches".'
---

# Submit Stacked PRs Workflow

**Goal:** Submit a PR-ready stack as ordered GitHub pull requests. Every PR targets the
selected base branch, heads stay on the selected publish remote, and explicit reviewer gates preserve
the intended incremental merge order.

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

<step n="1" goal="Establish a representable legacy GitHub stack">
  <action>Require a clean worktree, immutable target SHAs, the ordered PR-ready layers with the
  planning layer first, and a fresh fetch of every candidate remote. Require a published integration
  evidence branch whose exact commit descends from the final layer and contains a committed validation
  report. The report must record the exact test command and counts, successful distribution builds
  with artifact hashes, and an explicit prefix-by-prefix partial-merge result.</action>
  <action>Enumerate local Git remotes and resolve each repository and default branch. Ask which target
  remote should receive the PRs, recommending `upstream` when it exists and `origin` otherwise, then
  ask which target branch every PR should use as its base. Ask which publish remote should retain the
  PR-ready heads and integration evidence, recommending `origin` for fork-to-upstream submissions and
  the target remote when both repositories are the same. Do not infer these choices from an earlier run.</action>
  <action>Show the selected target remote, target repository, common base branch, exact base SHA,
  publish remote, and head repository. If another canonical
  remote exists, show whether its corresponding base has the same SHA; divergence requires explicit
  user confirmation before proceeding.</action>
  <action>Ask whether to submit automatically or generate a manual submission package, recommending
  automatic submission by default. Confirm the choice before creating any PR. Both modes use the same
  titles, upstream template or fallback template, body content, ordering, and stack navigation.</action>
  <critical>Use the single-base stack model for every target, including `origin`: every PR base is the
  one confirmed target branch. Publish exact heads only to the confirmed publish remote. Later PRs
  intentionally show cumulative diffs until their prerequisite PRs merge; never retarget them to
  intermediate stack branches.</critical>
  <action>Create a run directory beneath the Git directory:
  `bmad-submit-prs/&lt;UTC timestamp&gt;/`. Persist the manifest, rendered bodies, preflight report,
  and submission journal there.</action>
</step>

<step n="2" goal="Adopt the upstream review contract">
  <action>Discover the upstream PR template from the fetched default branch, including
  `.github/PULL_REQUEST_TEMPLATE.md`, `.github/pull_request_template.md`,
  `docs/PULL_REQUEST_TEMPLATE.md`, `PULL_REQUEST_TEMPLATE.md`, or templates beneath
  `.github/PULL_REQUEST_TEMPLATE/`. If multiple templates apply, choose the closest feature template
  and record the choice.</action>
  <action>If none exists, use these sections: Summary; Motivation and context; Changes; Testing;
  Risk, rollout, and compatibility; Reviewer guidance; Checklist.</action>
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
  remote, publish remote, one common base and its exact SHA, publish-remote branch names, and exact
  local `tip` SHAs. Include the required structured `integration_evidence`; unsupported prose claims
  are not a substitute. Every PR targets the common base, including same-repository submissions.</action>
  <action>Run
  `uv run {skill-root}/scripts/submit_pr_stack.py &lt;manifest&gt; --dry-run --output &lt;journal&gt;`.
  Review titles, bases, heads, SHAs, bodies, table, and graph; add `--verbose` for sanitized commands
  and per-layer progress.</action>
  <check if="authentication, push permission, target SHA, ancestry, upstream remote identity, or an existing PR conflicts">
    Report the exact failed invariant before branch publication or PR creation. Ask the user to retry
    the same target, choose another remote and base branch, or stop safely. A new target returns to
    Step 1 and produces a new manifest and run directory.
  </check>
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
    future nodes stay Pending. Each rerun emits edit commands for existing PRs and a draft-create command
    only for the next contiguous layer. After creating the draft integration PR, rerun once more so its
    URL is added to every component body before any component is marked ready. Then skip the
    automatic-submission actions below.
  </check>
  <check if="the user chose automatic submission">
  <action>After human-visible dry-run approval, run the script with `--apply`. The script preflights all
  remote and GitHub invariants before side effects, publishes exact SHAs to the publish remote with
  force-with-lease, and creates every PR against the common target base. Create new PRs as drafts so
  none becomes reviewable before its warning and links are complete.</action>
  <action>Reuse an open PR only when head and base match; refuse closed, duplicate, or mismatched state.
  Persist after each success. Retry transient reads and idempotent writes with bounded backoff, but
  leave ambiguous creates to an idempotent rerun that reconciles remote state from the journal.</action>
  <action>During sequential creation, prior PR titles and graph nodes are clickable and future nodes
  are marked pending. Explain stacked PRs with a link to `https://www.stacking.dev/`. After all PRs
  exist, update every body and one marker comment per PR with the complete linked graph and ordered
  table, verify the target base has not moved, then mark PRs ready unless the manifest requests drafts.
  Do not create duplicate navigation comments on retry.</action>
  <action>After every component PR exists, create or update one combined-stack validation PR from the
  published integration evidence branch to the common target base. Keep it draft permanently, link it
  from every component PR, and fail if an existing validation PR is ready, closed, moved, or mismatched.</action>
  <check if="branch publication or PR submission fails after side effects begin">
    Persist the journal and show every branch and PR already created. Ask the user to retry the same
    target, choose another remote and base branch, or stop safely. Never close, delete, or rewrite
    partial results without separate approval. When another target is chosen, return to Step 1 with a
    new run directory and leave the prior target unchanged.
  </check>
  </check>
</step>

<step n="5" goal="Prove the reviewer experience and hand off safely">
  <action>Query every submitted PR and verify: expected repository, exact head SHA, expected base,
  head repository owner, correct open/draft state, planning link, complete navigation graph, explicit
  linked prerequisite warning, integration branch and immutable report links, exact test/build evidence,
  and feature-flag safety statement. Treat cumulative diffs as expected until listed prerequisites merge.</action>
  <action>Verify the combined-stack validation PR is open and draft at the exact integration commit,
  links every component PR, contains the immutable evidence links, and says not to merge. Report its
  GitHub checks as pending, passing, or failing from live data; never infer CI success from local tests.</action>
  <action>For automatic submission, report the planning PR first, then a table of every PR number,
  clickable URL, base/head branch, source SHA, and status. For manual submission, do not invent a PR
  summary; report the package, instructions, title/body files, manifest, links file, and journal paths.</action>
  <action>Explain merge order: land PRs strictly from 1 through N. After each merge, refresh later PRs
  so GitHub recalculates their diffs. If prerequisite changes remain, stop and restack the remaining
  heads before review. Never delete publish-remote head branches until their PRs merge or close.</action>
  <action>Run the resolved `{workflow.on_complete}` when non-empty.</action>
</step>

</workflow>
