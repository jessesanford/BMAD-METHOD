#!/usr/bin/env python3
"""Validate every applied stack prefix and commit submission-ready evidence."""

from __future__ import annotations

import argparse, hashlib, json, math, os, re, shlex, shutil, subprocess, sys, tempfile
from pathlib import Path, PurePosixPath
from typing import Any

class EvidenceError(RuntimeError):
    pass
def git(repo: Path, *args: str, data: str | None = None, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, input=data, capture_output=True,
                            env={**os.environ, **(env or {})})
    if result.returncode:
        raise EvidenceError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()
def load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be a JSON object")
    return value
def exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise EvidenceError(f"{label} keys must be exactly: {', '.join(sorted(expected))}")
def safe_path(value: Any, label: str, *, glob: bool = False) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise EvidenceError(f"{label} must be a repository-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or path == PurePosixPath(".") or ".." in path.parts or ".git" in path.parts:
        raise EvidenceError(f"{label} must be a safe repository-relative path")
    if not glob and any(char in value for char in "*?["):
        raise EvidenceError(f"{label} must not be a glob")
    return value
def argv(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x for x in value):
        raise EvidenceError(f"{label} must be a non-empty argv string array")
    return value
def timeout(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)): raise EvidenceError(f"{label} must be a timeout in seconds")
    if not math.isfinite(value) or value <= 0 or value > 86400: raise EvidenceError(f"{label} must be between 0 and 86400 seconds")
    return float(value)
def config(path: Path) -> dict[str, Any]:
    value = load(path, "config")
    exact_keys(value, {"schema_version", "report_path", "test", "builds", "feature_flag"}, "config")
    if isinstance(value["schema_version"], bool) or value["schema_version"] != 1: raise EvidenceError("config schema_version must be 1")
    value["report_path"] = safe_path(value["report_path"], "report_path")
    test = value["test"]
    if not isinstance(test, dict): raise EvidenceError("test must be an object")
    exact_keys(test, {"argv", "parser", "timeout_seconds"}, "test")
    test["argv"] = argv(test["argv"], "test.argv")
    test["timeout_seconds"] = timeout(test["timeout_seconds"], "test.timeout_seconds")
    if test["parser"] not in {"unittest", "pytest"}: raise EvidenceError("test.parser must be unittest or pytest")
    if not isinstance(value["builds"], list) or not value["builds"]: raise EvidenceError("builds must be a non-empty list")
    for number, build in enumerate(value["builds"], 1):
        if not isinstance(build, dict): raise EvidenceError(f"build {number} must be an object")
        exact_keys(build, {"argv", "artifacts", "timeout_seconds"}, f"build {number}")
        build["argv"] = argv(build["argv"], f"build {number}.argv")
        build["timeout_seconds"] = timeout(build["timeout_seconds"], f"build {number}.timeout_seconds")
        if not isinstance(build["artifacts"], list) or not build["artifacts"]: raise EvidenceError(f"build {number}.artifacts must be non-empty")
        build["artifacts"] = [safe_path(x, f"build {number}.artifact", glob=True)
                              for x in build["artifacts"]]
    flag = value["feature_flag"]
    if not isinstance(flag, dict): raise EvidenceError("feature_flag must be an object")
    flag_keys = {"name", "safe_default", "disabled_behavior", "default_check_argv", "disabled_check_argv"}
    exact_keys(flag, flag_keys, "feature_flag")
    for field in ("name", "disabled_behavior"):
        if not isinstance(flag[field], str) or not flag[field].strip(): raise EvidenceError(f"feature_flag.{field} must be non-empty")
    if flag["safe_default"] != "disabled": raise EvidenceError("feature_flag.safe_default must be disabled")
    flag["default_check_argv"] = argv(flag["default_check_argv"], "feature_flag.default_check_argv")
    flag["disabled_check_argv"] = argv(flag["disabled_check_argv"], "feature_flag.disabled_check_argv")
    return value
def resolve(repo: Path, revision: str) -> str:
    return git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}")
