#!/usr/bin/env python3

import json
import re
import subprocess
import sys
from pathlib import Path

HIDDEN_COMMANDS = {"ls-responses", "copy-responses", "copy"}
GATE_VERSION = 1
GATE_KEEP = 100


def content_text(record):
    message = record.get("message") or {}
    content = message.get("content")

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []

        for item in content:
            if (
                isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ):
                parts.append(item["text"])

        return "\n".join(parts)

    return ""


def command_name(text):
    message_match = re.search(
        r"<command-message>([^<]+)</command-message>",
        text,
    )

    name_match = re.search(
        r"<command-name>/([^<]+)</command-name>",
        text,
    )

    if not message_match or not name_match:
        return None

    message_name = message_match.group(1).strip()
    slash_name = name_match.group(1).strip()

    return slash_name if message_name == slash_name else None


def command_args(text):
    match = re.search(
        r"<command-args>(.*?)</command-args>",
        text,
        flags=re.DOTALL,
    )

    return match.group(1) if match else ""


def assistant_response(record):
    if record.get("type") != "assistant":
        return None

    message = record.get("message") or {}
    content = message.get("content")

    if isinstance(content, str):
        return content if content else None

    if isinstance(content, list):
        parts = []

        for item in content:
            if (
                isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
                and item["text"] != ""
            ):
                parts.append(item["text"])

        return "\n".join(parts) if parts else None

    return None


def load_records(path):
    records = []

    with path.open("r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()

            if not line:
                continue

            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"invalid JSONL at line {lineno}: {exc}"
                ) from exc

    return records


def find_transcript(session_id):
    root = Path.home() / ".claude" / "projects"

    if not root.is_dir():
        return None

    matches = list(root.glob(f"*/{session_id}.jsonl"))

    if not matches:
        matches = list(root.rglob(f"{session_id}.jsonl"))

    unique = sorted({path.resolve() for path in matches})

    if not unique:
        return None

    if len(unique) != 1:
        raise RuntimeError(
            f"expected one transcript for session {session_id}, "
            f"found {len(unique)}"
        )

    return unique[0]


def latest_invocation_args(records, wanted):
    for record in reversed(records):
        if (
            record.get("type") != "user"
            or bool(record.get("isMeta", False))
        ):
            continue

        text = content_text(record)

        if command_name(text) == wanted:
            return command_args(text)

    raise RuntimeError(
        f"could not locate current /{wanted} invocation in transcript"
    )


def filtered_responses(records):
    responses = []
    suppress = False

    for record in records:
        record_type = record.get("type")

        if (
            record_type == "user"
            and not bool(record.get("isMeta", False))
        ):
            suppress = (
                command_name(content_text(record))
                in HIDDEN_COMMANDS
            )
            continue

        if record_type == "assistant" and not suppress:
            response = assistant_response(record)

            if response is not None:
                responses.append(response)

    return responses


def parse_count(raw):
    raw = raw.strip()

    if raw == "":
        return 10

    if not re.fullmatch(r"[1-9][0-9]*", raw):
        raise ValueError(
            "invalid count — use /ls-responses [positive-integer]"
        )

    return int(raw)


