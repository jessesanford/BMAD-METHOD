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
        return git(self.repo, "rev-parse", "HEAD")

    def test_squashes_excludes_and_overlays(self) -> None:
        git(self.repo, "switch", "-qc", "story")
        self.commit("implementation", {"src/feature.py": "one\n", "_bmad/state.md": "local\n"})
        tip = self.commit("fix", {"src/feature.py": "two\n"})
        overlay = self.run_dir / "plan.md"
        overlay.write_text("# Plan\n", encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "base": self.base,
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


if __name__ == "__main__":
    unittest.main()
