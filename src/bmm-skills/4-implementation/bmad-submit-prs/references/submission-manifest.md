# Stacked PR Submission Manifest

Use JSON. Store it and all body files beneath `.git/bmad-submit-prs/<run-id>/`.

```json
{
  "schema_version": 1,
  "repository": "github.example.com/upstream/project",
  "publish_remote": "upstream",
  "default_base": "main",
  "stack_label": "feature-x",
  "feature_summary": "Adds opt-in tracing across the migration-agent fleet.",
  "draft": false,
  "template_source": ".github/PULL_REQUEST_TEMPLATE.md",
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
      "prefix_tips": ["FULL_PLAN_SHA", "FULL_STORY_1_SHA"],
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
- `repository`, `publish_remote`, and `default_base` record the user's confirmed target. Recommend
  `upstream` when that remote exists locally and `origin` otherwise; never bake the recommendation
  into the script as an implicit choice.
- `feature_summary` is a concise feature-level blurb repeated on implementation PRs beside the
  planning-PR link.
- `stack_label` is 1-4 succinct, feature-derived lowercase keywords such as `arize-ax`. It need not
  prove repository-wide uniqueness.
- `integration_evidence` is mandatory and fail-closed:
  - Generate this object with `bmad-pr-ready/scripts/produce_validation_evidence.py` from the applied
    builder report and its strict argv-only validation config; copy the emitted object unchanged.
  - `branch` must exist locally and on `publish_remote` at the exact full `commit`.
  - `commit` must descend from the final stack layer and contain `report_path`.
  - `tests` records the exact command and its pass, skip, and warning counts.
  - Every `builds` entry must have `status: "passed"` and the SHA-256 digest of the built artifact.
  - `partial_merge_safety` must report every submitted prefix as validated and list its exact ordered
    tip in `prefix_tips`. Its feature flag must default to disabled and state what the disabled path avoids.
  - The script derives a live integration-branch link and an immutable report permalink. It refuses
    to render test, build, or partial-merge claims when any invariant is missing or inconsistent.
- `publish_remote` must resolve to that same repository. This requirement is what permits each PR to
  use the prior layer as its GitHub base and show only its focused diff.
- `branch` is an existing local PR-ready branch; `tip` is its immutable full SHA.
- `title` uses a conventional prefix. The script inserts `(stacked-pr: <stack_label> [N/X])`
  immediately before its colon in rendered files, navigation, and submitted PR titles.
- `remote_branch` is the branch published in the upstream repository. Prefer an isolated contributor
  namespace and avoid protected or existing feature branches.
- The first PR base is `default_base`; each later PR base is the prior `remote_branch`.
- `body_file` resolves relative to the manifest. It contains the upstream template plus layer-specific
  content. The script appends deterministic navigation, links submitted PR titles in the full stack,
  identifies the series as a stacked PR with a link to `https://www.stacking.dev/`, and adds the
  evidence-backed integration and partial-merge safety section to every PR.
- Set `draft` when the complete stack should initially avoid normal ready-for-review signaling.

## Manual submission package

Run the same manifest with `--manual --rendered-dir <directory>` to create numbered title/body files,
`SUBMIT.md`, `manual-links.json`, and a journal without creating PRs. Submit in the order shown in
`SUBMIT.md`. After creating PRs, add their 1-based `position`, `number`, and `url` to
`manual-links.json`, then rerun with `--manual-links <file>` to regenerate descriptions whose prior
stack nodes are clickable and whose future nodes remain Pending.

Use `--verbose` during automatic submission to show sanitized `git`/`gh` commands and per-layer
progress. For enterprise repositories the script ignores `GH_TOKEN` (the GitHub.com token variable)
and uses `GH_ENTERPRISE_TOKEN` or the stored credential for that host.
