import json

from click.testing import CliRunner

from kb.cli import main



def run(root, *args, input=None):
    r = CliRunner().invoke(main, ["-C", str(root), *args], input=input, catch_exceptions=False)
    return r


def test_end_to_end(root, tmp_path):
    r = run(root, "new", "observation", "--title", "Noisy run 1234", "--summary", "Coherent noise high.",
            "--subject", "detector/tpc/noise", "--runs", "1234", "--by", "A. Colleague", "--how", "verbal",
            "--text", "Noise high.")
    assert r.exit_code == 0, r.output
    oid = r.output.split()[0]
    assert run(root, "check").exit_code == 0
    assert oid in run(root, "ls").output
    assert "detector/tpc/noise" in run(root, "ls", "--subjects").output
    assert oid in run(root, "search", "coherent").output
    assert "## Current items" in run(root, "context", "detector/tpc").output
    r = run(root, "item", "supersede", oid, "--runs", "1243", "--text", "Was 1243.")
    nid = r.output.split()[0]
    assert oid not in run(root, "ls").output and nid in run(root, "ls").output
    assert json.loads(run(root, "show", nid, "--json").output)["supersedes"] == [oid]
    r = run(root, "new", "topic", "--title", "Noise", "--summary", "About noise.", "--subject", "detector/tpc",
            "--derived-from", nid, "--text", f"Run 1243 [{nid}].")
    tid = r.output.split()[0]
    assert run(root, "item", "stale").output == ""                 # made from its inputs as they are: pinned
    r = run(root, "item", "supersede", nid, "--runs", "1244", "--text", "Was 1244.")
    assert "topic" in run(root, "item", "stale").output             # an input was corrected
    newest = r.output.split()[0]
    run(root, "item", "set", tid, "--replace-ref", f"{nid}={newest}")
    run(root, "item", "stamp", tid)
    assert run(root, "item", "stale").output == ""
    assert run(root, "item", "deps", "-r", newest).output.startswith(tid)


def test_errors_are_one_line(root):
    r = CliRunner().invoke(main, ["-C", str(root), "show", "obs-0000000000"])
    assert r.exit_code == 1 and "no item obs-0000000000" in r.output and "Traceback" not in r.output


def test_check_fails_with_exit_1(root):
    run(root, "new", "observation", "--title", "T", "--summary", "S", "--by", "x", "--text", "t")
    from kb.items import Store
    kb = Store.open(root)
    kb.conn.execute("UPDATE items SET json = replace(json, '\"T\"', '\"Changed\"')")
    r = run(root, "check")
    assert r.exit_code == 1 and "[tamper]" in r.output


def test_checkout_edit_checkin_and_hook(root, tmp_path):
    r = run(root, "new", "topic", "--title", "Noise", "--summary", "About noise.", "--subject", "a",
            "--derived-from", "obs-0000000000", "--text", "Old page.")
    assert r.exit_code != 0                                  # a reference found nowhere is refused
    oid = run(root, "new", "observation", "--title", "T", "--summary", "S", "--by", "x", "--text", "t").output.split()[0]
    tid = run(root, "new", "topic", "--title", "Noise", "--summary", "About noise.", "--subject", "a",
              "--derived-from", oid, "--text", "Old page.").output.split()[0]
    d = tmp_path / "co"
    assert run(root, "item", "checkout", tid, str(d)).exit_code == 0
    (d / "page.md").write_text("New page.\n")
    data = json.loads((d / "item.json").read_text())
    data["summary"] = "Better summary of noise."
    (d / "item.json").write_text(json.dumps(data))
    event = {"tool_name": "Edit", "tool_input": {"file_path": str(d / "item.json")}}
    assert CliRunner().invoke(main, ["hook", "check"], input=json.dumps(event)).exit_code == 0
    out = json.loads(run(root, "item", "checkin", str(d)).output)
    assert out["content"] == ["page.md"] and out["fields"] == ["summary"]
    assert run(root, "item", "content", tid).output == "New page.\n"
    # A frozen field changed in a checkout: the hook reports it to Claude (exit 2), checkin refuses.
    d2 = tmp_path / "co2"
    run(root, "item", "checkout", oid, str(d2))
    data = json.loads((d2 / "item.json").read_text())
    data["asserted_by"] = [{"who": "someone else"}]
    (d2 / "item.json").write_text(json.dumps(data))
    event = {"tool_name": "Edit", "tool_input": {"file_path": str(d2 / "item.json")}}
    r = CliRunner().invoke(main, ["hook", "check"], input=json.dumps(event))
    assert r.exit_code == 2 and "frozen" in r.output
    assert run(root, "item", "checkin", str(d2)).exit_code == 1


