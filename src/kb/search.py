"""Search: full text (BM25), semantic (embeddings), or both fused by rank."""

from . import query
from .embed import semantic
from .items import Store

RRF_K = 60


def search(kb: Store, text: str, mode: str = "text", limit: int = 20, include_old: bool = False,
           embedder=None) -> list[dict]:
    if mode == "text":
        return query.fts(kb, text, limit, include_old)
    def row(rid, score, snip=""):
        rec = kb.items.get(rid)
        if rec is None:
            return None
        d = rec.data
        return {"id": rid, "kind": rec.kind, "title": d.get("title"), "summary": d.get("summary"),
                "status": d.get("status"), "current": int(kb.is_current(rid)), "score": score, "snip": snip}

    sem = [(rid, s) for rid, s in semantic(kb, text, limit * 3, embedder)]
    if mode == "semantic":
        rows = [row(rid, s) for rid, s in sem]
    else:
        lex = query.fts(kb, text, limit * 3, include_old=True)
        scores: dict[str, float] = {}
        snips = {r["id"]: r["snip"] for r in lex}
        for rank, r in enumerate(lex):
            scores[r["id"]] = scores.get(r["id"], 0) + 1 / (RRF_K + rank)
        for rank, (rid, _) in enumerate(sem):
            scores[rid] = scores.get(rid, 0) + 1 / (RRF_K + rank)
        rows = [row(rid, s, snips.get(rid, "")) for rid, s in sorted(scores.items(), key=lambda kv: -kv[1])]
    rows = [r for r in rows if r and (include_old or r["current"])]
    return rows[:limit]
