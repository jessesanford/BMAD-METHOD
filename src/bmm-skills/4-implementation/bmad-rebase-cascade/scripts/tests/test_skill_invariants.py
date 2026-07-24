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


class RebaseCascadeInvariantTests(unittest.TestCase):
    def test_default_branch_divergence_halts_without_rewrite(self) -> None:
        region = step(3)
        self.assertRegex(region, r"`git merge --ff-only [^`]+`")
        check = re.search(
            r'<check if="the fast-forward fails.*?</check>', region, re.DOTALL
        )
        self.assertIsNotNone(check)
        self.assertRegex(check.group(), r"HALT immediately\.")
        self.assertRegex(
            check.group(),
            r"Do NOT force, rebase, reset, or merge the default branch any other way",
        )

    def test_conflicts_abort_and_stop_the_remaining_cascade(self) -> None:
        check = re.search(
            r'<check if="any rebase in this loop conflicts">.*?</check>',
            step(5),
            re.DOTALL,
        )
        self.assertIsNotNone(check)
        self.assertRegex(check.group(), r"`git rebase --abort` immediately")
        self.assertRegex(check.group(), r"STOP the workflow here")
        self.assertRegex(check.group(), r"do not continue cascading")

    def test_empty_stack_halts(self) -> None:
        check = re.search(
            r'<check if="no such branches exist">.*?</check>', step(4), re.DOTALL
        )
        self.assertIsNotNone(check)
        self.assertRegex(check.group(), r"HALT: .*nothing to cascade")

    def test_pushes_only_stack_branches_to_origin(self) -> None:
        region = step(6)
        commands = re.findall(r"`git push [^`\n]+`", region)
        self.assertEqual(
            commands, ["`git push --force-with-lease origin <branch>`"]
        )
        self.assertRegex(region, r"Never push to `upstream`\.")
        self.assertNotRegex(TEXT, r"git push[^`\n]*(?:upstream|--mirror)")


if __name__ == "__main__":
    unittest.main()
