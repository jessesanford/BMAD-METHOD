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

    def test_partial_navigation_links_prior_and_marks_future(self) -> None:
        rendered = MODULE.render_navigation(
            self.layers,
            {0: {"number": 41, "url": "https://example.test/pull/41"}},
            0,
            "main",
        )
        self.assertIn("[#41](https://example.test/pull/41)", rendered)
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
        )
        self.assertIn("[#41](https://example.test/pull/41)", rendered)
        self.assertIn("[#42](https://example.test/pull/42)", rendered)
        self.assertNotIn("| Pending |", rendered)
        self.assertIn("**This PR:** 2 of 2", rendered)
        self.assertIn("| `release` |", rendered)

    def test_remote_url_parsing_supports_ssh_and_https(self) -> None:
        self.assertEqual(
            MODULE.parse_remote("git@github.example.com:owner/repo.git"),
            ("github.example.com", "owner", "repo"),
        )

    def test_manual_instructions_include_order_files_and_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            rendered = MODULE.render_manual_instructions(
                {
                    "repository": "github.example.com/owner/repo",
                    "default_base": "main",
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
