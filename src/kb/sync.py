"""Twins: copies of one store (same store id), kept in sync item by item.

A ``twin`` neighbor is this same store somewhere else, e.g. a home store on a
laptop and on a desktop.  ``kb sync`` moves every new or changed item both
ways, as clocked transfers: when both sides changed an item's mutable fields,
the later change wins (``prefer = newer``); a change to what an item asserts
can't happen (frozen) and would be reported as a conflict.  A twin reached
only through a published snapshot is pulled from, not written.

This is spike S4 territory: last-writer-wins per item by update time is the
simple first version (see decisions/0001 and questions B15).
"""

from .errors import KBError
from .federation import neighbors, open_store
from .push import changed_items
from .items import Store, now
from .transfer import transfer


def _sync_set(ids: list[str]) -> dict:
    # A transient set: twins are one store, so their sync needs no stored selection.
    return {"id": "ks-twinsync" + now().replace(":", "").replace("-", "").lower()[:10],
            "kind": "kset", "members": ids, "depth": "shallow"}


def sync(kb: Store, names: list[str] | None = None) -> list[dict]:
    twins = [n for n in neighbors(kb) if n.relation == "twin" and (not names or n.name in names)]
    if not twins:
        raise KBError("no twin neighbors" + (f" named {', '.join(names)}" if names else "")
                      + "; kb neighbor add NAME --relation twin --path PATH (or --url SNAPSHOT)")
    out = []
    for n in twins:
        if not n.available:
            out.append({"twin": n.name, "result": "not available here (kb neighbor fetch, or a path)"})
            continue
        other = open_store(n.location, readonly=not n.writable)
        if other.cfg.id != kb.cfg.id:
            raise KBError(f"{n.name} is a different store (id {other.cfg.id}), not a twin")
        rep = {"twin": n.name}
        ids = changed_items(other, kb, "newer")
        rep["pulled"] = transfer(other, kb, _sync_set(ids), prefer="newer", edge=n.name, direction="sync",
                                 log_source=False)["items"] if ids else {}
        if n.writable:
            ids = changed_items(kb, other, "newer")
            rep["pushed"] = transfer(kb, other, _sync_set(ids), prefer="newer", edge=n.name,
                                     direction="sync")["items"] if ids else {}
        else:
            rep["pushed"] = "read-only snapshot; run kb sync on the other side"
        out.append(rep)
    return out
