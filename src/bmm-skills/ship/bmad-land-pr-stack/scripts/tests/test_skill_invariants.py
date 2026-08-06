import re
import unittest
from pathlib import Path

SKILL = Path(__file__).parents[2] / "SKILL.md"
TEXT = SKILL.read_text(encoding="utf-8")


def step(number: int) -> str:
    match = re.search(rf'<step n="{number}".*?</step>', TEXT, flags=re.DOTALL)
    assert match, f"step {number} missing"
    return match.group()


def flat(text: str) -> str:
    return " ".join(text.split())


class LandPrStackInvariantTests(unittest.TestCase):
    def test_retarget_precedes_merge(self) -> None:
        region = flat(step(5))
        self.assertRegex(
            region,
            r"before merging it, retarget its immediate successor",
        )
        self.assertRegex(
            flat(TEXT),
            r"Retarget the successor BEFORE merging the current layer",
        )

    def test_order_comes_from_topology_not_pr_numbers(self) -> None:
        region = flat(step(2))
        self.assertRegex(region, r"PR numbers do not encode stack order")
        self.assertRegex(region, r"Resolve order from the base/head chain")
        check = re.search(
            r'<check if="the base/head chain and the \[N/X\] title labels disagree.*?</check>',
            step(2),
            re.DOTALL,
        )
        self.assertIsNotNone(check)
        self.assertRegex(flat(check.group()), r"HALT")

    def test_recovery_manifest_captured_before_mutation(self) -> None:
        region = flat(step(2))
        self.assertRegex(region, r"Record the recovery manifest \*\*before any mutation\*\*")
        self.assertRegex(region, r"do not prune remote-tracking\s*refs")

    def test_integration_pr_is_never_merged(self) -> None:
        region = flat(step(8))
        self.assertRegex(region, r"must remain OPEN and DRAFT")
        self.assertRegex(region, r"must never be merged")

    def test_no_self_approval_or_bypass_merge(self) -> None:
        flat_text = flat(TEXT)
        self.assertRegex(
            flat_text,
            r"Never .*re-approving your own retarget",
        )
        check = re.search(
            r'<check if="the merge is refused for a rule violation">.*?</check>',
            step(5),
            re.DOTALL,
        )
        self.assertIsNotNone(check)
        self.assertRegex(
            flat(check.group()),
            r"Do not retry with an admin/bypass flag, do not self-approve",
        )

    def test_review_refresh_polls_on_a_bounded_timer(self) -> None:
        region = flat(step(6))
        self.assertRegex(region, r"\{workflow\.review_poll_interval_seconds\}")
        self.assertRegex(region, r"\{workflow\.review_poll_timeout_seconds\}")
        self.assertRegex(region, r"never busy-wait")
        check = re.search(
            r'<check if="the poll window expires without an approval">.*?</check>',
            step(6),
            re.DOTALL,
        )
        self.assertIsNotNone(check)
        self.assertRegex(flat(check.group()), r"Stop the loop cleanly at a layer boundary")

    def test_recovery_restores_base_before_reopening(self) -> None:
        region = flat(step(7))
        self.assertRegex(region, r"Restore the deleted base branch")
        self.assertRegex(region, r"Reopen the PR")
        self.assertRegex(
            region,
            r"Reopening fails while its base branch does not exist",
        )
        self.assertLess(
            region.index("Restore the deleted base branch"),
            region.index("Reopen the PR"),
        )

    def test_stale_default_branch_routes_to_rebase_cascade(self) -> None:
        check = re.search(
            r'<check if="the default branch has advanced past the recorded cascade base">.*?</check>',
            step(3),
            re.DOTALL,
        )
        self.assertIsNotNone(check)
        self.assertRegex(flat(check.group()), r"Land nothing")
        self.assertRegex(flat(check.group()), r"bmad-rebase-cascade")

    def test_landing_requires_explicit_authorization(self) -> None:
        region = flat(step(1))
        self.assertRegex(region, r"Require an explicit human instruction to merge")
        self.assertRegex(region, r"must never be inferred")


if __name__ == "__main__":
    unittest.main()
