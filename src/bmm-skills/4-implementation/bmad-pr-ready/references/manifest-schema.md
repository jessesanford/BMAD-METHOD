# PR-Ready Decision Manifest

Use JSON with immutable full SHAs:

```json
{
  "schema_version": 1,
  "base": "FULL_UPSTREAM_SHA",
  "remote": "origin",
  "exclude_paths": ["_bmad/**", "_bmad-output/**", ".agents/**"],
  "layers": [
    {
      "source": "feat/example/plan",
      "source_tip": "FULL_SOURCE_SHA",
      "source_parent": "FULL_SOURCE_PARENT_SHA",
      "target": "feat/example/plan-pr-ready",
      "decision_summary": "One reviewer-facing planning outcome.",
      "groups": [
        {
          "through": "FULL_SOURCE_SHA",
          "message": "docs: describe the feature for upstream reviewers",
          "novelty_rationale": "The source commits all complete one planning outcome.",
          "overlays": [
            {"path": "docs/plans/example.md", "source": "clean-plan.md"}
          ]
        }
      ]
    }
  ]
}
```

- Layer order is target stack order. The first parents to `base`; each later layer parents to the
  previous target tip.
- `source_parent..source_tip` is the complete source delta. Group `through` SHAs advance monotonically;
  the final group ends at `source_tip`.
- Empty sanitized groups are skipped. Overlay sources resolve relative to the manifest.
- Targets are unique, end in `-pr-ready`, and never alias source refs.
- Exclusions remove only source-introduced changes. Matching upstream-base files remain.
- Existing local/remote targets receive timestamped safety refs before replacement.
