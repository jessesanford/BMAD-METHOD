#!/bin/sh
# Preflight check for gh-projects BMAD module.
# Validates that required tools and Python modules are available
# before skill activation. POSIX sh only — no bashisms.
#
# Usage: sh preflight.sh
# Exit 0: all required checks pass (warnings possible)
# Exit 1: one or more required checks failed

failures=0
warnings=0

version_gte() {
  major1="${1%%.*}"; minor1="${1#*.}"
  major2="${2%%.*}"; minor2="${2#*.}"
  [ "$major1" -gt "$major2" ] && return 0
  [ "$major1" -eq "$major2" ] && [ "$minor1" -ge "$minor2" ] && return 0
  return 1
}

check_tool() {
  tool="$1"; min_ver="$2"; required="$3"; why="$4"

  if ! command -v "$tool" >/dev/null 2>&1; then
    if [ "$required" = "true" ]; then
      echo "CHECK: tool=$tool status=FAIL reason=\"not found\" required=true why=\"$why\""
      failures=$((failures + 1))
    else
      echo "CHECK: tool=$tool status=WARN reason=\"not found\" required=false why=\"$why\""
      warnings=$((warnings + 1))
    fi
    return 1
  fi

  if [ -n "$min_ver" ]; then
    raw_ver=$("$tool" --version 2>&1 | head -1)
    case "$tool" in
      python3) ver=$(echo "$raw_ver" | sed -n 's/.*Python \([0-9]*\.[0-9]*\).*/\1/p') ;;
      gh)      ver=$(echo "$raw_ver" | sed -n 's/.*gh version \([0-9]*\.[0-9]*\).*/\1/p') ;;
      *)       ver=$(echo "$raw_ver" | sed -n 's/.*\([0-9]*\.[0-9]*\).*/\1/p') ;;
    esac

    if [ -z "$ver" ]; then
      echo "CHECK: tool=$tool status=WARN reason=\"could not parse version from: $raw_ver\" required=$required"
      warnings=$((warnings + 1))
      return 0
    fi

    if ! version_gte "$ver" "$min_ver"; then
      if [ "$required" = "true" ]; then
        echo "CHECK: tool=$tool status=FAIL reason=\"version $ver < $min_ver\" required=true why=\"$why\""
        failures=$((failures + 1))
      else
        echo "CHECK: tool=$tool status=WARN reason=\"version $ver < $min_ver\" required=false why=\"$why\""
        warnings=$((warnings + 1))
      fi
      return 1
    fi

    echo "CHECK: tool=$tool status=OK version=$ver required=$required"
  else
    echo "CHECK: tool=$tool status=OK required=$required"
  fi
  return 0
}

check_python_module() {
  mod="$1"
  err=$(python3 -c "import $mod" 2>&1)
  rc=$?
  err=$(echo "$err" | tail -1)
  if [ $rc -ne 0 ]; then
    echo "CHECK: tool=python3:$mod status=FAIL reason=\"$err\" required=true"
    failures=$((failures + 1))
    return 1
  fi
  echo "CHECK: tool=python3:$mod status=OK required=true"
  return 0
}

check_gh_auth() {
  if [ -n "$GH_TOKEN" ] || [ -n "$GITHUB_TOKEN" ]; then
    echo "CHECK: tool=gh:auth status=OK method=token required=true"
    return 0
  fi
  $1 >/dev/null 2>&1
  if [ $? -ne 0 ]; then
    echo "CHECK: tool=gh:auth status=FAIL reason=\"no GH_TOKEN/GITHUB_TOKEN and gh auth status failed\" required=true why=\"$2\""
    failures=$((failures + 1))
    return 1
  fi
  echo "CHECK: tool=gh:auth status=OK method=gh-auth required=true"
  return 0
}

# --- Checks ---

echo "PREFLIGHT: module=gh-projects"

# Python 3.11+ with required stdlib modules
if check_tool "python3" "3.11" "true" "resolve_customization.py uses tomllib (3.11+) and json"; then
  check_python_module "json"
  check_python_module "tomllib"
  check_python_module "pathlib"
fi

# GitHub CLI 2.0+ with authentication
if check_tool "gh" "2.0" "true" "GitHub Projects sync requires authenticated gh CLI"; then
  check_gh_auth "gh auth status" "gh CLI must be authenticated"
fi

# jq (optional)
check_tool "jq" "" "false" "JSON processing fallback when Python unavailable"

# --- Summary ---

if [ "$failures" -gt 0 ]; then
  echo "PREFLIGHT: status=FAIL failures=$failures warnings=$warnings"
  exit 1
else
  echo "PREFLIGHT: status=OK failures=0 warnings=$warnings"
  exit 0
fi
