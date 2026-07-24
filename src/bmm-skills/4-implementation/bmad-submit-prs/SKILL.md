---
name: bmad-submit-prs
description: 'Submit a validated PR-ready branch stack as ordered, reviewer-friendly GitHub pull requests with templates, dependency bases, stack maps, and durable cross-links. Use when the user says "submit the stacked PRs", "open the PR stack", or "publish the PR-ready branches".'
---

# Submit Stacked PRs Workflow

**Goal:** Submit a PR-ready stack as focused legacy GitHub pull requests. Create the planning PR first,
then implementation PRs in dependency order, and fully cross-link every PR.

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
  <action>Enumerate local Git remotes and resolve each repository and default branch. Ask the user
  which remote should receive the PRs, recommending `upstream` when it exists and `origin` otherwise.
  Then ask which branch on that remote should be the first PR's base, recommending that remote's
  default branch. Do not infer acceptance from an earlier run or create a manifest before both
  choices are confirmed.</action>
  <action>Show the selected remote, repository, base branch, and exact base SHA. If another canonical
  remote exists, show whether its corresponding base has the same SHA; divergence requires explicit
  user confirmation before proceeding.</action>
  <action>Ask whether to submit automatically or generate a manual submission package, recommending
  automatic submission by default. Confirm the choice before creating any PR. Both modes use the same
  titles, upstream template or fallback template, body content, ordering, and stack navigation.</action>
  <critical>Legacy GitHub can show focused chained diffs only when every PR base branch exists in the
  selected repository. A multi-PR fork stack cannot use a branch in one repository as a base in
  another. Therefore publish every PR-ready head to the selected repository, with explicit user
  authorization and push permission, before creating PRs. Never silently fall back to cumulative
  PRs against the selected base.</critical>
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
  <action>Choose 1-4 succinct feature keywords without checking uniqueness; project titles as
  `<prefix>(stacked-pr: <keywords> [N/X]): <subject>`. Write a feature summary and body per layer.
  The plan explains feature, split, order, validation, and reviewer path. Implementation PRs link it
  and state scope, prerequisite, validation, and risk without repeating the design.</action>
  <action>Every body must link the published integration branch and immutable validation report,
  state the exact test result and built artifacts, and explain why each dependency-ordered partial
  merge is safe. When safety relies on a feature flag, name it, prove it defaults disabled, and state
  that the disabled path does not import or initialize the gated runtime.</action>
</step>

<step n="3" goal="Create a fail-closed submission manifest">
  <action>Write the schema in `references/submission-manifest.md`. Use upstream-hosted `remote_branch`
  names for every layer and exact local `tip` SHAs. Include the required structured
  `integration_evidence`; unsupported prose claims are not a substitute. The first PR targets upstream
  main; every later PR targets the prior layer's remote branch.</action>
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
    creating or editing any PR. `SUBMIT.md` gives exact web and `gh` submission instructions, base/head
    pairs, and review order. Tell the user where the package, instructions, manifest, and journal live.
    When the user records submitted PR numbers/URLs in `manual-links.json`, rerun with
    `--manual-links` so prior nodes become clickable while future nodes stay Pending. Then skip the
    automatic-submission actions below.
  </check>
  <check if="the user chose automatic submission">
  <action>After human-visible dry-run approval, run the script with `--apply`. The script preflights all
  remote and GitHub invariants before side effects, publishes exact SHAs with force-with-lease, creates
  the planning PR first, and creates each subsequent PR against the prior upstream-hosted layer.</action>
  <action>Reuse an open PR only when head and base match; refuse closed, duplicate, or mismatched state.
  Persist after each success. Retry transient reads and idempotent writes with bounded backoff, but
  leave ambiguous creates to an idempotent rerun that reconciles remote state from the journal.</action>
  <action>During sequential creation, prior PR titles and graph nodes are clickable and future nodes
  are marked pending. Explain stacked PRs with a link to `https://www.stacking.dev/`. After all PRs
  exist, update every body and one marker comment per PR with the complete linked graph and ordered
  table. Do not create duplicate navigation comments on retry.</action>
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
  correct open/draft state, planning link, complete navigation graph, integration branch and immutable
  report links, exact test/build evidence, feature-flag safety statement, and no unexpected cumulative diff.</action>
  <action>For automatic submission, report the planning PR first, then a table of every PR number,
  clickable URL, base/head branch, source SHA, and status. For manual submission, do not invent a PR
  summary; report the package, instructions, title/body files, manifest, links file, and journal paths.</action>
  <action>Explain merge order: land from the bottom of the stack upward, then rebase/retarget remaining
  layers as the hosting platform requires. Never delete upstream stack branches until their PRs merge
  or close.</action>
  <action>Run the resolved `{workflow.on_complete}` when non-empty.</action>
</step>

</workflow>
