import copy
import json
import shutil

import pytest
from click.testing import CliRunner

from kb import assets, authoring, store
from kb.cli import main
from kb.errors import KBError
from kb.records import KB, Record

from conftest import make_store, observation


def write(d, files):
    d.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text)
    return d


V1 = {"slides.pdf": "slides, first upload", "notes.txt": "speaker notes"}
V2 = {"slides.pdf": "slides, corrected", "notes.txt": "speaker notes"}


@pytest.fixture
def talk(kb):
    return assets.new(kb, "fileset", {"title": "Noise talk materials", "summary": "Slides and notes of the noise talk."})


def labels(kb, ref):
    return [v["label"] for v in kb.get(ref).data["versions"]]


def test_flat_directory_versions_by_observation(kb, talk, tmp_path):
    cur = write(tmp_path / "current", V1)
    assets.bind(kb, talk.id, "dir", "fs", {"root": str(cur)})
    [s] = assets.scan(kb, talk.id)
    assert s.status == "new" and s.version is None
    assets.register(kb, talk.id, keep="no")
    assert labels(kb, talk.id) == ["v1"]
    assert [x.status for x in assets.scan(kb, talk.id)] == ["known"]
    write(cur, V2)                                         # overwritten in place
    [s] = assets.scan(kb, talk.id)
    assert s.status == "new"
    assets.register(kb, talk.id, keep="no")
    assert labels(kb, talk.id) == ["v1", "v2"]
    res = assets.resolve(kb, talk.id, "latest")
    assert res.version == "v2" and res.files["slides.pdf"].read_text() == "slides, corrected"
    with pytest.raises(KBError, match="no reachable source holds"):    # v1's bytes are gone from the directory
        assets.resolve(kb, talk.id, "v1")


def test_keeping_makes_versions_survive_overwrites(kb, talk, tmp_path):
    cur = write(tmp_path / "current", V1)
    assets.bind(kb, talk.id, "dir", "fs", {"root": str(cur)})
    done = assets.register(kb, talk.id)                    # unversioned source: kept by default
    assert done[0]["kept"]
    write(cur, V2)
    assets.register(kb, talk.id)
    res = assets.resolve(kb, talk.id, "v1")
    assert res.binding == "kb" and res.files["slides.pdf"].read_text() == "slides, first upload"


def test_pattern_versions_and_named_roots(root, tmp_path):
    hist = tmp_path / "mnt" / "eos" / "talks" / "noise"
    write(hist / "v1", V1)
    write(hist / "v2", V2)
    kb = KB.open(root)
    (root / ".kb").mkdir(exist_ok=True)
    (root / ".kb" / "local.toml").write_text(f'[filesystems.eos-group]\npath = "{tmp_path / "mnt" / "eos"}"\n')
    kb = KB.open(root)
    a = assets.new(kb, "fileset", {"title": "Talk", "summary": "Talk materials."})
    assets.bind(kb, a.id, "eos", "fs", {"root": "eos-group", "path": "talks/noise"},
                versioning="pattern", pattern="{version}")
    seen = assets.scan(kb, a.id)
    assert sorted((s.candidate.native, s.status, s.version) for s in seen) == [("v1", "new", "v1"), ("v2", "new", "v2")]
    assets.register(kb, a.id, keep="no")
    assert labels(kb, a.id) == ["v1", "v2"]
    assert assets.resolve(kb, a.id, "v1").files["slides.pdf"].read_text() == "slides, first upload"
    # The same binding on a machine where the root isn't mapped: unreachable, not an error.
    (root / ".kb" / "local.toml").write_text("")
    assert [s.status for s in assets.scan(KB.open(root), a.id)] == ["unreachable"]


