"""Indico sources (git-annex-remote-indico), --target, and sharing sources among a computer's users."""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from click.testing import CliRunner

from kb import datasets, sharing, sources
from kb.cli import main
from kb.errors import KBError
from kb.items import Store

needs_datalad = pytest.mark.skipif(datasets.available() is not None, reason=f"DataLad: {datasets.available()}")
needs_indico = pytest.mark.skipif(shutil.which("git-indico") is None or datasets.available() is not None,
                                  reason="needs git-annex-remote-indico and DataLad")


class FakeIndico(BaseHTTPRequestHandler):
    """The few endpoints git indico ingest uses: two public events with one file each, in category 7."""
    files: dict[str, bytes] = {}
    events: dict[int, dict] = {}

    def log_message(self, *args):
        pass

    def _send(self, status, body: bytes, kind="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path.startswith("/export/event/"):
            n = int(path.rsplit("/", 1)[1].removesuffix(".json"))
            found = [self.events[n]] if n in self.events else []
            return self._send(200, json.dumps({"count": len(found), "results": found}).encode())
        if path.startswith("/category/") and path.endswith("/info"):
            return self._send(200, json.dumps({"category": {"id": 7, "title": "Meetings", "path": [
                {"id": 0, "title": "Home"}, {"id": 7, "title": "Meetings"}]}, "subcategories": []}).encode())
        if path in self.files:
            return self._send(200, self.files[path], "application/octet-stream")
        return self._send(404, b'{"message": "not found"}')


@pytest.fixture
def indico_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeIndico)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    files, events = {}, {}
    for n, day in ((41, "2026-10-21"), (42, "2026-10-28")):
        data = f"slides of meeting {n}\n".encode()
        path = f"/event/{n}/attachments/9/{n}0/slides.pdf"
        files[path] = data
        events[n] = {
            "_type": "Conference", "id": str(n), "title": f"Meeting {n}", "categoryId": 7, "category": "Meetings",
            "url": f"{base}/event/{n}/", "startDate": {"date": day, "time": "10:00:00", "tz": "UTC"},
            "endDate": {"date": day, "time": "11:00:00", "tz": "UTC"}, "description": "", "chairs": [],
            "folders": [{"_type": "folder", "id": 9, "title": None, "attachments": [{
                "_type": "attachment", "id": n * 10, "type": "file", "title": "slides.pdf",
                "filename": "slides.pdf", "download_url": base + path, "content_type": "application/pdf",
                "size": len(data), "checksum": hashlib.md5(data).hexdigest()}]}],
            "contributions": [], "sessions": []}
    server.RequestHandlerClass = type("H", (FakeIndico,), {"files": files, "events": events})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield base
    server.shutdown()


def mode(p: Path) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


def kb_cli(root, *args):
    return CliRunner().invoke(main, ["-C", str(root), *args], catch_exceptions=False)


# -- recognizing --------------------------------------------------------------------------

def test_indico_urls_are_guessed_by_host():
    g = sources.guess(["https://indico.bnl.gov/category/201/"])
    assert g.type == "indico" and "https://indico.bnl.gov" in g.explain()
    assert sources.guess(["http://127.0.0.1:9/event/1/"], only="indico").type == "indico"
    from kb import indico
    assert indico.site("https://indico.example.org/prefix/event/12/contributions/3/") == \
        "https://indico.example.org/prefix"
    assert indico.ingest_args(["U"], {"since": "2025-01-01", "events": "no", "type": "pdf,pptx"}) == [
        "git", "indico", "ingest", "--since", "2025-01-01", "--no-events", "--type", "pdf", "--type", "pptx", "U"]


def test_options_are_checked(kb):
    with pytest.raises(KBError, match="take no option since"):
        sources.add(kb, ["/nowhere"], "filesystem", options={"since": "2025"})


# -- Indico sources -----------------------------------------------------------------------

@needs_indico
def test_indico_source_from_urls(kb, indico_site, tmp_path):
    target = tmp_path / "site"
    rec = sources.add(kb, [f"{indico_site}/event/41/"], "indico", target=target)
    assert rec.data["source_type"] == "indico" and rec.data["service"] == indico_site
    assert rec.data["source_id"] == datasets.dataset_id(target)
    kb = Store.open(kb.root)
    assert datasets.locate(kb, kb.get(rec.id)) == target.resolve()
    log = subprocess.run(["git", "-C", str(target), "log", "--format=%s"], capture_output=True, text=True).stdout
    assert "[DATALAD RUNCMD] Ingest" in log           # datalad rerun repeats it
    # another event of the site goes into the same dataset, found by the source item
    again = sources.add(kb, [f"{indico_site}/event/42/"], "indico")
    assert again.id == rec.id
    pdf = next(target.glob("*/event/42/attachments/9/420/slides.pdf"))
    import datalad.api as dl
    dl.get(str(pdf), dataset=str(target), result_renderer="disabled")
    assert pdf.read_text() == "slides of meeting 42\n"
    # the dataset itself is recognized as an Indico source, not a plain DataLad one
    assert sources.guess([str(target)]).type == "indico"


