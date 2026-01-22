#!/bin/bash
# lint-capture.sh - Wrapper to capture linter JSON output for ai-fix
#
# Usage: lint-capture.sh <linter> <json-arg> [other-args...] <files...>
#   lint-capture.sh ruff --output-format=json file1.py file2.py
#   lint-capture.sh eslint --format=json file1.js
#   lint-capture.sh mypy --output=json file.py
#
# Captures JSON output to /tmp/ai-fix-lint.ndjson (newline-delimited JSON)
# Each line: {"linter": "ruff", "data": [...]}

CAPTURE_FILE="${AI_FIX_CAPTURE_FILE:-/tmp/ai-fix-lint.ndjson}"
LINTER="$1"
shift

# Run linter and capture output
json_output=$("$LINTER" "$@" 2>&1)
exit_code=$?

# Append JSON to capture file with linter name wrapper
if [ -n "$json_output" ] && [ "$exit_code" -ne 0 ]; then
    # Wrap the output with linter metadata
    echo "{\"linter\": \"$LINTER\", \"data\": $json_output}" >> "$CAPTURE_FILE"
fi

# Output the JSON (pre-commit will display it)
echo "$json_output"
exit $exit_code
