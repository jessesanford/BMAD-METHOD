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
    if manifest.get("schema_version") != 1 or not manifest.get("layers"):
        raise SubmitError("manifest requires schema_version 1 and non-empty layers")
    return manifest


def load_manual_links(path: Path | None, layer_count: int) -> dict[int, dict[str, Any]]:
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
        if (
            not isinstance(position, int)
            or not 1 <= position <= layer_count
            or not isinstance(number, int)
            or number < 1
            or not isinstance(url, str)
            or not url.startswith(("https://", "http://"))
        ):
            raise SubmitError("manual PR links require valid position, number, and URL fields")
        links[position - 1] = {"number": number, "url": url}
    return links


def validate(repo: Path, path: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if git(repo, "status", "--porcelain"):
        raise SubmitError("worktree must be clean")
    for field in ("repository", "publish_remote", "default_base", "feature_summary", "stack_label"):
        if not manifest.get(field):
            raise SubmitError(f"manifest missing {field}")
    if (
        len(manifest["stack_label"]) > 24
        or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+){0,3}", manifest["stack_label"])
    ):
        raise SubmitError("stack_label must be 1-4 lowercase keywords, at most 24 characters")
    expected_host, expected_owner, expected_name = split_repository(manifest["repository"])
    remote_url = git(repo, "remote", "get-url", manifest["publish_remote"])
    host, owner, name = parse_remote(remote_url)
    if (owner.lower(), name.lower()) != (expected_owner.lower(), expected_name.lower()):
        raise SubmitError("publish_remote does not point to the manifest repository")
    if expected_host and host and expected_host.lower() != host.lower():
        raise SubmitError("publish_remote host does not match the manifest repository")
    targets: set[str] = set()
    prior: str | None = None
    layers = manifest["layers"]
    stack_base = resolve(repo, f"refs/remotes/{manifest['publish_remote']}/{manifest['default_base']}")
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
        expected_parent = prior or stack_base
        if not is_ancestor(repo, expected_parent, layer["_tip"]):
            raise SubmitError(f"layer {index + 1} does not descend from layer {index}")
        prior = layer["_tip"]
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


def render_navigation(
    layers: list[dict[str, Any]],
    links: dict[int, dict[str, Any]],
    current: int | None,
    default_base: str,
    stack_label: str,
) -> str:
    lines = [MARKER, "## Stack navigation", ""]
    if current is not None:
        lines.extend([f"**This PR:** {current + 1} of {len(layers)}", ""])
    lines.extend(
        [
            "This PR is part of a [stacked pull request](https://www.stacking.dev/), split into",
            "small, dependency-ordered changes for focused review.",
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
    lines.extend(["```", "", "| # | Layer | Base | PR |", "|---:|---|---|---|"])
    for index, layer in enumerate(layers):
        base = f"`{default_base}`" if index == 0 else f"`{layers[index - 1]['remote_branch']}`"
        pr = links.get(index)
        link = f"[#{pr['number']}]({pr['url']})" if pr else "Pending"
        rendered_title = stacked_title(layer, index, len(layers), stack_label)
        title = f"[{rendered_title}]({pr['url']})" if pr else f"{rendered_title} (Pending)"
        here = " **(this PR)**" if current == index else ""
        lines.append(f"| {index + 1} | {title} - {layer['summary']}{here} | {base} | {link} |")
    return "\n".join(lines) + "\n"


def render_body(
    layers: list[dict[str, Any]],
    links: dict[int, dict[str, Any]],
    index: int,
    default_base: str,
    feature_summary: str,
    stack_label: str,
) -> str:
    content = layers[index]["_body_file"].read_text(encoding="utf-8").rstrip()
    planning = links.get(0)
    pointer = ""
    if index and planning:
        pointer = (
            f"\n\n**Feature context:** {feature_summary} "
            f"See [Planning PR #{planning['number']}]({planning['url']}) for the complete design and rollout."
        )
    return (
        content
        + pointer
        + "\n\n"
        + render_navigation(layers, links, index, default_base, stack_label)
    )


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
        f"**Repository:** `{manifest['repository']}`  ",
        f"**First base:** `{manifest['default_base']}`  ",
        f"**PR count:** {len(layers)}",
        "",
        "## Submit in this order",
        "",
        "Create the PRs from top to bottom. For each row, copy the numbered title file into the",
        "GitHub title field and the matching body file into the description field. The body files",
        "use the same template and stack graph as automatic submission.",
        "",
        "| # | Base | Head | Title | Body | Create | PR |",
        "|---:|---|---|---|---|---|---|",
    ]
    web_url = repository_web_url(manifest["repository"])
    for index, layer in enumerate(layers):
        position = index + 1
        base = manifest["default_base"] if index == 0 else layers[index - 1]["remote_branch"]
        head = layer["remote_branch"]
        create_url = f"{web_url}/compare/{quote(base, safe='/')}...{quote(head, safe='/')}?expand=1"
        pr = links.get(index)
        pr_link = f"[#{pr['number']}]({pr['url']})" if pr else "Pending"
        lines.append(
            f"| {position} | `{base}` | `{head}` | `{position:02d}-title.txt` | "
            f"`{position:02d}-body.md` | [Open form]({create_url}) | {pr_link} |"
        )
    lines.extend(
        [
            "",
            "## Command-line alternative",
            "",
            "Run each command from this package directory, in numeric order:",
            "",
            "```bash",
        ]
    )
    for index, layer in enumerate(layers):
        position = index + 1
        base = manifest["default_base"] if index == 0 else layers[index - 1]["remote_branch"]
        lines.append(
            f'gh pr create --repo "{manifest["repository"]}" --base "{base}" '
            f'--head "{layer["remote_branch"]}" --title "$(cat {position:02d}-title.txt)" '
            f'--body-file "{position:02d}-body.md"'
        )
    lines.extend(
        [
            "```",
            "",
            "## Make submitted PRs clickable in later descriptions",
            "",
            f"Record each created PR in `{links_path.name}` using its 1-based stack position:",
            "",
            "```json",
            '{"prs":[{"position":1,"number":123,"url":"https://github.example/owner/repo/pull/123"}]}',
            "```",
            "",
            "Then rerun manual packaging. Existing positions become clickable in every regenerated graph;",
            "future positions remain clearly marked Pending:",
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
            ).rstrip(),
            "",
        ]
    )
    return "\n".join(lines)


