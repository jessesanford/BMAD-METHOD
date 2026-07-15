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
  and identifies the series as a stacked PR with a link to `https://www.stacking.dev/`.
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
