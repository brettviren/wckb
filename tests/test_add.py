"""kb add: --target (copies, taken in by the source there) and metadata found in what is added."""

import importlib.util
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from click.testing import CliRunner

from kb import authoring, content, datasets, metadata, sources
from kb.cli import main
from kb.errors import KBError
from kb.items import Store

from conftest import git

DOC = {"title": "T", "summary": "S.", "doctype": "other", "creators": ["A"], "date": "2026"}
needs_datalad = pytest.mark.skipif(datasets.available() is not None, reason=f"DataLad: {datasets.available()}")
needs_pymupdf = pytest.mark.skipif(importlib.util.find_spec("pymupdf") is None, reason="needs PyMuPDF")


def add(kb, files, fields=DOC, **kw):
    return authoring.add(kb, "document", [str(f) for f in files], dict(fields), **kw)


# -- --target ------------------------------------------------------------------------------

@pytest.fixture
def note(tmp_path) -> Path:
    f = tmp_path / "in" / "note.txt"
    f.parent.mkdir()
    f.write_text("a note\n")
    return f


def test_target_plain_directory(kb, note, tmp_path):
    rec = add(kb, [note], target=tmp_path / "out")
    assert not (tmp_path / "out").exists() or (tmp_path / "out").is_file()   # a new path: the file itself
    assert (tmp_path / "out").read_text() == "a note\n"
    types = [l["type"] for l in rec.rendition("original")["locations"]]
    assert types == ["db", "local"]
    (tmp_path / "dir").mkdir()
    rec = add(kb, [note], target=tmp_path / "dir", force=True)
    assert (tmp_path / "dir" / "note.txt").read_text() == "a note\n"
    add(kb, [note], target=str(tmp_path / "new") + "/", force=True)     # a path ending in / is a directory
    assert (tmp_path / "new" / "note.txt").read_text() == "a note\n"
    note.write_text("changed\n")
    with pytest.raises(KBError, match="exists, with other content"):
        add(kb, [note], target=tmp_path / "dir", force=True)


def test_target_in_a_filesystem_source(kb, note, tmp_path):
    tree = tmp_path / "talks"
    tree.mkdir()
    src = sources.add(kb, [str(tree)], "filesystem", name="talks")
    rec = add(kb, [note], target=tree / "2026" / "note.txt")
    locs = rec.rendition("original")["locations"]
    assert locs == [{"type": "source", "source": src.id, "path": "2026/note.txt"}]
    kb = Store.open(kb.root)
    r = kb.get(rec.id)
    assert content.resolve(kb, r, r.rendition("original")) == tree / "2026" / "note.txt"


def test_target_in_a_git_source(kb, note, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "README").write_text("r\n")
    git(repo, "add", "README")
    git(repo, "commit", "-qm", "first")
    # not a source here: copied, not committed
    rec = add(kb, [note], target=repo / "notes" / "a.txt")
    assert "local" in [l["type"] for l in rec.rendition("original")["locations"]]
    assert "notes/a.txt" not in git(repo, "ls-files")
    src = sources.add(kb, [str(repo)], "git")
    rec = add(kb, [note], target=repo / "notes" / "b.txt", force=True)
    assert rec.rendition("original")["locations"] == [{"type": "source", "source": src.id, "path": "notes/b.txt"}]
    assert "notes/b.txt" in git(repo, "ls-files") and "Add notes/b.txt (kb add)" in git(repo, "log", "-1")


@needs_datalad
def test_target_in_a_dataset(kb, note, tmp_path):
    import datalad.api as dl
    ds = tmp_path / "ds"
    dl.create(str(ds), result_renderer="disabled")
    big = note.parent / "big.bin"
    big.write_bytes(b"\0" * 5000)
    rec = authoring.add(kb, "dataset", [str(big)], {"title": "B", "summary": "Bytes."}, target=ds / "raw" / "big.bin")
    (loc,) = rec.rendition("original")["locations"]
    assert loc["type"] == "datalad" and loc["path"] == "raw/big.bin" and loc.get("key")
    assert Store.open(kb.root).get(loc["source"]).data["source_id"] == datasets.dataset_id(ds)
    assert not git(ds, "status", "--porcelain").strip()                # saved
    assert sources.containing(Store.open(kb.root), ds / "raw" / "big.bin")[1] == ds.resolve()


