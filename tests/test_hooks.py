import json

from kb import hooks

from conftest import observation


def test_recall_finds_related_records(kb):
    a = observation(kb, title="Coherent noise in the induction plane",
                    summary="Induction plane coherent noise doubled after the grounding change.")
    observation(kb, title="Photon detector gain", summary="PDS gains drifted 3%.", subject=("detector/pds",))
    out = hooks.recall({"prompt": "Why is there so much coherent noise on the induction plane lately?",
                        "cwd": str(kb.root)})
    assert a.id in out and "PDS" not in out
    assert hooks.recall({"prompt": "hi", "cwd": str(kb.root)}) == ""
    assert hooks.recall({"prompt": "/kb-promote now please do it", "cwd": str(kb.root)}) == ""


def test_session_note(kb):
    observation(kb)
    out = hooks.session({"cwd": str(kb.root)})
    assert out.startswith("KB store test (other): 1 items, 0 topics.")


def test_run_never_raises(tmp_path):
    assert hooks.run("recall", "not json") == (0, "")
    assert hooks.run("session", json.dumps({"cwd": str(tmp_path)})) == (0, "")
    assert hooks.run("check", json.dumps({"tool_input": {"file_path": "/nonexistent/x"}})) == (0, "")
