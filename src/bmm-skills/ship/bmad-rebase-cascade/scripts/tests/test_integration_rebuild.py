#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///

"""Guards integration/evidence rebuild whenever the composed stack changes.

Integration and validation-evidence branches are derived artifacts of one exact
stack revision. A cascade rewrites every component SHA, and an extension adds
layers the old branch never composed. In both cases the previously recorded
evidence describes a tree that is no longer under review.

The failure this suite prevents is the tempting shortcut: rebase the old
integration branch in place, or carry its validation report forward, so the
stack still *looks* validated. That presents untested code as proven. The skills
must instead recut a new branch from the new final head, re-run validation, and
retire the superseded PR without deleting its branch.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SHIP_ROOT = Path(__file__).parents[3]

REBUILD_SKILLS = ("bmad-rebase-cascade", "bmad-extend-pr-stack")


def skill_text(name: str) -> str:
    path = SHIP_ROOT / name / "SKILL.md"
    assert path.is_file(), f"{path} is missing"
    return " ".join(path.read_text(encoding="utf-8").split())


class IntegrationRebuildTests(unittest.TestCase):
    def test_both_stack_changing_skills_require_a_recut(self) -> None:
        for name in REBUILD_SKILLS:
            with self.subTest(skill=name):
                text = skill_text(name)
                self.assertIn(
                    "final component head",
                    text,
                    f"{name} must cut the new integration branch from the new final head",
                )
                self.assertIn(
                    "bmad-integration-review",
                    text,
                    f"{name} must delegate re-validation rather than reimplement it",
                )

    def test_rebasing_the_old_integration_branch_is_forbidden(self) -> None:
        for name in REBUILD_SKILLS:
            with self.subTest(skill=name):
                self.assertIn(
                    "in place",
                    skill_text(name),
                    f"{name} must forbid updating the old integration branch in place",
                )

    def test_stale_evidence_may_not_be_carried_forward(self) -> None:
        cascade = skill_text("bmad-rebase-cascade")
        self.assertIn("fabricates proof", cascade)
        self.assertIn(
            "leave the stack without fresh evidence",
            cascade,
            "cascade must prefer absent evidence over stale evidence",
        )
        extend = skill_text("bmad-extend-pr-stack")
        self.assertIn("presents untested code as validated", extend)

    def test_superseded_branches_are_retired_but_not_deleted(self) -> None:
        for name in REBUILD_SKILLS:
            with self.subTest(skill=name):
                text = skill_text(name)
                self.assertIn(
                    "Never delete the superseded branch",
                    text,
                    f"{name} must preserve the superseded branch as an audit record",
                )
                self.assertIn(
                    "naming the replacement",
                    text,
                    f"{name} must point reviewers from the closed PR to its replacement",
                )

    def test_cascade_halts_on_unique_commits_in_a_derived_branch(self) -> None:
        cascade = skill_text("bmad-rebase-cascade")
        self.assertIn(
            'if="a derived branch carries unique commits',
            cascade,
            "recutting must not silently drop work unique to the integration branch",
        )


if __name__ == "__main__":
    unittest.main()
