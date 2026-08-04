#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///

"""Guards cross-project pointer sync across every skill that moves a stack head.

While a stack is unmerged and unreleased, dependent repositories track it by
revision. Any skill that moves a head therefore invalidates those pointers. The
failure this suite prevents is a head-moving skill that stays silent about it:
the dependent keeps building against a commit that a cascade or extension has
already orphaned, and nothing surfaces the drift until a build resolves the wrong
code.

`bmad-stack-working-branch` owns the full contract; the head-moving skills must
each raise the obligation at the point they move the head.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SHIP_ROOT = Path(__file__).parents[3]
CONTRACT_SKILL = SHIP_ROOT / "bmad-stack-working-branch" / "SKILL.md"

HEAD_MOVING_SKILLS = (
    "bmad-rebase-cascade",
    "bmad-extend-pr-stack",
    "bmad-integration-review",
    "bmad-pr-ready",
    "bmad-submit-prs",
)


def skill_text(name: str) -> str:
    path = SHIP_ROOT / name / "SKILL.md"
    assert path.is_file(), f"{path} is missing"
    return path.read_text(encoding="utf-8")


class CrossProjectPointerTests(unittest.TestCase):
    def test_head_moving_skills_raise_the_pointer_obligation(self) -> None:
        for name in HEAD_MOVING_SKILLS:
            with self.subTest(skill=name):
                text = " ".join(skill_text(name).split())
                self.assertRegex(
                    text,
                    r"pins? this one by revision",
                    f"{name} must identify dependents that pin this repo by revision",
                )
                self.assertIn(
                    "bmad-stack-working-branch",
                    text,
                    f"{name} must defer to the pointer-sync contract",
                )

    def test_obligation_is_scoped_to_the_unmerged_unreleased_window(self) -> None:
        for name in HEAD_MOVING_SKILLS:
            with self.subTest(skill=name):
                text = " ".join(skill_text(name).split())
                self.assertRegex(
                    text,
                    r"unmerged and unreleased",
                    f"{name} must scope pointer sync to the pre-merge, pre-release window",
                )

    def test_contract_skill_defines_the_pointer_sync_step(self) -> None:
        text = " ".join(CONTRACT_SKILL.read_text(encoding="utf-8").split())
        self.assertRegex(text, r'<step n="5" goal="Sync dependent project pointers')
        for expected in (
            "submodule SHAs",
            "lockfile",
            "current canonical head",
        ):
            self.assertIn(expected, text, f"contract must cover {expected}")

    def test_contract_forbids_hand_editing_lockfiles_and_faking_evidence(self) -> None:
        text = " ".join(CONTRACT_SKILL.read_text(encoding="utf-8").split())
        self.assertRegex(
            text,
            r"never hand-edit or search-and-replace the revision",
            "contract must forbid substituting a revision into a lockfile",
        )
        self.assertRegex(
            text,
            r"Never rewrite them to claim a run that did not happen",
            "contract must forbid fabricating validation evidence",
        )

    def test_contract_flags_orphaned_pins(self) -> None:
        text = " ".join(CONTRACT_SKILL.read_text(encoding="utf-8").split())
        check = re.search(
            r"<check if=\"the currently pinned commit survives only on an archived or rewritten branch\">.*?</check>",
            text,
        )
        self.assertIsNotNone(check, "contract must detect orphaned pins")
        self.assertIn("orphaned pin", check.group())


if __name__ == "__main__":
    unittest.main()
