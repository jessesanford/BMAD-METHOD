#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///

"""Guards the standing rule that a cascade expires when the default branch moves.

Per-layer and integration validation only ever prove something about the exact
default-branch commit the stack was cascaded onto. Reviews take time, other teams keep
merging, and the default branch advances -- so that evidence goes stale on someone
else's merge rather than on any change the stack author made. Nothing about the stack
looks different when this happens, which is precisely why it gets missed: the pass
counts from the last run are still sitting there, still green, and no longer describe
any tree that will exist after merge.

The failure this suite prevents is presenting stale green as current evidence --
reviewing, submitting, or merging a stack whose recorded base is no longer the default
branch head, on the strength of a validation run that has quietly expired.

So every skill that curates, submits, or validates the stack must state the invariant,
require re-cascading and re-validating before review/submit/merge, and require the base
SHA to be recorded so the staleness check is mechanical rather than a matter of memory.
bmad-integration-review carries the hard gate, because it is the last checkpoint before
a stack is treated as ready.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SHIP_ROOT = Path(__file__).parents[3]

# Every skill that can curate, submit, or validate stack layers.
STACK_SKILLS = (
    "bmad-integration-review",
    "bmad-rebase-cascade",
    "bmad-extend-pr-stack",
    "bmad-pr-ready",
    "bmad-submit-prs",
)


def skill_text(name: str) -> str:
    path = SHIP_ROOT / name / "SKILL.md"
    assert path.is_file(), f"{path} is missing"
    return " ".join(path.read_text(encoding="utf-8").split())


class CascadeFreshnessTests(unittest.TestCase):
    def test_every_stack_skill_states_the_invariant(self) -> None:
        for name in STACK_SKILLS:
            with self.subTest(skill=name):
                self.assertIn(
                    "Default-branch freshness invariant",
                    skill_text(name),
                    f"{name} must state that validation is only valid against the base it ran on",
                )

    def test_validation_is_scoped_to_the_exact_base_commit(self) -> None:
        for name in STACK_SKILLS:
            with self.subTest(skill=name):
                self.assertIn(
                    "only valid against the exact default-branch commit",
                    skill_text(name),
                    f"{name} must scope validation to the base commit it was earned on",
                )

    def test_re_cascade_is_required_before_review_submit_and_merge(self) -> None:
        for name in STACK_SKILLS:
            with self.subTest(skill=name):
                text = skill_text(name)
                self.assertIn(
                    "re-cascade onto the current default-branch head",
                    text,
                    f"{name} must require re-cascading onto the live default branch",
                )
                for moment in ("before stack review", "before merging"):
                    self.assertIn(
                        moment,
                        text,
                        f"{name} must name '{moment}' as a moment the invariant applies",
                    )

    def test_the_base_sha_must_be_recorded_so_the_check_is_mechanical(self) -> None:
        for name in STACK_SKILLS:
            with self.subTest(skill=name):
                text = skill_text(name)
                self.assertIn(
                    "Record the default-branch base SHA",
                    text,
                    f"{name} must require recording the base SHA the cascade ran against",
                )
                self.assertIn(
                    "Enforce it mechanically rather than by memory",
                    text,
                    f"{name} must not leave the staleness check to recall",
                )

    def test_stale_green_may_not_be_presented_as_current_evidence(self) -> None:
        for name in STACK_SKILLS:
            with self.subTest(skill=name):
                text = skill_text(name)
                self.assertIn(
                    "never restate a prior run's pass counts as if they still hold",
                    text,
                    f"{name} must forbid reusing expired pass counts as evidence",
                )

    def test_integration_review_halts_on_a_moved_base(self) -> None:
        text = skill_text("bmad-integration-review")
        self.assertIn(
            "the live default-branch head differs from the recorded cascade base",
            text,
            "integration-review must detect a moved base",
        )
        self.assertIn(
            "HALT and run **bmad-rebase-cascade** first",
            text,
            "integration-review must halt rather than review a stale stack",
        )
        self.assertIn(
            "This is a gate, not a recommendation",
            text,
            "the freshness check must be a gate, not advice",
        )

    def test_the_invariant_is_standing_not_one_time(self) -> None:
        for name in STACK_SKILLS:
            with self.subTest(skill=name):
                self.assertIn(
                    "standing invariant, not a one-time fix",
                    skill_text(name),
                    f"{name} must present the rule as recurring",
                )


if __name__ == "__main__":
    unittest.main()
