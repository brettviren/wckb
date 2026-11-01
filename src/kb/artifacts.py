"""Artifacts: documents made here to present knowledge (decisions/0004).

An artifact is a ``document`` of doctype ``technote``, ``demo`` or
``notebook`` (or any document with a ``provenance`` level).  Its inputs, the
items it presents, are in ``derived_from``, pinned by digest when it is made;
when one of them changes, the artifact is stale (``kb item stale``), and a
person or LLM remakes or revises it.  Its ``provenance`` says how much of
its making is recorded: ``cited`` (the inputs), ``recorded`` (also the steps),
``rebuildable`` (a view remakes it).
"""

from . import graph
from .items import Store


def listing(kb: Store, doctypes: tuple[str, ...] = (), stale_only: bool = False,
            include_old: bool = False) -> list[dict]:
    """The store's artifacts, newest first: what each is, how it was made, and whether it is stale."""
    stale = {s.id: s.reasons for s in graph.stale(kb) if s.what == "artifact"}
    rows = []
    for rec in kb.items.values():
        d = rec.data
        if not graph.is_artifact(d) or (doctypes and d.get("doctype") not in doctypes):
            continue
        if not include_old and not kb.is_current(rec.id):
            continue
        if stale_only and rec.id not in stale:
            continue
        rows.append({"id": rec.id, "title": d.get("title", ""), "summary": d.get("summary", ""),
                     "doctype": d.get("doctype"), "provenance": d.get("provenance", "cited"),
                     "date": d.get("date") or next(iter((d.get("dates") or {}).values()), None),
                     "inputs": len(rec.inputs()), "made_with": (d.get("created") or {}).get("with", []),
                     "stale": stale.get(rec.id, []), "created": d["created"]["at"]})
    return sorted(rows, key=lambda r: r["created"], reverse=True)


# -- phase 2: workspaces, journals, harvest -----------------------------------------------------------

import json  # noqa: E402
import secrets  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

from . import journal  # noqa: E402
from .errors import KBError  # noqa: E402

#: Where kb puts the items an artifact reads, inside its workspace (committed, so runs can declare them).
INPUTS = "inputs"
_RUN_START, _RUN_END = "=== Do not change lines below ===", "^^^ Do not change lines above ^^^"


