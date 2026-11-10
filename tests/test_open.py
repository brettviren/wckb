"""kb open: the file of an item (its original, or its text), named, got if need be, opened."""

import subprocess
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from kb import authoring, datasets, opener
from kb.cli import main
from kb.errors import KBError
from kb.items import Store

from conftest import set_store

DOC = {"title": "Slides", "summary": "Some slides.", "doctype": "presentation", "creators": ["A"], "date": "2026"}


def kb_cli(root, *args):
    return CliRunner().invoke(main, ["-C", str(root), *args], catch_exceptions=False)


@pytest.fixture
def opened(tmp_path, kb):
    """open.command: a script that records what it was asked to open."""
    log = tmp_path / "opened.txt"
    script = tmp_path / "fake-open"
    script.write_text(f'#!/bin/sh\necho "$1" >> {log}\n')
    script.chmod(0o755)
    kb.set_setting("open", {"command": [str(script), "{path}"]})
    return log


def test_open_the_original_or_its_text(kb, tmp_path, opened):
    f = tmp_path / "talk.pptx"
    f.write_bytes(b"not really pptx")
    rec = authoring.add(kb, "document", [str(f)], dict(DOC), guess=False)
    _, r, p = opener.path(kb, rec.id)
    assert r["name"] == "original" and p.name == "talk.pptx" and p.read_bytes() == b"not really pptx"
    with pytest.raises(KBError, match="no text extracted from it"):
        opener.path(kb, rec.id, text=True)
    note = tmp_path / "talk.md"
    note.write_text("# The talk\n")
    t = authoring.add(kb, "document", [str(note)], dict(DOC), force=True)
    _, r, p = opener.path(kb, t.id, text=True)                 # text already: the original itself
    assert r["name"] == "original" and p.name == "talk.md" and p.read_text() == "# The talk\n"
    out = kb_cli(kb.root, "open", rec.id)
    assert out.exit_code == 0 and out.stdout.strip().endswith("talk.pptx")
    for _ in range(50):                                        # the opener runs in the background
        if opened.exists():
            break
        time.sleep(0.05)
    assert opened.read_text().strip().endswith("talk.pptx")


def test_open_extracted_text(kb, tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "hello from the pdf")
    doc.save(tmp_path / "paper.pdf")
    r = kb_cli(kb.root, "add", str(tmp_path / "paper.pdf"), "--title", "Paper", "--summary", "A paper.",
               "--creators", "A", "--date", "2026", "--set", "doctype=paper")
    ident = r.stdout.split()[0]
    kb = Store.open(kb.root)
    _, r, p = opener.path(kb, ident)
    assert r["name"] == "original" and p.name == "paper.pdf"      # a hit in #text still opens the PDF
    _, r, p = opener.path(kb, ident, text=True)
    assert r["name"] == "text" and "hello from the pdf" in p.read_text()
    assert kb_cli(kb.root, "open", ident, "--text", "--print").stdout.strip() == str(p)


def test_content_kept_by_hash_gets_its_name(kb, tmp_path):
    set_store(kb.root)
    kb.set_setting("limits", {"max_inline_bytes": 10})
    kb = Store.open(kb.root)
    f = tmp_path / "big.csv"
    f.write_text("a,b\n1,2\n3,4\n")
    rec = authoring.add(kb, "dataset", [str(f)], {"title": "Big", "summary": "Numbers."})
    assert rec.rendition("original")["locations"][0]["type"] == "cas"
    _, _, p = opener.path(kb, rec.id)
    assert p.name == "big.csv" and p.is_symlink() and p.read_text() == "a,b\n1,2\n3,4\n"


