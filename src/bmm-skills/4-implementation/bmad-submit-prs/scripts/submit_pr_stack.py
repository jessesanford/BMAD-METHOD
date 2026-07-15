#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Validate, render, and idempotently submit a reviewer-friendly GitHub PR stack."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

MARKER = "<!-- bmad-stack-navigation:v1 -->"


class SubmitError(RuntimeError):
    """Submission cannot continue safely."""


def run(command: list[str], cwd: Path, input_text: str | None = None) -> str:
    result = subprocess.run(command, cwd=cwd, text=True, input=input_text, capture_output=True)
    if result.returncode:
        raise SubmitError(result.stderr.strip() or f"{' '.join(command)} failed")
    return result.stdout.strip()


def git(repo: Path, *args: str) -> str:
    return run(["git", *args], repo)


def gh(repo: Path, repository: str, *args: str) -> str:
    return run(["gh", *args, "--repo", repository], repo)


def resolve(repo: Path, revision: str) -> str:
    return git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}")


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=repo)
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


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubmitError(f"cannot read manifest: {exc}") from exc
    if manifest.get("schema_version") != 1 or not manifest.get("layers"):
        raise SubmitError("manifest requires schema_version 1 and non-empty layers")
    return manifest


def validate(repo: Path, path: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if git(repo, "status", "--porcelain"):
        raise SubmitError("worktree must be clean")
    for field in ("repository", "publish_remote", "default_base"):
        if not manifest.get(field):
            raise SubmitError(f"manifest missing {field}")
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


def node_label(index: int, layer: dict[str, Any], links: dict[int, dict[str, Any]]) -> str:
    pr = links.get(index)
    prefix = f"#{pr['number']} " if pr else "Pending: "
    return (prefix + layer["title"]).replace('"', "'")


def render_navigation(
    layers: list[dict[str, Any]],
    links: dict[int, dict[str, Any]],
    current: int | None,
    default_base: str,
) -> str:
    lines = [MARKER, "## Stack navigation", ""]
    if current is not None:
        lines.extend([f"**This PR:** {current + 1} of {len(layers)}", ""])
    lines.extend(["```mermaid", "flowchart TD"])
    for index, layer in enumerate(layers):
        lines.append(f'  L{index + 1}["{node_label(index, layer, links)}"]')
        if index:
            lines.append(f"  L{index} --> L{index + 1}")
        if index in links:
            lines.append(f'  click L{index + 1} "{links[index]["url"]}" "Open PR"')
    lines.extend(["```", "", "| # | Layer | Base | PR |", "|---:|---|---|---|"])
    for index, layer in enumerate(layers):
        base = f"`{default_base}`" if index == 0 else f"`{layers[index - 1]['remote_branch']}`"
        pr = links.get(index)
        link = f"[#{pr['number']}]({pr['url']})" if pr else "Pending"
        here = " **(this PR)**" if current == index else ""
        lines.append(f"| {index + 1} | {layer['summary']}{here} | {base} | {link} |")
    return "\n".join(lines) + "\n"


def render_body(
    layers: list[dict[str, Any]],
    links: dict[int, dict[str, Any]],
    index: int,
    default_base: str,
) -> str:
    content = layers[index]["_body_file"].read_text(encoding="utf-8").rstrip()
    planning = links.get(0)
    pointer = ""
    if index and planning:
        pointer = f"\n\n**Feature overview:** [Planning PR #{planning['number']}]({planning['url']})"
    return content + pointer + "\n\n" + render_navigation(layers, links, index, default_base)


def write_journal(path: Path | None, payload: dict[str, Any]) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def remote_sha(repo: Path, remote: str, branch: str) -> str | None:
    output = git(repo, "ls-remote", "--heads", remote, f"refs/heads/{branch}")
    return output.split()[0] if output else None


def github_preflight(repo: Path, manifest: dict[str, Any], layers: list[dict[str, Any]]) -> None:
    host, owner, name = split_repository(manifest["repository"])
    if host:
        run(["gh", "auth", "status", "--hostname", host], repo)
    endpoint = f"repos/{owner}/{name}"
    permission = ["gh", "api"]
    if host:
        permission.extend(["--hostname", host])
    permission.extend([endpoint, "--jq", ".permissions.push"])
    if run(permission, repo) != "true":
        raise SubmitError("authenticated user lacks push permission for upstream stack branches")
    repository = manifest["repository"]
    for index, layer in enumerate(layers):
        existing = json.loads(
            gh(
                repo,
                repository,
                "pr",
                "list",
                "--state",
                "all",
                "--head",
                layer["remote_branch"],
                "--limit",
                "100",
                "--json",
                "number,url,state,isDraft,baseRefName,headRefName",
            )
            or "[]"
        )
        expected_base = manifest["default_base"] if index == 0 else layers[index - 1]["remote_branch"]
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


def publish(repo: Path, manifest: dict[str, Any], layer: dict[str, Any]) -> None:
    remote = manifest["publish_remote"]
    old = remote_sha(repo, remote, layer["remote_branch"])
    lease = f"--force-with-lease=refs/heads/{layer['remote_branch']}:{old or ''}"
    git(repo, "push", lease, remote, f"{layer['_tip']}:refs/heads/{layer['remote_branch']}")
    if remote_sha(repo, remote, layer["remote_branch"]) != layer["_tip"]:
        raise SubmitError(f"published branch SHA mismatch: {layer['remote_branch']}")


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
    comments = json.loads(run([*command, f"{endpoint}?per_page=100"], repo) or "[]")
    match = next((item for item in comments if MARKER in (item.get("body") or "")), None)
    if match:
        comment_endpoint = f"repos/{owner}/{name}/issues/comments/{match['id']}"
        run([*command, "--method", "PATCH", comment_endpoint, "-f", f"body={body}"], repo)
    else:
        gh(repo, manifest["repository"], "pr", "comment", pr["url"], "--body", body)


def submit(
    repo: Path,
    manifest_path: Path,
    apply: bool,
    output: Path | None,
    rendered_dir: Path | None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    layers = validate(repo, manifest_path, manifest)
    links: dict[int, dict[str, Any]] = {}
    journal: dict[str, Any] = {
        "status": "preflight",
        "repository": manifest["repository"],
        "default_base": manifest["default_base"],
        "template_source": manifest.get("template_source"),
        "layers": [],
    }
    destination = rendered_dir or manifest_path.parent / "rendered"
    destination.mkdir(parents=True, exist_ok=True)
    for index, layer in enumerate(layers):
        body_path = destination / f"{index + 1:02d}-{layer['remote_branch'].replace('/', '-')}.md"
        body_path.write_text(render_body(layers, links, index, manifest["default_base"]), encoding="utf-8")
        journal["layers"].append(
            {
                "branch": layer["branch"],
                "remote_branch": layer["remote_branch"],
                "tip": layer["_tip"],
                "base": manifest["default_base"] if index == 0 else layers[index - 1]["remote_branch"],
                "title": layer["title"],
                "rendered_body": str(body_path),
            }
        )
    write_journal(output, journal)
    if not apply:
        journal["status"] = "dry-run"
        write_journal(output, journal)
        return journal
    github_preflight(repo, manifest, layers)
    for layer in layers:
        publish(repo, manifest, layer)
    for index, layer in enumerate(layers):
        base = manifest["default_base"] if index == 0 else layers[index - 1]["remote_branch"]
        existing = layer.get("_existing_pr")
        body = render_body(layers, links, index, manifest["default_base"])
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
                    layer["title"],
                    "--body-file",
                    handle.name,
                    "--base",
                    base,
                )
                pr = existing
            else:
                arguments = [
                    "pr",
                    "create",
                    "--base",
                    base,
                    "--head",
                    layer["remote_branch"],
                    "--title",
                    layer["title"],
                    "--body-file",
                    handle.name,
                ]
                if manifest.get("draft"):
                    arguments.append("--draft")
                url = gh(repo, manifest["repository"], *arguments).splitlines()[-1]
                number = int(url.rstrip("/").rsplit("/", 1)[-1])
                pr = {"number": number, "url": url, "state": "OPEN"}
        links[index] = {"number": pr["number"], "url": pr["url"]}
        journal["layers"][index]["pr"] = links[index]
        journal["status"] = "submitting"
        write_journal(output, journal)
    for index, layer in enumerate(layers):
        body = render_body(layers, links, index, manifest["default_base"])
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
        navigation = render_navigation(layers, links, index, manifest["default_base"])
        upsert_navigation_comment(repo, manifest, links[index], navigation)
        expected_base = manifest["default_base"] if index == 0 else layers[index - 1]["remote_branch"]
        verify_pull_request(repo, manifest, layer, expected_base, links[index])
    journal["status"] = "complete"
    write_journal(output, journal)
    return journal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--rendered-dir", type=Path)
    args = parser.parse_args()
    try:
        result = submit(
            args.repo.resolve(),
            args.manifest.resolve(),
            args.apply,
            args.output.resolve() if args.output else None,
            args.rendered_dir.resolve() if args.rendered_dir else None,
        )
    except SubmitError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not args.output:
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
