#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Collect immutable commit and file evidence for PR-ready boundary decisions."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def resolve(repo: Path, revision: str) -> str:
    return git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}").strip()


def parse_layer(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("layer must be SOURCE=PARENT")
    source, parent = value.split("=", 1)
    if not source or not parent:
        raise argparse.ArgumentTypeError("layer must contain non-empty SOURCE and PARENT")
    return source, parent


def collect(repo: Path, base: str, layers: list[tuple[str, str]]) -> dict:
    base_sha = resolve(repo, base)
    result_layers = []
    for source, parent in layers:
        source_sha = resolve(repo, source)
        parent_sha = resolve(repo, parent)
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", parent_sha, source_sha],
            cwd=repo,
            capture_output=True,
        )
        if ancestor.returncode:
            raise RuntimeError(f"{parent} is not an ancestor of {source}")
        commits = []
        for sha in git(repo, "rev-list", "--reverse", f"{parent_sha}..{source_sha}").splitlines():
            metadata = git(repo, "show", "-s", "--format=%s%n%b", sha).rstrip().splitlines()
            commits.append(
                {
                    "sha": sha,
                    "subject": metadata[0] if metadata else "",
                    "body": "\n".join(metadata[1:]).strip(),
                    "files": git(repo, "diff-tree", "--no-commit-id", "--name-status", "-r", sha).splitlines(),
                    "shortstat": git(repo, "show", "--format=", "--shortstat", sha).strip(),
                }
            )
        result_layers.append(
            {
                "source": source,
                "source_tip": source_sha,
                "source_parent": parent_sha,
                "commits": commits,
                "layer_files": git(repo, "diff", "--name-status", parent_sha, source_sha).splitlines(),
                "layer_shortstat": git(repo, "diff", "--shortstat", parent_sha, source_sha).strip(),
            }
        )
    return {"schema_version": 1, "base": base_sha, "layers": result_layers}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", required=True)
    parser.add_argument("--layer", action="append", type=parse_layer, required=True, metavar="SOURCE=PARENT")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    try:
        payload = collect(args.repo.resolve(), args.base, args.layer)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    rendered = json.dumps(payload, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
