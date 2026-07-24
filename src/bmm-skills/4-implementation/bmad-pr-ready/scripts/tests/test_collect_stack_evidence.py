import argparse
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "collect_stack_evidence.py"
SPEC = importlib.util.spec_from_file_location("collector", SCRIPT)
assert SPEC and SPEC.loader
COLLECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COLLECTOR)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()


class CollectStackEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Evidence Tester")
        git(self.repo, "config", "user.email", "evidence@example.com")
        (self.repo / "old.txt").write_text("original\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def commit(self, branch: str, subject: str, body: str, filename: str) -> str:
        git(self.repo, "switch", "-q", "-C", branch, self.base)
        (self.repo / filename).write_text(f"{branch}\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", subject, "-m", body)
        return git(self.repo, "rev-parse", "HEAD")

    def test_schema_tips_metadata_and_rename_status_are_stable(self) -> None:
        git(self.repo, "switch", "-q", "-c", "rename-layer")
        git(self.repo, "mv", "old.txt", "new.txt")
        git(self.repo, "commit", "-qm", "rename file", "-m", "Preserve provenance.")
        tip = git(self.repo, "rev-parse", "HEAD")

        payload = COLLECTOR.collect(
            self.repo, self.base, [("rename-layer", self.base)]
        )

        self.assertEqual(set(payload), {"schema_version", "base", "layers"})
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["base"], self.base)
        layer = payload["layers"][0]
        self.assertEqual(
            set(layer),
            {
                "source",
                "source_tip",
                "source_parent",
                "commits",
                "layer_files",
                "layer_shortstat",
            },
        )
        self.assertEqual(
            (layer["source"], layer["source_tip"], layer["source_parent"]),
            ("rename-layer", tip, self.base),
        )
        commit = layer["commits"][0]
        self.assertEqual(
            set(commit), {"sha", "subject", "body", "files", "shortstat"}
        )
        self.assertEqual(
            (commit["sha"], commit["subject"], commit["body"]),
            (tip, "rename file", "Preserve provenance."),
        )
        self.assertEqual(commit["files"], ["R100\told.txt\tnew.txt"])
        self.assertEqual(layer["layer_files"], ["R100\told.txt\tnew.txt"])

    def test_layers_use_independent_parent_boundaries(self) -> None:
        first = self.commit("one", "first layer", "", "one.txt")
        second = self.commit("two", "second layer", "", "two.txt")

        layers = COLLECTOR.collect(
            self.repo, self.base, [("one", self.base), ("two", self.base)]
        )["layers"]

        self.assertEqual([item["source_tip"] for item in layers], [first, second])
        self.assertEqual(
            [[commit["subject"] for commit in item["commits"]] for item in layers],
            [["first layer"], ["second layer"]],
        )
        self.assertEqual(layers[0]["layer_files"], ["A\tone.txt"])
        self.assertEqual(layers[1]["layer_files"], ["A\ttwo.txt"])

    def test_empty_range_has_explicit_empty_evidence(self) -> None:
        layer = COLLECTOR.collect(
            self.repo, self.base, [(self.base, self.base)]
        )["layers"][0]
        self.assertEqual(layer["commits"], [])
        self.assertEqual(layer["layer_files"], [])
        self.assertEqual(layer["layer_shortstat"], "")

    def test_invalid_layer_syntax_and_empty_components_are_rejected(self) -> None:
        for value, message in (
            ("branch", "SOURCE=PARENT"),
            ("=main", "non-empty SOURCE and PARENT"),
            ("branch=", "non-empty SOURCE and PARENT"),
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                argparse.ArgumentTypeError, message
            ):
                COLLECTOR.parse_layer(value)

    def test_invalid_refs_and_non_ancestor_ranges_are_rejected(self) -> None:
        for base, layers in (
            ("missing", []),
            (self.base, [("missing", self.base)]),
            (self.base, [(self.base, "missing")]),
        ):
            with self.subTest(base=base, layers=layers), self.assertRaises(RuntimeError):
                COLLECTOR.collect(self.repo, base, layers)
        self.commit("source", "source", "", "source.txt")
        self.commit("parent", "parent", "", "parent.txt")
        with self.assertRaisesRegex(
            RuntimeError, "parent is not an ancestor of source"
        ):
            COLLECTOR.collect(self.repo, self.base, [("source", "parent")])


if __name__ == "__main__":
    unittest.main()
