"""Shared bits for the read-only live checks in tests/live/.

Every script here only READS (GET/list calls and POST /audiences/count, which is what the
segment editor sends while you type). Nothing creates, updates or deletes anything and no
credits are spent. Run from a directory whose parent chain contains the .env with
CLAY_SESSION (the repo root, or your project root). The loader stops at the nearest directory
that has a .git entry — a git worktree's .git *file* counts — so from a worktree export the
cookie instead:  CLAY_SESSION=s%3A...  python tests/live/<script>.py ...

    python tests/live/<script>.py --workspace <id> ...

Pass --workspace explicitly: the cookie's FIRST workspace is the default in ClayClient and is
usually not the one you mean. Paste only the printed classifications (PASS/FAIL/sql/loose)
into docs or the PR — never raw counts or ids.
"""
from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from clay_client import ClayClient  # noqa: E402


def client(workspace: str | int) -> ClayClient:
    """ClayClient bound to `workspace`; the constructor's login line (it prints your e-mail)
    is swallowed so the login e-mail never reaches the output. The rest of the output is still
    not for sharing as-is: paste only the PASS/FAIL classifications, never raw counts or ids
    (module docstring)."""
    with contextlib.redirect_stdout(io.StringIO()):
        return ClayClient(workspace_id=int(workspace))


def verdict(name: str, ok: bool | None, detail: str = "") -> None:
    tag = "PASS" if ok else ("INFO" if ok is None else "FAIL")
    print(f"  {tag:4} {name}{(' — ' + detail) if detail else ''}")