def test_many_sources_one_version(kb, talk, tmp_path):
    """The scenario: an unversioned web source (Indico-like), copies kept as v1/, v2/ on disk, and a
    current directory. All are bound; resolution yields one verified file set per version."""
    web = write(tmp_path / "indico", V2)                      # Indico now serves the corrected slides
    hist = write(tmp_path / "copies" / "v1", V1).parent
    write(hist / "v2", V2)
    cur = write(tmp_path / "current", V2)
    assets.bind(kb, talk.id, "indico", "url",
                {"urls": {n: (web / n).as_uri() for n in V2}})
    assets.bind(kb, talk.id, "copies", "fs", {"root": str(hist)}, versioning="pattern", pattern="{version}")
    assets.bind(kb, talk.id, "current", "fs", {"root": str(cur)})
    seen = assets.scan(kb, talk.id)
    assets.register(kb, talk.id, seen, keep="no")
    assert labels(kb, talk.id) == ["v1", "v2"]               # labels from the versioned source
    seen = {(s.candidate.binding, s.candidate.native): s for s in assets.scan(kb, talk.id)}
    assert seen[("indico", None)].status == "known" and seen[("indico", None)].version == "v2"
    assert seen[("current", None)].version == "v2"
    v2 = next(v for v in kb.get(talk.id).data["versions"] if v["label"] == "v2")
    assert {s["binding"] for s in v2["seen"]} == {"copies", "indico", "current"}
    res = assets.resolve(kb, talk.id, "v2")
    assert res.binding == "copies"                           # fs before url, then binding order
    # Damage the preferred copy: it is divergent and skipped; another source supplies v2.
    (hist / "v2" / "slides.pdf").write_text("corrupted")
    res = assets.resolve(kb, talk.id, "v2")
    assert res.binding == "current" and any("divergent" in n for n in res.notes)
    assert res.files["slides.pdf"].read_text() == "slides, corrected"


def test_conflicts_and_ambiguity(kb, talk, tmp_path):
    hist = tmp_path / "copies"
    write(hist / "v1", V1)
    assets.bind(kb, talk.id, "copies", "fs", {"root": str(hist)}, versioning="pattern", pattern="{version}")
    assets.register(kb, talk.id, keep="no")
    write(hist / "v1", {"slides.pdf": "rewritten v1"})       # someone overwrote v1/ in place
    [s] = assets.scan(kb, talk.id)
    assert s.status == "conflict" and "differs from v1" in s.detail
    assert assets.register(kb, talk.id, keep="no") == []     # conflicts are reported, never registered
    # Two directories mapped to one label with different content: ambiguous within the source.
    other = tmp_path / "other"
    write(other / "final", V2)
    write(other / "final-2", {"slides.pdf": "something else"})
    b = assets.new(kb, "fileset", {"title": "B", "summary": "B."})
    assets.bind(kb, b.id, "o", "fs", {"root": str(other)}, versioning="map", pattern="{version}",
                mapping={"final": "v3", "final-2": "v3"})
    assert {s.status for s in assets.scan(kb, b.id)} == {"conflict"}


def test_registered_versions_are_frozen(kb, talk, tmp_path):
    cur = write(tmp_path / "c", V1)
    assets.bind(kb, talk.id, "dir", "fs", {"root": str(cur)})
    assets.register(kb, talk.id, keep="no")
    data = copy.deepcopy(kb.get(talk.id).data)
    data["versions"][0]["digest"] = "0" * 64
    with pytest.raises(KBError, match="append-only"):
        kb.save(Record(data, kb))
    data = copy.deepcopy(kb.get(talk.id).data)
    data["versions"] = []
    with pytest.raises(KBError, match="append-only"):
        kb.save(Record(data, kb))
    data = copy.deepcopy(kb.get(talk.id).data)          # adding versions and bindings is fine
    data["versions"].append({"label": "v9"})
    data["bindings"].append({"name": "w", "type": "url", "locator": {"urls": {}}})
    kb.save(Record(data, kb))


