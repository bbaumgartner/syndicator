#!/usr/bin/env python3
"""List workflow files with referenced sub-workflows before their parents."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def reference(node: dict[str, Any]) -> str | None:
    value = node.get("parameters", {}).get("workflowId")
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("value"), str):
        return value["value"]
    return None


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} SOURCE_ROOT", file=sys.stderr)
        return 2
    source_root = Path(sys.argv[1])
    paths = sorted((source_root / "n8n" / "workflows").glob("*.json"))
    workflows = {path: json.loads(path.read_text(encoding="utf-8")) for path in paths}
    by_id = {workflow["id"]: path for path, workflow in workflows.items()}

    ordered: list[Path] = []
    visiting: set[Path] = set()
    visited: set[Path] = set()

    def visit(path: Path) -> None:
        if path in visited:
            return
        if path in visiting:
            raise ValueError(f"workflow dependency cycle at {path.name}")
        visiting.add(path)
        dependencies = {
            dependency
            for node in workflows[path].get("nodes", [])
            if (dependency := reference(node)) is not None
        }
        for dependency in sorted(dependencies):
            if dependency not in by_id:
                raise ValueError(f"{path.name} references unknown workflow {dependency}")
            visit(by_id[dependency])
        visiting.remove(path)
        visited.add(path)
        ordered.append(path)

    for path in paths:
        visit(path)
    for path in ordered:
        print(path.relative_to(source_root))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
