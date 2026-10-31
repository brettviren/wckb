"""Queries across this store and the stores it can reach (read-only)."""

from . import context as context_mod
from . import query, search as search_mod
from .errors import KBError
from .federation import reachable
from .items import Store


def stores(kb: Store, reach: int = 0, only: list[str] | None = None) -> list[tuple[str, Store]]:
    out = [("here", kb)]
    if reach > 0 or only:
        out += [(r.label, r.kb) for r in reachable(kb, max(reach, 1), only=only)]
    return out


def _merge(rows: list[dict]) -> list[dict]:
    """One row per item ID (the nearest store's), noting where else it is (pushed copies)."""
    out: dict[str, dict] = {}
    for r in rows:
        if r["id"] in out:
            out[r["id"]].setdefault("also_in", []).append(r["store"])
        else:
            out[r["id"]] = r
    return list(out.values())


def search(kb: Store, text: str, mode: str = "text", limit: int = 20, include_old: bool = False,
           reach: int = 0, only: list[str] | None = None) -> list[dict]:
    rows = []
    for label, store in stores(kb, reach, only):
        try:
            hits = search_mod.search(store, text, mode, limit, include_old)
        except KBError:
            if label == "here":
                raise
            continue  # e.g. no embeddings in that store
        rows += [h | {"store": label} for h in hits]
    rows = _merge(rows)
    if len(rows) > limit and mode == "text":
        rows.sort(key=lambda r: r["score"])  # bm25: lower is better, comparable enough across stores
    return rows[:limit]


def ls(kb: Store, reach: int = 0, only: list[str] | None = None, **kw) -> list[dict]:
    rows = []
    for label, store in stores(kb, reach, only):
        rows += [{k: r[k] for k in r.keys() if k != "json"} | {"store": label} for r in query.ls(store, **kw)]
    return _merge(rows)


def context(kb: Store, target: str, runs=None, budget: int = 12000, reach: int = 0,
            only: list[str] | None = None) -> str:
    """This store's context in full, then each reachable store's, sharing what budget is left."""
    here = context_mod.context(kb, target, runs, budget if not (reach or only) else budget * 2 // 3)
    others = stores(kb, reach, only)[1:]
    if not others:
        return here
    parts = [here]
    share = max(600, (budget - len(here)) // len(others))
    for label, store in others:
        try:
            text = context_mod.context(store, target, runs, share)
        except KBError as exc:
            text = f"(unavailable: {exc})\n"
        if text.startswith("# KB context"):
            text = text.split("\n", 1)[1]
        if "Nothing found" in text:
            continue
        parts.append(f"\n# From {label} ({store.cfg.scope} store {store.cfg.name}; read-only here)\n{text}")
    return "".join(parts)
