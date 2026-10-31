"""``kb status``: the store at a glance, and what needs attention.

Only cheap things: counts from the store, the neighbors, the inbox, and the
quick ``doctor`` diagnoses.  ``kb doctor`` runs the full set and fixes.
"""

from collections import Counter

from . import doctor
from .items import Store


def status(kb: Store) -> dict:
    from .bundle import entries
    from .federation import neighbors
    cfg = kb.cfg
    current = [r for r in kb.items.values() if kb.is_current(r.id)]
    last = kb.conn.execute("SELECT at, action, item, actor FROM events ORDER BY seq DESC LIMIT 1").fetchone()
    findings = doctor.diagnose(kb, quick=True)
    return {
        "store": {"name": cfg.name, "scope": cfg.scope, "access": cfg.access, "id": cfg.id,
                  "root": str(kb.root), "user": cfg.user, "host": cfg.host},
        "items": {"current": len(current), "replaced": len(kb.items) - len(current),
                  "by_kind": dict(sorted(Counter(r.kind for r in current).items()))},
        "last_change": dict(last) if last else None,
        "inbox": {"pending": len(entries(kb, "pending"))},
        "edges": [{"name": n.name, "relation": n.relation, "available": n.available, "writable": n.writable,
                   "land": n.mode} for n in neighbors(kb)],
        "content_stores": {name: (p is not None and p.is_dir()) for name, p in sorted(cfg.cas.items())},
        "sources": _sources(kb),
        "attention": [f.as_dict() for f in findings],
    }


def _sources(kb: Store) -> list[dict]:
    from . import datasets
    return [{"id": r.id, "type": r.data.get("source_type"), "title": r.data.get("title"),
             "here": datasets.locate(kb, r) is not None}
            for r in kb.items.values() if r.kind == "source" and r.data.get("source_type") == datasets.SOURCE_TYPE]
