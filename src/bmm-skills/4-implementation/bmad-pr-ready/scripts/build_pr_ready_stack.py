#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Build and validate a sanitized PR-ready stack from an explicit decision manifest."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class BuildError(RuntimeError):
    """A manifest or Git invariant failed."""


def run_git(repo: Path, *args: str, env: dict[str, str] | None = None, data: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        env={**os.environ, **(env or {})},
        input=data,
        capture_output=True,
    )
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        raise BuildError(detail or f"git {' '.join(args)} failed")
    return result.stdout


def git_text(repo: Path, *args: str, env: dict[str, str] | None = None, data: bytes | None = None) -> str:
    return run_git(repo, *args, env=env, data=data).decode().strip()


def resolve(repo: Path, revision: str) -> str:
    return git_text(repo, "rev-parse", "--verify", f"{revision}^{{commit}}")


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=repo,
            capture_output=True,
        ).returncode
        == 0
    )


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot read manifest: {exc}") from exc
    if payload.get("schema_version") != 1 or not payload.get("layers"):
        raise BuildError("manifest requires schema_version 1 and non-empty layers")
    return payload


def validate(repo: Path, manifest: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[str]]:
    base = resolve(repo, str(manifest.get("base", "")))
    excludes = manifest.get("exclude_paths", [])
    if not isinstance(excludes, list) or not all(isinstance(item, str) and item for item in excludes):
        raise BuildError("exclude_paths must be non-empty strings")
    targets: set[str] = set()
    for layer in manifest["layers"]:
        required = ("source", "source_tip", "source_parent", "target", "decision_summary", "groups")
        missing = [field for field in required if not layer.get(field)]
        if missing:
            raise BuildError(f"layer missing: {', '.join(missing)}")
        layer["_tip"] = resolve(repo, layer["source_tip"])
        layer["_parent"] = resolve(repo, layer["source_parent"])
        if resolve(repo, layer["source"]) != layer["_tip"]:
            raise BuildError(f"source ref drifted: {layer['source']}")
        if not is_ancestor(repo, layer["_parent"], layer["_tip"]):
            raise BuildError(f"source parent is not an ancestor: {layer['source']}")
        target = layer["target"]
        if not target.endswith("-pr-ready") or target == layer["source"] or target in targets:
            raise BuildError(f"invalid or duplicate target: {target}")
        targets.add(target)
        cursor = layer["_parent"]
        for group in layer["groups"]:
            if not all(group.get(field) for field in ("through", "message", "novelty_rationale")):
                raise BuildError(f"incomplete group in {layer['source']}")
            group["_through"] = resolve(repo, group["through"])
            if not is_ancestor(repo, cursor, group["_through"]) or not is_ancestor(
                repo, group["_through"], layer["_tip"]
            ):
                raise BuildError(f"group outside ordered source range: {layer['source']}")
            cursor = group["_through"]
        if cursor != layer["_tip"]:
            raise BuildError(f"final group must end at source_tip: {layer['source']}")
    return base, manifest["layers"], excludes


def apply_delta(repo: Path, env: dict[str, str], old: str, new: str, excludes: list[str]) -> None:
    pathspec = [".", *(f":(exclude){item}" for item in excludes)]
    patch = run_git(repo, "diff", "--binary", "--full-index", old, new, "--", *pathspec)
    if patch:
        run_git(repo, "apply", "--cached", "--3way", "--whitespace=nowarn", "-", env=env, data=patch)


def overlays(
    repo: Path,
    env: dict[str, str],
    values: list[dict[str, str]],
    manifest_dir: Path,
) -> list[str]:
    written = []
    for value in values:
        target = value.get("path", "")
        source = Path(value.get("source", ""))
        if not target or target.startswith("/") or ".." in Path(target).parts:
            raise BuildError("overlay target must be repository-relative")
        source = source if source.is_absolute() else manifest_dir / source
        if not source.is_file():
            raise BuildError(f"overlay source missing: {source}")
        blob = git_text(repo, "hash-object", "-w", str(source))
        run_git(repo, "update-index", "--add", "--cacheinfo", "100644", blob, target, env=env)
        written.append(target)
    return written


def tree_paths(repo: Path, revision: str) -> set[str]:
    output = git_text(repo, "ls-tree", "-r", "--name-only", revision)
    return set(output.splitlines()) if output else set()


def current_ref(repo: Path, ref: str) -> str | None:
    result = subprocess.run(["git", "rev-parse", "--verify", ref], cwd=repo, text=True, capture_output=True)
    return result.stdout.strip() if result.returncode == 0 else None


