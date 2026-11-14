"""Web documents: a user guide, a design document or a manual published as many web pages.

``kb add URL --spider`` reads the page at URL and the pages it links to,
and theirs, as long as they are within the *scope*: the same scheme and
host, and a path under the scope's path (by default the directory of
URL, so ``https://h/phlex/`` takes ``https://h/phlex/guide/x.html`` but
not ``https://h/phlex-design/``).  Only web pages are taken (what a page
loads, styles and images, goes into its self-contained copy instead), and
a page served twice under two URLs (``guide/`` and ``guide/index.html``)
is kept once.

It makes:

- one document for the web document as a whole, identified by the scope's
  URL, with the metadata rendition ``pages`` (JSON: each page's path, URL,
  title and item);
- one document per page, ``part-of`` the whole, with its original, its
  text, its self-contained copy (``page``) and its sections (``sections``,
  kb.sections), so that a reference can cite the whole (``doc-W``), a page
  (``doc-P``) or a section of it (``doc-P@anchor``).

With a target directory, the pages' self-contained copies are laid out
there as the site is (``guide/x.html``), and their links to each other
are made relative, so the copy can be browsed offline; in a DataLad
dataset each is saved (``datalad save``).  Links out of scope stay as
they are.  Standard library only (and monolith for the copies).
"""

import copy
import fnmatch
import hashlib
import json
import logging
import posixpath
import re
import time
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from . import content, schema
from .errors import KBError
from .items import Item, Store, now
from .logs import tell

log = logging.getLogger(__name__)

PAGES = "pages"
#: Fields of the whole that each page has too (title, summary, identifiers and links are each page's own).
INHERITED = ("doctype", "creators", "version", "date", "dates", "subject", "tags", "access", "license",
             "valid", "venue", "status")
#: Paths whose name says they are not web pages: not read at all.
NOT_PAGES = {".css", ".js", ".mjs", ".map", ".json", ".xml", ".txt", ".rst", ".md", ".png", ".jpg", ".jpeg",
             ".gif", ".svg", ".ico", ".webp", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".pdf", ".zip", ".gz",
             ".tgz", ".tar", ".whl", ".ipynb", ".py", ".mp4", ".webm", ".mp3"}

#: Elements and attributes that load what a page needs (``link`` only for the rels in REQUISITE_RELS).
REQUISITE_ATTRS = {"link": ("href",), "script": ("src",), "img": ("src",), "source": ("src",), "video": ("src", "poster"),
                   "audio": ("src",), "track": ("src",), "embed": ("src",), "object": ("data",), "input": ("src",),
                   "iframe": ("src",)}
REQUISITE_RELS = {"stylesheet", "icon", "preload", "modulepreload", "apple-touch-icon", "mask-icon", "manifest"}
_CSS_URL = re.compile(r"""url\(\s*(["']?)([^"')]+?)\1\s*\)|@import\s+(["'])(.+?)\3""", re.I)


def css_refs(css: str) -> list[str]:
    """What a style sheet (or a style attribute) loads: its url(...) and @import "...", data: URLs left out."""
    out = [m[2] or m[4] for m in _CSS_URL.finditer(css)]
    return [u.strip() for u in out if u and not u.strip().startswith("data:")]


def normalize(url: str) -> str:
    """URL without its fragment, scheme and host in lower case, without a default port, path at least /."""
    p = urlsplit(url.strip())
    host = (p.hostname or "").lower()
    if p.port and not ((p.scheme == "http" and p.port == 80) or (p.scheme == "https" and p.port == 443)):
        host += f":{p.port}"
    return urlunsplit((p.scheme.lower(), host, p.path or "/", p.query, ""))


