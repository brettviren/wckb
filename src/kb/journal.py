"""Journals: what happened while artifacts were made (decisions/0004, phase 2).

People (and LLMs) move between artifacts, and between an artifact and other
work; they won't keep a "current workspace" switched right.  So there is no
such state.  An *artifact workspace* is a directory (a DataLad dataset)
marked by ``.kb-artifact/workspace.json``, found from the working directory
as a store context is.  Each entry is appended to the journal of the
workspace it happened in, or, outside any, to the store's own journal
(``.kb/journal.jsonl``), and says where (``cwd``), when (``at``) and in which
session (``session``: Claude Code's, when kb's session hook has set
``KB_SESSION``).  Harvesting an artifact (``kb artifact finish``) gathers its
workspace's entries, and offers the store journal's entries of the same
sessions, for the person or LLM to take or leave.

Entries (``kind``):

- ``served``: items kb gave someone to read (``kb show``, ``kb item content``,
  ``kb rendition path``, ``kb open``, ``kb context ID``), with their digests:
  the candidate inputs of an artifact;
- ``wrote``: items created (a fit result filed as an observation ...);
- ``run``: a command run with ``kb artifact run`` (``datalad run``), its commit;
- ``prompt``: a prompt given to an LLM in a workspace (the artifact hook);
- ``edit``: a file an LLM wrote or edited in a workspace (the artifact hook);
- ``session``: a session began, with its model (the session hook).

Journal writes never fail a command: a journal is a help, not a record of
record.  The record is what harvest files in the store.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

WS_DIR = ".kb-artifact"
WS_FILE = "workspace.json"
JOURNAL = "journal.jsonl"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def workspace(start: Path | None = None) -> Path | None:
    """The artifact workspace holding START (default: the working directory), or None."""
    here = Path(start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / WS_DIR / WS_FILE).is_file():
            return d
    return None


def read_workspace(ws: Path) -> dict:
    return json.loads((Path(ws) / WS_DIR / WS_FILE).read_text())


def write_workspace(ws: Path, data: dict) -> None:
    p = Path(ws) / WS_DIR / WS_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def session() -> str | None:
    return os.environ.get("KB_SESSION") or None


def journal_path(kb=None, start: Path | None = None) -> Path | None:
    """Where an entry made in START goes: its workspace's journal, else the store's."""
    ws = workspace(start)
    if ws is not None:
        return ws / WS_DIR / JOURNAL
    return kb.cfg.kbdir / JOURNAL if kb is not None else None


def append(kind: str, kb=None, start: Path | None = None, **fields) -> None:
    """Add an entry; never fails the command that makes it."""
    try:
        path = journal_path(kb, start)
        if path is None or (kb is not None and kb.readonly and workspace(start) is None):
            return
        entry = {"at": now(), "kind": kind, "cwd": str(Path(start or Path.cwd()).resolve()),
                 **({"session": session()} if session() else {}), **fields}
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:          # noqa: BLE001 - journals are a help
        log.debug("journal: %s", exc)


def served(kb, refs: list[str], how: str) -> None:
    """kb gave these items to someone to read: log them with the digest they had."""
    items = []
    for ref in refs:
        ident = ref.split("#")[0]
        if ident in kb.items:
            items.append({"id": ident, "digest": kb.ref_digest(ident), **({"rendition": ref.split("#")[1]}
                                                                         if "#" in ref else {})})
    if items:
        append("served", kb, how=how, items=items, store=kb.cfg.id)


def entries(path: Path) -> list[dict]:
    if not path or not Path(path).is_file():
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
