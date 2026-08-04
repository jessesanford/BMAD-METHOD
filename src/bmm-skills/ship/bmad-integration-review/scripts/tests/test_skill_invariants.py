import re
import unittest
from pathlib import Path

SKILL = Path(__file__).parents[2] / "SKILL.md"
TEXT = SKILL.read_text(encoding="utf-8")


def step(number: int) -> str:
    match = re.search(
        rf'<step n="{number}".*?</step>', TEXT, flags=re.DOTALL
    )
    assert match, f"step {number} missing"
    return match.group()


def check(region: str, condition: str) -> str:
    match = re.search(
        rf'<check if="{re.escape(condition)}">.*?</check>', region, re.DOTALL
    )
    assert match, f"check missing: {condition}"
    return match.group()


class IntegrationReviewInvariantTests(unittest.TestCase):
    def test_merge_conflicts_abort_and_halt(self) -> None:
        region = check(step(3), "any merge conflicts")
        self.assertRegex(region, r"`git merge --abort`")
        self.assertRegex(region, r"Do NOT\s+attempt to resolve it yourself")
        self.assertRegex(region, r"HALT and report the branch and conflicting file list")

    def test_test_failures_escalate_without_auto_fix(self) -> None:
        region = check(step(4), "any test fails, in either category")
        self.assertRegex(region, r"Do NOT[\s\S]*?attempt to fix it yourself")
        self.assertRegex(region, r"HALT: report which test\(s\) failed")
        self.assertRegex(region, r"recommend `bmad-correct-course`")

    def test_canonical_adversarial_review_fails_closed(self) -> None:
        region = step(5)
        self.assertEqual(region.count("`skill:bmad-review lenses=adversarial`"), 1)
        self.assertEqual(
            TEXT.count("`skill:bmad-review lenses=adversarial`"), 1
        )
        failure = check(
            region,
            "the skill invocation fails, its announced lens plan is not exactly adversarial, or it does not return a valid JSON array",
        )
        self.assertRegex(failure, r"HALT\.")
        self.assertRegex(failure, r"Do not synthesize findings")

    def test_persisted_report_must_match_returned_findings(self) -> None:
        region = check(
            step(5),
            "{review_report} is missing or unreadable, or its canonical findings JSON is not identical to the returned array",
        )
        self.assertRegex(region, r"HALT\.")
        self.assertRegex(region, r"Report the report-integrity failure")
        self.assertRegex(region, r"Do not rewrite\s+either output")

    def test_significance_is_human_selected_and_never_auto_fixed(self) -> None:
        region = step(6)
        nonempty = check(region, "the canonical findings array is non-empty")
        self.assertRegex(nonempty, r"Ask the human which findings")
        self.assertRegex(nonempty, r"Do not decide significance yourself")
        selected = check(
            region, "the human selects one or more findings as significant"
        )
        self.assertRegex(selected, r"Do not fix any finding or modify a story branch")
        self.assertRegex(selected, r"Invoke `skill:bmad-correct-course`")
        self.assertRegex(selected, r"human selected\s+these as significant")


if __name__ == "__main__":
    unittest.main()