@dataclass
class Scope:
    """Where a crawl may go: URLs under ROOT (scheme, host, and a path prefix ending in /)."""
    root: str

    @classmethod
    def of(cls, start: str, scope: str | None = None) -> "Scope":
        """The scope given (a URL), else that of START: a URL's path is a directory when it ends in /
        or its last part has no suffix (``/phlex``), else the directory of that file (``/phlex/x.html``)."""
        base = normalize(scope or start)
        p = urlsplit(base)
        if p.scheme not in ("http", "https"):
            raise KBError(f"{scope or start}: spidering needs an http(s) URL")
        path = p.path
        if not path.endswith("/"):
            path = posixpath.dirname(path) + "/" if posixpath.splitext(path)[1] else path + "/"
        s = cls(urlunsplit((p.scheme, p.netloc, path, "", "")))
        if not s.contains(normalize(start)):
            raise KBError(f"{start} is not within the scope {s.root}")
        return s

    def contains(self, url: str) -> bool:
        """Is URL (normalized) within the scope?  Paths are compared whole: /phlex/ does not hold /phlex-design/."""
        return url.startswith(self.root) or url + "/" == self.root

    def relpath(self, url: str) -> str:
        """Where the page at URL goes in a tree laid out as the site is: ``dir/`` -> ``dir/index.html``,
        ``x`` (no suffix) -> ``x.html``; a query adds a hash of it to the name."""
        p = urlsplit(url)
        rel = p.path[len(urlsplit(self.root).path):] if url.startswith(self.root) else ""
        rel = rel.lstrip("/")
        if rel == "" or rel.endswith("/"):
            rel += "index.html"
        stem, ext = posixpath.splitext(rel)
        if ext.lower() not in (".html", ".htm", ".xhtml"):
            stem, ext = rel, ".html"
        if p.query:
            stem += "-" + hashlib.sha256(p.query.encode()).hexdigest()[:8]
        return stem + ext


def _filepath(scope: "Scope", url: str) -> str:
    """Where the file at URL (not a page) goes in a tree laid out as the site is: its path in the scope,
    without the query (``_static/x.css?v=1`` -> ``_static/x.css``: what a browser looks for in a local copy)."""
    rel = urlsplit(url).path[len(urlsplit(scope.root).path):].lstrip("/")
    return rel + "index.html" if rel == "" or rel.endswith("/") else rel


class _PageParser(HTMLParser):
    """Links, base, title, first h1, description and first paragraph of a page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.base = None
        self.title = ""
        self.h1 = ""
        self.description = ""
        self.requisites: list[str] = []         # what the page loads: styles, scripts, images, media
        self._in = None
        self._depth_skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for attr in REQUISITE_ATTRS.get(tag, ()):
            if a.get(attr) and (tag != "link" or set((a.get("rel") or "").lower().split()) & REQUISITE_RELS):
                self.requisites.append(a[attr])
        for attr in ("srcset",) if tag in ("img", "source") else ():
            self.requisites += [c.split()[0] for c in (a.get(attr) or "").split(",") if c.split()]
        if a.get("style"):
            self.requisites += css_refs(a["style"])
        if tag == "style":
            self._in = "style"
        elif tag in ("a", "area") and a.get("href"):
            self.links.append(a["href"])
        elif tag == "base" and a.get("href") and self.base is None:
            self.base = a["href"]
        elif tag == "meta" and (a.get("name") or "").lower() == "description" and a.get("content"):
            self.description = self.description or a["content"].strip()
        elif tag == "title" and not self.title:
            self._in = "title"
        elif tag == "h1" and not self.h1:
            self._in = "h1"
        elif self._in == "h1" and "headerlink" in (a.get("class") or ""):
            self._depth_skip += 1

    def handle_endtag(self, tag):
        if tag == "a" and self._depth_skip:
            self._depth_skip -= 1
        elif tag == self._in:
            self._in = None

    def handle_data(self, data):
        if self._in == "style":
            self.requisites += css_refs(data)
        elif self._in == "title":
            self.title += data
        elif self._in == "h1" and not self._depth_skip:
            self.h1 += data


def _clean(s: str) -> str:
    return " ".join(s.replace("¶", " ").split())


def _first_paragraph(page: str, limit: int = 400) -> str:
    """The first paragraph of the page's main content with some words in it, clipped to LIMIT."""
    from .readability import _Tree, main_content
    html, _ = main_content(page)
    t = _Tree()
    t.feed(html)
    t.close()
    for p in t.root.find(lambda n: n.tag == "p"):
        def text(n):
            return "".join(c if isinstance(c, str) else text(c) for c in n.children)
        words = _clean(text(p))
        if len(words.split()) >= 5:
            return words if len(words) <= limit else words[:limit - 1].rsplit(" ", 1)[0] + "…"
    return ""


