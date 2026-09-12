#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

CONFIG="$HOME/.claude"
BIN_DIR="$CONFIG/response-tools/bin"
SKILL_DIR="$CONFIG/skills"

HELPER="$BIN_DIR/response_history.py"
LS_SKILL="$SKILL_DIR/ls-responses/SKILL.md"
COPY_SKILL="$SKILL_DIR/copy-responses/SKILL.md"

command -v python3 >/dev/null 2>&1 || {
    echo "ERROR: python3 is required"
    exit 1
}

command -v pbcopy >/dev/null 2>&1 || {
    echo "ERROR: pbcopy is required (macOS)"
    exit 1
}

for path in "$HELPER" "$LS_SKILL" "$COPY_SKILL"; do
    if [ -e "$path" ]; then
        echo "ERROR: refusing to overwrite existing file:"
        echo "  $path"
        echo
        echo "Remove or archive the existing installation first."
        exit 1
    fi
done

mkdir -p \
    "$BIN_DIR" \
    "$SKILL_DIR/ls-responses" \
    "$SKILL_DIR/copy-responses"

install -m 755 \
    "$ROOT/bin/response_history.py" \
    "$HELPER"

python3 - \
    "$ROOT/skills/ls-responses/SKILL.md.template" \
    "$LS_SKILL" \
    "$HELPER" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
helper = sys.argv[3]

text = src.read_text(encoding="utf-8")

if text.count("__HELPER_PATH__") != 2:
    raise SystemExit(
        "ERROR: unexpected __HELPER_PATH__ count "
        "in ls-responses skill template"
    )

dst.write_text(
    text.replace("__HELPER_PATH__", helper),
    encoding="utf-8",
)
PY

python3 - \
    "$ROOT/skills/copy-responses/SKILL.md.template" \
    "$COPY_SKILL" \
    "$HELPER" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
helper = sys.argv[3]

text = src.read_text(encoding="utf-8")

if text.count("__HELPER_PATH__") != 2:
    raise SystemExit(
        "ERROR: unexpected __HELPER_PATH__ count "
        "in copy-responses skill template"
    )

dst.write_text(
    text.replace("__HELPER_PATH__", helper),
    encoding="utf-8",
)
PY

python3 -m py_compile "$HELPER"
python3 "$HELPER" self-test

echo
echo "Installed:"
echo "  $HELPER"
echo "  $LS_SKILL"
echo "  $COPY_SKILL"
echo
echo "Start a new Claude Code session."
