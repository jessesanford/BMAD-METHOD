#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "build_pr_ready_stack.py"
SPEC = importlib.util.spec_from_file_location("builder", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True).stdout.strip()


class BuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Test User")
        git(self.repo, "config", "user.email", "test@example.com")
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        self.upstream = self.repo / ".git" / "upstream.git"
        self.origin = self.repo / ".git" / "origin.git"
        git(self.repo, "init", "--bare", "-q", str(self.upstream))
        git(self.repo, "init", "--bare", "-q", str(self.origin))
        git(self.repo, "remote", "add", "upstream", str(self.upstream))
        git(self.repo, "remote", "add", "origin", str(self.origin))
        git(self.repo, "push", "-q", "upstream", f"{self.base}:refs/heads/main")
        git(self.repo, "push", "-q", "origin", f"{self.base}:refs/heads/main")
        self.run_dir = self.repo / ".git" / "bmad-test"
        self.run_dir.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def commit(self, message: str, files: dict[str, str]) -> str:
        for name, content in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", message)
        tip = git(self.repo, "rev-parse", "HEAD")
        branch = git(self.repo, "branch", "--show-current")
        if branch and branch != "main":
            git(self.repo, "push", "-q", "--force", "origin", f"{tip}:refs/heads/{branch}")
        return tip

    def test_squashes_excludes_and_overlays(self) -> None:
        git(self.repo, "switch", "-qc", "story")
        self.commit("implementation", {"src/feature.py": "one\n", "_bmad/state.md": "local\n"})
        tip = self.commit("fix", {"src/feature.py": "two\n"})
        overlay = self.run_dir / "plan.md"
        overlay.write_text("# Plan\n", encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "base": self.base,
            "base_remote": "upstream",
            "base_branch": "main",
            "source_remote": "origin",
            "remote": "upstream",
            "exclude_paths": ["_bmad/**"],
            "layers": [
                {
                    "source": "story",
                    "source_tip": tip,
                    "source_parent": self.base,
                    "target": "story-pr-ready",
                    "decision_summary": "one outcome",
                    "groups": [
                        {
                            "through": tip,
                            "message": "feat: clean feature",
                            "novelty_rationale": "fix completes implementation",
                            "overlays": [{"path": "docs/plan.md", "source": str(overlay)}],
                        }
                    ],
                }
            ],
        }
        path = self.run_dir / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        dry_run = MODULE.build(self.repo, path, apply=False, push=False)
        result = MODULE.build(self.repo, path, apply=True, push=False)
        target = result["layers"][0]["new_tip"]
        self.assertEqual(dry_run["layers"][0]["new_tip"], target)
        self.assertEqual(git(self.repo, "rev-list", "--count", f"{self.base}..{target}"), "1")
        self.assertEqual(git(self.repo, "show", f"{target}:src/feature.py"), "two")
        self.assertNotIn("_bmad/state.md", git(self.repo, "ls-tree", "-r", "--name-only", target))

    def test_preserves_independent_groups(self) -> None:
        git(self.repo, "switch", "-qc", "story")
        first = self.commit("schema", {"src/schema.py": "schema\n"})
        tip = self.commit("runtime", {"src/runtime.py": "runtime\n"})
        manifest = {
            "schema_version": 1,
            "base": self.base,
            "base_remote": "upstream",
            "base_branch": "main",
            "source_remote": "origin",
            "remote": "upstream",
            "exclude_paths": [],
            "layers": [
                {
                    "source": "story",
                    "source_tip": tip,
                    "source_parent": self.base,
                    "target": "story-pr-ready",
                    "decision_summary": "two boundaries",
                    "groups": [
                        {"through": first, "message": "feat: schema", "novelty_rationale": "public contract"},
                        {"through": tip, "message": "feat: runtime", "novelty_rationale": "runtime consumer"},
                    ],
                }
            ],
        }
        path = self.run_dir / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        result = MODULE.build(self.repo, path, apply=False, push=False)
        self.assertEqual(git(self.repo, "rev-list", "--count", f"{self.base}..{result['layers'][0]['new_tip']}"), "2")

    def test_supports_single_repository_role_topology(self) -> None:
        git(self.repo, "remote", "add", "single", str(self.upstream))
        git(self.repo, "switch", "-qc", "story")
        tip = self.commit("implementation", {"src/feature.py": "one\n"})
        git(self.repo, "push", "-q", "single", f"{tip}:refs/heads/story")
        manifest = {
            "schema_version": 1,
            "base": self.base,
            "base_remote": "single",
            "base_branch": "main",
            "source_remote": "single",
            "remote": "single",
            "exclude_paths": [],
            "layers": [
                {
                    "source": "story",
                    "source_tip": tip,
                    "source_parent": self.base,
                    "target": "story-pr-ready",
                    "decision_summary": "one outcome",
                    "groups": [
                        {
                            "through": tip,
                            "message": "feat: feature",
                            "novelty_rationale": "one layer",
                        }
                    ],
                }
            ],
        }
        path = self.run_dir / "single-repository-manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")

        result = MODULE.build(self.repo, path, apply=False, push=False)

        self.assertEqual(result["remote"], "single")
        self.assertEqual(result["layers"][0]["source_tip"], tip)

    def test_requires_canonical_upstream_remote(self) -> None:
        manifest = {
            "schema_version": 1,
            "base": self.base,
            "base_remote": "upstream",
            "base_branch": "main",
            "source_remote": "origin",
            "remote": "origin",
            "exclude_paths": [],
            "layers": [{}],
        }
        path = self.run_dir / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.BuildError, "canonical target repository"):
            MODULE.build(self.repo, path, apply=False, push=False)

    def test_rejects_source_branch_published_on_upstream(self) -> None:
        git(self.repo, "switch", "-qc", "story")
        tip = self.commit("implementation", {"src/feature.py": "one\n"})
        git(self.repo, "push", "-q", "upstream", f"{tip}:refs/heads/story")
        manifest = {
            "schema_version": 1,
            "base": self.base,
            "base_remote": "upstream",
            "base_branch": "main",
            "source_remote": "origin",
            "remote": "upstream",
            "exclude_paths": [],
            "layers": [
                {
                    "source": "story",
                    "source_tip": tip,
                    "source_parent": self.base,
                    "target": "story-pr-ready",
                    "decision_summary": "one outcome",
                    "groups": [
                        {
                            "through": tip,
                            "message": "feat: feature",
                            "novelty_rationale": "one layer",
                        }
                    ],
                }
            ],
        }
        with self.assertRaisesRegex(MODULE.BuildError, "must not be published on upstream"):
            MODULE.validate(self.repo, manifest)

    def test_pushes_immutable_tip_to_upstream(self) -> None:
        git(self.repo, "switch", "-qc", "story")
        tip = self.commit("implementation", {"src/feature.py": "one\n"})
        manifest = {
            "schema_version": 1,
            "base": self.base,
            "base_remote": "upstream",
            "base_branch": "main",
            "source_remote": "origin",
            "remote": "upstream",
            "exclude_paths": [],
            "layers": [
                {
                    "source": "story",
                    "source_tip": tip,
                    "source_parent": self.base,
                    "target": "story-pr-ready",
                    "decision_summary": "one outcome",
                    "groups": [
                        {
                            "through": tip,
                            "message": "feat: clean feature",
                            "novelty_rationale": "one review layer",
                        }
                    ],
                }
            ],
        }
        path = self.run_dir / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        result = MODULE.build(self.repo, path, apply=True, push=True)
        published = git(self.repo, "ls-remote", "--heads", "upstream", "story-pr-ready").split()[0]
        self.assertEqual(published, result["layers"][0]["new_tip"])
        self.assertEqual(git(self.repo, "ls-remote", "--heads", "origin", "story-pr-ready"), "")

    def test_rejects_pushurl_that_targets_another_repository(self) -> None:
        other = self.repo / ".git" / "other.git"
        git(self.repo, "init", "--bare", "-q", str(other))
        git(self.repo, "config", "--add", "remote.upstream.pushurl", str(other))
        git(self.repo, "switch", "-qc", "story")
        tip = self.commit("implementation", {"src/feature.py": "one\n"})
        manifest = {
            "schema_version": 1,
            "base": self.base,
            "base_remote": "upstream",
            "base_branch": "main",
            "source_remote": "origin",
            "remote": "upstream",
            "exclude_paths": [],
            "layers": [
                {
                    "source": "story",
                    "source_tip": tip,
                    "source_parent": self.base,
                    "target": "story-pr-ready",
                    "decision_summary": "one outcome",
                    "groups": [
                        {
                            "through": tip,
                            "message": "feat: clean feature",
                            "novelty_rationale": "one review layer",
                        }
                    ],
                }
            ],
        }
        path = self.run_dir / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(MODULE.BuildError, "push URLs.*one repository"):
            MODULE.build(self.repo, path, apply=True, push=True)
        self.assertEqual(git(self.repo, "ls-remote", "--heads", str(other)), "")

    def test_rejects_adjacent_layers_that_resolve_to_same_commit(self) -> None:
        git(self.repo, "switch", "-qc", "story")
        tip = self.commit("implementation", {"src/feature.py": "one\n"})
        git(self.repo, "branch", "story-empty", tip)
        git(self.repo, "push", "-q", "origin", f"{tip}:refs/heads/story-empty")
        manifest = {
            "schema_version": 1,
            "base": self.base,
            "base_remote": "upstream",
            "base_branch": "main",
            "source_remote": "origin",
            "remote": "upstream",
            "exclude_paths": [],
            "layers": [
                {
                    "source": "story",
                    "source_tip": tip,
                    "source_parent": self.base,
                    "target": "story-pr-ready",
                    "decision_summary": "implementation",
                    "groups": [
                        {
                            "through": tip,
                            "message": "feat: clean feature",
                            "novelty_rationale": "implementation",
                        }
                    ],
                },
                {
                    "source": "story-empty",
                    "source_tip": tip,
                    "source_parent": tip,
                    "target": "story-empty-pr-ready",
                    "decision_summary": "empty",
                    "groups": [
                        {
                            "through": tip,
                            "message": "feat: empty layer",
                            "novelty_rationale": "must be rejected",
                        }
                    ],
                },
            ],
        }
        path = self.run_dir / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(MODULE.BuildError, "empty component layer"):
            MODULE.build(self.repo, path, apply=False, push=False)

    @mock.patch.object(MODULE, "pull_requests_for_head")
    def test_refuses_to_replace_target_used_by_any_pr(self, pulls_mock: mock.Mock) -> None:
        git(self.repo, "push", "-q", "upstream", f"{self.base}:refs/heads/story-pr-ready")
        git(self.repo, "switch", "-qc", "story")
        tip = self.commit("implementation", {"src/feature.py": "one\n"})
        pulls_mock.return_value = [{"number": 41, "state": "closed"}]
        manifest = {
            "schema_version": 1,
            "base": self.base,
            "base_remote": "upstream",
            "base_branch": "main",
            "source_remote": "origin",
            "remote": "upstream",
            "exclude_paths": [],
            "layers": [
                {
                    "source": "story",
                    "source_tip": tip,
                    "source_parent": self.base,
                    "target": "story-pr-ready",
                    "decision_summary": "one outcome",
                    "groups": [
                        {
                            "through": tip,
                            "message": "feat: clean feature",
                            "novelty_rationale": "one review layer",
                        }
                    ],
                }
            ],
        }
        path = self.run_dir / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(MODULE.BuildError, "backup refs are prohibited"):
            MODULE.build(self.repo, path, apply=True, push=True)
        published = git(
            self.repo,
            "ls-remote",
            "--heads",
            "upstream",
            "story-pr-ready",
        ).split()[0]
        self.assertEqual(published, self.base)
        self.assertEqual(git(self.repo, "branch", "--list", "story-pr-ready"), "")


if __name__ == "__main__":
    unittest.main()