def _git(ws: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise KBError(f"git {' '.join(args[:2])} in {ws}: {r.stderr.strip()}")
    return r.stdout


def _save(ws: Path, message: str, paths: list[str] | None = None) -> None:
    import datalad.api as dl
    dl.save(dataset=str(ws), path=paths, message=message, result_renderer="disabled", on_failure="ignore")


def start(kb: Store, path: Path, title: str, prompts: bool = True) -> tuple[Path, dict]:
    """Make PATH an artifact workspace: a DataLad dataset (made if PATH is new or empty, adopted if it is
    one), marked by .kb-artifact/workspace.json and described in the store as a datalad source."""
    from . import datasets, sharing
    datasets.need()
    ws = Path(path).expanduser().absolute()
    if (ws / journal.WS_DIR / journal.WS_FILE).is_file():
        raise KBError(f"{ws} is an artifact workspace already ({journal.read_workspace(ws)['title']})")
    if not (ws / ".git").exists():
        if ws.exists() and any(ws.iterdir()):
            raise KBError(f"{ws} is not empty and not a dataset: make it one (datalad create --force) or pick "
                          "another directory")
        sharing.create(ws)                                   # text in git, the rest annexed
    elif datasets.dataset_root(ws) is None:
        raise KBError(f"{ws} is a git repository but not a DataLad dataset (datalad create --force there)")
    ws = ws.resolve()
    src = datasets.describe(kb, [str(ws)], title=f"{title} (workspace)",
                            summary=f"The workspace where the artifact '{title}' is made: its sources, "
                                    "runs and outputs.")
    data = {"id": "aw-" + secrets.token_hex(5), "title": title, "store": kb.cfg.id, "source": src.id,
            "started": journal.now(), "prompts": prompts, **({"session": journal.session()} if journal.session()
                                                              else {})}
    journal.write_workspace(ws, data)
    ignore = ws / ".gitignore"
    line = f"{journal.WS_DIR}/{journal.JOURNAL}"
    if line not in (ignore.read_text().splitlines() if ignore.exists() else []):
        with open(ignore, "a") as f:
            f.write(f"# kb's journal of this artifact's making: local, harvested by kb artifact finish\n{line}\n")
    _save(ws, f"kb: start the artifact workspace '{title}'")
    journal.append("start", kb, start=ws, workspace=data["id"])
    return ws, data


def _here(start: Path | None = None) -> Path:
    ws = journal.workspace(start)
    if ws is None:
        raise KBError("not in an artifact workspace (kb artifact start DIR --title ... makes one; "
                      "kb finds it from the working directory)")
    return ws


def run(kb: Store, command: list[str], inputs: list[str] = (), outputs: list[str] = (),
        message: str | None = None, start: Path | None = None) -> dict:
    """Run COMMAND in the workspace under ``datalad run``: the items INPUTS are copied into inputs/ first
    and declared, so the commit says what the command read (items and files) and what it made."""
    from . import datasets, opener
    datasets.need()
    ws = _here(start)
    declared, record = [], []
    for ref in inputs:
        rec, r, p = opener.path(kb, ref)
        dest = ws / INPUTS / f"{rec.id}-{p.name}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists() or content_differs(p, dest):
            if dest.is_symlink() or dest.exists():
                dest.unlink()
            shutil.copyfile(p, dest)
        declared.append(str(dest.relative_to(ws)))
        record.append({"id": rec.id, "rendition": r["name"], "digest": kb.ref_digest(rec.id),
                       "file": str(dest.relative_to(ws))})
    if record:
        (ws / INPUTS / "inputs.json").write_text(json.dumps(record, indent=2) + "\n")
        _save(ws, "kb: inputs for a run", [INPUTS])
        journal.served(kb, [f"{x['id']}#{x['rendition']}" for x in record], "run")
    if _git(ws, "status", "--porcelain").strip():   # datalad run needs a clean dataset: keep the work so far
        _save(ws, "Work in progress (saved before a run by kb artifact run)")
    before = _git(ws, "rev-parse", "HEAD").strip()
    import datalad.api as dl
    from datalad.distribution.dataset import Dataset
    from shlex import join
    res = dl.run(cmd=join(command), dataset=Dataset(str(ws)), inputs=declared or None, outputs=list(outputs) or None,
                 message=message or f"kb artifact run: {join(command)[:60]}", explicit=False,
                 result_renderer="disabled", on_failure="ignore", return_type="list")
    bad = [r for r in res if r.get("status") in ("error", "impossible")]
    if bad:
        raise KBError(f"datalad run in {ws}: {bad[0].get('message')}")
    after = _git(ws, "rev-parse", "HEAD").strip()
    changed = _git(ws, "show", "--name-only", "--format=", after).split() if after != before else []
    entry = {"command": join(command), "commit": after if after != before else None, "inputs": record,
             "outputs": changed}
    journal.append("run", kb, start=ws, **entry)
    return entry


def content_differs(a: Path, b: Path) -> bool:
    from .content import sha256_file
    return not b.exists() or sha256_file(a)[0] != sha256_file(b)[0]


def _journals(kb: Store, ws: Path) -> tuple[list[dict], list[dict]]:
    """(the workspace's entries, the store journal's entries of the same sessions since it started)."""
    data = journal.read_workspace(ws)
    here = journal.entries(ws / journal.WS_DIR / journal.JOURNAL)
    sessions = {e["session"] for e in here if e.get("session")} | ({data["session"]} if data.get("session") else set())
    store = [e for e in journal.entries(kb.cfg.kbdir / journal.JOURNAL)
             if e.get("session") in sessions and e.get("at", "") >= data["started"]]
    return here, store


def candidates(kb: Store, start: Path | None = None) -> list[dict]:
    """The items read while making the workspace's artifact: each with how often and how it was read,
    and where: ``here`` (in the workspace) or ``session`` (in the same session, elsewhere: less sure)."""
    ws = _here(start)
    here, store = _journals(kb, ws)
    out: dict[str, dict] = {}
    for where, entries in (("here", here), ("session", store)):
        for e in entries:
            if e.get("kind") != "served" or e.get("store") not in (None, kb.cfg.id):
                continue
            for it in e.get("items", []):
                c = out.setdefault(it["id"], {"id": it["id"], "where": where, "how": [], "reads": 0})
                c["reads"] += 1
                c["digest"] = it["digest"]
                if e.get("how") not in c["how"]:
                    c["how"].append(e.get("how"))
                if where == "here":
                    c["where"] = "here"
    for c in out.values():
        rec = kb.items.get(c["id"])
        c["title"] = rec.data.get("title", "") if rec else "(not in this store)"
        c["kind"] = rec.kind if rec else None
        c["current"] = bool(rec) and kb.is_current(c["id"])
    return sorted(out.values(), key=lambda c: (c["where"] != "here", -c["reads"]))


def _run_records(ws: Path, since: str) -> list[dict]:
    """``datalad run`` commits in the workspace since SINCE: command, inputs, outputs, commit, time."""
    out = []
    log_ = _git(ws, "log", f"--since={since}", "--format=%H%x1f%aI%x1f%an%x1f%B%x1e", check=False)
    for chunk in log_.split("\x1e"):
        parts = chunk.strip("\n").split("\x1f")
        if len(parts) < 4:
            continue
        sha, at, author, body = parts
        if _RUN_START in body and _RUN_END in body:
            try:
                rec = json.loads(body.split(_RUN_START, 1)[1].split(_RUN_END, 1)[0])
            except ValueError:
                continue
            out.append({"type": "code", "commit": sha, "at": at, "command": rec.get("cmd"),
                        "inputs": rec.get("inputs", []), "outputs": rec.get("outputs", [])})
        else:
            out.append({"type": "human", "commit": sha, "at": at, "author": author,
                        "message": body.strip().splitlines()[0] if body.strip() else ""})
    return out


def process(kb: Store, ws: Path) -> dict:
    """The process record of the workspace's artifact: its steps, from the journals and the git history."""
    data = journal.read_workspace(ws)
    here, store = _journals(kb, ws)
    models = {e["session"]: e.get("model") for e in journal.entries(kb.cfg.kbdir / journal.JOURNAL) + here
              if e.get("kind") == "session" and e.get("session")}
    steps: list[dict] = []
    reads = [f"{it['id']}@{it['digest'][:12]}" for e in here if e.get("kind") == "served" for it in e["items"]]
    if reads:
        steps.append({"type": "kb", "reads": list(dict.fromkeys(reads))})
    commits = [s for s in _run_records(ws, data["started"]) if not s.get("message", "").startswith("kb:")]
    steps += sorted(commits, key=lambda s: s["at"])
    by_session: dict[str, dict] = {}
    for e in here:
        if e.get("kind") in ("prompt", "edit"):
            sid = e.get("session") or "unknown"
            s = by_session.setdefault(sid, {"type": "llm", "session": sid, "model": models.get(sid),
                                            "prompts": 0, "files": [], "first": e["at"]})
            if e["kind"] == "prompt":
                s["prompts"] += 1
            elif e.get("file") and e["file"] not in s["files"]:
                s["files"].append(e["file"])
    steps += list(by_session.values())
    wrote = [e["item"] for e in here + store if e.get("kind") == "wrote"]
    for i, s in enumerate(steps, 1):
        s["id"] = f"s{i}"
    level = "recorded" if any(s["type"] in ("code", "llm") for s in steps) else "cited"
    return {"level": level, "workspace": data["source"], "title": data["title"], "started": data["started"],
            "steps": steps, "wrote": list(dict.fromkeys(wrote))}


def finish(kb: Store, output: Path, fields: dict, drop: list[str] = (), add: list[str] = (),
           with_session: bool = False, start: Path | None = None) -> dict:
    """Harvest the workspace into the store: OUTPUT (a file in the workspace: the PDF, the demo's page)
    becomes an artifact document whose inputs are what was read here (less DROP, plus ADD, plus what was
    read in the same sessions elsewhere WITH_SESSION), with its process record; prompts given here become
    prompt documents, part of it.  A workspace finished before gives a new version (it supersedes)."""
    from . import authoring
    from .edit import add_rendition
    ws = _here(start or Path(output).parent)
    out = Path(output).expanduser().absolute()
    if not out.is_relative_to(ws):
        raise KBError(f"{out} is not in the workspace {ws}: the artifact's files live in its workspace")
    if not out.exists():
        raise KBError(f"no such file: {out}")
    data = journal.read_workspace(ws)
    began = journal.now()
    inputs = [c["id"] for c in candidates(kb, ws) if c["current"] and c["id"] not in drop
              and (c["where"] == "here" or with_session)]
    inputs += [kb.get(a).id for a in add if kb.get(a).id not in inputs]
    _save(ws, f"kb: finish '{data['title']}'")
    rec_process = process(kb, ws)
    fields = dict(fields)
    fields.setdefault("title", data["title"])
    fields.setdefault("doctype", "technote")
    fields.setdefault("provenance", rec_process["level"])
    fields["derived_from"] = list(dict.fromkeys(fields.get("derived_from", []) + inputs))
    if data.get("artifact") and data["artifact"] in kb.items and kb.is_current(data["artifact"]):
        fields.setdefault("supersedes", [data["artifact"]])
    art = authoring.add(kb, "document", [str(out)], fields, force=True)
    pj = ws / journal.WS_DIR / "process.json"
    pj.write_text(json.dumps(rec_process, indent=2, ensure_ascii=False) + "\n")
    add_rendition(kb, art.id, pj, name="process", role="metadata", media_type="application/json")
    prompts = []
    if data.get("prompts", True):
        for e in journal.entries(ws / journal.WS_DIR / journal.JOURNAL):
            if e.get("kind") == "prompt" and e.get("at", "") > data.get("harvested", "") and e.get("prompt"):
                prompts.append(_prompt_doc(kb, e, art, rec_process))
    data.update(artifact=art.id, harvested=began, finished=journal.now())
    journal.write_workspace(ws, data)
    _save(ws, f"kb: finished '{data['title']}' as {art.id}")
    return {"artifact": art.id, "inputs": inputs, "process": rec_process, "prompts": prompts}


def _prompt_doc(kb: Store, e: dict, art, rec_process: dict) -> str:
    from . import authoring
    text = e["prompt"].strip()
    first = text.splitlines()[0][:100] if text else "prompt"
    model = next((s.get("model") for s in rec_process["steps"] if s.get("session") == e.get("session")), None)
    fields = {"title": f"Prompt: {first}", "summary": f"A prompt given while making '{art.data['title']}'.",
              "doctype": "prompt", "creators": [kb.cfg.user], "dates": {"created": e["at"][:10]},
              "links": [{"rel": "part-of", "ref": art.id}], "subject": art.data.get("subject", []),
              "access": art.data.get("access", kb.cfg.access)}
    if model:
        fields["created"] = {"with": [model]}
    rec = authoring.new(kb, "document", fields, text=text)
    return rec.id


def steps_view(kb: Store, ident: str) -> dict:
    """The process record of an artifact (its 'process' rendition)."""
    rec = kb.get(ident)
    if not rec.has_rendition("process"):
        raise KBError(f"{rec.id} has no process record (artifacts finished in a workspace have one)")
    return json.loads(rec.text("process"))
