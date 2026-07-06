#!/usr/bin/env python
"""Strip Databricks notebook magic markers and emit raw Python source.

The `%md` and `%sql` cell separators in `.py` Databricks notebooks are stored
as `# MAGIC %md` / `# COMMAND ----------` comment lines. The Databricks
Import-Notebook API expects clean Python source for `language=PYTHON` notebooks,
so we drop those markers and the empty lines that surround them.
"""
from __future__ import annotations

import argparse
from pathlib import Path

MAGIC_PREFIXES = ("# MAGIC", "# COMMAND")


def strip_magic(source: str) -> str:
    """Return ``source`` with magic markers and their blank neighbours removed."""
    lines: list[str] = []
    for raw in source.splitlines():
        stripped = raw.lstrip()
        if any(stripped.startswith(p) for p in MAGIC_PREFIXES):
            # Skip magic lines and the following blank line if any.
            continue
        lines.append(raw)
    text = "\n".join(lines)
    # Collapse runs of more than 2 blank lines.
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    clean = strip_magic(args.input.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(clean, encoding="utf-8")
    print(f"wrote {args.output} ({len(clean)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
