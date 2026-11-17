#!/usr/bin/env python3
"""Apply renovate/base-branches.json5 preset lists into Renovate *.json5 configs.

Usage:
  python3 script/apply-base-branches.py [--check]

--check  exit 0 if presets already match the SoT; exit 1 if apply would change files.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import json5
except ImportError:
    sys.stderr.write("json5 is required (pip install json5)\n")
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parents[1]
SOT_PATH = REPO_ROOT / "renovate" / "base-branches.json5"
RENOVATE_DIR = REPO_ROOT / "renovate"

BASE_BRANCHES_RE = re.compile(r'"baseBranches"\s*:\s*\[[^\]]*\]', re.DOTALL)


def load_sot() -> dict:
    with SOT_PATH.open(encoding="utf-8") as f:
        return json5.load(f)


def key_indent(text: str, match_start: int) -> str:
    line_start = text.rfind("\n", 0, match_start) + 1
    return text[line_start:match_start]


def format_base_branches(branches: list[str], *, indent: str, multiline: bool) -> str:
    if multiline or len(branches) > 4:
        item_indent = indent + "  "
        inner = ",\n".join(f'{item_indent}"{b}"' for b in branches)
        return f'"baseBranches": [\n{inner}\n{indent}]'
    joined = ", ".join(f'"{b}"' for b in branches)
    return f'"baseBranches": [{joined}]'


def was_multiline(match_text: str) -> bool:
    return "\n" in match_text


def apply_to_file(path: Path, branches: list[str], *, dry_run: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    match = BASE_BRANCHES_RE.search(text)
    if not match:
        raise SystemExit(f"No baseBranches array found in {path}")

    indent = key_indent(text, match.start())
    replacement = format_base_branches(
        branches, indent=indent, multiline=was_multiline(match.group(0))
    )
    new_text = BASE_BRANCHES_RE.sub(replacement, text, count=1)
    if new_text == text:
        return False
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify presets match base-branches.json5 without writing",
    )
    args = parser.parse_args()

    sot = load_sot()
    presets = sot.get("presets") or {}
    if not presets:
        sys.stderr.write(f"No presets defined in {SOT_PATH}\n")
        return 1

    changed = []
    for filename, branches in presets.items():
        path = RENOVATE_DIR / filename
        if not path.is_file():
            sys.stderr.write(f"Missing preset file: {path}\n")
            return 1
        if apply_to_file(path, list(branches), dry_run=args.check):
            changed.append(filename)

    if args.check:
        if changed:
            sys.stderr.write(
                "Presets out of sync with renovate/base-branches.json5:\n  "
                + "\n  ".join(changed)
                + "\nRun: python3 script/apply-base-branches.py\n"
            )
            return 1
        print("OK: presets match renovate/base-branches.json5")
        return 0

    if changed:
        print("Updated:", ", ".join(changed))
    else:
        print("No changes needed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