@dataclass
class Page:
    url: str                            # normalized, as first reached
    rel: str                            # its path in a tree laid out as the site is
    fetched: content.Fetched
    title: str = ""
    summary: str = ""
    sections: list = field(default_factory=list)
    aliases: list = field(default_factory=list)     # other URLs that gave the same page


@dataclass
class File:
    """What a page loads (a style sheet, a script, an image), read to be kept beside the pages."""
    url: str
    rel: str
    fetched: content.Fetched
    media_type: str


@dataclass
class Crawl:
    scope: Scope
    start: str
    pages: list[Page] = field(default_factory=list)
    #: URL -> why it was not taken (excluded, not a web page, a fetch failure, the page limit).
    skipped: dict[str, str] = field(default_factory=dict)
    #: What the pages load, when asked for (crawl requisites=True).
    files: list[File] = field(default_factory=list)
    #: What the pages load that was not read: URL -> why (out of scope: it stays at its URL).
    skipped_files: dict[str, str] = field(default_factory=dict)

    def by_url(self) -> dict[str, Page]:
        out = {}
        for p in self.pages:
            out[p.url] = p
            for a in p.aliases:
                out[a] = p
        return out


def crawl(kb: Store, start: str, scope: Scope | None = None, exclude=(), max_pages: int = 200,
          delay: float = 0.2, requisites: bool = False) -> Crawl:
    """Read the web pages within SCOPE that START leads to, breadth first.  EXCLUDE: shell patterns of
    paths in the scope (``search.html``, ``_sources/*``) not to read.  At most MAX_PAGES pages; DELAY
    seconds between requests, to be gentle with the server.  REQUISITES: also read what the pages
    load (styles, scripts, images, fonts, and what style sheets load) within the scope."""
    from .content import FetchError, MARKUP, sniff
    scope = scope or Scope.of(start)
    start = normalize(start)
    c = Crawl(scope, start)
    queue, seen, by_sha, wanted = [start], {start}, {}, {}
    root_path = urlsplit(scope.root).path
    first = True
    while queue:
        url = queue.pop(0)
        rel_raw = urlsplit(url).path[len(root_path):]
        if any(fnmatch.fnmatch(rel_raw, pat) or fnmatch.fnmatch(scope.relpath(url), pat) for pat in exclude):
            c.skipped[url] = "excluded"
            continue
        if posixpath.splitext(urlsplit(url).path)[1].lower() in NOT_PAGES:
            c.skipped[url] = "not a web page (by its name)"
            continue
        if len(c.pages) >= max_pages:
            c.skipped[url] = f"over the page limit ({max_pages})"
            continue
        if not first and delay:
            time.sleep(delay)
        first = False
        try:
            f = content.fetch_info(kb, url)
        except FetchError as exc:
            c.skipped[url] = str(exc)
            tell(log, logging.WARNING, f"not read: {exc}")
            continue
        final = normalize(f.url or url)
        if not scope.contains(final):
            c.skipped[url] = f"redirected out of scope, to {final}"
            continue
        mtype = sniff(f.path, posixpath.basename(urlsplit(final).path) or "index.html", f.media_type)
        if mtype not in MARKUP:
            c.skipped[url] = f"not a web page ({mtype})"
            continue
        if f.sha256 in by_sha:                       # the same page under another URL
            by_sha[f.sha256].aliases.append(url)
            continue
        page = f.path.read_text(encoding="utf-8", errors="replace")
        p = _PageParser()
        p.feed(page)
        p.close()
        from .sections import find
        title = _clean(p.h1) or _clean(p.title) or scope.relpath(final)
        got = Page(url, scope.relpath(final), f, title, _clean(p.description) or _first_paragraph(page),
                   find(page))
        if final != url:
            got.aliases.append(final)
            seen.add(final)
        c.pages.append(got)
        by_sha[f.sha256] = got
        base = urljoin(final, p.base) if p.base else final
        for ref in p.requisites if requisites else ():
            ref = ref.strip()
            if ref and not ref.startswith(("data:", "javascript:", "#", "about:", "blob:")):
                wanted.setdefault(normalize(urljoin(base, ref)), None)
        for href in p.links:
            href = href.strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
                continue
            nxt = normalize(urljoin(base, href))
            if nxt not in seen and scope.contains(nxt):
                seen.add(nxt)
                queue.append(nxt)
    if requisites:
        _read_files(kb, c, list(wanted), exclude, delay)
    return c


