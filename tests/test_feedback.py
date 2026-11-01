"""Fixes from a research session run through kb: web pages, tags, sentence searches, messages, recall."""

import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from kb import authoring, hooks, query, search
from kb.errors import KBError
from kb.items import Store

from conftest import observation

PAGE = b"""<!DOCTYPE html><html><head><title>vecdb - a tiny vector store</title>
<meta name="description" content="vecdb keeps vectors in SQLite. It is small.">
<meta name="author" content="Ann Example"></head>
<body><nav>Skip to content</nav><h1>vecdb</h1><p>Use a <b>partition key</b> column to split vectors by store.</p></body></html>"""


@pytest.fixture
def site():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Last-Modified", "Tue, 15 Oct 2024 08:00:00 GMT")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)
    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_a_web_page(kb, site):
    rec = authoring.add(kb, "document", [f"{site}/asg/vecdb"], {"doctype": "manual"})
    d, r = rec.data, rec.rendition("original")
    assert r["media_type"] == "text/html"                     # from the server, though the URL has no extension
    assert d["title"] == "vecdb - a tiny vector store" and d["summary"].startswith("vecdb keeps vectors")
    assert d["creators"] == ["Ann Example"] and "date" not in d               # the page says nothing of when
    assert d["dates"]["modified"] == "2024-10-15" and len(d["dates"]["accessed"]) == 10   # the server; kb
    if shutil.which("pandoc"):
        from kb import transforms
        made, failed = transforms.extract_missing(kb, rec)
        assert not failed and made[0]["name"] == "text"
        kb = Store.open(kb.root)
        (hit,) = search.search(kb, "partition key")
        assert hit["rendition"] == "text" and "<b>" not in hit["snip"]
    kb = Store.open(kb.root)                                  # markup is never indexed as it is
    assert not kb.conn.execute("SELECT 1 FROM fts WHERE rendition = 'original'").fetchone()


def test_tags_are_listed_filtered_and_searched(kb):
    a = observation(kb, title="sqlite-vec is in-process", tags=["in-process", "sqlite"])
    observation(kb, title="Qdrant is a server", tags=["server"])
    assert [r["id"] for r in query.ls(kb, tags=["in-process"])] == [a.id]
    assert query.ls(kb, tags=["in-process", "server"]) == []
    assert query.tags(kb) == {"in-process": 1, "server": 1, "sqlite": 1}
    assert search.search(kb, "in-process")[0]["id"] == a.id


def test_a_sentence_finds_items_with_some_of_its_words(kb):
    a = observation(kb, title="nomic-embed needs task prefixes", summary="Queries and documents get prefixes.")
    rows = search.search(kb, "which embedding model needs prefixes")
    assert rows[0]["id"] == a.id and rows[0]["match"] == "some"
    assert search.search(kb, "task prefixes")[0]["match"] == "all"


def test_messages_name_options_and_limits(kb):
    with pytest.raises(KBError, match=r"needs one of 'date' \(give --date\), 'dates' \(give --dated"):
        authoring.new(kb, "document", {"title": "T", "summary": "S.", "doctype": "manual", "creators": ["A"],
                                       "identifiers": {"url": "https://example.org"}})
    with pytest.raises(KBError, match="too long: 450 characters, at most 400"):
        observation(kb, summary="x" * 450)
    a = observation(kb)
    with pytest.raises(KBError, match="attribute the assertion of an observation or config"):
        authoring.new(kb, "topic", {"title": "T", "summary": "S.", "subject": ["x"], "derived_from": [a.id],
                                    "asserted_by": [{"who": "claude"}]}, text="page")


def test_recall_names_each_item_once(kb):
    a = observation(kb, title="Coherent noise grounding", summary="Coherent noise fell after grounding.",
                    text="\n\n".join(["coherent noise grounding " * 40] * 6))    # several chunks
    out = hooks.recall({"prompt": "what happened to the coherent noise after grounding?", "cwd": str(kb.root)})
    assert out.count(a.id) == 1


