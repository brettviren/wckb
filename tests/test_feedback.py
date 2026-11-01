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
    assert d["creators"] == ["Ann Example"] and d["date"] == "2024-10-15"    # Last-Modified, the page has no date
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
    with pytest.raises(KBError, match=r"'date' is a required property \(give --date\)"):
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
