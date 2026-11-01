"""Claude Code hooks: ``kb hook check|recall|session``.

Hooks read the event JSON on stdin.  They must be fast and must never get in
the way: anything unexpected exits 0 silently.  check reports problems with
exit code 2 (shown to Claude); recall and session print a few lines that
Claude Code adds to the context.
"""

import json
import re
from pathlib import Path

from .config import find_root
from .errors import KBError
from .items import Store

from .query import STOP


def _open(event: dict, root: Path | None) -> Store | None:
    try:
        if root:
            return Store.open(root, readonly=True)
        cwd = event.get("cwd")
        return Store.open(find_root(Path(cwd) if cwd else None), readonly=True)
    except KBError:
        return None


def check(event: dict, root: Path | None = None) -> tuple[int, str]:
    """After Claude edits a file inside a kb item checkout, validate the checkout (nothing is applied)."""
    from .edit import CHECKOUT_MARK, checkin
    path = (event.get("tool_input") or {}).get("file_path")
    if not path:
        return 0, ""
    p = Path(path).resolve()
    for d in p.parents:
        if (d / CHECKOUT_MARK).exists():
            break
    else:
        return 0, ""
    import json as _json
    try:
        mark = _json.loads((d / CHECKOUT_MARK).read_text())
        kb = Store.open(Path(mark["store"]), readonly=True)
        checkin(kb, d, dry_run=True)
    except KBError as exc:
        return 2, (f"kb: the checkout {d} would not check in:\n  {exc}\n"
                   f"Fix it, then run kb item checkin {d} to apply.")
    except Exception:  # noqa: BLE001 - a hook must never break the session
        return 0, ""
    return 0, ""


def keywords(prompt: str, limit: int = 8) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_+.-]{2,}|\d{3,}", prompt)
    seen, out = set(), []
    for w in sorted(words, key=len, reverse=True):
        lw = w.lower().strip(".-")
        if lw in STOP or lw in seen:
            continue
        seen.add(lw)
        out.append(lw)
    return out[:limit]


def recall(event: dict, root: Path | None = None, limit: int = 5) -> str:
    """A handful of current items that share at least two key words with the prompt."""
    prompt = event.get("prompt", "")
    if len(prompt) < 15 or prompt.lstrip().startswith("/"):
        return ""
    kb = _open(event, root)
    if kb is None or not kb.items:
        return ""
    words = keywords(prompt)
    if not words:
        return ""
    q = " OR ".join('"' + w.replace('"', '') + '"' for w in words)
    # the item heads only (chunk 0): one row per item, matched on what it is about
    rows = [r for r in kb.conn.execute(
        """SELECT i.id, i.kind, json_extract(i.json, '$.status') AS status,
                  json_extract(i.json, '$.title') AS title, json_extract(i.json, '$.summary') AS summary,
                  f.subjects AS subj
           FROM fts f JOIN items i ON i.id = f.id
           WHERE fts MATCH ? AND f.rendition = '' ORDER BY bm25(fts, 0, 0, 0, 0, 0, 5.0, 3.0, 2.0, 1.0)
           LIMIT 20""", (q,)).fetchall()
            if kb.is_current(r["id"])]
    need = 2 if len(words) > 1 else 1
    hits = []
    for r in rows:
        hay = " ".join(str(x or "") for x in (r["title"], r["summary"], r["subj"])).lower()
        if sum(w in hay for w in words) >= need:
            hits.append(r)
        if len(hits) >= limit:
            break
    if not hits:
        return ""
    lines = [f"KB may be relevant (store {kb.cfg.name}; `kb context ID` for more, ignore if not):"]
    for r in hits:
        status = f", {r['status']}" if r["status"] else ""
        lines.append(f"- {r['id']} [{r['kind']}{status}] {r['title']}: {r['summary']}")
    return "\n".join(lines)


