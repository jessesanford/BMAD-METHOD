import importlib.util, json, subprocess, tempfile, unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).parents[1]
def module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value
PRODUCER = module("producer", SCRIPTS / "produce_validation_evidence.py")
SUBMIT = module("submit", SCRIPTS.parents[1] / "bmad-submit-prs" / "scripts" / "submit_pr_stack.py")
def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, text=True,
                          capture_output=True, check=True).stdout.strip()
RUNNER = """\
import pathlib, sys
mode = sys.argv[1]
def write(name, content="wheel"):
    path = pathlib.Path(name)
    path.parent.mkdir(exist_ok=True); path.write_text(content)
if mode == "fail" and pathlib.Path("two.txt").exists():
    raise SystemExit(4)
if mode == "probe-fail":
    raise SystemExit(5)
if mode == "probe-fail-second" and pathlib.Path("two.txt").exists():
    raise SystemExit(5)
if mode in ("test", "fail", "test-artifact"): print("Ran 2 tests in 0.001s\\n\\nOK (skipped=1)")
if mode == "test-artifact":
    write("dist/package.whl")
elif mode == "build-both":
    write("dist/first.whl"); write("dist/later.whl")
elif mode == "mutate":
    write("dist/package.whl", "changed"); write("dist/second.whl")
elif mode == "symlink":
    out = pathlib.Path("dist/package.whl")
    out.parent.mkdir(exist_ok=True); out.symlink_to("../runner.py")
elif mode == "build":
    write("dist/package.whl")
"""
class EvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Test User"); git(self.repo, "config", "user.email", "test@example.com")
        (self.repo / "runner.py").write_text(RUNNER, encoding="utf-8")
        git(self.repo, "add", "."); git(self.repo, "commit", "-qm", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        self.first = self.commit("one", "one.txt")
        self.second = self.commit("two", "two.txt")
        git(self.repo, "branch", "one-pr-ready", self.first); git(self.repo, "branch", "two-pr-ready", self.second)
        self.report = self.repo / ".git" / "builder.json"
        self.report.write_text(json.dumps({
            "status": "applied", "base": self.base,
            "layers": [{"target": "one-pr-ready", "new_tip": self.first},
                       {"target": "two-pr-ready", "new_tip": self.second}],
        }), encoding="utf-8")
        self.config = self.repo / ".git" / "evidence.json"
        self.write_config()
    def tearDown(self) -> None:
        self.temp.cleanup()
    def commit(self, content: str, name: str) -> str:
        (self.repo / name).write_text(content, encoding="utf-8")
        git(self.repo, "add", name); git(self.repo, "commit", "-qm", content)
        return git(self.repo, "rev-parse", "HEAD")
    def write_config(self, *, test: str = "test", build: str = "build",
                     artifact: str = "dist/*.whl", report: str = "docs/stack-evidence.md") -> None:
        self.config.write_text(json.dumps({
            "schema_version": 1, "report_path": report,
            "test": {"argv": ["python3", "runner.py", test], "parser": "unittest", "timeout_seconds": 5},
            "builds": [{"argv": ["python3", "runner.py", build], "artifacts": [artifact], "timeout_seconds": 5}],
            "feature_flag": {
                "name": "FEATURE_ENABLED", "safe_default": "disabled",
                "disabled_behavior": "does not import feature runtime",
                "default_check_argv": ["python3", "runner.py", "check"],
                "disabled_check_argv": ["python3", "runner.py", "check"],
            },
        }), encoding="utf-8")
    def produce(self, branch: str = "integration/validated", expected: str | None = None):
        return PRODUCER.produce(self.repo, self.report, self.config, branch, expected)
    def steps(self, test: str, *builds: tuple[str, str]) -> None:
        self.write_config(test=test)
        value = json.loads(self.config.read_text())
        value["builds"] = [{"argv": ["python3", "runner.py", mode], "artifacts": [artifact],
                            "timeout_seconds": 5} for mode, artifact in builds]
        self.config.write_text(json.dumps(value))
    def ref(self, branch: str) -> str | None:
        result = subprocess.run(["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
                                cwd=self.repo, text=True, capture_output=True)
        return result.stdout.strip() if not result.returncode else None
    def test_two_prefix_evidence_satisfies_submit_report_contract(self) -> None:
        result = self.produce()
        evidence = result["integration_evidence"]
        manifest = {"repository": "example/project", "_head_repository": "example/project", "publish_remote": "origin",
                    "integration_evidence": evidence}
        with mock.patch.object(SUBMIT, "remote_sha", return_value=evidence["commit"]):
            SUBMIT.validate_integration_evidence(self.repo, manifest,
                                                 [{"_tip": self.first, "remote_branch": "one-pr-ready"},
                                                  {"_tip": self.second, "remote_branch": "two-pr-ready"}])
        self.assertEqual(evidence["partial_merge_safety"]["validated_prefixes"], 2)
        self.assertEqual(evidence["partial_merge_safety"]["prefix_tips"], [self.first, self.second])
        self.assertEqual(git(self.repo, "rev-parse", f"{evidence['commit']}^"), self.second)
    def test_unittest_parser_accepts_only_clean_supported_summaries(self) -> None:
        self.assertEqual(PRODUCER.test_counts("Ran 2 tests\n\nOK", "unittest"),
                         {"passed": 2, "skipped": 0, "warnings": 0})
        for summary in ("OK (expected failures=1)", "OK (unexpected successes=1)",
                        "ResourceWarning: leak\nOK"):
            with self.subTest(summary=summary), self.assertRaises(PRODUCER.EvidenceError):
                PRODUCER.test_counts(f"Ran 2 tests\n\n{summary}", "unittest")
    def test_pytest_parser_accepts_clean_summary_and_rejects_failures_or_missing_summary(self) -> None:
        self.assertEqual(
            PRODUCER.test_counts("===== 5 passed in 0.12s =====", "pytest"),
            {"passed": 5, "skipped": 0, "warnings": 0},
        )
        self.assertEqual(
            PRODUCER.test_counts(
                "===== 5 passed, 1 skipped, 2 warnings in 0.12s =====", "pytest"
            ),
            {"passed": 5, "skipped": 1, "warnings": 2},
        )
        for output, message in (
            ("===== 4 passed, 1 failed in 0.12s =====", "failures or errors"),
            ("===== 4 passed, 1 error in 0.12s =====", "failures or errors"),
            ("collected 5 items", "no unique summary"),
            (
                "===== 5 passed in 0.12s =====\n===== 5 passed in 0.12s =====",
                "no unique summary",
            ),
            ("===== 5 xfailed in 0.12s =====", "unsupported outcomes"),
        ):
            with self.subTest(output=output), self.assertRaisesRegex(
                PRODUCER.EvidenceError, message
            ):
                PRODUCER.test_counts(output, "pytest")
    def test_failing_later_prefix_safety_probes_preserve_evidence_ref(self) -> None:
        git(self.repo, "branch", "integration/validated", self.first)
        for field, label in (
            ("default_check_argv", "default safety check"),
            ("disabled_check_argv", "disabled safety check"),
        ):
            with self.subTest(field=field):
                value = json.loads(self.config.read_text())
                value["feature_flag"][field] = ["python3", "runner.py", "probe-fail-second"]
                self.config.write_text(json.dumps(value))
                with self.assertRaisesRegex(PRODUCER.EvidenceError, label):
                    self.produce()
                self.assertEqual(self.ref("integration/validated"), self.first)
                self.write_config()
    def test_prefix_test_failure_leaves_no_ref(self) -> None:
        self.write_config(test="fail")
        with self.assertRaisesRegex(PRODUCER.EvidenceError, "two-pr-ready"): self.produce()
        self.assertIsNone(self.ref("integration/validated"))
    def test_argv_metacharacters_are_literal(self) -> None:
        marker = self.repo / ".git" / "shell-owned"
        value = json.loads(self.config.read_text())
        value["test"]["argv"] += [";", "touch", str(marker)]
        self.config.write_text(json.dumps(value))
        self.produce()
        self.assertFalse(marker.exists())
    def test_missing_and_symlink_artifacts_are_rejected(self) -> None:
        for mode, pattern, message in (
            ("missing", "dist/*.whl", "artifact missing"),
            ("symlink", "dist/*.whl", "non-symlink"),
        ):
            with self.subTest(mode=mode):
                self.write_config(build=mode, artifact=pattern)
                with self.assertRaisesRegex(PRODUCER.EvidenceError, message): self.produce(f"integration/{mode}")
                self.assertIsNone(self.ref(f"integration/{mode}"))
    def test_artifacts_require_build_provenance_and_final_hash(self) -> None:
        cases = (
            ("test-artifact", (("noop", "dist/*.whl"),), "preexist"),
            ("test", (("build-both", "dist/first.whl"), ("noop", "dist/later.whl")), "preexist"),
            ("test", (("build", "dist/package.whl"), ("mutate", "dist/second.whl")), "mutated"),
        )
        for test, builds, message in cases:
            with self.subTest(message=message):
                self.steps(test, *builds)
                with self.assertRaisesRegex(PRODUCER.EvidenceError, message): self.produce("integration/provenance")
                self.assertIsNone(self.ref("integration/provenance"))
    def test_stale_component_target_is_rejected(self) -> None:
        git(self.repo, "branch", "-f", "two-pr-ready", self.base)
        with self.assertRaisesRegex(PRODUCER.EvidenceError, "stale target"): self.produce()
        self.assertIsNone(self.ref("integration/validated"))
    def test_ref_transaction_rejects_symbolic_evidence_and_component_race(self) -> None:
        git(self.repo, "symbolic-ref", "refs/heads/integration/validated", "refs/heads/one-pr-ready")
        with self.assertRaisesRegex(PRODUCER.EvidenceError, "must not be symbolic"): self.produce()
        self.assertEqual(git(self.repo, "symbolic-ref", "refs/heads/integration/validated"),
                         "refs/heads/one-pr-ready")
        self.assertEqual(self.ref("one-pr-ready"), self.first)
        git(self.repo, "symbolic-ref", "--delete", "refs/heads/integration/validated")
        original = PRODUCER.commit_report
        def race(*args):
            git(self.repo, "branch", "-f", "one-pr-ready", self.base)
            return original(*args)
        with mock.patch.object(PRODUCER, "commit_report", side_effect=race):
            with self.assertRaisesRegex(PRODUCER.EvidenceError, "changed during validation"): self.produce()
        self.assertEqual(self.ref("one-pr-ready"), self.base)
        self.assertIsNone(self.ref("integration/validated"))
    def test_exact_expected_old_replaces_evidence_ref(self) -> None:
        first = self.produce()
        old = first["integration_evidence"]["commit"]
        self.assertEqual(self.produce(expected=old), first)
        self.write_config(report="docs/new-stack-evidence.md")
        new = self.produce(expected=old)["integration_evidence"]["commit"]
        self.assertNotEqual(old, new)
        self.assertEqual(self.ref("integration/validated"), new)
        with self.assertRaisesRegex(PRODUCER.EvidenceError, "expected-old"):
            self.produce(expected=old)
        self.assertEqual(self.ref("integration/validated"), new)
if __name__ == "__main__":
    unittest.main()
