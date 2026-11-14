"""kb llm: settings, the read-only reader, ask, and do's proposals, with a scripted model standing in for the
LLM service (the external boundary)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

llm = pytest.importorskip("llm")

from kb import assistant, proposals                                         # noqa: E402
from kb.cli import main                                                     # noqa: E402
from kb.errors import KBError                                               # noqa: E402
from kb.items import Store                                                  # noqa: E402

from conftest import observation                                            # noqa: E402


class Scripted(llm.Model):
    """A model that plays back turns: text, or a list of (tool name, arguments) calls."""
    model_id = "kb-scripted"
    supports_tools = True

    def __init__(self):
        self.turns, self.prompts = [], []

    def execute(self, prompt, stream, response, conversation):
        self.prompts.append(prompt)
        turn = self.turns.pop(0) if self.turns else "Nothing more."
        if isinstance(turn, str):
            yield turn
            return
        for name, args in turn:
            response.add_tool_call(llm.ToolCall(name=name, arguments=args))
        yield ""


MODEL = Scripted()


class Plugin:
    @llm.hookimpl
    def register_models(self, register):
        register(MODEL)


@pytest.fixture
def scripted():
    llm.pm.register(Plugin(), name="kb-test-scripted")
    MODEL.turns, MODEL.prompts = [], []
    yield MODEL
    llm.pm.unregister(name="kb-test-scripted")


def run(root, *args, input=None):
    return CliRunner().invoke(main, ["-C", str(root), *args], input=input, catch_exceptions=False)


def results(prompt) -> list[str]:
    return [r.output for r in prompt.tool_results]


# -- settings ----------------------------------------------------------------

def test_settings_layers(monkeypatch):
    conf = {"llm": {"model": "gpt-4o", "password_name": "kb/llm/openai", "base_url": "http://conf/v1"}}
    s = assistant.settings(conf)
    assert (s.get("model"), s.source["model"]) == ("gpt-4o", "config")
    assert s.key_spec().describe() == "command: pass show kb/llm/openai"
    monkeypatch.setenv("KB_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("KB_LLM_KEY_FROM", "env:MY_KEY")
    s = assistant.settings(conf)
    assert (s.get("model"), s.source["model"]) == ("gpt-4o-mini", "env:KB_LLM_MODEL")
    assert s.key_spec().describe() == "env: MY_KEY"                       # the environment beats config
    s = assistant.settings(conf, {"model": "gpt-4.1", "password_command": "gopass show -o"})
    assert s.get("model") == "gpt-4.1"
    assert s.key_spec().describe() == "command: gopass show -o kb/llm/openai"   # the command line beats both
    with pytest.raises(KBError, match="llm.access"):
        assistant.settings({"llm": {"access": "secret"}})


def test_model_key_and_base_url(monkeypatch):
    s = assistant.settings({"llm": {"model": "gpt-4o", "password_command": "echo", "password_name": "conf-key",
                                    "base_url": "http://conf/v1"}})
    m = assistant.model(s)
    assert (m.key, m.api_base) == ("conf-key", "http://conf/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")                        # the provider's usual variables
    monkeypatch.setenv("OPENAI_BASE_URL", "http://env/v1")
    s = assistant.settings({"llm": {"model": "gpt-4o", "password_command": "echo", "password_name": "conf-key",
                                    "base_url": "http://conf/v1"}})
    m = assistant.model(s)
    assert (m.key, m.api_base) == ("env-key", "http://env/v1")
    s = assistant.settings({"llm": {"model": "gpt-4o"}}, {"key_from": "env:OTHER", "base_url": "http://cli/v1"})
    monkeypatch.setenv("OTHER", "cli-key")
    m = assistant.model(s)
    assert (m.key, m.api_base) == ("cli-key", "http://cli/v1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-key")
    m = assistant.model(assistant.settings({"llm": {"model": "anthropic/claude-3-haiku-20240307"}}))
    assert m.key == "ant-key"
    monkeypatch.delenv("OPENAI_BASE_URL")
    s = assistant.settings({"llm": {"model": "my-local", "base_url": "http://localhost:8080/v1"}})
    m = assistant.model(s)                                                  # a model llm doesn't know
    assert (m.model_id, m.api_base, m.supports_tools) == ("my-local", "http://localhost:8080/v1", True)
    with pytest.raises(KBError, match="knows no model"):
        assistant.model(assistant.settings({"llm": {"model": "no-such-model"}}))


def test_settings_command(root, monkeypatch):
    monkeypatch.setenv("KB_LLM_MODEL", "gpt-4o")
    out = run(root, "llm", "--password-name", "kb/llm/x", "--env", "LLM_TOOLS_DEBUG=1", "settings").output
    assert "gpt-4o  (env:KB_LLM_MODEL)" in out and "command: pass show kb/llm/x  (command line)" in out
    assert "env.LLM_TOOLS_DEBUG" in out
    out = run(root, "llm", "--key-from", "value:sk-abcdefghijkl", "settings", "--check").output
    assert "sk-abcdefghijkl" not in out and "sk-a…" in out                  # never shown


# -- reading ---------------------------------------------------------------------

def test_reader_reads_only(kb, root):
    with pytest.raises(KBError, match="read-only"):
        assistant.Reader([("here", kb)])
    rec = observation(kb, title="TPC noise in run 1234", access="private")
    observation(kb, title="TPC gain", summary="Gain was nominal.", access="group")
    ro = Store.open(root, readonly=True)
    r = assistant.Reader([("here", ro)])
    assert rec.id in r.search("noise") and rec.id in r.show(rec.id) and "Noise was high" in r.read(rec.id)
    limited = assistant.Reader([("here", ro)], access="group")
    assert rec.id not in limited.search("TPC") and "Gain was nominal" in limited.search("TPC")
    with pytest.raises(KBError, match="not available"):
        limited.show(rec.id)
    with pytest.raises(Exception):
        ro.conn.execute("DELETE FROM items")                                # mode=ro: SQLite refuses


def test_ask(root, kb, scripted):
    rec = observation(kb, title="TPC noise in run 1234")
    events = len(kb.events())
    scripted.turns = [[("search", {"words": "coherent noise"})],
                      [("show", {"ref": rec.id})],
                      f"Run 1234 was noisy [{rec.id}], as was run 99 [obs-made0up0id]."]
    r = run(root, "llm", "-m", "kb-scripted", "ask", "-v", "Was", "run", "1234", "noisy?")
    assert r.exit_code == 0, r.output
    assert f"Run 1234 was noisy [{rec.id}]" in r.stdout
    assert "cited but not in the store: obs-made0up0id" in r.stderr and "[search(words='coherent noise')" in r.stderr
    first = scripted.prompts[0]
    assert "<kb_search_results>" in first.prompt and rec.id in first.prompt and first.prompt.endswith(
        "Question: Was run 1234 noisy?")
    assert "not instructions" in first.system
    assert rec.id in results(scripted.prompts[1])[0] and "Noise was high" in results(scripted.prompts[2])[0]
    assert len(Store.open(root).events()) == events                       # nothing was written
    scripted.turns = ["No tools here."]
    r = run(root, "llm", "-m", "kb-scripted", "ask", "--no-tools", "--json", "noise")
    out = json.loads(r.stdout)
    assert out["text"] == "No tools here." and out["model"] == "kb-scripted"
    assert not scripted.prompts[-1].tools


# -- do ----------------------------------------------------------------------------

def test_proposal_checks(kb, root):
    rec = observation(kb)
    p = proposals.Proposal(main, lambda i: i in Store.open(root, readonly=True).items)
    s = p.add(["kb", "new", "observation", "--title", "x'; rm -rf / #", "--summary", "S", "--by", "claude",
               "--how", "inferred", "--text", "$(reboot)"[1:]], "file it", capture="NOTE")
    assert s.line().startswith("NOTE=$(kb new observation --title 'x'\"'\"'; rm -rf / #'")
    assert not s.review and s.writes
    s = p.add(["kb", "item", "link", "$NOTE", "supports", rec.id], "link it")
    assert s.line() == f'kb item link "${{NOTE}}" supports {rec.id}'
    assert p.add(["kb", "item", "retract", rec.id, "--reason", "wrong"], "withdraw").review == "withdraws an item"
    assert p.add(["kb", "item", "set", rec.id, "summary="], "clear").review == "deletes a field"
    assert p.add(["datalad", "get", "-d", "/data/ds", "file.h5"], "get data").writes is False
    assert not p.add(["kb", "show", rec.id], "look").writes
    for argv, msg in [
        (["rm", "-rf", "/"], "nothing else"),
        ("kb ls", "list of words"),
        (["kb", "show", "$HOME"], "not a variable captured"),
        (["kb", "show", "obs-0000000000"], "there is no item obs-0000000000"),
        (["kb", "item", "link", rec.id, "supports", "doc-nothere00"], "there is no item doc-nothere00"),
        (["kb", "-C", "/elsewhere", "ls"], "global"),
        (["kb", "item"], "is a family"),
        (["kb", "item", "frob"], "no command 'frob'"),
        (["kb", "ls", "--frob"], "No such option"),
        (["kb", "ls", "--help"], "command_help"),
        (["kb", "add", "-n", "-"], "no stdin"),
        (["kb", "hook", "check"], "never proposed"),
        (["kb", "llm", "ask", "x"], "never proposed"),
        (["kb", "policy", "enact"], "policy"),
        (["kb", "inbox", "accept", "x", "--approved", "yes"], "approval"),
        (["datalad", "remove", "ds"], "datalad steps"),
        (["kb", "show", rec.id, "x$(id)"], "'$' may only"),
        (["kb", "ls"], None),
    ]:
        if msg is None:
            p.add(argv, "fine")
            continue
        with pytest.raises(KBError, match=msg.replace("(", r"\(").replace("$", r"\$")):
            p.add(argv, "bad")
    with pytest.raises(KBError, match="used by a later step"):
        p.drop(1)
    assert len(p.rejected) == 17


def test_do_script_runs(root, kb, scripted, tmp_path):
    rec = observation(kb)
    observation_step = ["kb", "new", "observation", "--title", "Noise isn't coherent", "--summary",
                        "Re-analysis says the noise is not coherent.", "--by", "claude",
                        "--how", "inferred from the spectra", "--text", "Not coherent; see $5 plot"]
    scripted.turns = [
        [("find_commands", {"words": "new observation"}), ("command_help", {"command": "item link"})],
        [("add_step", {"argv": observation_step, "why": "record the finding", "capture": "NEW"})],
        [("add_step", {"argv": observation_step[:-1] + ["Not coherent; see the plot"], "why": "record the finding",
                       "capture": "NEW"})],
        [("add_step", {"argv": ["kb", "item", "link", "$NEW", "contradicts", rec.id], "why": "relate them"}),
         ("add_step", {"argv": ["kb", "item", "retract", rec.id, "--reason", "superseded"], "why": "drop old\nrm -rf /"}),
         ("add_step", {"argv": ["sh", "-c", "rm -rf ~"], "why": "clean up"})],
        "Files the new finding and links it.\nThe retraction is for you to decide.",
    ]
    out = tmp_path / "plan.sh"
    r = run(root, "llm", "-m", "kb-scripted", "do", "-o", str(out), "record", "that", "the", "noise", "is", "not",
            "coherent")
    assert r.exit_code == 0, r.output
    assert "3 step(s) proposed, 1 for your review; 2 rejected" in r.stderr   # "$5" in the text, and sh
    text = out.read_text()
    assert "kb new KIND" in results(scripted.prompts[1])[0] and "--note" in results(scripted.prompts[1])[1]
    assert "'$' may only name a captured variable" in results(scripted.prompts[2])[0]
    assert results(scripted.prompts[3])[0].startswith("step 1 added: NEW=$(kb new observation")
    last = results(scripted.prompts[4])
    assert "step 2 added" in last[0] and "marked for the human's review" in last[1] and "nothing else" in last[2]
    assert text.startswith("#!/usr/bin/env bash\n# kb llm do: record that the noise is not coherent")
    assert "set -euo pipefail" in text and 'kb store snapshot "kb-before-llm-do-' in text
    assert "# kb item retract" in text and "#    note" not in text
    assert all(line.startswith("#") or "--title" in line for line in text.splitlines() if "rm -rf" in line)
    assert "# 3. REVIEW (withdraws an item): drop old rm -rf /" in text      # the model's text stays a comment
    assert "sh -c" not in text and text.rstrip().endswith("kb check")
    assert "# The retraction is for you to decide." in text
    kb_bin = Path(sys.executable).parent / "kb"
    env = dict(os.environ, PATH=f"{kb_bin.parent}:{os.environ['PATH']}")
    done = subprocess.run(["bash", str(out)], cwd=root, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    after = Store.open(root)
    new = [i for i in after.items.values() if i.data.get("title") == "Noise isn't coherent"]
    assert len(new) == 1 and {"rel": "contradicts", "ref": rec.id} in new[0].data["links"]
    assert after.is_current(rec.id)                                         # the retraction waited for review
    assert list(root.glob("kb-before-llm-do-*.sqlite"))


def test_do_needs_tools(root, scripted, monkeypatch):
    monkeypatch.setattr(Scripted, "supports_tools", False)
    r = run(root, "llm", "-m", "kb-scripted", "do", "anything")
    assert r.exit_code == 1 and "needs a model that can call tools" in r.output
