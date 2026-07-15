#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///

from __future__ import annotations

import importlib.util
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
        self.assertEqual(
            MODULE.parse_remote("https://github.example.com/owner/repo.git"),
            ("github.example.com", "owner", "repo"),
        )


if __name__ == "__main__":
    unittest.main()
