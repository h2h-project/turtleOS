#!/usr/bin/env bash
set -uo pipefail

# ============================================================
# turtleOS De-Linter
# ------------------------------------------------------------
# Inspects the turtleOS Python code for likely bugs. Two checks:
#
#   1. ruff       — static analysis of device/, tests/ and scripts/
#                   (rules in ruff.toml at the repo root)
#   2. mpy-cross  — compiles every file in device/ with the
#                   MicroPython compiler, so syntax the board would
#                   reject is caught on the laptop
#
# Nothing is uploaded and no board needs to be connected.
#
# Usage:
#   ./scripts/de_lint_please.sh                 # check everything
#   ./scripts/de_lint_please.sh --stats         # counts per rule, no listing
#   ./scripts/de_lint_please.sh --fix           # apply ruff's safe auto-fixes
#   ./scripts/de_lint_please.sh device/src/nav  # check only these paths (ruff only)
#   ./scripts/de_lint_please.sh --no-mpy        # skip the MicroPython compile
#
# Exit code: 0 = clean, 1 = findings, 2 = the tools could not be set up.
#
# The tools go in the same venv the Synker uses for mpremote
# (~/Documents/HopeTurtle/.tools-venv), installed on first run.
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$HOME/Documents/HopeTurtle/.tools-venv"

# shellcheck source=scripts/tools_venv.sh
source "$SCRIPT_DIR/tools_venv.sh"

FIX=0
STATS=0
RUN_MPY=1
PATHS=()

for arg in "$@"; do
    case "$arg" in
        --fix)     FIX=1 ;;
        --stats)   STATS=1 ;;
        --no-mpy)  RUN_MPY=0 ;;
        -h|--help) sed -n '4,28p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*)        echo "Unknown option: $arg (try --help)"; exit 2 ;;
        *)         PATHS+=("$arg") ;;
    esac
done

# Find a tool on PATH or in the venv; otherwise install it into the venv.
# Prints the tool's path; returns 1 if it can't be had.
find_or_install_tool() {
    local tool="$1"
    if command -v "$tool" >/dev/null 2>&1; then
        command -v "$tool"
    elif [[ -x "$VENV_DIR/bin/$tool" ]]; then
        echo "$VENV_DIR/bin/$tool"
    else
        {
            echo "$tool not found — installing it into $VENV_DIR"
            tools_ensure_venv && tools_venv_install "$tool"
        } >&2 || return 1
        echo "$VENV_DIR/bin/$tool"
    fi
}

cd "$REPO_DIR" || exit 2
status=0

# ------------------------------------------------------------
# 1. ruff
# ------------------------------------------------------------
RUFF="$(find_or_install_tool ruff)" || { echo "ERROR: could not install ruff."; exit 2; }

if [[ ${#PATHS[@]} -eq 0 ]]; then
    TARGETS=(device tests scripts)
    FULL_RUN=1
else
    TARGETS=("${PATHS[@]}")
    FULL_RUN=0
fi

echo "== ruff ($("$RUFF" --version | awk '{print $2}')): ${TARGETS[*]}"
RUFF_ARGS=(check --config "$REPO_DIR/ruff.toml")
[[ $FIX -eq 1 ]]   && RUFF_ARGS+=(--fix)
[[ $STATS -eq 1 ]] && RUFF_ARGS+=(--statistics)
[[ $STATS -eq 0 ]] && RUFF_ARGS+=(--output-format concise)
"$RUFF" "${RUFF_ARGS[@]}" "${TARGETS[@]}" || status=1

# ------------------------------------------------------------
# 2. MicroPython compile check (full runs only)
# ------------------------------------------------------------
if [[ $RUN_MPY -eq 1 && $FULL_RUN -eq 1 ]]; then
    echo
    if MPY_CROSS="$(find_or_install_tool mpy-cross)"; then
        echo "== mpy-cross ($("$MPY_CROSS" --version 2>&1 | awk '{print $2}')): device/"
        out="$(mktemp)"
        err="$(mktemp)"
        trap 'rm -f "$out" "$err"' EXIT
        total=0
        failed=0
        while IFS= read -r -d '' f; do
            total=$((total + 1))
            if ! "$MPY_CROSS" -o "$out" "$f" 2>"$err"; then
                failed=$((failed + 1))
                echo "$f"
                sed 's/^/    /' "$err"
            fi
        done < <(find device -name '*.py' -print0 | sort -z)
        if [[ $failed -eq 0 ]]; then
            echo "All $total files compile for MicroPython."
        else
            echo "$failed of $total files do not compile for MicroPython."
            status=1
        fi
    else
        echo "== mpy-cross: could not be installed — MicroPython compile check skipped."
    fi
fi

echo
if [[ $status -eq 0 ]]; then
    echo "✓  No lint findings."
else
    echo "✗  Findings above. Re-run with --fix to apply ruff's safe auto-fixes."
fi
exit $status
