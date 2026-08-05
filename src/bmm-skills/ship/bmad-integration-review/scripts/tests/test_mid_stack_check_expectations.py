#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///

"""Guards where a stacked chain's CI signal is actually authoritative.

In a stacked PR chain, a middle layer routinely fails its own checks for a purely
logical reason: it uses a dependency, module, fixture, migration, or config that a
LATER layer in the same stack introduces. That is correct behavior for a stack, not
a defect. The integration/validation branch exists precisely to answer the only
question that matters -- does the combined changeset pass once every layer is merged
in order?

The failure this suite prevents is an agent treating a red mid-stack check as a bug
and "fixing" the stack to make it green: moving dependency declarations to earlier
layers, pulling pin/version bumps forward, reordering layers, or sprinkling skips.
Each of those rewrites already-reviewed branches and discards reviewer approvals to
chase a signal that was never the gate.

So every skill that curates, moves, submits, or validates the stack must state that
mid-stack red is expected, that the integration branch is the authoritative gate, and
must require classifying a failure as later-layer-dependency vs genuine defect vs
partial-merge hazard before touching anything.

The prohibition has one narrow carve-out, and these tests pin it down so it cannot
quietly widen. Mid-stack green was never the gate, but the default branch always is:
when a stack sits directly on the default branch, merging one approved layer makes the
default branch's tree equal to that layer's tree, so a layer that fails its own checks
will break trunk for everyone the moment it lands. Pulling a declaration or pin forward
is then the correct fix rather than the prohibited one. Because that exception is easy
to over-apply, every skill stating it must also demand proof in a pristine worktree,
deny that a feature flag makes a partial merge safe, and record which class was invoked.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SHIP_ROOT = Path(__file__).parents[3]

# Every skill that can move, curate, submit, or validate stack layers.
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


class MidStackCheckExpectationTests(unittest.TestCase):
    def test_every_stack_skill_says_mid_stack_red_is_expected(self) -> None:
        for name in STACK_SKILLS:
            with self.subTest(skill=name):
                text = skill_text(name)
                self.assertTrue(
                    "LATER layer in the same stack" in text,
                    f"{name} must explain that a layer may depend on a later layer",
                )

    def test_every_stack_skill_forbids_chasing_a_mid_stack_green(self) -> None:
        for name in STACK_SKILLS:
            with self.subTest(skill=name):
                text = skill_text(name)
                self.assertTrue(
                    "mid-stack green" in text,
                    f"{name} must forbid restructuring the stack to silence a mid-stack check",
                )
                for forbidden in ("reorder layers", "pin"):
                    self.assertTrue(
                        forbidden in text,
                        f"{name} must name '{forbidden}' among the forbidden shortcuts",
                    )

    def test_integration_branch_is_named_the_authoritative_gate(self) -> None:
        for name in STACK_SKILLS:
            with self.subTest(skill=name):
                self.assertTrue(
                    "authoritative green gate" in skill_text(name),
                    f"{name} must name the integration branch as the only required-green branch",
                )

    def test_integration_review_states_the_claim_being_proven(self) -> None:
        text = skill_text("bmad-integration-review")
        self.assertTrue(
            "merged into the default branch in the order its PRs describe" in text,
            "the integration branch's exact claim must be spelled out",
        )

    def test_genuine_defects_are_still_fixed_at_their_owning_layer(self) -> None:
        # The rule must not become an excuse to ignore real breakage.
        for name in STACK_SKILLS:
            with self.subTest(skill=name):
                text = skill_text(name)
                self.assertTrue(
                    "genuine defect" in text.lower(),
                    f"{name} must still require fixing real defects",
                )
                self.assertTrue(
                    "owning layer" in text,
                    f"{name} must route a genuine fix to the layer that owns it",
                )

    def test_a_classification_step_precedes_any_fix(self) -> None:
        text = skill_text("bmad-integration-review")
        self.assertTrue(
            "Before \"fixing\" any failing check, classify it" in text,
            "integration-review must require classifying the failure first",
        )
        self.assertTrue(
            "would still fail with the entire stack merged" in skill_text("bmad-rebase-cascade"),
            "the discriminating test for a genuine defect must be stated",
        )

    def test_partial_merge_hazard_is_carved_out_of_the_prohibition(self) -> None:
        # Mid-stack green is not the gate, but the default branch always is. A layer that
        # breaks the default branch the moment it merges is not a cosmetic signal, and the
        # blanket "never pull a pin forward" rule must not forbid keeping trunk releasable.
        for name in STACK_SKILLS:
            with self.subTest(skill=name):
                text = skill_text(name)
                self.assertTrue(
                    "DEFAULT BRANCH" in text or "Partial-merge hazard" in text,
                    f"{name} must carve out the case where a layer breaks the default branch on merge",
                )

    def test_the_carve_out_requires_proof_rather_than_a_hunch(self) -> None:
        # The exception is easy to over-apply; every skill that states it must also
        # demand the layer be checked out and run against the default branch's own gates.
        for name in STACK_SKILLS:
            with self.subTest(skill=name):
                text = skill_text(name)
                self.assertTrue(
                    "pristine worktree" in text,
                    f"{name} must require reproducing the breakage in a pristine worktree",
                )

    def test_feature_flags_are_not_accepted_as_partial_merge_protection(self) -> None:
        # A flag gates runtime behavior; it cannot stop a collection error, a bad lockfile,
        # or a failed migration from landing on the default branch.
        for name in STACK_SKILLS:
            with self.subTest(skill=name):
                text = skill_text(name)
                self.assertTrue(
                    "feature flag does not cover" in text or "no feature flag protects" in text,
                    f"{name} must state that a feature flag does not make a partial merge safe",
                )

    def test_the_invoked_class_must_be_recorded_for_reviewers(self) -> None:
        for name in STACK_SKILLS:
            with self.subTest(skill=name):
                text = skill_text(name)
                self.assertTrue(
                    "which class" in text,
                    f"{name} must require recording which classification justified touching the stack",
                )


if __name__ == "__main__":
    unittest.main()