def test_match_content_ignores_renames(kb, tmp_path):
    a = assets.new(kb, "fileset", {"title": "A", "summary": "A."}, match="content")
    cur = write(tmp_path / "c", V1)
    assets.bind(kb, a.id, "dir", "fs", {"root": str(cur)})
    assets.register(kb, a.id, keep="no")
    (cur / "slides.pdf").rename(cur / "talk.pdf")
    assert [s.status for s in assets.scan(kb, a.id)] == ["known"]


def test_file_asset_and_renditions_pointing_at_it(kb, tmp_path):
    a = assets.new(kb, "file", {"title": "Slides", "summary": "The slides."})
    f = write(tmp_path / "d", {"slides.pdf": "pdf bytes"}) / "slides.pdf"
    v = assets.add_version(kb, a.id, f)
    assert v["label"] == "v1" and kb.get(a.id).data["versions"][0]["digest"] == store.sha256_file(f)[0]
    with pytest.raises(KBError, match="already version v1"):
        assets.add_version(kb, a.id, f)
    doc = authoring.new(kb, "document", {"title": "Talk", "summary": "A talk.", "doctype": "presentation",
                                         "date": "2026-10-28", "creators": ["A"], "renditions": [{
                                             "name": "original", "role": "original", "media_type": "application/pdf",
                                             "sha256": store.sha256_file(f)[0], "size": 9,
                                             "locations": [{"type": "asset", "asset": a.id, "version": "v1"}]}]})
    p = store.resolve(kb, doc, doc.rendition("original"))
    assert p.read_text() == "pdf bytes"
    from kb.render import Lookup, item_view
    v = item_view(doc, Lookup(kb), kb)
    assert any("asset" in w and "version v1" in w for r in v["renditions"] for w in r["where"])


def test_assets_travel_with_their_kept_content(kb, talk, tmp_path):
    from kb import bundle
    from kb.push import make_kset
    assets.add_version(kb, talk.id, write(tmp_path / "v", V1))
    other = make_store(tmp_path / "other", "group", "other")
    b = bundle.make(kb, make_kset(kb, [talk.id]).id)
    bundle.apply(other, b)
    res = assets.resolve(KB.open(other.root), talk.id, "v1")
    assert res.binding == "kb" and res.files["notes.txt"].read_text() == "speaker notes"
    # A second path brings a new version of the same asset: versions are united.
    assets.add_version(kb, talk.id, write(tmp_path / "v2", V2))
    bundle.apply(KB.open(other.root), bundle.make(KB.open(kb.root), make_kset(KB.open(kb.root), [talk.id]).id))
    assert labels(KB.open(other.root), talk.id) == ["v1", "v2"]


def test_planned_sources_say_so(kb, talk):
    assets.bind(kb, talk.id, "indico", "indico", {"server": "https://indico.example", "event": "123"})
    [s] = assets.scan(kb, talk.id)
    assert s.status == "unreachable" and "planned" in s.detail


def test_cli(root, tmp_path):
    run = lambda *a: CliRunner().invoke(main, ["-C", str(root), *a], catch_exceptions=False)  # noqa: E731
    aid = run("asset", "new", "--type", "fileset", "--title", "Talk").output.strip()
    hist = tmp_path / "h"
    write(hist / "v1", V1)
    write(hist / "v2", V2)
    run("asset", "bind", aid, "copies", "--source", "fs", "--set", f"root={hist}", "--versioning", "pattern",
        "--pattern", "{version}")
    out = run("asset", "scan", aid, "--register").output
    assert "registered v1" in out and "registered v2" in out
    assert "v2" in run("asset", "versions", aid).output
    d = tmp_path / "out"
    out = run("asset", "resolve", f"{aid}@v1", "--to", str(d)).output
    assert (d / "slides.pdf").read_text() == "slides, first upload" and "from copies" in out
    assert json.loads(run("asset", "resolve", aid, "--json").output)["version"] == "v2"
    r = run("render", aid).output
    assert "a file set, 2 version(s), latest v2" in r and "versions: {version}" in r
