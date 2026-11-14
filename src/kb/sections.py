"""Sections of a web page, so that a reference can cite one: ``doc-…@anchor``.

A page's sections are its headings that have an anchor: the heading's own
``id``, or that of the ``section`` (or ``div``, ``article``) it opens, as
Sphinx, MkDocs and most generators write them.  :func:`find` lists them
from the page as served; kb keeps the list as the metadata rendition
``sections`` (JSON: ``[{"id", "title", "level"}]``), made when a web page
is added.

:func:`text` gives one section's text: the lines of the item's extracted
text (markdown) from the section's heading up to the next heading of the
same or a higher level.  Headings are matched in order by their words, as
the markdown keeps no anchors.  Standard library only.
"""

import json
import re

from .errors import KBError
from .ids import ANCHOR
from .items import Item, Store
from .readability import Node, _Tree

NAME = "sections"
_HEADING = {f"h{i}": i for i in range(1, 7)}
_ANCHOR = re.compile(rf"^{ANCHOR}$")


def _text(node: Node) -> str:
    out = []
    for c in node.children:
        if isinstance(c, str):
            out.append(c)
        elif "headerlink" not in (c.attrs.get("class") or ""):     # Sphinx's ¶ after each heading
            out.append(_text(c))
    return "".join(out)


def _title(node: Node) -> str:
    return " ".join(_text(node).replace("¶", " ").split())


def find(page: str) -> list[dict]:
    """The sections of web page PAGE (HTML) that can be cited: [{"id", "title", "level"}], in page order."""
    t = _Tree()
    t.feed(page)
    t.close()
    out, seen = [], set()
    for node in t.root.find(lambda n: n.tag in _HEADING):
        anchor = node.attrs.get("id")
        parent = node.parent
        if not anchor and parent is not None and parent.tag in ("section", "div", "article"):
            first = next((c for c in parent.children if isinstance(c, Node)), None)
            if first is node:
                anchor = parent.attrs.get("id")
        title = _title(node)
        if anchor and title and _ANCHOR.match(anchor) and anchor not in seen:
            seen.add(anchor)
            out.append({"id": anchor, "title": title, "level": _HEADING[node.tag]})
    return out


def listed(rec: Item) -> list[dict] | None:
    """The sections REC lists (its rendition ``sections``), or None if it has none."""
    if not rec.has_rendition(NAME):
        return None
    t = rec.text(NAME)
    return json.loads(t) if t else None


def has(rec: Item, anchor: str) -> bool | None:
    """Does REC have section ANCHOR?  None when it lists no sections (nothing to say)."""
    got = listed(rec)
    return None if got is None else any(s["id"] == anchor for s in got)


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def _words(s: str) -> str:
    s = _MD_LINK.sub(r"\1", s).replace("¶", " ")
    return " ".join(re.sub(r"[`*_\\]", "", s).split()).casefold()


def text(kb: Store, rec: Item, anchor: str, name: str = "text") -> str:
    """The text of section ANCHOR of REC: from its heading in rendition NAME (extracted markdown) to the
    next heading of the same or a higher level."""
    got = listed(rec)
    if got is None:
        raise KBError(f"{rec.id} lists no sections (it has no rendition '{NAME}')")
    idx = next((i for i, s in enumerate(got) if s["id"] == anchor), None)
    if idx is None:
        raise KBError(f"{rec.id} has no section {anchor!r}; its sections: "
                      + ", ".join(s["id"] for s in got[:30]))
    body = rec.text(name)
    if body is None:
        raise KBError(f"{rec.id}#{name} is not text held in the store")
    lines = body.splitlines(keepends=True)
    heads = [(i, len(m[1]), _words(m[2])) for i, m in ((i, _MD_HEADING.match(l)) for i, l in enumerate(lines)) if m]
    # match the listed sections to the markdown headings in order, each to the next with its words
    at, start, level = 0, None, None
    for j, s in enumerate(got[:idx + 1]):
        want = _words(s["title"])
        k = next((k for k in range(at, len(heads)) if heads[k][2] == want), None)
        if k is None:
            if j == idx:
                raise KBError(f"section {anchor!r} ({s['title']!r}) is not found in {rec.id}#{name}")
            continue
        at = k + 1
        if j == idx:
            start, level = heads[k][0], heads[k][1]
    end = next((i for i, lv, _ in heads if i > start and lv <= level), len(lines))
    return "".join(lines[start:end])
