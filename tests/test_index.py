"""The search index: text by rendition and chunk (extracted text included), embeddings as a cache."""

import importlib.util
import json
import sqlite3

import pytest
from click.testing import CliRunner

from kb import authoring, doctor, embed, query, search, transforms
from kb.cli import main
from kb.errors import KBError
from kb.items import Store

from conftest import observation, set_store

needs_pdf = pytest.mark.skipif(importlib.util.find_spec("pymupdf") is None, reason="needs PyMuPDF")
DOC = {"title": "Detector notes", "summary": "Notes.", "doctype": "other", "creators": ["A"], "date": "2026"}


def pdf(path, text):
    import pymupdf
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    doc.save(path)
    return path


@needs_pdf
def test_text_extracted_on_add_is_searched(root, tmp_path):
    f = pdf(tmp_path / "talk.pdf", "The purity monitor saw electron lifetime drop.")
    r = CliRunner().invoke(main, ["-C", str(root), "add", str(f), "--title", "Purity talk", "--summary", "A talk.",
                                  "--creators", "A", "--date", "2026", "--set", "doctype=presentation"],
                           catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert "extracted" in r.stderr and "#text from #original" in r.stderr
    kb = Store.open(root)
    (hit,) = search.search(kb, "electron lifetime")
    assert hit["rendition"] == "text" and hit["from"] == "original" and "[electron]" in hit["snip"]
    # words of the title and of the text match together, though they are in different columns
    assert search.search(kb, "purity lifetime")[0]["id"] == hit["id"]
    out = CliRunner().invoke(main, ["-C", str(root), "search", "lifetime"]).stdout
    assert "in #text (from #original)" in out


@needs_pdf
def test_unextracted_remedy(kb, tmp_path):
    rec = authoring.add(kb, "document", [str(pdf(tmp_path / "a.pdf", "cryostat pressure excursion"))], dict(DOC))
    assert search.search(kb, "cryostat") == []
    (f,) = doctor.diagnose(kb, ["unextracted"])
    assert f.mode == "auto" and rec.id in f.summary
    assert doctor.treat(kb, [f], lambda f: True)[0]["result"] == "fixed"
    kb = Store.open(kb.root)
    assert search.search(kb, "cryostat")[0]["rendition"] == "text"
    assert doctor.diagnose(kb, ["unextracted"]) == []
    assert transforms.text_name("original") == "text" and transforms.text_name("slides") == "slides-text"


def test_text_outside_the_database_is_indexed(kb, tmp_path):
    set_store(kb.root)
    kb.set_setting("limits", {"max_inline_bytes": 100})
    kb = Store.open(kb.root)
    f = tmp_path / "log.md"
    f.write_text("Shift log.\n\n" + "filler words here. " * 20 + "\n\nThe HV trip happened at midnight.\n")
    rec = authoring.add(kb, "document", [str(f)], dict(DOC))
    assert rec.rendition("original")["locations"][0]["type"] == "cas"
    (hit,) = search.search(kb, "midnight")
    assert hit["id"] == rec.id and hit["rendition"] == "original" and hit["from"] is None
    rows = kb.conn.execute("SELECT rendition, chunk FROM fts WHERE id = ? ORDER BY rendition, chunk",
                           (rec.id,)).fetchall()
    assert [tuple(r) for r in rows][0] == ("", 0) and all(r[0] == "original" for r in rows[1:])


def bag(identity, dims=("noise", "gain", "drift", "photon", "coherent", "detector")):
    def e(texts):
        return [[t.lower().count(w) + 0.01 for w in dims] for t in texts]
    e.identity = identity
    return e


def test_vectors_are_a_cache_by_model(kb, tmp_path, monkeypatch):
    observation(kb, title="Coherent noise", summary="Coherent noise was high.", text="noise noise coherent")
    observation(kb, title="Photon gain", summary="PDS gain drifted.", subject=("detector/pds",),
                text="photon detector gain drift")
    a, b = bag("model-a"), bag("model-b", ("noise", "gain", "drift", "photon"))
    assert embed.embed(kb, a)["embedded"] == 4
    assert embed.embed(kb, b)["embedded"] == 4          # another model: its own vectors
    kb.reindex()
    assert embed.embed(kb, a)["embedded"] == 0          # reindexing loses nothing
    assert embed.embed(kb, a)["cached"] == 4 and embed.missing(kb, "model-b") == (0, 4)
    (top, score, rendition) = embed.semantic(kb, "photon gain drift", embedder=b)[0]
    assert kb.get(top).data["title"] == "Photon gain"
    assert embed.vectors_path(kb).parent.name == "wckb"     # the user's cache, not the store
    hy = search.search(kb, "coherent noise", mode="hybrid", embedder=a)
    assert hy[0]["title"] == "Coherent noise"


def test_upgrade_from_database_version_1(kb):
    observation(kb, title="Old", summary="From before.", text="ancient words")
    c = kb.conn
    c.execute("DROP TABLE fts")
    c.execute("CREATE VIRTUAL TABLE fts USING fts5(id UNINDEXED, title, summary, subjects, body)")
    c.execute("CREATE TABLE vectors (identity TEXT, id TEXT, chunk INTEGER, text_hash TEXT, dim INTEGER, vec BLOB,"
              " PRIMARY KEY (identity, id, chunk))")
    c.execute("INSERT INTO vectors VALUES ('old-model', 'x', 0, 'h0', 2, ?)", (embed._pack([1.0, 0.0]),))
    c.execute("PRAGMA user_version = 1")
    with pytest.raises(KBError, match="predates this kb"):
        query.fts(Store.open(kb.root, readonly=True), "ancient")
    kb = Store.open(kb.root)                          # upgraded: the index is rebuilt
    assert search.search(kb, "ancient")[0]["title"] == "Old"
    assert "_reindex" not in kb.settings()
    r = embed.embed(kb, bag("model-a"))               # the old vectors are copied into the cache
    db = sqlite3.connect(r["file"])
    assert db.execute("SELECT count(*) FROM vectors WHERE identity = 'old-model'").fetchone()[0] == 1
    assert not kb.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'vectors_v1'").fetchone()
