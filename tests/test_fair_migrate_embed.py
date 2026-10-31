import hashlib
import json

import pytest

from kb import authoring, embed, fair, migrate, search, store
from kb.errors import KBError
from kb.records import KB

from conftest import observation


def test_rocrate(kb, tmp_path):
    authoring.new(kb, "person", {"title": "Ann", "summary": "Ann.", "name": "Ann Example"}, slug="ann")
    o = observation(kb, by="per-ann")
    f = tmp_path / "d.csv"
    f.write_text("a\n1\n")
    d = authoring.add(kb, "dataset", [str(f)], {"title": "D", "summary": "Data.", "license": "CC-BY-4.0"})
    meta = fair.rocrate(kb, [o.id, d.id], tmp_path / "crate")
    crate = json.loads(meta.read_text())
    graph = {e["@id"]: e for e in crate["@graph"]}
    assert graph[f"#{d.id}"]["license"] == "CC-BY-4.0"
    assert graph["#per-ann"]["name"] == "Ann Example"
    assert (tmp_path / "crate" / d.id / "d.csv").exists()
    assert graph[f"{d.id}/d.csv"]["sha256"] == store.sha256_file(f)[0]


def test_datapackage(kb, tmp_path):
    f = tmp_path / "noise.csv"
    f.write_text("run,rms,ok,date\n1234,3.1,true,2026-10-01\n1235,2.9,false,2026-10-02\n")
    d = authoring.add(kb, "dataset", [str(f)], {"title": "Noise", "summary": "RMS per run."})
    pkg = fair.datapackage(kb, d.id)
    types = {fl["name"]: fl["type"] for fl in pkg["resources"][0]["schema"]["fields"]}
    assert types == {"run": "integer", "rms": "number", "ok": "boolean", "date": "date"}
    assert KB.open(kb.root).get(d.id).rendition("datapackage")["role"] == "metadata"
    from kb import checks
    assert checks.check(KB.open(kb.root)) == []


def test_migration_chain(kb):
    o = observation(kb)
    fake = {1: lambda d: {**d, "x": {"migrated": True}}}
    up = migrate.upgrade(dict(o.data), to=2, migrations=fake)
    assert up["schema_version"] == 2 and up["x"] == {"migrated": True}
    with pytest.raises(KBError, match="no migration"):
        migrate.upgrade(dict(o.data), to=3, migrations=fake)
    assert migrate.migrate(kb, to=1) == []


class FakeEmbedder:
    """Bag-of-words vectors: deterministic and meaning-ish, with no model."""
    identity = "fake"
    vocab = ["noise", "gain", "drift", "photon", "tpc", "coherent", "detector", "pds"]

    def __call__(self, texts):
        return [[t.lower().count(w) + 0.01 for w in self.vocab] for t in texts]


def test_embed_and_semantic_search(kb):
    a = observation(kb, title="Coherent noise", text="coherent noise in the tpc")
    b = observation(kb, title="PDS gain", summary="Photon detector gain drift.", subject=("detector/pds",),
                    runs=("2000",), text="photon gain drift")
    e = FakeEmbedder()
    r = embed.embed(kb, e)
    assert r["embedded"] == r["total"] > 0
    assert embed.embed(kb, e)["embedded"] == 0  # unchanged text is not re-embedded
    assert embed.semantic(kb, "gain drift of photon detectors", embedder=e)[0][0] == b.id
    hy = search.search(kb, "coherent noise", mode="hybrid", embedder=e)
    assert hy[0]["id"] == a.id


def test_embedder_against_http(kb):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            data = [{"index": i, "embedding": [len(t), 1.0]} for i, t in enumerate(body["input"])]
            out = json.dumps({"data": data}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        e = embed.Embedder(f"http://127.0.0.1:{srv.server_port}/v1", "m")
        assert e(["ab", "abc"]) == [[2, 1.0], [3, 1.0]]
    finally:
        srv.shutdown()


def test_chunks():
    text = "\n\n".join(["p" * 600] * 5)
    cs = embed.chunks(text, 1500)
    assert all(len(c) <= 1500 for c in cs) and "".join(cs).count("p") == 3000


def test_verify_detects_upstream_change(kb, tmp_path):
    f = tmp_path / "slides.md"
    f.write_text("v1")
    rec = authoring.add(kb, "document", [f.as_uri()], {"title": "T", "summary": "S", "doctype": "presentation",
                                                       "date": "2026", "creators": ["A"]})
    assert [i["result"] for i in store.verify(kb, [rec])] == ["ok"]
    f.write_text("v2: re-uploaded")
    out = store.verify(kb, [rec])
    assert out[0]["result"] == "changed" and "--supersedes" in out[0]["detail"]


def test_verify_records_missing_hash(kb, tmp_path):
    f = tmp_path / "p.md"
    f.write_text("paper")
    rec = authoring.add(kb, "document", [f.as_uri()], {"title": "T", "summary": "S", "doctype": "paper",
                                                       "date": "2026", "creators": ["A"]}, fetch=False)
    assert store.verify(kb, [rec])[0]["result"] == "unhashed"
    store.verify(kb, [rec], record=True)
    assert KB.open(kb.root).get(rec.id).rendition("original")["sha256"] == hashlib.sha256(b"paper").hexdigest()
