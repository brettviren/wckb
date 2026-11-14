"""Web documents read page by page (kb add URL --spider), and references to sections (ID@anchor)."""

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from click.testing import CliRunner

from kb import authoring, sections, spider
from kb.cli import main
from kb.errors import KBError
from kb.ids import parse_anchor, parse_ref
from kb.items import Store

from kb import datasets
needs_datalad = pytest.mark.skipif(datasets.available() is not None, reason=f"DataLad: {datasets.available()}")
needs_monolith = pytest.mark.skipif(shutil.which("monolith") is None, reason="monolith not installed")

INDEX = b"""<html><head><title>Home &#8212; Site 1.0 documentation</title>
<link rel="stylesheet" href="style.css?v=3"><link rel="next" href="b.html"><script src="_static/app.js"></script>
<script src="https://cdn.invalid/lib.js"></script></head><body><img src="_static/logo.png" srcset="_static/logo2.png 2x">
<nav><a href="index.html">Home</a></nav>
<h1>Welcome to Site<a class="headerlink" href="#welcome">\xc2\xb6</a></h1>
<p>Site is a framework for doing many things with data in a pipeline.</p>
<a href="guide/a.html">A</a> <a href="guide/">Guide</a> <a href="b.html#more">B</a>
<a href="../site-other/x.html">sibling</a> <a href="/site-other/">sibling dir</a>
<a href="_sources/index.rst.txt">source</a> <a href="search.html">search</a>
<a href="https://external.invalid/">external</a> <a href="mailto:a@b.c">mail</a>
</body></html>"""

GUIDE = b"""<html><head><title>Guide</title></head><body><h1>Guide</h1>
<p>The guide explains each part of the site in turn.</p><a href="../index.html">up</a> <a href="a.html">A</a>
</body></html>"""

A = b"""<html><head><title>A page</title><meta name="description" content="All about A."></head><body>
<main><section id="a-page"><h1>A page<a class="headerlink" href="#a-page">\xc2\xb6</a></h1>
<p>Intro to A with enough words here.</p>
<section id="details"><h2>Details<a class="headerlink" href="#details">\xc2\xb6</a></h2>
<p>The details of A are these.</p>
<section id="fine-print"><h3>Fine print</h3><p>Very fine.</p></section>
</section>
<section id="end"><h2>End</h2><p>The end of A.</p></section>
</section>
<a href="../b.html">B</a> <a href="#details">here</a> <a href="https://external.invalid/x">out</a></main>
</body></html>"""

B = b"""<html><head><title>B</title></head><body><h1 id="more">More about B</h1><p>B has more words in it here.</p>
</body></html>"""

FILES = {
    "/site/": (INDEX, "text/html"), "/site/index.html": (INDEX, "text/html"),
    "/site/guide/": (GUIDE, "text/html"), "/site/guide/a.html": (A, "text/html"),
    "/site/b.html": (B, "text/html"),
    "/site/style.css": (b"@import 'more.css'; h1{color:red;background:url(_static/bg.png)}", "text/css"),
    "/site/more.css": (b"p{background:url('../site-other/x.png')}", "text/css"),
    "/site/_static/app.js": (b"var x=1;", "text/javascript"), "/site/_static/logo.png": (b"\x89PNG1", "image/png"),
    "/site/_static/logo2.png": (b"\x89PNG2", "image/png"), "/site/_static/bg.png": (b"\x89PNG3", "image/png"),
    "/site/_sources/index.rst.txt": (b"Welcome", "text/plain"),
    "/site/search.html": (b"<html><body>search</body></html>", "text/html"),
    "/site-other/x.html": (b"<html><body>other</body></html>", "text/html"),
    "/site-other/": (b"<html><body>other</body></html>", "text/html"),
}


