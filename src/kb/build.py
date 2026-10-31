"""Building and publishing views.

A view's sources (documents, scripts, Makefile ...) are renditions with role
``source`` held in the store.  ``build`` materializes them into a work area
under ``.kb/build/<id>/``, resolves the inputs into ``inputs.json``, runs the
declared tool with ``KB_*`` variables set, and records a manifest (a
``metadata`` rendition of the view) that lets anyone tell when the view is
stale.  ``publish`` copies the output to the publish store and logs a
``publish`` event.  Edit sources with ``kb item checkout VIEW DIR`` / ``kb item checkin DIR``.
"""

import copy
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from . import __version__, content
from .errors import KBError
from .ids import parse_ref
from .items import Store, Item, canonical, now

MANIFEST = "manifest"


def source_files(rec: Item) -> dict[str, str]:
    """Hashes of a view's source files, by file name."""
    out = {}
    for r in rec.renditions:
        if r["role"] == "source":
            fn = next((l.get("filename") for l in r["locations"] if l["type"] == "db"), None) or r["name"]
            out[fn] = r.get("sha256")
    return dict(sorted(out.items()))


def read_manifest(rec: Item) -> dict | None:
    if not rec.has_rendition(MANIFEST):
        return None
    text = rec.text(MANIFEST)
    return json.loads(text) if text else None


def _view_digest(rec: Item) -> str:
    data = {k: v for k, v in rec.data.items() if k != "renditions"}
    return hashlib.sha256(canonical(data)).hexdigest()


def view_staleness(kb: Store, rec: Item) -> list[str]:
    man = read_manifest(rec)
    if man is None:
        return ["never built"]
    reasons = []
    pinned = {i["ref"]: i["digest"] for i in man.get("inputs", [])}
    for ref in rec.inputs():
        ident = parse_ref(ref)[0]
        if not kb.exists(ref):
            reasons.append(f"input {ref} is not in this store")
        elif ref not in pinned:
            reasons.append(f"input {ref} added since built")
        else:
            if pinned[ref] != kb.ref_digest(ref):
                reasons.append(f"input {ref} changed since built")
            if not kb.is_current(ident):
                reasons.append(f"input {ident} is superseded/retracted")
    for ref in pinned:
        if ref not in rec.inputs():
            reasons.append(f"input {ref} removed since built")
    if man.get("sources") != source_files(rec):
        reasons.append("view sources changed since built")
    if man.get("view_digest") != _view_digest(rec):
        reasons.append("view item changed since built")
    return reasons


def resolve_inputs(kb: Store, rec: Item) -> dict:
    """ref -> {item (a JSON file), renditions: {name: path or None}}; named renditions are fetched."""
    out = {}
    area = kb.cfg.build_dir / rec.id / "items"
    area.mkdir(parents=True, exist_ok=True)
    for ref in rec.inputs():
        ident, name = parse_ref(ref)
        dep = kb.get(ident)
        rp = area / f"{dep.id}.json"
        rp.write_text(json.dumps(dep.data, indent=2) + "\n")
        rends = {}
        for r in dep.renditions:
            wanted = name == r["name"]
            path = content.resolve(kb, dep, r, fetch_urls=wanted)
            if wanted and path is None:
                raise KBError(f"{rec.id}: input {ref} is not reachable from here")
            rends[r["name"]] = str(path) if path else None
        out[ref] = {"item": str(rp), "renditions": rends}
    return out


def _tool_argv(rec: Item, out: Path, inputs_json: Path, work: Path) -> list[list[str]]:
    b = rec.data["build"]
    tool, target = b["tool"], b.get("target")
    if tool == "make":
        return [["make", f"KB_OUT={out}"] + ([target] if target else [])]
    if tool == "snakemake":
        return [["snakemake", "--cores", "all", "--config", f"kb_out={out}"] + ([target] if target else [])]
    if tool == "quarto":
        fmts = b.get("formats") or ["html"]
        return [["quarto", "render", target, "--to", f, "--output-dir", str(out)] for f in fmts]
    if tool == "pandoc":
        fmts = b.get("formats") or ["html"]
        stem = Path(target).stem
        return [["pandoc", target, "-s", "--resource-path", str(work), "-o", str(out / f"{stem}.{f}")]
                for f in fmts]
    subst = {"out": str(out), "inputs": str(inputs_json), "work": str(work), "view": str(work)}
    return [[a.format(**subst) for a in b["command"]]]


def _tool_version(tool: str) -> str:
    exe = {"make": "make", "snakemake": "snakemake", "quarto": "quarto", "pandoc": "pandoc"}.get(tool)
    if not exe or not shutil.which(exe):
        return ""
    out = subprocess.run([exe, "--version"], capture_output=True, text=True)
    lines = (out.stdout or out.stderr).strip().splitlines()
    return lines[0] if lines else ""


def materialize(kb: Store, rec: Item, work: Path) -> None:
    for r in rec.renditions:
        if r["role"] != "source":
            continue
        fn = next((l.get("filename") for l in r["locations"] if l["type"] == "db"), None) or r["name"]
        data = rec.content(r["name"])
        if data is None:
            p = content.resolve(kb, rec, r)
            if p is None:
                raise KBError(f"{rec.id}: source {fn} is not reachable")
            data = p.read_bytes()
        dest = work / fn
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        if fn.endswith((".sh", ".py")) or "/bin/" in fn:
            dest.chmod(0o755)


