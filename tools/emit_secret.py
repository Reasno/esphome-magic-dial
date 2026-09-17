#!/usr/bin/env python3
"""Print one secret value from secrets.yaml on stdout, without a newline.

Used to pipe the OTA password straight into a file on the HA box, so it never
lands in shell history or terminal output.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: emit_secret.py <secrets.yaml> <key>", file=sys.stderr)
        return 2
    data = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8")) or {}
    value = data.get(sys.argv[2])
    if value is None:
        print(f"key not found: {sys.argv[2]}", file=sys.stderr)
        return 1
    sys.stdout.write(str(value).strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
