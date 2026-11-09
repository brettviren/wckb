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


#: Files above this size are annexed (content fetched on demand); everything smaller is plain git, so a
#: work area has no locked files unless it holds something big (``workspace.largefiles`` in the settings).
LARGEFILES = "1MB"

#: What a workspace's dataset ignores, so build products don't bloat it (``workspace.ignore`` adds more,
#: ``--ignore`` more still). A pattern with a leading slash is anchored at the workspace root.
BUILD_IGNORES = (
    "*.o", "*.obj", "*.a", "*.lib", "*.so", "*.so.*", "*.dylib", "*.dll", "*.lo", "*.la", "*.exe",   # code
    "__pycache__/", "*.pyc", "*.pyo", "*.egg-info/", ".venv/", "venv/", "node_modules/", ".ipynb_checkpoints/",
    "build/", "_build/", "CMakeFiles/", "CMakeCache.txt", "cmake_install.cmake",
    "/bin/", "/lib/", "/lib64/", "/include/", "/install/",                                            # an install prefix
    "*.aux", "*.fls", "*.fdb_latexmk", "*.synctex.gz", "*.toc", "*.blg",                              # LaTeX leftovers
)
_IGNORE_HEADER = "# kb artifact start/adopt: build products and nested repositories (edit freely)"
#: Directories not searched for nested repositories (build trees and environments).
_SKIP_DIRS = {".git", ".datalad", journal.WS_DIR, "build", "_build", "CMakeFiles", "node_modules", "__pycache__",
              ".venv", "venv"}
REPOS = "repos.json"


def _settings(kb: Store, largefiles: str | None, ignore: tuple[str, ...]) -> tuple[str, list[str]]:
    mine = kb.setting("workspace", {}) or {}
    extra = mine.get("ignore", [])
    return (largefiles or mine.get("largefiles") or LARGEFILES,
            list(dict.fromkeys([*BUILD_IGNORES, *([extra] if isinstance(extra, str) else extra), *ignore])))


def configure(ws: Path, largefiles: str = LARGEFILES, ignore: tuple[str, ...] = BUILD_IGNORES) -> list[str]:
    """Settings of the workspace's dataset, so annex gets in nobody's way: only files above LARGEFILES are
    annexed and they are unlocked (writable files, not read-only links), the SHA256E backend, and IGNORE
    patterns in .gitignore. Idempotent; what it changed is returned. Nothing is saved."""
    done = []
    attrs = ws / ".gitattributes"
    lines = attrs.read_text().splitlines() if attrs.exists() else []
    rule = f"* annex.largefiles=(largerthan={largefiles})"
    mine = [ln for ln in lines if ln.startswith("* annex.largefiles=") and ln != rule]
    custom = [ln for ln in mine if ln != "* annex.largefiles=((mimeencoding=binary)and(largerthan=0))"]
    if custom:
        done.append(f"kept your annex.largefiles rule in .gitattributes ({custom[-1]})")
    else:
        keep = [ln for ln in lines if ln not in mine and ln != rule and not ln.startswith("**/.git* annex.largefiles")]
        new = [("* annex.backend=SHA256E" if ln.startswith("* annex.backend=") else ln) for ln in keep]
        if not any(ln.startswith("* annex.backend=") for ln in new):
            new.append("* annex.backend=SHA256E")
        new += [rule, "**/.git* annex.largefiles=nothing"]           # last rule wins: dot files stay in git
        if new != lines:
            attrs.write_text("\n".join(new) + "\n")
            done.append(f"annex only files above {largefiles}, with SHA256E (.gitattributes)")
    r = subprocess.run(["git", "-C", str(ws), "config", "annex.addunlocked", "true"], capture_output=True, text=True)
    if r.returncode != 0:
        raise KBError(f"git config in {ws}: {r.stderr.strip()}")
    done.append("annexed files are unlocked: writable, not read-only links (annex.addunlocked)")
    gi = ws / ".gitignore"
    have = gi.read_text().splitlines() if gi.exists() else []
    new = [p for p in ignore if p not in {ln.strip() for ln in have}]
    if new:
        with open(gi, "a") as f:
            if have and have[-1].strip():
                f.write("\n")
            if _IGNORE_HEADER not in have:
                f.write(_IGNORE_HEADER + "\n")
            f.write("\n".join(new) + "\n")
        done.append(f"ignore {len(new)} pattern(s) in .gitignore")
    return done


def find_repos(ws: Path) -> list[str]:
    """Git repositories nested in the workspace (paths relative to it), not looking inside them or in build
    trees: they have histories and remotes of their own, so the workspace dataset ignores them."""
    import os
    found = []
    for dirpath, dirs, files in os.walk(ws):
        here = Path(dirpath)
        if here != ws and (".git" in dirs or ".git" in files):
            found.append(here.relative_to(ws).as_posix())
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
    return found