def build(kb: Store, ident: str, log=None) -> dict:
    rec = kb.get(ident)
    if rec.kind != "view":
        raise KBError(f"{rec.id} is a {rec.kind}, not a view")
    area = kb.cfg.build_dir / rec.id
    work, out = area / "work", area / "out"
    for d in (work, out):
        if d.exists():
            shutil.rmtree(d)
    work.mkdir(parents=True)
    out.mkdir(parents=True)
    materialize(kb, rec, work)
    inputs = resolve_inputs(kb, rec)
    inputs_json = area / "inputs.json"
    inputs_json.write_text(json.dumps(inputs, indent=2) + "\n")
    env = dict(os.environ, KB_ROOT=str(kb.root), KB_VIEW=str(work), KB_OUT=str(out),
               KB_INPUTS=str(inputs_json), KB_WORK=str(work))
    tool = rec.data["build"]["tool"]
    logfile = area / "build.log"
    with open(logfile, "w") as lf:
        for argv in _tool_argv(rec, out, inputs_json, work):
            if log:
                log(" ".join(argv))
            try:
                r = subprocess.run(argv, cwd=work, env=env, stdout=lf, stderr=subprocess.STDOUT)
            except OSError as exc:
                raise KBError(f"cannot run {argv[0]} for {rec.id}: {exc}") from exc
            if r.returncode != 0:
                raise KBError(f"build of {rec.id} failed ({r.returncode}); see {logfile}")
    outputs = {}
    for p in sorted(out.rglob("*")):
        if p.is_file():
            sha, size = content.sha256_file(p)
            outputs[str(p.relative_to(out))] = {"sha256": sha, "size": size}
    if not outputs:
        raise KBError(f"build of {rec.id} wrote nothing to $KB_OUT ({out})")
    manifest = {
        "view": rec.id, "built_at": now(), "built_by": kb.cfg.user, "kb_version": __version__,
        "tool": tool, "tool_version": _tool_version(tool), "view_digest": _view_digest(rec),
        "inputs": [{"ref": ref, "digest": kb.ref_digest(ref)} for ref in rec.inputs()],
        "sources": source_files(rec), "outputs": outputs,
    }
    data = copy.deepcopy(rec.data)
    with kb.tx():
        locs, sha, size = content.place_bytes(kb, (json.dumps(manifest, indent=2) + "\n").encode(), "manifest.json")
        data["renditions"] = [r for r in data.get("renditions", []) if r["name"] != MANIFEST] + [
            {"name": MANIFEST, "role": "metadata", "media_type": "application/json", "sha256": sha, "size": size,
             "locations": locs}]
        kb.save(Item(data, kb), "build", {"outputs": len(outputs)})
    return manifest


def manifest_version(manifest: dict) -> str:
    core = {k: v for k, v in manifest.items() if k not in ("built_at", "built_by")}
    return hashlib.sha256(canonical(core)).hexdigest()[:12]


def publish(kb: Store, ident: str) -> dict:
    rec = kb.get(ident)
    if kb.cfg.publish_path is None:
        raise KBError("no publish path: kb config set views '{\"publish_path\": ...}' (or .kb/local.toml)")
    man = read_manifest(rec)
    if man is None:
        raise KBError(f"{rec.id} has never been built")
    reasons = view_staleness(kb, rec)
    if reasons:
        raise KBError(f"{rec.id} is stale ({'; '.join(reasons)}); kb view build it first")
    out = kb.cfg.build_dir / rec.id / "out"
    for rel, meta in man["outputs"].items():
        p = out / rel
        if not p.is_file() or content.sha256_file(p)[0] != meta["sha256"]:
            raise KBError(f"build output {rel} does not match the manifest; rebuild {rec.id}")
    version = manifest_version(man)
    dest = kb.cfg.publish_path / rec.id / version
    if not dest.exists():
        tmp = dest.with_name(version + ".partial")
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(out, tmp)
        (tmp / "_kb_manifest.json").write_text(json.dumps(man, indent=2) + "\n")
        tmp.replace(dest)
    latest = kb.cfg.publish_path / rec.id / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(version)
    entry = {"version": version, "path": str(dest), "inputs": [i["ref"] for i in man["inputs"]]}
    if kb.cfg.publish_url:
        entry["url"] = f"{kb.cfg.publish_url.rstrip('/')}/{rec.id}/{version}/"
    with kb.tx():
        kb.log_event("publish", rec.id, entry)
    return entry | {"at": now(), "by": kb.cfg.user}


def publications(kb: Store, rec: Item) -> list[dict]:
    return [e["detail"] | {"at": e["at"], "by": e["actor"]} for e in kb.events(rec.id, "publish")]


def world(kb: Store, log=lambda m: None, publish_too: bool = False) -> dict:
    """Re-extract stale renditions, then rebuild stale views in dependency order."""
    from . import graph, transforms
    report = {"extracted": [], "built": [], "published": [], "failed": []}
    for s in graph.stale_renditions(kb):
        rec = kb.get(s.id)
        r = rec.rendition(s.rendition)
        try:
            transforms.extract(kb, rec, r["derived_from"], r["transform"]["name"], s.rendition, force=True)
            report["extracted"].append(f"{s.id}#{s.rendition}")
            log(f"extracted {s.id}#{s.rendition}")
        except KBError as exc:
            report["failed"].append({"id": f"{s.id}#{s.rendition}", "error": str(exc)})
    views = [r.id for r in kb.items.values() if r.kind == "view" and view_staleness(kb, r)]
    for vid in graph.topo_order(kb, views):
        try:
            build(kb, vid, log)
            report["built"].append(vid)
            log(f"built {vid}")
            if publish_too:
                report["published"].append(publish(kb, vid)["path"])
        except KBError as exc:
            report["failed"].append({"id": vid, "error": str(exc)})
            log(f"FAILED {vid}: {exc}")
    return report