# -- metadata --------------------------------------------------------------------------------

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2601.01234v2</id>
    <published>2026-01-05T18:00:00Z</published>
    <title>Coherent Noise in Liquid Argon
      Time Projection Chambers</title>
    <summary>We study coherent noise in large LArTPCs. It grows with the number of channels.
      A filter removes most of it. Further details follow in the paper.</summary>
    <author><name>Ann Example</name></author>
    <author><name>Bob Sample</name></author>
    <arxiv:doi>10.1000/xyz123</arxiv:doi>
    <arxiv:journal_ref>JINST 21 (2026) P01001</arxiv:journal_ref>
    <category term="physics.ins-det"/>
    <category term="hep-ex"/>
  </entry>
</feed>
"""


@pytest.fixture
def arxiv(monkeypatch):
    """A fake arXiv: the API answers for 2601.01234; the PDF is a few bytes."""
    asked = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            p = urlsplit(self.path)
            asked.append(self.path)
            if p.path == "/api/query" and parse_qs(p.query).get("id_list", [""])[0].startswith("2601.01234"):
                body, kind = ATOM.encode(), "application/atom+xml"
            elif p.path.startswith("/pdf/2601.01234"):
                body, kind = b"%PDF-1.4 fake", "application/pdf"
            else:
                body, kind = b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>', "application/atom+xml"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(metadata, "LOOKUP", True)
    monkeypatch.setattr(metadata, "ARXIV_API", base + "/api/query")
    monkeypatch.setattr(metadata, "ARXIV_PDF", base + "/pdf/")
    yield asked
    server.shutdown()


def test_arxiv_ids():
    assert metadata.arxiv_id("https://arxiv.org/abs/2601.01234") == "2601.01234"
    assert metadata.arxiv_id("https://arxiv.org/pdf/2601.01234v3.pdf") == "2601.01234v3"
    assert metadata.arxiv_id("https://arxiv.org/abs/hep-ex/0601001") == "hep-ex/0601001"
    assert metadata.arxiv_id("arXiv:2601.01234") == "2601.01234"
    assert metadata.arxiv_id("https://example.org/abs/2601.01234") is None
    assert metadata.summary_of("One. Two two. Three. Four.") == "One. Two two. Three."


def test_add_from_arxiv(root, arxiv):
    r = CliRunner().invoke(main, ["-C", str(root), "add", "https://arxiv.org/abs/2601.01234"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert "found" in r.stderr and "arXiv 2601.01234" in r.stderr
    rec = Store.open(root).get(r.stdout.split()[0])
    d = rec.data
    assert d["title"] == "Coherent Noise in Liquid Argon Time Projection Chambers"
    assert d["creators"] == ["Ann Example", "Bob Sample"] and d["date"] == "2026-01-05"
    assert d["dates"]["published"] == "2026-01-05" and "accessed" in d["dates"]    # arXiv's date; kb's download
    assert d["identifiers"] == {"arxiv": "2601.01234", "doi": "10.1000/xyz123"}
    assert d["doctype"] == "paper" and d["venue"] == "JINST 21 (2026) P01001" and d["version"] == "2"
    assert d["summary"].startswith("We study coherent noise") and "Further details" not in d["summary"]
    assert set(d["tags"]) == {"physics.ins-det", "hep-ex"}
    orig = rec.rendition("original")
    assert orig["media_type"] == "application/pdf" and "/pdf/2601.01234v2" in orig["locations"][0]["url"]
    assert "A filter removes most of it." in rec.text("abstract")
    # options win; and the same paper again is refused before anything is fetched
    r = CliRunner().invoke(main, ["-C", str(root), "add", "https://arxiv.org/abs/2601.01234", "--title", "Mine",
                                  "--force", "--no-fetch"], catch_exceptions=False)
    assert Store.open(root).get(r.stdout.split()[0]).data["title"] == "Mine"
    r = CliRunner().invoke(main, ["-C", str(root), "add", "https://arxiv.org/abs/2601.01234"])
    assert r.exit_code == 1 and "already in this store" in r.output


def test_arxiv_unreachable_is_a_warning(kb, note, monkeypatch):
    monkeypatch.setattr(metadata, "LOOKUP", True)
    monkeypatch.setattr(metadata, "ARXIV_API", "http://127.0.0.1:9/api/query")
    rec = add(kb, [note], {**DOC, "identifiers": {"arxiv": "2601.09999"}})
    assert rec.data["title"] == "T"


def _pdf(path: Path, landscape=True, page_text="", **meta) -> Path:
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=800 if landscape else 600, height=600 if landscape else 800)
    page.insert_text((72, 72), page_text or "Hello")
    doc.set_metadata(meta)
    doc.save(path)
    return path


@needs_pymupdf
@pytest.mark.parametrize("reader", ["pymupdf", "poppler"])
def test_pdf_metadata(kb, tmp_path, monkeypatch, reader):
    if reader == "poppler":
        if not shutil.which("pdfinfo"):
            pytest.skip("needs poppler's pdfinfo")
    pdf = _pdf(tmp_path / "talk.pdf", title="TPC noise update", author="Ann Example; Bob Sample",
               subject="Noise grew after the grounding change.", creationDate="D:20261028101500Z",
               page_text="See doi:10.1000/abc.987 for details.")
    if reader == "poppler":
        monkeypatch.setitem(sys.modules, "pymupdf", None)          # import fails: the command-line tools are used
    rec = authoring.add(kb, "document", [str(pdf)], {})
    d = rec.data
    assert (d["title"], d["creators"], d["dates"]["created"], d["doctype"]) == (
        "TPC noise update", ["Ann Example", "Bob Sample"], "2026-10-28", "presentation")
    assert d["summary"] == "Noise grew after the grounding change." and d["identifiers"] == {"doi": "10.1000/abc.987"}


@needs_pymupdf
def test_pdf_junk_title_and_arxiv_stamp(kb, tmp_path, arxiv):
    pdf = _pdf(tmp_path / "p.pdf", landscape=False, title="Microsoft Word - draft3.docx", author="someone",
               page_text="arXiv:2601.01234v2 [physics.ins-det] 5 Jan 2026")
    rec = authoring.add(kb, "document", [str(pdf)], {"summary": "Mine."})
    assert rec.data["title"].startswith("Coherent Noise") and rec.data["creators"] == ["Ann Example", "Bob Sample"]
    assert rec.data["summary"] == "Mine." and rec.data["identifiers"]["arxiv"] == "2601.01234"


def test_image_exif(kb, tmp_path):
    PIL = pytest.importorskip("PIL")
    from PIL import Image
    img = Image.new("RGB", (4, 4))
    exif = img.getexif()
    exif[0x013B] = "Ann Example"                    # Artist
    exif[0x010E] = "The TPC after the fill"         # ImageDescription
    exif.get_ifd(0x8769)[0x9003] = "2026:10:28 09:30:00"   # DateTimeOriginal
    img.save(tmp_path / "tpc.jpg", exif=exif)
    rec = authoring.add(kb, "document", [str(tmp_path / "tpc.jpg")], {"summary": "A photo."})
    assert (rec.data["title"], rec.data["creators"], rec.data["dates"]["captured"], rec.data["doctype"]) == (
        "The TPC after the fill", ["Ann Example"], "2026-10-28", "other")


def test_no_guess(kb, tmp_path):
    pytest.importorskip("pymupdf")
    pdf = _pdf(tmp_path / "talk.pdf", title="Found title")
    rec = authoring.add(kb, "document", [str(pdf)], dict(DOC), guess=False)
    assert rec.data["title"] == "T" and rec.data["doctype"] == "other"
