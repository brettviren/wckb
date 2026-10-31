"""The derivation graph: dependencies, staleness and stamping.

Edges run from a derived item to its inputs (``derived_from``).  A topic or
view pins each input by digest once stamped or built; when an input's digest
changes or it stops being current, the derived item is stale.
"""

import json
from dataclasses import dataclass, field

from .errors import KBError
from .ids import parse_ref
from .items import Store, Item


@dataclass
class Stale:
    id: str
    kind: str
    what: str                     # "rendition", "topic", "view", "item"
    reasons: list[str] = field(default_factory=list)
    rendition: str | None = None

    def as_dict(self):
        return {"id": self.id, "kind": self.kind, "what": self.what,
                "rendition": self.rendition, "reasons": self.reasons}


def deps(kb: Store, ident: str, transitive: bool = False) -> list[str]:
    seen: list[str] = []
    todo = [ident]
    while todo:
        rec = kb.get(todo.pop(0))
        for ref in rec.inputs():
            dep = parse_ref(ref)[0]
            if dep not in seen and dep != ident:
                seen.append(dep)
                if transitive and dep in kb.items:
                    todo.append(dep)
    return seen


def reverse_edges(kb: Store) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for rec in kb.items.values():
        for ref in rec.inputs():
            out.setdefault(parse_ref(ref)[0], []).append(rec.id)
        for ref in rec.data.get("evidence", []):
            if "://" not in ref:
                out.setdefault(parse_ref(ref)[0], []).append(rec.id)
    return out


def rdeps(kb: Store, ident: str, transitive: bool = False) -> list[str]:
    kb.get(ident)
    edges = reverse_edges(kb)
    seen: list[str] = []
    todo = [ident]
    while todo:
        for user in edges.get(todo.pop(0), []):
            if user not in seen and user != ident:
                seen.append(user)
                if transitive:
                    todo.append(user)
    return seen


def topo_order(kb: Store, ids) -> list[str]:
    """*ids* ordered so every item comes after those of its inputs also in *ids*."""
    wanted = set(ids)
    out: list[str] = []
    state: dict[str, int] = {}

    def visit(i, path):
        if state.get(i) == 2:
            return
        if state.get(i) == 1:
            raise KBError("derivation cycle: " + " -> ".join(path + [i]))
        state[i] = 1
        if i in kb.items:
            for ref in kb.items[i].inputs():
                d = parse_ref(ref)[0]
                if d in wanted:
                    visit(d, path + [i])
        state[i] = 2
        out.append(i)

    for i in sorted(wanted):
        visit(i, [])
    return out


def _input_problems(kb: Store, rec: Item, pinned: dict[str, str]) -> list[str]:
    reasons = []
    for ref in rec.inputs():
        ident = parse_ref(ref)[0]
        if not kb.exists(ref):
            reasons.append(f"input {ref} does not exist")
            continue
        if not kb.is_current(ident):
            reasons.append(f"input {ident} is superseded/retracted by {', '.join(kb.superseded_by[ident])}")
        if ref not in pinned:
            reasons.append(f"input {ref} not stamped")
        elif pinned[ref] != kb.ref_digest(ref):
            reasons.append(f"input {ref} changed since stamped")
    return reasons


def stale_renditions(kb: Store) -> list[Stale]:
    out = []
    for rec in kb.items.values():
        for r in rec.renditions:
            if r["role"] != "derived":
                continue
            try:
                src = rec.rendition(r["derived_from"])
            except KBError:
                out.append(Stale(rec.id, rec.kind, "rendition", [f"source rendition {r['derived_from']} is gone"], r["name"]))
                continue
            if r.get("source_sha256") != src.get("sha256"):
                out.append(Stale(rec.id, rec.kind, "rendition", [f"{r['derived_from']} changed"], r["name"]))
    return out


def topic_staleness(kb: Store, rec: Item) -> list[str]:
    pinned = {d["ref"]: d["digest"] for d in rec.data.get("derived_from", [])
              if isinstance(d, dict) and "digest" in d}
    return _input_problems(kb, rec, pinned)


def stale(kb: Store) -> list[Stale]:
    from .build import view_staleness
    out = stale_renditions(kb)
    for rec in kb.items.values():
        if rec.kind == "view":
            reasons = view_staleness(kb, rec)
            if reasons:
                out.append(Stale(rec.id, rec.kind, "view", reasons))
        elif rec.data.get("derived_from"):
            reasons = topic_staleness(kb, rec)
            if reasons:
                out.append(Stale(rec.id, rec.kind, "topic" if rec.kind == "topic" else "item", reasons))
    return out


def stamp(kb: Store, ident: str) -> Item:
    """Pin a derived item's inputs by digest (after revising it)."""
    import copy
    rec = kb.get(ident)
    if rec.frozen:
        raise KBError(f"{rec.id} is frozen; frozen items are not stamped")
    if rec.kind == "view":
        raise KBError("views are pinned by kb view build, which records a manifest")
    missing = [ref for ref in rec.inputs() if not kb.exists(ref)]
    if missing:
        raise KBError(f"cannot stamp {rec.id}: inputs not in this store: {', '.join(missing)} (kb pull fetches them)")
    data = copy.deepcopy(rec.data)
    data["derived_from"] = [{"ref": ref, "digest": kb.ref_digest(ref)} for ref in rec.inputs()]
    return kb.save(Item(data, kb), "stamp")


def dag(kb: Store, fmt: str = "json") -> str:
    nodes = {i: {"kind": r.kind, "title": r.data.get("title", ""), "current": kb.is_current(i)}
             for i, r in kb.items.items()}
    edges = [(i, parse_ref(ref)[0]) for i, r in kb.items.items() for ref in r.inputs()]
    edges += [(new, old) for old, news in kb.superseded_by.items() for new in news]
    if fmt == "json":
        return json.dumps({"nodes": nodes, "edges": edges}, indent=2)
    if fmt == "dot":
        lines = ["digraph kb {", "  rankdir=LR;"]
        for i, n in nodes.items():
            style = "" if n["current"] else ", style=dashed"
            label = f"{i}\\n{n['title'][:40]}".replace('"', "'")
            lines.append(f'  "{i}" [label="{label}"{style}];')
        lines += [f'  "{a}" -> "{b}";' for a, b in edges]
        lines.append("}")
        return "\n".join(lines)
    raise KBError("dag format is json or dot")