def repo_info(path: Path) -> dict:
    """Where a nested repository comes from and what state it is in: remotes, branch, HEAD, uncommitted work."""
    def g(*args):
        r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""
    remotes = {n: g("remote", "get-url", n) for n in g("remote").split()}
    return {"remotes": remotes, "branch": g("symbolic-ref", "--short", "-q", "HEAD") or None,
            "head": g("rev-parse", "HEAD") or None, "dirty": bool(g("status", "--porcelain", "--untracked-files=no"))}


def repos_snapshot(ws: Path, found: list[str] | None = None) -> dict[str, dict]:
    """The nested repositories of the workspace and their state now. Those listed in
    .kb-artifact/repos.json before are kept while they exist; FOUND adds others."""
    f = ws / journal.WS_DIR / REPOS
    known = list(json.loads(f.read_text())) if f.exists() else []
    paths = sorted({*known, *(found or [])})
    return {p: repo_info(ws / p) for p in paths if (ws / p / ".git").exists()}


def _write_repos(ws: Path, found: list[str] | None = None) -> dict[str, dict]:
    snap = repos_snapshot(ws, found)
    f = ws / journal.WS_DIR / REPOS
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n")
    return snap


def _register(kb: Store, ws: Path, title: str, prompts: bool) -> dict:
    """Describe the workspace's dataset as a source and mark it as an artifact workspace."""
    from . import datasets
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
    return data


def start(kb: Store, path: Path, title: str, prompts: bool = True, largefiles: str | None = None,
          ignore: tuple[str, ...] = ()) -> tuple[Path, dict]:
    """Make PATH an artifact workspace: a DataLad dataset (made if PATH is new or empty, configured by
    ``configure``; used as it is if it is one), marked by .kb-artifact/workspace.json and described in the
    store as a datalad source."""
    from . import datasets, sharing
    datasets.need()
    ws = Path(path).expanduser().absolute()
    if (ws / journal.WS_DIR / journal.WS_FILE).is_file():
        raise KBError(f"{ws} is an artifact workspace already ({journal.read_workspace(ws)['title']})")
    if not (ws / ".git").exists():
        if ws.exists() and any(ws.iterdir()):
            raise KBError(f"{ws} is not empty and not a dataset: kb artifact adopt {ws} makes it a workspace "
                          "around what is there")
        sharing.create(ws, text2git=False)
        configure(ws, *_settings(kb, largefiles, ignore))
    elif datasets.dataset_root(ws) is None:
        raise KBError(f"{ws} is a git repository but not a DataLad dataset: kb artifact adopt {ws} makes it a "
                      "workspace")
    ws = ws.resolve()
    return ws, _register(kb, ws, title, prompts)


def adopt(kb: Store, path: Path, title: str | None = None, prompts: bool = True, largefiles: str | None = None,
          ignore: tuple[str, ...] = ()) -> dict:
    """Make the existing directory PATH an artifact workspace, around what is there (nothing is moved or
    changed): a DataLad dataset is made (or an existing git repository or dataset used), its annex settings and
    ignores are set (``configure``), the git repositories nested in it are ignored and their remotes and commits
    noted (.kb-artifact/repos.json), and what is there is saved. Run again on a workspace, it refreshes those
    settings and the notes on nested repositories.  Returns what was done."""
    from . import datasets
    datasets.need()
    ws = Path(path).expanduser().absolute()
    if not ws.is_dir():
        raise KBError(f"{ws} is not a directory")
    done = []
    if datasets.dataset_root(ws) != ws.resolve():
        import datalad.api as dl
        dl.create(path=str(ws), force=True, result_renderer="disabled")
        done.append(f"made {ws} a DataLad dataset (what is there is untouched)")
    nested = find_repos(ws)
    lf, patterns = _settings(kb, largefiles, ignore)
    done += configure(ws, lf, tuple(patterns) + tuple(f"/{p}/" for p in nested))
    snap = _write_repos(ws, nested)
    for p, info in snap.items():
        done.append(f"ignored the repository {p} (" + (next(iter(info["remotes"].values()), None) or "no remote")
                    + (", uncommitted work" if info["dirty"] else "") + ")")
    existing = (ws / journal.WS_DIR / journal.WS_FILE).is_file()
    if existing:
        _save(ws, "kb: refresh the workspace settings")
        data = journal.read_workspace(ws)
    else:
        data = _register(kb, ws.resolve(), title or ws.name, prompts)
    return {"workspace": ws, "data": data, "done": done, "repos": snap, "existing": existing}


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
    out = {"level": level, "workspace": data["source"], "title": data["title"], "started": data["started"],
           "steps": steps, "wrote": list(dict.fromkeys(wrote))}
    repos = repos_snapshot(ws)
    if repos:
        out["repos"] = repos          # the repositories ignored in the workspace, and where they stood
    return out