def _read_files(kb: Store, c: Crawl, urls: list[str], exclude, delay: float) -> None:
    """Read what the pages load (URLS), and what style sheets among them load, into C.files."""
    from .content import FetchError, sniff
    pages = c.by_url()
    queue, seen, at = list(urls), set(urls), {}
    root_path = urlsplit(c.scope.root).path
    while queue:
        url = queue.pop(0)
        if url in pages:
            continue
        if not c.scope.contains(url):
            c.skipped_files[url] = "out of scope: left at its URL"
            continue
        rel = _filepath(c.scope, url)
        if any(fnmatch.fnmatch(urlsplit(url).path[len(root_path):], pat) or fnmatch.fnmatch(rel, pat)
               for pat in exclude):
            c.skipped_files[url] = "excluded"
            continue
        if delay:
            time.sleep(delay)
        try:
            f = content.fetch_info(kb, url)
        except FetchError as exc:
            c.skipped_files[url] = str(exc)
            continue
        if rel in at:
            if at[rel].fetched.sha256 != f.sha256:
                c.skipped_files[url] = f"its path {rel} is taken by {at[rel].url}, which differs"
            continue
        mtype = sniff(f.path, posixpath.basename(rel), f.media_type)
        got = File(url, rel, f, mtype)
        c.files.append(got)
        at[rel] = got
        if mtype == "text/css" or rel.endswith(".css"):
            css = f.path.read_text(encoding="utf-8", errors="replace")
            for ref in css_refs(css):
                nxt = normalize(urljoin(f.url or url, ref))
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)


_HREF = re.compile(r"""(<a\b[^>]*?\shref\s*=\s*)(["'])(.*?)\2""", re.I | re.S)


def local_links(page: Page, pages: dict[str, Page]):
    """A post-processor for PAGE's self-contained copy: links to the other pages read (PAGES, by URL)
    made relative to where they are laid out, so the copies link to each other."""
    here = posixpath.dirname(page.rel)

    def post(path: Path) -> dict:
        n = 0

        def swap(m):
            nonlocal n
            href = unescape(m[3])
            if href.startswith("#") or "://" not in href:
                return m[0]
            target, _, frag = href.partition("#")
            to = pages.get(normalize(target))
            if to is None:
                return m[0]
            n += 1
            rel = posixpath.relpath(to.rel, here or ".")
            if to is page:
                rel = posixpath.basename(page.rel)
            return f"{m[1]}{m[2]}{rel}{'#' + frag if frag else ''}{m[2]}"

        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        path.write_text(_HREF.sub(swap, text), encoding="utf-8", errors="surrogateescape")
        return {"links": "local", "links_made_local": n}
    return post


@dataclass
class Added:
    whole: Item
    pages: list[Item]
    crawl: Crawl
    failed: list[str]


def _existing(kb: Store, root: str) -> list[str]:
    return [r.id for r in kb.items.values() if r.kind == "document" and kb.is_current(r.id)
            and r.data.get("identifiers", {}).get("url") == root and r.has_rendition(PAGES)]


