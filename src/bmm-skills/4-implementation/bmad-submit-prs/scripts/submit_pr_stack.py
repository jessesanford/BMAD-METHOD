#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Validate, render, and idempotently submit a reviewer-friendly GitHub PR stack."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

MARKER = "<!-- bmad-stack-navigation:v1 -->"
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
    if host and host.casefold() != "github.com" and COMMAND_ENV.pop("GH_TOKEN", None):
        progress(
            "auth",
            f"ignoring GH_TOKEN for enterprise host {host}; using GH_ENTERPRISE_TOKEN "
            "or the stored gh credential",
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
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if len(parts) != 2:
        raise SubmitError(f"cannot parse remote URL: {value}")
    return parsed.hostname, parts[0], parts[1]


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
    if manifest.get("schema_version") != 2 or not manifest.get("layers"):
        raise SubmitError("manifest requires schema_version 2 and non-empty layers")
    return manifest


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
    if remote_sha(repo, manifest["publish_remote"], evidence["branch"]) != commit:
        raise SubmitError("integration evidence branch is not published at its recorded commit")

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

    required_report_content = (
        evidence["test_command"],
        f"{tests['passed']} passed, {tests['skipped']} skipped, {tests['warnings']} warnings",
        f"{validated}/{total}",
        feature_flag["name"],
        *prefix_tips,
        *(value for build in builds for value in (build["artifact"], build["sha256"])),
    )
    if any(value not in report for value in required_report_content):
        raise SubmitError("committed integration report does not substantiate the manifest evidence")

    web_url = repository_web_url(manifest["_head_repository"])
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
    if (
        len(manifest["stack_label"]) > 24
        or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+){0,3}", manifest["stack_label"])
    ):
        raise SubmitError("stack_label must be 1-4 lowercase keywords, at most 24 characters")
    expected_host, expected_owner, expected_name = split_repository(manifest["repository"])
    target_url = git(repo, "remote", "get-url", manifest["target_remote"])
    target_host, target_owner, target_name = parse_remote(target_url)
    manifest_host = expected_host or "github.com"
    if (target_owner.lower(), target_name.lower()) != (
        expected_owner.lower(),
        expected_name.lower(),
    ):
        raise SubmitError("target_remote does not point to the manifest repository")
    if not target_host or manifest_host.lower() != target_host.lower():
        raise SubmitError("target_remote host does not match the manifest repository")

    publish_url = git(repo, "remote", "get-url", manifest["publish_remote"])
    publish_host, publish_owner, publish_name = parse_remote(publish_url)
    if not publish_host or not target_host or target_host.lower() != publish_host.lower():
        raise SubmitError("target and publish remotes must use the same GitHub host")
    manifest["_head_owner"] = publish_owner
    manifest["_head_repository"] = (
        f"{publish_host}/{publish_owner}/{publish_name}"
        if publish_host
        else f"{publish_owner}/{publish_name}"
    )
    manifest["_cross_repository"] = (
        publish_owner.casefold(),
        publish_name.casefold(),
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
    for index, layer in enumerate(layers):
        missing = [
            field
            for field in ("branch", "remote_branch", "tip", "title", "summary", "body_file")
            if not layer.get(field)
        ]
        if missing:
            raise SubmitError(f"layer {index + 1} missing: {', '.join(missing)}")
        if not layer["branch"].endswith("-pr-ready"):
            raise SubmitError(f"layer branch is not PR-ready: {layer['branch']}")
        layer["_tip"] = resolve(repo, layer["tip"])
        if resolve(repo, layer["branch"]) != layer["_tip"]:
            raise SubmitError(f"branch drifted from manifest tip: {layer['branch']}")
        body = Path(layer["body_file"])
        body = body if body.is_absolute() else path.parent / body
        if not body.is_file():
            raise SubmitError(f"body file missing: {body}")
        layer["_body_file"] = body
        if layer["remote_branch"] in targets:
            raise SubmitError(f"duplicate remote branch: {layer['remote_branch']}")
        targets.add(layer["remote_branch"])
        layer["_head_ref"] = (
            f"{publish_owner}:{layer['remote_branch']}"
            if manifest["_cross_repository"]
            else layer["remote_branch"]
        )
        expected_parent = prior or stack_base
        if not is_ancestor(repo, expected_parent, layer["_tip"]):
            raise SubmitError(f"layer {index + 1} does not descend from layer {index}")
        prior = layer["_tip"]
    validate_integration_evidence(repo, manifest, layers)
    if manifest["integration_evidence"]["branch"] in targets:
        raise SubmitError("integration evidence branch must differ from component PR branches")
    manifest["_integration_layer"] = integration_layer(manifest)
    return layers


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
                "> After this PR merges, refresh **Files changed** on later PRs.",
                "> If prerequisite changes remain, stop: the remaining heads must be restacked before review.",
            ]
        )
    else:
        lines.extend(
            [
                f"> This is PR {current + 1} of {len(layers)} in the PR stack for the {feature_name} feature.",
                "> Please review all PRs in the stack in order.",
                f"> **DO NOT APPROVE until every PR below is merged into `{default_base}`:**",
                ">",
            ]
        )
        for index in range(current):
            lines.append(f"> {index + 1}. {prerequisite_link(index, layers, links, stack_label)}")
        lines.extend(
            [
                ">",
                "> After all listed PRs merge, refresh **Files changed**; "
                "if prerequisite changes remain, stop and restack this head before review.",
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
            f"`{default_base}` and keeps its head on `{head_owner}`. Later PRs therefore show",
            "cumulative changes until their prerequisite PRs merge. Review and merge strictly from the",
            "first layer through the last; after each merge, refresh the remaining PRs so GitHub",
            "recalculates their diffs. Squash or rebase merges may require restacking the remaining",
            "heads; do not approve a PR while prerequisite changes remain in Files changed.",
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
            f"`{default_base}` | `{head_owner}:{layer['remote_branch']}` | {link} |"
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
        "does not leave `main` in an unvalidated partial state. "
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
    return {
        "remote_branch": branch,
        "_head_ref": (
            f"{manifest['_head_owner']}:{branch}"
            if manifest["_cross_repository"]
            else branch
        ),
        "_tip": manifest["integration_evidence"]["_commit"],
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


def write_journal(path: Path | None, payload: dict[str, Any]) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def remote_sha(repo: Path, remote: str, branch: str) -> str | None:
    output = git(repo, "ls-remote", "--heads", remote, f"refs/heads/{branch}")
    return output.split()[0] if output else None


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
        f"**Head repository:** `{manifest['_head_repository']}`  ",
        f"**Base for every PR:** `{manifest['default_base']}`  ",
        f"**PR count:** {len(layers)}",
        "",
        "## Submit in this order",
        "",
        "Create the PRs from top to bottom. For each row, copy the numbered title file into the",
        "GitHub title field and the matching body file into the description field. The body files",
        "use the same template, explicit prerequisite warning, and stack graph as automatic submission.",
        "Every PR targets the same base; do not approve a later PR until all PRs listed at its top",
        "have merged and its Files changed view has been refreshed.",
        "",
        "| # | Base | Head | Title | Body | Action | PR |",
        "|---:|---|---|---|---|---|---|",
    ]
    web_url = repository_web_url(manifest["repository"])
    for index, layer in enumerate(layers):
        position = index + 1
        base = manifest["default_base"]
        head = layer["_head_ref"]
        create_url = (
            f"{web_url}/compare/{quote(base, safe='/')}..."
            f"{quote(head, safe='/:')}?expand=1"
        )
        pr = links.get(index)
        pr_link = f"[#{pr['number']}]({pr['url']})" if pr else "Pending"
        if pr:
            action = "Update existing"
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
                f'gh pr edit "{pr["url"]}" --title "$(cat {position:02d}-title.txt)" '
                f'--body-file "{position:02d}-body.md"'
            )
        elif index == len(links):
            lines.append(
                f'gh pr create --repo "{manifest["repository"]}" '
                f'--base "{manifest["default_base"]}" '
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
    if len(links) == len(layers):
        existing = manifest.get("_existing_integration_pr")
        if existing:
            lines.extend(
                [
                    "The exact draft validation PR already exists. Refresh its generated content:",
                    "",
                    "```bash",
                    f'gh pr edit "{existing["url"]}" '
                    '--title "$(cat integration-title.txt)" '
                    '--body-file "integration-body.md"',
                    "```",
                ]
            )
        else:
            lines.extend(
                [
                    "All component links are verified. Create the integration PR as a draft; never",
                    "mark it ready and never merge it. Then rerun this package so every component",
                    "body receives the integration-PR link before any component is marked ready:",
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
    else:
        lines.extend(
            [
                "Not ready. Record and verify every component PR link, then regenerate this package.",
                "No integration-PR create command is emitted while any component is Pending.",
            ]
        )
    existing_integration = manifest.get("_existing_integration_pr")
    if len(links) == len(layers) and existing_integration and not manifest.get("draft"):
        lines.extend(
            [
                "",
                "## Mark component PRs ready after final refresh",
                "",
                "The combined validation PR has been discovered and linked in every generated body.",
                "After the update commands above succeed, mark each component PR ready; never mark the",
                "combined validation PR ready:",
                "",
                "```bash",
                *[f'gh pr ready "{links[index]["url"]}"' for index in range(len(layers))],
                "```",
            ]
        )
    lines.extend(
        [
            "",
            "## Make submitted PRs clickable in later descriptions",
            "",
            f"After creating each PR, record it in `{links_path.name}` using its 1-based stack position:",
            "",
            "```json",
            '{"prs":[{"position":1,"number":123,"url":"https://github.example/owner/repo/pull/123"}]}',
            "```",
            "",
            "Rerun manual packaging before creating the next PR. Existing positions become clickable in",
            "every regenerated merge gate and graph; future positions remain clearly marked Pending:",
            "",
            "```bash",
            f'uv run "{Path(__file__).resolve()}" "{manifest_path}" --manual '
            f'--manual-links "{links_path}" --rendered-dir "{destination}"'
            + (f' --output "{output}"' if output else ""),
            "```",
            "",
            render_navigation(
                layers,
                links,
                None,
                manifest["default_base"],
                manifest["stack_label"],
                manifest["_head_owner"],
            ).rstrip(),
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
    progress(
        "preflight",
        f"checking target {manifest['repository']} and fork heads {manifest['_head_repository']}",
    )
    if host:
        run(["gh", "auth", "status", "--hostname", host], repo, retry_transient=True)
    target = json.loads(gh_api(repo, manifest["repository"], f"repos/{owner}/{name}"))
    source = json.loads(
        gh_api(repo, manifest["repository"], f"repos/{head_owner}/{head_name}")
    )
    if not source.get("permissions", {}).get("push"):
        raise SubmitError("authenticated user lacks push permission for fork head branches")
    if (
        manifest["_cross_repository"]
        and repository_network_root(source) != repository_network_root(target)
    ):
        raise SubmitError("publish_remote repository is not in the target repository's fork network")

    target_base = remote_sha(repo, manifest["target_remote"], manifest["default_base"])
    if target_base != manifest["base_sha"]:
        raise SubmitError("target base branch moved after manifest creation")


def preflight_integration_pull_request(repo: Path, manifest: dict[str, Any]) -> None:
    combined = integration_layer(manifest)
    existing = pull_requests_for_head(
        repo,
        manifest["repository"],
        manifest["_head_owner"],
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
            or pr["headRefOid"] != combined["_tip"]
            or pr["headRepositoryOwner"].casefold()
            != manifest["_head_owner"].casefold()
        ):
            raise SubmitError("existing combined-stack validation PR conflicts")
        manifest["_existing_integration_pr"] = pr
    manifest["_integration_layer"] = combined


def github_preflight(repo: Path, manifest: dict[str, Any], layers: list[dict[str, Any]]) -> None:
    github_repository_preflight(repo, manifest)
    repository = manifest["repository"]
    for index, layer in enumerate(layers):
        expected_base = manifest["default_base"]
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
        if len(existing) > 1:
            raise SubmitError(f"multiple PRs exist for {layer['remote_branch']}")
        if existing:
            pr = existing[0]
            if (
                pr["state"] != "OPEN"
                or pr["baseRefName"] != expected_base
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
        if published and published != layer["_tip"] and not existing:
            raise SubmitError(
                f"refusing to replace unowned fork branch without an open PR: {layer['remote_branch']}"
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
            or pr["baseRefName"] != manifest["default_base"]
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


def pull_requests_for_head(
    repo: Path,
    repository: str,
    head_owner: str,
    remote_branch: str,
) -> list[dict[str, Any]]:
    _, owner, name = split_repository(repository)
    head = quote(f"{head_owner}:{remote_branch}", safe=":")
    payload = json.loads(
        gh_api(
            repo,
            repository,
            f"repos/{owner}/{name}/pulls?state=all&head={head}&per_page=100",
        )
        or "[]"
    )
    return [
        {
            "number": item["number"],
            "url": item["html_url"],
            "state": item["state"].upper(),
            "isDraft": bool(item["draft"]),
            "baseRefName": item["base"]["ref"],
            "headRefName": item["head"]["ref"],
            "headRefOid": item["head"]["sha"],
            "headRepositoryOwner": item["head"]["repo"]["owner"]["login"],
        }
        for item in payload
    ]


def reconcile_created_pull_request(
    repo: Path,
    manifest: dict[str, Any],
    layer: dict[str, Any],
    expected_base: str,
) -> dict[str, Any] | None:
    existing = pull_requests_for_head(
        repo,
        manifest["repository"],
        manifest["_head_owner"],
        layer["remote_branch"],
    )
    if not existing:
        return None
    if len(existing) > 1:
        raise SubmitError(f"multiple PRs exist for {layer['remote_branch']}")
    pr = existing[0]
    if (
        pr["state"] != "OPEN"
        or pr["baseRefName"] != expected_base
        or pr["headRefOid"] != layer["_tip"]
        or pr["headRepositoryOwner"].casefold() != manifest["_head_owner"].casefold()
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
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            url = gh(
                repo,
                manifest["repository"],
                *arguments,
                retry_transient=False,
            ).splitlines()[-1]
            return {
                "number": int(url.rstrip("/").rsplit("/", 1)[-1]),
                "url": url,
                "state": "OPEN",
            }
        except SubmitError as exc:
            progress(
                "submit",
                f"create failed for {layer['remote_branch']}; checking remote state",
            )
            existing = reconcile_created_pull_request(repo, manifest, layer, base)
            if existing:
                progress("submit", f"reconciled PR #{existing['number']} after create failure")
                return existing
            if not is_transient_failure(str(exc)):
                raise
            if attempt == RETRY_ATTEMPTS:
                raise
            retry_delay(attempt, "gh pr create")
    raise AssertionError("retry loop exhausted")


def publish(repo: Path, manifest: dict[str, Any], layer: dict[str, Any]) -> None:
    remote = manifest["publish_remote"]
    old = remote_sha(repo, remote, layer["remote_branch"])
    if old == layer["_tip"]:
        return
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
        "headRefName": payload["head"]["ref"],
        "headRefOid": payload["head"]["sha"],
        "headRepositoryOwner": payload["head"]["repo"]["owner"]["login"],
        "body": payload.get("body") or "",
    }
    expected = {
        "state": "OPEN",
        "title": stacked_title(layer, index, len(layers), manifest["stack_label"]),
        "baseRefName": expected_base,
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
    expected_body = render_body(
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
        *(link["url"] for link in links.values()),
        *(
            [evidence["_integration_pr_url"]]
            if evidence.get("_integration_pr_url")
            else []
        ),
    )
    if any(value not in body for value in required_body_content):
        raise SubmitError(f"submitted PR evidence is incomplete for {layer['remote_branch']}")
    warning = body.split("\n\n", 1)[0]
    for prior in range(index):
        if links[prior]["url"] not in warning:
            raise SubmitError(
                f"submitted PR warning omits prerequisite {prior + 1} for {layer['remote_branch']}"
            )


def verify_integration_pull_request(
    repo: Path,
    manifest: dict[str, Any],
    layers: list[dict[str, Any]],
    links: dict[int, dict[str, Any]],
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
    combined = manifest["_integration_layer"]
    expected = {
        "state": "open",
        "draft": True,
        "title": combined["title"],
        "base": manifest["default_base"],
        "head": combined["remote_branch"],
        "sha": combined["_tip"],
        "owner": manifest["_head_owner"].casefold(),
    }
    actual = {
        "state": payload["state"],
        "draft": bool(payload["draft"]),
        "title": payload["title"],
        "base": payload["base"]["ref"],
        "head": payload["head"]["ref"],
        "sha": payload["head"]["sha"],
        "owner": payload["head"]["repo"]["owner"]["login"].casefold(),
    }
    mismatches = [field for field, value in expected.items() if actual[field] != value]
    body = payload.get("body") or ""
    if body != render_integration_body(manifest, layers, links):
        mismatches.append("body")
    required = (
        "> **Combined stack validation PR - DO NOT MERGE**",
        "## Component PRs",
        "## Combined-stack evidence",
        manifest["integration_evidence"]["_branch_url"],
        manifest["integration_evidence"]["_report_url"],
        *(link["url"] for link in links.values()),
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


def submit(
    repo: Path,
    manifest_path: Path,
    apply: bool,
    manual: bool,
    output: Path | None,
    rendered_dir: Path | None,
    manual_links: Path | None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    configure_command_environment(manifest["repository"])
    layers = validate(repo, manifest_path, manifest)
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
    else:
        github_repository_preflight(repo, manifest)
        preflight_integration_pull_request(repo, manifest)
        if manifest.get("_existing_integration_pr") and len(links) != len(layers):
            raise SubmitError(
                "existing combined-stack validation PR requires every manual component link"
            )
        verify_published_layers(repo, manifest, layers)
        validate_manual_links_live(repo, manifest, layers, links)
        existing_integration = manifest.get("_existing_integration_pr")
        if existing_integration:
            manifest["integration_evidence"]["_integration_pr_url"] = existing_integration["url"]
    journal: dict[str, Any] = {
        "status": "preflight",
        "repository": manifest["repository"],
        "head_repository": manifest["_head_repository"],
        "default_base": manifest["default_base"],
        "base_sha": manifest["base_sha"],
        "stack_label": manifest["stack_label"],
        "template_source": manifest.get("template_source"),
        "integration_evidence": {
            "branch": manifest["integration_evidence"]["branch"],
            "commit": manifest["integration_evidence"]["_commit"],
            "branch_url": manifest["integration_evidence"]["_branch_url"],
            "report_url": manifest["integration_evidence"]["_report_url"],
        },
        "layers": [],
    }
    destination = rendered_dir or manifest_path.parent / "rendered"
    destination.mkdir(parents=True, exist_ok=True)
    for index, layer in enumerate(layers):
        title = stacked_title(layer, index, len(layers), manifest["stack_label"])
        title_path = destination / f"{index + 1:02d}-title.txt"
        body_path = destination / f"{index + 1:02d}-body.md"
        title_path.write_text(title + "\n", encoding="utf-8")
        body_path.write_text(
            render_body(
                layers,
                links,
                index,
                manifest["default_base"],
                manifest["feature_summary"],
                manifest["stack_label"],
                manifest["integration_evidence"],
                manifest["_head_owner"],
                manifest["feature_name"],
            ),
            encoding="utf-8",
        )
        journal_layer = {
            "branch": layer["branch"],
            "remote_branch": layer["remote_branch"],
            "tip": layer["_tip"],
            "base": manifest["default_base"],
            "head": layer["_head_ref"],
            "title": title,
            "source_title": layer["title"],
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
        render_integration_body(manifest, layers, links),
        encoding="utf-8",
    )
    journal["integration_pr"] = {
        "branch": combined["remote_branch"],
        "tip": combined["_tip"],
        "base": manifest["default_base"],
        "head": combined["_head_ref"],
        "title": combined["title"],
        "rendered_title": str(combined_title_path),
        "rendered_body": str(combined_body_path),
        "draft": True,
        "merge": "prohibited",
    }
    write_journal(output, journal)
    if manual:
        links_path = manual_links or destination / "manual-links.json"
        if not links_path.exists():
            links_path.write_text('{\n  "prs": []\n}\n', encoding="utf-8")
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
    for index, layer in enumerate(layers):
        progress("publish", f"{index + 1}/{len(layers)}: {layer['remote_branch']}")
        publish(repo, manifest, layer)
    for index, layer in enumerate(layers):
        base = manifest["default_base"]
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
            body = render_body(
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
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md") as handle:
                handle.write(body)
                handle.flush()
                pr = create_pull_request(repo, manifest, layer, base, handle.name, title)
        links[index] = {"number": pr["number"], "url": pr["url"]}
        progress("submit", f"recorded PR #{pr['number']}: {pr['url']}")
        journal["layers"][index]["pr"] = links[index]
        journal["status"] = "submitting"
        write_journal(output, journal)

    combined = manifest["_integration_layer"]
    combined_body = render_integration_body(manifest, layers, links)
    existing_combined = manifest.get("_existing_integration_pr")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md") as handle:
        handle.write(combined_body)
        handle.flush()
        if existing_combined:
            gh(
                repo,
                manifest["repository"],
                "pr",
                "edit",
                existing_combined["url"],
                "--title",
                combined["title"],
                "--body-file",
                handle.name,
                "--base",
                manifest["default_base"],
            )
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
        body = render_body(
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
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md") as handle:
            handle.write(body)
            handle.flush()
            gh(
                repo,
                manifest["repository"],
                "pr",
                "edit",
                links[index]["url"],
                "--title",
                stacked_title(layer, index, len(layers), manifest["stack_label"]),
                "--body-file",
                handle.name,
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

    for index, layer in enumerate(layers):
        title = stacked_title(layer, index, len(layers), manifest["stack_label"])
        Path(journal["layers"][index]["rendered_title"]).write_text(
            title + "\n",
            encoding="utf-8",
        )
        Path(journal["layers"][index]["rendered_body"]).write_text(
            render_body(
                layers,
                links,
                index,
                manifest["default_base"],
                manifest["feature_summary"],
                manifest["stack_label"],
                manifest["integration_evidence"],
                manifest["_head_owner"],
                manifest["feature_name"],
            ),
            encoding="utf-8",
        )
    Path(journal["integration_pr"]["rendered_title"]).write_text(
        combined["title"] + "\n",
        encoding="utf-8",
    )
    Path(journal["integration_pr"]["rendered_body"]).write_text(
        render_integration_body(manifest, layers, links),
        encoding="utf-8",
    )

    for index, layer in enumerate(layers):
        verify_pull_request(
            repo,
            manifest,
            layers,
            links,
            index,
            layer,
            manifest["default_base"],
            links[index],
            None,
        )
    verify_integration_pull_request(
        repo,
        manifest,
        layers,
        links,
        integration_pr,
    )

    if remote_sha(repo, manifest["target_remote"], manifest["default_base"]) != manifest["base_sha"]:
        raise SubmitError("target base branch moved during submission; PRs remain draft")

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
            manifest["default_base"],
            links[index],
            bool(manifest.get("draft")),
        )
    verify_integration_pull_request(
        repo,
        manifest,
        layers,
        links,
        integration_pr,
    )
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
        )
    except SubmitError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not args.output:
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
