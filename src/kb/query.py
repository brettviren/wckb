"""Querying a store: faceted listing and full-text search.

The query tables (subjects, runs, refs, identifiers, renditions, fts) live in
the store's database and are kept in step on every write (``Store.save``);
``kb store reindex`` (``Store.reindex``) rebuilds them only after a repair.
"""

import sqlite3

from .errors import KBError
from .items import Store



def ls(kb: Store, kind=None, subject=None, runs=None, status=None, include_old=False,
       since=None, limit=None, tags=()) -> list[dict]:
    """Items by facets, newest first; TAGS: items with every one of these tags."""
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
    for tag in tags or ():
        where.append("EXISTS (SELECT 1 FROM json_each(i.json, '$.tags') t WHERE t.value = ?)")
        args.append(tag)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY i.created_at DESC"
    rows = [dict(r) for r in kb.conn.execute(q, args)]
    for r in rows:
        r["current"] = int(kb.is_current(r["id"]))
    if not include_old:
        rows = [r for r in rows if r["current"]]
    return rows[:limit] if limit else rows


#: Words left out when a search falls back to any of the words (they would match everything).
STOP = set("""a an and are as at be but by can could did do does for from had has have how i if in into is it
its me my no not of on or our please should so than that the their them then there these they this to us
was we were what when where which who why will with would you your about also any just like make get use
some more need want know tell show give let new one two""".split())


def match_expression(text: str, any_word: bool = False) -> str:
    """What somebody typed, as an FTS5 query: every word quoted (punctuation is not syntax), all of them
    (ANDed), or with ANY_WORD any of them but common words (ORed); a trailing * keeps prefix search."""
    terms = []
    for w in text.split():
        star = w.endswith("*")
        w = w.rstrip("*").replace('"', '""')
        if w and not (any_word and w.lower().strip("?.,!:;") in STOP):
            terms.append(f'"{w}"' + ("*" if star else ""))
    return (" OR " if any_word else " ").join(terms)


def fts(kb: Store, text: str, limit: int = 20, include_old: bool = False) -> list[dict]:
    """Items matching TEXT, best first, each with the rendition (and chunk) of its best row: '' for the
    item's head (title, summary, subjects), else the text rendition the words are in.  Items with all the
    words come first; when they are fewer than LIMIT, items with some of them follow (``match``: all|some),
    so a question typed as a sentence still finds something."""
    rows = _fts(kb, match_expression(text), limit, include_old)
    for r in rows:
        r["match"] = "all"
    if len(rows) < limit and len(text.split()) > 1:
        seen = {r["id"] for r in rows}
        for r in _fts(kb, match_expression(text, any_word=True), limit, include_old):
            if r["id"] not in seen:
                rows.append(r | {"match": "some"})
    return rows[:limit]


def _fts(kb: Store, q: str, limit: int, include_old: bool) -> list[dict]:
    if not q:
        return []
    # the rows scored first (MATERIALIZED: bm25 and snippet need the MATCH), then the best row per item
    sql = """WITH h AS MATERIALIZED (
               SELECT id, rendition, chunk, page, bm25(fts, 0, 0, 0, 0, 0, 5.0, 3.0, 2.0, 1.0) AS score,
                      -- the excerpt: of the text if the words are there, else of the summary
                      CASE WHEN instr(snippet(fts, 8, '[', ']', '…', 12), '[') > 0
                           THEN snippet(fts, 8, '[', ']', '…', 12)
                           ELSE snippet(fts, 6, '[', ']', '…', 12) END AS snip
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
    """Where a hit is: the rendition, and the one it was derived from (the original of extracted text),
    with their media types."""
    rec = kb.items.get(ident)
    if not rendition or rec is None or not rec.has_rendition(rendition):
        return {"rendition": rendition or None, "from": None}
    r = rec.rendition(rendition)
    out = {"rendition": rendition, "media_type": r.get("media_type"), "from": r.get("derived_from")}
    if out["from"] and rec.has_rendition(out["from"]):
        out["from_media_type"] = rec.rendition(out["from"]).get("media_type")
    return out


def tags(kb: Store) -> dict[str, int]:
    """The tags of current items, with how many items have each."""
    out: dict[str, int] = {}
    for r in kb.conn.execute("SELECT i.id, t.value FROM items i, json_each(i.json, '$.tags') t"):
        if kb.is_current(r[0]):
            out[r[1]] = out.get(r[1], 0) + 1
    return dict(sorted(out.items()))


def subjects(kb: Store) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in kb.conn.execute("SELECT subject, id FROM subjects"):
        if kb.is_current(r["id"]):
            out[r["subject"]] = out.get(r["subject"], 0) + 1
    return dict(sorted(out.items()))

