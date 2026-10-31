import json
import re

import pytest
from click.testing import CliRunner

from kb import authoring, bundle, render
from kb.cli import main
from kb.errors import KBError
from kb.push import make_kset
from kb.items import Store

from conftest import make_store, observation


@pytest.fixture
def material(kb, tmp_path):
    """A small body of knowledge: a person, a talk with extracted text, observations, a topic, nested sets."""
    authoring.new(kb, "person", {"title": "Ann", "summary": "Ann Example, TPC group.", "name": "Ann Example",
                                 "affiliation": "Example Lab"}, slug="ann")
    f = tmp_path / "talk.md"
    f.write_text("## Slide 1: Noise update\n- RMS up 20% in runs 1200-1300\n## Slide 2: Grounding\n- Fix proposed\n")
    doc = authoring.add(kb, "document", [str(f)], {
        "title": "TPC noise update", "summary": "Noise rose 20% in runs 1200-1300; a grounding fix is proposed.",
        "doctype": "presentation", "date": "2026-10-28", "creators": ["per-ann"],
        "identifiers": {"arxiv": "2601.01234", "indico": "event/12345/contribution/7"},
        "event": {"title": "Collaboration meeting", "date": "2026-10-28"}, "subject": ["detector/tpc/noise"]},
        url="https://indico.example/event/12345/attachments/talk.pdf")
    o1 = observation(kb, by="per-ann", runs=("1243",), title="Run 1243 was the worst",
                     summary="Coherent noise peaked in run 1243.", evidence=[f"{doc.id}#original"],
                     text="Coherent noise peaked in run 1243 (30% above baseline).")
    o2 = observation(kb, runs=("1200-1300",), title="Grounding fix scheduled", summary="Fix goes in at the next access.")
    top = authoring.new(kb, "topic", {"title": "TPC noise", "summary": "What we know about TPC noise.",
                                      "subject": ["detector/tpc/noise"], "derived_from": [o1.id, o2.id]},
                        text=f"# TPC noise\n\nRun 1243 was worst [{o1.id}]; a fix is scheduled [{o2.id}].\n")
    inner = make_kset(kb, [o1.id, o2.id], "shallow", title="The observations")
    outer = make_kset(kb, [top.id, inner.id, doc.id], "shallow", title="TPC noise findings")
    return {"doc": doc, "o1": o1, "o2": o2, "top": top, "inner": inner, "outer": outer}


def test_bundle_round_trip_and_integrity(kb, material, tmp_path):
    b = bundle.make(kb, material["outer"].id, note="for the group")
    ids = {i["data"]["id"] for i in b["items"]}
    assert ids == {material[k].id for k in ("top", "inner", "doc", "o1", "o2")}
    p = bundle.write(b, tmp_path)
    assert p.name.endswith(bundle.SUFFIX) and bundle.read(p)["id"] == b["id"]
    tampered = json.loads(p.read_text())
    tampered["items"][0]["data"]["title"] = "Something else"
    p.write_text(json.dumps(tampered))
    with pytest.raises(KBError, match="checksum"):
        bundle.read(p)


def test_bundle_check_against_another_store(kb, material, tmp_path):
    other = make_store(tmp_path / "other", "group", "other")
    b = bundle.make(kb, material["outer"].id)
    out = bundle.check(other, b)
    assert out["ok"] and len(out["plan"]["new"]) == 6 and out["plan"]["conflict"] == []
    rep = bundle.apply(other, b)
    assert set(rep["items"]["new"]) == set(out["plan"]["new"])
    assert Store.open(other.root).get(material["o1"].id).text("note").startswith("Coherent noise peaked")


def test_render_is_for_people(kb, material, tmp_path):
    other = make_store(tmp_path / "other", "group", "other")
    other.set_setting("links", {"indico": "https://indico.example"})
    b = bundle.make(kb, material["outer"].id, note="Please review before Thursday")
    text = render.bundle_text(render.bundle_view(b, Store.open(other.root)))
    assert "from test" in text and "Please review before Thursday" in text
    assert "What accepting it would do" in text and "6 new" in text
    assert "Ann Example" in text                                  # person resolved, not per-ann
    assert "28 Oct 2026" in text                                  # dates for people
    assert "https://arxiv.org/abs/2601.01234" in text             # identifiers become links
    assert "https://indico.example/event/12345/contribution/7" in text
    assert "https://indico.example/event/12345/attachments/talk.pdf" in text
    assert "Coherent noise peaked in run 1243" in text            # an excerpt of the material
    assert "Slide 1: Noise update" in text
    assert "run 1243" in text and "runs 1200–1300" in text
    assert not re.search(r"\b[0-9a-f]{64}\b", text)               # no hashes
    tree = text[text.index("Contents"):]
    assert "TPC noise findings" in tree and "└─" in tree and "The observations" in tree
    md = render.bundle_text(render.bundle_view(b, Store.open(other.root)), md=True)
    assert md.startswith("# Bundle") and "[arxiv](https://arxiv.org/abs/2601.01234)" in md
    v = json.loads(render.to_json(render.bundle_view(b)))         # a view model for programs and LLMs
    assert v["tree"]["children"][1]["children"][0]["id"] == material["o1"].id


