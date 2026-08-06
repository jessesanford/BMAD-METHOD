---
name: bmad-land-pr-stack
description: 'Stacked-PR projects only: land a submitted PR stack into the default branch layer by layer — pre-emptively retargeting each successor onto the default branch, refreshing the reviews that protection rules dismiss on retarget, and never merging the permanent-draft integration PR. Use when the user says "merge the stack", "land the PR stack", "merge each PR into main in order", or after every component PR is approved and green.'
---

# Land PR Stack Workflow

**Goal:** Merge an approved, green PR stack into the default branch in dependency order, so that
every component PR merges into the **default branch** rather than into its predecessor's branch, and
so that no layer is silently lost, auto-closed, or merged out of order.

**Your Role:** Release operator running an irreversible, ordered sequence. Merging is the one
stacked-PR phase that cannot be undone with a rebase — a wrong base means commits land in the wrong
branch, and a wrong order means the default branch briefly holds a layer whose prerequisite is
missing. Be mechanical and verify after every mutation.

**This skill only applies to stacked-PR projects** — repos using "one branch + one PR per story,
chained as a stack". It is the phase after `bmad-submit-prs` (or `bmad-extend-pr-stack`) has produced
a live, reviewed stack. It does not create, rewrite, or re-review PRs; it lands them.

## The two problems this workflow exists to solve

Landing a stack is not "merge each PR". Two GitHub behaviors actively fight a naive merge loop:

1. **Deleting a merged head auto-closes its dependent PR.** Every component PR after the first is
   based on its predecessor's head branch. The moment that predecessor merges and its head branch is
   deleted — by `--delete-branch` or by the repository's `delete_branch_on_merge` setting — GitHub
   closes the dependent PR and marks it `CONFLICTING`. A closed PR **cannot be retargeted**
   (`Cannot change the base branch of a closed pull request`), and it cannot even be reopened while
   its base branch is missing. Recovery means restoring a deleted ref just to reopen a PR.
2. **Retargeting dismisses approvals.** Changing a PR's base changes its effective diff, so rulesets
   with `dismiss_stale_reviews_on_push` drop every existing approval. With
   `require_last_push_approval` also enabled, the operator who performed the retarget can never
   self-approve the result. Every layer therefore needs a *fresh* human approval after retargeting,
   and the operator cannot supply it.

Problem 1 is avoidable. Problem 2 is not — it is the reviewing organization's policy, and this
workflow's job is to make satisfying it cheap and explicit rather than to circumvent it.

<critical>
**Retarget the successor BEFORE merging the current layer.** GitHub only auto-closes a PR when the
branch it is *based on* is deleted. If PR N+1 has already been retargeted onto the default branch, then
merging PR N and deleting its head branch does not touch PR N+1 at all.

Retarget-then-merge turns the entire reopen/restore-deleted-ref recovery dance into a path you never
enter. Merge-then-repair is the fallback for a stack someone already broke, not the normal route.

Never "fix" a dismissed approval by re-approving your own retarget, by using admin/bypass merge to
skip the review gate, or by relaxing the ruleset. The dismissal is the control working as designed.
</critical>

## Conventions

- Bare paths resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory.
- `{project-root}` resolves to the project root.
- `{skill-name}` resolves to `bmad-land-pr-stack`.

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

<step n="1" goal="Confirm the stack is landable and get explicit authorization">
  <action>Require the project's stacked-branching rule or an equivalent stacked-PR convention, plus a
  live submitted stack. If the stack has not been submitted yet, this is the wrong skill — route to
  `bmad-submit-prs`.</action>
  <action>Require an explicit human instruction to merge. Landing is irreversible and must never be
  inferred from "the stack is green" or from a prior submission approval. Confirm the exact intended
  scope: which PRs land, and that the combined validation PR is excluded.</action>
  <action>Require a clean worktree with no merge, rebase, cherry-pick, or revert in progress, and
  confirm authentication against the target host. For an enterprise host, target it explicitly and
  neutralize competing public-host credentials — see `references/reviewer-refresh.md`.</action>
  <check if="the user asked to land 'the whole stack' without qualification">
    Confirm explicitly that this excludes the permanent-draft combined validation PR. Restate which PR
    number that is. Never resolve the ambiguity silently in either direction.
  </check>
</step>

