import json
import shutil

import pytest

from kb import authoring, graph, transforms
from kb.errors import KBError


def minimal_pdf(text: str) -> bytes:
    """A one-page PDF showing *text*, built by hand so the tests need no PDF writer."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


DOC = {"title": "Doc", "summary": "A document.", "doctype": "technote", "date": "2026", "creators": ["A"]}


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="needs poppler pdftotext")
def test_pdf_extract_and_staleness(kb, tmp_path):
    f = tmp_path / "note.pdf"
    f.write_bytes(minimal_pdf("Drift velocity 1.6 mm per us"))
    rec = authoring.add(kb, "document", [str(f)], DOC)
    r = transforms.extract(kb, rec, transform="pdftotext")
    assert r["fidelity"] == "extracted" and r["source_sha256"] == rec.rendition("original")["sha256"]
    assert "Drift velocity" in kb.get(rec.id).text("text")
    with pytest.raises(KBError, match="up to date"):
        transforms.extract(kb, rec, transform="pdftotext")
    # A derived rendition whose source hash doesn't match is stale.
    import copy
    from kb.items import Item
    data = copy.deepcopy(kb.get(rec.id).data)
    data["renditions"][-1]["source_sha256"] = "f" * 64
    kb.save(Item(data, kb))
    assert [s.rendition for s in graph.stale_renditions(kb)] == ["text"]


@pytest.mark.skipif(not shutil.which("pandoc"), reason="needs pandoc")
def test_pandoc_html(kb, tmp_path):
    f = tmp_path / "page.html"
    f.write_text("<h1>Title</h1><p>Some <b>bold</b> text.</p>")
    rec = authoring.add(kb, "document", [str(f)], DOC)
    transforms.extract(kb, rec)
    assert "# Title" in kb.get(rec.id).text("text")


@pytest.mark.skipif(not shutil.which("pandoc"), reason="needs pandoc")
def test_pandoc_html_no_div_tags(kb, tmp_path):
    f = tmp_path / "page.html"
    f.write_text('<div class="wrap"><h1>Title</h1><div class="prose"><p>Some <span class="x">'
                 'spanned</span> text.</p></div></div>')
    rec = authoring.add(kb, "document", [str(f)], DOC)
    transforms.extract(kb, rec)
    text = kb.get(rec.id).text("text")
    assert "Some spanned text." in text and "<div" not in text and "<span" not in text


def test_pptx(kb, tmp_path):
    pptx = pytest.importorskip("pptx")
    prs = pptx.Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Noise update"
    s.placeholders[1].text_frame.text = "RMS up 20% in run 1234"
    s.notes_slide.notes_text_frame.text = "Mention the grounding fix."
    path = tmp_path / "talk.pptx"
    prs.save(path)
    rec = authoring.add(kb, "document", [str(path)], {**DOC, "doctype": "presentation"})
    transforms.extract(kb, rec)
    text = kb.get(rec.id).text("text")
    assert "## Slide 1: Noise update" in text and "RMS up 20%" in text and "grounding fix" in text


def test_root_describe(kb, tmp_path):
    uproot = pytest.importorskip("uproot")
    np = pytest.importorskip("numpy")
    path = tmp_path / "hists.root"
    with uproot.recreate(path) as f:
        f["rms"] = np.histogram(np.arange(10.0), bins=5, range=(0, 10))
        f["tree"] = {"run": np.array([1, 2, 3]), "rms": np.array([1.0, 2.0, 3.0])}
    rec = authoring.add(kb, "dataset", [str(path)], {"title": "H", "summary": "Hists."})
    r = transforms.extract(kb, rec, name="structure")
    assert r["media_type"] == "application/json"
    desc = json.loads(kb.get(rec.id).text("structure"))
    by = {o["key"].split(";")[0]: o for o in desc["objects"]}
    assert by["rms"]["integral"] == 10.0 and by["tree"]["entries"] == 3


def test_command_transform_from_config(root, tmp_path):
    from kb.items import Store
    kb = Store.open(root)
    kb.set_setting("transforms", {"upper": {"command": ["sh", "-c", "tr a-z A-Z < {input} > {output}"],
                                            "media_types": ["text/plain"], "fidelity": "lossless"}})
    f = tmp_path / "a.txt"
    f.write_text("hello\n")
    rec = authoring.add(kb, "document", [str(f)], DOC)
    r = transforms.extract(kb, rec)
    assert r["transform"]["name"] == "upper" and kb.get(rec.id).text("text") == "HELLO\n"


def test_no_transform(kb, tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"\0")
    rec = authoring.add(kb, "document", [str(f)], DOC)
    with pytest.raises(KBError, match="no available transform"):
        transforms.extract(kb, rec)


needs_monolith = pytest.mark.skipif(not shutil.which("monolith"), reason="needs monolith")


@needs_monolith
def test_page_of_local_file(kb, tmp_path):
    (tmp_path / "s.css").write_text("body{color:red}")
    f = tmp_path / "page.html"
    f.write_text('<html><head><link rel="stylesheet" href="s.css"></head>'
                 '<body><p>Hello page.</p><img src="gone.png"></body></html>')
    rec = authoring.add(kb, "document", [str(f)], DOC)
    assert transforms.choose(kb, "text/html").name == "pandoc"      # a page transform is never chosen for text
    # kb holds a copy of the page without s.css beside it: the file given says where to look
    made, failed = transforms.page_missing(kb, rec, {"original": f.resolve().as_uri()})
    assert not failed and [r["name"] for r in made] == ["page"]
    page = made[0]
    assert page["derived_from"] == "original" and page["transform"]["name"] == "monolith"
    options = page["transform"]["options"]
    assert "base" not in options and len(options["missing"]) == 1 and "gone.png" in options["missing"][0]
    html = kb.get(rec.id).text("page")
    assert "data:text/css" in html and "color:red" in html
    # a page does not count as text extracted: the page is still extracted for search, once
    assert [s for s, _ in transforms.unextracted(kb, kb.get(rec.id))] == ["original"]
    transforms.extract_missing(kb, kb.get(rec.id))
    assert "Hello page." in kb.get(rec.id).text("text")
    assert transforms.unpaged(kb, kb.get(rec.id)) == []
    assert transforms.page_missing(kb, kb.get(rec.id)) == ([], [])


@needs_monolith
def test_add_url_keeps_page(root, tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from click.testing import CliRunner
    from kb.cli import main
    from kb.items import Store

    files = {"/report/index.html": (b'<html><head><link rel="stylesheet" href="style.css"></head>'
                                    b'<body><h1>Report</h1></body></html>', "text/html"),
             "/report/style.css": (b"h1{color:blue}", "text/css")}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body, kind = files.get(self.path, (b"", None))
            self.send_response(200 if kind else 404)
            self.send_header("Content-Type", kind or "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/report/index.html"
    try:
        args = ["-C", str(root), "add", url, "--title", "Report", "--summary", "A report.",
                "--creators", "A", "--set", "doctype=report"]
        r = CliRunner().invoke(main, args, catch_exceptions=False)
        assert r.exit_code == 0, r.output
        assert "made " in r.stderr
        kb = Store.open(root)
        rec = kb.get(r.stdout.split()[0])
        page = rec.rendition("page")
        assert page["transform"]["options"]["base"] == url      # style.css found next to the page's URL
        assert "h1{color:blue}" in rec.text("page") and "# Report" in rec.text("text")
        r = CliRunner().invoke(main, ["-C", str(root), "open", rec.id, "--print"], catch_exceptions=False)
        assert r.exit_code == 0 and r.stdout.strip().endswith(".html")
        from kb import opener
        assert opener.choose(rec)["name"] == "page"

        r = CliRunner().invoke(main, args + ["--no-page", "--force"], catch_exceptions=False)
        assert r.exit_code == 0, r.output
        assert not Store.open(root).get(r.stdout.split()[0]).has_rendition("page")
    finally:
        server.shutdown()
