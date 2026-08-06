#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Validate, render, and idempotently submit a reviewer-friendly GitHub PR stack."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

MARKER = "<!-- bmad-stack-navigation:v1 -->"
FALLBACK_TEMPLATE_SOURCE = "bmad-submit-prs:pr-111-fallback"
ORIGIN_REVIEW_APPROVAL_PHRASE = "approve origin review for upstream submission"
UPSTREAM_APPLY_APPROVAL_PHRASE = "approve regenerated upstream dry run"
FALLBACK_HEADINGS = (
    "## Summary",
    "## Motivation and context",
    "## Changes",
    "## Testing",
    "## Risk, rollout, and compatibility",
    "## Reviewer guidance",
    "## Checklist",
)
FALLBACK_CHECKLIST_ITEMS = (
    "The change is scoped to this PR and prerequisites are identified.",
    "Tests and documentation are updated where applicable.",
    "Risks, rollout, rollback, and compatibility are addressed.",
    "No credentials, generated release artifacts, or unrelated changes are included.",
)
VERBOSE = False
COMMAND_ENV: dict[str, str] | None = None
RETRY_ATTEMPTS = 5
RETRY_BASE_DELAY_SECONDS = 2
TRANSIENT_ERROR_MARKERS = (
    "bad gateway",
    "connection refused",
    "connection reset",
    "connection timed out",
    "context deadline exceeded",
    "could not resolve host",
    "couldn't connect to server",
    "empty reply from server",
    "failed to connect",
    "http 408",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "i/o timeout",
    "tls handshake timeout",
    "remote end hung up unexpectedly",
    "unexpected eof",
)


class SubmitError(RuntimeError):
    """Submission cannot continue safely."""


def progress(stage: str, message: str) -> None:
    print(f"[{stage}] {message}", file=sys.stderr, flush=True)


def display_command(command: list[str]) -> str:
    sanitized: list[str] = []
    redact_next = False
    for argument in command:
        if redact_next:
            sanitized.append("<redacted>")
            redact_next = False
        elif argument == "--body":
            sanitized.append(argument)
            redact_next = True
        elif argument.startswith("body="):
            sanitized.append("body=<redacted>")
        else:
            sanitized.append(argument)
    return shlex.join(sanitized)


def configure_command_environment(repository: str) -> None:
    global COMMAND_ENV
    host, _, _ = split_repository(repository)
    COMMAND_ENV = os.environ.copy()
    if host and host.casefold() != "github.com":
        removed = [
            token
            for token in ("GH_TOKEN", "GITHUB_TOKEN")
            if COMMAND_ENV.pop(token, None) is not None
        ]
        if removed:
            progress(
                "auth",
                f"ignoring {', '.join(removed)} for enterprise host {host}; using "
                "GH_ENTERPRISE_TOKEN, GITHUB_ENTERPRISE_TOKEN, or the stored gh credential",
            )


def is_transient_failure(message: str) -> bool:
    normalized = message.casefold()
    return bool(re.search(r"\beof\b", normalized)) or any(
        marker in normalized for marker in TRANSIENT_ERROR_MARKERS
    )


def retry_delay(attempt: int, operation: str) -> None:
    delay = min(RETRY_BASE_DELAY_SECONDS * 2 ** (attempt - 1), 30)
    print(
        f"transient network failure during {operation}; retrying in {delay}s "
        f"({attempt + 1}/{RETRY_ATTEMPTS})",
        file=sys.stderr,
    )
    time.sleep(delay)


def run(
    command: list[str],
    cwd: Path,
    input_text: str | None = None,
    *,
    retry_transient: bool = False,
) -> str:
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        if VERBOSE:
            progress("command", display_command(command))
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            input=input_text,
            capture_output=True,
            env=COMMAND_ENV,
        )
        if not result.returncode:
            if VERBOSE:
                progress("command", f"ok: {command[0]}")
            return result.stdout.strip()
        error = result.stderr.strip() or f"{' '.join(command)} failed"
        if not retry_transient or not is_transient_failure(error) or attempt == RETRY_ATTEMPTS:
            raise SubmitError(error)
        retry_delay(attempt, command[0])
    raise AssertionError("retry loop exhausted")


def git(repo: Path, *args: str) -> str:
    retry_transient = bool(args) and args[0] in {"fetch", "ls-remote"}
    return run(["git", *args], repo, retry_transient=retry_transient)


def gh(
    repo: Path,
    repository: str,
    *args: str,
    retry_transient: bool = True,
) -> str:
    return run(["gh", *args, "--repo", repository], repo, retry_transient=retry_transient)


def gh_api(
    repo: Path,
    repository: str,
    endpoint: str,
    *args: str,
    retry_transient: bool = True,
) -> str:
    host, _, _ = split_repository(repository)
    command = ["gh", "api"]
    if host:
        command.extend(["--hostname", host])
    command.extend([endpoint, *args])
    return run(command, repo, retry_transient=retry_transient)


def resolve(repo: Path, revision: str) -> str:
    return git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}")


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        env=COMMAND_ENV,
    )
    return result.returncode == 0


def split_repository(value: str) -> tuple[str | None, str, str]:
    parts = value.rstrip("/").removesuffix(".git").split("/")
    if len(parts) == 2:
        return None, parts[0], parts[1]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    raise SubmitError("repository must be OWNER/REPO or HOST/OWNER/REPO")


def parse_remote(value: str) -> tuple[str | None, str, str]:
    if value.startswith("git@"):
        match = re.fullmatch(r"git@([^:]+):([^/]+)/(.+)", value)
        if not match:
            raise SubmitError(f"cannot parse remote URL: {value}")
        return match.group(1), match.group(2), match.group(3).removesuffix(".git")
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise SubmitError(f"cannot parse remote URL: {value}") from exc
    standard_ports = {"https": 443, "http": 80, "ssh": 22, "git": 9418}
    if port is not None and port != standard_ports.get(parsed.scheme):
        raise SubmitError(f"remote URL uses a nonstandard port: {value}")
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if len(parts) != 2:
        raise SubmitError(f"cannot parse remote URL: {value}")
    return parsed.hostname, parts[0], parts[1]


def validate_remote_urls(
    repo: Path,
    remote: str,
    repository: str,
    *,
    role: str = "declared",
) -> tuple[str, str, str]:
    expected_host, expected_owner, expected_name = split_repository(repository)
    expected = (
        (expected_host or "github.com").casefold(),
        expected_owner.casefold(),
        expected_name.casefold(),
    )
    fetch_urls = git(repo, "remote", "get-url", "--all", remote).splitlines()
    push_urls = git(repo, "remote", "get-url", "--push", "--all", remote).splitlines()
    if not fetch_urls or not push_urls:
        raise SubmitError(f"remote {remote} must have fetch and push URLs")
    for value in (*fetch_urls, *push_urls):
        host, owner, name = parse_remote(value)
        actual = ((host or "github.com").casefold(), owner.casefold(), name.casefold())
        if actual != expected:
            raise SubmitError(
                f"all effective {remote} fetch and push URLs must resolve to "
                f"the {role} repository"
            )
    return expected_host or "github.com", expected_owner, expected_name