<step n="2" goal="Resolve true stack order from live topology, never from PR numbers">
  <action>Fetch every authoritative remote. Enumerate the open component PRs and build the order by
  chaining `base` → `head` refs: the layer whose base is the default branch is first, the layer whose
  base is that layer's head is second, and so on to the tip.</action>
  <critical>
  PR numbers do not encode stack order. A stack that was extended, re-submitted, split, or repaired
  will interleave numbers freely — a later-numbered PR is routinely an *earlier* layer. Ordering a
  merge loop by PR number will merge layers out of dependency order.
  Resolve order from the base/head chain and cross-check it against the `[N/X]` layer labels in the
  titles. Both must agree.
  </critical>
  <check if="the base/head chain and the [N/X] title labels disagree, or the chain does not form one unbroken line">
    HALT and report both orderings with the conflicting refs. A broken or forked chain means the stack
    is not in a landable shape; do not guess an order.
  </check>
  <action>Identify the combined validation/integration PR explicitly — it targets the default branch,
  is permanently draft, and says DO NOT MERGE. Record its number on an exclusion list and carry that
  list through every later step.</action>
  <action>Record the recovery manifest **before any mutation**: for every layer, its PR number, head
  ref, base ref, and exact head SHA, plus the current default-branch SHA. Persist it under
  `bmad-land-pr-stack/<UTC timestamp>/` beneath the Git directory.</action>
  <critical>
  This manifest is the only reliable way to restore a base branch that a merge deletes. Capture the
  SHAs while the refs still exist, and keep local refs for the whole run: do not prune remote-tracking
  refs, do not garbage-collect, and do not delete local branches mid-run. A deleted upstream branch
  with no local copy and no recorded SHA is an unrecoverable auto-close.
  </critical>
</step>

<step n="3" goal="Re-verify freshness against the live default branch">
  <action>Compare the default-branch SHA the stack was last cascaded and validated against with the
  live default-branch head.</action>
  <check if="the default branch has advanced past the recorded cascade base">
    The stack's validation evidence is stale. Land nothing. Route to `bmad-rebase-cascade` and
    `bmad-integration-review` to re-cascade and re-validate, then return here.
  </check>
  <action>State plainly that this freshness check binds at the start of the run only for the first
  layer. Each subsequent merge advances the default branch by construction, which is expected and is
  not staleness — but any *foreign* merge landing during the run is. Re-check before each layer.</action>
  <check if="a commit not produced by this run appears on the default branch mid-sequence">
    Someone else merged into the default branch while the stack was landing. Stop the loop and
    re-validate the remaining layers against the new head before continuing.
  </check>
</step>

<step n="4" goal="Discover the review-refresh policy before touching anything">
  <action>Read the protection configuration that governs the default branch — repository branch
  protection and every applicable organization ruleset. Record
  `required_approving_review_count`, `dismiss_stale_reviews_on_push`, `require_last_push_approval`,
  `require_code_owner_review`, and the allowed merge methods.</action>
  <action>From that, decide up front whether retargeting will dismiss approvals. If
  `dismiss_stale_reviews_on_push` is true, plan on one fresh approval per layer and say so now — the
  human needs to know the cadence before the run starts, not after the first surprise.</action>
  <check if="require_last_push_approval is true">
    The operator running this workflow cannot approve their own retargeted PRs. Confirm the reviewers
    who will approve each layer, and confirm they are available for the whole run, before mutating
    anything. A stack stalled mid-land with half its layers merged is worse than one not started.
  </check>
  <action>Confirm the merge method the project wants and that the ruleset permits it. Prefer a true
  merge commit for stacked layers unless the project standard says otherwise; record the choice.</action>
  <check if="the project requires squash or rebase merges">
    Warn that these rewrite each layer's commits, so every remaining head must be restacked onto the
    rewritten default branch after each merge rather than merely retargeted. That is a materially
    different loop — confirm the user wants it before proceeding.
  </check>
</step>

<step n="5" goal="Land each layer with retarget-then-merge">
  <action>Process layers strictly in the resolved dependency order, one at a time. Never batch, never
  parallelize, and never skip ahead to an "easy" layer.</action>
  <action>For the current layer L, before merging it, retarget its immediate successor L+1 onto the
  default branch. This is the step that prevents L+1 being auto-closed when L's head branch is
  deleted. If L is the last component layer, there is no successor to retarget.</action>
  <action>Retarget with the REST endpoint
  `PATCH repos/<owner>/<repo>/pulls/<number>` sending `base=<default-branch>`.</action>
  <critical>
  Prefer the REST base change over the GraphQL-backed `gh pr edit --base`. On enterprise hosts the
  GraphQL mutation intermittently fails with a generic `Something went wrong while executing your
  query` while the REST PATCH succeeds against the same PR. Retrying the failing GraphQL path wastes
  the run; switch transports instead.
  </critical>
  <action>After retargeting, verify from live data that the successor's base is now the default branch
  and that it is `MERGEABLE`. Then refresh its review per step 6 — do not defer the refresh, because a
  layer whose approval is dismissed but never re-requested will block the loop later.</action>
  <action>Immediately before merging layer L, verify from **live** data: it is OPEN, its base is the
  default branch, it is `MERGEABLE`, its required checks pass, and it carries a current approval.</action>
  <critical>
  Verify required checks are actually green for the layer being merged — do not merge on `MERGEABLE`
  alone, and do not treat a still-running check as passing. Long test jobs frequently finish minutes
  after a fast lint/validate job reports green.
  Pay particular attention to any layer that had no prior review or whose checks were last observed on
  a different base: its evidence is the least trustworthy in the stack.
  </critical>
  <action>Merge L with the confirmed merge method. Then verify from live data that it reports MERGED
  with a merge timestamp before advancing. A merge command that returns quietly is not proof.</action>
  <check if="the merge is refused for a rule violation">
    Report the exact rule. Do not retry with an admin/bypass flag, do not self-approve, and do not
    weaken the ruleset. Surface the requirement to the human and wait.
  </check>
  <action>Record each completed layer in the run journal as it lands, so an interrupted run can be
  resumed without re-deriving what already merged.</action>