def test_new_from_json_stdin(root):
    payload = {"title": "Sim drift", "summary": "Drift velocity used in 2025 production.",
               "system": "production/sim", "valid": {"time": {"start": "2025-01-01", "end": "2025-12-31"}},
               "parameters": {"drift_mm_us": 1.6}}
    r = run(root, "new", "config", "--from-json", "-", "--json", input=json.dumps(payload))
    assert json.loads(r.output)["parameters"] == {"drift_mm_us": 1.6}


def test_add_and_path(root, tmp_path):
    f = tmp_path / "d.csv"
    f.write_text("x\n1\n")
    r = run(root, "add", str(f), "--kind", "dataset", "--title", "D", "--summary", "Data.")
    did = r.output.split()[0]
    assert run(root, "rendition", "path", f"{did}#original").output.strip().endswith("d.csv")
    assert json.loads(run(root, "data", "describe", did).output)["resources"][0]["path"] == "d.csv"


def test_schema_and_transforms_listing(root):
    assert "observation" in run(root, "item", "schema").output
    assert json.loads(run(root, "item", "schema", "observation").output)["title"] == "Observation"
    assert "pdftotext" in run(root, "rendition", "transforms").output


def test_init_scopes_and_policy(tmp_path):
    r = CliRunner().invoke(main, ["init", str(tmp_path / "new"), "--scope", "group"])
    assert r.exit_code == 0, r.output
    conf = json.loads(CliRunner().invoke(main, ["-C", str(tmp_path / "new"), "config", "get", "kb"]).output)
    assert conf["scope"] == "group" and conf["access"] == "group" and conf["id"].startswith("kb-")
    settings = json.loads((tmp_path / "new" / ".claude" / "settings.json").read_text())
    assert settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "kb hook check"
    assert (tmp_path / "new" / ".claude" / "skills" / "kb-curate" / "SKILL.md").exists()
    assert "<!-- kb:policy kb-core@" in (tmp_path / "new" / "CLAUDE.md").read_text()
    r = CliRunner().invoke(main, ["-C", str(tmp_path / "new"), "policy", "status"])
    assert "enacted and current" in r.output