def github_preflight(repo: Path, manifest: dict[str, Any], layers: list[dict[str, Any]]) -> None:
    host, owner, name = split_repository(manifest["repository"])
    progress("preflight", f"checking authentication and push permission for {manifest['repository']}")
    if host:
        run(["gh", "auth", "status", "--hostname", host], repo, retry_transient=True)
    endpoint = f"repos/{owner}/{name}"
    permission = ["gh", "api"]
    if host:
        permission.extend(["--hostname", host])
    permission.extend([endpoint, "--jq", ".permissions.push"])
    if run(permission, repo, retry_transient=True) != "true":
        raise SubmitError("authenticated user lacks push permission for upstream stack branches")
    repository = manifest["repository"]
    for index, layer in enumerate(layers):
        expected_base = manifest["default_base"] if index == 0 else layers[index - 1]["remote_branch"]
        progress(
            "preflight",
            f"{index + 1}/{len(layers)}: {layer['remote_branch']} -> {expected_base}",
        )
        existing = pull_requests_for_head(repo, repository, layer["remote_branch"])
        if len(existing) > 1:
            raise SubmitError(f"multiple PRs exist for {layer['remote_branch']}")
        if existing:
            pr = existing[0]
            if pr["state"] != "OPEN" or pr["baseRefName"] != expected_base:
                raise SubmitError(f"existing PR conflicts for {layer['remote_branch']}")
            if bool(pr["isDraft"]) != bool(manifest.get("draft")):
                raise SubmitError(f"existing PR draft state conflicts for {layer['remote_branch']}")
            layer["_existing_pr"] = pr
        published = remote_sha(repo, manifest["publish_remote"], layer["remote_branch"])
        if published and published != layer["_tip"] and not existing:
            raise SubmitError(
                f"refusing to replace unowned upstream branch without an open PR: {layer['remote_branch']}"
            )


def pull_requests_for_head(
    repo: Path,
    repository: str,
    remote_branch: str,
) -> list[dict[str, Any]]:
    return json.loads(
        gh(
            repo,
            repository,
            "pr",
            "list",
            "--state",
            "all",
            "--head",
            remote_branch,
            "--limit",
            "100",
            "--json",
            "number,url,state,isDraft,baseRefName,headRefName",
        )
        or "[]"
    )