</step>

<step n="6" goal="Refresh the dismissed review deterministically">
  <action>After a retarget dismisses a layer's approval, immediately assign the PR to the agreed
  reviewers and request their review, so the layer appears in their review queue rather than waiting
  to be noticed.</action>
  <action>Then wait for the approval by polling the PR's review decision on a fixed interval with a
  bounded overall timeout, and stop polling the moment it reports approved. Use the cadence in
  `{workflow.review_poll_interval_seconds}` and `{workflow.review_poll_timeout_seconds}`.</action>
  <critical>
  Poll on a timer; never busy-wait, and never re-request review repeatedly while waiting. Repeated
  review requests spam reviewers and do not make an approval arrive sooner.
  </critical>
  <check if="the poll window expires without an approval">
    Stop the loop cleanly at a layer boundary. Report exactly which layers merged, which layer is
    waiting, and what the reviewers still need to do. Never let a stalled approval push the run into
    merging a later layer out of order.
  </check>
  <action>Treat `CHANGES_REQUESTED` as a full stop, not a slow approval: the reviewer has found
  something in a layer that is about to become the default branch. Hand it back for correction.</action>
</step>

<step n="7" goal="Repair a stack that was already broken by merge-then-delete">
  <check if="every layer still has a live base and no PR was auto-closed">
    Skip this step. It exists only to recover a stack that was landed without the step 5 ordering.
  </check>
  <action>For an auto-closed layer, recover in this exact order, because each action is a precondition
  for the next:
  1. Restore the deleted base branch upstream by pushing the exact SHA recorded in the step 2
     manifest (or the surviving local ref) back to its original ref name.
  2. Reopen the PR. Reopening fails while its base branch does not exist, which is why restore comes
     first.
  3. Retarget it onto the default branch via the REST PATCH.
  4. Refresh its review per step 6.
  5. Delete the temporarily restored branch again only after the retarget succeeds.</action>
  <critical>
  Restoring the branch is a temporary scaffold to make the PR reopenable — it is not a revival of that
  layer. Never leave the restored ref in place as if it were live stack state, and never restore a ref
  to a SHA you did not record or verify.
  </critical>
  <check if="the base SHA was never recorded and no local ref survives">
    HALT. The PR cannot be reopened and the layer must be re-submitted as a new PR against the default
    branch, preserving the original as historical record. Confirm that with the human rather than
    force-creating a replacement.
  </check>
</step>

<step n="8" goal="Verify the landing and leave the integration PR alone">
  <action>Verify from live data that every intended component PR reports MERGED, and that the default
  branch contains one merge commit per layer in the expected order. Reconcile that list against the
  step 2 manifest so a silently dropped layer cannot pass as success.</action>
  <critical>
  The combined validation/integration PR must remain OPEN and DRAFT. It exists to run checks against
  the fully integrated tree and must never be merged — merging it would replay the whole stack's diff
  as one unreviewed commit. Confirm its final state explicitly as part of declaring success.
  </critical>
  <action>Confirm no unintended PR was closed, retargeted, or merged during the run — in particular
  any unrelated open PR that happened to be based on a branch this run deleted.</action>
  <action>Report: the ordered list of merged PRs with their merge times, the final default-branch SHA,
  the integration PR left open as draft, any layer that did not land and why, and any restored ref that
  still needs cleanup.</action>
  <action>Once the stack has landed, the interim anchor branch is obsolete: recommend refreshing or
  retiring it via `bmad-stack-working-branch`, and update any dependent repository that pinned the
  unmerged head so it now tracks the merged default branch.</action>
  <action>Run the resolved `{workflow.on_complete}` when non-empty.</action>
</step>

</workflow>
