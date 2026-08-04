#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///

"""Guards the branch precondition on every path that writes code.

`bmad-build` can reach implementation through `step-03-implement.md` or through
`step-oneshot.md`. A guardrail present on only one of them is the failure mode this
suite exists to catch: the uncovered path silently commits to a shared ref such as
`main`, `prep/stack-working`, or an already-published stack layer.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).parents[2]
SHIP_ROOT = SKILL_ROOT.parent

WRITE_PATHS = (
    SKILL_ROOT / "step-03-implement.md",
    SKILL_ROOT / "step-oneshot.md",
    SHIP_ROOT / "bmad-build-auto" / "step-03-implement.md",
)


class BranchGuardrailTests(unittest.TestCase):
    def test_every_implementation_path_declares_the_branch_precondition(self) -> None:
        for path in WRITE_PATHS:
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file(), f"{path} is missing")
                text = path.read_text(encoding="utf-8")
                self.assertIn("### Branch precondition", text)
                self.assertIn("Before writing any file", text)

    def test_shared_refs_are_named_as_unwritable(self) -> None:
        for path in WRITE_PATHS:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                for ref in ("main", "prep/stack-working", "-pr-ready", "integration/"):
                    self.assertIn(ref, text, f"{path.name} must name {ref} as a shared ref")

    def test_guardrail_forbids_rewriting_the_shared_branch(self) -> None:
        for path in WRITE_PATHS:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertRegex(
                    text,
                    r"[Nn]ever (?:reset, rebase, or force-push|solve this by resetting)",
                    f"{path.name} must forbid rewriting the shared branch",
                )


if __name__ == "__main__":
    unittest.main()
