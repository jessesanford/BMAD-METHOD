# Reviewer refresh and the approval-dismissal contract

Reference for `bmad-land-pr-stack`. Read this when a retarget drops approvals, when a merge is
refused for a review reason, or when landing a stack on an enterprise host.

## Why retargeting dismisses approvals

A review approves a *diff*, not a branch name. Changing a PR's base changes the merge base, so the
diff the reviewer approved is no longer the diff that would land. Rulesets with
`dismiss_stale_reviews_on_push` therefore drop existing approvals when the base changes.

This is the control working correctly. In a stacked-PR landing, every layer after the first gets
retargeted from its predecessor's head onto the default branch, so **every layer after the first
needs one fresh approval**. Budget for that up front rather than discovering it at layer two.

## Why the operator cannot self-approve

`require_last_push_approval` requires approval from someone other than the last person to push or
otherwise change the PR. The operator running the landing workflow *is* that person, by definition of
having retargeted it. Admin or bypass merge is refused for the same reason.

There is no configuration-side trick that preserves the approval through a base change. Do not
attempt one. The correct response is to make the human approval fast and predictable:

1. Assign the PR to the agreed reviewers so it enters their queue.
2. Request their review explicitly.
3. Poll the review decision on a fixed interval until it reports approved.

Do not re-request review on every poll — it spams reviewers without accelerating anything.

## Where the policy actually lives

Protection can come from repository branch protection, from organization-level rulesets, or both. A
repository can return `404` for branch protection while still being fully governed by an org ruleset.
Check both before concluding a rule is absent, and read the ruleset's source so the human knows which
level would have to change if they ever wanted it to.

Settings that change how this workflow behaves:

| Setting | Effect on landing |
| --- | --- |
| `dismiss_stale_reviews_on_push` | Every retarget costs one fresh approval |
| `require_last_push_approval` | Operator can never self-approve; admin merge refused |
| `required_approving_review_count` | How many reviewers must be lined up per layer |
| `require_code_owner_review` | Reviewer identity is constrained, not just the count |
| `delete_branch_on_merge` | Merging a layer deletes the branch its successor is based on |

`delete_branch_on_merge` is the setting that makes retarget-before-merge mandatory rather than merely
tidy. With it enabled, an explicit `--delete-branch` flag is not required for the auto-close to happen.

## Base changes: prefer REST over GraphQL

Change a PR's base with the REST endpoint:

```
PATCH repos/<owner>/<repo>/pulls/<number>   base=<default-branch>
```

The GraphQL-backed `gh pr edit --base` intermittently fails on enterprise hosts with a generic
`GraphQL: Something went wrong while executing your query` while the REST PATCH succeeds against the
same PR moments later. When the base change fails, switch transports rather than retrying the same
one.

Neither transport can retarget a **closed** PR: the API returns
`Cannot change the base branch of a closed pull request`. That is the failure mode
retarget-before-merge exists to avoid.

## Enterprise host authentication

Where the environment is authenticated against both public GitHub and an enterprise host, public
credentials in the environment can override the saved enterprise login and produce
`401 Bad credentials` against a repository the user can plainly see in the browser.

Target the enterprise host explicitly and unset the competing credential variables for the command —
including the generic token variables, not just the enterprise-specific one — and pass the repository
explicitly rather than relying on the inferred remote. Verify with an auth status check against the
enterprise host before starting a landing run; discovering the auth problem halfway through a stack is
far more expensive than checking first.
