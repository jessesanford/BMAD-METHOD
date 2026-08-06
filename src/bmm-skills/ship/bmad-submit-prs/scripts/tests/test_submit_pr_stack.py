#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "submit_pr_stack.py"
SPEC = importlib.util.spec_from_file_location("submitter", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SubmitterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layers = [
            {
                "title": "docs: plan feature",
                "summary": "Feature plan.",
                "remote_branch": "stack/plan-pr-ready",
                "_head_ref": "stack/plan-pr-ready",
            },
            {
                "title": "feat: implement feature",
                "summary": "Implementation.",
                "remote_branch": "stack/story-pr-ready",
                "_head_ref": "stack/story-pr-ready",
            },
        ]
        self.evidence = {
            "branch": "integration/feature-x",
            "commit": "a" * 40,
            "report_path": "docs/validation/feature-x.md",
            "test_command": "uv run pytest",
            "tests": {"passed": 42, "skipped": 1, "warnings": 0},
            "builds": [
                {
                    "artifact": "feature_x-1.0.0-py3-none-any.whl",
                    "status": "passed",
                    "sha256": "b" * 64,
                }
            ],
            "partial_merge_safety": {
                "validated_prefixes": 2,
                "total_prefixes": 2,
                "prefix_tips": ["first", "final"],
                "feature_flag": {
                    "name": "FEATURE_X_ENABLED",
                    "safe_default": "disabled",
                    "disabled_behavior": "the disabled path does not initialize the feature",
                },
            },
            "_commit": "a" * 40,
            "_branch_url": "https://example.test/tree/integration/feature-x",
            "_report_url": "https://example.test/blob/commit/docs/validation/feature-x.md",
        }

    def write_dry_run_journal_fixture(
        self,
        directory: Path,
        *,
        existing_prs: dict[int, dict[str, Any]] | None = None,
        existing_integration_pr: dict[str, Any] | None = None,
    ) -> tuple[Path, dict[str, Any], list[dict[str, Any]], Path, dict[str, Any]]:
        body = directory / "body.md"
        body.write_text("## Summary\n\nLayer.\n", encoding="utf-8")
        layers = [
            {
                "branch": f"stack/{name}-pr-ready",
                "remote_branch": f"stack/{name}-pr-ready",
                "_head_ref": f"stack/{name}-pr-ready",
                "_tip": str(index + 1) * 40,
                "_base_sha": "0" * 40 if index == 0 else str(index) * 40,
                "_body_file": body,
                "title": f"feat: {name}",
                "summary": name,
            }
            for index, name in enumerate(("plan", "story-1.1", "story-1.2"))
        ]
        manifest = {
            "repository": "github.example.com/upstream/repo",
            "default_base": "main",
            "base_sha": "0" * 40,
            "feature_name": "Feature X",
            "feature_summary": "Adds focused behavior.",
            "stack_label": "feature-x",
            "evidence_remote": "origin",
            "template_source": ".github/PULL_REQUEST_TEMPLATE.md",
            "integration_evidence": dict(self.evidence),
            "_head_repository": "github.example.com/upstream/repo",
            "_head_owner": "upstream",
            "_evidence_owner": "fork-owner",
            "_evidence_repository": "github.example.com/fork-owner/repo",
            "_evidence_cross_repository": False,
        }
        manifest["_integration_layer"] = MODULE.integration_layer(manifest)
        manifest_path = directory / "manifest.json"
        manifest_path.write_text("{}\n", encoding="utf-8")
        journal_path = directory / "approved-dry-run.json"
        existing_prs = existing_prs or {}

        def populate_preflight(
            _repo: Path,
            live_manifest: dict[str, Any],
            live_layers: list[dict[str, Any]],
        ) -> None:
            for index, pr in existing_prs.items():
                live_layers[index]["_existing_pr"] = dict(pr)
            if existing_integration_pr is not None:
                live_manifest["_existing_integration_pr"] = dict(existing_integration_pr)

        with mock.patch.object(MODULE, "gh"), mock.patch.object(
            MODULE, "create_pull_request"
        ), mock.patch.object(MODULE, "publish"), mock.patch.object(
            MODULE, "github_preflight", side_effect=populate_preflight
        ), mock.patch.object(
            MODULE, "configure_command_environment"
        ), mock.patch.object(
            MODULE, "validate", return_value=layers
        ), mock.patch.object(
            MODULE, "load_manifest", return_value=manifest
        ):
            journal = MODULE.submit(
                directory,
                manifest_path,
                apply=False,
                manual=False,
                output=journal_path,
                rendered_dir=directory / "rendered",
                manual_links=None,
            )
        return manifest_path, manifest, layers, journal_path, journal

    def reset_preflight_state(
        self,
        manifest: dict[str, Any],
        layers: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        manifest_copy = dict(manifest)
        manifest_copy["integration_evidence"] = dict(manifest["integration_evidence"])
        manifest_copy.pop("_existing_integration_pr", None)
        manifest_copy["integration_evidence"].pop("_integration_pr_url", None)
        layers_copy = [dict(layer) for layer in layers]
        for layer in layers_copy:
            layer.pop("_existing_pr", None)
        return manifest_copy, layers_copy

    def build_apply_submit_fixture(
        self,
        directory: Path,
    ) -> tuple[Path, dict[str, Any], list[dict[str, Any]], dict[str, str], dict[str, Any]]:
        manifest_path = directory / "manifest.json"
        manifest_path.write_text("{}\n", encoding="utf-8")
        layers = [
            {
                "branch": "stack/plan-pr-ready",
                "remote_branch": "stack/plan-pr-ready",
                "_head_ref": "stack/plan-pr-ready",
                "_tip": "1" * 40,
                "_base_sha": "0" * 40,
            }
        ]
        manifest = {
            "repository": "github.example.com/upstream/repo",
            "default_base": "main",
            "base_sha": "0" * 40,
            "stack_label": "feature-x",
            "feature_name": "Feature X",
            "feature_summary": "Adds focused behavior.",
            "evidence_remote": "origin",
            "integration_evidence": dict(self.evidence),
            "_head_owner": "upstream",
            "_head_repository": "github.example.com/upstream/repo",
            "_evidence_owner": "fork-owner",
            "_evidence_repository": "github.example.com/fork-owner/repo",
            "_evidence_cross_repository": True,
            "_origin_review": {"status": "approved"},
        }
        manifest["_integration_layer"] = MODULE.integration_layer(manifest)
        approval = {
            "apply_request": str(directory / "apply-request.json"),
            "apply_request_sha256": "a" * 64,
            "preparation_receipt": str(directory / "preparation-receipt.json"),
            "preparation_receipt_sha256": "b" * 64,
        }
        approved_journal = {
            "_approved_component_bodies": ["approved component body"],
            "_approved_integration_body": "approved integration body",
        }
        return manifest_path, manifest, layers, approval, approved_journal

    def test_partial_navigation_links_prior_and_marks_future(self) -> None:
        rendered = MODULE.render_navigation(
            self.layers,
            {0: {"number": 41, "url": "https://example.test/pull/41"}},
            0,
            "main",
            "feature-x",
            "contributor",
        )
        self.assertIn("[#41](https://example.test/pull/41)", rendered)
        self.assertIn(
            "[docs(stacked-pr: feature-x [1/2]): plan feature](https://example.test/pull/41)",
            rendered,
        )
        self.assertIn("[stacked pull request](https://www.stacking.dev/)", rendered)
        self.assertIn("Pending", rendered)
        self.assertIn("L1 --> L2", rendered)
        self.assertEqual(rendered.count("| `main` |"), 1)
        self.assertIn("| None | `main` |", rendered)
        self.assertIn("| `stack/plan-pr-ready` | `contributor:stack/story-pr-ready` |", rendered)

    def test_complete_navigation_links_every_pr(self) -> None:
        rendered = MODULE.render_navigation(
            self.layers,
            {
                0: {"number": 41, "url": "https://example.test/pull/41"},
                1: {"number": 42, "url": "https://example.test/pull/42"},
            },
            1,
            "release",
            "feature-x",
            "contributor",
        )
        self.assertIn("[#41](https://example.test/pull/41)", rendered)
        self.assertIn("[#42](https://example.test/pull/42)", rendered)
        self.assertNotIn("| Pending |", rendered)
        self.assertIn("**This PR:** 2 of 2", rendered)
        self.assertEqual(rendered.count("| `release` |"), 1)
        self.assertIn("`stack/plan-pr-ready` |", rendered)

    def test_merge_gate_uses_feature_name_and_explicit_prerequisites(self) -> None:
        first = MODULE.render_merge_warning(
            self.layers,
            {},
            0,
            "main",
            "feature-x",
            "Arize AX",
        )
        self.assertIn("**Stack Merge Gate (1/2)**", first)
        self.assertIn(
            "This is the first PR in a series of PRs composing a PR stack for the Arize AX feature.",
            first,
        )
        self.assertIn("This is the planning PR.", first)
        self.assertIn("Do not approve or merge any PR out of order", first)
        self.assertIn("See the **Stack PR Navigation** section below.", first)
        self.assertNotIn("simulated", first.casefold())
        self.assertNotIn("#stack-navigation", first)

        second = MODULE.render_merge_warning(
            self.layers,
            {0: {"number": 41, "url": "https://example.test/pull/41"}},
            1,
            "main",
            "feature-x",
            "Arize AX",
        )
        self.assertIn("[#41 - docs(stacked-pr: feature-x [1/2])", second)
        self.assertIn("DO NOT APPROVE until every prerequisite PR below has merged", second)

    def test_component_bases_follow_traditional_stack(self) -> None:
        layers = [
            {"remote_branch": "stack/plan-pr-ready"},
            {"remote_branch": "stack/story-1.1-pr-ready"},
            {"remote_branch": "stack/story-1.2-pr-ready"},
        ]
        self.assertEqual(
            [MODULE.component_base(layers, index, "main") for index in range(3)],
            ["main", "stack/plan-pr-ready", "stack/story-1.1-pr-ready"],
        )

    def test_component_remote_name_cannot_alias_pr_ready_branch(self) -> None:
        with self.assertRaisesRegex(MODULE.SubmitError, "exactly match"):
            MODULE.validate_component_branch_names(
                {
                    "branch": "stack/story-pr-ready",
                    "remote_branch": "stack/story-release",
                }
            )

    def test_json_inputs_require_objects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = directory / "manifest.json"
            manifest.write_text("[]\n", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.SubmitError, "JSON object"):
                MODULE.load_manifest(manifest)
            links = directory / "links.json"
            links.write_text("[]\n", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.SubmitError, "JSON object"):
                MODULE.load_manual_links(links, 1, "example.test/owner/repo")

    def test_journal_write_replaces_symlink_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            target = directory / "target.json"
            target.write_text("preserve\n", encoding="utf-8")
            journal = directory / "journal.json"
            journal.symlink_to(target)
            MODULE.write_journal(journal, {"status": "preflight", "layers": []})
            self.assertFalse(journal.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "preserve\n")

    def test_markdown_fence_closer_rejects_trailing_text(self) -> None:
        content = "```text\n## Hidden\n``` trailing\n## Still hidden\n```\n## Visible\n"
        self.assertEqual(MODULE.markdown_headings(content), ["## Visible"])

    def test_malformed_remote_port_is_rejected_cleanly(self) -> None:
        with self.assertRaisesRegex(MODULE.SubmitError, "cannot parse remote URL"):
            MODULE.parse_remote("https://example.test:not-a-port/owner/repo.git")

    def test_repository_template_remains_authoritative_over_fallback(self) -> None:
        repository_template = ".github/PULL_REQUEST_TEMPLATE.md"
        MODULE.validate_template_source(repository_template, [repository_template])
        with self.assertRaisesRegex(MODULE.SubmitError, "fallback template is forbidden"):
            MODULE.validate_template_source(
                MODULE.FALLBACK_TEMPLATE_SOURCE,
                [repository_template],
            )
        MODULE.validate_template_source(MODULE.FALLBACK_TEMPLATE_SOURCE, [])

    @mock.patch.object(MODULE, "git")
    def test_repository_template_discovery_is_case_insensitive_and_markdown_only(
        self,
        git_mock: mock.Mock,
    ) -> None:
        git_mock.return_value = "\n".join(
            (
                "pull_request_template.md",
                "docs/pull_request_template.md",
                ".github/pull_request_template/feature.md",
                ".github/PULL_REQUEST_TEMPLATE/config.yml",
            )
        )
        self.assertEqual(
            MODULE.repository_template_paths(Path.cwd(), "a" * 40),
            [
                ".github/pull_request_template/feature.md",
                "docs/pull_request_template.md",
                "pull_request_template.md",
            ],
        )

    @mock.patch.object(MODULE, "git")
    def test_repository_template_structure_must_be_preserved(
        self,
        git_mock: mock.Mock,
    ) -> None:
        git_mock.return_value = (
            "## Summary\n\nKeep this sentence.\n\n"
            "## Testing\n\n- [ ] Added or updated tests\n"
        )
        MODULE.validate_repository_template_body(
            Path.cwd(),
            "a" * 40,
            ".github/PULL_REQUEST_TEMPLATE.md",
            "## Summary\n\nKeep this sentence.\n\nDone.\n\n"
            "## Testing\n\n- [x] Added or updated tests\n",
            label="body",
        )
        with self.assertRaisesRegex(MODULE.SubmitError, "heading"):
            MODULE.validate_repository_template_body(
                Path.cwd(),
                "a" * 40,
                ".github/PULL_REQUEST_TEMPLATE.md",
                "## Summary\n\nNo testing section.\n",
                label="body",
            )
        with self.assertRaisesRegex(MODULE.SubmitError, "template prose"):
            MODULE.validate_repository_template_body(
                Path.cwd(),
                "a" * 40,
                ".github/PULL_REQUEST_TEMPLATE.md",
                "## Summary\n\n## Testing\n\n- [x] Added or updated tests\n",
                label="body",
            )

    @mock.patch.object(MODULE, "remote_sha")
    @mock.patch.object(MODULE, "pull_requests_for_head")
    def test_origin_review_approval_binds_exact_component_and_evidence_prs(
        self,
        pulls_mock: mock.Mock,
        remote_mock: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            component_tip = "a" * 40
            evidence_tip = "b" * 40
            base_tip = "c" * 40
            pre_manifest = directory / "pre-manifest.json"
            pre_journal = directory / "pre-journal.json"
            accepted_state = directory / "accepted-state.json"
            approved_spec = directory / "spec.md"
            pre_body = directory / "pre-body.md"
            current_body = directory / "regenerated-body.md"
            pre_manifest.write_text('{"pre":"manifest"}\n', encoding="utf-8")
            pre_body.write_text("pre-review body\n", encoding="utf-8")
            current_body.write_text("regenerated body\n", encoding="utf-8")
            pre_journal.write_text(
                json.dumps({"layers": [{"source_body": str(pre_body)}]}),
                encoding="utf-8",
            )
            pr_ready = directory / "pr-ready.json"
            pr_ready.write_text(
                json.dumps(
                    {
                        "status": "applied",
                        "remote": "upstream",
                        "layers": [
                            {
                                "source": "feat/source",
                                "source_tip": component_tip,
                                "target": "feat/component-pr-ready",
                                "new_tip": component_tip,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            accepted_state.write_text(
                json.dumps(
                    {"status": "accepted", "prReadyArtifact": str(pr_ready)}
                ),
                encoding="utf-8",
            )
            approved_spec.write_text("---\nstatus: done\n---\n", encoding="utf-8")
            request = directory / "request.json"
            request.write_text(
                json.dumps(
                    {
                        "canonical_accepted_state": str(accepted_state),
                        "submission_manifest": str(pre_manifest),
                        "submission_journal": str(pre_journal),
                        "approved_spec": {"path": str(approved_spec)},
                    }
                ),
                encoding="utf-8",
            )
            component_head = {
                "role": "component",
                "position": 1,
                "branch": "bmad-review/feature/01",
                "sha": component_tip,
                "base": "main",
                "base_sha": base_tip,
                "title": "feat: component",
            }
            evidence_head = {
                "role": "evidence",
                "position": None,
                "branch": "bmad-review/feature/evidence",
                "sha": evidence_tip,
                "base": "main",
                "base_sha": base_tip,
                "title": "test: evidence",
            }
            component_body = "origin component"
            evidence_body = "DO NOT MERGE origin evidence"
            component_pr = {
                "role": "component",
                "position": 1,
                "number": 1,
                "url": "https://example.test/fork/repo/pull/1",
                "state": "OPEN",
                "draft": True,
                "title": component_head["title"],
                "body_sha256": MODULE.sha256_text(component_body),
                "head_sha": component_tip,
            }
            evidence_pr = {
                "role": "evidence",
                "position": None,
                "number": 2,
                "url": "https://example.test/fork/repo/pull/2",
                "state": "OPEN",
                "draft": True,
                "title": evidence_head["title"],
                "body_sha256": MODULE.sha256_text(evidence_body),
                "head_sha": evidence_tip,
            }
            input_hashes = {
                "request": MODULE.sha256_file(request),
                "canonical_accepted_state": MODULE.sha256_file(accepted_state),
                "submission_manifest": MODULE.sha256_file(pre_manifest),
                "submission_journal": MODULE.sha256_file(pre_journal),
                "approved_spec": MODULE.sha256_file(approved_spec),
            }
            staging_progress = directory / "staging-progress.json"
            staging_progress.write_text('{"status":"complete"}\n', encoding="utf-8")
            stage = directory / "stage.json"
            stage_receipt = {
                "schema_version": 1,
                "status": "staged",
                "request_path": str(request),
                "input_hashes": input_hashes,
                "staging_progress": str(staging_progress),
                "staging_progress_sha256": MODULE.sha256_file(staging_progress),
                "repository": "example.test/fork/repo",
                "host": "example.test",
                "origin_remote": "origin",
                "base_policy": "require-equal",
                "namespace": "bmad-review/feature",
                "base_sha": base_tip,
                "heads": [component_head, evidence_head],
                "pull_requests": [component_pr, evidence_pr],
                "evidence_contract": {
                    "permanent_draft": True,
                    "merge": "prohibited",
                    "base": "main",
                },
            }
            stage.write_text(json.dumps(stage_receipt), encoding="utf-8")
            receipt = {
                "schema_version": 1,
                "status": "audited",
                "request_path": str(request),
                "input_hashes": input_hashes,
                "stage_receipt": str(stage),
                "stage_receipt_sha256": MODULE.sha256_file(stage),
                "repository": "example.test/fork/repo",
                "host": "example.test",
                "namespace": "bmad-review/feature",
                "base_sha": base_tip,
                "projection": {"heads": [component_head, evidence_head]},
                "live_refs": [
                    {"branch": component_head["branch"], "sha": component_tip},
                    {"branch": evidence_head["branch"], "sha": evidence_tip},
                ],
                "pull_requests": [component_pr, evidence_pr],
                "stage_pull_requests": [component_pr, evidence_pr],
            }
            receipt_path = directory / "audit.json"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            manifest = {
                "_evidence_repository": "example.test/fork/repo",
                "_evidence_owner": "fork",
                "evidence_remote": "origin",
                "target_remote": "upstream",
                "publish_remote": "upstream",
                "base_sha": base_tip,
                "default_base": "main",
                "integration_evidence": {"_commit": evidence_tip},
                "origin_review": {
                    "audit_receipt": str(receipt_path),
                    "audit_receipt_sha256": MODULE.sha256_file(receipt_path),
                    "pre_review_manifest_sha256": input_hashes["submission_manifest"],
                    "pre_review_journal_sha256": input_hashes["submission_journal"],
                    "approved": True,
                    "approval_phrase": MODULE.ORIGIN_REVIEW_APPROVAL_PHRASE,
                },
            }
            current_manifest = directory / "upstream-manifest.json"
            current_manifest.write_text(
                json.dumps({"origin_review": manifest["origin_review"]}),
                encoding="utf-8",
            )
            pulls_mock.side_effect = [
                [{
                    "number": 1, "url": component_pr["url"], "title": component_head["title"],
                    "body": component_body, "body_sha256": component_pr["body_sha256"],
                    "state": "OPEN", "isDraft": True, "baseRefName": "main",
                    "baseRefOid": base_tip, "headRefName": component_head["branch"],
                    "headRefOid": component_tip, "headRepositoryOwner": "fork",
                }],
                [{
                    "number": 2, "url": evidence_pr["url"], "title": evidence_head["title"],
                    "body": evidence_body, "body_sha256": evidence_pr["body_sha256"],
                    "state": "OPEN", "isDraft": True, "baseRefName": "main",
                    "baseRefOid": base_tip, "headRefName": evidence_head["branch"],
                    "headRefOid": evidence_tip, "headRepositoryOwner": "fork",
                }],
            ]
            remote_mock.side_effect = lambda _repo, remote, branch: (
                component_tip
                if (remote, branch)
                in {
                    ("origin", "feat/source"),
                    ("upstream", "feat/component-pr-ready"),
                }
                else None
            )
            MODULE.validate_origin_review_approval(
                directory,
                current_manifest,
                manifest,
                [
                    {
                        "_tip": component_tip,
                        "_body_file": current_body,
                        "remote_branch": "feat/component-pr-ready",
                    }
                ],
            )
            self.assertEqual(
                manifest["_origin_review"]["audit_receipt_sha256"],
                MODULE.sha256_file(receipt_path),
            )

    @mock.patch.object(MODULE, "configure_command_environment")
    @mock.patch.object(MODULE, "validate", return_value=[])
    @mock.patch.object(
        MODULE,
        "load_manifest",
        return_value={
            "repository": "example.test/upstream/repo",
            "_evidence_cross_repository": True,
        },
    )
    def test_fork_apply_requires_approved_origin_review(
        self,
        _load_mock: mock.Mock,
        _validate_mock: mock.Mock,
        _configure_mock: mock.Mock,
    ) -> None:
        with self.assertRaisesRegex(MODULE.SubmitError, "origin review is staged"):
            MODULE.submit(
                Path.cwd(),
                Path("manifest.json"),
                apply=True,
                manual=False,
                output=None,
                rendered_dir=None,
                manual_links=None,
            )

    def test_origin_review_required_reflects_evidence_topology(self) -> None:
        self.assertTrue(
            MODULE.origin_review_required({"_evidence_cross_repository": True})
        )
        self.assertFalse(
            MODULE.origin_review_required({"_evidence_cross_repository": False})
        )

    @mock.patch.object(MODULE, "configure_command_environment")
    @mock.patch.object(MODULE, "validate", return_value=[])
    @mock.patch.object(
        MODULE,
        "load_manifest",
        return_value={
            "repository": "example.test/upstream/repo",
            "_evidence_cross_repository": False,
        },
    )
    def test_single_repo_apply_skips_origin_review_but_still_requires_approved_dry_run(
        self,
        _load_mock: mock.Mock,
        _validate_mock: mock.Mock,
        _configure_mock: mock.Mock,
    ) -> None:
        """Direct-to-upstream (non-fork) submissions have no preview repository to audit,
        so the origin-review ceremony must not block --apply. --approved-dry-run-journal
        remains mandatory and is the sole human-approval gate in this topology."""
        with self.assertRaisesRegex(
            MODULE.SubmitError, "requires --approved-dry-run-journal"
        ):
            MODULE.submit(
                Path.cwd(),
                Path("manifest.json"),
                apply=True,
                manual=False,
                output=None,
                rendered_dir=None,
                manual_links=None,
            )

    @mock.patch.object(MODULE, "github_preflight", side_effect=RuntimeError("reached preflight"))
    @mock.patch.object(MODULE, "validate_sealed_apply_request")
    @mock.patch.object(MODULE, "configure_command_environment")
    @mock.patch.object(MODULE, "validate", return_value=[])
    @mock.patch.object(MODULE, "validate_approved_dry_run", return_value={"ok": True})
    @mock.patch.object(
        MODULE,
        "load_manifest",
        return_value={
            "repository": "example.test/upstream/repo",
            "_evidence_cross_repository": False,
        },
    )
    def test_single_repo_apply_never_invokes_sealed_apply_request(
        self,
        _load_mock: mock.Mock,
        _approved_dry_run_mock: mock.Mock,
        _validate_mock: mock.Mock,
        _configure_mock: mock.Mock,
        sealed_request_mock: mock.Mock,
        _preflight_mock: mock.Mock,
    ) -> None:
        """The fork-only prepare/seal apply-request ceremony (backed by
        prepare_upstream_submission.py) must never run for a single-repo submission,
        even when a stray --approved-apply-request happens to be passed. Execution must
        instead proceed past both fork-only gates straight into the normal preflight path."""
        with self.assertRaisesRegex(RuntimeError, "reached preflight"):
            MODULE.submit(
                Path.cwd(),
                Path("manifest.json"),
                apply=True,
                manual=False,
                output=None,
                rendered_dir=None,
                manual_links=None,
                approved_dry_run=Path("dry-run.json"),
                approved_apply_request=Path("apply-request.json"),
            )
        sealed_request_mock.assert_not_called()

    def test_first_apply_with_existing_prs_skips_progress_reconcile_but_still_verifies(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest_path, manifest, layers, approval, approved_journal = (
                self.build_apply_submit_fixture(directory)
            )
            output_path = directory / "apply-progress.json"

            def populate_preflight(
                _repo: Path,
                live_manifest: dict[str, Any],
                live_layers: list[dict[str, Any]],
            ) -> None:
                live_layers[0]["_existing_pr"] = {
                    "number": 41,
                    "url": "https://example.test/upstream/repo/pull/41",
                }
                live_manifest["_existing_integration_pr"] = {
                    "number": 90,
                    "url": "https://example.test/upstream/repo/pull/90",
                }

            with mock.patch.object(
                MODULE, "load_manifest", return_value=manifest
            ), mock.patch.object(
                MODULE, "configure_command_environment"
            ), mock.patch.object(
                MODULE, "validate", return_value=layers
            ), mock.patch.object(
                MODULE, "validate_approved_dry_run", return_value=approved_journal
            ), mock.patch.object(
                MODULE, "validate_sealed_apply_request", return_value=approval
            ), mock.patch.object(
                MODULE, "github_preflight", side_effect=populate_preflight
            ), mock.patch.object(
                MODULE, "verify_pull_request"
            ) as verify_pr_mock, mock.patch.object(
                MODULE,
                "verify_integration_pull_request",
                side_effect=RuntimeError("verified live integration"),
            ) as verify_integration_mock:
                with self.assertRaisesRegex(
                    RuntimeError, "verified live integration"
                ):
                    MODULE.submit(
                        directory,
                        manifest_path,
                        apply=True,
                        manual=False,
                        output=output_path,
                        rendered_dir=directory / "rendered",
                        manual_links=None,
                        approved_dry_run=directory / "approved-dry-run.json",
                        approved_apply_request=directory / "approved-apply-request.json",
                    )

            self.assertFalse(output_path.exists())
            self.assertEqual(verify_pr_mock.call_count, 1)
            self.assertEqual(verify_pr_mock.call_args.args[7]["number"], 41)
            self.assertEqual(verify_integration_mock.call_count, 1)
            self.assertEqual(verify_integration_mock.call_args.args[4]["number"], 90)

    def test_resume_apply_still_rejects_live_pr_drift_from_progress_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest_path, manifest, layers, approval, approved_journal = (
                self.build_apply_submit_fixture(directory)
            )
            output_path = directory / "apply-progress.json"
            output_path.write_text(
                json.dumps(
                    {
                        "status": "submitting",
                        "manifest": str(manifest_path.resolve()),
                        "manifest_sha256": MODULE.sha256_file(manifest_path),
                        "repository": manifest["repository"],
                        "base_sha": manifest["base_sha"],
                        "apply_approval": approval,
                        "layers": [
                            {
                                "branch": layers[0]["branch"],
                                "remote_branch": layers[0]["remote_branch"],
                                "tip": layers[0]["_tip"],
                                "base": "main",
                                "head": layers[0]["_head_ref"],
                                "pr": {
                                    "number": 99,
                                    "url": "https://example.test/upstream/repo/pull/99",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            def populate_preflight(
                _repo: Path,
                _manifest: dict[str, Any],
                live_layers: list[dict[str, Any]],
            ) -> None:
                live_layers[0]["_existing_pr"] = {
                    "number": 41,
                    "url": "https://example.test/upstream/repo/pull/41",
                }

            with mock.patch.object(
                MODULE, "load_manifest", return_value=manifest
            ), mock.patch.object(
                MODULE, "configure_command_environment"
            ), mock.patch.object(
                MODULE, "validate", return_value=layers
            ), mock.patch.object(
                MODULE, "validate_approved_dry_run", return_value=approved_journal
            ), mock.patch.object(
                MODULE, "validate_sealed_apply_request", return_value=approval
            ), mock.patch.object(
                MODULE, "github_preflight", side_effect=populate_preflight
            ), mock.patch.object(MODULE, "verify_pull_request") as verify_pr_mock:
                with self.assertRaisesRegex(
                    MODULE.SubmitError,
                    "live upstream component PRs do not match the sealed apply journal",
                ):
                    MODULE.submit(
                        directory,
                        manifest_path,
                        apply=True,
                        manual=False,
                        output=output_path,
                        rendered_dir=directory / "rendered",
                        manual_links=None,
                        approved_dry_run=directory / "approved-dry-run.json",
                        approved_apply_request=directory / "approved-apply-request.json",
                    )

            verify_pr_mock.assert_not_called()

    def test_only_integration_layer_uses_origin_fork_head(self) -> None:
        manifest = {
            "_head_owner": "upstream-owner",
            "_evidence_owner": "fork-owner",
            "base_sha": "a" * 40,
            "feature_name": "Feature",
            "integration_evidence": {
                "branch": "integration/evidence",
                "_commit": "b" * 40,
            },
        }
        layer = MODULE.integration_layer(manifest)
        self.assertEqual(layer["_head_ref"], "fork-owner:integration/evidence")
        self.assertEqual(layer["_head_owner"], "fork-owner")

    def test_single_repository_evidence_uses_local_head(self) -> None:
        manifest = {
            "_head_owner": "target-owner",
            "_evidence_owner": "target-owner",
            "_evidence_cross_repository": False,
            "base_sha": "a" * 40,
            "feature_name": "Feature",
            "integration_evidence": {
                "branch": "integration/evidence",
                "_commit": "b" * 40,
            },
        }
        self.assertEqual(
            MODULE.integration_layer(manifest)["_head_ref"],
            "integration/evidence",
        )

    @mock.patch.object(MODULE, "remote_sha")
    def test_fork_release_rejects_component_head_on_evidence_remote(
        self,
        remote_mock: mock.Mock,
    ) -> None:
        remote_mock.return_value = "a" * 40
        manifest = {
            "_evidence_cross_repository": True,
            "evidence_remote": "origin",
            "publish_remote": "upstream",
            "integration_evidence": {"branch": "integration/evidence"},
        }
        with self.assertRaisesRegex(MODULE.SubmitError, "PR-ready component head"):
            MODULE.validate_release_branch_placement(
                Path.cwd(),
                manifest,
                [{"remote_branch": "stack/plan-pr-ready"}],
            )

    @mock.patch.object(MODULE, "remote_sha", side_effect=[None, "b" * 40])
    def test_fork_release_rejects_evidence_head_on_target(
        self,
        _remote_mock: mock.Mock,
    ) -> None:
        manifest = {
            "_evidence_cross_repository": True,
            "evidence_remote": "origin",
            "publish_remote": "upstream",
            "integration_evidence": {"branch": "integration/evidence"},
        }
        with self.assertRaisesRegex(MODULE.SubmitError, "evidence branch"):
            MODULE.validate_release_branch_placement(
                Path.cwd(),
                manifest,
                [{"remote_branch": "stack/plan-pr-ready"}],
            )

    @mock.patch.object(MODULE, "remote_sha", return_value="c" * 40)
    def test_moved_origin_evidence_is_rejected(self, _remote_mock: mock.Mock) -> None:
        manifest = {
            "evidence_remote": "origin",
            "integration_evidence": {
                "branch": "integration/evidence",
                "_commit": "b" * 40,
            },
        }
        with self.assertRaisesRegex(MODULE.SubmitError, "evidence branch moved"):
            MODULE.verify_published_evidence(Path.cwd(), manifest)

    @mock.patch.object(MODULE, "gh")
    @mock.patch.object(MODULE, "create_pull_request")
    @mock.patch.object(MODULE, "publish")
    @mock.patch.object(MODULE, "github_preflight")
    @mock.patch.object(MODULE, "configure_command_environment")
    @mock.patch.object(MODULE, "validate")
    @mock.patch.object(MODULE, "load_manifest")
    def test_three_layer_dry_run_records_bases_without_publication(
        self,
        load_mock: mock.Mock,
        validate_mock: mock.Mock,
        _configure_mock: mock.Mock,
        _preflight_mock: mock.Mock,
        publish_mock: mock.Mock,
        create_mock: mock.Mock,
        gh_mock: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            body = directory / "body.md"
            body.write_text("## Summary\n\nLayer.\n", encoding="utf-8")
            layers = [
                {
                    "branch": f"stack/{name}-pr-ready",
                    "remote_branch": f"stack/{name}-pr-ready",
                    "_head_ref": f"stack/{name}-pr-ready",
                    "_tip": str(index + 1) * 40,
                    "_body_file": body,
                    "title": f"feat: {name}",
                    "summary": name,
                }
                for index, name in enumerate(("plan", "story-1.1", "story-1.2"))
            ]
            manifest = {
                "repository": "github.example.com/upstream/repo",
                "default_base": "main",
                "base_sha": "0" * 40,
                "feature_name": "Feature X",
                "feature_summary": "Adds focused behavior.",
                "stack_label": "feature-x",
                "evidence_remote": "origin",
                "template_source": ".github/PULL_REQUEST_TEMPLATE.md",
                "integration_evidence": dict(self.evidence),
                "_head_repository": "github.example.com/upstream/repo",
                "_head_owner": "upstream",
                "_evidence_owner": "fork-owner",
                "_evidence_repository": "github.example.com/fork-owner/repo",
                "_cross_repository": False,
            }
            manifest["_integration_layer"] = MODULE.integration_layer(manifest)
            load_mock.return_value = manifest
            validate_mock.return_value = layers
            manifest_path = directory / "manifest.json"
            manifest_path.write_text("{}\n", encoding="utf-8")
            journal = MODULE.submit(
                directory,
                manifest_path,
                apply=False,
                manual=False,
                output=None,
                rendered_dir=directory / "rendered",
                manual_links=None,
            )
            for layer in journal["layers"]:
                self.assertEqual(layer["source_body"], str(body.resolve()))
                for artifact in ("source_body", "rendered_title", "rendered_body"):
                    self.assertEqual(
                        layer[f"{artifact}_sha256"],
                        MODULE.sha256_file(Path(layer[artifact])),
                    )
            for artifact in ("rendered_title", "rendered_body"):
                self.assertEqual(
                    journal["integration_pr"][f"{artifact}_sha256"],
                    MODULE.sha256_file(Path(journal["integration_pr"][artifact])),
                )

        self.assertEqual(
            [layer["base"] for layer in journal["layers"]],
            ["main", "stack/plan-pr-ready", "stack/story-1.1-pr-ready"],
        )
        self.assertEqual(journal["integration_pr"]["base"], "main")
        self.assertEqual(journal["status"], "dry-run")
        publish_mock.assert_not_called()
        create_mock.assert_not_called()
        gh_mock.assert_not_called()

    def test_validate_approved_dry_run_accepts_real_dry_run_journal_round_trip(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest_path, manifest, layers, journal_path, journal = (
                self.write_dry_run_journal_fixture(directory)
            )
            manifest, layers = self.reset_preflight_state(manifest, layers)

            approved = MODULE.validate_approved_dry_run(
                journal_path.resolve(),
                manifest_path,
                manifest,
                layers,
            )

            self.assertEqual(
                [layer["base"] for layer in approved["layers"]],
                ["main", "stack/plan-pr-ready", "stack/story-1.1-pr-ready"],
            )
            self.assertEqual(
                approved["_approved_component_bodies"],
                [
                    Path(layer["rendered_body"]).read_text(encoding="utf-8")
                    for layer in journal["layers"]
                ],
            )
            self.assertEqual(
                approved["_approved_integration_body"],
                Path(journal["integration_pr"]["rendered_body"]).read_text(
                    encoding="utf-8"
                ),
            )

    def test_validate_approved_dry_run_accepts_existing_component_links_round_trip(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest_path, manifest, layers, journal_path, journal = (
                self.write_dry_run_journal_fixture(
                    directory,
                    existing_prs={
                        0: {
                            "number": 14,
                            "url": "https://example.test/upstream/repo/pull/14",
                        }
                    },
                )
            )
            manifest, layers = self.reset_preflight_state(manifest, layers)
            recorded_layer = journal["layers"][1]
            self.assertIn("pr", journal["layers"][0])
            self.assertNotEqual(
                recorded_layer["rendered_body_sha256"],
                MODULE.sha256_text(
                    MODULE.render_body(
                        layers,
                        {},
                        1,
                        manifest["default_base"],
                        manifest["feature_summary"],
                        manifest["stack_label"],
                        manifest["integration_evidence"],
                        manifest["_head_owner"],
                        manifest["feature_name"],
                    )
                ),
            )
            self.assertNotEqual(
                journal["integration_pr"]["rendered_body_sha256"],
                MODULE.sha256_text(MODULE.render_integration_body(manifest, layers, {})),
            )

            approved = MODULE.validate_approved_dry_run(
                journal_path.resolve(),
                manifest_path,
                manifest,
                layers,
            )

            self.assertEqual(approved["layers"][0]["pr"]["number"], 14)

    def test_validate_approved_dry_run_accepts_existing_integration_pr_round_trip(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest_path, manifest, layers, journal_path, journal = (
                self.write_dry_run_journal_fixture(
                    directory,
                    existing_integration_pr={
                        "number": 90,
                        "url": "https://example.test/upstream/repo/pull/90",
                    },
                )
            )
            manifest, layers = self.reset_preflight_state(manifest, layers)
            recorded_layer = journal["layers"][0]
            self.assertIn("pr", journal["integration_pr"])
            self.assertIn(
                "Combined stack validation PR",
                Path(recorded_layer["rendered_body"]).read_text(encoding="utf-8"),
            )
            self.assertNotEqual(
                recorded_layer["rendered_body_sha256"],
                MODULE.sha256_text(
                    MODULE.render_body(
                        layers,
                        {},
                        0,
                        manifest["default_base"],
                        manifest["feature_summary"],
                        manifest["stack_label"],
                        manifest["integration_evidence"],
                        manifest["_head_owner"],
                        manifest["feature_name"],
                    )
                ),
            )

            approved = MODULE.validate_approved_dry_run(
                journal_path.resolve(),
                manifest_path,
                manifest,
                layers,
            )

            self.assertEqual(approved["integration_pr"]["pr"]["number"], 90)

    def test_validate_approved_dry_run_rejects_sha_instead_of_branch_base(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest_path, manifest, layers, journal_path, _journal = (
                self.write_dry_run_journal_fixture(directory)
            )
            manifest, layers = self.reset_preflight_state(manifest, layers)
            payload = json.loads(journal_path.read_text(encoding="utf-8"))
            payload["layers"][1]["base"] = layers[1]["_base_sha"]
            journal_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(
                MODULE.SubmitError, "approved dry-run journal layer topology changed"
            ):
                MODULE.validate_approved_dry_run(
                    journal_path.resolve(),
                    manifest_path,
                    manifest,
                    layers,
                )

    def test_load_apply_progress_accepts_branch_bases_from_persisted_journal(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest_path, manifest, layers, journal_path, _journal = (
                self.write_dry_run_journal_fixture(directory)
            )
            manifest, layers = self.reset_preflight_state(manifest, layers)
            approval = {
                "apply_request": str(directory / "apply-request.json"),
                "apply_request_sha256": "a" * 64,
                "preparation_receipt": str(directory / "preparation-receipt.json"),
                "preparation_receipt_sha256": "b" * 64,
            }
            progress = json.loads(journal_path.read_text(encoding="utf-8"))
            progress["status"] = "submitting"
            progress["apply_approval"] = approval
            progress_path = directory / "apply-progress.json"
            MODULE.write_journal(progress_path, progress)

            loaded = MODULE.load_apply_progress(
                progress_path,
                manifest_path,
                manifest,
                layers,
                approval,
            )

            self.assertEqual(
                [layer["base"] for layer in loaded["layers"]],
                ["main", "stack/plan-pr-ready", "stack/story-1.1-pr-ready"],
            )

    @mock.patch.object(MODULE, "git", return_value="")
    @mock.patch.object(MODULE, "validate_remote_urls")
    def test_manifest_forbids_cross_repository_component_publication(
        self,
        remote_mock: mock.Mock,
        _git_mock: mock.Mock,
    ) -> None:
        remote_mock.side_effect = [
            ("example.test", "upstream", "repo"),
            ("example.test", "fork-owner", "repo"),
        ]
        manifest = {
            "repository": "example.test/upstream/repo",
            "target_remote": "upstream",
            "publish_remote": "origin",
            "evidence_remote": "origin",
            "evidence_repository": "example.test/fork-owner/repo",
            "default_base": "main",
            "base_sha": "a" * 40,
            "feature_name": "Feature X",
            "feature_summary": "Summary",
            "stack_label": "feature-x",
            "draft": True,
            "template_source": ".github/PULL_REQUEST_TEMPLATE.md",
            "layers": [{}],
        }
        with self.assertRaisesRegex(MODULE.SubmitError, "cross-repository component heads"):
            MODULE.validate(Path.cwd(), Path("manifest.json"), manifest)

    @mock.patch.object(MODULE, "preflight_integration_pull_request")
    @mock.patch.object(MODULE, "remote_sha", return_value=None)
    @mock.patch.object(MODULE, "pull_requests_for_head")
    @mock.patch.object(MODULE, "github_repository_preflight")
    def test_preflight_ignores_superseded_closed_pr_for_its_head(
        self,
        _repository_mock: mock.Mock,
        pulls_mock: mock.Mock,
        remote_mock: mock.Mock,
        _integration_mock: mock.Mock,
    ) -> None:
        """A permanently-closed PR (e.g. unreopenable after a force-push) that is explicitly
        listed as superseded must not block preflight for a fresh PR on the same head."""
        pulls_mock.return_value = [
            {
                "number": 33,
                "url": "https://example.test/upstream/repo/pull/33",
                "state": "CLOSED",
                "isDraft": False,
                "baseRefName": "stack/plan-pr-ready",
                "baseRefOid": "z" * 40,
                "headRefOid": "b" * 40,
                "headRepositoryOwner": "upstream",
            }
        ]
        layers = [
            {
                "remote_branch": "stack/plan-pr-ready",
                "_head_ref": "stack/plan-pr-ready",
                "_tip": "a" * 40,
                "_superseded_prs": set(),
            },
            {
                "remote_branch": "stack/story-pr-ready",
                "_head_ref": "stack/story-pr-ready",
                "_tip": "b" * 40,
                "_superseded_prs": {33},
            },
        ]
        manifest = {
            "repository": "example.test/upstream/repo",
            "_head_owner": "upstream",
            "publish_remote": "upstream",
            "default_base": "main",
            "base_sha": "a" * 40,
            "draft": True,
        }
        pulls_mock.side_effect = [[], pulls_mock.return_value]
        remote_mock.side_effect = ["a" * 40, "b" * 40]

        MODULE.github_preflight(Path.cwd(), manifest, layers)
        self.assertNotIn("_existing_pr", layers[1])

    @mock.patch.object(MODULE, "preflight_integration_pull_request")
    @mock.patch.object(MODULE, "remote_sha", return_value=None)
    @mock.patch.object(MODULE, "pull_requests_for_head")
    @mock.patch.object(MODULE, "github_repository_preflight")
    def test_preflight_rejects_superseded_prs_that_are_still_open(
        self,
        _repository_mock: mock.Mock,
        pulls_mock: mock.Mock,
        remote_mock: mock.Mock,
        _integration_mock: mock.Mock,
    ) -> None:
        """superseded_prs must never silently bypass a live, still-open competing PR."""
        pulls_mock.return_value = [
            {
                "number": 33,
                "url": "https://example.test/upstream/repo/pull/33",
                "state": "OPEN",
                "isDraft": False,
                "baseRefName": "stack/plan-pr-ready",
                "baseRefOid": "z" * 40,
                "headRefOid": "b" * 40,
                "headRepositoryOwner": "upstream",
            }
        ]
        layers = [
            {
                "remote_branch": "stack/plan-pr-ready",
                "_head_ref": "stack/plan-pr-ready",
                "_tip": "a" * 40,
                "_superseded_prs": set(),
            },
            {
                "remote_branch": "stack/story-pr-ready",
                "_head_ref": "stack/story-pr-ready",
                "_tip": "b" * 40,
                "_superseded_prs": {33},
            },
        ]
        manifest = {
            "repository": "example.test/upstream/repo",
            "_head_owner": "upstream",
            "publish_remote": "upstream",
            "default_base": "main",
            "base_sha": "a" * 40,
            "draft": True,
        }
        pulls_mock.side_effect = [[], pulls_mock.return_value]
        remote_mock.return_value = "a" * 40

        with self.assertRaisesRegex(MODULE.SubmitError, "still-OPEN"):
            MODULE.github_preflight(Path.cwd(), manifest, layers)

    @mock.patch.object(MODULE, "preflight_integration_pull_request")
    @mock.patch.object(MODULE, "remote_sha", return_value=None)
    @mock.patch.object(MODULE, "pull_requests_for_head")
    @mock.patch.object(MODULE, "github_repository_preflight")
    def test_preflight_rejects_superseded_pr_number_that_does_not_exist(
        self,
        _repository_mock: mock.Mock,
        pulls_mock: mock.Mock,
        remote_mock: mock.Mock,
        _integration_mock: mock.Mock,
    ) -> None:
        """A stale/mistyped superseded_prs entry that matches no PR fails closed."""
        layers = [
            {
                "remote_branch": "stack/plan-pr-ready",
                "_head_ref": "stack/plan-pr-ready",
                "_tip": "a" * 40,
                "_superseded_prs": set(),
            },
            {
                "remote_branch": "stack/story-pr-ready",
                "_head_ref": "stack/story-pr-ready",
                "_tip": "b" * 40,
                "_superseded_prs": {33},
            },
        ]
        manifest = {
            "repository": "example.test/upstream/repo",
            "_head_owner": "upstream",
            "publish_remote": "upstream",
            "default_base": "main",
            "base_sha": "a" * 40,
            "draft": True,
        }
        pulls_mock.side_effect = [[], []]
        remote_mock.return_value = "a" * 40

        with self.assertRaisesRegex(MODULE.SubmitError, "fix the allowlist"):
            MODULE.github_preflight(Path.cwd(), manifest, layers)

    @mock.patch.object(MODULE, "preflight_integration_pull_request")
    @mock.patch.object(MODULE, "remote_sha", return_value=None)
    @mock.patch.object(MODULE, "pull_requests_for_head")
    @mock.patch.object(MODULE, "github_repository_preflight")
    def test_preflight_rejects_existing_pr_with_wrong_component_base(
        self,
        _repository_mock: mock.Mock,
        pulls_mock: mock.Mock,
        remote_mock: mock.Mock,
        _integration_mock: mock.Mock,
    ) -> None:
        pulls_mock.return_value = [
            {
                "number": 42,
                "url": "https://example.test/upstream/repo/pull/42",
                "state": "OPEN",
                "isDraft": True,
                "baseRefName": "main",
                "baseRefOid": "a" * 40,
                "headRefOid": "b" * 40,
                "headRepositoryOwner": "upstream",
            }
        ]
        layers = [
            {
                "remote_branch": "stack/plan-pr-ready",
                "_head_ref": "stack/plan-pr-ready",
                "_tip": "a" * 40,
            },
            {
                "remote_branch": "stack/story-pr-ready",
                "_head_ref": "stack/story-pr-ready",
                "_tip": "b" * 40,
            },
        ]
        manifest = {
            "repository": "example.test/upstream/repo",
            "_head_owner": "upstream",
            "publish_remote": "upstream",
            "default_base": "main",
            "base_sha": "a" * 40,
            "draft": True,
        }
        pulls_mock.side_effect = [[], pulls_mock.return_value]
        remote_mock.return_value = "a" * 40

        with self.assertRaisesRegex(MODULE.SubmitError, "existing PR conflicts"):
            MODULE.github_preflight(Path.cwd(), manifest, layers)

    @mock.patch.object(MODULE, "preflight_integration_pull_request")
    @mock.patch.object(MODULE, "remote_sha", return_value=None)
    @mock.patch.object(MODULE, "pull_requests_for_head")
    @mock.patch.object(MODULE, "github_repository_preflight")
    def test_preflight_rejects_existing_pr_from_wrong_head_repository(
        self,
        _repository_mock: mock.Mock,
        pulls_mock: mock.Mock,
        _remote_mock: mock.Mock,
        _integration_mock: mock.Mock,
    ) -> None:
        pulls_mock.return_value = [
            {
                "number": 41,
                "url": "https://example.test/upstream/repo/pull/41",
                "state": "OPEN",
                "isDraft": True,
                "baseRefName": "main",
                "baseRefOid": "a" * 40,
                "headRefOid": "a" * 40,
                "headRepositoryOwner": "fork-owner",
            }
        ]
        layers = [
            {
                "remote_branch": "stack/plan-pr-ready",
                "_head_ref": "stack/plan-pr-ready",
                "_tip": "a" * 40,
            }
        ]
        manifest = {
            "repository": "example.test/upstream/repo",
            "_head_owner": "upstream",
            "publish_remote": "upstream",
            "default_base": "main",
            "base_sha": "a" * 40,
            "draft": True,
        }

        with self.assertRaisesRegex(MODULE.SubmitError, "existing PR conflicts"):
            MODULE.github_preflight(Path.cwd(), manifest, layers)

    @mock.patch.object(MODULE, "preflight_integration_pull_request")
    @mock.patch.object(MODULE, "remote_sha", return_value=None)
    @mock.patch.object(MODULE, "pull_requests_for_head", return_value=[])
    @mock.patch.object(MODULE, "github_repository_preflight")
    def test_preflight_requires_exact_pr_ready_head_already_published(
        self,
        _repository_mock: mock.Mock,
        _pulls_mock: mock.Mock,
        _remote_mock: mock.Mock,
        _integration_mock: mock.Mock,
    ) -> None:
        layers = [
            {
                "remote_branch": "stack/plan-pr-ready",
                "_head_ref": "stack/plan-pr-ready",
                "_tip": "a" * 40,
            }
        ]
        manifest = {
            "repository": "example.test/upstream/repo",
            "_head_owner": "upstream",
            "publish_remote": "upstream",
            "default_base": "main",
            "base_sha": "0" * 40,
            "draft": True,
        }

        with self.assertRaisesRegex(MODULE.SubmitError, "missing or stale"):
            MODULE.github_preflight(Path.cwd(), manifest, layers)

    def test_enterprise_environment_ignores_github_token(self) -> None:
        original = MODULE.COMMAND_ENV
        self.addCleanup(setattr, MODULE, "COMMAND_ENV", original)
        with mock.patch.dict(
            MODULE.os.environ,
            {"GH_TOKEN": "github-token", "GITHUB_TOKEN": "github-token-2"},
        ):
            MODULE.configure_command_environment("github.example.com/owner/repo")
        self.assertNotIn("GH_TOKEN", MODULE.COMMAND_ENV)
        self.assertNotIn("GITHUB_TOKEN", MODULE.COMMAND_ENV)

    def test_command_display_redacts_inline_bodies(self) -> None:
        rendered = MODULE.display_command(
            ["gh", "api", "-f", "body=secret", "pr", "comment", "--body", "also-secret"]
        )
        self.assertNotIn("secret", rendered)
        self.assertIn("body=<redacted>", rendered)

    def test_implementation_body_links_feature_plan_with_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            body_file = Path(temporary) / "body.md"
            body_file.write_text("## Summary\n\nFocused change.\n", encoding="utf-8")
            layers = [dict(layer, _body_file=body_file) for layer in self.layers]
            rendered = MODULE.render_body(
                layers,
                {0: {"number": 41, "url": "https://example.test/pull/41"}},
                1,
                "main",
                "Adds opt-in tracing across the migration-agent fleet.",
                "feature-x",
                self.evidence,
                "contributor",
                "Feature X",
            )
        self.assertTrue(rendered.startswith("> [!WARNING]\n> **Stack Merge Gate (2/2)**"))
        self.assertIn("PR stack for the Feature X feature", rendered)
        self.assertIn("#41 - docs(stacked-pr: feature-x [1/2])", rendered)
        self.assertIn("Adds opt-in tracing across the migration-agent fleet.", rendered)
        self.assertIn("[Planning PR #41](https://example.test/pull/41)", rendered)
        self.assertIn("[stacked pull request](https://www.stacking.dev/)", rendered)
        self.assertIn("All **2/2** cumulative stack prefixes", rendered)
        self.assertIn("FEATURE_X_ENABLED", rendered)
        self.assertIn("**42 passed, 1 skipped, 0 warnings**", rendered)
        self.assertIn("feature_x-1.0.0-py3-none-any.whl", rendered)

    def test_combined_stack_body_is_draft_only_and_links_components(self) -> None:
        manifest = {
            "feature_name": "Arize AX",
            "stack_label": "arize-ax",
            "integration_evidence": self.evidence,
        }
        layers = [
            dict(layer, _tip=str(index + 1) * 40)
            for index, layer in enumerate(self.layers)
        ]
        rendered = MODULE.render_integration_body(
            manifest,
            layers,
            {
                0: {"number": 41, "url": "https://example.test/pull/41"},
                1: {"number": 42, "url": "https://example.test/pull/42"},
            },
        )

        self.assertTrue(rendered.startswith("> [!CAUTION]"))
        self.assertIn("Combined stack validation PR - DO NOT MERGE", rendered)
        self.assertIn("Review and merge the component PRs", rendered)
        self.assertIn("https://example.test/pull/41", rendered)
        self.assertIn("https://example.test/pull/42", rendered)
        self.assertIn("authoritative target-repository CI result", rendered)
        self.assertIn("does not claim", rendered)

    @mock.patch.object(MODULE, "remote_sha")
    @mock.patch.object(MODULE, "git")
    @mock.patch.object(MODULE, "is_ancestor")
    @mock.patch.object(MODULE, "resolve")
    def test_integration_evidence_requires_published_descendant_with_full_prefix_coverage(
        self,
        resolve_mock: mock.Mock,
        is_ancestor_mock: mock.Mock,
        git_mock: mock.Mock,
        remote_sha_mock: mock.Mock,
    ) -> None:
        manifest = {
            "repository": "example.test/owner/repo",
            "publish_remote": "upstream",
            "evidence_remote": "origin",
            "_evidence_repository": "example.test/fork/repo",
            "integration_evidence": {
                key: value for key, value in self.evidence.items() if not key.startswith("_")
            },
        }
        resolve_mock.return_value = "a" * 40
        is_ancestor_mock.return_value = True
        remote_sha_mock.return_value = "a" * 40
        git_mock.return_value = (
            "- Test command: `uv run pytest`\n"
            "- Final tests: 42 passed, 1 skipped, 0 warnings\n"
            "- Prefix coverage: 2/2\n"
            "- Feature flag: `FEATURE_X_ENABLED`; default: `disabled`\n"
            "- Disabled behavior: the disabled path does not initialize the feature\n"
            "| `one` | `first` | passed | 42 passed, 1 skipped, 0 warnings |\n"
            "| `two` | `final` | passed | 42 passed, 1 skipped, 0 warnings |\n"
            f"| `feature_x-1.0.0-py3-none-any.whl` | passed | `{'b' * 64}` |\n"
        )

        MODULE.validate_integration_evidence(
            Path.cwd(),
            manifest,
            [
                {"_tip": "first", "remote_branch": "one"},
                {"_tip": "final", "remote_branch": "two"},
            ],
        )

        self.assertIn("_branch_url", manifest["integration_evidence"])
        self.assertIn("_report_url", manifest["integration_evidence"])
        self.assertTrue(
            manifest["integration_evidence"]["_branch_url"].startswith(
                "https://example.test/fork/repo/"
            )
        )
        git_mock.assert_called_once_with(
            Path.cwd(),
            "show",
            f"{'a' * 40}:docs/validation/feature-x.md",
        )

    @mock.patch.object(MODULE, "remote_sha", return_value="a" * 40)
    @mock.patch.object(
        MODULE,
        "git",
        return_value=(
            "uv run pytest\n42 passed, 1 skipped, 0 warnings\n2/2\n"
            "FEATURE_X_ENABLED\nfirst\nfinal\nfeature_x-1.0.0-py3-none-any.whl\n"
            + "b" * 64
        ),
    )
    @mock.patch.object(MODULE, "is_ancestor", return_value=True)
    @mock.patch.object(MODULE, "resolve", return_value="a" * 40)
    def test_integration_evidence_rejects_incomplete_or_changed_prefix_coverage(
        self,
        _resolve_mock: mock.Mock,
        _is_ancestor_mock: mock.Mock,
        _git_mock: mock.Mock,
        _remote_sha_mock: mock.Mock,
    ) -> None:
        evidence = {
            key: value for key, value in self.evidence.items() if not key.startswith("_")
        }
        evidence["partial_merge_safety"] = dict(evidence["partial_merge_safety"])
        evidence["partial_merge_safety"]["validated_prefixes"] = 1
        manifest = {
            "repository": "example.test/owner/repo",
            "publish_remote": "upstream",
            "evidence_remote": "origin",
            "_evidence_repository": "example.test/fork/repo",
            "integration_evidence": evidence,
        }

        with self.assertRaisesRegex(MODULE.SubmitError, "every submitted stack prefix"):
            MODULE.validate_integration_evidence(
                Path.cwd(),
                manifest,
                [
                    {"_tip": "first", "remote_branch": "one"},
                    {"_tip": "final", "remote_branch": "two"},
                ],
            )
        evidence["partial_merge_safety"]["validated_prefixes"] = 2
        with self.assertRaisesRegex(MODULE.SubmitError, "prefix tips"):
            MODULE.validate_integration_evidence(
                Path.cwd(),
                manifest,
                [
                    {"_tip": "changed", "remote_branch": "one"},
                    {"_tip": "final", "remote_branch": "two"},
                ],
            )

    def test_integration_evidence_rejects_stale_or_unsupported_claims(self) -> None:
        cases = (
            ("branch drift", None, None, ["a" * 40, "c" * 40], True, "a" * 40,
             "report", "branch drifted from its recorded commit"),
            ("non-descendant", None, None, None, False, "a" * 40,
             "report", "does not descend from the final stack layer"),
            ("unpublished", None, None, None, True, "c" * 40,
             "report", "is not published on evidence_remote at its recorded commit"),
            ("report", None, None, None, True, "a" * 40,
             "unsubstantiated", "report does not substantiate"),
            ("build status", ("builds", 0, "status"), "failed", None, True, "a" * 40,
             "report", "build 1 did not pass"),
            ("digest", ("builds", 0, "sha256"), "not-a-digest", None, True, "a" * 40,
             "report", "build 1 requires a SHA-256 digest"),
            ("flag", ("partial_merge_safety", "feature_flag", "safe_default"), "enabled",
             None, True, "a" * 40, "report", "feature flag must default to disabled"),
        )
        complete_report = (
            "# Stacked Validation Evidence\n\n"
            "- Test command: `uv run pytest`\n"
            "- Final tests: 42 passed, 1 skipped, 0 warnings\n"
            "- Prefix coverage: 2/2\n"
            "- Feature flag: `FEATURE_X_ENABLED`; default: `disabled`\n\n"
            "- Disabled behavior: the disabled path does not initialize the feature\n\n"
            "| Target | Tip | Result | Tests |\n"
            "| --- | --- | --- | --- |\n"
            "| `one` | `first` | passed | 42 passed, 1 skipped, 0 warnings |\n"
            "| `two` | `final` | passed | 42 passed, 1 skipped, 0 warnings |\n\n"
            "| Artifact | Result | SHA-256 |\n"
            "| --- | --- | --- |\n"
            f"| `feature_x-1.0.0-py3-none-any.whl` | passed | `{'b' * 64}` |\n"
        )
        for name, path, value, resolutions, ancestor, published, report, message in cases:
            with self.subTest(name=name):
                evidence = json.loads(json.dumps({
                    key: item for key, item in self.evidence.items()
                    if not key.startswith("_")
                }))
                if path:
                    target = evidence
                    for component in path[:-1]:
                        target = target[component]
                    target[path[-1]] = value
                manifest = {
                    "repository": "example.test/owner/repo",
                    "publish_remote": "origin",
                    "evidence_remote": "origin",
                    "_head_repository": "example.test/contributor/repo",
                    "integration_evidence": evidence,
                }
                resolve = mock.patch.object(
                    MODULE, "resolve",
                    side_effect=resolutions,
                    return_value="a" * 40,
                )
                with resolve, mock.patch.object(
                    MODULE, "is_ancestor", return_value=ancestor
                ), mock.patch.object(
                    MODULE, "remote_sha", return_value=published
                ), mock.patch.object(
                    MODULE, "git",
                    return_value=complete_report if report == "report" else report,
                ), self.assertRaisesRegex(MODULE.SubmitError, message):
                    MODULE.validate_integration_evidence(
                        Path.cwd(),
                        manifest,
                        [
                            {"_tip": "first", "remote_branch": "one"},
                            {"_tip": "final", "remote_branch": "two"},
                        ],
                    )

    def test_integration_evidence_checks_each_report_contract_independently(self) -> None:
        evidence = {key: value for key, value in self.evidence.items() if not key.startswith("_")}
        manifest = {
            "repository": "example.test/owner/repo",
            "publish_remote": "origin",
            "evidence_remote": "origin",
            "_head_repository": "example.test/contributor/repo",
            "integration_evidence": evidence,
        }
        report = (
            "- Test command: `uv run pytest`\n"
            "- Final tests: 42 passed, 1 skipped, 0 warnings\n"
            "- Prefix coverage: 2/2\n"
            "- Feature flag: `FEATURE_X_ENABLED`; default: `disabled`\n"
            "- Disabled behavior: the disabled path does not initialize the feature\n"
            "| `one` | `first` | passed | 42 passed, 1 skipped, 0 warnings |\n"
            "| `two` | `final` | passed | 42 passed, 1 skipped, 0 warnings |\n"
            f"| `feature_x-1.0.0-py3-none-any.whl` | passed | `{'b' * 64}` |\n"
        )
        cases = (
            ("token bag", " ".join((evidence["test_command"], "42 passed, 1 skipped, 0 warnings", "2/2",
                                    "FEATURE_X_ENABLED", "first", "final", "feature_x-1.0.0-py3-none-any.whl", "b" * 64))),
            ("command", report.replace("`uv run pytest`", "`other`", 1)),
            ("tests", report.replace("- Final tests:", "- Other tests:", 1)),
            ("coverage", report.replace("- Prefix coverage:", "- Other coverage:", 1)),
            ("flag", report.replace("- Feature flag:", "- Other flag:", 1)),
            ("summary", report.replace("- Disabled behavior:", "- Other behavior:", 1)),
            ("prefix", report.replace("| `one` | `first` | passed | 42 passed, 1 skipped, 0 warnings |\n", "")),
            ("later prefix", report.replace("| `two` | `final` | passed | 42 passed, 1 skipped, 0 warnings |\n", "")),
            ("build", report.replace(f"| `feature_x-1.0.0-py3-none-any.whl` | passed | `{'b' * 64}` |\n", "")),
            ("final contradiction", report + "- Final tests: 1 failed\n"),
            ("contradiction", report + "| `one` | `first` | passed | 41 passed, 1 failed |\n"),
        )
        for name, invalid_report in cases:
            with self.subTest(name=name), mock.patch.object(MODULE, "resolve", return_value="a" * 40), \
                    mock.patch.object(MODULE, "is_ancestor", return_value=True), \
                    mock.patch.object(MODULE, "remote_sha", return_value="a" * 40), \
                    mock.patch.object(MODULE, "git", return_value=invalid_report), \
                    self.assertRaisesRegex(MODULE.SubmitError, "does not substantiate"):
                MODULE.validate_integration_evidence(
                    Path.cwd(), manifest, [{"_tip": "first", "remote_branch": "one"},
                                           {"_tip": "final", "remote_branch": "two"}],
                )

    def test_verify_pull_request_rejects_incomplete_expected_evidence_section(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            body_file = Path(temporary) / "body.md"
            body_file.write_text("## Summary\n\nFocused change.\n", encoding="utf-8")
            layers = [
                dict(layer, _tip=str(index + 1) * 40, _body_file=body_file)
                for index, layer in enumerate(self.layers)
            ]
            links = {
                0: {"number": 41, "url": "https://example.test/pull/41"},
                1: {"number": 42, "url": "https://example.test/pull/42"},
            }
            manifest = {
                "repository": "example.test/owner/repo",
                "default_base": "main",
                "feature_name": "Feature X",
                "feature_summary": "Focused change.",
                "stack_label": "feature-x",
                "_head_owner": "contributor",
                "_evidence_repository": "example.test/contributor/repo",
                "integration_evidence": self.evidence,
            }
            expected = MODULE.render_body(
                layers, links, 1, "main", "Focused change.", "feature-x",
                self.evidence, "contributor", "Feature X"
            )
            marker = "\n\n## Stack validation and partial-merge safety"
            navigation = "\n\n" + MODULE.MARKER
            before, remainder = expected.split(marker, 1)
            _, after = remainder.split(navigation, 1)
            bodies = {
                "removed": before + navigation + after,
                "missing report link": expected.replace(
                    self.evidence["_report_url"],
                    "https://example.test/changed-report",
                    1,
                ),
            }
            for name, body in bodies.items():
                with self.subTest(name=name), mock.patch.object(
                    MODULE,
                    "gh_api",
                    return_value=json.dumps({
                        "state": "open", "draft": True,
                        "title": MODULE.stacked_title(layers[1], 1, 2, "feature-x"),
                        "base": {"ref": "main", "sha": layers[0]["_tip"]},
                        "head": {
                            "ref": layers[1]["remote_branch"],
                            "sha": layers[1]["_tip"],
                            "repo": {"owner": {"login": "contributor"}},
                        },
                        "body": body,
                    }),
                ), self.assertRaisesRegex(
                    MODULE.SubmitError, "PR evidence is incomplete"
                ):
                    MODULE.verify_pull_request(
                        Path.cwd(), manifest, layers, links, 1, layers[1], "main",
                        {"number": 42}, True, body
                    )

    @mock.patch.object(MODULE, "gh_api")
    def test_verify_pull_request_updates_stale_title_and_body_in_place(
        self,
        api_mock: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            body_file = Path(temporary) / "body.md"
            body_file.write_text("## Summary\n\nFocused change.\n", encoding="utf-8")
            layers = [
                dict(layer, _tip=str(index + 1) * 40, _body_file=body_file)
                for index, layer in enumerate(self.layers)
            ]
            links = {
                0: {"number": 41, "url": "https://example.test/pull/41"},
                1: {"number": 42, "url": "https://example.test/pull/42"},
            }
            manifest = {
                "repository": "example.test/owner/repo",
                "_head_owner": "contributor",
                "default_base": "main",
                "base_sha": "0" * 40,
                "stack_label": "feature-x",
                "feature_name": "Feature X",
                "feature_summary": "Focused change.",
                "integration_evidence": self.evidence,
                "draft": False,
            }
            expected = MODULE.render_body(
                layers,
                links,
                1,
                "main",
                "Focused change.",
                "feature-x",
                self.evidence,
                "contributor",
                "Feature X",
            )
            api_mock.side_effect = [
                json.dumps(
                    {
                        "state": "open",
                        "draft": True,
                        "title": "stale title",
                        "base": {"ref": "main", "sha": layers[0]["_tip"]},
                        "head": {
                            "ref": layers[1]["remote_branch"],
                            "sha": layers[1]["_tip"],
                            "repo": {"owner": {"login": "contributor"}},
                        },
                        "body": "stale body",
                    }
                ),
                "",
            ]

            MODULE.verify_pull_request(
                Path.cwd(),
                manifest,
                layers,
                links,
                1,
                layers[1],
                "main",
                {"number": 42},
                True,
                expected,
                allow_editorial_sync=True,
            )

        self.assertEqual(len(api_mock.call_args_list), 2)
        patch_call = api_mock.call_args_list[1]
        self.assertEqual(
            patch_call.args[:3],
            (
                Path.cwd(),
                "example.test/owner/repo",
                "repos/owner/repo/pulls/42",
            ),
        )
        self.assertEqual(
            patch_call.args[3:],
            ("--method", "PATCH", "-f", f"title={MODULE.stacked_title(layers[1], 1, 2, 'feature-x')}", "-f", f"body={expected}"),
        )

    @mock.patch.object(MODULE, "gh_api")
    def test_verify_pull_request_accepts_already_ready_pr_and_refreshes_editorial_content(
        self,
        api_mock: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            body_file = Path(temporary) / "body.md"
            body_file.write_text("## Summary\n\nFocused change.\n", encoding="utf-8")
            layers = [
                dict(layer, _tip=str(index + 1) * 40, _body_file=body_file)
                for index, layer in enumerate(self.layers)
            ]
            links = {0: {"number": 41, "url": "https://example.test/pull/41"}}
            manifest = {
                "repository": "example.test/owner/repo",
                "_head_owner": "contributor",
                "default_base": "main",
                "base_sha": "0" * 40,
                "stack_label": "feature-x",
                "feature_name": "Feature X",
                "feature_summary": "Focused change.",
                "integration_evidence": self.evidence,
                "draft": False,
            }
            expected = MODULE.render_body(
                layers,
                links,
                0,
                "main",
                "Focused change.",
                "feature-x",
                self.evidence,
                "contributor",
                "Feature X",
            )
            api_mock.side_effect = [
                json.dumps(
                    {
                        "state": "open",
                        "draft": False,
                        "title": "old ready title",
                        "base": {"ref": "main", "sha": "0" * 40},
                        "head": {
                            "ref": layers[0]["remote_branch"],
                            "sha": layers[0]["_tip"],
                            "repo": {"owner": {"login": "contributor"}},
                        },
                        "body": "old ready body",
                    }
                ),
                "",
            ]

            MODULE.verify_pull_request(
                Path.cwd(),
                manifest,
                layers,
                links,
                0,
                layers[0],
                "main",
                {"number": 41},
                True,
                expected,
                allow_editorial_sync=True,
            )

        self.assertEqual(len(api_mock.call_args_list), 2)

    @mock.patch.object(MODULE, "gh_api")
    def test_verify_pull_request_still_rejects_structural_mismatch(self, api_mock: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            body_file = Path(temporary) / "body.md"
            body_file.write_text("## Summary\n\nFocused change.\n", encoding="utf-8")
            layers = [
                dict(layer, _tip=str(index + 1) * 40, _body_file=body_file)
                for index, layer in enumerate(self.layers)
            ]
            manifest = {
                "repository": "example.test/owner/repo",
                "_head_owner": "contributor",
                "default_base": "main",
                "base_sha": "0" * 40,
                "stack_label": "feature-x",
                "feature_name": "Feature X",
                "feature_summary": "Focused change.",
                "integration_evidence": self.evidence,
                "draft": False,
            }
            expected = MODULE.render_body(
                layers,
                {},
                0,
                "main",
                "Focused change.",
                "feature-x",
                self.evidence,
                "contributor",
                "Feature X",
            )
            cases = (
                (
                    "head sha",
                    {
                        "state": "open",
                        "draft": True,
                        "title": MODULE.stacked_title(layers[0], 0, 2, "feature-x"),
                        "base": {"ref": "main", "sha": "0" * 40},
                        "head": {
                            "ref": layers[0]["remote_branch"],
                            "sha": "9" * 40,
                            "repo": {"owner": {"login": "contributor"}},
                        },
                        "body": expected,
                    },
                    "headRefOid",
                ),
                (
                    "base",
                    {
                        "state": "open",
                        "draft": True,
                        "title": MODULE.stacked_title(layers[0], 0, 2, "feature-x"),
                        "base": {"ref": "release", "sha": "0" * 40},
                        "head": {
                            "ref": layers[0]["remote_branch"],
                            "sha": layers[0]["_tip"],
                            "repo": {"owner": {"login": "contributor"}},
                        },
                        "body": expected,
                    },
                    "baseRefName",
                ),
                (
                    "owner",
                    {
                        "state": "open",
                        "draft": True,
                        "title": MODULE.stacked_title(layers[0], 0, 2, "feature-x"),
                        "base": {"ref": "main", "sha": "0" * 40},
                        "head": {
                            "ref": layers[0]["remote_branch"],
                            "sha": layers[0]["_tip"],
                            "repo": {"owner": {"login": "fork-owner"}},
                        },
                        "body": expected,
                    },
                    "headRepositoryOwner",
                ),
            )
            for name, payload, field in cases:
                with self.subTest(name=name):
                    api_mock.reset_mock()
                    api_mock.return_value = json.dumps(payload)
                    with self.assertRaisesRegex(
                        MODULE.SubmitError, f"submitted PR state mismatch.*{field}"
                    ):
                        MODULE.verify_pull_request(
                            Path.cwd(),
                            manifest,
                            layers,
                            {},
                            0,
                            layers[0],
                            "main",
                            {"number": 41},
                            True,
                            expected,
                        )
                    self.assertEqual(api_mock.call_count, 1)

    @mock.patch.object(MODULE, "gh_api")
    def test_verify_integration_pull_request_updates_stale_title_and_body_in_place(
        self,
        api_mock: mock.Mock,
    ) -> None:
        manifest = {
            "repository": "example.test/owner/repo",
            "default_base": "main",
            "base_sha": "0" * 40,
            "feature_name": "Feature X",
            "stack_label": "feature-x",
            "_evidence_owner": "contributor",
            "integration_evidence": self.evidence,
        }
        manifest["_integration_layer"] = MODULE.integration_layer(
            {
                **manifest,
                "integration_evidence": {
                    **self.evidence,
                    "branch": self.evidence["branch"],
                    "_commit": self.evidence["_commit"],
                },
            }
        )
        layers = [
            {"title": "docs: plan feature", "summary": "Feature plan.", "remote_branch": "stack/plan-pr-ready", "_tip": "1" * 40},
            {"title": "feat: implement feature", "summary": "Implementation.", "remote_branch": "stack/story-pr-ready", "_tip": "2" * 40},
        ]
        expected = MODULE.render_integration_body(
            manifest,
            layers,
            {0: {"number": 41, "url": "https://example.test/pull/41"}},
        )
        api_mock.side_effect = [
            json.dumps(
                {
                    "state": "open",
                    "draft": True,
                    "title": "stale integration title",
                    "base": {"ref": "main", "sha": "0" * 40},
                    "head": {
                        "ref": manifest["_integration_layer"]["remote_branch"],
                        "sha": manifest["_integration_layer"]["_tip"],
                        "repo": {"owner": {"login": "contributor"}},
                    },
                    "body": "stale integration body",
                }
            ),
            "",
        ]

        MODULE.verify_integration_pull_request(
            Path.cwd(),
            manifest,
            layers,
            {0: {"number": 41, "url": "https://example.test/pull/41"}},
            {"number": 90},
            expected,
            allow_editorial_sync=True,
        )

        self.assertEqual(len(api_mock.call_args_list), 2)

    def test_pr_111_fallback_headings_are_exact_and_ordered(self) -> None:
        template = Path(
            MODULE.__file__
        ).parents[1] / "references" / "pr-111-fallback-template.md"
        content = template.read_text(encoding="utf-8")
        MODULE.validate_fallback_body(content, label="fallback")
        with self.assertRaisesRegex(MODULE.SubmitError, "exactly once"):
            MODULE.validate_fallback_body(
                content + "\n## Summary\n\nDuplicate.\n",
                label="fallback",
            )
        swapped = content.replace(
            "## Motivation and context", "## TEMP", 1
        ).replace("## Changes", "## Motivation and context", 1).replace(
            "## TEMP", "## Changes", 1
        )
        with self.assertRaisesRegex(MODULE.SubmitError, "order"):
            MODULE.validate_fallback_body(swapped, label="fallback")
        with self.assertRaisesRegex(MODULE.SubmitError, "exactly the seven"):
            MODULE.validate_fallback_body(
                content + "\n## Extra\n\nNot canonical.\n",
                label="fallback",
            )
        MODULE.validate_fallback_body(
            content + "\n## Generated appendix\n",
            label="rendered fallback",
            allow_appendix_headings=True,
        )
        fenced = "\n".join(f"```\n{heading}\n```" for heading in MODULE.FALLBACK_HEADINGS)
        with self.assertRaisesRegex(MODULE.SubmitError, "exactly once"):
            MODULE.validate_fallback_body(fenced, label="fallback")
        commented = "\n".join(
            f"<!-- {heading} -->" for heading in MODULE.FALLBACK_HEADINGS
        )
        with self.assertRaisesRegex(MODULE.SubmitError, "exactly once"):
            MODULE.validate_fallback_body(commented, label="fallback")
        missing_check = content.replace(
            "- [ ] The change is scoped to this PR and prerequisites are identified.\n",
            "",
        )
        with self.assertRaisesRegex(MODULE.SubmitError, "checklist item"):
            MODULE.validate_fallback_body(missing_check, label="fallback")
        with self.assertRaisesRegex(MODULE.SubmitError, "another Markdown heading style"):
            MODULE.validate_fallback_body(
                content + "\nSummary\n-------\n\nDuplicate.\n",
                label="fallback",
            )
        checklist = "\n".join(f"- [ ] {item}" for item in MODULE.FALLBACK_CHECKLIST_ITEMS)
        misplaced = content.replace(
            f"## Checklist\n\n{checklist}",
            f"{checklist}\n\n## Checklist",
        )
        with self.assertRaisesRegex(MODULE.SubmitError, "under '## Checklist'"):
            MODULE.validate_fallback_body(misplaced, label="fallback")

    def test_stacked_title_inserts_position_after_conventional_prefix(self) -> None:
        self.assertEqual(
            MODULE.stacked_title(
                {"title": "feat(observability): add tracing"},
                0,
                16,
                "arize-ax",
            ),
            "feat(observability)(stacked-pr: arize-ax [1/16]): add tracing",
        )

    def test_remote_url_parsing_supports_ssh_and_https(self) -> None:
        self.assertEqual(
            MODULE.parse_remote("git@github.example.com:owner/repo.git"),
            ("github.example.com", "owner", "repo"),
        )
        self.assertTrue(
            MODULE.is_transient_failure('Post "https://api.github.example/graphql": EOF')
        )

    @mock.patch.object(MODULE, "gh_api")
    def test_head_lookup_exposes_wrong_owner_for_fail_closed_preflight(
        self,
        api_mock: mock.Mock,
    ) -> None:
        api_mock.return_value = json.dumps(
            [
                {
                    "number": 41,
                    "html_url": "https://example.test/owner/repo/pull/41",
                    "state": "open",
                    "draft": True,
                    "title": "Example",
                    "body": "Body",
                    "created_at": "2026-07-22T16:00:00Z",
                    "base": {"ref": "main", "sha": "c" * 40},
                    "head": {
                        "ref": "stack/story-pr-ready",
                        "sha": "a" * 40,
                        "repo": {"owner": {"login": "contributor"}},
                    },
                }
            ]
        )

        pulls = MODULE.pull_requests_for_head(
            Path.cwd(),
            "example.test/owner/repo",
            "contributor",
            "stack/story-pr-ready",
        )

        self.assertEqual(pulls[0]["headRepositoryOwner"], "contributor")
        self.assertEqual(
            api_mock.call_args.args[2],
            "repos/owner/repo/pulls?state=all&head=contributor%3Astack%2Fstory-pr-ready&per_page=100",
        )

    @mock.patch.object(MODULE, "git")
    def test_remote_validation_rejects_mismatched_effective_pushurl(
        self,
        git_mock: mock.Mock,
    ) -> None:
        git_mock.side_effect = [
            "https://example.test/upstream/repo.git",
            "https://example.test/someone-else/repo.git",
        ]

        with self.assertRaisesRegex(MODULE.SubmitError, "push URLs.*declared repository"):
            MODULE.validate_remote_urls(
                Path.cwd(),
                "upstream",
                "example.test/upstream/repo",
            )

    @mock.patch.object(MODULE.time, "sleep")
    @mock.patch.object(MODULE.subprocess, "run")
    def test_transient_commands_retry_with_backoff(
        self,
        run_mock: mock.Mock,
        sleep_mock: mock.Mock,
    ) -> None:
        run_mock.side_effect = [
            MODULE.subprocess.CompletedProcess([], 1, "", "Failed to connect to host"),
            MODULE.subprocess.CompletedProcess([], 0, "reachable\n", ""),
        ]

        result = MODULE.run(["gh", "api", "rate_limit"], Path.cwd(), retry_transient=True)

        self.assertEqual(result, "reachable")
        self.assertEqual(run_mock.call_count, 2)
        sleep_mock.assert_called_once_with(2)

    @mock.patch.object(MODULE.time, "sleep")
    @mock.patch.object(MODULE.subprocess, "run")
    def test_non_transient_commands_fail_without_retry(
        self,
        run_mock: mock.Mock,
        sleep_mock: mock.Mock,
    ) -> None:
        run_mock.return_value = MODULE.subprocess.CompletedProcess([], 1, "", "HTTP 403: Forbidden")

        with self.assertRaisesRegex(MODULE.SubmitError, "403"):
            MODULE.run(["gh", "api", "repo"], Path.cwd(), retry_transient=True)

        run_mock.assert_called_once()
        sleep_mock.assert_not_called()

    @mock.patch.object(MODULE, "retry_delay")
    @mock.patch.object(MODULE, "git")
    @mock.patch.object(MODULE, "remote_sha")
    def test_publish_retries_when_lease_is_unchanged(
        self,
        remote_sha_mock: mock.Mock,
        git_mock: mock.Mock,
        retry_delay_mock: mock.Mock,
    ) -> None:
        remote_sha_mock.side_effect = [None, None, "new-tip"]
        git_mock.side_effect = [MODULE.SubmitError("Failed to connect"), ""]

        MODULE.publish(
            Path.cwd(),
            {"publish_remote": "origin"},
            {"remote_branch": "stack/story-pr-ready", "_tip": "new-tip"},
        )

        self.assertEqual(git_mock.call_count, 2)
        retry_delay_mock.assert_called_once_with(1, "git push")

    @mock.patch.object(MODULE, "retry_delay")
    @mock.patch.object(MODULE, "git")
    @mock.patch.object(MODULE, "remote_sha")
    def test_publish_refuses_retry_after_remote_race(
        self,
        remote_sha_mock: mock.Mock,
        git_mock: mock.Mock,
        retry_delay_mock: mock.Mock,
    ) -> None:
        remote_sha_mock.side_effect = [None, "someone-elses-tip"]
        git_mock.side_effect = MODULE.SubmitError("Failed to connect")

        with self.assertRaisesRegex(MODULE.SubmitError, "remote branch changed"):
            MODULE.publish(
                Path.cwd(),
                {"publish_remote": "origin"},
                {"remote_branch": "stack/story-pr-ready", "_tip": "new-tip"},
            )

        retry_delay_mock.assert_not_called()

    @mock.patch.object(MODULE, "remote_sha", return_value="unapproved-tip")
    def test_publish_refuses_to_replace_drifted_head(
        self,
        remote_sha_mock: mock.Mock,
    ) -> None:
        with self.assertRaisesRegex(MODULE.SubmitError, "refusing to replace"):
            MODULE.publish(
                Path.cwd(),
                {"publish_remote": "upstream"},
                {"remote_branch": "stack/story-pr-ready", "_tip": "approved-tip"},
            )
        remote_sha_mock.assert_called_once()

    @mock.patch.object(
        MODULE,
        "gh",
        return_value="https://example.test/owner/repo/pull/41",
    )
    def test_create_uses_upstream_head_and_stages_draft(
        self,
        gh_mock: mock.Mock,
    ) -> None:
        pr = MODULE.create_pull_request(
            Path.cwd(),
            {
                "repository": "example.test/owner/repo",
                "draft": False,
                "_head_owner": "upstream",
            },
            {
                "remote_branch": "stack/story-pr-ready",
                "_head_ref": "stack/story-pr-ready",
                "title": "feat: story",
            },
            "main",
            "body.md",
            "feat(stacked-pr: feature-x [2/2]): story",
        )

        arguments = gh_mock.call_args.args
        self.assertIn("main", arguments)
        self.assertIn("stack/story-pr-ready", arguments)
        self.assertIn("--draft", arguments)
        self.assertEqual(pr["number"], 41)

    @mock.patch.object(MODULE, "gh")
    def test_create_fails_closed_on_ambiguous_transport_failure(
        self,
        gh_mock: mock.Mock,
    ) -> None:
        gh_mock.side_effect = MODULE.SubmitError('Post "https://api.example/graphql": EOF')
        with self.assertRaisesRegex(MODULE.SubmitError, "EOF"):
            MODULE.create_pull_request(
                Path.cwd(),
                {
                    "repository": "example.test/owner/repo",
                    "draft": False,
                    "_head_owner": "upstream",
                },
                {
                    "remote_branch": "stack/story-pr-ready",
                    "_head_ref": "stack/story-pr-ready",
                    "title": "feat: story",
                },
                "main",
                "body.md",
                "feat(stacked-pr: feature-x [2/2]): story",
            )

    @mock.patch.object(MODULE, "gh", return_value="created")
    def test_create_fails_closed_on_malformed_success_output(
        self,
        gh_mock: mock.Mock,
    ) -> None:
        with self.assertRaisesRegex(MODULE.SubmitError, "ambiguous PR creation output"):
            MODULE.create_pull_request(
                Path.cwd(),
                {"repository": "example.test/owner/repo"},
                {
                    "remote_branch": "stack/story-pr-ready",
                    "_head_ref": "stack/story-pr-ready",
                },
                "main",
                "/tmp/body.md",
                "feat(stacked-pr: feature-x [2/2]): story",
            )

    @mock.patch.object(MODULE, "gh_api")
    def test_pull_request_lookup_rejects_deleted_head_repository(
        self,
        gh_api_mock: mock.Mock,
    ) -> None:
        gh_api_mock.return_value = json.dumps(
            [[{"head": {"ref": "stack/story-pr-ready", "repo": None}}]]
        )
        with self.assertRaisesRegex(MODULE.SubmitError, "head repository is unavailable"):
            MODULE.pull_requests_for_head(
                Path.cwd(),
                "example.test/owner/repo",
                "owner",
                "stack/story-pr-ready",
            )

    def test_manual_instructions_include_order_files_and_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            rendered = MODULE.render_manual_instructions(
                {
                    "repository": "github.example.com/owner/repo",
                    "default_base": "main",
                    "_head_repository": "github.example.com/upstream/repo",
                    "_head_owner": "upstream",
                    "_integration_layer": {
                        "_head_ref": "integration/feature-x"
                    },
                    "feature_summary": "Adds focused behavior.",
                    "stack_label": "feature-x",
                },
                self.layers,
                {0: {"number": 41, "url": "https://github.example.com/owner/repo/pull/41"}},
                directory / "manifest.json",
                directory,
                directory / "manual-links.json",
                directory / "journal.json",
            )
        self.assertIn("01-title.txt", rendered)
        self.assertIn("02-body.md", rendered)
        self.assertIn("stack/story-pr-ready", rendered)
        self.assertIn("[#41](https://github.example.com/owner/repo/pull/41)", rendered)
        self.assertIn("Pending", rendered)
        self.assertEqual(rendered.count('--base "main"'), 0)
        self.assertIn('--base "stack/plan-pr-ready"', rendered)
        self.assertIn('--head "stack/story-pr-ready"', rendered)
        self.assertIn(
            "/compare/stack/plan-pr-ready...stack/story-pr-ready?expand=1",
            rendered,
        )
        self.assertIn("integration-title.txt", rendered)
        self.assertIn("integration-body.md", rendered)
        self.assertIn(
            'gh pr comment "https://github.example.com/owner/repo/pull/41"',
            rendered,
        )
        self.assertIn('--body-file "01-navigation.md"', rendered)
        self.assertIn('--body-file "02-body.md" --draft', rendered)
        self.assertNotIn('--body-file "01-body.md" --draft', rendered)
        self.assertIn("Not ready.", rendered)
        self.assertNotIn('--body-file "integration-body.md" --draft', rendered)

    def test_manual_integration_command_requires_every_component_link(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            rendered = MODULE.render_manual_instructions(
                {
                    "repository": "github.example.com/owner/repo",
                    "default_base": "main",
                    "_head_repository": "github.example.com/upstream/repo",
                    "_head_owner": "upstream",
                    "_integration_layer": {
                        "_head_ref": "integration/feature-x"
                    },
                    "feature_summary": "Adds focused behavior.",
                    "stack_label": "feature-x",
                },
                self.layers,
                {
                    0: {
                        "number": 41,
                        "url": "https://github.example.com/owner/repo/pull/41",
                    },
                    1: {
                        "number": 42,
                        "url": "https://github.example.com/owner/repo/pull/42",
                    },
                },
                directory / "manifest.json",
                directory,
                directory / "manual-links.json",
                directory / "journal.json",
            )
        self.assertIn('--body-file "integration-body.md" --draft', rendered)
        self.assertNotIn("Not ready.", rendered)

    def test_manual_existing_integration_is_reused_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            component_links = {
                0: {
                    "number": 41,
                    "url": "https://github.example.com/owner/repo/pull/41",
                },
                1: {
                    "number": 42,
                    "url": "https://github.example.com/owner/repo/pull/42",
                },
            }
            rendered = MODULE.render_manual_instructions(
                {
                    "repository": "github.example.com/owner/repo",
                    "default_base": "main",
                    "draft": False,
                    "_head_repository": "github.example.com/upstream/repo",
                    "_head_owner": "upstream",
                    "_integration_layer": {
                        "_head_ref": "integration/feature-x"
                    },
                    "_existing_integration_pr": {
                        "number": 43,
                        "url": "https://github.example.com/owner/repo/pull/43",
                    },
                    "feature_summary": "Adds focused behavior.",
                    "stack_label": "feature-x",
                },
                self.layers,
                component_links,
                directory / "manifest.json",
                directory,
                directory / "manual-links.json",
                directory / "journal.json",
            )
        self.assertIn(
            "[#43](https://github.example.com/owner/repo/pull/43)", rendered
        )
        self.assertNotIn('--head "integration/feature-x"', rendered)
        self.assertNotIn("gh pr ready", rendered)
        self.assertIn("Do not recreate it", rendered)

    def test_reconcile_create_requires_exact_draft_head(self) -> None:
        layer = {
            "remote_branch": "stack/story-pr-ready",
            "_tip": "a" * 40,
            "_base_sha": "a" * 40,
        }
        manifest = {
            "repository": "example.test/owner/repo",
            "_head_owner": "upstream",
            "default_base": "main",
            "base_sha": "a" * 40,
        }
        conflicting = {
            "number": 41,
            "url": "https://example.test/pull/41",
            "state": "OPEN",
            "isDraft": False,
            "baseRefName": "main",
            "baseRefOid": "c" * 40,
            "headRefName": "stack/story-pr-ready",
            "headRefOid": "b" * 40,
            "headRepositoryOwner": "upstream",
        }
        with mock.patch.object(
            MODULE,
            "pull_requests_for_head",
            return_value=[conflicting],
        ):
            with self.assertRaisesRegex(MODULE.SubmitError, "ambiguous PR creation"):
                MODULE.reconcile_created_pull_request(
                    Path.cwd(),
                    manifest,
                    layer,
                    "main",
                )

    @mock.patch.object(MODULE, "remote_sha")
    @mock.patch.object(MODULE, "pull_requests_for_head")
    def test_reconcile_create_uses_immutable_predecessor_oid(
        self,
        pulls_mock: mock.Mock,
        remote_sha_mock: mock.Mock,
    ) -> None:
        predecessor = "1" * 40
        layer = {
            "remote_branch": "stack/story-pr-ready",
            "_tip": "2" * 40,
            "_base_sha": predecessor,
        }
        expected = {
            "number": 42,
            "url": "https://example.test/pull/42",
            "state": "OPEN",
            "isDraft": True,
            "baseRefName": "stack/plan-pr-ready",
            "baseRefOid": predecessor,
            "headRefName": "stack/story-pr-ready",
            "headRefOid": "2" * 40,
            "headRepositoryOwner": "upstream",
        }
        pulls_mock.return_value = [expected]
        manifest = {
            "repository": "example.test/owner/repo",
            "_head_owner": "upstream",
            "default_base": "main",
            "base_sha": "0" * 40,
        }

        reconciled = MODULE.reconcile_created_pull_request(
            Path.cwd(),
            manifest,
            layer,
            "stack/plan-pr-ready",
        )

        self.assertIs(reconciled, expected)
        remote_sha_mock.assert_not_called()

    @mock.patch.object(MODULE, "remote_sha")
    @mock.patch.object(MODULE, "pull_requests_for_head")
    def test_reconcile_ignores_superseded_pr_alongside_freshly_created_one(
        self,
        pulls_mock: mock.Mock,
        remote_sha_mock: mock.Mock,
    ) -> None:
        """After `gh pr create`, the closed superseded PR and the brand-new PR both exist for
        the same head; reconcile must filter out the former rather than flag "multiple PRs"."""
        predecessor = "1" * 40
        layer = {
            "remote_branch": "stack/story-pr-ready",
            "_tip": "2" * 40,
            "_base_sha": predecessor,
            "_superseded_prs": {33},
        }
        superseded = {
            "number": 33,
            "url": "https://example.test/pull/33",
            "state": "CLOSED",
            "isDraft": False,
            "baseRefName": "stack/plan-pr-ready",
            "baseRefOid": "z" * 40,
            "headRefName": "stack/story-pr-ready",
            "headRefOid": "9" * 40,
            "headRepositoryOwner": "upstream",
        }
        fresh = {
            "number": 42,
            "url": "https://example.test/pull/42",
            "state": "OPEN",
            "isDraft": True,
            "baseRefName": "stack/plan-pr-ready",
            "baseRefOid": predecessor,
            "headRefName": "stack/story-pr-ready",
            "headRefOid": "2" * 40,
            "headRepositoryOwner": "upstream",
        }
        pulls_mock.return_value = [superseded, fresh]
        manifest = {
            "repository": "example.test/owner/repo",
            "_head_owner": "upstream",
            "default_base": "main",
            "base_sha": "0" * 40,
        }

        reconciled = MODULE.reconcile_created_pull_request(
            Path.cwd(),
            manifest,
            layer,
            "stack/plan-pr-ready",
        )

        self.assertIs(reconciled, fresh)
        remote_sha_mock.assert_not_called()

    @mock.patch.object(MODULE, "validate_manual_links_live")
    @mock.patch.object(MODULE, "pull_requests_for_head")
    def test_manual_packaging_discovers_existing_prs_without_links(
        self,
        pulls_mock: mock.Mock,
        validate_mock: mock.Mock,
    ) -> None:
        pulls_mock.side_effect = [
            [
                {
                    "number": 41,
                    "url": "https://example.test/upstream/repo/pull/41",
                    "state": "OPEN",
                    "isDraft": True,
                    "baseRefName": "main",
                    "baseRefOid": "0" * 40,
                    "headRefOid": "1" * 40,
                    "headRepositoryOwner": "upstream",
                }
            ],
            [
                {
                    "number": 42,
                    "url": "https://example.test/upstream/repo/pull/42",
                    "state": "OPEN",
                    "isDraft": True,
                    "baseRefName": "stack/plan-pr-ready",
                    "baseRefOid": "1" * 40,
                    "headRefOid": "2" * 40,
                    "headRepositoryOwner": "upstream",
                }
            ],
        ]
        layers = [
            {
                "remote_branch": "stack/plan-pr-ready",
                "_tip": "1" * 40,
            },
            {
                "remote_branch": "stack/story-pr-ready",
                "_tip": "2" * 40,
            },
        ]
        manifest = {
            "repository": "example.test/upstream/repo",
            "_head_owner": "upstream",
            "default_base": "main",
            "base_sha": "0" * 40,
        }
        links: dict[int, dict[str, object]] = {}

        MODULE.discover_manual_links(Path.cwd(), manifest, layers, links)

        self.assertEqual([links[index]["number"] for index in range(2)], [41, 42])
        validate_mock.assert_called_once_with(Path.cwd(), manifest, layers, links)

    @mock.patch.object(MODULE, "pull_requests_for_head")
    def test_manual_packaging_fails_closed_on_conflicting_discovered_pr(
        self,
        pulls_mock: mock.Mock,
    ) -> None:
        pulls_mock.return_value = [
            {
                "number": 41,
                "url": "https://example.test/upstream/repo/pull/41",
                "state": "CLOSED",
                "isDraft": True,
                "baseRefName": "main",
                "baseRefOid": "0" * 40,
                "headRefOid": "1" * 40,
                "headRepositoryOwner": "upstream",
            }
        ]
        layers = [{"remote_branch": "stack/plan-pr-ready", "_tip": "1" * 40}]
        manifest = {
            "repository": "example.test/upstream/repo",
            "_head_owner": "upstream",
            "default_base": "main",
            "base_sha": "0" * 40,
        }

        with self.assertRaisesRegex(MODULE.SubmitError, "retarget/restack"):
            MODULE.discover_manual_links(
                Path.cwd(),
                manifest,
                layers,
                {},
            )

    def test_manual_links_use_one_based_positions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "links.json"
            path.write_text(
                json.dumps(
                    {
                        "prs": [
                            {
                                "position": 1,
                                "number": 41,
                                "url": "https://example.test/owner/repo/pull/41",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            links = MODULE.load_manual_links(path, 2, "example.test/owner/repo")
        self.assertEqual(
            links,
            {0: {"number": 41, "url": "https://example.test/owner/repo/pull/41"}},
        )
        self.assertEqual(
            MODULE.parse_remote("https://github.example.com/owner/repo.git"),
            ("github.example.com", "owner", "repo"),
        )

    def test_manual_links_reject_gapped_prerequisites(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "links.json"
            path.write_text(
                json.dumps(
                    {
                        "prs": [
                            {
                                "position": 2,
                                "number": 42,
                                "url": "https://example.test/owner/repo/pull/42",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MODULE.SubmitError, "contiguous prefix"):
                MODULE.load_manual_links(path, 2, "example.test/owner/repo")


if __name__ == "__main__":
    unittest.main()