def test_cli_reference_in_sync():
    import importlib.util
    from pathlib import Path
    here = Path(__file__).parent.parent
    spec = importlib.util.spec_from_file_location("gen_cli_doc", here / "tools" / "gen_cli_doc.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (here / "docs" / "reference" / "cli.md").read_text() == mod.render(), \
        "regenerate with: uv run python tools/gen_cli_doc.py"


def test_add_store_modes(root, tmp_path):
    f = tmp_path / "n.txt"
    f.write_text("small")
    r = run(root, "add", str(f), "--kind", "dataset", "--title", "N", "--summary", "S", "--place", "db")
    assert r.exit_code == 0, r.output
    r = run(root, "add", str(f), "--kind", "dataset", "--title", "N2", "--summary", "S", "--place", "local")
    assert r.exit_code == 0, r.output


def test_command_tree_shape():
    top = CliRunner().invoke(main, ["--help"]).output
    assert top.index("Everyday:") < top.index("Commands by thing") < top.index("Integration:")
    assert "hook " not in top                                        # internal entry points stay hidden
    import click
    from kb.cli import SECTIONS
    listed = {n for _, names in SECTIONS for n in names}
    assert set(main.commands) - listed == {"hook"}                   # every command has a section
    for name, cmd in main.commands.items():
        if isinstance(cmd, click.Group) and not cmd.hidden:
            r = CliRunner().invoke(main, [name])
            assert "Commands:" in r.output, name                       # a family lists itself


def test_show_formats(root):
    oid = run(root, "new", "observation", "--title", "T", "--summary", "S", "--by", "x", "--text", "Noise.").output.split()[0]
    assert "Noise." in run(root, "show", oid, "--format", "llm").output
    assert json.loads(run(root, "show", oid, "--json").output)["id"] == oid
    assert json.loads(run(root, "show", oid, "--format", "view").output)["title"] == "T"
    assert "T" in run(root, "show", oid).output


def test_data_describe_files_and_sources(root, tmp_path):
    f = tmp_path / "gains.tsv"
    f.write_text("ch\tgain\n1\t1.5\n")
    pkg = json.loads(run(root, "data", "describe", str(f)).output)
    assert pkg["resources"][0]["schema"]["fields"] == [{"name": "ch", "type": "integer"},
                                                       {"name": "gain", "type": "number"}]
    out = run(root, "source", "types").output
    assert "location  cas" in out and "asset     fs" in out


def test_pull_without_a_neighbor_needs_items(root):
    r = CliRunner().invoke(main, ["-C", str(root), "pull", "--deep"])
    assert r.exit_code != 0 and "--dangling" in r.output


def test_cli_search_suggests_commands():
    def hits(*words):
        r = CliRunner().invoke(main, ["cli", "search", "--json", *words])
        assert r.exit_code == 0, r.output
        return json.loads(r.output)
    assert hits("embeddings")[0]["command"] == "kb store embed"            # plural and -ing forms
    assert "kb item retract" in [h["command"] for h in hits("delete", "item")]   # a synonym
    top = hits("semantic", "search")[0]
    assert top["line"].startswith("kb search WORDS...") and "--semantic" in top["line"]
    assert top["options"][0]["option"] == "--semantic" and top["options"][0]["help"]
    glob = [h for h in hits("log", "level") if h["command"] == "kb"][0]       # options given before any command
    assert "--log-level" in glob["line"] and glob["line"].endswith("COMMAND ...")
    assert "kb hook" not in str(hits("hook", "session"))                     # hidden commands stay hidden
    r = CliRunner().invoke(main, ["cli", "search", "zzyzx"])
    assert r.exit_code == 0 and "no command matches" in r.output
    r = CliRunner().invoke(main, ["cli", "search", "pull", "request"])
    assert r.output.startswith("kb forge pr ")
    assert "Usage:" in CliRunner().invoke(main, ["cli", "search"]).output


def test_cli_search_stems():
    from kb.cmdline import quality, stem
    assert [stem(w) for w in ("embeddings", "embed", "added", "pulled", "notes", "queries", "status")] == \
        ["embed", "embed", "add", "pull", "note", "query", "status"]
    assert quality("stale", "staleness") > quality("delete", "retract") > quality("stale", "push") == 0


def test_cli_tree():
    def tree(*args):
        r = CliRunner().invoke(main, ["cli", "tree", *args])
        return r
    whole = tree().output
    assert whole.startswith("kb\n") and "├── item\n" in whole and "│   ├── set REF [ASSIGNMENTS...]" in whole
    assert "── hook\n" not in whole and "--json" not in whole                    # hidden left out; options on request
    assert tree("forge", "pr").output == tree("forge.pr").output == tree("kb", "forge", "pr").output
    pr = tree("forge.pr").output
    assert pr.startswith("kb forge pr\n") and "└── comment --body BODY NUMBER" in pr and "item" not in pr
    assert "--state open|closed|all" in tree("forge.pr", "-o").output
    assert "Forge name" not in tree("forge.pr", "-o").output
    assert "--forge FORGE_NAME  Forge name" in tree("forge.pr", "-O").output   # option help implies options
    one = tree("item", "set", "-d").output
    assert one.startswith("kb item set REF [ASSIGNMENTS...]\n") and "the rest needs kb item supersede" in one
    assert "git-pre-push" in tree("hook").output                             # a hidden family, when named
    r = tree("item", "nope")
    assert r.exit_code == 1 and "kb item has no command 'nope'" in r.output and "supersede" in r.output
    assert "is a command, not a family" in tree("item.set.x").output
    node = json.loads(tree("cli", "--json").output)
    assert [c["name"] for c in node["commands"]] == ["search", "tree"]
    assert node["commands"][1]["options"][0] == {"option": "-o, --options", "help": "Show each command's options.",
                                                 "required": False}
