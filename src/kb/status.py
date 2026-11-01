"""``kb status``: the store at a glance, and what needs attention.

Only cheap things: counts from the store, the neighbors, the inbox, and the
quick ``doctor`` diagnoses.  ``kb doctor`` runs the full set and fixes.
"""

from collections import Counter

from . import doctor
from .items import Store


def status(kb: Store, context=None) -> dict:
    """``context``: the :class:`kb.config.StoreContext` the store was found by, if it was."""
    from .bundle import entries
    from .config import config_files
    from .federation import neighbors
    cfg = kb.cfg
    current = [r for r in kb.items.values() if kb.is_current(r.id)]
    last = kb.conn.execute("SELECT at, action, item, actor FROM events ORDER BY seq DESC LIMIT 1").fetchone()
    findings = doctor.diagnose(kb, quick=True)
    return {
        "store": {"name": cfg.name, "scope": cfg.scope, "access": cfg.access, "id": cfg.id,
                  "root": str(kb.root), "user": cfg.user, "host": cfg.host},
        "context": context and {"name": context.name, "named_by": context.named_by,
                                "located_by": context.located_by},
        "config_files": [str(f) for f in config_files()],
        "items": {"current": len(current), "replaced": len(kb.items) - len(current),
                  "by_kind": dict(sorted(Counter(r.kind for r in current).items()))},
        "last_change": dict(last) if last else None,
        "inbox": {"pending": len(entries(kb, "pending"))},
        "edges": [{"name": n.name, "relation": n.relation, "available": n.available, "writable": n.writable,
                   "land": n.mode} for n in neighbors(kb)],
        "content_stores": {name: (p is not None and p.is_dir()) for name, p in sorted(cfg.cas.items())},
        "content_dataset": _content(kb),
        "sources": _sources(kb),
        "attention": [f.as_dict() for f in findings],
    }


def _content(kb: Store) -> dict | None:
    from .content import CONTENT_SETTING, content_dataset
    found = content_dataset(kb)
    if found:
        return {"id": found[0].id, "path": str(found[1])}
    ref = (kb.setting(CONTENT_SETTING) or {}).get("dataset")
    return {"id": ref, "path": None} if ref else None


def _sources(kb: Store) -> list[dict]:
    from .sources import report_all
    return [{"id": r["id"], "type": r["type"], "title": r["title"], "here": r["here"] is not None}
            for r in report_all(kb)]