def stacked_title(
    layer: dict[str, Any],
    index: int,
    count: int,
    stack_label: str,
) -> str:
    prefix, separator, subject = layer["title"].partition(":")
    if not separator or not prefix.strip() or not subject.strip():
        raise SubmitError(f"layer title must use a conventional prefix: {layer['title']}")
    return (
        f"{prefix.strip()}(stacked-pr: {stack_label} [{index + 1}/{count}]): "
        f"{subject.strip()}"
    )


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubmitError(f"cannot read manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise SubmitError("manifest must be a JSON object")
    if manifest.get("schema_version") != 2 or not manifest.get("layers"):
        raise SubmitError("manifest requires schema_version 2 and non-empty layers")
    return manifest


def visible_markdown_lines(content: str) -> list[str]:
    visible: list[str] = []
    fence: tuple[str, int] | None = None
    in_comment = False
    for line in content.splitlines():
        stripped = line.lstrip()
        if fence is not None:
            if re.fullmatch(
                rf"\s*{re.escape(fence[0])}{{{fence[1]},}}\s*",
                line,
            ):
                fence = None
            continue
        opener = re.match(r"^(`{3,}|~{3,})(?:\s*[^`~]*)?$", stripped)
        if opener:
            candidate = opener.group(1)
            fence = (candidate[0], len(candidate))
            continue
        remainder = line
        while remainder:
            if in_comment:
                _before, separator, remainder = remainder.partition("-->")
                if not separator:
                    break
                in_comment = False
                continue
            before, separator, after = remainder.partition("<!--")
            if before:
                visible.append(before)
            if not separator:
                break
            in_comment = True
            remainder = after
    return visible


def markdown_headings(content: str, *, level: int | None = 2) -> list[str]:
    headings: list[str] = []
    marker = "#" * level if level is not None else None
    for line in visible_markdown_lines(content):
        if (
            (marker is not None and line.startswith(f"{marker} "))
            or (marker is None and re.match(r"^#{1,6} \S", line))
        ):
            headings.append(line.rstrip())
    return headings


def markdown_heading_texts(content: str) -> list[str]:
    headings: list[str] = []
    previous: str | None = None
    for line in visible_markdown_lines(content):
        atx = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if atx:
            headings.append(atx.group(1))
        elif previous and re.match(r"^\s*(?:=+|-+)\s*$", line):
            headings.append(previous.strip())
        previous = line if line.strip() else None
    return headings


def markdown_checklist_items(content: str) -> list[str]:
    return [
        re.sub(r"^\s*[-*+]\s+\[[ xX]\]\s*", "", line).strip()
        for line in visible_markdown_lines(content)
        if not line.startswith(("    ", "\t"))
        and re.match(r"^\s*[-*+]\s+\[[ xX]\]\s+\S", line)
    ]


def validate_fallback_body(
    content: str,
    *,
    label: str,
    allow_appendix_headings: bool = False,
) -> None:
    headings = markdown_headings(content)
    heading_texts = markdown_heading_texts(content)
    for heading in FALLBACK_HEADINGS:
        if headings.count(heading) != 1:
            raise SubmitError(
                f"{label} must contain fallback heading {heading!r} exactly once"
            )
        if heading_texts.count(heading.removeprefix("## ")) != 1:
            raise SubmitError(
                f"{label} must not duplicate fallback heading {heading!r} "
                "with another Markdown heading style"
            )
    positions = [headings.index(heading) for heading in FALLBACK_HEADINGS]
    if positions != sorted(positions):
        raise SubmitError(f"{label} fallback headings are out of canonical PR #111 order")
    if allow_appendix_headings:
        if headings[: len(FALLBACK_HEADINGS)] != list(FALLBACK_HEADINGS):
            raise SubmitError(
                f"{label} must place canonical PR #111 headings before generated appendices"
            )
    elif headings != list(FALLBACK_HEADINGS):
        raise SubmitError(
            f"{label} must use exactly the seven canonical PR #111 level-two sections"
        )
    checklist_items = markdown_checklist_items(content)
    for item in FALLBACK_CHECKLIST_ITEMS:
        if checklist_items.count(item) != 1:
            raise SubmitError(
                f"{label} must contain fallback checklist item {item!r} exactly once"
            )
    visible = visible_markdown_lines(content)
    checklist_start = visible.index("## Checklist") + 1
    checklist_section: list[str] = []
    for line in visible[checklist_start:]:
        if line.startswith("## "):
            break
        checklist_section.append(line)
    section_items = markdown_checklist_items("\n".join(checklist_section))
    if section_items != list(FALLBACK_CHECKLIST_ITEMS):
        raise SubmitError(
            f"{label} must keep the canonical fallback checklist under '## Checklist'"
        )


def repository_template_paths(repo: Path, base_sha: str) -> list[str]:
    paths = git(repo, "ls-tree", "-r", "--name-only", base_sha).splitlines()
    fixed = {
        ".github/pull_request_template.md",
        "docs/pull_request_template.md",
        "pull_request_template.md",
    }
    return sorted(
        path
        for path in paths
        if path.casefold() in fixed
        or (
            path.casefold().startswith(".github/pull_request_template/")
            and Path(path).suffix.casefold() in {".md", ".markdown"}
        )
    )


def validate_template_source(template_source: str, repository_templates: list[str]) -> None:
    if template_source == FALLBACK_TEMPLATE_SOURCE:
        if repository_templates:
            raise SubmitError(
                "fallback template is forbidden while the target repository provides "
                f"an applicable template: {repository_templates}"
            )
    elif template_source not in repository_templates:
        raise SubmitError(
            "template_source must name an applicable template from the immutable target base"
        )


def validate_repository_template_body(
    repo: Path,
    base_sha: str,
    template_source: str,
    body_content: str,
    *,
    label: str,
) -> None:
    template = git(repo, "show", f"{base_sha}:{template_source}")
    required_headings = markdown_heading_texts(template)
    body_headings = markdown_heading_texts(body_content)
    position = -1
    for heading in required_headings:
        try:
            position = body_headings.index(heading, position + 1)
        except ValueError as exc:
            raise SubmitError(
                f"{label} does not preserve repository template heading {heading!r}"
            ) from exc
    checklist_items = markdown_checklist_items(template)
    body_checklist_items = markdown_checklist_items(body_content)
    for item in checklist_items:
        if item not in body_checklist_items:
            raise SubmitError(
                f"{label} does not preserve repository template checklist item {item!r}"
            )
    meaningful = [
        line.strip()
        for line in visible_markdown_lines(template)
        if line.strip()
        and not re.match(r"^#{1,6}\s+", line)
        and not re.match(r"^\s*[-*+]\s+\[[ xX]\]\s+", line)
        and not re.match(r"^\s*(?:=+|-+)\s*$", line)
    ]
    body_visible = "\n".join(visible_markdown_lines(body_content))
    for line in meaningful:
        if line not in body_visible:
            raise SubmitError(
                f"{label} does not preserve repository template prose {line!r}"
            )


def origin_review_required(manifest: dict[str, Any]) -> bool:
    """Whether the fork-hosted origin-preview audit ceremony applies.

    Origin review (and the downstream regenerate/reseal apply-request ceremony in
    prepare_upstream_submission.py) exists to bridge a two-repository gap: a fork-hosted
    preview stack is audited first, then a *second*, regenerated package binding that audit
    is what actually gets applied to the real upstream target. When evidence_remote/
    evidence_repository already resolve to the exact target repository (single-repo,
    direct-to-upstream submission, no fork involved), there is no preview repository to
    audit and no gap to bridge: the one canonical --dry-run already runs against the real
    target, so --approved-dry-run-journal alone (binding the exact reviewed manifest/body/
    title bytes) is a complete, adequate human-approval gate on its own.
    """
    return bool(manifest["_evidence_cross_repository"])


def validate_origin_review_approval(
    repo: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    layers: list[dict[str, Any]],
) -> None:
    review = manifest.get("origin_review")
    if not isinstance(review, dict) or set(review) != {
        "audit_receipt",
        "audit_receipt_sha256",
        "pre_review_manifest_sha256",
        "pre_review_journal_sha256",
        "approved",
        "approval_phrase",
    }:
        raise SubmitError(
            "origin_review must bind the exact audit receipt, pre-review package hashes, "
            "approved flag, and approval phrase"
        )
    if not isinstance(review["audit_receipt"], str):
        raise SubmitError("origin review audit_receipt must be an absolute path")
    receipt_path = Path(review["audit_receipt"])
    if not receipt_path.is_absolute() or not receipt_path.is_file():
        raise SubmitError(f"origin review audit receipt is missing: {receipt_path}")
    expected_hash = review["audit_receipt_sha256"]
    if not isinstance(expected_hash, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_hash
    ):
        raise SubmitError("origin review audit receipt hash does not match")
    if (
        review["approved"] is not True
        or review["approval_phrase"] != ORIGIN_REVIEW_APPROVAL_PHRASE
    ):
        raise SubmitError(
            "upstream submission requires explicit approval of the audited origin review"
        )
    try:
        receipt_bytes = receipt_path.read_bytes()
        if hashlib.sha256(receipt_bytes).hexdigest() != expected_hash:
            raise SubmitError("origin review audit receipt hash does not match")
        receipt = json.loads(receipt_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise SubmitError(f"cannot read origin review audit receipt: {exc}") from exc
    if not isinstance(receipt, dict):
        raise SubmitError("origin review audit receipt must be a JSON object")
    audit_fields = {
        "schema_version",
        "status",
        "request_path",
        "input_hashes",
        "stage_receipt",
        "stage_receipt_sha256",
        "repository",
        "host",
        "namespace",
        "base_sha",
        "projection",
        "live_refs",
        "pull_requests",
        "stage_pull_requests",
    }
    if (
        set(receipt) != audit_fields
        or type(receipt.get("schema_version")) is not int
        or receipt["schema_version"] != 1
    ):
        raise SubmitError("origin review audit receipt does not match the canonical schema")
    if receipt.get("status") != "audited":
        raise SubmitError("origin review receipt is not a completed live audit")
    if not isinstance(receipt["request_path"], str) or not isinstance(
        receipt["stage_receipt"], str
    ):
        raise SubmitError("origin review audit receipt paths must be strings")
    request_path = Path(receipt["request_path"])
    stage_path = Path(receipt["stage_receipt"])
    if (
        not request_path.is_absolute()
        or not request_path.is_file()
        or not stage_path.is_absolute()
        or not stage_path.is_file()
        or sha256_file(stage_path) != receipt["stage_receipt_sha256"]
    ):
        raise SubmitError("origin review audit does not bind a valid stage receipt and request")
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubmitError(f"cannot read origin review request: {exc}") from exc
    if not isinstance(request, dict):
        raise SubmitError("origin review request must be a JSON object")
    input_hashes = receipt["input_hashes"]
    expected_input_hashes = {
        "request",
        "canonical_accepted_state",
        "submission_manifest",
        "submission_journal",
        "approved_spec",
    }
    if not isinstance(input_hashes, dict) or set(input_hashes) != expected_input_hashes:
        raise SubmitError("origin review audit input_hashes are not canonical")
    if sha256_file(request_path) != input_hashes["request"]:
        raise SubmitError("origin review request changed after audit")
    for request_field, hash_field in (
        ("canonical_accepted_state", "canonical_accepted_state"),
        ("approved_spec", "approved_spec"),
    ):
        value = request.get(request_field)
        if request_field == "approved_spec" and isinstance(value, dict):
            value = value.get("path")
        artifact = Path(value) if isinstance(value, str) else None
        if (
            artifact is None
            or not artifact.is_absolute()
            or not artifact.is_file()
            or sha256_file(artifact) != input_hashes[hash_field]
        ):
            raise SubmitError(f"origin review {request_field} changed after audit")
    accepted_state_path = Path(str(request["canonical_accepted_state"]))
    try:
        accepted_state = json.loads(accepted_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubmitError(f"cannot read canonical accepted state: {exc}") from exc
    pr_ready_path_value = (
        accepted_state.get("prReadyArtifact")
        if isinstance(accepted_state, dict)
        else None
    )
    pr_ready_path = (
        Path(pr_ready_path_value) if isinstance(pr_ready_path_value, str) else None
    )
    if (
        pr_ready_path is None
        or not pr_ready_path.is_absolute()
        or not pr_ready_path.is_file()
    ):
        raise SubmitError("canonical accepted state lacks its PR-ready receipt")
    try:
        pr_ready = json.loads(pr_ready_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubmitError(f"cannot read PR-ready receipt: {exc}") from exc
    records = pr_ready.get("layers") if isinstance(pr_ready, dict) else None
    if (
        not isinstance(pr_ready, dict)
        or pr_ready.get("status") != "applied"
        or pr_ready.get("remote") != manifest["publish_remote"]
        or not isinstance(records, list)
        or len(records) != len(layers)
    ):
        raise SubmitError("PR-ready receipt does not match the approved component stack")
    for index, (record, layer) in enumerate(zip(records, layers), start=1):
        if not isinstance(record, dict):
            raise SubmitError(f"PR-ready receipt layer {index} is malformed")
        source = record.get("source")
        source_tip = record.get("source_tip")
        target = record.get("target")
        new_tip = record.get("new_tip")
        if (
            not isinstance(source, str)
            or not isinstance(source_tip, str)
            or not re.fullmatch(r"[0-9a-f]{40}", source_tip)
            or target != layer["remote_branch"]
            or new_tip != layer["_tip"]
            or remote_sha(repo, manifest["evidence_remote"], source) != source_tip
            or remote_sha(repo, manifest["target_remote"], source) is not None
            or remote_sha(repo, manifest["publish_remote"], target) != new_tip
            or remote_sha(repo, manifest["evidence_remote"], target) is not None
        ):
            raise SubmitError(
                f"source/PR-ready remote placement changed for component {index}"
            )
    pre_manifest = Path(str(request.get("submission_manifest", "")))
    pre_journal = Path(str(request.get("submission_journal", "")))
    if (
        not pre_manifest.is_absolute()
        or not pre_manifest.is_file()
        or not pre_journal.is_absolute()
        or not pre_journal.is_file()
        or sha256_file(pre_manifest) != input_hashes.get("submission_manifest")
        or sha256_file(pre_journal) != input_hashes.get("submission_journal")
        or review["pre_review_manifest_sha256"] != input_hashes.get("submission_manifest")
        or review["pre_review_journal_sha256"] != input_hashes.get("submission_journal")
    ):
        raise SubmitError("origin review audit does not bind the exact pre-review package")
    try:
        pre_journal_payload = json.loads(pre_journal.read_text(encoding="utf-8"))
        stage_receipt = json.loads(stage_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubmitError(f"cannot read origin review receipt chain: {exc}") from exc
    if not isinstance(pre_journal_payload, dict) or not isinstance(stage_receipt, dict):
        raise SubmitError("origin review receipt chain artifacts must be JSON objects")
    stage_fields = {
        "schema_version",
        "status",
        "request_path",
        "input_hashes",
        "staging_progress",
        "staging_progress_sha256",
        "repository",
        "host",
        "origin_remote",
        "base_policy",
        "namespace",
        "base_sha",
        "evidence_contract",
        "heads",
        "pull_requests",
    }
    if (
        set(stage_receipt) != stage_fields
        or type(stage_receipt.get("schema_version")) is not int
        or stage_receipt["schema_version"] != 1
        or stage_receipt.get("status") != "staged"
        or stage_receipt.get("request_path") != str(request_path)
        or stage_receipt.get("input_hashes") != input_hashes
        or stage_receipt.get("repository") != receipt["repository"]
        or stage_receipt.get("namespace") != receipt["namespace"]
        or stage_receipt.get("base_sha") != receipt["base_sha"]
        or stage_receipt.get("pull_requests") != receipt["stage_pull_requests"]
        or stage_receipt.get("pull_requests") != receipt["pull_requests"]
        or stage_receipt.get("origin_remote") != manifest["evidence_remote"]
        or stage_receipt.get("base_policy") != "require-equal"
        or stage_receipt.get("evidence_contract")
        != {
            "permanent_draft": True,
            "merge": "prohibited",
            "base": manifest["default_base"],
        }
    ):
        raise SubmitError("origin review stage receipt does not match the canonical audit chain")
    staging_progress = stage_receipt.get("staging_progress")
    if not isinstance(staging_progress, str):
        raise SubmitError("origin review stage receipt lacks staging progress")
    staging_progress_path = Path(staging_progress)
    if (
        not staging_progress_path.is_absolute()
        or not staging_progress_path.is_file()
        or sha256_file(staging_progress_path)
        != stage_receipt.get("staging_progress_sha256")
    ):
        raise SubmitError("origin review staging progress changed after audit")
    if (
        manifest_path.resolve() == pre_manifest.resolve()
        or sha256_file(manifest_path) == input_hashes["submission_manifest"]
    ):
        raise SubmitError(
            "upstream submission manifest must be regenerated after origin review"
        )
    prior_bodies = {
        Path(str(item["source_body"])).resolve()
        for item in pre_journal_payload.get("layers", [])
        if isinstance(item, dict) and isinstance(item.get("source_body"), str)
    }
    if any(layer["_body_file"].resolve() in prior_bodies for layer in layers):
        raise SubmitError(
            "upstream submission body files must be regenerated after origin review"
        )
    try:
        receipt_repository = tuple(
            (part or "").casefold()
            for part in split_repository(str(receipt.get("repository", "")))
        )
        evidence_repository = tuple(
            (part or "").casefold()
            for part in split_repository(manifest["_evidence_repository"])
        )
    except SubmitError as exc:
        raise SubmitError("origin review receipt repository is invalid") from exc
    if receipt_repository != evidence_repository:
        raise SubmitError("origin review receipt repository does not match evidence_remote")
    if receipt.get("base_sha") != manifest["base_sha"]:
        raise SubmitError("origin review base SHA does not match the upstream package")
    projection = receipt.get("projection")
    if not isinstance(projection, dict):
        raise SubmitError("origin review audit projection must be an object")
    heads = projection.get("heads")
    pull_requests = receipt.get("pull_requests")
    if (
        not isinstance(heads, list)
        or not isinstance(pull_requests, list)
        or len(heads) != len(layers) + 1
        or len(pull_requests) != len(heads)
    ):
        raise SubmitError(
            "origin review receipt must contain every component plus one evidence PR"
        )
    namespace = receipt.get("namespace")
    if not isinstance(namespace, str) or not namespace.startswith("bmad-review/"):
        raise SubmitError("origin review namespace is invalid")
    head_branches = [head.get("branch") for head in heads if isinstance(head, dict)]
    pr_numbers = [
        pull_request.get("number")
        for pull_request in pull_requests
        if isinstance(pull_request, dict)
    ]
    if (
        len(head_branches) != len(set(head_branches))
        or len(pr_numbers) != len(set(pr_numbers))
        or any(type(number) is not int for number in pr_numbers)
    ):
        raise SubmitError("origin review heads and pull requests must be unique")
    for index, (layer, head, pull_request) in enumerate(
        zip(layers, heads, pull_requests), start=1
    ):
        if not isinstance(head, dict) or not isinstance(pull_request, dict):
            raise SubmitError(f"origin review component {index} must be an object")
        expected_base = manifest["default_base"] if index == 1 else heads[index - 2].get("branch")
        expected_base_sha = manifest["base_sha"] if index == 1 else layers[index - 2]["_tip"]
        if (
            head.get("role") != "component"
            or type(head.get("position")) is not int
            or head["position"] != index
            or not str(head.get("branch", "")).startswith(f"{namespace}/")
            or head.get("sha") != layer["_tip"]
            or head.get("base") != expected_base
            or head.get("base_sha") != expected_base_sha
            or pull_request.get("role") != "component"
            or type(pull_request.get("position")) is not int
            or pull_request["position"] != index
            or pull_request.get("state") != "OPEN"
            or pull_request.get("draft") is not True
            or pull_request.get("head_sha") != layer["_tip"]
        ):
            raise SubmitError(
                f"origin review receipt component {index} does not match the current stack"
            )
    evidence_head = heads[-1]
    evidence_pr = pull_requests[-1]
    if not isinstance(evidence_head, dict) or not isinstance(evidence_pr, dict):
        raise SubmitError("origin review evidence projection must contain objects")
    evidence_commit = manifest["integration_evidence"]["_commit"]
    if (
        evidence_head.get("role") != "evidence"
        or not str(evidence_head.get("branch", "")).startswith(f"{namespace}/")
        or evidence_head.get("sha") != evidence_commit
        or evidence_head.get("base") != manifest["default_base"]
        or evidence_head.get("base_sha") != manifest["base_sha"]
        or evidence_pr.get("role") != "evidence"
        or evidence_pr.get("state") != "OPEN"
        or evidence_pr.get("draft") is not True
        or evidence_pr.get("head_sha") != evidence_commit
    ):
        raise SubmitError(
            "origin review receipt lacks the exact permanent-draft integration evidence PR"
        )
    expected_live_refs = [
        {"branch": head["branch"], "sha": head["sha"]} for head in heads
    ]
    if receipt.get("live_refs") != expected_live_refs:
        raise SubmitError("origin review audit live refs do not match its projection")
    stage_heads = stage_receipt.get("heads")
    if not isinstance(stage_heads, list) or len(stage_heads) != len(heads):
        raise SubmitError("origin review stage heads do not match the audit projection")
    for staged, audited in zip(stage_heads, heads):
        if (
            not isinstance(staged, dict)
            or not isinstance(audited, dict)
            or {key: value for key, value in staged.items() if key != "observed_old_sha"}
            != {key: value for key, value in audited.items() if key != "observed_old_sha"}
        ):
            raise SubmitError("origin review stage heads changed before audit")
    for head, pull_request in zip(heads, pull_requests):
        existing = pull_requests_for_head(
            repo,
            manifest["_evidence_repository"],
            manifest["_evidence_owner"],
            head["branch"],
        )
        if len(existing) != 1:
            raise SubmitError(
                f"origin review head {head['branch']!r} must have exactly one live PR"
            )
        live = existing[0]
        if (
            live["number"] != pull_request.get("number")
            or live["url"] != pull_request.get("url")
            or live["state"] != "OPEN"
            or live["isDraft"] is not True
            or live["title"] != pull_request.get("title")
            or live["body_sha256"] != pull_request.get("body_sha256")
            or live["headRefName"] != head.get("branch")
            or live["headRefOid"] != head.get("sha")
            or live["baseRefName"] != head.get("base")
            or live["baseRefOid"] != head.get("base_sha")
            or live["headRepositoryOwner"].casefold()
            != manifest["_evidence_owner"].casefold()
        ):
            raise SubmitError(
                f"live origin review PR #{pull_request.get('number')} changed after audit"
            )
        if head.get("role") == "evidence" and "DO NOT MERGE" not in live["body"]:
            raise SubmitError("origin integration proof PR lost its DO NOT MERGE contract")
    manifest["_origin_review"] = {
        "audit_receipt": str(receipt_path.resolve()),
        "audit_receipt_sha256": expected_hash,
        "pre_review_manifest_sha256": review["pre_review_manifest_sha256"],
        "pre_review_journal_sha256": review["pre_review_journal_sha256"],
        "approval_phrase": review["approval_phrase"],
    }


def validate_component_branch_names(layer: dict[str, Any]) -> None:
    if not layer["branch"].endswith("-pr-ready"):
        raise SubmitError(f"layer branch is not PR-ready: {layer['branch']}")
    if layer["remote_branch"] != layer["branch"]:
        raise SubmitError(
            f"component remote_branch must exactly match its PR-ready branch: "
            f"{layer['branch']}"
        )


def validate_release_branch_placement(
    repo: Path,
    manifest: dict[str, Any],
    layers: list[dict[str, Any]],
) -> None:
    if not manifest["_evidence_cross_repository"]:
        return
    for layer in layers:
        if remote_sha(repo, manifest["evidence_remote"], layer["remote_branch"]) is not None:
            raise SubmitError(
                f"PR-ready component head must not be published on evidence_remote: "
                f"{layer['remote_branch']}"
            )
    if (
        remote_sha(
            repo,
            manifest["publish_remote"],
            manifest["integration_evidence"]["branch"],
        )
        is not None
    ):
        raise SubmitError(
            "integration evidence branch must not be published on the component target repository"
        )


def load_manual_links(
    path: Path | None,
    layer_count: int,
    repository: str,
) -> dict[int, dict[str, Any]]:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubmitError(f"cannot read manual PR links: {exc}") from exc
    if not isinstance(payload, dict):
        raise SubmitError("manual PR links must be a JSON object")
    links: dict[int, dict[str, Any]] = {}
    for item in payload.get("prs", []):
        position = item.get("position")
        number = item.get("number")
        url = item.get("url")
        expected_url = (
            f"{repository_web_url(repository)}/pull/{number}"
            if isinstance(number, int)
            else ""
        )
        if (
            not isinstance(position, int)
            or not 1 <= position <= layer_count
            or not isinstance(number, int)
            or number < 1
            or not isinstance(url, str)
            or url.rstrip("/").casefold() != expected_url.casefold()
        ):
            raise SubmitError("manual PR links require valid position, number, and URL fields")
        links[position - 1] = {"number": number, "url": url}
    positions = sorted(links)
    if positions and positions != list(range(positions[-1] + 1)):
        raise SubmitError("manual PR links must form a contiguous prefix from position 1")
    return links


def require_count(value: Any, field: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < int(positive):
        qualifier = "positive" if positive else "non-negative"
        raise SubmitError(f"integration_evidence {field} must be a {qualifier} integer")
    return value


def validate_integration_evidence(
    repo: Path,
    manifest: dict[str, Any],
    layers: list[dict[str, Any]],
) -> None:
    evidence = manifest.get("integration_evidence")
    if not isinstance(evidence, dict):
        raise SubmitError("manifest missing integration_evidence")
    for field in ("branch", "commit", "report_path", "test_command"):
        if not isinstance(evidence.get(field), str) or not evidence[field].strip():
            raise SubmitError(f"integration_evidence missing {field}")

    report_path = Path(evidence["report_path"])
    if report_path.is_absolute() or ".." in report_path.parts:
        raise SubmitError("integration_evidence report_path must be repository-relative")

    commit = resolve(repo, evidence["commit"])
    if resolve(repo, evidence["branch"]) != commit:
        raise SubmitError("integration evidence branch drifted from its recorded commit")
    if not is_ancestor(repo, layers[-1]["_tip"], commit):
        raise SubmitError("integration evidence commit does not descend from the final stack layer")
    report = git(repo, "show", f"{commit}:{evidence['report_path']}")
    if remote_sha(repo, manifest["evidence_remote"], evidence["branch"]) != commit:
        raise SubmitError(
            "integration evidence branch is not published on evidence_remote "
            "at its recorded commit"
        )

    tests = evidence.get("tests")
    if not isinstance(tests, dict):
        raise SubmitError("integration_evidence missing tests")
    require_count(tests.get("passed"), "tests.passed", positive=True)
    require_count(tests.get("skipped"), "tests.skipped")
    require_count(tests.get("warnings"), "tests.warnings")

    builds = evidence.get("builds")
    if not isinstance(builds, list) or not builds:
        raise SubmitError("integration_evidence builds must be a non-empty list")
    for index, build in enumerate(builds, start=1):
        if (
            not isinstance(build, dict)
            or not isinstance(build.get("artifact"), str)
            or not build["artifact"].strip()
        ):
            raise SubmitError(f"integration_evidence build {index} missing artifact")
        if build.get("status") != "passed":
            raise SubmitError(f"integration_evidence build {index} did not pass")
        if not re.fullmatch(r"[0-9a-f]{64}", str(build.get("sha256", ""))):
            raise SubmitError(f"integration_evidence build {index} requires a SHA-256 digest")

    safety = evidence.get("partial_merge_safety")
    if not isinstance(safety, dict):
        raise SubmitError("integration_evidence missing partial_merge_safety")
    validated = require_count(
        safety.get("validated_prefixes"),
        "partial_merge_safety.validated_prefixes",
        positive=True,
    )
    total = require_count(
        safety.get("total_prefixes"),
        "partial_merge_safety.total_prefixes",
        positive=True,
    )
    if validated != total or total != len(layers):
        raise SubmitError("partial-merge safety must validate every submitted stack prefix")
    prefix_tips = safety.get("prefix_tips")
    if prefix_tips != [layer["_tip"] for layer in layers]:
        raise SubmitError("partial-merge safety prefix tips must exactly match the submitted stack")
    feature_flag = safety.get("feature_flag")
    if not isinstance(feature_flag, dict):
        raise SubmitError("partial_merge_safety missing feature_flag")
    for field in ("name", "safe_default", "disabled_behavior"):
        if not isinstance(feature_flag.get(field), str) or not feature_flag[field].strip():
            raise SubmitError(f"partial_merge_safety feature_flag missing {field}")
    if feature_flag["safe_default"].casefold() != "disabled":
        raise SubmitError("partial_merge_safety feature flag must default to disabled")

    report_lines = {line.strip() for line in report.splitlines()}
    required_report_lines = (
        f"- Test command: `{evidence['test_command']}`",
        f"- Final tests: {tests['passed']} passed, {tests['skipped']} skipped, {tests['warnings']} warnings",
        f"- Prefix coverage: {validated}/{total}",
        f"- Feature flag: `{feature_flag['name']}`; default: `{feature_flag['safe_default']}`",
        f"- Disabled behavior: {feature_flag['disabled_behavior']}",
    )
    prefix_rows_match = all(
        len(re.findall(
            rf"(?m)^\|\s*`{re.escape(layer['remote_branch'])}`\s*\|\s*`{re.escape(tip)}`\s*\|\s*passed\s*\|\s*[1-9]\d* passed, \d+ skipped, \d+ warnings\s*\|$",
            report,
        )) == 1
        for layer, tip in zip(layers, prefix_tips)
    )
    build_rows_match = all(
        len(re.findall(
            rf"(?m)^\|\s*`{re.escape(build['artifact'])}`\s*\|\s*passed\s*\|\s*`{build['sha256']}`\s*\|$",
            report,
        )) == 1
        for build in builds
    )
    contradictory_result = re.search(r"(?im)(?:\d+\s+(?:failed|errors?)|^\|.*\|\s*(?:skipped|cancelled|timed out)\s*\|)", report)
    if (
        any(line not in report_lines for line in required_report_lines)
        or not prefix_rows_match
        or not build_rows_match
        or contradictory_result
    ):
        raise SubmitError("committed integration report does not substantiate the manifest evidence")

    web_url = repository_web_url(manifest["_evidence_repository"])
    evidence["_commit"] = commit
    evidence["_branch_url"] = f"{web_url}/tree/{quote(evidence['branch'], safe='/')}"
    evidence["_report_url"] = (
        f"{web_url}/blob/{commit}/{quote(evidence['report_path'], safe='/')}"
    )


def validate(repo: Path, path: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if git(repo, "status", "--porcelain"):
        raise SubmitError("worktree must be clean")
    for field in (
        "repository",
        "target_remote",
        "publish_remote",
        "evidence_remote",
        "evidence_repository",
        "default_base",
        "base_sha",
        "feature_name",
        "feature_summary",
        "stack_label",
    ):
        if not manifest.get(field):
            raise SubmitError(f"manifest missing {field}")
    if not isinstance(manifest.get("draft"), bool):
        raise SubmitError("manifest draft must be a JSON boolean")
    if not isinstance(manifest.get("template_source"), str) or not manifest["template_source"]:
        raise SubmitError("manifest missing template_source")
    if (
        len(manifest["stack_label"]) > 24
        or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+){0,3}", manifest["stack_label"])
    ):
        raise SubmitError("stack_label must be 1-4 lowercase keywords, at most 24 characters")
    target_host, target_owner, target_name = validate_remote_urls(
        repo,
        manifest["target_remote"],
        manifest["repository"],
        role="canonical target",
    )
    publish_host, publish_owner, publish_name = validate_remote_urls(
        repo,
        manifest["publish_remote"],
        manifest["repository"],
        role="component publish",
    )
    manifest["_head_owner"] = publish_owner
    manifest["_head_repository"] = (
        f"{publish_host}/{publish_owner}/{publish_name}"
        if publish_host
        else f"{publish_owner}/{publish_name}"
    )
    manifest["_cross_repository"] = False
    if (
        publish_host.casefold(),
        publish_owner.casefold(),
        publish_name.casefold(),
    ) != (
        target_host.casefold(),
        target_owner.casefold(),
        target_name.casefold(),
    ):
        raise SubmitError(
            "component publish_remote must resolve to the target repository; "
            "cross-repository component heads are forbidden"
        )
    evidence_host, evidence_owner, evidence_name = validate_remote_urls(
        repo,
        manifest["evidence_remote"],
        manifest["evidence_repository"],
        role="integration evidence",
    )
    manifest["_evidence_owner"] = evidence_owner
    manifest["_evidence_repository"] = (
        f"{evidence_host}/{evidence_owner}/{evidence_name}"
        if evidence_host
        else f"{evidence_owner}/{evidence_name}"
    )
    if evidence_host.casefold() != target_host.casefold():
        raise SubmitError("evidence_remote must use the target repository's GitHub host")
    manifest["_evidence_cross_repository"] = (
        evidence_owner.casefold(),
        evidence_name.casefold(),
    ) != (
        target_owner.casefold(),
        target_name.casefold(),
    )

    targets: set[str] = set()
    prior: str | None = None
    layers = manifest["layers"]
    stack_base = resolve(repo, f"refs/remotes/{manifest['target_remote']}/{manifest['default_base']}")
    if not re.fullmatch(r"[0-9a-f]{40}", manifest["base_sha"]):
        raise SubmitError("base_sha must be a full 40-character commit SHA")
    recorded_base = resolve(repo, manifest["base_sha"])
    if manifest["base_sha"] != recorded_base:
        raise SubmitError("base_sha must be the exact resolved target base commit")
    if recorded_base != stack_base:
        raise SubmitError("target base branch drifted from manifest base_sha")
    repository_templates = repository_template_paths(repo, recorded_base)
    validate_template_source(manifest["template_source"], repository_templates)
    for index, layer in enumerate(layers):
        missing = [
            field
            for field in ("branch", "remote_branch", "tip", "title", "summary", "body_file")
            if not layer.get(field)
        ]
        if missing:
            raise SubmitError(f"layer {index + 1} missing: {', '.join(missing)}")
        validate_component_branch_names(layer)
        superseded_prs = layer.get("superseded_prs", [])
        if not isinstance(superseded_prs, list) or not all(
            isinstance(item, int) and item > 0 for item in superseded_prs
        ):
            raise SubmitError(f"layer {index + 1} superseded_prs must be a list of positive integers")
        layer["_superseded_prs"] = set(superseded_prs)
        layer["_tip"] = resolve(repo, layer["tip"])
        if resolve(repo, layer["branch"]) != layer["_tip"]:
            raise SubmitError(f"branch drifted from manifest tip: {layer['branch']}")
        body = Path(layer["body_file"])
        body = body if body.is_absolute() else path.parent / body
        if not body.is_file():
            raise SubmitError(f"body file missing: {body}")
        layer["_body_file"] = body
        try:
            body_content = body.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise SubmitError(f"cannot read body file {body}: {exc}") from exc
        if manifest["template_source"] == FALLBACK_TEMPLATE_SOURCE:
            validate_fallback_body(
                body_content,
                label=f"layer {index + 1} body",
            )
        else:
            validate_repository_template_body(
                repo,
                recorded_base,
                manifest["template_source"],
                body_content,
                label=f"layer {index + 1} body",
            )
        if layer["remote_branch"] in targets:
            raise SubmitError(f"duplicate remote branch: {layer['remote_branch']}")
        targets.add(layer["remote_branch"])
        if layer["remote_branch"] == manifest["default_base"]:
            raise SubmitError("component head branch must differ from the default base")
        layer["_base"] = (
            manifest["default_base"]
            if index == 0
            else layers[index - 1]["remote_branch"]
        )
        layer["_head_ref"] = layer["remote_branch"]
        layer["_head_owner"] = publish_owner
        expected_parent = prior or stack_base
        if not is_ancestor(repo, expected_parent, layer["_tip"]):
            raise SubmitError(f"layer {index + 1} does not descend from layer {index}")
        if layer["_tip"] == expected_parent:
            raise SubmitError(
                f"layer {index + 1} is empty: adjacent manifest layers resolve to the same commit"
            )
        layer["_base_sha"] = expected_parent
        prior = layer["_tip"]
    validate_integration_evidence(repo, manifest, layers)
    if manifest["integration_evidence"]["branch"] in targets:
        raise SubmitError("integration evidence branch must differ from component PR branches")
    if manifest["integration_evidence"]["branch"].endswith("-pr-ready"):
        raise SubmitError("integration evidence branch must not use the PR-ready component suffix")
    validate_release_branch_placement(repo, manifest, layers)
    manifest["_integration_layer"] = integration_layer(manifest)
    if manifest.get("origin_review") is not None:
        validate_origin_review_approval(repo, path, manifest, layers)
    return layers


def component_base(
    layers: list[dict[str, Any]],
    index: int,
    default_base: str,
) -> str:
    return default_base if index == 0 else layers[index - 1]["remote_branch"]


def component_base_sha(
    manifest: dict[str, Any],
    layers: list[dict[str, Any]],
    index: int,
) -> str:
    return manifest["base_sha"] if index == 0 else layers[index - 1]["_tip"]


def node_label(
    index: int,
    layer: dict[str, Any],
    links: dict[int, dict[str, Any]],
    count: int,
    stack_label: str,
) -> str:
    pr = links.get(index)
    prefix = f"#{pr['number']} " if pr else "Pending: "
    return (prefix + stacked_title(layer, index, count, stack_label)).replace('"', "'")


def prerequisite_link(
    index: int,
    layers: list[dict[str, Any]],
    links: dict[int, dict[str, Any]],
    stack_label: str,
) -> str:
    pr = links.get(index)
    title = stacked_title(layers[index], index, len(layers), stack_label)
    if pr:
        return f"[#{pr['number']} - {title}]({pr['url']})"
    return f"PR {index + 1} - {title} (Pending)"


def render_merge_warning(
    layers: list[dict[str, Any]],
    links: dict[int, dict[str, Any]],
    current: int,
    default_base: str,
    stack_label: str,
    feature_name: str,
) -> str:
    lines = [
        "> [!WARNING]",
        f"> **Stack Merge Gate ({current + 1}/{len(layers)})**",
        ">",
    ]
    if current == 0:
        lines.extend(
            [
                f"> This is the first PR in a series of PRs composing a PR stack for the {feature_name} feature.",
                "> This is the planning PR. Please review all PRs in the stack in order.",
                "> **Do not approve or merge any PR out of order or before its prerequisite PRs have been merged.**",
                f"> After this PR merges, retarget/restack the next PR onto `{default_base}`, then cascade every",
                "> dependent branch before it can merge. Otherwise GitHub would merge that PR into",
                f"> this predecessor branch instead of `{default_base}`.",
                "> Then refresh **Files changed** and verify that only the next component remains.",
            ]
        )
    else:
        lines.extend(
            [
                f"> This is PR {current + 1} of {len(layers)} in the PR stack for the {feature_name} feature.",
                "> Please review all PRs in the stack in order.",
                "> **DO NOT APPROVE until every prerequisite PR below has merged:**",
                ">",
            ]
        )
        for index in range(current):
            lines.append(f"> {index + 1}. {prerequisite_link(index, layers, links, stack_label)}")
        lines.extend(
            [
                ">",
                f"> After the immediate predecessor merges, retarget/restack this PR onto `{default_base}` and",
                "> cascade its dependent branches before merging it. Otherwise GitHub would merge",
                "> this PR into the predecessor branch. Then refresh **Files changed** and verify",
                "> that only this component remains.",
            ]
        )
    lines.extend(
        [
            ">",
            "> See the **Stack PR Navigation** section below.",
        ]
    )
    return "\n".join(lines)


def render_navigation(
    layers: list[dict[str, Any]],
    links: dict[int, dict[str, Any]],
    current: int | None,
    default_base: str,
    stack_label: str,
    head_owner: str,
) -> str:
    lines = [MARKER, "## Stack PR Navigation", ""]
    if current is not None:
        lines.extend([f"**This PR:** {current + 1} of {len(layers)}", ""])
    lines.extend(
        [
            "This is a [stacked pull request](https://www.stacking.dev/) series. Every PR targets",
            f"its immediate predecessor (`{default_base}` for the first) and keeps its head on",
            f"`{head_owner}`. Each Files changed view is therefore the delta for one layer. Review",
            "strictly from the first layer through the last. These are the initial review bases:",
            f"`{default_base} -> layer 1`, then each prior PR-ready branch -> its dependent. After a predecessor",
            f"merges, retarget/restack the next PR onto `{default_base}` and cascade every dependent branch",
            "before merging it; otherwise GitHub would merge into the predecessor branch. Republish",
            "the exact heads and re-check every diff after each cascade.",
            "",
        ]
    )
    lines.extend(["```mermaid", "flowchart TD"])
    for index, layer in enumerate(layers):
        lines.append(
            f'  L{index + 1}["{node_label(index, layer, links, len(layers), stack_label)}"]'
        )
        if index:
            lines.append(f"  L{index} --> L{index + 1}")
        if index in links:
            lines.append(f'  click L{index + 1} "{links[index]["url"]}" "Open PR"')
    lines.extend(
        [
            "```",
            "",
            "| # | Layer | Must merge first | Base | Head | PR |",
            "|---:|---|---|---|---|---|",
        ]
    )
    for index, layer in enumerate(layers):
        pr = links.get(index)
        link = f"[#{pr['number']}]({pr['url']})" if pr else "Pending"
        rendered_title = stacked_title(layer, index, len(layers), stack_label)
        title = f"[{rendered_title}]({pr['url']})" if pr else f"{rendered_title} (Pending)"
        here = " **(this PR)**" if current == index else ""
        prerequisites = (
            "None"
            if index == 0
            else ", ".join(
                f"[#{links[prior]['number']}]({links[prior]['url']})"
                if prior in links
                else f"PR {prior + 1} (Pending)"
                for prior in range(index)
            )
        )
        lines.append(
            f"| {index + 1} | {title} - {layer['summary']}{here} | {prerequisites} | "
            f"`{component_base(layers, index, default_base)}` | "
            f"`{head_owner}:{layer['remote_branch']}` | {link} |"
        )
    return "\n".join(lines) + "\n"


def render_body(
    layers: list[dict[str, Any]],
    links: dict[int, dict[str, Any]],
    index: int,
    default_base: str,
    feature_summary: str,
    stack_label: str,
    integration_evidence: dict[str, Any],
    head_owner: str,
    feature_name: str,
) -> str:
    content = layers[index]["_body_file"].read_text(encoding="utf-8").rstrip()
    planning = links.get(0)
    pointer = ""
    if index and planning:
        pointer = (
            f"\n\n**Feature context:** {feature_summary} "
            f"See [Planning PR #{planning['number']}]({planning['url']}) for the complete design and rollout."
        )
    tests = integration_evidence["tests"]
    safety = integration_evidence["partial_merge_safety"]
    feature_flag = safety["feature_flag"]
    builds = ", ".join(f"`{build['artifact']}`" for build in integration_evidence["builds"])
    combined_pr = (
        f"\n- [Combined stack validation PR]({integration_evidence['_integration_pr_url']}) "
        "- **draft only; do not merge**"
        if integration_evidence.get("_integration_pr_url")
        else ""
    )
    validation = (
        "\n\n## Stack validation and partial-merge safety\n\n"
        f"All **{safety['validated_prefixes']}/{safety['total_prefixes']}** cumulative stack prefixes "
        "passed their required tests and builds, so the supported dependency-ordered merge sequence "
        f"does not leave `{default_base}` in an unvalidated partial state. "
        f"`{feature_flag['name']}` defaults to **{feature_flag['safe_default']}**; "
        f"{feature_flag['disabled_behavior']}\n\n"
        f"- [Validated integration branch]({integration_evidence['_branch_url']}) at "
        f"`{integration_evidence['_commit']}`\n"
        f"- [Committed validation report]({integration_evidence['_report_url']})\n"
        f"- Tests: `{integration_evidence['test_command']}` - **{tests['passed']} passed, "
        f"{tests['skipped']} skipped, {tests['warnings']} warnings**\n"
        f"- Built distributions: {builds}"
        f"{combined_pr}"
    )
    return (
        render_merge_warning(
            layers,
            links,
            index,
            default_base,
            stack_label,
            feature_name,
        )
        + "\n\n"
        + content
        + pointer
        + validation
        + "\n\n"
        + render_navigation(
            layers,
            links,
            index,
            default_base,
            stack_label,
            head_owner,
        )
    )


def integration_title(feature_name: str) -> str:
    return f"test(stack): validate complete {feature_name} PR stack"


def integration_layer(manifest: dict[str, Any]) -> dict[str, Any]:
    branch = manifest["integration_evidence"]["branch"]
    evidence_owner = manifest.get("_evidence_owner", manifest.get("_head_owner"))
    return {
        "remote_branch": branch,
        "_head_ref": (
            f"{evidence_owner}:{branch}"
            if manifest.get("_evidence_cross_repository", True)
            else branch
        ),
        "_head_owner": evidence_owner,
        "_tip": manifest["integration_evidence"]["_commit"],
        "_base_sha": manifest["base_sha"],
        "_must_remain_draft": True,
        "title": integration_title(manifest["feature_name"]),
    }


def render_integration_body(
    manifest: dict[str, Any],
    layers: list[dict[str, Any]],
    links: dict[int, dict[str, Any]],
) -> str:
    evidence = manifest["integration_evidence"]
    tests = evidence["tests"]
    builds = ", ".join(f"`{build['artifact']}`" for build in evidence["builds"])
    lines = [
        "> [!CAUTION]",
        "> **Combined stack validation PR - DO NOT MERGE**",
        ">",
        f"> This draft contains the complete {manifest['feature_name']} PR stack solely so the",
        "> target repository's GitHub checks can run against the final integrated tree.",
        "> Review and merge the component PRs below in order; do not approve or merge this PR.",
        "",
        "## Purpose",
        "",
        "Use the checks on this draft as evidence that the totality of the stack passes the target",
        "repository's CI when composed. Code review belongs on the component PRs, where each intended",
        "layer is explained and tracked. This draft is not a substitute merge path.",
        "",
        "## Reviewer checklist",
        "",
        "- Confirm the GitHub checks below complete successfully against the combined stack.",
        "- Review each component PR in order and enforce its Stack Merge Gate.",
        "- Merge only component PRs; keep this combined validation PR in draft and never merge it.",
        "- If a component changes, require a refreshed integration branch and rerun these checks.",
        "",
        "## Component PRs",
        "",
        "| # | Component | Head SHA | PR |",
        "|---:|---|---|---|",
    ]
    for index, layer in enumerate(layers):
        pr = links.get(index)
        title = stacked_title(layer, index, len(layers), manifest["stack_label"])
        if pr:
            title_cell = f"[{title}]({pr['url']})"
            pr_cell = f"[#{pr['number']}]({pr['url']})"
        else:
            title_cell = f"{title} (Pending)"
            pr_cell = "Pending"
        lines.append(
            f"| {index + 1} | {title_cell} | `{layer['_tip']}` | {pr_cell} |"
        )
    lines.extend(
        [
            "",
            "## Combined-stack evidence",
            "",
            f"- Integration head: [`{evidence['_commit']}`]({evidence['_branch_url']})",
            f"- Immutable validation report: [open report]({evidence['_report_url']})",
            f"- Local integration command: `{evidence['test_command']}`",
            f"- Local result: **{tests['passed']} passed, {tests['skipped']} skipped, "
            f"{tests['warnings']} warnings**",
            f"- Built distributions: {builds}",
            "",
            "The committed report records local integration and prefix validation. The GitHub checks",
            "on this draft are the authoritative target-repository CI result; this body does not claim",
            "they pass until GitHub reports them as successful.",
        ]
    )
    return "\n".join(lines) + "\n"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SubmitError(f"cannot hash rendered submission artifact {path}: {exc}") from exc
    return digest.hexdigest()


def refresh_journal_artifact_hashes(payload: dict[str, Any]) -> None:
    for layer in payload.get("layers", []):
        for path_field in ("source_body", "rendered_title", "rendered_body"):
            value = layer.get(path_field)
            if not isinstance(value, str) or not value:
                raise SubmitError(f"submission journal layer is missing {path_field}")
            layer[f"{path_field}_sha256"] = sha256_file(Path(value))
    integration = payload.get("integration_pr")
    if integration is not None:
        if not isinstance(integration, dict):
            raise SubmitError("submission journal integration_pr must be an object")
        for path_field in ("rendered_title", "rendered_body"):
            value = integration.get(path_field)
            if not isinstance(value, str) or not value:
                raise SubmitError(f"submission journal integration_pr is missing {path_field}")
            integration[f"{path_field}_sha256"] = sha256_file(Path(value))


def write_journal(path: Path | None, payload: dict[str, Any]) -> None:
    refresh_journal_artifact_hashes(payload)
    if path:
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


def remote_sha(repo: Path, remote: str, branch: str) -> str | None:
    output = git(repo, "ls-remote", "--heads", remote, f"refs/heads/{branch}")
    return output.split()[0] if output else None


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def verify_published_layers(repo: Path, manifest: dict[str, Any], layers: list[dict[str, Any]]) -> None:
    output = git(repo, "ls-remote", "--heads", manifest["publish_remote"])
    published = {
        ref.removeprefix("refs/heads/"): sha
        for line in output.splitlines()
        for sha, ref in [line.split(maxsplit=1)]
    }
    for layer in layers:
        if published.get(layer["remote_branch"]) != layer["_tip"]:
            raise SubmitError(f"remote branch SHA mismatch: {layer['remote_branch']}")


def verify_published_evidence(repo: Path, manifest: dict[str, Any]) -> None:
    evidence = manifest["integration_evidence"]
    if remote_sha(repo, manifest["evidence_remote"], evidence["branch"]) != evidence["_commit"]:
        raise SubmitError("evidence branch moved from its recorded commit")


def validate_approved_dry_run(
    path: Path | None,
    manifest_path: Path,
    manifest: dict[str, Any],
    layers: list[dict[str, Any]],
) -> dict[str, Any]:
    if path is None or not path.is_absolute() or not path.is_file():
        raise SubmitError(
            "upstream apply requires --approved-dry-run-journal from the regenerated package"
        )
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubmitError(f"cannot read approved dry-run journal: {exc}") from exc
    if not isinstance(journal, dict):
        raise SubmitError("approved dry-run journal must be a JSON object")
    if (
        journal.get("status") != "dry-run"
        or journal.get("manifest") != str(manifest_path.resolve())
        or journal.get("manifest_sha256") != sha256_file(manifest_path)
        or journal.get("repository") != manifest["repository"]
        or journal.get("head_repository") != manifest["_head_repository"]
        or journal.get("evidence_remote") != manifest["evidence_remote"]
        or journal.get("evidence_repository") != manifest["_evidence_repository"]
        or journal.get("default_base") != manifest["default_base"]
        or journal.get("base_sha") != manifest["base_sha"]
        or journal.get("stack_label") != manifest["stack_label"]
        or journal.get("template_source") != manifest["template_source"]
        or journal.get("origin_review") != manifest.get("_origin_review")
    ):
        raise SubmitError("approved dry-run journal does not match the regenerated manifest")
    recorded_layers = journal.get("layers")
    if not isinstance(recorded_layers, list) or len(recorded_layers) != len(layers):
        raise SubmitError("approved dry-run journal has the wrong layer count")
    approved_component_bodies: list[str] = []
    for index, (layer, recorded) in enumerate(zip(layers, recorded_layers)):
        if not isinstance(recorded, dict):
            raise SubmitError("approved dry-run journal contains a malformed layer")
        expected_title = stacked_title(
            layer, index, len(layers), manifest["stack_label"]
        )
        expected_body = render_body(
            layers,
            {},
            index,
            manifest["default_base"],
            manifest["feature_summary"],
            manifest["stack_label"],
            manifest["integration_evidence"],
            manifest["_head_owner"],
            manifest["feature_name"],
        )
        if (
            recorded.get("branch") != layer["branch"]
            or recorded.get("remote_branch") != layer["remote_branch"]
            or recorded.get("tip") != layer["_tip"]
            or recorded.get("base") != layer["_base_sha"]
            or recorded.get("head") != layer["_head_ref"]
            or recorded.get("title") != expected_title
            or recorded.get("source_title") != layer["title"]
            or recorded.get("source_body") != str(layer["_body_file"].resolve())
            or recorded.get("source_body_sha256") != sha256_file(layer["_body_file"])
            or recorded.get("rendered_title_sha256")
            != sha256_text(expected_title + "\n")
            or recorded.get("rendered_body_sha256") != sha256_text(expected_body)
        ):
            raise SubmitError("approved dry-run journal layer topology changed")
        for field in ("rendered_title", "rendered_body"):
            artifact = Path(str(recorded.get(field, "")))
            if not artifact.is_absolute() or not artifact.is_file():
                raise SubmitError(f"approved dry-run journal {field} changed")
            try:
                artifact_bytes = artifact.read_bytes()
                artifact_text = artifact_bytes.decode("utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise SubmitError(
                    f"cannot bind approved dry-run journal {field}: {exc}"
                ) from exc
            if (
                hashlib.sha256(artifact_bytes).hexdigest()
                != recorded.get(f"{field}_sha256")
            ):
                raise SubmitError(f"approved dry-run journal {field} changed")
            if field == "rendered_body":
                approved_component_bodies.append(artifact_text)
    integration = journal.get("integration_pr")
    if (
        not isinstance(integration, dict)
        or integration.get("branch") != manifest["integration_evidence"]["branch"]
        or integration.get("tip") != manifest["integration_evidence"]["_commit"]
        or integration.get("draft") is not True
        or integration.get("merge") != "prohibited"
        or integration.get("head_repository") != manifest["_evidence_repository"]
        or integration.get("base") != manifest["default_base"]
        or integration.get("head") != manifest["_integration_layer"]["_head_ref"]
        or integration.get("title") != manifest["_integration_layer"]["title"]
        or integration.get("rendered_title_sha256")
        != sha256_text(manifest["_integration_layer"]["title"] + "\n")
        or integration.get("rendered_body_sha256")
        != sha256_text(render_integration_body(manifest, layers, {}))
    ):
        raise SubmitError("approved dry-run journal integration proof changed")
    approved_integration_body = ""
    for field in ("rendered_title", "rendered_body"):
        artifact = Path(str(integration.get(field, "")))
        if not artifact.is_absolute() or not artifact.is_file():
            raise SubmitError(f"approved dry-run journal integration {field} changed")
        try:
            artifact_bytes = artifact.read_bytes()
            artifact_text = artifact_bytes.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise SubmitError(
                f"cannot bind approved dry-run journal integration {field}: {exc}"
            ) from exc
        if (
            hashlib.sha256(artifact_bytes).hexdigest()
            != integration.get(f"{field}_sha256")
        ):
            raise SubmitError(f"approved dry-run journal integration {field} changed")
        if field == "rendered_body":
            approved_integration_body = artifact_text
    try:
        generated = datetime.fromisoformat(
            str(journal["generated_at"]).replace("Z", "+00:00")
        )
    except (KeyError, ValueError) as exc:
        raise SubmitError(
            "approved dry-run journal lacks a valid generated_at timestamp"
        ) from exc
    if generated.tzinfo is None or generated > datetime.now(timezone.utc):
        raise SubmitError("approved dry-run journal generated_at is not a valid past instant")
    journal["_approved_component_bodies"] = approved_component_bodies
    journal["_approved_integration_body"] = approved_integration_body
    return journal


def validate_sealed_apply_request(
    repo: Path,
    path: Path | None,
    manifest_path: Path,
    dry_run_path: Path | None,
    output_path: Path | None,
) -> dict[str, Any]:
    if (
        path is None
        or not path.is_absolute()
        or not path.is_file()
        or dry_run_path is None
        or output_path is None
    ):
        raise SubmitError("upstream apply requires an approved sealed apply request")
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubmitError(f"cannot read approved apply request: {exc}") from exc
    fields = {
        "schema_version",
        "preparation_receipt",
        "preparation_receipt_sha256",
        "apply_journal",
        "upstream_apply_authorized",
        "upstream_apply_authorization_phrase",
    }
    if (
        not isinstance(request, dict)
        or set(request) != fields
        or request.get("schema_version") != 1
        or request.get("upstream_apply_authorized") is not True
        or request.get("upstream_apply_authorization_phrase")
        != UPSTREAM_APPLY_APPROVAL_PHRASE
        or request.get("apply_journal") != str(output_path.resolve())
    ):
        raise SubmitError("approved apply request schema or authorization is invalid")
    receipt_value = request.get("preparation_receipt")
    receipt = Path(receipt_value) if isinstance(receipt_value, str) else None
    if receipt is None or not receipt.is_absolute() or not receipt.is_file():
        raise SubmitError("approved apply request preparation receipt is missing")
    if request.get("preparation_receipt_sha256") != sha256_file(receipt):
        raise SubmitError("approved apply request preparation receipt hash changed")
    try:
        prepared = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubmitError(f"cannot read preparation receipt: {exc}") from exc
    if (
        not isinstance(prepared, dict)
        or prepared.get("schema_version") != 1
        or prepared.get("status") != "dry-run-ready"
        or prepared.get("manifest") != str(manifest_path.resolve())
        or prepared.get("manifest_sha256") != sha256_file(manifest_path)
        or prepared.get("dry_run_journal") != str(dry_run_path.resolve())
        or prepared.get("dry_run_journal_sha256") != sha256_file(dry_run_path)
    ):
        raise SubmitError("approved apply request does not bind the exact sealed package")
    validator = Path(__file__).with_name("prepare_upstream_submission.py")
    try:
        validated = json.loads(
            run(
                [
                    sys.executable,
                    str(validator),
                    str(path),
                    "--repo",
                    str(repo),
                    "--mode",
                    "validate-apply",
                ],
                repo,
            )
        )
    except json.JSONDecodeError as exc:
        raise SubmitError("strong apply-request validator returned malformed JSON") from exc
    expected = {
        "manifest": str(manifest_path.resolve()),
        "dry_run_journal": str(dry_run_path.resolve()),
        "apply_journal": str(output_path.resolve()),
    }
    if not isinstance(validated, dict) or any(
        validated.get(field) != value for field, value in expected.items()
    ):
        raise SubmitError("strong apply-request validation returned mismatched artifacts")
    return validated


def repository_web_url(repository: str) -> str:
    host, owner, name = split_repository(repository)
    return f"https://{host or 'github.com'}/{owner}/{name}"


def render_manual_instructions(
    manifest: dict[str, Any],
    layers: list[dict[str, Any]],
    links: dict[int, dict[str, Any]],
    manifest_path: Path,
    destination: Path,
    links_path: Path,
    output: Path | None,
) -> str:
    lines = [
        "# Manual stacked PR submission",
        "",
        f"**Target repository:** `{manifest['repository']}`  ",
        f"**Component head repository:** `{manifest['_head_repository']}`  ",
        f"**Evidence repository:** "
        f"`{manifest.get('_evidence_repository', manifest['_head_repository'])}`  ",
        f"**First base:** `{manifest['default_base']}`; each later base is the prior head  ",
        f"**PR count:** {len(layers)}",
        "",
        "## Submit in this order",
        "",
        "Create the PRs from top to bottom. For each row, copy the numbered title file into the",
        "GitHub title field and the matching body file into the description field. The body files",
        "use the same template, explicit prerequisite warning, and stack graph as automatic submission.",
        "Each PR targets its immediate predecessor; do not approve a later PR until all PRs listed",
        "at its top have merged. After each predecessor merge, retarget/restack the next PR onto",
        f"`{manifest['default_base']}` and cascade all dependent branches before that PR can merge; otherwise GitHub would",
        "merge it into the predecessor branch. Regenerate this package after each cascade.",
        "",
        "| # | Base | Head | Title | Body | Action | PR |",
        "|---:|---|---|---|---|---|---|",
    ]
    web_url = repository_web_url(manifest["repository"])
    for index, layer in enumerate(layers):
        position = index + 1
        base = component_base(layers, index, manifest["default_base"])
        head = layer["_head_ref"]
        create_url = (
            f"{web_url}/compare/{quote(base, safe='/')}..."
            f"{quote(head, safe='/:')}?expand=1"
        )
        pr = links.get(index)
        pr_link = f"[#{pr['number']}]({pr['url']})" if pr else "Pending"
        if pr:
            action = "Verified existing"
        elif index == len(links):
            action = f"[Create draft]({create_url})"
        else:
            action = "Blocked"
        lines.append(
            f"| {position} | `{base}` | `{head}` | `{position:02d}-title.txt` | "
            f"`{position:02d}-body.md` | {action} | {pr_link} |"
        )
    lines.extend(
        [
            "",
            "## Command-line alternative",
            "",
            "Run these commands from this package directory. Existing PRs are refreshed first, then",
            "only the next unsubmitted layer is emitted; later layers stay blocked until it is recorded.",
            "",
            "```bash",
        ]
    )
    for index, layer in enumerate(layers):
        position = index + 1
        pr = links.get(index)
        if pr:
            lines.append(
                f'gh pr comment "{pr["url"]}" '
                f'--body-file "{position:02d}-navigation.md"'
            )
        elif index == len(links):
            lines.append(
                f'gh pr create --repo "{manifest["repository"]}" '
                f'--base "{component_base(layers, index, manifest["default_base"])}" '
                f'--head "{layer["_head_ref"]}" --title "$(cat {position:02d}-title.txt)" '
                f'--body-file "{position:02d}-body.md" --draft'
            )
    lines.extend(
        [
            "```",
            "",
            "## Create the combined validation PR last",
            "",
            "Generated files: `integration-title.txt` and `integration-body.md`.",
            "",
        ]
    )
    existing_integration = manifest.get("_existing_integration_pr")
    if len(links) != len(layers):
        lines.extend(
            [
                "Not ready. Record every component PR before creating the permanent-draft",
                "integration PR.",
            ]
        )
    elif isinstance(existing_integration, dict):
        lines.extend(
            [
                "The permanent-draft integration PR already exists:",
                f"[#{existing_integration['number']}]({existing_integration['url']}).",
                "Do not recreate it, mark it ready, merge it, or rewrite its approved body.",
                "",
                "```bash",
                f'gh pr comment "{existing_integration["url"]}" '
                '--body-file "integration-navigation.md"',
                "```",
            ]
        )
    else:
        lines.extend(
            [
                "After all component commands succeed, create the integration PR as a draft. Never mark",
                "it ready and never merge it. Do not regenerate or rewrite the approved PR bodies:",
                "",
                "```bash",
                f'gh pr create --repo "{manifest["repository"]}" '
                f'--base "{manifest["default_base"]}" '
                f'--head "{manifest["_integration_layer"]["_head_ref"]}" '
                '--title "$(cat integration-title.txt)" '
                '--body-file "integration-body.md" --draft',
                "```",
            ]
        )
    lines.extend(
        [
            "",
            "The component and integration bodies are the exact sealed dry-run artifacts. Add any",
            "live PR navigation as comments after creation; do not edit these approved bodies.",
            "",
        ]
    )
    return "\n".join(lines)


def repository_network_root(repository: dict[str, Any]) -> str:
    source = repository.get("source")
    if isinstance(source, dict) and isinstance(source.get("full_name"), str):
        return source["full_name"].casefold()
    return repository["full_name"].casefold()


def github_repository_preflight(repo: Path, manifest: dict[str, Any]) -> None:
    host, owner, name = split_repository(manifest["repository"])
    _, head_owner, head_name = split_repository(manifest["_head_repository"])
    _, evidence_owner, evidence_name = split_repository(manifest["_evidence_repository"])
    progress(
        "preflight",
        f"checking target components {manifest['repository']} and evidence "
        f"{manifest['_evidence_repository']}",
    )
    if host:
        run(["gh", "auth", "status", "--hostname", host], repo, retry_transient=True)
    target = json.loads(gh_api(repo, manifest["repository"], f"repos/{owner}/{name}"))
    component_source = json.loads(
        gh_api(repo, manifest["repository"], f"repos/{head_owner}/{head_name}")
    )
    evidence_source = json.loads(
        gh_api(repo, manifest["repository"], f"repos/{evidence_owner}/{evidence_name}")
    )
    if not component_source.get("permissions", {}).get("push"):
        raise SubmitError("authenticated user lacks push permission for component head branches")
    if not evidence_source.get("permissions", {}).get("push"):
        raise SubmitError("authenticated user lacks push permission for evidence branches")
    if (
        manifest["_evidence_cross_repository"]
        and repository_network_root(evidence_source) != repository_network_root(target)
    ):
        raise SubmitError(
            "cross-repository evidence repository is not in the target repository's fork network"
        )

    target_base = remote_sha(repo, manifest["target_remote"], manifest["default_base"])
    if target_base != manifest["base_sha"]:
        raise SubmitError("target base branch moved after manifest creation")


def preflight_integration_pull_request(repo: Path, manifest: dict[str, Any]) -> None:
    combined = integration_layer(manifest)
    existing = pull_requests_for_head(
        repo,
        manifest["repository"],
        manifest["_evidence_owner"],
        combined["remote_branch"],
    )
    if len(existing) > 1:
        raise SubmitError("multiple combined-stack validation PRs exist")
    if existing:
        pr = existing[0]
        if (
            pr["state"] != "OPEN"
            or not pr["isDraft"]
            or pr["baseRefName"] != manifest["default_base"]
            or pr["baseRefOid"] != manifest["base_sha"]
            or pr["headRefOid"] != combined["_tip"]
            or pr["headRepositoryOwner"].casefold()
            != manifest["_evidence_owner"].casefold()
        ):
            raise SubmitError("existing combined-stack validation PR conflicts")
        manifest["_existing_integration_pr"] = pr
    manifest["_integration_layer"] = combined


def github_preflight(repo: Path, manifest: dict[str, Any], layers: list[dict[str, Any]]) -> None:
    github_repository_preflight(repo, manifest)
    repository = manifest["repository"]
    for index, layer in enumerate(layers):
        expected_base = component_base(layers, index, manifest["default_base"])
        progress(
            "preflight",
            f"{index + 1}/{len(layers)}: {layer['_head_ref']} -> {expected_base}",
        )
        existing = pull_requests_for_head(
            repo,
            repository,
            manifest["_head_owner"],
            layer["remote_branch"],
        )
        superseded = layer.get("_superseded_prs", set())
        if superseded:
            found_numbers = {pr["number"] for pr in existing}
            unmatched = superseded - found_numbers
            if unmatched:
                raise SubmitError(
                    f"superseded_prs for {layer['remote_branch']} references PR(s) "
                    f"{sorted(unmatched)} that do not exist for this head; fix the allowlist"
                )
            still_open = [
                pr["number"] for pr in existing if pr["number"] in superseded and pr["state"] == "OPEN"
            ]
            if still_open:
                raise SubmitError(
                    f"superseded_prs for {layer['remote_branch']} lists still-OPEN PR(s) "
                    f"{sorted(still_open)}; only a CLOSED PR may be marked superseded"
                )
            existing = [pr for pr in existing if pr["number"] not in superseded]
        if len(existing) > 1:
            raise SubmitError(f"multiple PRs exist for {layer['remote_branch']}")
        if existing:
            pr = existing[0]
            if (
                pr["state"] != "OPEN"
                or pr["baseRefName"] != expected_base
                or pr["baseRefOid"] != component_base_sha(manifest, layers, index)
                or pr["headRepositoryOwner"].casefold()
                != manifest["_head_owner"].casefold()
            ):
                raise SubmitError(f"existing PR conflicts for {layer['remote_branch']}")
            if manifest.get("draft") and not pr["isDraft"]:
                raise SubmitError(f"existing PR draft state conflicts for {layer['remote_branch']}")
            if pr["headRefOid"] != layer["_tip"]:
                raise SubmitError(
                    f"refusing to rewrite existing PR head: {layer['remote_branch']}"
                )
            layer["_existing_pr"] = pr
        published = remote_sha(repo, manifest["publish_remote"], layer["remote_branch"])
        if published != layer["_tip"]:
            raise SubmitError(
                f"PR-ready target branch is missing or stale: {layer['remote_branch']}"
            )
    preflight_integration_pull_request(repo, manifest)


def validate_manual_links_live(
    repo: Path,
    manifest: dict[str, Any],
    layers: list[dict[str, Any]],
    links: dict[int, dict[str, Any]],
) -> None:
    for index, link in links.items():
        layer = layers[index]
        existing = pull_requests_for_head(
            repo,
            manifest["repository"],
            manifest["_head_owner"],
            layer["remote_branch"],
        )
        if len(existing) != 1:
            raise SubmitError(f"manual link cannot resolve PR for layer {index + 1}")
        pr = existing[0]
        if (
            pr["number"] != link["number"]
            or pr["url"] != link["url"]
            or pr["state"] != "OPEN"
            or pr["baseRefName"]
            != component_base(layers, index, manifest["default_base"])
            or pr["baseRefOid"] != component_base_sha(manifest, layers, index)
            or pr["headRefOid"] != layer["_tip"]
            or pr["headRepositoryOwner"].casefold()
            != manifest["_head_owner"].casefold()
        ):
            raise SubmitError(f"manual link conflicts for layer {index + 1}")
        if manifest.get("draft") and not pr["isDraft"]:
            raise SubmitError(f"manual PR draft state conflicts for layer {index + 1}")
        staged = len(links) < len(layers) or not manifest.get("_existing_integration_pr")
        if staged and not pr["isDraft"]:
            raise SubmitError(
                f"manual PR must remain draft until integration linking: layer {index + 1}"
            )


def discover_manual_links(
    repo: Path,
    manifest: dict[str, Any],
    layers: list[dict[str, Any]],
    links: dict[int, dict[str, Any]],
) -> None:
    for index, layer in enumerate(layers):
        existing = pull_requests_for_head(
            repo,
            manifest["repository"],
            manifest["_head_owner"],
            layer["remote_branch"],
        )
        if len(existing) > 1:
            raise SubmitError(
                f"multiple existing PRs use manual layer {index + 1} head "
                f"{layer['remote_branch']}; resolve them before regenerating the package"
            )
        supplied = links.get(index)
        if not existing:
            if supplied:
                raise SubmitError(
                    f"manual link for layer {index + 1} no longer resolves to an existing PR"
                )
            continue
        pr = existing[0]
        if (
            pr["state"] != "OPEN"
            or pr["baseRefName"]
            != component_base(layers, index, manifest["default_base"])
            or pr["baseRefOid"] != component_base_sha(manifest, layers, index)
            or pr["headRefOid"] != layer["_tip"]
            or pr["headRepositoryOwner"].casefold()
            != manifest["_head_owner"].casefold()
        ):
            raise SubmitError(
                f"existing PR for manual layer {index + 1} conflicts; retarget/restack it "
                "to the recorded immutable base/head, then regenerate the package"
            )
        if supplied and (supplied["number"] != pr["number"] or supplied["url"] != pr["url"]):
            raise SubmitError(f"manual link conflicts with discovered PR for layer {index + 1}")
        links[index] = {"number": pr["number"], "url": pr["url"]}
    positions = sorted(links)
    if positions and positions != list(range(positions[-1] + 1)):
        raise SubmitError(
            "existing manual PRs do not form a contiguous prefix; create or reconcile "
            "the missing predecessor PR before regenerating the package"
        )
    validate_manual_links_live(repo, manifest, layers, links)


def pull_requests_for_head(
    repo: Path,
    repository: str,
    head_owner: str,
    remote_branch: str,
) -> list[dict[str, Any]]:
    _, owner, name = split_repository(repository)
    encoded_head = quote(f"{head_owner}:{remote_branch}", safe="")
    payload = json.loads(
        gh_api(
            repo,
            repository,
            f"repos/{owner}/{name}/pulls?state=all&head={encoded_head}&per_page=100",
            "--paginate",
            "--slurp",
        )
        or "[]"
    )
    items = (
        [item for page in payload for item in page]
        if payload and isinstance(payload[0], list)
        else payload
    )
    for item in items:
        head = item.get("head") if isinstance(item, dict) else None
        if not isinstance(head, dict) or head.get("ref") != remote_branch:
            continue
        head_repo = head.get("repo")
        owner = head_repo.get("owner") if isinstance(head_repo, dict) else None
        if not isinstance(owner, dict) or not isinstance(owner.get("login"), str):
            raise SubmitError(
                f"head repository is unavailable for {remote_branch}; "
                "the fork may have been deleted or detached"
            )
    return [
        {
            "number": item["number"],
            "url": item["html_url"],
            "state": item["state"].upper(),
            "isDraft": bool(item["draft"]),
            "title": item["title"],
            "body": item.get("body") or "",
            "body_sha256": sha256_text(item.get("body") or ""),
            "baseRefName": item["base"]["ref"],
            "baseRefOid": item["base"]["sha"],
            "headRefName": item["head"]["ref"],
            "headRefOid": item["head"]["sha"],
            "headRepositoryOwner": item["head"]["repo"]["owner"]["login"],
            "createdAt": item["created_at"],
        }
        for item in items
        if item["head"]["ref"] == remote_branch
    ]


def reconcile_created_pull_request(
    repo: Path,
    manifest: dict[str, Any],
    layer: dict[str, Any],
    expected_base: str,
) -> dict[str, Any] | None:
    expected_owner = layer.get("_head_owner", manifest["_head_owner"])
    existing = pull_requests_for_head(
        repo,
        manifest["repository"],
        expected_owner,
        layer["remote_branch"],
    )
    existing = [pr for pr in existing if pr["number"] not in layer.get("_superseded_prs", set())]
    if not existing:
        return None
    if len(existing) > 1:
        raise SubmitError(f"multiple PRs exist for {layer['remote_branch']}")
    pr = existing[0]
    if (
        pr["state"] != "OPEN"
        or pr["baseRefName"] != expected_base
        or pr["baseRefOid"] != layer.get("_base_sha")
        or pr["headRefOid"] != layer["_tip"]
        or pr["headRepositoryOwner"].casefold() != expected_owner.casefold()
        or not pr["isDraft"]
    ):
        raise SubmitError(f"ambiguous PR creation conflicts for {layer['remote_branch']}")
    return pr


def create_pull_request(
    repo: Path,
    manifest: dict[str, Any],
    layer: dict[str, Any],
    base: str,
    body_file: str,
    title: str,
) -> dict[str, Any]:
    arguments = [
        "pr",
        "create",
        "--base",
        base,
        "--head",
        layer["_head_ref"],
        "--title",
        title,
        "--body-file",
        body_file,
        "--draft",
    ]
    output = gh(
        repo,
        manifest["repository"],
        *arguments,
        retry_transient=False,
    )
    lines = output.splitlines()
    if not lines or not re.fullmatch(r"https?://[^/\s]+/.+/pull/\d+/?", lines[-1]):
        raise SubmitError(
            f"ambiguous PR creation output for {layer['remote_branch']}; "
            "inspect GitHub before retrying"
        )
    url = lines[-1]
    try:
        number = int(url.rstrip("/").rsplit("/", 1)[-1])
    except ValueError as exc:
        raise SubmitError(
            f"ambiguous PR creation output for {layer['remote_branch']}; "
            "inspect GitHub before retrying"
        ) from exc
    return {
        "number": number,
        "url": url,
        "state": "OPEN",
    }


def publish(repo: Path, manifest: dict[str, Any], layer: dict[str, Any]) -> None:
    remote = manifest["publish_remote"]
    old = remote_sha(repo, remote, layer["remote_branch"])
    if old == layer["_tip"]:
        return
    if old is not None:
        raise SubmitError(
            f"approved component head drifted on {remote}: {layer['remote_branch']}; "
            "refusing to replace unapproved remote work"
        )
    lease = f"--force-with-lease=refs/heads/{layer['remote_branch']}:{old or ''}"
    refspec = f"{layer['_tip']}:refs/heads/{layer['remote_branch']}"
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            git(repo, "push", lease, remote, refspec)
        except SubmitError as exc:
            if not is_transient_failure(str(exc)):
                raise
            published = remote_sha(repo, remote, layer["remote_branch"])
            if published == layer["_tip"]:
                return
            if published != old:
                raise SubmitError(
                    f"remote branch changed during push: {layer['remote_branch']}"
                ) from exc
            if attempt == RETRY_ATTEMPTS:
                raise
            retry_delay(attempt, "git push")
            continue
        if remote_sha(repo, remote, layer["remote_branch"]) != layer["_tip"]:
            raise SubmitError(f"published branch SHA mismatch: {layer['remote_branch']}")
        return


def verify_pull_request(
    repo: Path,
    manifest: dict[str, Any],
    layers: list[dict[str, Any]],
    links: dict[int, dict[str, Any]],
    index: int,
    layer: dict[str, Any],
    expected_base: str,
    pr: dict[str, Any],
    expected_draft: bool | None,
    expected_body: str,
) -> None:
    _, owner, name = split_repository(manifest["repository"])
    payload = json.loads(
        gh_api(
            repo,
            manifest["repository"],
            f"repos/{owner}/{name}/pulls/{pr['number']}",
        )
    )
    state = {
        "state": payload["state"].upper(),
        "isDraft": bool(payload["draft"]),
        "title": payload["title"],
        "baseRefName": payload["base"]["ref"],
        "baseRefOid": payload["base"]["sha"],
        "headRefName": payload["head"]["ref"],
        "headRefOid": payload["head"]["sha"],
        "headRepositoryOwner": payload["head"]["repo"]["owner"]["login"],
        "body": payload.get("body") or "",
    }
    expected = {
        "state": "OPEN",
        "title": stacked_title(layer, index, len(layers), manifest["stack_label"]),
        "baseRefName": expected_base,
        "baseRefOid": component_base_sha(manifest, layers, index),
        "headRefName": layer["remote_branch"],
        "headRefOid": layer["_tip"],
    }
    if expected_draft is not None:
        expected["isDraft"] = expected_draft
    mismatches = [key for key, value in expected.items() if state.get(key) != value]
    if state["headRepositoryOwner"].casefold() != manifest["_head_owner"].casefold():
        mismatches.append("headRepositoryOwner")
    if mismatches:
        raise SubmitError(f"submitted PR state mismatch for {layer['remote_branch']}: {', '.join(mismatches)}")
    body = state.get("body") or ""
    if body != expected_body:
        raise SubmitError(f"submitted PR body drifted for {layer['remote_branch']}")
    evidence = manifest["integration_evidence"]
    required_body_content = (
        MARKER,
        "> [!WARNING]",
        "refresh **Files changed**",
        "https://www.stacking.dev/",
        evidence["_branch_url"],
        evidence["_report_url"],
        evidence["test_command"],
        evidence["partial_merge_safety"]["feature_flag"]["name"],
    )
    if any(value not in body for value in required_body_content):
        raise SubmitError(f"submitted PR evidence is incomplete for {layer['remote_branch']}")


def verify_integration_pull_request(
    repo: Path,
    manifest: dict[str, Any],
    layers: list[dict[str, Any]],
    links: dict[int, dict[str, Any]],
    pr: dict[str, Any],
    expected_body: str,
) -> None:
    _, owner, name = split_repository(manifest["repository"])
    payload = json.loads(
        gh_api(
            repo,
            manifest["repository"],
            f"repos/{owner}/{name}/pulls/{pr['number']}",
        )
    )
    combined = manifest["_integration_layer"]
    expected = {
        "state": "open",
        "draft": True,
        "title": combined["title"],
        "base": manifest["default_base"],
        "baseSha": manifest["base_sha"],
        "head": combined["remote_branch"],
        "sha": combined["_tip"],
        "owner": manifest["_evidence_owner"].casefold(),
    }
    actual = {
        "state": payload["state"],
        "draft": bool(payload["draft"]),
        "title": payload["title"],
        "base": payload["base"]["ref"],
        "baseSha": payload["base"]["sha"],
        "head": payload["head"]["ref"],
        "sha": payload["head"]["sha"],
        "owner": payload["head"]["repo"]["owner"]["login"].casefold(),
    }
    mismatches = [field for field, value in expected.items() if actual[field] != value]
    body = payload.get("body") or ""
    if body != expected_body:
        mismatches.append("body")
    required = (
        "> **Combined stack validation PR - DO NOT MERGE**",
        "## Component PRs",
        "## Combined-stack evidence",
        manifest["integration_evidence"]["_branch_url"],
        manifest["integration_evidence"]["_report_url"],
    )
    if any(value not in body for value in required):
        mismatches.append("body")
    if mismatches:
        raise SubmitError(
            "combined-stack validation PR mismatch: " + ", ".join(mismatches)
        )


def finalize_draft_state(
    repo: Path,
    manifest: dict[str, Any],
    pr: dict[str, Any],
) -> None:
    _, owner, name = split_repository(manifest["repository"])
    payload = json.loads(
        gh_api(
            repo,
            manifest["repository"],
            f"repos/{owner}/{name}/pulls/{pr['number']}",
        )
    )
    is_draft = bool(payload["draft"])
    if manifest.get("draft"):
        if not is_draft:
            raise SubmitError(f"PR #{pr['number']} is ready but manifest requires draft")
        return
    if not is_draft:
        return
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            gh(
                repo,
                manifest["repository"],
                "pr",
                "ready",
                pr["url"],
                retry_transient=False,
            )
            return
        except SubmitError as exc:
            current = json.loads(
                gh_api(
                    repo,
                    manifest["repository"],
                    f"repos/{owner}/{name}/pulls/{pr['number']}",
                )
            )
            if not current["draft"]:
                return
            if not is_transient_failure(str(exc)) or attempt == RETRY_ATTEMPTS:
                raise
            retry_delay(attempt, "gh pr ready")


def upsert_navigation_comment(
    repo: Path,
    manifest: dict[str, Any],
    pr: dict[str, Any],
    body: str,
) -> None:
    host, owner, name = split_repository(manifest["repository"])
    command = ["gh", "api"]
    if host:
        command.extend(["--hostname", host])
    endpoint = f"repos/{owner}/{name}/issues/{pr['number']}/comments"
    pages = json.loads(
        run(
            [*command, "--paginate", "--slurp", f"{endpoint}?per_page=100"],
            repo,
            retry_transient=True,
        )
        or "[]"
    )
    comments = [comment for page in pages for comment in page]
    match = next((item for item in comments if MARKER in (item.get("body") or "")), None)
    if match:
        comment_endpoint = f"repos/{owner}/{name}/issues/comments/{match['id']}"
        run(
            [*command, "--method", "PATCH", comment_endpoint, "-f", f"body={body}"],
            repo,
            retry_transient=True,
        )
    else:
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                gh(
                    repo,
                    manifest["repository"],
                    "pr",
                    "comment",
                    pr["url"],
                    "--body",
                    body,
                    retry_transient=False,
                )
                return
            except SubmitError as exc:
                if not is_transient_failure(str(exc)):
                    raise
                pages = json.loads(
                    run(
                        [
                            *command,
                            "--paginate",
                            "--slurp",
                            f"{endpoint}?per_page=100",
                        ],
                        repo,
                        retry_transient=True,
                    )
                    or "[]"
                )
                comments = [comment for page in pages for comment in page]
                if any(MARKER in (item.get("body") or "") for item in comments):
                    return
                if attempt == RETRY_ATTEMPTS:
                    raise
                retry_delay(attempt, "gh pr comment")


def load_apply_progress(
    path: Path | None,
    manifest_path: Path,
    manifest: dict[str, Any],
    layers: list[dict[str, Any]],
    approval: dict[str, Any],
) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise SubmitError("existing apply journal must be a regular file")
    try:
        progress_journal = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubmitError(f"cannot read existing apply journal: {exc}") from exc
    if not isinstance(progress_journal, dict):
        raise SubmitError("existing apply journal must be a JSON object")
    approval_receipt = {
        field: approval[field]
        for field in (
            "apply_request",
            "apply_request_sha256",
            "preparation_receipt",
            "preparation_receipt_sha256",
        )
    }
    if (
        progress_journal.get("status") not in {"preflight", "submitting", "complete"}
        or progress_journal.get("manifest") != str(manifest_path.resolve())
        or progress_journal.get("manifest_sha256") != sha256_file(manifest_path)
        or progress_journal.get("repository") != manifest["repository"]
        or progress_journal.get("base_sha") != manifest["base_sha"]
        or progress_journal.get("apply_approval") != approval_receipt
    ):
        raise SubmitError("existing apply journal does not match the sealed package")
    records = progress_journal.get("layers")
    if not isinstance(records, list) or len(records) != len(layers):
        raise SubmitError("existing apply journal has the wrong layer count")
    seen_gap = False
    for index, (layer, record) in enumerate(zip(layers, records)):
        if (
            not isinstance(record, dict)
            or record.get("branch") != layer["branch"]
            or record.get("remote_branch") != layer["remote_branch"]
            or record.get("tip") != layer["_tip"]
            or record.get("base")
            != component_base(layers, index, manifest["default_base"])
            or record.get("head") != layer["_head_ref"]
        ):
            raise SubmitError("existing apply journal layer topology changed")
        if "pr" not in record:
            seen_gap = True
        elif seen_gap:
            raise SubmitError("existing apply journal PR progress is not contiguous")
    return progress_journal


def submit(
    repo: Path,
    manifest_path: Path,
    apply: bool,
    manual: bool,
    output: Path | None,
    rendered_dir: Path | None,
    manual_links: Path | None,
    approved_dry_run: Path | None = None,
    approved_apply_request: Path | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    configure_command_environment(manifest["repository"])
    layers = validate(repo, manifest_path, manifest)
    if (apply or manual) and origin_review_required(manifest) and "_origin_review" not in manifest:
        raise SubmitError(
            "upstream apply is blocked until origin review is staged, audited, "
            f"and explicitly approved with {ORIGIN_REVIEW_APPROVAL_PHRASE!r}; "
            "then regenerate the manifest and dry-run journal"
        )
    approved_journal = (
        validate_approved_dry_run(
            approved_dry_run,
            manifest_path,
            manifest,
            layers,
        )
        if apply or manual
        else None
    )
    apply_approval = (
        validate_sealed_apply_request(
            repo,
            approved_apply_request,
            manifest_path,
            approved_dry_run,
            output,
        )
        if (apply or manual) and origin_review_required(manifest)
        else None
    )
    prior_progress = (
        load_apply_progress(output, manifest_path, manifest, layers, apply_approval)
        if apply and apply_approval is not None
        else None
    )
    links = (
        load_manual_links(manual_links, len(layers), manifest["repository"])
        if manual
        else {}
    )
    if not manual:
        github_preflight(repo, manifest, layers)
        existing_integration = manifest.get("_existing_integration_pr")
        if existing_integration:
            manifest["integration_evidence"]["_integration_pr_url"] = existing_integration["url"]
        links.update(
            {
                index: {
                    "number": layer["_existing_pr"]["number"],
                    "url": layer["_existing_pr"]["url"],
                }
                for index, layer in enumerate(layers)
                if layer.get("_existing_pr")
            }
        )
        existing_prs = [
            layer["_existing_pr"]
            for layer in layers
            if layer.get("_existing_pr")
        ]
        if manifest.get("_origin_review") and not apply and (
            existing_prs or manifest.get("_existing_integration_pr")
        ):
            raise SubmitError(
                "regenerated upstream dry run requires no pre-existing upstream PRs"
            )
        if apply and approved_journal is not None:
            prior_layers = prior_progress.get("layers", []) if prior_progress else []
            for index, layer in enumerate(layers):
                live = layer.get("_existing_pr")
                recorded = (
                    prior_layers[index].get("pr")
                    if index < len(prior_layers)
                    and isinstance(prior_layers[index], dict)
                    else None
                )
                if bool(live) != bool(recorded) or (
                    live
                    and (
                        live["number"] != recorded.get("number")
                        or live["url"] != recorded.get("url")
                    )
                ):
                    raise SubmitError(
                        "live upstream component PRs do not match the sealed apply journal"
                    )
                if live:
                    verify_pull_request(
                        repo,
                        manifest,
                        layers,
                        links,
                        index,
                        layer,
                        component_base(layers, index, manifest["default_base"]),
                        live,
                        True,
                        approved_journal["_approved_component_bodies"][index],
                    )
            live_integration = manifest.get("_existing_integration_pr")
            prior_integration = (
                prior_progress.get("integration_pr", {}).get("pr")
                if prior_progress
                else None
            )
            if bool(live_integration) != bool(prior_integration) or (
                live_integration
                and (
                    live_integration["number"] != prior_integration.get("number")
                    or live_integration["url"] != prior_integration.get("url")
                )
            ):
                raise SubmitError(
                    "live integration PR does not match the sealed apply journal"
                )
            if live_integration:
                verify_integration_pull_request(
                    repo,
                    manifest,
                    layers,
                    links,
                    live_integration,
                    approved_journal["_approved_integration_body"],
                )
    else:
        github_repository_preflight(repo, manifest)
        preflight_integration_pull_request(repo, manifest)
        verify_published_layers(repo, manifest, layers)
        discover_manual_links(repo, manifest, layers, links)
        for index, link in links.items():
            verify_pull_request(
                repo,
                manifest,
                layers,
                links,
                index,
                layers[index],
                component_base(layers, index, manifest["default_base"]),
                link,
                True,
                approved_journal["_approved_component_bodies"][index],
            )
        if manifest.get("_existing_integration_pr") and len(links) != len(layers):
            raise SubmitError(
                "existing combined-stack validation PR requires every component PR to be "
                "discovered and valid; reconcile the missing component, then regenerate"
            )
        existing_integration = manifest.get("_existing_integration_pr")
        if existing_integration:
            verify_integration_pull_request(
                repo,
                manifest,
                layers,
                links,
                existing_integration,
                approved_journal["_approved_integration_body"],
            )
            manifest["integration_evidence"]["_integration_pr_url"] = existing_integration["url"]
    journal: dict[str, Any] = {
        "status": "preflight",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": sha256_file(manifest_path),
        "repository": manifest["repository"],
        "head_repository": manifest["_head_repository"],
        "evidence_remote": manifest["evidence_remote"],
        "evidence_repository": manifest["_evidence_repository"],
        "default_base": manifest["default_base"],
        "base_sha": manifest["base_sha"],
        "stack_label": manifest["stack_label"],
        "template_source": manifest.get("template_source"),
        "origin_review": manifest.get("_origin_review"),
        "apply_approval": (
            {
                field: apply_approval[field]
                for field in (
                    "apply_request",
                    "apply_request_sha256",
                    "preparation_receipt",
                    "preparation_receipt_sha256",
                )
            }
            if apply_approval is not None
            else None
        ),
        "integration_evidence": {
            "branch": manifest["integration_evidence"]["branch"],
            "commit": manifest["integration_evidence"]["_commit"],
            "remote": manifest["evidence_remote"],
            "repository": manifest["_evidence_repository"],
            "branch_url": manifest["integration_evidence"]["_branch_url"],
            "report_url": manifest["integration_evidence"]["_report_url"],
        },
        "layers": [],
    }
    destination = rendered_dir or manifest_path.parent / "rendered"
    approved_component_bodies = (
        approved_journal["_approved_component_bodies"]
        if approved_journal is not None
        else []
    )
    approved_integration_body = (
        approved_journal["_approved_integration_body"]
        if approved_journal is not None
        else ""
    )
    destination.mkdir(parents=True, exist_ok=True)
    for index, layer in enumerate(layers):
        title = stacked_title(layer, index, len(layers), manifest["stack_label"])
        title_path = destination / f"{index + 1:02d}-title.txt"
        body_path = destination / f"{index + 1:02d}-body.md"
        title_path.write_text(title + "\n", encoding="utf-8")
        rendered_body = (
            approved_component_bodies[index]
            if apply or manual
            else render_body(
                layers,
                links,
                index,
                manifest["default_base"],
                manifest["feature_summary"],
                manifest["stack_label"],
                manifest["integration_evidence"],
                manifest["_head_owner"],
                manifest["feature_name"],
            )
        )
        if manifest["template_source"] == FALLBACK_TEMPLATE_SOURCE:
            validate_fallback_body(
                rendered_body,
                label=f"rendered layer {index + 1} body",
                allow_appendix_headings=True,
            )
        body_path.write_text(rendered_body, encoding="utf-8")
        journal_layer = {
            "branch": layer["branch"],
            "remote_branch": layer["remote_branch"],
            "tip": layer["_tip"],
            "base": component_base(layers, index, manifest["default_base"]),
            "head": layer["_head_ref"],
            "title": title,
            "source_title": layer["title"],
            "source_body": str(layer["_body_file"].resolve()),
            "rendered_title": str(title_path),
            "rendered_body": str(body_path),
        }
        if index in links:
            journal_layer["pr"] = links[index]
        journal["layers"].append(journal_layer)
    combined = manifest["_integration_layer"]
    combined_title_path = destination / "integration-title.txt"
    combined_body_path = destination / "integration-body.md"
    combined_title_path.write_text(combined["title"] + "\n", encoding="utf-8")
    combined_body_path.write_text(
        approved_integration_body
        if apply or manual
        else render_integration_body(manifest, layers, links),
        encoding="utf-8",
    )
    journal["integration_pr"] = {
        "branch": combined["remote_branch"],
        "tip": combined["_tip"],
        "base": manifest["default_base"],
        "head": combined["_head_ref"],
        "head_repository": manifest["_evidence_repository"],
        "title": combined["title"],
        "rendered_title": str(combined_title_path),
        "rendered_body": str(combined_body_path),
        "draft": True,
        "merge": "prohibited",
    }
    if manifest.get("_existing_integration_pr"):
        existing = manifest["_existing_integration_pr"]
        journal["integration_pr"]["pr"] = {
            "number": existing["number"],
            "url": existing["url"],
        }
    write_journal(output, journal)
    if manual:
        links_path = manual_links or destination / "manual-links.json"
        if not links_path.exists():
            links_path.write_text('{\n  "prs": []\n}\n', encoding="utf-8")
        for index in links:
            (destination / f"{index + 1:02d}-navigation.md").write_text(
                render_navigation(
                    layers,
                    links,
                    index,
                    manifest["default_base"],
                    manifest["stack_label"],
                    manifest["_head_owner"],
                ),
                encoding="utf-8",
            )
        if len(links) == len(layers) and manifest.get("_existing_integration_pr"):
            (destination / "integration-navigation.md").write_text(
                render_navigation(
                    layers,
                    links,
                    None,
                    manifest["default_base"],
                    manifest["stack_label"],
                    manifest["_head_owner"],
                ),
                encoding="utf-8",
            )
        instructions = destination / "SUBMIT.md"
        instructions.write_text(
            render_manual_instructions(
                manifest,
                layers,
                links,
                manifest_path,
                destination,
                links_path,
                output,
            ),
            encoding="utf-8",
        )
        journal["status"] = "manual-package"
        journal["manual_package"] = str(destination)
        journal["instructions"] = str(instructions)
        journal["manual_links"] = str(links_path)
        write_journal(output, journal)
        return journal
    if not apply:
        journal["status"] = "dry-run"
        write_journal(output, journal)
        return journal
    refreshed_approval = (
        validate_sealed_apply_request(
            repo,
            approved_apply_request,
            manifest_path,
            approved_dry_run,
            output,
        )
        if origin_review_required(manifest)
        else None
    )
    if refreshed_approval != apply_approval:
        raise SubmitError("sealed apply approval changed before mutation")
    verify_published_layers(repo, manifest, layers)
    if origin_review_required(manifest):
        validate_origin_review_approval(repo, manifest_path, manifest, layers)
    verify_published_evidence(repo, manifest)
    for index, layer in enumerate(layers):
        progress("publish", f"{index + 1}/{len(layers)}: {layer['remote_branch']}")
        publish(repo, manifest, layer)
    if origin_review_required(manifest):
        validate_origin_review_approval(repo, manifest_path, manifest, layers)
    for index, layer in enumerate(layers):
        if origin_review_required(manifest):
            validate_origin_review_approval(repo, manifest_path, manifest, layers)
        base = component_base(layers, index, manifest["default_base"])
        existing = layer.get("_existing_pr")
        title = stacked_title(layer, index, len(layers), manifest["stack_label"])
        action = "updating" if existing else "creating"
        progress(
            "submit",
            f"{index + 1}/{len(layers)}: {action} {layer['remote_branch']} -> {base}",
        )
        if existing:
            pr = existing
        else:
            body = approved_component_bodies[index]
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                suffix=".md",
                dir=destination,
            ) as handle:
                handle.write(body)
                handle.flush()
                pr = create_pull_request(repo, manifest, layer, base, handle.name, title)
        links[index] = {"number": pr["number"], "url": pr["url"]}
        progress("submit", f"recorded PR #{pr['number']}: {pr['url']}")
        journal["layers"][index]["pr"] = links[index]
        journal["status"] = "submitting"
        write_journal(output, journal)

    combined = manifest["_integration_layer"]
    if origin_review_required(manifest):
        validate_origin_review_approval(repo, manifest_path, manifest, layers)
    verify_published_evidence(repo, manifest)
    combined_body = approved_integration_body
    existing_combined = manifest.get("_existing_integration_pr")
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        suffix=".md",
        dir=destination,
    ) as handle:
        handle.write(combined_body)
        handle.flush()
        if existing_combined:
            integration_pr = existing_combined
        else:
            integration_pr = create_pull_request(
                repo,
                manifest,
                combined,
                manifest["default_base"],
                handle.name,
                combined["title"],
            )
    manifest["integration_evidence"]["_integration_pr_url"] = integration_pr["url"]
    journal["integration_pr"]["pr"] = {
        "number": integration_pr["number"],
        "url": integration_pr["url"],
    }
    journal["status"] = "submitting"
    write_journal(output, journal)

    for index, layer in enumerate(layers):
        progress(
            "finalize",
            f"{index + 1}/{len(layers)}: linking PR #{links[index]['number']}",
        )
        navigation = render_navigation(
            layers,
            links,
            index,
            manifest["default_base"],
            manifest["stack_label"],
            manifest["_head_owner"],
        )
        upsert_navigation_comment(repo, manifest, links[index], navigation)
    integration_navigation = render_navigation(
        layers,
        links,
        None,
        manifest["default_base"],
        manifest["stack_label"],
        manifest["_head_owner"],
    )
    upsert_navigation_comment(
        repo,
        manifest,
        integration_pr,
        integration_navigation,
    )

    for index, layer in enumerate(layers):
        verify_pull_request(
            repo,
            manifest,
            layers,
            links,
            index,
            layer,
            component_base(layers, index, manifest["default_base"]),
            links[index],
            None,
            approved_component_bodies[index],
        )
    verify_integration_pull_request(
        repo,
        manifest,
        layers,
        links,
        integration_pr,
        approved_integration_body,
    )

    if remote_sha(repo, manifest["target_remote"], manifest["default_base"]) != manifest["base_sha"]:
        raise SubmitError("target base branch moved during submission; PRs remain draft")
    verify_published_layers(repo, manifest, layers)
    verify_published_evidence(repo, manifest)
    validate_release_branch_placement(repo, manifest, layers)

    if origin_review_required(manifest):
        validate_origin_review_approval(repo, manifest_path, manifest, layers)
    for index in range(len(layers)):
        finalize_draft_state(repo, manifest, links[index])

    for index, layer in enumerate(layers):
        verify_pull_request(
            repo,
            manifest,
            layers,
            links,
            index,
            layer,
            component_base(layers, index, manifest["default_base"]),
            links[index],
            bool(manifest.get("draft")),
            approved_component_bodies[index],
        )
    verify_integration_pull_request(
        repo,
        manifest,
        layers,
        links,
        integration_pr,
        approved_integration_body,
    )
    if origin_review_required(manifest):
        validate_origin_review_approval(repo, manifest_path, manifest, layers)
    journal["status"] = "complete"
    write_journal(output, journal)
    return journal


def main() -> int:
    global VERBOSE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--manual", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--rendered-dir", type=Path)
    parser.add_argument("--manual-links", type=Path)
    parser.add_argument("--approved-dry-run-journal", type=Path)
    parser.add_argument("--approved-apply-request", type=Path)
    parser.add_argument("--verbose", action="store_true", help="show sanitized git/gh commands")
    args = parser.parse_args()
    VERBOSE = args.verbose
    try:
        result = submit(
            args.repo.resolve(),
            args.manifest.resolve(),
            args.apply,
            args.manual,
            args.output.resolve() if args.output else None,
            args.rendered_dir.resolve() if args.rendered_dir else None,
            args.manual_links.resolve() if args.manual_links else None,
            (
                args.approved_dry_run_journal.resolve()
                if args.approved_dry_run_journal
                else None
            ),
            (
                args.approved_apply_request.resolve()
                if args.approved_apply_request
                else None
            ),
        )
    except SubmitError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not args.output:
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
