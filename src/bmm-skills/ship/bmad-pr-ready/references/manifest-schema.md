# PR-Ready Decision Manifest

Use JSON with immutable full SHAs:

```json
{
  "schema_version": 1,
  "base": "FULL_UPSTREAM_SHA",
  "base_remote": "upstream",
  "base_branch": "main",
  "source_remote": "origin",
  "remote": "upstream",
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
- Empty sanitized groups are skipped, but a whole layer may not be empty: adjacent target layers
  that resolve to the same commit are rejected. Overlay sources resolve relative to the manifest.
- Targets are unique, end in `-pr-ready`, and never alias source refs.
- Exclusions remove only source-introduced changes. Matching upstream-base files remain.
- Existing local targets receive timestamped safety refs before replacement. Backup branches are
  never published to a release remote.
- `base` must equal the exact `base_remote/base_branch` SHA. `remote` must resolve to the same
  repository as `base_remote`.
- In the standard fork release topology, `base_remote` and `remote` are `upstream`, while
  `source_remote` is `origin`. Every source branch must exist on `origin` at `source_tip` and must
  not exist on upstream; PR-ready heads must not exist on origin. Single-repository projects remain
  supported when all three roles resolve to the same repository.
- Pushes name the immutable `new_tip`, use the exact observed target SHA as the lease expectation,
  and verify the resulting remote tip.
- Every effective target-remote push URL must resolve to the same canonical repository as its
  fetch URL. Replacing an existing upstream target that is the head of an open or closed PR is
  refused pending explicit human authorization; there is no automatic bypass.
