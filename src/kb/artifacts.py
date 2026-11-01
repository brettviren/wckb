"""Artifacts: documents made here to present knowledge (decisions/0004).

An artifact is a ``document`` of doctype ``technote``, ``demo`` or
``notebook`` (or any document with a ``provenance`` level).  Its inputs, the
items it presents, are in ``derived_from``, pinned by digest when it is made;
when one of them changes, the artifact is stale (``kb item stale``), and a
person or LLM remakes or revises it.  Its ``provenance`` says how much of
its making is recorded: ``cited`` (the inputs), ``recorded`` (also the steps),
``rebuildable`` (a view remakes it).
"""

from . import graph
from .items import Store


def listing(kb: Store, doctypes: tuple[str, ...] = (), stale_only: bool = False,
            include_old: bool = False) -> list[dict]:
    """The store's artifacts, newest first: what each is, how it was made, and whether it is stale."""
    stale = {s.id: s.reasons for s in graph.stale(kb) if s.what == "artifact"}
    rows = []
    for rec in kb.items.values():
        d = rec.data
        if not graph.is_artifact(d) or (doctypes and d.get("doctype") not in doctypes):
            continue
        if not include_old and not kb.is_current(rec.id):
            continue
        if stale_only and rec.id not in stale:
            continue
        rows.append({"id": rec.id, "title": d.get("title", ""), "summary": d.get("summary", ""),
                     "doctype": d.get("doctype"), "provenance": d.get("provenance", "cited"),
                     "date": d.get("date") or next(iter((d.get("dates") or {}).values()), None),
                     "inputs": len(rec.inputs()), "made_with": (d.get("created") or {}).get("with", []),
                     "stale": stale.get(rec.id, []), "created": d["created"]["at"]})
    return sorted(rows, key=lambda r: r["created"], reverse=True)
