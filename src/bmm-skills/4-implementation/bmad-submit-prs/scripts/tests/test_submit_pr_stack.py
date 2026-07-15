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
            },
            {
                "title": "feat: implement feature",
                "summary": "Implementation.",
                "remote_branch": "stack/story-pr-ready",
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

    def test_partial_navigation_links_prior_and_marks_future(self) -> None:
        rendered = MODULE.render_navigation(
            self.layers,
            {0: {"number": 41, "url": "https://example.test/pull/41"}},
            0,
            "main",
            "feature-x",
        )
        self.assertIn("[#41](https://example.test/pull/41)", rendered)
        self.assertIn(
            "[docs(stacked-pr: feature-x [1/2]): plan feature](https://example.test/pull/41)",
            rendered,
        )
        self.assertIn("[stacked pull request](https://www.stacking.dev/)", rendered)
        self.assertIn("Pending", rendered)
        self.assertIn("L1 --> L2", rendered)
        self.assertIn("| `main` |", rendered)

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
        )
        self.assertIn("[#41](https://example.test/pull/41)", rendered)
        self.assertIn("[#42](https://example.test/pull/42)", rendered)
        self.assertNotIn("| Pending |", rendered)
        self.assertIn("**This PR:** 2 of 2", rendered)
        self.assertIn("| `release` |", rendered)

    def test_enterprise_environment_ignores_github_token(self) -> None:
        original = MODULE.COMMAND_ENV
        self.addCleanup(setattr, MODULE, "COMMAND_ENV", original)
        with mock.patch.dict(MODULE.os.environ, {"GH_TOKEN": "github-token"}):
            MODULE.configure_command_environment("github.example.com/owner/repo")
        self.assertNotIn("GH_TOKEN", MODULE.COMMAND_ENV)

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
            )
        self.assertIn("Adds opt-in tracing across the migration-agent fleet.", rendered)
        self.assertIn("[Planning PR #41](https://example.test/pull/41)", rendered)
        self.assertIn("[stacked pull request](https://www.stacking.dev/)", rendered)
        self.assertIn("All **2/2** cumulative stack prefixes", rendered)
        self.assertIn("FEATURE_X_ENABLED", rendered)
        self.assertIn("**42 passed, 1 skipped, 0 warnings**", rendered)
        self.assertIn("feature_x-1.0.0-py3-none-any.whl", rendered)

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
            "publish_remote": "origin",
            "integration_evidence": {
                key: value for key, value in self.evidence.items() if not key.startswith("_")
            },
        }
        resolve_mock.return_value = "a" * 40
        is_ancestor_mock.return_value = True
        remote_sha_mock.return_value = "a" * 40
        git_mock.return_value = (
            "uv run pytest\n42 passed, 1 skipped, 0 warnings\n2/2\n"
            "FEATURE_X_ENABLED\nfeature_x-1.0.0-py3-none-any.whl\n"
            f"{'b' * 64}\n"
        )

        MODULE.validate_integration_evidence(
            Path.cwd(),
            manifest,
            [{"_tip": "first"}, {"_tip": "final"}],
        )

        self.assertIn("_branch_url", manifest["integration_evidence"])
        self.assertIn("_report_url", manifest["integration_evidence"])
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
            "FEATURE_X_ENABLED\nfeature_x-1.0.0-py3-none-any.whl\n"
            + "b" * 64
        ),
    )
    @mock.patch.object(MODULE, "is_ancestor", return_value=True)
    @mock.patch.object(MODULE, "resolve", return_value="a" * 40)
    def test_integration_evidence_rejects_incomplete_prefix_coverage(
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
            "publish_remote": "origin",
            "integration_evidence": evidence,
        }

        with self.assertRaisesRegex(MODULE.SubmitError, "every submitted stack prefix"):
            MODULE.validate_integration_evidence(
                Path.cwd(),
                manifest,
                [{"_tip": "first"}, {"_tip": "final"}],
            )

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
        remote_sha_mock.side_effect = ["old-tip", "old-tip", "new-tip"]
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
        remote_sha_mock.side_effect = ["old-tip", "someone-elses-tip"]
        git_mock.side_effect = MODULE.SubmitError("Failed to connect")

        with self.assertRaisesRegex(MODULE.SubmitError, "remote branch changed"):
            MODULE.publish(
                Path.cwd(),
                {"publish_remote": "origin"},
                {"remote_branch": "stack/story-pr-ready", "_tip": "new-tip"},
            )

        retry_delay_mock.assert_not_called()

    @mock.patch.object(MODULE, "retry_delay")
    @mock.patch.object(MODULE, "reconcile_created_pull_request")
    @mock.patch.object(MODULE, "gh")
    def test_create_reconciles_ambiguous_transport_failure(
        self,
        gh_mock: mock.Mock,
        reconcile_mock: mock.Mock,
        retry_delay_mock: mock.Mock,
    ) -> None:
        gh_mock.side_effect = MODULE.SubmitError('Post "https://api.example/graphql": EOF')
        reconcile_mock.return_value = {
            "number": 41,
            "url": "https://example.test/pull/41",
            "state": "OPEN",
        }

        pr = MODULE.create_pull_request(
            Path.cwd(),
            {"repository": "example.test/owner/repo", "draft": False},
            {"remote_branch": "stack/story-pr-ready", "title": "feat: story"},
            "stack/plan-pr-ready",
            "/tmp/body.md",
            "feat(stacked-pr: feature-x [2/2]): story",
        )

        self.assertEqual(pr["number"], 41)
        reconcile_mock.assert_called_once()
        retry_delay_mock.assert_not_called()

    def test_manual_instructions_include_order_files_and_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            rendered = MODULE.render_manual_instructions(
                {
                    "repository": "github.example.com/owner/repo",
                    "default_base": "main",
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

    def test_manual_links_use_one_based_positions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "links.json"
            path.write_text(
                json.dumps(
                    {
                        "prs": [
                            {
                                "position": 2,
                                "number": 42,
                                "url": "https://example.test/pull/42",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            links = MODULE.load_manual_links(path, 2)
        self.assertEqual(links, {1: {"number": 42, "url": "https://example.test/pull/42"}})
        self.assertEqual(
            MODULE.parse_remote("https://github.example.com/owner/repo.git"),
            ("github.example.com", "owner", "repo"),
        )


if __name__ == "__main__":
    unittest.main()
