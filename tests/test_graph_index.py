import pytest

from kb import authoring, context, graph, index, search
from kb.errors import KBError

from conftest import observation


def topic(kb, inputs, text="Summary page."):
    return authoring.new(kb, "topic", {"title": "TPC noise", "summary": "What we know about TPC noise.",
                                       "subject": ["detector/tpc/noise"], "derived_from": inputs}, text=text)


def test_stamp_and_staleness_propagation(kb):
    o = observation(kb)
    t = topic(kb, [o.id], f"Run 1234 was noisy [{o.id}].")
    assert [s.reasons for s in graph.stale(kb)] == [[f"input {o.id} not stamped"]]
    graph.stamp(kb, t.id)
    assert graph.stale(kb) == []

    fixed = authoring.supersede(kb, o.id, {}, text="Run 1243, not 1234.")
    stale = graph.stale(kb)
    assert len(stale) == 1 and stale[0].id == t.id and "superseded" in stale[0].reasons[0]
    assert graph.rdeps(kb, o.id) == [t.id]

    from kb.edit import set_content, set_fields
    set_fields(kb, t.id, replace_ref=[f"{o.id}={fixed.id}"])
    set_content(kb, t.id, f"Run 1243 was noisy [{fixed.id}].\n")
    assert "not stamped" in " ".join(graph.topic_staleness(kb, kb.get(t.id)))
    graph.stamp(kb, t.id)
    assert graph.stale(kb) == []


def test_status_change_makes_dependents_stale_but_tags_do_not(kb):
    from kb.edit import set_fields
    o = observation(kb)
    t = topic(kb, [o.id])
    graph.stamp(kb, t.id)
    set_fields(kb, o.id, ["tags=[\"x\"]", "summary=Reworded."])
    assert graph.stale(kb) == []
    set_fields(kb, o.id, ["status=disputed"])
    assert [s.id for s in graph.stale(kb)] == [t.id]


def test_stamp_refuses_sources(kb):
    o = observation(kb)
    with pytest.raises(KBError, match="frozen"):
        graph.stamp(kb, o.id)


def test_topo_order_and_dag(kb):
    o = observation(kb)
    t1 = topic(kb, [o.id])
    t2 = topic(kb, [t1.id])
    assert graph.topo_order(kb, [t2.id, t1.id, o.id]) == [o.id, t1.id, t2.id]
    assert graph.deps(kb, t2.id, transitive=True) == [t1.id, o.id]
    dot = graph.dag(kb, "dot")
    assert f'"{t2.id}" -> "{t1.id}"' in dot


def test_index_ls_and_search(kb):
    a = observation(kb, runs=("1200-1300",), title="Noisy period", text="Coherent noise on the induction plane.")
    b = observation(kb, runs=("2000",), subject=("detector/pds",), title="PDS gain drift",
                    summary="Photon detector gains drifted by 3%.", text="Gain drift observed.")
    authoring.supersede(kb, b.id, {}, text="Drift was 2%.")
    index.build(kb)
    assert [r["id"] for r in index.ls(kb, subject="detector/tpc")] == [a.id]
    assert [r["id"] for r in index.ls(kb, runs=[1250, 1250])] == [a.id]
    assert index.ls(kb, runs=[1301, 1999]) == []
    assert b.id not in [r["id"] for r in index.ls(kb)]
    assert b.id in [r["id"] for r in index.ls(kb, include_old=True)]
    hits = search.search(kb, "induction")
    assert [h["id"] for h in hits] == [a.id] and "[induction]" in hits[0]["snip"]
    assert search.search(kb, "induct*")[0]["id"] == a.id
    assert search.search(kb, 'E=mc^2 "quoted"') == []  # punctuation is not syntax


def test_query_tables_stay_in_step(kb):
    from kb.edit import set_fields
    o = observation(kb)
    observation(kb, title="Another")
    assert len(index.ls(kb)) == 2
    set_fields(kb, o.id, add=["subject=detector/daq"])
    assert [r["id"] for r in index.ls(kb, subject="detector/daq")] == [o.id]
    assert index.build(kb) == 2 and len(index.ls(kb)) == 2


def test_context_pack(kb):
    o = observation(kb, text="Noise detail.")
    t = topic(kb, [o.id], f"# TPC noise\n\nRun 1234 was noisy [{o.id}].")
    graph.stamp(kb, t.id)
    old = observation(kb, runs=("1100",), title="Old claim")
    authoring.retract(kb, old.id, "Was a typo.", {})
    out = context.context(kb, "detector/tpc")
    assert out.index("## Topics") < out.index("## Current records") < out.index("## Superseded")
    assert f"Run 1234 was noisy [{o.id}]" in out
    one = context.context(kb, o.id)
    assert "## Used by" in one and t.id in one
    small = context.context(kb, "detector/tpc", budget=300)
    assert "budget" in small and len(small) < 600
    assert "Found by text search" in context.context(kb, "coherent")
