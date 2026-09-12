#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

CONFIG="$HOME/.claude"
BIN_DIR="$CONFIG/response-tools/bin"
COMMAND_DIR="$CONFIG/commands"

HELPER="$BIN_DIR/response_history.py"
LS_COMMAND="$COMMAND_DIR/ls-responses.md"
COPY_COMMAND="$COMMAND_DIR/copy-responses.md"
COPY_SINGLE_COMMAND="$COMMAND_DIR/copy-response.md"

command -v python3 >/dev/null 2>&1 || {
    echo "ERROR: python3 is required"
    exit 1
}

command -v pbcopy >/dev/null 2>&1 || {
    echo "ERROR: pbcopy is required (macOS)"
    exit 1
}

for path in "$HELPER" "$LS_COMMAND" "$COPY_COMMAND" "$COPY_SINGLE_COMMAND"; do
    if [ -e "$path" ]; then
        echo "ERROR: refusing to overwrite existing file:"
        echo "  $path"
        echo
        echo "Remove or archive the existing installation first."
        exit 1
    fi
done

mkdir -p "$BIN_DIR" "$COMMAND_DIR"

install -m 755 \
    "$ROOT/bin/response_history.py" \
    "$HELPER"

install_command() {
    template="$1"
    destination="$2"
    label="$3"

    python3 - \
        "$template" \
        "$destination" \
        "$HELPER" \
        "$label" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
helper = sys.argv[3]
label = sys.argv[4]

text = src.read_text(encoding="utf-8")

if text.count("__HELPER_PATH__") != 2:
    raise SystemExit(
        "ERROR: unexpected __HELPER_PATH__ count "
        f"in {label} template"
    )

dst.write_text(
    text.replace("__HELPER_PATH__", helper),
    encoding="utf-8",
)
PY
}

install_command \
    "$ROOT/commands/ls-responses.md.template" \
    "$LS_COMMAND" \
    "ls-responses"

install_command \
    "$ROOT/commands/copy-responses.md.template" \
    "$COPY_COMMAND" \
    "copy-responses"

install_command \
    "$ROOT/commands/copy-response.md.template" \
    "$COPY_SINGLE_COMMAND" \
    "copy-response"

python3 -m py_compile "$HELPER"

python3 "$HELPER" self-test

echo
echo "Installed:"
echo "  $HELPER"
echo "  $LS_COMMAND"
echo "  $COPY_COMMAND"
echo "  $COPY_SINGLE_COMMAND"
echo
echo "Start a new Claude Code session, then run:"
echo "  /copy-response"
echo "  /ls-responses 10"