def test_mistyped_remedy(kb, site, monkeypatch):
    from kb import content, doctor
    with monkeypatch.context() as m:       # as kb recorded pages before it looked at what servers say
        m.setattr(content, "sniff", lambda path, name, said=None: "application/octet-stream")
        rec = authoring.add(kb, "document", [f"{site}/page"], {"doctype": "manual", "title": "P",
                                                               "summary": "A page.", "creators": ["A"], "date": "2026"})
    assert rec.rendition("original")["media_type"] == "application/octet-stream"   # as kb recorded pages before
    (f,) = doctor.diagnose(kb, ["mistyped"])
    assert f.mode == "auto" and "text/html" in f.summary
    assert doctor.treat(kb, [f], lambda f: True)[0]["result"] == "fixed"
    kb = Store.open(kb.root)
    assert kb.get(rec.id).rendition("original")["media_type"] == "text/html"
    assert doctor.diagnose(kb, ["mistyped"]) == []


@pytest.fixture
def refusing():
    """A site that answers 403 to kb, and 404 under /gone."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(404 if self.path.startswith("/gone") else 403)
            self.end_headers()
    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_fetch_failures_are_recorded(kb, refusing):
    from kb import content, doctor
    with pytest.raises(KBError, match="HTTP 404.*the URL is wrong"):
        authoring.add(kb, "document", [f"{refusing}/gone/page.html"], {"doctype": "manual"})
    rec = authoring.add(kb, "document", [f"{refusing}/docs/vss.html"],
                        {"doctype": "manual", "title": "VSS", "summary": "Docs.", "creators": ["DuckDB"],
                         "dates": {"accessed": "2026-11-01"}})
    (loc,) = rec.rendition("original")["locations"]
    assert loc["failed"]["status"] == 403 and loc["failed"]["reason"] == "Forbidden" and "sha256" not in rec.rendition("original")
    found = doctor.diagnose(kb, ["unextracted"])
    assert [f.mode for f in found] == ["manual"] and "HTTP 403 Forbidden" in found[0].summary   # not retried by itself
    report = content.verify(kb, [kb.get(rec.id)])
    assert report[0]["result"] == "unreachable" and report[0]["status"] == 403


def test_a_link_and_its_back_link(kb):
    from kb import graph
    a = observation(kb, title="LanceDB is embedded")
    b = observation(kb, title="sqlite-vec is embedded")
    graph.link(kb, a.id, "alternative-to", b.id, note="own storage format")
    kb = Store.open(kb.root)
    (e,) = [e for e in graph.neighbours(kb, b.id) if e["rel"] == "alternative-to"]
    assert e["direction"] == "in" and e["other"] == a.id and e["label"] == "alternative to"
    graph.link(kb, a.id, "supports", b.id)
    labels = {e["label"] for e in graph.neighbours(Store.open(kb.root), b.id)}
    assert {"alternative to", "supported by"} <= labels
    with pytest.raises(KBError, match="no relation 'likes'"):
        graph.link(kb, a.id, "likes", b.id)
    graph.link(kb, a.id, "supports", b.id, remove=True)
    assert [x["rel"] for x in Store.open(kb.root).get(a.id).data["links"]] == ["alternative-to"]
    from kb.schema import load_schemas
    assert load_schemas()["item"]["properties"]["links"]["items"]["properties"]["rel"]["enum"] == list(graph.RELATIONS)


def test_qualified_dates_can_be_corrected(kb):
    from kb import edit
    rec = authoring.new(kb, "document", {"title": "README", "summary": "A readme.", "doctype": "manual",
                                         "creators": ["A"], "dates": {"accessed": "2026-11-01"},
                                         "identifiers": {"url": "https://example.org/r"}})
    edit.set_fields(kb, rec.id, sets=["dates.published=2024-08-01"])
    assert Store.open(kb.root).get(rec.id).data["dates"] == {"accessed": "2026-11-01", "published": "2024-08-01"}