@pytest.fixture
def site():
    asked = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            asked.append(self.path)
            if self.path == "/site":
                self.send_response(301)
                self.send_header("Location", "/site/")
                self.end_headers()
                return
            body, kind = FILES.get(self.path.split("?")[0], (b"", None))
            self.send_response(200 if kind else 404)
            self.send_header("Content-Type", kind or "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", asked
    finally:
        server.shutdown()


WHOLE = {"doctype": "manual", "creators": ["The Site Team"], "version": "1.0", "subject": ["software/site"]}


def test_scope():
    s = spider.Scope.of("https://h.org/phlex/")
    assert s.root == "https://h.org/phlex/"
    assert s.contains("https://h.org/phlex/guide/x.html")
    assert not s.contains("https://h.org/phlex-design/")         # a sibling with the same prefix
    assert not s.contains("https://h.org/")
    assert not s.contains("http://h.org/phlex/x.html") and not s.contains("https://other.org/phlex/x.html")
    assert spider.Scope.of("https://h.org/phlex").root == "https://h.org/phlex/"
    assert spider.Scope.of("https://H.org:443/phlex/index.html#x").root == "https://h.org/phlex/"
    assert spider.Scope.of("https://h.org/phlex/guide/x.html", "https://h.org/phlex/").root == "https://h.org/phlex/"
    with pytest.raises(KBError, match="not within"):
        spider.Scope.of("https://h.org/other/x.html", "https://h.org/phlex/")
    assert s.relpath("https://h.org/phlex/") == "index.html"
    assert s.relpath("https://h.org/phlex/guide/") == "guide/index.html"
    assert s.relpath("https://h.org/phlex/guide/x.html") == "guide/x.html"
    assert s.relpath("https://h.org/phlex/api/thing") == "api/thing.html"


def test_sections_found_and_cut():
    found = sections.find(A.decode())
    assert [(s["id"], s["title"], s["level"]) for s in found] == [
        ("a-page", "A page", 1), ("details", "Details", 2), ("fine-print", "Fine print", 3), ("end", "End", 2)]
    assert sections.find(B.decode()) == [{"id": "more", "title": "More about B", "level": 1}]


def test_anchor_refs():
    assert parse_anchor("doc-3vq8r2m1kd@limitations") == ("doc-3vq8r2m1kd", None, "limitations")
    assert parse_anchor("doc-3vq8r2m1kd#text@a.b:c") == ("doc-3vq8r2m1kd", "text", "a.b:c")
    assert parse_ref("doc-3vq8r2m1kd#page@x") == ("doc-3vq8r2m1kd", "page")
    with pytest.raises(KBError):
        parse_anchor("doc-3vq8r2m1kd@")


def test_crawl_stays_in_scope(kb, site):
    base, asked = site
    c = spider.crawl(kb, base + "/site", exclude=("search.html",), delay=0)
    assert [p.rel for p in c.pages] == ["index.html", "guide/a.html", "guide/index.html", "b.html"]
    assert not any(a.startswith("/site-other") for a in asked)          # never read out of scope
    assert "/site/search.html" not in asked and "/site/_sources/index.rst.txt" not in asked
    assert c.skipped[base + "/site/search.html"] == "excluded"
    home = c.pages[0]
    assert home.title == "Welcome to Site" and home.summary.startswith("Site is a framework")
    assert base + "/site/index.html" in home.aliases                   # the same page twice: kept once
    assert c.pages[1].summary == "All about A."


def test_spider_add(kb, site, tmp_path):
    base, _ = site
    got = spider.add(kb, base + "/site/", {**WHOLE, "title": "Site documentation"}, exclude=("search.html",),
                     delay=0, page=False)
    assert got.failed == []
    whole = got.whole
    assert whole.data["identifiers"]["url"] == base + "/site/" and whole.data["title"] == "Site documentation"
    listed = spider.pages_of(whole)
    assert [p["path"] for p in listed] == ["index.html", "guide/a.html", "guide/index.html", "b.html"]
    for p, rec in zip(listed, got.pages):
        assert p["item"] == rec.id
        assert rec.data["links"] == [{"rel": "part-of", "ref": whole.id}]
        assert rec.data["version"] == "1.0" and rec.data["creators"] == ["The Site Team"]
        assert rec.data["subject"] == ["software/site"] and rec.data["doctype"] == "manual"
    a = got.pages[1]
    assert a.data["title"] == "A page" and a.has_rendition("text") and a.has_rendition("sections")
    assert "# A page\n" in a.text("text") and "¶" not in a.text("text")      # no header links in the text
    # a section's text: its heading to the next heading of the same level, its subsections included
    t = sections.text(kb, a, "details")
    assert "details of A" in t and "Very fine" in t and "end of A" not in t
    assert "end of A" in sections.text(kb, a, "end")
    with pytest.raises(KBError, match="no section"):
        sections.text(kb, a, "nope")
    # references to the whole, a page and a section; an unknown section is refused
    obs = authoring.new(kb, "observation", {
        "title": "A has details", "summary": "s", "asserted_by": [{"who": "claude"}],
        "evidence": [f"{a.id}@details", whole.id]}, "A has details.")
    assert kb.exists(f"{a.id}@details") and not kb.exists(f"{a.id}@nope")
    with pytest.raises(KBError, match="no item"):
        authoring.new(kb, "observation", {"title": "x", "summary": "s", "asserted_by": [{"who": "claude"}],
                                          "evidence": [f"{a.id}@nope"]}, "x")
    assert any(r["from"] == obs.id for r in __import__("kb.graph", fromlist=["x"]).neighbours(kb, a.id))
    with pytest.raises(KBError, match="in this store already"):
        spider.add(kb, base + "/site/", WHOLE, delay=0, page=False)


def test_spider_needs_doctype_and_creators(kb, site):
    base, asked = site
    with pytest.raises(KBError, match="doctype"):
        spider.add(kb, base + "/site/", {"creators": ["X"]}, delay=0)
    with pytest.raises(KBError, match="version"):
        spider.add(kb, base + "/site/", {**WHOLE, "version": 1.0}, delay=0)
    assert asked == []                                    # refused before reading anything


@needs_monolith
def test_spider_cli_target_links_local(root, site, tmp_path):
    base, _ = site
    out = tmp_path / "docs" / "site"
    args = ["-C", str(root), "add", base + "/site/", "--spider", "--target", str(out), "--exclude", "search.html",
            "--delay", "0", "--set", "doctype=manual", "--creators", "The Site Team", "--set", "version=1.0.0",
            "--title", "Site documentation", "--summary", "The site's documentation."]
    r = CliRunner().invoke(main, args, catch_exceptions=False)
    assert r.exit_code == 0, r.output
    kb = Store.open(root)
    whole = kb.get(r.stdout.split()[0])
    assert sorted(str(p.relative_to(out)) for p in out.rglob("*.html")) == [
        "b.html", "guide/a.html", "guide/index.html", "index.html"]
    a_html = (out / "guide" / "a.html").read_text()
    assert 'href="../b.html"' in a_html and 'href="#details"' in a_html
    assert 'href="https://external.invalid/x"' in a_html                 # out of scope: left as it was
    home = (out / "index.html").read_text()
    assert 'href="guide/a.html"' in home and 'href="b.html#more"' in home and 'href="guide/index.html"' in home
    assert "color:red" in home                                       # self-contained
    a = kb.get(spider.pages_of(whole)[1]["item"])
    page = a.rendition("page")
    assert page["transform"]["options"]["links"] == "local"
    assert any(loc["type"] == "local" and loc["path"].endswith("guide/a.html") for loc in page["locations"])
    r = CliRunner().invoke(main, ["-C", str(root), "item", "content", f"{a.id}@details"], catch_exceptions=False)
    assert r.exit_code == 0 and "details of A" in r.stdout and "end of A" not in r.stdout
    pages = json.loads(kb.get(whole.id).text("pages"))
    assert pages["scope"] == base + "/site/"


def test_crawl_requisites(kb, site):
    base, asked = site
    c = spider.crawl(kb, base + "/site/", exclude=("search.html",), delay=0, requisites=True)
    assert sorted(f.rel for f in c.files) == ["_static/app.js", "_static/bg.png", "_static/logo.png",
                                              "_static/logo2.png", "more.css", "style.css"]
    assert not any(a.startswith("/site-other") for a in asked)
    assert c.skipped_files["https://cdn.invalid/lib.js"].startswith("out of scope")
    assert c.skipped_files[base + "/site-other/x.png"].startswith("out of scope")
    assert spider.css_refs("a{b:url(data:image/png;base64,xx)} c{d:url( 'e.png' )}") == ["e.png"]


@needs_datalad
def test_spider_mirror_into_dataset(root, site, tmp_path):
    import datalad.api as dl
    from kb import content
    base, _ = site
    ds = tmp_path / "docs"
    dl.create(str(ds), result_renderer="disabled")
    (ds / ".gitignore").write_text("!*\n")
    dl.save(dataset=str(ds), result_renderer="disabled")
    commits = len(_git_log(ds))
    args = ["-C", str(root), "add", base + "/site/", "--spider", "--mirror", "--target", str(ds / "site"),
            "--exclude", "search.html", "--delay", "0", "--set", "doctype=manual", "--creators", "T"]
    r = CliRunner().invoke(main, args, catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert len(_git_log(ds)) == commits + 1                              # one save for everything
    got = sorted(str(p.relative_to(ds / "site")) for p in (ds / "site").rglob("*") if p.is_file() or p.is_symlink())
    assert got == ["_static/app.js", "_static/bg.png", "_static/logo.png", "_static/logo2.png", "b.html",
                   "guide/a.html", "guide/index.html", "index.html", "more.css", "style.css"]
    assert (ds / "site" / "guide" / "a.html").read_bytes() == A          # as served
    kb = Store.open(root)
    whole = kb.get(r.stdout.split()[0])
    m = json.loads(whole.text("pages"))
    assert m["layout"] == "mirror" and len(m["files"]) == 6 and "https://cdn.invalid/lib.js" in m["files_skipped"]
    a = kb.get(m["pages"][1]["item"])
    locs = a.rendition("original")["locations"]
    assert [loc["type"] for loc in locs] == ["datalad", "url"] and locs[0]["path"] == "site/guide/a.html"
    assert locs[1]["retrieved"]
    assert not a.has_rendition("page") and "details of A" in sections.text(kb, a, "details")

    # a new version: each page supersedes the earlier one of the same URL; the files are replaced in place
    FILES["/site/b.html"] = (B.replace(b"more words", b"other words"), "text/html")
    try:
        r = CliRunner().invoke(main, args + ["--supersedes", whole.id], catch_exceptions=False)
    finally:
        FILES["/site/b.html"] = (B, "text/html")
    assert r.exit_code == 0, r.output
    kb = Store.open(root)
    new = kb.get(r.stdout.split()[0])
    assert not kb.is_current(whole.id)
    for old, now in zip(m["pages"], spider.pages_of(new)):
        assert kb.get(now["item"]).data["supersedes"] == [old["item"]] and not kb.is_current(old["item"])
    assert b"other words" in (ds / "site" / "b.html").read_bytes()
    assert len(_git_log(ds)) == commits + 2


def _git_log(path):
    import subprocess
    return subprocess.run(["git", "-C", str(path), "log", "--oneline"], capture_output=True, text=True).stdout.split("\n")[:-1]
