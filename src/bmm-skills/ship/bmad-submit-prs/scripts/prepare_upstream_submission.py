#!/usr/bin/env python3
"""Regenerate and seal a post-origin-review upstream submission package."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ORIGIN_APPROVAL_PHRASE = "approve origin review for upstream submission"
UPSTREAM_APPROVAL_PHRASE = "approve regenerated upstream dry run"


class PreparationError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PreparationError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreparationError(f"cannot read {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PreparationError(f"{label} must be a JSON object")
    return payload


def require_absolute_file(value: object, *, label: str) -> Path:
    if not isinstance(value, str):
        raise PreparationError(f"{label} must be an absolute file path")
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise PreparationError(f"{label} must be an existing absolute file")
    return path


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def ensure_artifact_directory(repo: Path, output: Path) -> None:
    if not output.is_absolute():
        raise PreparationError("artifact directory must be absolute")
    try:
        relative = output.resolve().relative_to(repo.resolve())
    except ValueError:
        return
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--", str(relative)],
        cwd=repo,
        check=False,
    )
    if result.returncode != 0:
        raise PreparationError("artifact directory inside the worktree must be git-ignored")


def validate_preparation_request(path: Path) -> tuple[dict[str, Any], Path, Path]:
    request = load_json(path, label="preparation request")
    expected = {
        "schema_version",
        "audit_receipt",
        "audit_receipt_sha256",
        "output_directory",
        "upstream_submit_authorized",
        "upstream_submit_authorization_phrase",
    }
    if set(request) != expected or request.get("schema_version") != 1:
        raise PreparationError("invalid upstream preparation request schema")
    if (
        request["upstream_submit_authorized"] is not True
        or request["upstream_submit_authorization_phrase"] != ORIGIN_APPROVAL_PHRASE
    ):
        raise PreparationError("missing explicit post-origin-review approval")
    audit = require_absolute_file(request["audit_receipt"], label="audit_receipt")
    if request["audit_receipt_sha256"] != sha256_file(audit):
        raise PreparationError("audit_receipt_sha256 mismatch")
    output = Path(str(request["output_directory"]))
    if not output.is_absolute():
        raise PreparationError("output_directory must be absolute")
    if len({path.resolve(), audit.resolve(), output.resolve()}) != 3:
        raise PreparationError("request, audit receipt, and output directory must be distinct")
    return request, audit, output


def prepare(repo: Path, request_path: Path) -> dict[str, Any]:
    _request, audit_path, output = validate_preparation_request(request_path)
    ensure_artifact_directory(repo, output)
    if output.exists():
        raise PreparationError("output_directory must not already exist")
    audit = load_json(audit_path, label="audit receipt")
    if audit.get("schema_version") != 1 or audit.get("status") != "audited":
        raise PreparationError("audit receipt is not canonical audited evidence")
    hashes = audit.get("input_hashes")
    if not isinstance(hashes, dict):
        raise PreparationError("audit receipt input_hashes must be an object")
    origin_request_path = require_absolute_file(
        audit.get("request_path"), label="audit request_path"
    )
    if hashes.get("request") != sha256_file(origin_request_path):
        raise PreparationError("origin request changed after audit")
    origin_request = load_json(origin_request_path, label="origin staging request")
    if origin_request.get("origin_finalize_authorized") is True:
        raise PreparationError(
            "origin review used for upstream approval must remain in draft audit state"
        )
    pre_manifest = require_absolute_file(
        origin_request.get("submission_manifest"), label="pre-review manifest"
    )
    pre_journal = require_absolute_file(
        origin_request.get("submission_journal"), label="pre-review journal"
    )
    if (
        hashes.get("submission_manifest") != sha256_file(pre_manifest)
        or hashes.get("submission_journal") != sha256_file(pre_journal)
    ):
        raise PreparationError("pre-review package changed after audit")
    manifest = load_json(pre_manifest, label="pre-review manifest")
    journal = load_json(pre_journal, label="pre-review journal")
    layers = manifest.get("layers")
    journal_layers = journal.get("layers")
    if (
        manifest.get("schema_version") != 2
        or not isinstance(layers, list)
        or not layers
        or not isinstance(journal_layers, list)
        or len(journal_layers) != len(layers)
    ):
        raise PreparationError("pre-review manifest is not schema-v2")
    regenerated = copy.deepcopy(manifest)
    regenerated["draft"] = True
    regenerated["origin_review"] = {
        "audit_receipt": str(audit_path.resolve()),
        "audit_receipt_sha256": sha256_file(audit_path),
        "pre_review_manifest_sha256": hashes["submission_manifest"],
        "pre_review_journal_sha256": hashes["submission_journal"],
        "approved": True,
        "approval_phrase": ORIGIN_APPROVAL_PHRASE,
    }
    sources: list[tuple[Path, bytes]] = []
    for index, layer in enumerate(regenerated["layers"], start=1):
        if not isinstance(layer, dict) or not isinstance(layer.get("body_file"), str):
            raise PreparationError(f"layer {index} is missing body_file")
        source = Path(layer["body_file"])
        if not source.is_absolute():
            source = pre_manifest.parent / source
        if not source.is_file() or source.is_symlink():
            raise PreparationError(f"layer {index} body file is missing")
        try:
            content = source.read_bytes()
        except OSError as exc:
            raise PreparationError(f"cannot read layer {index} body file: {exc}") from exc
        recorded = journal_layers[index - 1]
        if (
            not isinstance(recorded, dict)
            or recorded.get("source_body") != str(source.resolve())
            or recorded.get("source_body_sha256")
            != hashlib.sha256(content).hexdigest()
        ):
            raise PreparationError(
                f"layer {index} body no longer matches the audited pre-review journal"
            )
        sources.append((source, content))
    try:
        bodies = output / "bodies"
        bodies.mkdir(parents=True)
        for index, (layer, source_record) in enumerate(
            zip(regenerated["layers"], sources), start=1
        ):
            _source, content = source_record
            destination = bodies / f"{index:02d}.md"
            destination.write_bytes(content)
            layer["body_file"] = str(destination.relative_to(output))
        manifest_path = output / "submission-manifest.json"
        atomic_write(manifest_path, regenerated)
        receipt = {
            "schema_version": 1,
            "status": "prepared",
            "request_path": str(request_path.resolve()),
            "request_sha256": sha256_file(request_path),
            "audit_receipt": str(audit_path.resolve()),
            "audit_receipt_sha256": sha256_file(audit_path),
            "manifest": str(manifest_path),
            "manifest_sha256": sha256_file(manifest_path),
            "dry_run_journal": str(output / "dry-run.json"),
            "preparation_receipt": str(output / "preparation-receipt.json"),
        }
        atomic_write(output / "prepared.json", receipt)
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise
    return receipt


def validate_dry_run_journal(journal: dict[str, Any], manifest: Path) -> None:
    if (
        journal.get("status") != "dry-run"
        or journal.get("manifest") != str(manifest.resolve())
        or journal.get("manifest_sha256") != sha256_file(manifest)
    ):
        raise PreparationError("dry-run journal does not bind the regenerated manifest")
    layers = journal.get("layers")
    integration = journal.get("integration_pr")
    if not isinstance(layers, list) or not layers or not isinstance(integration, dict):
        raise PreparationError("dry-run journal lacks component or integration artifacts")
    for record in [*layers, integration]:
        if not isinstance(record, dict):
            raise PreparationError("dry-run journal contains a malformed artifact record")
        fields = (
            ("source_body",) if record is not integration else ()
        ) + ("rendered_title", "rendered_body")
        for field in fields:
            value = record.get(field)
            artifact = Path(value) if isinstance(value, str) else None
            if (
                artifact is None
                or not artifact.is_absolute()
                or not artifact.is_file()
                or record.get(f"{field}_sha256") != sha256_file(artifact)
            ):
                raise PreparationError(f"dry-run journal {field} is not sealed")


def validate_prepared_receipt(
    repo: Path, prepared_path: Path
) -> tuple[dict[str, Any], Path, Path, Path, Path]:
    prepared = load_json(prepared_path, label="prepared receipt")
    prepared_fields = {
        "schema_version",
        "status",
        "request_path",
        "request_sha256",
        "audit_receipt",
        "audit_receipt_sha256",
        "manifest",
        "manifest_sha256",
        "dry_run_journal",
        "preparation_receipt",
    }
    if (
        set(prepared) != prepared_fields
        or prepared.get("schema_version") != 1
        or prepared.get("status") != "prepared"
    ):
        raise PreparationError("prepared receipt is invalid")
    request_path = require_absolute_file(
        prepared.get("request_path"), label="request_path"
    )
    request, audit_path, request_output = validate_preparation_request(request_path)
    output = prepared_path.parent.resolve()
    if (
        request_output.resolve() != output
        or prepared.get("request_sha256") != sha256_file(request_path)
        or prepared.get("audit_receipt") != str(audit_path.resolve())
        or prepared.get("audit_receipt_sha256") != sha256_file(audit_path)
        or request.get("audit_receipt_sha256") != prepared.get("audit_receipt_sha256")
    ):
        raise PreparationError("prepared receipt does not bind its approved audit request")
    manifest = require_absolute_file(prepared.get("manifest"), label="manifest")
    dry_run = require_absolute_file(
        prepared.get("dry_run_journal"), label="dry_run_journal"
    )
    if (
        prepared.get("manifest_sha256") != sha256_file(manifest)
    ):
        raise PreparationError("prepared inputs changed during dry run")
    journal = load_json(dry_run, label="dry-run journal")
    validate_dry_run_journal(journal, manifest)
    destination = Path(str(prepared["preparation_receipt"]))
    if (
        manifest.resolve() != output / "submission-manifest.json"
        or dry_run.resolve() != output / "dry-run.json"
        or destination.resolve() != output / "preparation-receipt.json"
    ):
        raise PreparationError("prepared artifacts escaped the output directory")
    return prepared, request_path, manifest, dry_run, destination


def seal(repo: Path, prepared_path: Path) -> dict[str, Any]:
    prepared, request_path, manifest, dry_run, destination = validate_prepared_receipt(
        repo, prepared_path
    )
    receipt = {
        "schema_version": 1,
        "status": "dry-run-ready",
        "prepared_receipt": str(prepared_path.resolve()),
        "prepared_receipt_sha256": sha256_file(prepared_path),
        "request_path": str(request_path),
        "request_sha256": prepared["request_sha256"],
        "manifest": str(manifest),
        "manifest_sha256": sha256_file(manifest),
        "dry_run_journal": str(dry_run),
        "dry_run_journal_sha256": sha256_file(dry_run),
    }
    atomic_write(destination, receipt)
    return receipt


def validate_apply_request(repo: Path, path: Path) -> dict[str, Any]:
    request = load_json(path, label="apply request")
    expected = {
        "schema_version",
        "preparation_receipt",
        "preparation_receipt_sha256",
        "apply_journal",
        "upstream_apply_authorized",
        "upstream_apply_authorization_phrase",
    }
    if set(request) != expected or request.get("schema_version") != 1:
        raise PreparationError("invalid upstream apply request schema")
    if (
        request["upstream_apply_authorized"] is not True
        or request["upstream_apply_authorization_phrase"] != UPSTREAM_APPROVAL_PHRASE
    ):
        raise PreparationError("missing explicit regenerated dry-run approval")
    receipt_path = require_absolute_file(
        request["preparation_receipt"], label="preparation_receipt"
    )
    if request["preparation_receipt_sha256"] != sha256_file(receipt_path):
        raise PreparationError("preparation_receipt_sha256 mismatch")
    receipt = load_json(receipt_path, label="preparation receipt")
    if receipt.get("schema_version") != 1 or receipt.get("status") != "dry-run-ready":
        raise PreparationError("preparation receipt is not dry-run-ready")
    prepared_path = require_absolute_file(
        receipt.get("prepared_receipt"), label="prepared_receipt"
    )
    if receipt.get("prepared_receipt_sha256") != sha256_file(prepared_path):
        raise PreparationError("prepared receipt changed after sealing")
    (
        prepared,
        prepared_request,
        prepared_manifest,
        prepared_dry_run,
        prepared_destination,
    ) = validate_prepared_receipt(repo, prepared_path)
    manifest = require_absolute_file(receipt.get("manifest"), label="manifest")
    dry_run = require_absolute_file(
        receipt.get("dry_run_journal"), label="dry_run_journal"
    )
    if (
        receipt.get("manifest_sha256") != sha256_file(manifest)
        or receipt.get("dry_run_journal_sha256") != sha256_file(dry_run)
        or manifest != prepared_manifest
        or dry_run != prepared_dry_run
        or receipt_path != prepared_destination
        or receipt.get("request_path") != str(prepared_request)
        or receipt.get("request_sha256") != prepared["request_sha256"]
    ):
        raise PreparationError("sealed regenerated package changed after approval")
    apply_journal = Path(str(request["apply_journal"]))
    if (
        not apply_journal.is_absolute()
        or apply_journal.parent.resolve() != receipt_path.parent.resolve()
        or apply_journal.is_symlink()
        or (apply_journal.exists() and not apply_journal.is_file())
    ):
        raise PreparationError(
            "apply_journal must be a regular file path in the sealed output directory"
        )
    dry_payload = load_json(dry_run, label="dry-run journal")
    artifact_paths = {
        Path(value).resolve()
        for record in [*dry_payload.get("layers", []), dry_payload.get("integration_pr", {})]
        if isinstance(record, dict)
        for field in ("source_body", "rendered_title", "rendered_body")
        for value in [record.get(field)]
        if isinstance(value, str)
    }
    paths = {
        path.resolve(),
        receipt_path.resolve(),
        prepared_path.resolve(),
        manifest.resolve(),
        dry_run.resolve(),
        apply_journal.resolve(),
    }
    if len(paths) != 6 or apply_journal.resolve() in artifact_paths:
        raise PreparationError("apply request artifact paths must be distinct")
    ensure_artifact_directory(repo, apply_journal.parent)
    return {
        "manifest": str(manifest),
        "dry_run_journal": str(dry_run),
        "apply_journal": str(apply_journal),
        "apply_request": str(path.resolve()),
        "apply_request_sha256": sha256_file(path),
        "preparation_receipt": str(receipt_path),
        "preparation_receipt_sha256": sha256_file(receipt_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--mode", choices=("prepare", "seal", "validate-apply"), required=True)
    args = parser.parse_args()
    try:
        if args.mode == "prepare":
            result = prepare(args.repo.resolve(), args.artifact.resolve())
        elif args.mode == "seal":
            result = seal(args.repo.resolve(), args.artifact.resolve())
        else:
            result = validate_apply_request(args.repo.resolve(), args.artifact.resolve())
    except PreparationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