@needs_indico
def test_shared_indico_owner_and_clone(tmp_path, indico_site):
    from conftest import make_store
    srv = tmp_path / "srv"
    srv.mkdir()
    owner = make_store(tmp_path / "owner-kb", "home", "owner")
    r = kb_cli(owner.root, "source", "add", "--type", "indico", "--shared", "--target", str(srv / "site"),
               f"{indico_site}/event/41/")
    assert r.exit_code == 0, r.output
    site = srv / "site"
    assert sharing.shared_config(site) == "group" and sharing.role(site) == "owner"
    assert mode(site / ".git") & stat.S_ISGID

    user = make_store(tmp_path / "user-kb", "home", "user")
    mine = tmp_path / "mine"
    r = kb_cli(user.root, "source", "add", "--shared", "--target", str(mine), str(site))
    assert r.exit_code == 0, r.output
    assert sharing.origin_of(mine) == site.resolve() and sharing.shared_config(mine) == "group"
    user = Store.open(user.root)
    rec = user.get(r.stdout.split()[0])
    assert rec.data["source_type"] == "indico" and datasets.locate(user, rec) == mine.resolve()
    pdf = next(mine.glob("*/event/41/attachments/9/410/slides.pdf"))
    import datalad.api as dl
    dl.get(str(pdf), dataset=str(mine), result_renderer="disabled")
    stored = pdf.resolve()
    assert stored.is_relative_to(site / ".git" / "annex") and mode(stored) & 0o040

    # a new event, added from the clone, is ingested into the owner's dataset and merged here
    r = kb_cli(user.root, "source", "add", "--type", "indico", f"{indico_site}/event/42/")
    assert r.exit_code == 0, r.output
    assert list(site.glob("*/event/42/event.json")) and list(mine.glob("*/event/42/event.json"))

    # unsharing: the clone gets its own annex; the owner's dataset stops being writable by the group
    r = kb_cli(user.root, "source", "share", "--unshare", str(mine))
    assert r.exit_code == 0, r.output
    assert not (mine / ".git" / "annex").is_symlink() and sharing.shared_config(mine) is None
    r = kb_cli(owner.root, "source", "share", "--unshare", str(site))
    assert r.exit_code == 0 and sharing.shared_config(site) is None and not mode(site / ".git") & 0o020
    r = kb_cli(owner.root, "source", "share", str(site))
    assert r.exit_code == 0 and sharing.shared_config(site) == "group" and mode(site / ".git") & 0o020


# -- DataLad sources ----------------------------------------------------------------------

@needs_datalad
def test_target_for_a_cloned_dataset(kb, tmp_path):
    import datalad.api as dl
    origin = tmp_path / "origin"
    dl.create(str(origin), result_renderer="disabled")
    rec = sources.add(kb, [origin.as_uri()], "datalad", target=tmp_path / "here")
    assert (tmp_path / "here" / ".datalad").is_dir()
    assert datasets.locate(Store.open(kb.root), rec) == (tmp_path / "here").resolve()
    with pytest.raises(KBError, match="--target is where a source made from a URL goes"):
        sources.add(kb, [str(origin)], "datalad", target=tmp_path / "elsewhere")


@needs_datalad
def test_shared_clone_needs_a_shared_dataset(kb, tmp_path):
    import datalad.api as dl
    origin = tmp_path / "origin"
    dl.create(str(origin), result_renderer="disabled")
    with pytest.raises(KBError, match="not shared; its owner can share it: kb source share"):
        sources.add(kb, [str(origin)], shared=True, target=tmp_path / "mine")
    kind, path, done = sources.share(kb, str(origin))
    assert kind == "datalad" and "set core.sharedRepository=group" in done
    rec = sources.add(kb, [str(origin)], shared=True, target=tmp_path / "mine")
    assert sharing.origin_of(tmp_path / "mine") == origin.resolve()
    assert rec.data["paths"] == [{"host": kb.cfg.host, "path": str(origin.resolve())}]


def test_filesystem_share(tmp_path):
    d = tmp_path / "talks"
    (d / "a").mkdir(parents=True)
    (d / "a" / "x.txt").write_text("x")
    (d / "a" / "x.txt").chmod(0o644)
    kind, path, done = sources.share(None, str(d), "filesystem")
    assert kind == "filesystem" and mode(d / "a" / "x.txt") == 0o664 and mode(d / "a") & stat.S_ISGID
    sources.share(None, str(d), "filesystem", unshare=True)
    assert mode(d / "a" / "x.txt") == 0o644


# -- kb config show -------------------------------------------------------------------------

def test_config_show_is_toml(root):
    r = kb_cli(root, "config", "show")
    assert r.stdout.startswith("# settings of the store test")
    shown = tomllib.loads(r.stdout)
    assert shown["kb"]["name"] == "test" and shown == json.loads(kb_cli(root, "config", "show", "--json").stdout)
