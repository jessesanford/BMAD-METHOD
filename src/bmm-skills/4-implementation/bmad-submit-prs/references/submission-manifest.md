# Stacked PR Submission Manifest

Use JSON. Store it and all body files beneath `.git/bmad-submit-prs/<run-id>/`.

```json
{
  "schema_version": 2,
  "repository": "github.example.com/upstream/project",
  "target_remote": "upstream",
  "publish_remote": "upstream",
  "evidence_remote": "origin",
  "evidence_repository": "github.example.com/contributor/project",
  "default_base": "main",
  "base_sha": "FULL_TARGET_BASE_SHA",
  "stack_label": "feature-x",
  "feature_name": "Feature X",
  "feature_summary": "Adds an opt-in capability across the affected components.",
  "draft": false,
  "template_source": ".github/PULL_REQUEST_TEMPLATE.md",
  "origin_review": {
    "audit_receipt": "/absolute/origin-live-audit-receipt.json",
    "audit_receipt_sha256": "FULL_64_CHARACTER_SHA256",
    "pre_review_manifest_sha256": "FULL_64_CHARACTER_SHA256",
    "pre_review_journal_sha256": "FULL_64_CHARACTER_SHA256",
    "approved": true,
    "approval_phrase": "approve origin review for upstream submission"
  },
  "integration_evidence": {
    "branch": "integration/feature-x-validated",
    "commit": "FULL_INTEGRATION_COMMIT_SHA",
    "report_path": "docs/validation/feature-x-stack.md",
    "test_command": "uv run pytest packages/feature-common/tests cli/tests --tb=short",
    "tests": {
      "passed": 256,
      "skipped": 5,
      "warnings": 2
    },
    "builds": [
      {
        "artifact": "feature_common-1.0.0-py3-none-any.whl",
        "status": "passed",
        "sha256": "FULL_64_CHARACTER_SHA256"
      },
      {
        "artifact": "feature_cli-1.0.0-py3-none-any.whl",
        "status": "passed",
        "sha256": "FULL_64_CHARACTER_SHA256"
      }
    ],
    "partial_merge_safety": {
      "validated_prefixes": 2,
      "total_prefixes": 2,
      "feature_flag": {
        "name": "FEATURE_X_ENABLED",
        "safe_default": "disabled",
        "disabled_behavior": "the disabled path does not import or initialize feature runtime code"
      }
    }
  },
  "layers": [
    {
      "branch": "feature/plan-pr-ready",
      "remote_branch": "contrib/alice/feature/plan-pr-ready",
      "tip": "FULL_LOCAL_SHA",
      "title": "docs: propose feature",
      "summary": "Reviewer-facing feature plan and stack overview.",
      "body_file": "01-plan.md"
    },
    {
      "branch": "feature/story-1-pr-ready",
      "remote_branch": "contrib/alice/feature/story-1-pr-ready",
      "tip": "FULL_LOCAL_SHA",
      "title": "feat: add the first feature layer",
      "summary": "The first independently reviewable implementation layer.",
      "body_file": "02-story-1.md"
    }
  ]
}
```

- `repository` is `[HOST/]OWNER/REPO` in `gh` syntax.
- `repository` is the canonical target repository. `target_remote` and `publish_remote` must both
  resolve to it, including every effective fetch and push URL; cross-repository component heads are
  forbidden. `base_sha` is the immutable full SHA of `target_remote/default_base`.
- In the standard fork-to-upstream topology, both target roles are local `upstream` and
  `evidence_remote` is local `origin`. Single-repository projects remain supported when all three
  roles resolve to the target repository.
- `feature_name` is the reviewer-facing feature name used by the merge gate.
- `feature_summary` is a concise feature-level blurb repeated on implementation PRs beside the
  planning-PR link.
- `stack_label` is 1-4 succinct, feature-derived lowercase keywords such as `arize-ax`. It need not
  prove repository-wide uniqueness.
- `integration_evidence` is mandatory and fail-closed:
  - `branch` must exist locally and on `evidence_remote` at the exact full `commit`.
  - `commit` must descend from the final stack layer and contain `report_path`.
  - `tests` records the exact command and its pass, skip, and warning counts.
  - Every `builds` entry must have `status: "passed"` and the SHA-256 digest of the built artifact.
  - `partial_merge_safety` must report every submitted prefix as validated. Its feature flag must
    default to disabled and state what the disabled runtime path avoids.
    - The script derives evidence links from the independently validated evidence repository. It refuses
    to render test, build, or partial-merge claims when any invariant is missing or inconsistent.
- `evidence_remote` and `evidence_repository` are mandatory and independently pin all effective
    URLs, repository, host, authenticated push permission, branch, and exact SHA. The evidence
    repository may be the target repository or a fork in the same GitHub network. Only integration
    evidence may be cross-repository.