def remote_ref(repo: Path, remote: str, branch: str) -> str | None:
    output = git_text(repo, "ls-remote", "--heads", remote, f"refs/heads/{branch}")
    return output.split()[0] if output else None


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._/-]+", "-", value).strip("/")


def commit_environment(repo: Path, revision: str) -> dict[str, str]:
    values = git_text(repo, "show", "-s", "--format=%an%n%ae%n%aI%n%cn%n%ce%n%cI", revision).splitlines()
    if len(values) != 6:
        raise BuildError(f"cannot resolve commit identity for {revision}")
    return {
        "GIT_AUTHOR_NAME": values[0],
        "GIT_AUTHOR_EMAIL": values[1],
        "GIT_AUTHOR_DATE": values[2],
        "GIT_COMMITTER_NAME": values[3],
        "GIT_COMMITTER_EMAIL": values[4],
        "GIT_COMMITTER_DATE": values[5],
    }


def build(repo: Path, manifest_path: Path, apply: bool, push: bool) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    base, layers, excludes = validate(repo, manifest)
    if push and not apply:
        raise BuildError("--push requires --apply")
    if git_text(repo, "status", "--porcelain"):
        raise BuildError("worktree must be clean")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    parent_tip = base
    built = []
    with tempfile.TemporaryDirectory(prefix="bmad-pr-ready-") as temporary:
        for layer_number, layer in enumerate(layers):
            source_cursor = layer["_parent"]
            tip = parent_tip
            groups = []
            for group_number, group in enumerate(layer["groups"]):
                env = {"GIT_INDEX_FILE": str(Path(temporary) / f"index-{layer_number}-{group_number}")}
                run_git(repo, "read-tree", tip, env=env)
                apply_delta(repo, env, source_cursor, group["_through"], excludes)
                overlay_paths = overlays(repo, env, group.get("overlays", []), manifest_path.parent)
                tree = git_text(repo, "write-tree", env=env)
                skipped = tree == git_text(repo, "rev-parse", f"{tip}^{{tree}}")
                if not skipped:
                    tip = git_text(
                        repo,
                        "commit-tree",
                        tree,
                        "-p",
                        tip,
                        env=commit_environment(repo, group["_through"]),
                        data=(group["message"].rstrip() + "\n").encode(),
                    )
                groups.append(
                    {
                        "through": group["_through"],
                        "tip": tip,
                        "skipped_empty": skipped,
                        "overlays": overlay_paths,
                        "novelty_rationale": group["novelty_rationale"],
                    }
                )
                source_cursor = group["_through"]
            base_paths = tree_paths(repo, base)
            banned = sorted(
                path
                for path in tree_paths(repo, tip) - base_paths
                if any(fnmatch.fnmatch(path, pattern) for pattern in excludes)
            )
            if banned:
                raise BuildError(f"{layer['target']} contains excluded paths: {banned}")
            if not is_ancestor(repo, parent_tip, tip):
                raise BuildError(f"{layer['target']} is not based on its prior PR-ready layer")
            built.append(
                {
                    "source": layer["source"],
                    "source_tip": layer["_tip"],
                    "target": layer["target"],
                    "old_local": current_ref(repo, f"refs/heads/{layer['target']}"),
                    "new_tip": tip,
                    "tree": git_text(repo, "rev-parse", f"{tip}^{{tree}}"),
                    "decision_summary": layer["decision_summary"],
                    "groups": groups,
                }
            )
            parent_tip = tip
    if apply:
        for layer in built:
            if layer["old_local"]:
                backup = f"refs/bmad-backups/pr-ready/{run_id}/{safe(layer['target'])}"
                run_git(repo, "update-ref", backup, layer["old_local"])
                layer["local_backup"] = backup
            run_git(repo, "update-ref", f"refs/heads/{layer['target']}", layer["new_tip"])
    remote = str(manifest.get("remote", "origin"))
    if push:
        for layer in built:
            old_remote = remote_ref(repo, remote, layer["target"])
            layer["old_remote"] = old_remote
            if old_remote:
                backup = f"backup/pr-ready/{run_id}/{safe(layer['target'])}"
                run_git(repo, "push", remote, f"{old_remote}:refs/heads/{backup}")
                layer["remote_backup"] = backup
            lease = f"--force-with-lease=refs/heads/{layer['target']}:{old_remote or ''}"
            run_git(repo, "push", lease, remote, f"{layer['new_tip']}:refs/heads/{layer['target']}")
            layer["pushed"] = True
    return {
        "status": "applied" if apply else "dry-run",
        "run_id": run_id,
        "base": base,
        "remote": remote,
        "exclude_paths": excludes,
        "layers": built,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    try:
        result = build(args.repo.resolve(), args.manifest.resolve(), args.apply, args.push)
    except BuildError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
