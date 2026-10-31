import json
import shutil

import pytest

from kb import authoring, build, graph, lint
from kb.errors import KBError
from kb.records import KB

from conftest import observation, set_store

MAKEFILE = """\
all:
\tpython3 scripts/table.py > $(KB_OUT)/table.md
"""
SCRIPT = """\
import json, os
inputs = json.load(open(os.environ["KB_INPUTS"]))
for ref, item in sorted(inputs.items()):
    rec = json.load(open(item["record"]))
    print(f"| {rec['id']} | {rec['title']} |")
"""


def make_view(kb, inputs, tool="make", files=None, **build_kw):
    from kb.edit import checkin, checkout
    v = authoring.new(kb, "view", {"title": "Noise table", "summary": "Table of noise records.",
                                   "derived_from": inputs, "build": {"tool": tool, **build_kw}})
    d = checkout(kb, v.id)
    for name, text in (files or {"Makefile": MAKEFILE, "scripts/table.py": SCRIPT}).items():
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text)
    checkin(kb, d)
    return kb.get(v.id)


def edit_source(kb, v, name, text):
    from kb.edit import checkin, checkout
    d = checkout(kb, v.id)
    (d / name).write_text(text)
    checkin(kb, d)


def test_build_manifest_and_staleness(kb):
    o = observation(kb)
    v = make_view(kb, [o.id])
    assert build.view_staleness(kb, v) == ["never built"]
    man = build.build(kb, v.id)
    out = kb.cfg.build_dir / v.id / "out" / "table.md"
    assert o.id in out.read_text()
    assert man["inputs"] == [{"ref": o.id, "digest": kb.ref_digest(o.id)}]
    assert set(man["sources"]) == {"Makefile", "scripts/table.py"}
    v = kb.get(v.id)
    assert build.view_staleness(kb, v) == [] and build.read_manifest(v)["outputs"]

    edit_source(kb, v, "scripts/table.py", SCRIPT + "# tweak\n")
    v = kb.get(v.id)
    assert build.view_staleness(kb, v) == ["view sources changed since built"]
    build.build(kb, v.id)

    authoring.supersede(kb, o.id, {}, text="Corrected.")
    reasons = build.view_staleness(kb, KB.open(kb.root).get(v.id))
    assert any("superseded" in r for r in reasons)


def test_build_failure_is_reported(kb):
    v = make_view(kb, [], files={"Makefile": "all:\n\tfalse\n"})
    with pytest.raises(KBError, match="build.log"):
        build.build(kb, v.id)


def test_build_must_write_output(kb):
    v = make_view(kb, [], files={"Makefile": "all:\n\ttrue\n"})
    with pytest.raises(KBError, match="wrote nothing"):
        build.build(kb, v.id)


@pytest.mark.skipif(not shutil.which("pandoc"), reason="needs pandoc")
def test_pandoc_view(kb):
    o = observation(kb)
    v = make_view(kb, [o.id], tool="pandoc", target="index.md", formats=["html"],
                  files={"index.md": f"# Noise\n\nSee {o.id}.\n"})
    man = build.build(kb, v.id)
    assert list(man["outputs"]) == ["index.html"]


def test_command_view_gets_inputs(kb, tmp_path):
    f = tmp_path / "d.csv"
    f.write_text("run,rms\n1,2\n")
    d = authoring.add(kb, "dataset", [str(f)], {"title": "D", "summary": "Data."})
    v = authoring.new(kb, "view", {"title": "Copy", "summary": "Copies the data.",
                                   "derived_from": [f"{d.id}#original"],
                                   "build": {"tool": "command", "command": [
                                       "python3", "-c",
                                       "import json,shutil,sys; i=json.load(open(sys.argv[1])); "
                                       "shutil.copy(next(iter(i.values()))['renditions']['original'], sys.argv[2])",
                                       "{inputs}", "{out}/copy.csv"]}})
    build.build(kb, v.id)
    assert (kb.cfg.build_dir / v.id / "out" / "copy.csv").read_text() == f.read_text()


def test_publish_versions_and_lint(root):
    set_store(root)
    kb = KB.open(root)
    o = observation(kb)
    v = make_view(kb, [o.id])
    with pytest.raises(KBError, match="never been built"):
        build.publish(kb, v.id)
    build.build(kb, v.id)
    e1 = build.publish(kb, v.id)
    assert e1["url"].startswith("https://example.org/views/") and (kb.cfg.publish_path / v.id / "latest").is_symlink()
    assert json.loads((kb.cfg.publish_path / v.id / e1["version"] / "_kb_manifest.json").read_text())["view"] == v.id

    new = authoring.supersede(kb, o.id, {}, text="Corrected.")
    kb = KB.open(root)
    with pytest.raises(KBError, match="stale"):
        build.publish(kb, v.id)
    assert any(p.code == "published-stale" for p in lint.lint(kb))

    from kb.edit import set_fields
    set_fields(kb, v.id, replace_ref=[f"{o.id}={new.id}"])
    build.build(kb, v.id)
    e2 = build.publish(kb, v.id)
    assert e2["version"] != e1["version"] and (kb.cfg.publish_path / v.id / e1["version"]).exists()
    assert [p["version"] for p in build.publications(kb, kb.get(v.id))] == [e1["version"], e2["version"]]


def test_world_builds_in_dependency_order(kb):
    o = observation(kb)
    v1 = make_view(kb, [o.id])
    v2 = make_view(kb, [v1.id])
    report = build.world(kb)
    assert report["built"] == graph.topo_order(kb, [v1.id, v2.id]) == [v1.id, v2.id]
    assert build.world(kb)["built"] == []
