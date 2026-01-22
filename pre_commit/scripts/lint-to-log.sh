#!/usr/bin/env bash
# lint-to-log.sh - Run a linter and save JSON output to XDG-compliant log directory
#
# Usage: lint-to-log.sh <linter> [linter-args...]
#
# This script:
# 1. Computes the XDG-compliant log directory for the current repo
# 2. Runs the linter with JSON output redirected to the log file
# 3. Runs the linter normally for display/exit code
#
# Environment variables:
#   XDG_STATE_HOME - Override state directory (default: ~/.local/state)
#
# Examples:
#   lint-to-log.sh ruff check --fix file.py
#   lint-to-log.sh mypy --ignore-missing-imports file.py
#   lint-to-log.sh eslint --format=json src/

set -euo pipefail

LINTER="${1:-}"
if [[ -z "$LINTER" ]]; then
    echo "Usage: lint-to-log.sh <linter> [args...]" >&2
    exit 2
fi
shift

# Compute XDG-compliant log directory
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
REPO_HASH=$(echo -n "$REPO_ROOT" | shasum -a 256 | cut -c1-12)
LOG_DIR="$STATE_HOME/pre-commit/logs/$REPO_HASH"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/$LINTER.json"

# Run linter based on type
case "$LINTER" in
    ruff)
        # First pass: JSON output to log file
        ruff check --output-format=json "$@" > "$LOG_FILE" 2>&1 || true
        # Second pass: normal output with fixes
        exec ruff check --fix --exit-non-zero-on-fix "$@"
        ;;
    mypy)
        # First pass: JSON output to log file
        mypy --output=json "$@" > "$LOG_FILE" 2>&1 || true
        # Second pass: normal output
        exec mypy --ignore-missing-imports --no-error-summary "$@"
        ;;
    eslint)
        # First pass: JSON output to log file
        eslint --format=json "$@" > "$LOG_FILE" 2>&1 || true
        # Second pass: normal output with fixes
        exec eslint --fix "$@"
        ;;
    biome)
        # First pass: JSON output to log file
        biome check --reporter=json "$@" > "$LOG_FILE" 2>&1 || true
        # Second pass: normal output with fixes
        exec biome check --write "$@"
        ;;
    pylint)
        # First pass: JSON output to log file
        pylint --output-format=json "$@" > "$LOG_FILE" 2>&1 || true
        # Second pass: normal output
        exec pylint "$@"
        ;;
    flake8)
        # First pass: JSON output to log file
        flake8 --format=json "$@" > "$LOG_FILE" 2>&1 || true
        # Second pass: normal output
        exec flake8 "$@"
        ;;
    *)
        echo "Unknown linter: $LINTER" >&2
        echo "Supported: ruff, mypy, eslint, biome, pylint, flake8" >&2
        exit 2
        ;;
esac