def stack(repo: Path, report: dict[str, Any]) -> list[dict[str, str]]:
    if report.get("status") != "applied" or not isinstance(report.get("layers"), list) or not report["layers"]:
        raise EvidenceError("builder report must have status applied and non-empty layers")
    prior, seen, result = resolve(repo, str(report.get("base", ""))), set(), []
    for number, layer in enumerate(report["layers"], 1):
        if not isinstance(layer, dict): raise EvidenceError(f"report layer {number} must be an object")
        target, tip = layer.get("target"), layer.get("new_tip")
        if not isinstance(target, str) or not target.endswith("-pr-ready") or target in seen: raise EvidenceError(f"invalid or duplicate PR-ready target at layer {number}")
        if not isinstance(tip, str) or resolve(repo, f"refs/heads/{target}") != tip: raise EvidenceError(f"stale target: {target}")
        if subprocess.run(["git", "merge-base", "--is-ancestor", prior, tip],
                          cwd=repo, capture_output=True).returncode:
            raise EvidenceError(f"discontinuous ancestry at target: {target}")
        seen.add(target)
        result.append({"target": target, "tip": tip})
        prior = tip
    return result
def command(args: list[str], cwd: Path, seconds: float, label: str) -> str:
    try:
        result = subprocess.run(args, cwd=cwd, text=True, capture_output=True,
                                timeout=seconds, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceError(f"{label} failed: {exc}") from exc
    output = "\n".join(part for part in (result.stdout.rstrip(), result.stderr.rstrip()) if part)
    if result.returncode:
        raise EvidenceError(f"{label} failed ({result.returncode}): {output}")
    return output
def test_counts(output: str, parser: str) -> dict[str, int]:
    if parser == "unittest":
        ran = re.findall(r"(?m)^Ran (\d+) tests?", output)
        summaries = re.findall(r"(?m)^OK(?: \((.*?)\))?$", output)
        if len(ran) != 1 or len(summaries) != 1 or re.search("warning", output, re.I):
            raise EvidenceError("unittest output has no clean successful summary")
        detail = summaries[0]
        if detail and not re.fullmatch(r"skipped=\d+", detail):
            raise EvidenceError("unittest summary contains unsupported outcomes")
        skipped = int(detail.removeprefix("skipped=")) if detail else 0
        counts = {"passed": int(ran[0]) - skipped, "skipped": skipped, "warnings": 0}
    else:
        summaries = re.findall(r"(?m)^=+\s*(.*?)\s+in \d+(?:\.\d+)?(?:s| seconds?)\s*=+$", output)
        if len(summaries) != 1:
            raise EvidenceError("pytest output has no unique summary")
        tokens = re.findall(r"(\d+)\s+(passed|skipped|warnings?|failed|errors?)", summaries[0])
        residue = re.sub(r"\d+\s+(?:passed|skipped|warnings?|failed|errors?)", "", summaries[0])
        if residue.strip(" ,") or not tokens:
            raise EvidenceError("pytest summary contains unsupported outcomes")
        totals: dict[str, int] = {}
        for count, name in tokens:
            totals[name.rstrip("s")] = totals.get(name.rstrip("s"), 0) + int(count)
        if totals.get("failed", 0) or totals.get("error", 0):
            raise EvidenceError("pytest summary reports failures or errors")
        counts = {name: totals.get(name.rstrip("s"), 0) for name in ("passed", "skipped", "warnings")}
    if counts["passed"] <= 0 or any(value < 0 for value in counts.values()):
        raise EvidenceError("test summary must report positive passes and no failures")
    return counts
def digest(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    try:
        path.resolve(strict=True).relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise EvidenceError(f"artifact escapes worktree: {relative.as_posix()}") from exc
    if path.is_symlink() or not path.is_file() or any((root / parent).is_symlink() for parent in relative.parents):
        raise EvidenceError(f"artifact is not a regular non-symlink file: {relative.as_posix()}")
    return hashlib.sha256(path.read_bytes()).hexdigest()
def artifacts(root: Path, patterns: list[str], seen: set[str]) -> list[dict[str, str]]:
    found = []
    for pattern in patterns:
        matches = sorted(root.glob(pattern))
        if not matches:
            raise EvidenceError(f"artifact missing: {pattern}")
        for path in matches:
            relative = path.relative_to(root).as_posix()
            if relative in seen:
                raise EvidenceError(f"duplicate artifact: {relative}")
            seen.add(relative)
            found.append({"artifact": relative, "status": "passed",
                          "sha256": digest(root, path)})
    return found
def validate_prefix(repo: Path, layer: dict[str, str], cfg: dict[str, Any]) -> dict[str, Any]:
    common = Path(git(repo, "rev-parse", "--git-common-dir"))
    common = common if common.is_absolute() else (repo / common).resolve()
    root = common / "bmad-validation-worktrees"
    root.mkdir(exist_ok=True)
    worktree = Path(tempfile.mkdtemp(prefix="prefix-", dir=root))
    added = False
    try:
        git(repo, "worktree", "add", "--quiet", "--detach", str(worktree), layer["tip"])
        added = True
        flag = cfg["feature_flag"]
        command(flag["default_check_argv"], worktree, cfg["test"]["timeout_seconds"], "default safety check")
        command(flag["disabled_check_argv"], worktree, cfg["test"]["timeout_seconds"], "disabled safety check")
        output = command(cfg["test"]["argv"], worktree, cfg["test"]["timeout_seconds"], "tests")
        counts = test_counts(output, cfg["test"]["parser"])
        built, seen = [], set()
        for number, build in enumerate(cfg["builds"], 1):
            if any(list(worktree.glob(pattern)) for pattern in build["artifacts"]):
                raise EvidenceError(f"build {number} artifacts preexist")
            command(build["argv"], worktree, build["timeout_seconds"], f"build {number}")
            built.extend(artifacts(worktree, build["artifacts"], seen))
        for item in built:
            if digest(worktree, worktree / item["artifact"]) != item["sha256"]:
                raise EvidenceError(f"artifact mutated after build: {item['artifact']}")
        return {"target": layer["target"], "tip": layer["tip"], "tests": counts, "builds": built}
    finally:
        if added:
            result = subprocess.run(["git", "worktree", "remove", "--force", str(worktree)],
                                    cwd=repo, capture_output=True)
        shutil.rmtree(worktree, ignore_errors=True)
        if added and result.returncode:
            raise EvidenceError("failed to remove validation worktree")
def render(results: list[dict[str, Any]], cfg: dict[str, Any]) -> str:
    final, flag = results[-1], cfg["feature_flag"]
    rows = "\n".join(
        f"| `{item['target']}` | `{item['tip']}` | passed | {item['tests']['passed']} passed, {item['tests']['skipped']} skipped, "
        f"{item['tests']['warnings']} warnings |" for item in results
    )
    builds = "\n".join(f"| `{x['artifact']}` | passed | `{x['sha256']}` |" for x in final["builds"])
    counts = final["tests"]
    return (
        "# Stacked Validation Evidence\n\n"
        f"- Test command: `{shlex.join(cfg['test']['argv'])}`\n"
        f"- Final tests: {counts['passed']} passed, {counts['skipped']} skipped, {counts['warnings']} warnings\n"
        f"- Prefix coverage: {len(results)}/{len(results)}\n"
        f"- Feature flag: `{flag['name']}`; default: `{flag['safe_default']}`\n"
        f"- Disabled behavior: {flag['disabled_behavior']}\n\n"
        "## Prefix results\n\n| Target | Tip | Result | Tests |\n| --- | --- | --- | --- |\n"
        f"{rows}\n\n## Final build artifacts\n\n| Artifact | Result | SHA-256 |\n| --- | --- | --- |\n"
        f"{builds}\n"
    )
def commit_report(repo: Path, tip: str, report_path: str, content: str) -> str:
    values = git(repo, "show", "-s", "--format=%an%n%ae%n%aI%n%cn%n%ce%n%cI", tip).splitlines()
    if len(values) != 6:
        raise EvidenceError("cannot resolve final-tip identity")
    names = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE")
    env = dict(zip(names, values))
    common = Path(git(repo, "rev-parse", "--git-common-dir"))
    common = common if common.is_absolute() else (repo / common).resolve()
    with tempfile.TemporaryDirectory(prefix="evidence-index-", dir=common) as directory:
        index = Path(directory) / "index"
        index_env = {**env, "GIT_INDEX_FILE": str(index)}
        git(repo, "read-tree", tip, env=index_env)
        blob = git(repo, "hash-object", "-w", "--stdin", data=content)
        git(repo, "update-index", "--add", "--cacheinfo", "100644", blob, report_path, env=index_env)
        tree = git(repo, "write-tree", env=index_env)
    return git(repo, "commit-tree", tree, "-p", tip, data="docs: record stacked validation evidence\n", env=env)
def produce(repo: Path, report_path: Path, config_path: Path, branch: str, expected: str | None) -> dict[str, Any]:
    cfg, layers = config(config_path), stack(repo, load(report_path, "builder report"))
    if git(repo, "check-ref-format", "--branch", branch) != branch or branch in {x["target"] for x in layers}:
        raise EvidenceError("evidence branch is invalid or collides with a component target")
    results = []
    for layer in layers:
        try:
            results.append(validate_prefix(repo, layer, cfg))
        except EvidenceError as exc:
            raise EvidenceError(f"{layer['target']}: {exc}") from exc
    final = layers[-1]["tip"]
    evidence_ref = f"refs/heads/{branch}"
    if not subprocess.run(["git", "symbolic-ref", "-q", evidence_ref], cwd=repo,
                          capture_output=True).returncode:
        raise EvidenceError("evidence branch must not be symbolic")
    current_result = subprocess.run(["git", "show-ref", "--verify", "--hash", evidence_ref],
                                    cwd=repo, text=True, capture_output=True)
    current = current_result.stdout.strip() if not current_result.returncode else None
    if current and (expected is None or expected != current):
        raise EvidenceError("evidence branch exists and does not match --expected-old")
    if expected is not None and not re.fullmatch(rf"[0-9a-f]{{{len(final)}}}", expected):
        raise EvidenceError("--expected-old must be an exact full object ID")
    report = render(results, cfg)
    commit = commit_report(repo, final, cfg["report_path"], report)
    commands = ["start"]
    for layer in layers:
        commands += ["option no-deref", f"verify refs/heads/{layer['target']} {layer['tip']}"]
    commands += ["option no-deref",
                 f"update {evidence_ref} {commit} {current}" if current else f"create {evidence_ref} {commit}",
                 "prepare", "commit"]
    try:
        git(repo, "update-ref", "--stdin", data="\n".join(commands) + "\n")
    except EvidenceError as exc:
        raise EvidenceError("component or evidence ref changed during validation") from exc
    counts, flag = results[-1]["tests"], cfg["feature_flag"]
    return {"integration_evidence": {
        "branch": branch, "commit": commit, "report_path": cfg["report_path"],
        "test_command": shlex.join(cfg["test"]["argv"]), "tests": counts,
        "builds": results[-1]["builds"],
        "partial_merge_safety": {
            "validated_prefixes": len(results), "total_prefixes": len(results),
            "prefix_tips": [item["tip"] for item in results],
            "feature_flag": {key: flag[key] for key in ("name", "safe_default", "disabled_behavior")},
        },
    }}
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--expected-old")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    try:
        result = produce(args.repo.resolve(), args.report.resolve(), args.config.resolve(), args.branch, args.expected_old)
        rendered = json.dumps(result, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=args.output.parent, delete=False) as handle:
                handle.write(rendered)
                temporary = Path(handle.name)
            os.replace(temporary, args.output)
        else:
            sys.stdout.write(rendered)
    except EvidenceError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