def parse_selection(raw, count):
    raw = raw.strip()

    if count < 1:
        raise ValueError(
            "nothing copied — session has 0 responses"
        )

    if raw == "":
        return [count]

    relative_match = re.fullmatch(r"-([1-9]|10)", raw)

    if relative_match:
        requested = int(relative_match.group(1))
        start = max(1, count - requested + 1)
        return list(range(start, count + 1))

    if raw.startswith("-"):
        raise ValueError(
            "invalid relative selection — use -1 through -10"
        )

    token_pattern = (
        r"#?[1-9][0-9]*"
        r"(?:\s*-\s*#?[1-9][0-9]*)?"
    )

    if not re.fullmatch(
        rf"{token_pattern}(?:\s*,\s*{token_pattern})*",
        raw,
    ):
        raise ValueError(
            "invalid selection — use /copy-responses "
            "[-1..-10 | n | n1,n2 | n1-n2] "
            "(absolute numbers may optionally start with #)"
        )

    indices = []

    for part in raw.split(","):
        part = part.strip()

        if "-" not in part:
            index = int(part.lstrip("#"))

            if index > count:
                raise ValueError(
                    f"nothing copied — out-of-range response: "
                    f"{index} (session has {count})"
                )

            indices.append(index)
            continue

        start_raw, end_raw = re.split(
            r"\s*-\s*",
            part,
            maxsplit=1,
        )
        start = int(start_raw.lstrip("#"))
        end = int(end_raw.lstrip("#"))

        if end < start:
            raise ValueError(
                "invalid range — range start must be <= range end"
            )

        bad = [
            value
            for value in (start, end)
            if value > count
        ]

        if bad:
            values = ",".join(str(value) for value in bad)
            raise ValueError(
                f"nothing copied — out-of-range response(s): "
                f"{values} (session has {count})"
            )

        indices.extend(range(start, end + 1))

    return indices


def table_preview(text):
    first = text.split("\n", 1)[0][:100]

    return (
        first
        .replace("\t", r"\t")
        .replace("|", r"\|")
    )


def gate_path(session_id):
    return (
        Path.home()
        / ".claude"
        / "state"
        / f"ls-{session_id}"
    )


