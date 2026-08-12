#!/usr/bin/env python3
"""Parse the supported Compose dotenv subset without evaluating shell code."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_value(raw: str, line_number: int) -> str:
    value = raw.strip()
    if not value:
        return ""

    if value.startswith("'"):
        end = value.find("'", 1)
        if end < 0 or value[end + 1 :].strip().lstrip("#").strip():
            raise ValueError(f"line {line_number}: invalid single-quoted value")
        return value[1:end]

    if value.startswith('"'):
        decoder = json.JSONDecoder()
        try:
            parsed, end = decoder.raw_decode(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number}: invalid double-quoted value") from exc
        if not isinstance(parsed, str):
            raise ValueError(f"line {line_number}: expected a string value")
        if value[end:].strip().lstrip("#").strip():
            raise ValueError(f"line {line_number}: content after quoted value")
        return parsed

    value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
    return value


def parse(path: Path) -> list[tuple[str, str]]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"line {line_number}: expected NAME=VALUE")
        name, raw_value = line.split("=", 1)
        name = name.strip()
        if not NAME.fullmatch(name):
            raise ValueError(f"line {line_number}: invalid variable name {name!r}")
        values[name] = parse_value(raw_value, line_number)
    return list(values.items())


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} FILE", file=sys.stderr)
        return 2
    try:
        values = parse(Path(sys.argv[1]))
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"{sys.argv[1]}: {exc}", file=sys.stderr)
        return 1
    output = sys.stdout.buffer
    for name, value in values:
        output.write(name.encode() + b"\0" + value.encode() + b"\0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
