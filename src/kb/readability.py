"""The main content of a web page: what is worth searching, without menus, banners and footers.

A web page is mostly what surrounds its content: navigation, headers,
footers, sidebars, forms, scripts.  :func:`main_content` keeps the content:

1. the page is parsed into a tree (leniently, as browsers do);
2. what is never content is dropped: ``script``, ``style``, ``nav``,
   ``header``, ``footer``, ``aside``, ``form`` and the like, and elements whose
   ARIA role says navigation, banner, footer or search, or that are hidden;
3. the content is the ``article`` with the most text (a README, a post),
   else ``main`` (or role ``main``), else the whole ``body``; one that holds
   too little of the page's text is passed over for the next.

The result is HTML again, for pandoc.  Standard library only.
"""

import html
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
        "track", "wbr"}
NEVER = {"script", "style", "noscript", "template", "svg", "nav", "header", "footer", "aside", "form",
         "button", "iframe", "select", "dialog", "canvas"}
NOT_CONTENT_ROLES = {"navigation", "banner", "contentinfo", "complementary", "search", "menubar", "toolbar"}
#: A candidate must hold at least this share of the body's text (else it is a teaser or a card).
SHARE = 0.25


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict, parent: "Node | None"):
        self.tag, self.attrs, self.children, self.parent = tag, attrs, [], parent

    def text_len(self) -> int:
        return sum(len(c.strip()) if isinstance(c, str) else c.text_len() for c in self.children)

    def find(self, pred) -> list["Node"]:
        out = [self] if pred(self) else []
        for c in self.children:
            if isinstance(c, Node):
                out += c.find(pred)
        return out

    def html(self) -> str:
        attrs = "".join(f' {k}="{html.escape(v or "", quote=True)}"' for k, v in self.attrs.items()
                        if k in ("href", "src", "alt", "title", "colspan", "rowspan"))
        if self.tag in VOID:
            return f"<{self.tag}{attrs}>"
        inner = "".join(html.escape(c, quote=False) if isinstance(c, str) else c.html() for c in self.children)
        return f"<{self.tag}{attrs}>{inner}</{self.tag}>"


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("document", {}, None)
        self.cur = self.root
        self.skip = 0               # inside an element that is never content

    def handle_starttag(self, tag, attrs):
        a = {k: v for k, v in attrs}
        dropped = (tag in NEVER or (a.get("role") or "") in NOT_CONTENT_ROLES or "hidden" in a
                   or a.get("aria-hidden") == "true")
        if self.skip or dropped:
            if tag not in VOID:
                self.skip += 1
            return
        node = Node(tag, a, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and not self.skip:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.skip:
            if tag not in VOID:
                self.skip -= 1
            return
        node = self.cur
        while node is not None and node.tag != tag:     # close what was left open, as browsers do
            node = node.parent
        if node is not None and node.parent is not None:
            self.cur = node.parent

    def handle_data(self, data):
        if not self.skip and data:
            self.cur.children.append(data)


def main_content(page: str) -> tuple[str, str]:
    """(the page's main content as HTML, which element it is: article, main or body)."""
    t = _Tree()
    t.feed(page)
    t.close()
    body = next(iter(t.root.find(lambda n: n.tag == "body")), t.root)
    total = body.text_len() or 1
    for how, pred in (("article", lambda n: n.tag == "article"),
                      ("main", lambda n: n.tag == "main" or n.attrs.get("role") == "main")):
        found = sorted(body.find(pred), key=lambda n: n.text_len(), reverse=True)
        if found and found[0].text_len() >= SHARE * total:
            return found[0].html(), how
    return body.html(), "body"
