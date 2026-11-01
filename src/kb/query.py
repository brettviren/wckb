"""Querying a store: faceted listing and full-text search.

The query tables (subjects, runs, refs, identifiers, renditions, fts) live in
the store's database and are kept in step on every write (``Store.save``);
``kb store reindex`` (``Store.reindex``) rebuilds them only after a repair.
"""

import sqlite3

from .errors import KBError
from .items import Store



def ls(kb: Store, kind=None, subject=None, runs=None, status=None, include_old=False,
       since=None, limit=None) -> list[dict]:
    q = """SELECT DISTINCT i.id, i.kind, json_extract(i.json, '$.title') AS title,
                  json_extract(i.json, '$.summary') AS summary, json_extract(i.json, '$.status') AS status,
                  json_extract(i.json, '$.access') AS access, i.created_at, i.updated_at
           FROM items i"""
    where, args = [], []
    if subject:
        q += " JOIN subjects s ON s.id = i.id"
        where.append("(s.subject = ? OR s.subject LIKE ?)")
        args += [subject.strip("/"), subject.strip("/") + "/%"]
    if runs:
        q += " JOIN runs n ON n.id = i.id"
        where.append("n.first <= ? AND n.last >= ?")
        args += [runs[1], runs[0]]
    if kind:
        where.append("i.kind = ?")
        args.append(kind)
    if status:
        where.append("json_extract(i.json, '$.status') = ?")
        args.append(status)
    if since:
        where.append("i.created_at >= ?")
        args.append(since)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY i.created_at DESC"
    rows = [dict(r) for r in kb.conn.execute(q, args)]
    for r in rows:
        r["current"] = int(kb.is_current(r["id"]))
    if not include_old:
        rows = [r for r in rows if r["current"]]
    return rows[:limit] if limit else rows


def match_expression(text: str) -> str:
    """What somebody typed, as an FTS5 query: every word quoted (punctuation is not syntax), ANDed; a
    trailing * keeps prefix search."""
    terms = []
    for w in text.split():
        star = w.endswith("*")
        w = w.rstrip("*").replace('"', '""')
        if w:
            terms.append(f'"{w}"' + ("*" if star else ""))
    return " ".join(terms)


def fts(kb: Store, text: str, limit: int = 20, include_old: bool = False) -> list[dict]:
    """Items matching TEXT, best first, each with the rendition (and chunk) of its best row: '' for the
    item's head (title, summary, subjects), else the text rendition the words are in."""
    q = match_expression(text)
    if not q:
        return []
    # the rows scored first (MATERIALIZED: bm25 and snippet need the MATCH), then the best row per item
    sql = """WITH h AS MATERIALIZED (
               SELECT id, rendition, chunk, page, bm25(fts, 0, 0, 0, 0, 0, 5.0, 3.0, 2.0, 1.0) AS score,
                      snippet(fts, -1, '[', ']', '…', 12) AS snip
               FROM fts WHERE fts MATCH ?)
             SELECT h.id, h.rendition, h.chunk, h.page, min(h.score) AS score, h.snip, i.kind,
                    json_extract(i.json, '$.title') AS title, json_extract(i.json, '$.summary') AS summary,
                    json_extract(i.json, '$.status') AS status
             FROM h JOIN items i ON i.id = h.id GROUP BY h.id ORDER BY score LIMIT ?"""
    try:
        rows = [dict(r) for r in kb.conn.execute(sql, (q, limit * 3))]
    except sqlite3.OperationalError as exc:
        if "no such column" not in str(exc):
            raise
        raise KBError(f"{kb.root}: its search index predates this kb; open the store once for writing "
                      "(any kb command there upgrades it), or search a fresh snapshot") from None
    for r in rows:
        r["current"] = int(kb.is_current(r["id"]))
        r.update(_via(kb, r["id"], r["rendition"]))
    if not include_old:
        rows = [r for r in rows if r["current"]]
    return rows[:limit]


def _via(kb: Store, ident: str, rendition: str) -> dict:
    """Where a hit is: the rendition, and the one it was derived from (the original of extracted text)."""
    rec = kb.items.get(ident)
    if not rendition or rec is None or not rec.has_rendition(rendition):
        return {"rendition": rendition or None, "from": None}
    return {"rendition": rendition, "from": rec.rendition(rendition).get("derived_from")}


def subjects(kb: Store) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in kb.conn.execute("SELECT subject, id FROM subjects"):
        if kb.is_current(r["id"]):
            out[r["subject"]] = out.get(r["subject"], 0) + 1
    return dict(sorted(out.items()))

