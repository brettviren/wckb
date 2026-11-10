"""The search index: text by rendition and chunk (extracted text included), embeddings as a cache."""

import importlib.util
import json
import subprocess
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
    (top, score, rendition, page) = embed.semantic(kb, "photon gain drift", embedder=b)[0]
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


# -- the content dataset, pages, OCR, sqlite-vec, reindex --fetch ----------------------------------

from kb import content, datasets, ocr  # noqa: E402

needs_datalad = pytest.mark.skipif(datasets.available() is not None, reason=f"DataLad: {datasets.available()}")


@needs_datalad
def test_large_content_goes_to_the_content_dataset(kb, tmp_path):
    big = tmp_path / "big.bin"
    big.write_bytes(b"z" * (kb.cfg.max_inline_bytes + 1))
    assert content.content_dataset(kb) is None
    rec = authoring.add(kb, "dataset", [str(big)], {"title": "Big", "summary": "Bytes."})
    (loc,) = rec.rendition("original")["locations"]
    kb = Store.open(kb.root)
    src, root = content.content_dataset(kb)
    assert loc["type"] == "datalad" and loc["source"] == src.id and loc["path"].startswith("sha256/")
    assert root == (kb.root / "content").resolve() and src.data["title"] == "test content"
    assert kb.setting("content")["dataset"] == src.id
    assert content.resolve(kb, kb.get(rec.id), kb.get(rec.id).rendition("original")).read_bytes() == big.read_bytes()
    # a second file reuses it; the store's status names it
    big.write_bytes(b"w" * (kb.cfg.max_inline_bytes + 1))
    rec2 = authoring.add(kb, "dataset", [str(big)], {"title": "Big 2", "summary": "More bytes."})
    assert rec2.rendition("original")["locations"][0]["source"] == src.id
    r = CliRunner().invoke(main, ["-C", str(kb.root), "status"])
    assert f"content   {root}" in r.stdout


@needs_datalad
def test_content_dataset_keeps_what_a_person_ignores(kb, tmp_path, monkeypatch):
    # a person's global ignore rules ("*.html" in ~/.config/git/ignore) made datalad save skip a page
    excludes = tmp_path / "ignore"
    excludes.write_text("*.html\n")
    (tmp_path / "gitconfig").write_text(f"[core]\n\texcludesfile = {excludes}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    page = tmp_path / "page.html"
    page.write_text("<p>" + "x" * (kb.cfg.max_inline_bytes + 1) + "</p>")
    rec = authoring.add(kb, "document", [str(page)], {"title": "Page", "summary": "A big page.",
                                                       "doctype": "report", "creators": ["A"], "date": "2026"})
    (loc,) = rec.rendition("original")["locations"]
    assert loc["type"] == "datalad" and loc["path"].endswith("/page.html")
    root = content.content_dataset(Store.open(kb.root))[1]
    assert "!*" in (root / ".gitignore").read_text().splitlines()
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True).stdout  # noqa: E731
    assert git("ls-files", ".gitignore").strip() == ".gitignore" and not git("status", "--porcelain").strip()

    # elsewhere, an ignored file is a clear error, not a silent skip
    (root / ".gitignore").write_text("*.html\n")
    assert datasets.ignored(root, "sha256/x/page.html").endswith(":*.html")
    assert datasets.ignored(root, loc["path"]) is None             # tracked files are never ignored


@needs_datalad
def test_content_dataset_elsewhere(kb, tmp_path):
    r = CliRunner().invoke(main, ["-C", str(kb.root), "store", "content", str(tmp_path / "elsewhere")],
                           catch_exceptions=False)
    assert r.exit_code == 0 and r.stdout.startswith(str(tmp_path / "elsewhere"))
    kb = Store.open(kb.root)
    assert kb.setting("content")["path"] == str(tmp_path / "elsewhere")
    assert content.content_dataset(kb)[1] == (tmp_path / "elsewhere").resolve()


