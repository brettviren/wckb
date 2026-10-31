"""``kb context``: one call that gives an LLM what the knowledge base knows about something.

Topics come first (they are the synthesis), then current items with their
summaries, then superseded ones that explain history.  Output stops at a
character budget so it can be pasted into a context window as is.
"""

import json

from . import graph, query, content
from .errors import KBError
from .ids import is_id, parse_ref
from .items import Store, Item


def _runs(rec: Item) -> str:
    rr = rec.data.get("valid", {}).get("runs")
    if not rr:
        return ""
    return "runs " + ",".join(str(a) if a == b else f"{a}-{b}" for a, b in rr)


def line(kb: Store, rec: Item) -> str:
    d = rec.data
    bits = [rec.kind] + [x for x in (d.get("status"), d.get("doctype"), _runs(rec)) if x]
    s = f"- **{rec.id}** [{', '.join(bits)}] {d.get('title', '')}: {d.get('summary', '')}"
    if not kb.is_current(rec.id):
        s += f" (superseded by {', '.join(kb.superseded_by[rec.id])})"
    return s


def _content(kb: Store, rec: Item) -> str:
    for r in rec.renditions:
        if r["role"] == "content":
            t = rec.text(r["name"])
            if t is None:
                p = content.resolve(kb, rec, r, fetch_urls=False)
                t = p.read_text(errors="replace") if p else None
            if t:
                return t.strip()
    return ""


class _Out:
    def __init__(self, budget):
        self.parts, self.used, self.budget, self.full = [], 0, budget, False

    def add(self, text):
        if self.full:
            return False
        if self.used + len(text) > self.budget:
            room = self.budget - self.used
            if room > 200:
                self.parts.append(text[:room] + "\n…[truncated]")
            self.parts.append(f"\n…[budget of {self.budget} characters reached; narrow the query or raise --budget]")
            self.full = True
            return False
        self.parts.append(text)
        self.used += len(text)
        return True

    def __str__(self):
        return "\n".join(self.parts) + "\n"


def context(kb: Store, target: str, runs=None, budget: int = 12000, limit: int = 40) -> str:
    out = _Out(budget)
    if is_id(target.split("#")[0]):
        return _item_context(kb, kb.get(target), out)

    rows = query.ls(kb, subject=target, runs=runs, include_old=True, limit=limit * 2)
    how = f"subject {target}"
    if not rows:
        hits = query.fts(kb, target, limit, include_old=True)
        rows = [{"id": h["id"]} for h in hits]
        how = f"text search {target!r}"
    if not rows:
        return f"# KB context: {target}\n\nNothing found by subject or text. Try kb search -s for meaning.\n"
    recs = [kb.get(r["id"]) for r in rows]
    out.add(f"# KB context: {target}\n\nFound by {how}" + (f", runs {runs[0]}-{runs[1]}" if runs else "") + ".")
    topics = [r for r in recs if r.kind == "topic" and kb.is_current(r.id)]
    current = [r for r in recs if r.kind != "topic" and kb.is_current(r.id)]
    old = [r for r in recs if not kb.is_current(r.id)]
    if topics:
        out.add("\n## Topics (synthesis; read these first)")
        for t in topics:
            stale = graph.topic_staleness(kb, t)
            out.add(f"\n### {t.id}: {t.data['title']}" + (" (STALE: inputs changed)" if stale else ""))
            out.add(_content(kb, t))
    if current:
        out.add("\n## Current items")
        for r in current[:limit]:
            out.add(line(kb, r))
    if old:
        out.add("\n## Superseded or retracted (history only; do not cite as current)")
        for r in old[:limit]:
            out.add(line(kb, r))
    out.add("\nMore: kb show ID; kb context ID; kb rendition path ID#RENDITION for content files.")
    return str(out)


def _item_context(kb: Store, rec: Item, out: _Out) -> str:
    out.add(f"# KB context: {rec.id}\n")
    out.add(line(kb, rec))
    d = rec.data
    for k in ("asserted_by", "creators", "date", "identifiers", "evidence", "system", "parameters"):
        if k in d:
            v = d[k]
            out.add(f"- {k}: {v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}")
    out.add("- renditions: " + ", ".join(f"{r['name']} ({r['role']}, {r['media_type']})" for r in rec.renditions))
    text = _content(kb, rec)
    if text:
        out.add(f"\n## Content\n\n{text}")
    for title, ids in (("Inputs", [parse_ref(r)[0] for r in rec.inputs()]),
                       ("Used by", graph.rdeps(kb, rec.id)),
                       ("Supersedes", [parse_ref(r)[0] for r in d.get("supersedes", []) + d.get("retracts", [])]),
                       ("Superseded by", kb.superseded_by.get(rec.id, []))):
        if ids:
            out.add(f"\n## {title}")
            for i in ids:
                if i in kb.items:
                    out.add(line(kb, kb.items[i]))
                else:
                    out.add(f"- {i} (missing)")
    return str(out)


def parse_run_window(spec: str | None):
    if not spec:
        return None
    from .authoring import parse_runs
    rr = parse_runs([spec])
    if len(rr) != 1:
        raise KBError("--runs takes one range here")
    return rr[0]