def test_render_changes_and_conflicts(kb, material, tmp_path):
    from kb.edit import set_fields
    other = make_store(tmp_path / "other", "group", "other")
    bundle.apply(other, bundle.make(kb, material["outer"].id))
    set_fields(kb, material["o2"].id, ["status=disputed"])
    ks = make_kset(Store.open(kb.root), [material["o2"].id])
    v = render.bundle_view(bundle.make(Store.open(kb.root), ks.id), Store.open(other.root))
    changed = [g for g in v["groups"] if g["action"] == "update"][0]["items"][0]
    assert any("status: unverified → disputed" in c for c in changed["changes"])


def test_review_flow_with_llm_and_human(kb, material, tmp_path, monkeypatch):
    group = make_store(tmp_path / "group", "group", "grp")
    b = bundle.make(kb, material["outer"].id)
    e = bundle.receive(group, b)
    assert e.status == "pending" and bundle.receive(group, b).bundle == e.bundle   # receiving twice is a no-op
    monkeypatch.setenv("KB_AGENT", "1")
    bundle.review(Store.open(group.root), e.bundle, "accept", "Attributed, cited, no conflicts.")
    with pytest.raises(KBError, match="reserved for the human"):                     # group: accept = never
        bundle.accept(Store.open(group.root), e.bundle, approved="the human said ok")
    monkeypatch.delenv("KB_AGENT")
    g = Store.open(group.root)
    bundle.review(g, e.bundle, "comment", "Agree with Claude.")
    assert [(r["reviewer"], r["reviewer_kind"]) for r in bundle.get(g, e.bundle).reviews] == \
        [("claude", "llm"), ("per-tester", "human")]
    bundle.accept(g, e.bundle)
    assert material["top"].id in Store.open(group.root).items
    assert [ev["action"] for ev in Store.open(group.root).events(material["outer"].id)][:2] == \
        ["inbox-receive", "review"]


def test_cli_bundle_and_review(kb, material, tmp_path):
    def run(root, *args):
        return CliRunner().invoke(main, ["-C", str(root), *args], catch_exceptions=False)
    group = make_store(tmp_path / "group", "group", "grp")
    out = run(kb.root, "bundle", "make", material["outer"].id, "-o", str(tmp_path)).output
    path = out.strip().splitlines()[0]
    assert "TPC noise findings" in run(group.root, "bundle", "show", path).output
    assert run(group.root, "bundle", "check", path).exit_code == 0
    run(group.root, "inbox", "receive", path)
    listing = run(group.root, "inbox", "list").output
    assert "pending" in listing and material["outer"].id in listing
    ref = listing.split()[0]
    run(group.root, "review", "note", ref, "--verdict", "accept", "--text", "Looks right.")
    shown = run(group.root, "review", "show", ref).output
    assert "Looks right." in shown and "What accepting it would do" in shown
    assert run(group.root, "review", "accept", ref).exit_code == 0
    assert "accepted" in run(group.root, "inbox", "list", "--status", "all").output
    rendered = run(kb.root, "render", material["outer"].id).output
    assert "The observations" in rendered and "└─" in rendered
    assert "transfer-in" in run(group.root, "log", material["o1"].id).output


def test_cli_config(root):
    run = lambda *a: CliRunner().invoke(main, ["-C", str(root), *a], catch_exceptions=False)  # noqa: E731
    run("config", "set", "cas.default", '{"path": "/tmp/somewhere"}')
    assert json.loads(run("config", "get", "cas").output) == {"default": {"path": "/tmp/somewhere"}}
    assert Store.open(root).cfg.cas["default"].as_posix() == "/tmp/somewhere"
    r = CliRunner().invoke(main, ["-C", str(root), "config", "set", "autonomy", "{}"])
    assert r.exit_code != 0 and "managed by kb policy" in r.output
