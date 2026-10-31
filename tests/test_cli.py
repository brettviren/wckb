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
    assert "topic" in run(root, "item", "stale").output
    run(root, "item", "stamp", tid)
    assert run(root, "item", "stale").output == ""
    assert run(root, "item", "deps", "-r", nid).output.startswith(tid)


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
    out = run(root, "source", "list").output
    assert "location  cas" in out and "asset     fs" in out


def test_pull_without_a_neighbor_needs_items(root):
    r = CliRunner().invoke(main, ["-C", str(root), "pull", "--deep"])
    assert r.exit_code != 0 and "--dangling" in r.output