def reconcile_created_pull_request(
    repo: Path,
    manifest: dict[str, Any],
    layer: dict[str, Any],
    expected_base: str,
) -> dict[str, Any] | None:
    existing = pull_requests_for_head(repo, manifest["repository"], layer["remote_branch"])
    if not existing:
        return None
    if len(existing) > 1:
        raise SubmitError(f"multiple PRs exist for {layer['remote_branch']}")
    pr = existing[0]
    if (
        pr["state"] != "OPEN"
        or pr["baseRefName"] != expected_base
        or bool(pr["isDraft"]) != bool(manifest.get("draft"))
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
        layer["remote_branch"],
        "--title",
        title,
        "--body-file",
        body_file,
    ]
    if manifest.get("draft"):
        arguments.append("--draft")
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
            if not is_transient_failure(str(exc)):
                raise
            progress(
                "submit",
                f"ambiguous create for {layer['remote_branch']}; checking remote state",
            )
            existing = reconcile_created_pull_request(repo, manifest, layer, base)
            if existing:
                progress("submit", f"reconciled PR #{existing['number']} after transport failure")
                return existing
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
    layer: dict[str, Any],
    expected_base: str,
    pr: dict[str, Any],
) -> None:
    state = json.loads(
        gh(
            repo,
            manifest["repository"],
            "pr",
            "view",
            pr["url"],
            "--json",
            "state,isDraft,baseRefName,headRefName,headRefOid",
        )
    )
    expected = {
        "state": "OPEN",
        "isDraft": bool(manifest.get("draft")),
        "baseRefName": expected_base,
        "headRefName": layer["remote_branch"],
        "headRefOid": layer["_tip"],
    }
    mismatches = [key for key, value in expected.items() if state.get(key) != value]
    if mismatches:
        raise SubmitError(f"submitted PR state mismatch for {layer['remote_branch']}: {', '.join(mismatches)}")


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
    comments = json.loads(
        run([*command, f"{endpoint}?per_page=100"], repo, retry_transient=True) or "[]"
    )
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
                comments = json.loads(
                    run(
                        [*command, f"{endpoint}?per_page=100"],
                        repo,
                        retry_transient=True,
                    )
                    or "[]"
                )
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
    links = load_manual_links(manual_links, len(layers)) if manual else {}
    journal: dict[str, Any] = {
        "status": "preflight",
        "repository": manifest["repository"],
        "default_base": manifest["default_base"],
        "stack_label": manifest["stack_label"],
        "template_source": manifest.get("template_source"),
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
            ),
            encoding="utf-8",
        )
        journal["layers"].append(
            {
                "branch": layer["branch"],
                "remote_branch": layer["remote_branch"],
                "tip": layer["_tip"],
                "base": manifest["default_base"] if index == 0 else layers[index - 1]["remote_branch"],
                "title": title,
                "source_title": layer["title"],
                "rendered_title": str(title_path),
                "rendered_body": str(body_path),
            }
        )
    write_journal(output, journal)
    if manual:
        verify_published_layers(repo, manifest, layers)
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
    github_preflight(repo, manifest, layers)
    for index, layer in enumerate(layers):
        progress("publish", f"{index + 1}/{len(layers)}: {layer['remote_branch']}")
        publish(repo, manifest, layer)
    for index, layer in enumerate(layers):
        base = manifest["default_base"] if index == 0 else layers[index - 1]["remote_branch"]
        existing = layer.get("_existing_pr")
        title = stacked_title(layer, index, len(layers), manifest["stack_label"])
        action = "updating" if existing else "creating"
        progress(
            "submit",
            f"{index + 1}/{len(layers)}: {action} {layer['remote_branch']} -> {base}",
        )
        body = render_body(
            layers,
            links,
            index,
            manifest["default_base"],
            manifest["feature_summary"],
            manifest["stack_label"],
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md") as handle:
            handle.write(body)
            handle.flush()
            if existing:
                gh(
                    repo,
                    manifest["repository"],
                    "pr",
                    "edit",
                    existing["url"],
                    "--title",
                    title,
                    "--body-file",
                    handle.name,
                    "--base",
                    base,
                )
                pr = existing
            else:
                pr = create_pull_request(repo, manifest, layer, base, handle.name, title)
        links[index] = {"number": pr["number"], "url": pr["url"]}
        progress("submit", f"recorded PR #{pr['number']}: {pr['url']}")
        journal["layers"][index]["pr"] = links[index]
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
                "--body-file",
                handle.name,
            )
        navigation = render_navigation(
            layers,
            links,
            index,
            manifest["default_base"],
            manifest["stack_label"],
        )
        upsert_navigation_comment(repo, manifest, links[index], navigation)
        expected_base = manifest["default_base"] if index == 0 else layers[index - 1]["remote_branch"]
        verify_pull_request(repo, manifest, layer, expected_base, links[index])
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
    raise SystemExit(main())