@pytest.mark.skipif(datasets.available() is not None, reason=f"DataLad: {datasets.available()}")
def test_open_gets_dataset_content(kb, tmp_path):
    import datalad.api as dl
    origin = tmp_path / "origin"
    dl.create(str(origin), result_renderer="disabled")
    (origin / "talk.pdf").write_bytes(b"%PDF-1.4 a talk")
    dl.save(dataset=str(origin), result_renderer="disabled")
    clone = tmp_path / "clone"
    dl.clone(source=str(origin), path=str(clone), result_renderer="disabled")
    dl.get(str(clone / "talk.pdf"), dataset=str(clone), result_renderer="disabled")
    rec = authoring.add(kb, "document", [str(clone / "talk.pdf")], dict(DOC), guess=False)
    assert rec.rendition("original")["locations"][0]["type"] == "datalad"
    dl.drop(str(clone / "talk.pdf"), dataset=str(clone), result_renderer="disabled")
    assert not (clone / "talk.pdf").exists()                   # the origin still has it
    kb = Store.open(kb.root)
    with pytest.raises(KBError, match="not reachable from here without fetching"):
        opener.path(kb, rec.id, fetch=False)
    _, _, p = opener.path(kb, rec.id)                          # datalad get, then open
    assert (clone / "talk.pdf").exists() and p.read_bytes() == b"%PDF-1.4 a talk" and p.name == "talk.pdf"


def test_opener_rules_by_environment(kb):
    kb.set_setting("open", {"command": ["gio", "open"], "rules": [
        {"name": "kitty over ssh", "env": ["SSH_CONNECTION", "KITTY_LISTEN_ON"], "command": "kitty-open",
         "terminal": True},
        {"env": "SSH_CONNECTION", "unless": "DISPLAY", "command": ["less", "{path}"], "terminal": True}]})
    kb = Store.open(kb.root)
    o = opener.choose_opener(kb, {"SSH_CONNECTION": "a b", "KITTY_LISTEN_ON": "unix:/tmp/k"})
    assert o.command == ["kitty-open"] and o.terminal and o.why == "open.rules[0] (kitty over ssh)"
    assert o.argv(Path("/x/a.pdf")) == ["kitty-open", "/x/a.pdf"]
    o = opener.choose_opener(kb, {"SSH_CONNECTION": "a b"})
    assert o.argv(Path("/x/a.pdf")) == ["less", "/x/a.pdf"] and o.why == "open.rules[1]"
    o = opener.choose_opener(kb, {"SSH_CONNECTION": "a b", "DISPLAY": ":0"})      # unless: falls through
    assert o.command == ["gio", "open"] and not o.terminal
    assert o.why == ("open.command; open.rules[0] (kitty over ssh) skipped: KITTY_LISTEN_ON not set; "
                     "open.rules[1] skipped: DISPLAY set")
    kb.set_setting("open", {"rules": [{"env": "X"}]})
    with pytest.raises(KBError, match=r"open.rules\[0\] needs a command"):
        opener.choose_opener(Store.open(kb.root), {})


def test_terminal_opener_runs_attached(kb, tmp_path):
    f = tmp_path / "talk.pdf"
    f.write_bytes(b"%PDF-1.4 not really")
    rec = authoring.add(kb, "document", [str(f)], dict(DOC), guess=False)
    log = tmp_path / "ran.txt"
    script = tmp_path / "term-open"
    script.write_text(f'#!/bin/sh\n[ "$1" = --flag ] && echo "$2" > {log}\n')
    script.chmod(0o755)
    kb.set_setting("open", {"rules": [{"env": "KB_TEST_OPEN", "command": [str(script), "--flag"],
                                       "terminal": True}]})
    out = CliRunner(env={"KB_TEST_OPEN": "1"}).invoke(main, ["-C", str(kb.root), "open", rec.id],
                                                       catch_exceptions=False)
    assert out.exit_code == 0, out.output
    assert log.read_text().strip().endswith("talk.pdf")          # done when kb returns: no waiting needed
    assert "(application/pdf) with " in out.stderr and "open.rules[0]" in out.stderr
    script.write_text("#!/bin/sh\nexit 3\n")
    out = CliRunner(env={"KB_TEST_OPEN": "1"}).invoke(main, ["-C", str(kb.root), "open", rec.id])
    assert out.exit_code != 0 and "failed (3)" in out.output
