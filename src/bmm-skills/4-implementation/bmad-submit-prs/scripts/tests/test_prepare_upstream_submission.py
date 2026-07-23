import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "prepare_upstream_submission.py"
SPEC = importlib.util.spec_from_file_location("preparer", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PreparationTests(unittest.TestCase):
    def fixture(self, directory: Path) -> tuple[Path, Path]:
        body = directory / "body.md"
        body.write_text("## Summary\n\nPrepared.\n", encoding="utf-8")
        manifest = directory / "pre-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "layers": [{"body_file": body.name}],
                }
            ),
            encoding="utf-8",
        )
        journal = directory / "pre-journal.json"
        journal.write_text(
            json.dumps(
                {
                    "status": "dry-run",
                    "layers": [
                        {
                            "source_body": str(body.resolve()),
                            "source_body_sha256": MODULE.sha256_file(body),
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        origin_request = directory / "origin-request.json"
        origin_request.write_text(
            json.dumps(
                {
                    "submission_manifest": str(manifest),
                    "submission_journal": str(journal),
                }
            ),
            encoding="utf-8",
        )
        audit = directory / "audit.json"
        audit.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "audited",
                    "request_path": str(origin_request),
                    "input_hashes": {
                        "request": MODULE.sha256_file(origin_request),
                        "submission_manifest": MODULE.sha256_file(manifest),
                        "submission_journal": MODULE.sha256_file(journal),
                    },
                }
            ),
            encoding="utf-8",
        )
        output = directory / "regenerated"
        request = directory / "prepare-request.json"
        request.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "audit_receipt": str(audit),
                    "audit_receipt_sha256": MODULE.sha256_file(audit),
                    "output_directory": str(output),
                    "upstream_submit_authorized": True,
                    "upstream_submit_authorization_phrase": MODULE.ORIGIN_APPROVAL_PHRASE,
                }
            ),
            encoding="utf-8",
        )
        return request, output

    def test_prepare_seal_and_validate_apply_bind_exact_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            request, output = self.fixture(directory)
            prepared = MODULE.prepare(Path.cwd(), request)
            regenerated = json.loads(Path(prepared["manifest"]).read_text())
            self.assertEqual(regenerated["layers"][0]["body_file"], "bodies/01.md")
            rendered_title = output / "title.txt"
            rendered_body = output / "body.md"
            integration_title = output / "integration-title.txt"
            integration_body = output / "integration-body.md"
            for path in (
                rendered_title,
                rendered_body,
                integration_title,
                integration_body,
            ):
                path.write_text(path.name + "\n", encoding="utf-8")
            source_body = output / "bodies/01.md"
            Path(prepared["dry_run_journal"]).write_text(
                json.dumps(
                    {
                        "status": "dry-run",
                        "manifest": prepared["manifest"],
                        "manifest_sha256": MODULE.sha256_file(Path(prepared["manifest"])),
                        "layers": [{
                            "source_body": str(source_body),
                            "source_body_sha256": MODULE.sha256_file(source_body),
                            "rendered_title": str(rendered_title),
                            "rendered_title_sha256": MODULE.sha256_file(rendered_title),
                            "rendered_body": str(rendered_body),
                            "rendered_body_sha256": MODULE.sha256_file(rendered_body),
                        }],
                        "integration_pr": {
                            "rendered_title": str(integration_title),
                            "rendered_title_sha256": MODULE.sha256_file(integration_title),
                            "rendered_body": str(integration_body),
                            "rendered_body_sha256": MODULE.sha256_file(integration_body),
                        },
                    }
                ),
                encoding="utf-8",
            )
            sealed = MODULE.seal(Path.cwd(), output / "prepared.json")
            apply_request = directory / "apply-request.json"
            apply_journal = output / "apply.json"
            apply_request.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "preparation_receipt": prepared["preparation_receipt"],
                        "preparation_receipt_sha256": MODULE.sha256_file(
                            Path(prepared["preparation_receipt"])
                        ),
                        "apply_journal": str(apply_journal),
                        "upstream_apply_authorized": True,
                        "upstream_apply_authorization_phrase": MODULE.UPSTREAM_APPROVAL_PHRASE,
                    }
                ),
                encoding="utf-8",
            )
            inputs = MODULE.validate_apply_request(Path.cwd(), apply_request)
            self.assertEqual(inputs["manifest"], sealed["manifest"])
            self.assertEqual(inputs["dry_run_journal"], sealed["dry_run_journal"])
            self.assertEqual(inputs["apply_journal"], str(apply_journal))
            self.assertEqual(inputs["apply_request"], str(apply_request.resolve()))
            apply_journal.write_text("{}\n", encoding="utf-8")
            resumed = MODULE.validate_apply_request(Path.cwd(), apply_request)
            self.assertEqual(resumed["apply_journal"], str(apply_journal))

    def test_prepare_rejects_nonempty_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            request, output = self.fixture(directory)
            output.mkdir()
            (output / "existing").write_text("preserve\n", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.PreparationError, "must not already exist"):
                MODULE.prepare(Path.cwd(), request)


if __name__ == "__main__":
    unittest.main()