- For a fork evidence topology, component heads must be absent from `evidence_remote`, and the
    evidence head must be absent from `publish_remote`. Thus source/evidence/backups never land in the
    canonical repository and PR-ready component heads never land in the fork.
- `branch` is an existing local PR-ready branch; `tip` is its immutable full SHA.
- `title` uses a conventional prefix. The script inserts `(stacked-pr: <stack_label> [N/X])`
  immediately before its colon in rendered files, navigation, and submitted PR titles.
- `remote_branch` must exactly equal `branch`; this prevents alternate aliases from bypassing the
  `*-pr-ready` placement rule.
- The first component PR base is `default_base`; each later base is the immediately previous layer's
  `remote_branch`. These are the initial review bases. After a predecessor merges, retarget/restack
  the next PR onto `default_base` and cascade every dependent branch before that PR can merge;
  otherwise GitHub would merge it into the predecessor branch. The integration validation PR alone
  also uses `default_base`.
- `body_file` resolves relative to the manifest. It contains the upstream template plus layer-specific
  content. The script prepends a Stack Merge Gate listing every prerequisite PR, appends deterministic
  navigation and `https://www.stacking.dev/` context, and adds evidence-backed integration and
  partial-merge safety to every PR.
- `template_source` records the applicable target-repository template. When no applicable template
  exists, set it to `bmad-submit-prs:pr-111-fallback` and base every body on
  `references/pr-111-fallback-template.md`; the submitter requires all seven canonical headings
  exactly once and in order. Template discovery is repeated against the immutable target-base tree:
  selecting the fallback while a repository template exists, or naming a repository template absent
  from that tree, fails closed.
- In fork topology, the first canonical dry run omits `origin_review` and is used only to cultivate
  the namespaced origin review stack and its permanent-draft evidence PR. After live audit and
  explicit human approval, regenerate the upstream package from current refs with `origin_review`
  binding the audit receipt, its exact SHA-256, and the audited pre-review manifest/journal hashes.
  Use `prepare_upstream_submission.py --mode prepare` to copy every source body into a new run
  directory, then seal the validated dry run with `--mode seal`. The submitter re-queries every
  origin PR and rejects drift in its head, base, SHA, title, body, draft state, or evidence contract.
- Review the sealed dry run and require `approve regenerated upstream dry run` in a distinct apply
  request validated with `prepare_upstream_submission.py --mode validate-apply`. Pass its exact
  journal and request to
  `--apply --approved-dry-run-journal <journal> --approved-apply-request <request>`.
  Manual submission requires the same sealed inputs. Automatic apply preserves reviewed title/body
  bytes and adds newly discovered PR navigation as comments.
  Existing upstream PRs are rejected
  because none existed in the approved dry run. Never reuse the pre-origin-review package.
- New automatic PRs are staged as drafts until every body and graph is finalized. Set `draft: true`
  to leave the completed stack in draft state; otherwise the script marks all PRs ready after audit.
- The integration evidence branch also becomes a separate combined-stack validation PR against
  `default_base`. Its title/body are generated deterministically, it links every component PR, and it
  remains draft regardless of `draft`. It exists only to run target-repository GitHub checks against
  the complete tree and must never be merged. Fork review gets its own draft evidence PR first;
  after approval, the target gets a cross-repository draft evidence PR from the same exact evidence
  branch and SHA. The evidence branch itself is never copied to the target repository.

## Manual submission package

Run the same manifest with `--manual --rendered-dir <directory>` to create numbered title/body files,
`integration-title.txt`, `integration-body.md`, `SUBMIT.md`, `manual-links.json`, and a journal without
creating PRs. Submit in the order shown in
`SUBMIT.md`. After creating each PR, add its 1-based `position`, `number`, and `url` to
`manual-links.json`, then rerun with `--manual-links <file>` before creating the next PR. If matching
component PRs already exist while that file is missing or incomplete, the package discovers and
validates them and emits refresh/edit instructions instead of duplicate create commands; conflicting
or non-contiguous PRs fail with reconciliation instructions. Every
command and compare URL uses that layer's predecessor base; regenerated Stack Merge Gates link submitted
prerequisites and list future positions as Pending. Each package preserves the sealed component
bodies, emits navigation-comment commands for existing PRs, and emits a draft-create command only for
the next contiguous layer. Create the combined validation PR last with `--draft`, after every component
link is present, then rerun once more to discover it and emit the complete integration navigation
comment without rewriting any approved body.

Use `--verbose` during automatic submission to show sanitized `git`/`gh` commands and per-layer
progress. For enterprise repositories the script ignores `GH_TOKEN` (the GitHub.com token variable)
and uses `GH_ENTERPRISE_TOKEN` or the stored credential for that host.