def arm_gate(session_id, response_count):
    marker = gate_path(session_id)

    marker.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    data = {
        "version": GATE_VERSION,
        "session_id": session_id,
        "response_count_at_list": response_count,
    }

    marker.write_text(
        json.dumps(
            data,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    markers = sorted(
        marker.parent.glob("ls-*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    for stale in markers[GATE_KEEP:]:
        try:
            stale.unlink()
        except FileNotFoundError:
            pass


def gate_is_armed(session_id):
    marker = gate_path(session_id)

    try:
        data = json.loads(
            marker.read_text(
                encoding="utf-8",
            )
        )
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ):
        return False

    return (
        data.get("version") == GATE_VERSION
        and data.get("session_id") == session_id
    )


def run_list(session_id, raw):
    transcript = find_transcript(session_id)

    if transcript is None:
        print(
            "transcript not ready — send one normal message, "
            "then run /ls-responses again"
        )
        return 0

    records = load_records(transcript)

    try:
        requested = parse_count(raw)
    except ValueError as exc:
        print(exc)
        return 0

    responses = filtered_responses(records)

    if not responses:
        print(
            "no assistant responses yet — gate not armed"
        )
        return 0

    start = max(
        0,
        len(responses) - requested,
    )

    lines = [
        "| # | response |",
        "|--:|---|",
    ]

    for index in range(
        start,
        len(responses),
    ):
        lines.append(
            f"| {index + 1} | "
            f"{table_preview(responses[index])} |"
        )

    print("\n".join(lines))

    arm_gate(
        session_id,
        len(responses),
    )

    return 0


def run_copy(session_id, raw):
    if not gate_is_armed(session_id):
        print(
            "not armed — run /ls-responses first, "
            "then /copy-responses [selection]"
        )
        return 0

    transcript = find_transcript(session_id)

    if transcript is None:
        print(
            "transcript not ready — "
            "run /ls-responses again"
        )
        return 0

    records = load_records(transcript)

    responses = filtered_responses(records)

    try:
        indices = parse_selection(
            raw,
            len(responses),
        )
    except ValueError as exc:
        print(exc)
        return 0

    payload = "\n\n".join(
        responses[index - 1]
        for index in indices
    )

    try:
        proc = subprocess.run(
            ["pbcopy"],
            input=payload,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print(
            "copy-responses failed — "
            "pbcopy not found; clipboard unchanged"
        )
        return 1

    if proc.returncode != 0:
        print(
            "copy-responses failed — "
            f"pbcopy exit {proc.returncode}; "
            "clipboard result unknown"
        )
        return proc.returncode

    labels = ",".join(
        f"#{index}"
        for index in indices
    )

    preview = (
        payload
        .replace("\n", " ")[:60]
    )

    print(
        f"copied {labels} "
        f"({len(indices)} of {len(indices)}, "
        f"{len(payload)} chars): "
        f"{preview}..."
    )

    return 0


def self_test():
    records = [
        {
            "type": "user",
            "isMeta": False,
            "message": {
                "content": "hello"
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "Hello",
                    },
                ]
            },
        },
        {
            "type": "user",
            "isMeta": False,
            "message": {
                "content":
                    "<command-message>"
                    "ls-responses"
                    "</command-message>"
                    "<command-name>"
                    "/ls-responses"
                    "</command-name>"
                    "<command-args>"
                    "10"
                    "</command-args>"
            },
        },
        {
            "type": "user",
            "isMeta": True,
            "message": {
                "content": "| # | response |"
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "| # | response |",
                    },
                ]
            },
        },
        {
            "type": "user",
            "isMeta": False,
            "message": {
                "content": "second"
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text":
                            "Second\tvalue | emoji 😀",
                    },
                ]
            },
        },
        {
            "type": "user",
            "isMeta": False,
            "message": {
                "content":
                    "<command-message>"
                    "copy-responses"
                    "</command-message>"
                    "<command-name>"
                    "/copy-responses"
                    "</command-name>"
                    "<command-args>"
                    "#1,2"
                    "</command-args>"
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "copied #1,#2",
                    },
                ]
            },
        },
    ]

    assert filtered_responses(records) == [
        "Hello",
        "Second\tvalue | emoji 😀",
    ]

    assert latest_invocation_args(
        records,
        "ls-responses",
    ) == "10"

    assert latest_invocation_args(
        records,
        "copy-responses",
    ) == "#1,2"

    assert parse_count("") == 10
    assert parse_count("4") == 4

    for value in (
        "0",
        "banana",
        "1x",
        "-1",
    ):
        try:
            parse_count(value)
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"parse_count accepted {value!r}"
            )

    assert parse_selection("", 12) == [12]
    assert parse_selection("-1", 12) == [12]
    assert parse_selection("-2", 12) == [11, 12]
    assert parse_selection("-3", 12) == [10, 11, 12]
    assert parse_selection("-4", 12) == [9, 10, 11, 12]
    assert parse_selection("-10", 12) == [3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    assert parse_selection("-10", 3) == [1, 2, 3]
    assert parse_selection("10", 12) == [10]
    assert parse_selection("2,3,4", 12) == [2, 3, 4]
    assert parse_selection("2-4", 12) == [2, 3, 4]
    assert parse_selection("#2-#4", 12) == [2, 3, 4]
    assert parse_selection("#1, 2", 2) == [1, 2]

    for value in (
        "0",
        "banana",
        "1x",
        "1,999",
        "-0",
        "-11",
        "4-2",
        "2-",
    ):
        try:
            parse_selection(
                value,
                12,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(
                "parse_selection accepted "
                f"{value!r}"
            )

    assert table_preview(
        "a\tb|c"
    ) == r"a\tb\|c"

    assert len("é😀") == 2

    print("SELFTEST PASS")


def main():
    if (
        len(sys.argv) == 2
        and sys.argv[1] == "self-test"
    ):
        self_test()
        return 0

    if len(sys.argv) != 4:
        print(
            "usage: response_history.py "
            "<ls|copy> <session-id> <raw-arguments>",
            file=sys.stderr,
        )
        return 2

    mode = sys.argv[1]
    session_id = sys.argv[2]
    raw = sys.argv[3]

    if not re.fullmatch(
        r"[0-9A-Za-z._-]+",
        session_id,
    ):
        print(
            "invalid session id",
            file=sys.stderr,
        )
        return 2

    try:
        if mode == "ls":
            return run_list(session_id, raw)

        if mode == "copy":
            return run_copy(session_id, raw)

        print(
            f"unknown mode: {mode}",
            file=sys.stderr,
        )

        return 2

    except Exception as exc:
        print(
            "response-history failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