def _put(src: Path, dest: Path) -> bool:
    """Copy SRC to DEST (replacing what is there, a locked annexed file too); False if DEST had these bytes."""
    import shutil
    if dest.is_symlink() or dest.exists():
        if dest.is_file() and content.sha256_file(dest)[0] == content.sha256_file(src)[0]:
            return False
        dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return True


def mirror(kb: Store, c: Crawl, target: Path, message: str) -> str:
    """Lay out the pages (as served) and what they load in TARGET as the site is, then take them all into
    the source holding TARGET at once (one datalad save, or one git commit).  What was done, for people."""
    from . import sources
    files = [(p.fetched.path, p.rel) for p in c.pages] + [(f.fetched.path, f.rel) for f in c.files]
    changed = sum(_put(src, target / rel) for src, rel in files)
    held = sources.containing(kb, target / files[0][1])
    if held is None:
        return f"{len(files)} file(s) in {target} ({changed} new or changed); not in a source: nothing saved"
    rec, root = held
    rels = [(target.resolve() / rel).relative_to(root).as_posix() for _, rel in files]
    sources.TYPES[rec.data["source_type"]].put_many(kb, rec, root, rels, message)
    return f"{len(files)} file(s) in {target} ({changed} new or changed), saved in {rec.id} ({root})"