def coauthor(event: dict) -> str:
    """Who an LLM session is, for KB_COAUTHOR: the model (if the event says) and the tool."""
    model = event.get("model")
    if isinstance(model, dict):
        model = model.get("id") or model.get("display_name")
    return f"{model} (Claude Code)" if model else "Claude Code"


def _set_coauthor(event: dict) -> None:
    """Give every command of the session KB_COAUTHOR (unless the person set one) and KB_SESSION (for the
    journals of artifact workspaces), through Claude Code's session environment file."""
    import os
    import shlex
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if not env_file:
        return
    lines = []
    if not os.environ.get("KB_COAUTHOR"):
        lines.append(f"export KB_COAUTHOR={shlex.quote(coauthor(event))}")
    if event.get("session_id"):
        lines.append(f"export KB_SESSION={shlex.quote(str(event['session_id']))}")
    try:
        with open(env_file, "a") as f:
            f.write("".join(line + "\n" for line in lines))
    except OSError:
        pass


def artifact(event: dict) -> None:
    """In an artifact workspace, note what the LLM session does there: the prompts it is given
    (UserPromptSubmit; unless the workspace says prompts=false) and the files it writes (PostToolUse)."""
    from . import journal
    sid = event.get("session_id")
    name = event.get("hook_event_name")
    if name == "UserPromptSubmit":
        cwd = Path(event.get("cwd") or ".")
        ws = journal.workspace(cwd)
        if ws is None or not journal.read_workspace(ws).get("prompts", True):
            return
        journal.append("prompt", start=cwd, prompt=event.get("prompt", ""), **({"session": sid} if sid else {}))
    elif name == "PostToolUse":
        path = (event.get("tool_input") or {}).get("file_path")
        if not path:
            return
        p = Path(path).resolve()
        ws = journal.workspace(p.parent)
        if ws is None or not p.is_relative_to(ws):
            return
        journal.append("edit", start=p.parent, file=str(p.relative_to(ws)), tool=event.get("tool_name"),
                       **({"session": sid} if sid else {}))


def session(event: dict, root: Path | None = None) -> str:
    """One short status note at session start; and the session's co-author for what it writes."""
    from . import graph, policy
    from .federation import neighbors
    from .push import candidates
    _set_coauthor(event)
    kb = _open(event, root)
    if kb is not None and event.get("session_id"):
        from . import journal
        journal.append("session", kb, start=Path(event.get("cwd") or "."), session=str(event["session_id"]),
                       model=coauthor(event))
    if kb is None:
        return ""
    recs = kb.items
    topics = sum(r.kind == "topic" for r in recs.values())
    parts = [f"KB store {kb.cfg.name} ({kb.cfg.scope}): {len(recs)} items, {topics} topics."]
    for n in neighbors(kb):
        if n.relation == "up" and n.writable:
            try:
                cs = [c for c in candidates(kb, n.name) if c.state in ("new", "changed")]
            except KBError:
                continue
            marked = sum(any("marked" in r or "corrects" in r for r in c.reasons) for c in cs)
            if marked:
                parts.append(f"{marked} item(s) marked or correcting pushed ones await promotion to {n.name} "
                             f"(kb edge candidates {n.name}).")
    try:
        st = graph.stale(kb)
        if st:
            parts.append(f"{len(st)} stale item(s) (kb item stale).")
        ps = policy.status(kb)
        if ps:
            parts.append(f"Policy: {len(ps)} item(s) need attention (kb policy status).")
    except KBError:
        pass
    return " ".join(parts)


def run(kind: str, stdin_text: str, root: Path | None = None) -> tuple[int, str]:
    try:
        event = json.loads(stdin_text) if stdin_text.strip() else {}
    except json.JSONDecodeError:
        return 0, ""
    try:
        if kind == "check":
            return check(event, root)
        if kind == "recall":
            return 0, recall(event, root)
        if kind == "session":
            return 0, session(event, root)
        if kind == "artifact":
            artifact(event)
            return 0, ""
    except Exception:  # noqa: BLE001 - a hook must never break the session
        return 0, ""
    return 0, ""
