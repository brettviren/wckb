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
    from kb.records import Record
    data = copy.deepcopy(kb.get(rec.id).data)
    data["renditions"][-1]["source_sha256"] = "f" * 64
    kb.save(Record(data, kb))
    assert [s.rendition for s in graph.stale_renditions(kb)] == ["text"]


@pytest.mark.skipif(not shutil.which("pandoc"), reason="needs pandoc")
def test_pandoc_html(kb, tmp_path):
    f = tmp_path / "page.html"
    f.write_text("<h1>Title</h1><p>Some <b>bold</b> text.</p>")
    rec = authoring.add(kb, "document", [str(f)], DOC)
    transforms.extract(kb, rec)
    assert "# Title" in kb.get(rec.id).text("text")


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
    from kb.records import KB
    kb = KB.open(root)
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