def add(kb: Store, start: str, fields: dict, *, scope: str | None = None, exclude=(), max_pages: int = 200,
        delay: float = 0.2, target: str | Path | None = None, page: bool = True, extract: bool = True,
        mirror_files: bool = False, mode: str = "auto", cas_name: str | None = None, force: bool = False,
        via: str | None = None) -> Added:
    """Add the web document at START: a document for the whole, one per page (see the module).  FIELDS
    are the whole's; pages inherit those in INHERITED.  TARGET: a directory where the pages are laid
    out as the site is: their self-contained copies, or with MIRROR_FILES the pages as served and each
    file they load (as wget -r -p does), saved at once; each page's original is then its file there.
    With FIELDS["supersedes"] (the whole of an earlier version), each page supersedes the earlier
    version's page of the same URL."""
    from . import authoring, transforms
    for need, opt in (("doctype", "--set doctype=manual (or another)"), ("creators", "--creators")):
        if not fields.get(need):
            raise KBError(f"a web document needs {need}: give {opt}; its pages share it")
    if mirror_files and target is None:
        raise KBError("a mirror needs --target: the directory the files go in")
    sc = Scope.of(start, scope)
    trial = authoring.deep_merge(authoring._skeleton(kb, "document"), authoring.deep_merge(
        {"title": "-", "summary": "-", "identifiers": {"url": sc.root}, "dates": {"accessed": now()[:10]}}, fields))
    errors = schema.validate(trial)
    if errors:                                  # before anything is read
        raise KBError("the web document's fields:\n  " + "\n  ".join(errors))
    replaced = set(fields.get("supersedes", []))
    earlier = {}
    for ref in replaced:
        earlier.update({q["url"]: q["item"] for q in pages_of(kb.get(ref)) if kb.is_current(q["item"])})
    there = [i for i in _existing(kb, sc.root) if i not in replaced]
    if not force and there:
        raise KBError(f"{sc.root} is in this store already as {', '.join(there)}; use --force "
                      "to add it again (or --supersedes ID for a new version)")
    if target is not None:
        target = Path(target).expanduser()
        target.mkdir(parents=True, exist_ok=True)
    c = crawl(kb, start, sc, exclude, max_pages, delay, requisites=mirror_files)
    if not c.pages:
        raise KBError(f"no web page read from {start}: " + "; ".join(f"{u}: {w}" for u, w in c.skipped.items()))
    tell(log, logging.INFO, f"read {len(c.pages)} page(s) in {sc.root}"
         + (f" and {len(c.files)} file(s) they load" if mirror_files else "")
         + (f"; {len(c.skipped)} link(s) not taken" if c.skipped else ""))
    if mirror_files:
        tell(log, logging.INFO, mirror(kb, c, target, f"Mirror {sc.root} (kb add --spider --mirror)"))
    today = now()[:10]
    first = c.pages[0]
    whole_fields = authoring.deep_merge({
        "title": first.title, "summary": first.summary or first.title,
        "identifiers": {"url": sc.root}, "dates": {"accessed": today}}, fields)
    whole = authoring.new(kb, "document", whole_fields, via=via)
    inherited = {k: copy.deepcopy(whole.data[k]) for k in INHERITED if k in whole.data}
    by_url = c.by_url()
    pt = None
    if page and not mirror_files:             # a mirror is browsable as it is
        try:
            pt = transforms.choose(kb, "text/html", purpose="page")
        except KBError as exc:
            tell(log, logging.WARNING, f"no self-contained pages: {exc}")
    made, failed = [], []
    for p in c.pages:
        pf = authoring.deep_merge(inherited, {
            "title": p.title, "summary": p.summary or p.title,
            "identifiers": {"url": p.url}, "dates": {"accessed": today},
            "links": [{"rel": "part-of", "ref": whole.id}]})
        old = next((earlier[u] for u in [p.url, *p.aliases] if u in earlier), None)
        if old:
            pf["supersedes"] = [old]
        if p.sections:
            data = (json.dumps(p.sections, ensure_ascii=False, indent=1) + "\n").encode()
            locs, sha, size = content.place_bytes(kb, data, "sections.json")
            pf["renditions"] = [{"name": "sections", "role": "metadata", "media_type": "application/json",
                                 "sha256": sha, "size": size, "locations": locs}]
        try:
            if mirror_files:                  # the original is the file in the mirror, and at its URL
                rec = authoring.add(kb, "document", [str(target / p.rel)], pf, mode=mode, cas_name=cas_name,
                                    url=p.url, force=True, via=via, guess=False, prefetched={p.url: p.fetched})
            else:
                rec = authoring.add(kb, "document", [p.url], pf, mode=mode, cas_name=cas_name, force=True,
                                    via=via, guess=False, prefetched={p.url: p.fetched})
        except KBError as exc:
            failed.append(f"{p.url}: {exc}")
            continue
        if extract:
            _, why = transforms.extract_missing(kb, rec)
            failed += why
        if pt is not None:
            try:
                transforms.extract(kb, rec, "original", pt.name, "page", base=p.fetched.url or p.url,
                                   post=local_links(p, by_url) if target is not None else None,
                                   target=(target / p.rel) if target is not None else None)
            except KBError as exc:
                failed.append(f"{rec.id}#page ({p.rel}): {exc}")
        made.append((p, kb.get(rec.id)))
        tell(log, logging.INFO, f"{rec.id} {p.rel}: {p.title}")
    manifest = {"scope": sc.root, "start": c.start,
                "pages": [{"path": p.rel, "url": p.url, "title": p.title, "item": r.id,
                           **({"aliases": p.aliases} if p.aliases else {})} for p, r in made],
                "skipped": c.skipped}
    if mirror_files:
        manifest["layout"] = "mirror"
        manifest["files"] = [{"path": f.rel, "url": f.url, "media_type": f.media_type, "sha256": f.fetched.sha256,
                              "size": f.fetched.size} for f in c.files]
        manifest["files_skipped"] = c.skipped_files
    data = (json.dumps(manifest, ensure_ascii=False, indent=1) + "\n").encode()
    locs, sha, size = content.place_bytes(kb, data, "pages.json")
    w = copy.deepcopy(kb.get(whole.id).data)
    w["renditions"] = [r for r in w.get("renditions", []) if r["name"] != PAGES] + [
        {"name": PAGES, "role": "metadata", "media_type": "application/json", "sha256": sha, "size": size,
         "locations": locs}]
    whole = kb.save(Item(w, kb), "pages", {"pages": len(made), "scope": sc.root})
    return Added(whole, [r for _, r in made], c, failed)


def pages_of(rec: Item) -> list[dict]:
    """The pages a web document lists (its rendition ``pages``), or []."""
    if not rec.has_rendition(PAGES):
        return []
    return json.loads(rec.text(PAGES) or "{}").get("pages", [])
