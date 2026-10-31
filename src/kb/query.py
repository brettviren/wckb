"""Querying a store: faceted listing and full-text search.

The query tables (subjects, runs, refs, identifiers, renditions, fts) live in
the store's database and are kept in step on every write (``Store.save``);
``kb store reindex`` (``Store.reindex``) rebuilds them only after a repair.
"""

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


def fts(kb: Store, text: str, limit: int = 20, include_old: bool = False) -> list[dict]:
    terms = []
    for w in text.split():
        star = w.endswith("*")
        w = w.rstrip("*").replace('"', '""')
        if w:
            terms.append(f'"{w}"' + ("*" if star else ""))
    if not terms:
        return []
    q = """SELECT f.id, bm25(fts, 0, 5.0, 3.0, 2.0, 1.0) AS score,
                  snippet(fts, 4, '[', ']', '…', 12) AS snip, i.kind,
                  json_extract(i.json, '$.title') AS title, json_extract(i.json, '$.summary') AS summary,
                  json_extract(i.json, '$.status') AS status
           FROM fts f JOIN items i ON i.id = f.id WHERE fts MATCH ? ORDER BY score LIMIT ?"""
    rows = [dict(r) for r in kb.conn.execute(q, (" ".join(terms), limit * 3))]
    for r in rows:
        r["current"] = int(kb.is_current(r["id"]))
    if not include_old:
        rows = [r for r in rows if r["current"]]
    return rows[:limit]


def subjects(kb: Store) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in kb.conn.execute("SELECT subject, id FROM subjects"):
        if kb.is_current(r["id"]):
            out[r["subject"]] = out.get(r["subject"], 0) + 1
    return dict(sorted(out.items()))