#: Keys of a note's sidecar (see ``sidecar``).
SIDECAR_STRINGS = ("title", "summary", "doctype", "date", "status", "access", "published_at")
SIDECAR_LISTS = ("subject", "tags", "creators", "inputs", "source")
SIDECAR_OTHER = ("build", "identifiers")


def sidecar_path(output: Path, ws: Path) -> Path | None:
    """The sidecar describing OUTPUT: NAME.note.toml beside it (NAME its file name without the suffix),
    else note.toml beside it, else note.toml at the workspace root."""
    for p in (output.with_name(output.stem + ".note.toml"), output.parent / "note.toml", ws / "note.toml"):
        if p.is_file():
            return p
    return None


def published(url: str) -> dict:
    """The identifiers of a page published at URL: the URL, and the ID of a claude.ai artifact."""
    import re
    ids = {"url": url}
    m = re.match(r"https://claude\.ai/(?:code/)?artifact/([\w-]+)", url)
    if m:
        ids["claude-artifact"] = m.group(1)
    return ids


def read_sidecar(path: Path) -> tuple[dict, list[str], dict]:
    """A note's sidecar, a TOML file that says what the note is whatever its source format (Markdown,
    org, LaTeX, Quarto, HTML): item fields (title, summary, doctype, date, status, access, subject, tags,
    creators, identifiers, published_at), ``inputs`` (item IDs it presents), and what only describes
    how it is made (``source``: its files, ``build``: its command). Returns (fields, inputs, how)."""
    import tomllib
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise KBError(f"{path}: {e}")
    known = SIDECAR_STRINGS + SIDECAR_LISTS + SIDECAR_OTHER
    unknown = sorted(set(raw) - set(known))
    if unknown:
        raise KBError(f"{path}: unknown key(s) {', '.join(unknown)}; the sidecar has {', '.join(known)}")
    import datetime
    if isinstance(raw.get("date"), (datetime.date, datetime.datetime)):
        raw["date"] = raw["date"].isoformat()[:10]     # TOML reads an unquoted 2026-11-09 as a date
    for k in SIDECAR_STRINGS + ("build",):
        if k in raw and not isinstance(raw[k], str):
            raise KBError(f"{path}: {k} must be a string")
    fields: dict = {k: raw[k] for k in ("title", "summary", "doctype", "date", "status", "access") if k in raw}
    for k in ("subject", "tags", "creators", "inputs", "source"):
        if k in raw:
            v = raw[k]
            raw[k] = [v] if isinstance(v, str) else list(v)
            if not all(isinstance(x, str) for x in raw[k]):
                raise KBError(f"{path}: {k} must be a string or a list of strings")
    for k in ("subject", "tags", "creators"):
        if k in raw:
            fields[k] = raw[k]
    ids = dict(raw.get("identifiers", {}))
    if not all(isinstance(v, str) for v in ids.values()):
        raise KBError(f"{path}: identifiers must map names to strings")
    if "published_at" in raw:
        ids = {**published(raw["published_at"]), **ids}
    if ids:
        fields["identifiers"] = ids
    how = {k: raw[k] for k in ("source", "build") if k in raw}
    return fields, raw.get("inputs", []), how


def finish(kb: Store, output: Path, fields: dict, drop: list[str] = (), add: list[str] = (),
           with_session: bool = False, start: Path | None = None, published_at: str | None = None) -> dict:
    """Harvest the workspace into the store: OUTPUT (a file in the workspace: the PDF, the demo's page)
    becomes an artifact document whose inputs are what was read here (less DROP, plus ADD, plus what was
    read in the same sessions elsewhere WITH_SESSION), with its process record; prompts given here become
    prompt documents, part of it.  A workspace finished before gives a new version (it supersedes).
    The note's sidecar (``read_sidecar``) gives defaults that FIELDS override; PUBLISHED_AT (a URL, such as a
    claude.ai artifact's) is recorded as an identifier."""
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
    side = sidecar_path(out, ws)
    side_fields, side_inputs, how = read_sidecar(side) if side else ({}, [], {})
    fields = {**side_fields, **fields}
    if "identifiers" in side_fields or "identifiers" in fields:
        fields["identifiers"] = {**side_fields.get("identifiers", {}), **fields.get("identifiers", {})}
    if published_at:
        fields["identifiers"] = {**fields.get("identifiers", {}), **published(published_at)}
    inputs = [c["id"] for c in candidates(kb, ws) if c["current"] and c["id"] not in drop
              and (c["where"] == "here" or with_session)]
    inputs += [kb.get(a).id for a in [*add, *side_inputs] if kb.get(a).id not in inputs]
    if (ws / journal.WS_DIR / REPOS).exists():
        _write_repos(ws)                  # where the ignored repositories stand now
    _save(ws, f"kb: finish '{data['title']}'")
    rec_process = process(kb, ws)
    if side:
        rec_process["note"] = {"sidecar": side.relative_to(ws).as_posix(), **how}
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
