#!/usr/bin/env python3
"""PostToolUse hook: fast feedback when a doc-guarded file is edited.

Runs tests/test_docs_guard.py right after Claude writes to CLAUDE.md,
README.md, docs/*.md, or .claude/rules/*.md, instead of waiting for the
next full `pytest tests/ -q` or for CI. Deliberately non-blocking (exit 2
only puts the failure on Claude's stderr — PostToolUse can't undo the
edit anyway): a multi-step doc edit is often inconsistent between
individual Edit calls (a link fixed a step later, an anchor renamed
before its referrers catch up), and hard-blocking on every intermediate
state would make exactly the kind of edit this guard exists to protect
(splitting CLAUDE.md across docs/) painful to perform.

See tests/test_docs_guard.py for what is actually checked.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

DOC_ROOTS = ("docs/", ".claude/rules/")
DOC_FILES = ("CLAUDE.md", "README.md")


def _repo_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    # Fall back to the hook's own location: .claude/hooks/<this file>.
    return Path(__file__).resolve().parent.parent.parent


def _is_guarded(file_path: str, repo_root: Path) -> bool:
    try:
        rel = Path(file_path).resolve().relative_to(repo_root.resolve())
    except ValueError:
        return False
    rel_posix = rel.as_posix()
    if rel_posix in DOC_FILES:
        return True
    return rel_posix.endswith(".md") and rel_posix.startswith(DOC_ROOTS)


def main() -> int:
    # Failure output can quote Japanese headings from docs/*.md; the
    # legacy Windows console codepage can't encode all of it, so replace
    # rather than crash the hook over a cosmetic character.
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # Can't parse the hook input — fail open, stay silent.

    file_path = payload.get("tool_input", {}).get("file_path")
    if not file_path:
        return 0

    repo_root = _repo_root()
    if not _is_guarded(file_path, repo_root):
        return 0

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_docs_guard.py", "-q"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode == 0:
        return 0  # Success: stay silent, per hook convention.

    sys.stderr.write(
        "tests/test_docs_guard.py failed after editing "
        f"{Path(file_path).name} - fix before moving on:\n\n"
        f"{result.stdout}{result.stderr}\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