@needs_pdf
def test_pdf_pages(kb, tmp_path):
    import pymupdf
    doc = pymupdf.open()
    for words in ("first page about calibration", "second page about the cathode voltage"):
        doc.new_page().insert_text((72, 72), words)
    doc.save(tmp_path / "two.pdf")
    rec = authoring.add(kb, "document", [str(tmp_path / "two.pdf")], dict(DOC))
    transforms.extract_missing(kb, rec)
    kb = Store.open(kb.root)
    (hit,) = search.search(kb, "cathode")
    assert (hit["rendition"], hit["page"]) == ("text", 2)
    out = CliRunner().invoke(main, ["-C", str(kb.root), "search", "cathode"]).stdout
    assert "in #text, page 2 (from #original)" in out


@pytest.fixture
def fake_ocr(tmp_path, monkeypatch):
    """An OCR engine configured as a command (as a local model behind a script would be)."""
    script = tmp_path / "fake-ocr"
    script.write_text("#!/bin/sh\necho \"text read from $(basename \"$1\")\"\n")
    script.chmod(0o755)
    return {"engines": {"fake": {"command": [str(script), "{input}"], "priority": 1, "version": "0.1"}}}


def test_ocr_engines(fake_ocr, tmp_path):
    e = ocr.engine(fake_ocr)
    assert e.name == "fake" and ocr.read(tmp_path / "x.png", fake_ocr)[0].strip() == "text read from x.png"
    assert [x.name for x in ocr.engines(fake_ocr)] == ["fake", "tesseract"]
    with pytest.raises(KBError, match="no OCR engine 'nope'"):
        ocr.engine({**fake_ocr, "engine": "nope"})


def test_ocr_transform_for_images(kb, tmp_path, fake_ocr):
    pytest.importorskip("PIL")
    from PIL import Image
    Image.new("RGB", (8, 8)).save(tmp_path / "board.png")
    kb.set_setting("ocr", fake_ocr)
    kb = Store.open(kb.root)
    rec = authoring.add(kb, "document", [str(tmp_path / "board.png")], dict(DOC))
    (made,), failed = transforms.extract_missing(kb, rec)
    assert not failed and made["fidelity"] == "ocr" and made["transform"]["options"]["ocr"] == "fake 0.1"
    assert search.search(Store.open(kb.root), "read")[0]["rendition"] == "text"


@needs_pdf
def test_scanned_pdf_pages_are_read_by_ocr(kb, tmp_path, fake_ocr):
    import pymupdf
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "a page with real text on it")
    doc.new_page()                                    # a "scan": no text of its own
    doc.save(tmp_path / "scan.pdf")
    kb.set_setting("ocr", fake_ocr)
    kb = Store.open(kb.root)
    rec = authoring.add(kb, "document", [str(tmp_path / "scan.pdf")], dict(DOC))
    (made,), _ = transforms.extract_missing(kb, rec)
    assert made["fidelity"] == "extracted" and made["transform"]["options"]["ocr_pages"] == [2]
    (hit,) = search.search(Store.open(kb.root), "page-2")
    assert hit["page"] == 2


def test_sqlite_vec_and_python_agree(kb, monkeypatch):
    observation(kb, title="Coherent noise", summary="Coherent noise was high.", text="noise noise coherent")
    observation(kb, title="Photon gain", summary="PDS gain drifted.", text="photon detector gain drift")
    e = bag("model-a")
    embed.embed(kb, e)
    with_vec = embed.semantic(kb, "photon gain drift", embedder=e)
    db = embed._vdb(kb)
    assert db.vec, "sqlite-vec should load here"
    assert db.execute("SELECT count(*) FROM vec_1").fetchone()[0] == 4
    db.close()
    kb.set_setting("embed", {"sqlite_vec": False})
    plain = embed.semantic(Store.open(kb.root), "photon gain drift", embedder=e)
    assert [r[0] for r in with_vec] == [r[0] for r in plain]
    assert all(abs(a[1] - b[1]) < 1e-5 for a, b in zip(with_vec, plain))


def test_reindex_fetch(kb, tmp_path):
    f = tmp_path / "remote.md"
    f.write_text("The magnet quench was logged.\n")
    rec = authoring.add(kb, "document", [f.as_uri()], dict(DOC), fetch=False)
    assert search.search(kb, "quench") == []
    kb.reindex()
    assert search.search(kb, "quench") == []          # not fetched: not here, so not indexed
    kb.reindex(fetch=True)
    assert search.search(kb, "quench")[0]["id"] == rec.id
