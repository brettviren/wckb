"""Changing existing items within policy.

``set_fields`` (``kb set``), renditions (``kb rendition add|locate``),
content (``kb content``), and a generic file round trip for the few cases
where files are the natural way to work: ``kb checkout`` writes an item and
its content into a directory, ``kb checkin`` reads the directory back,
validates, and applies what changed (``kb edit`` does both around $EDITOR).

On frozen items (sources, released policies) only the fields describing our
relationship to the assertion may change; the database refuses the rest.
"""

import copy
import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from . import schema, store
from .authoring import _safe_name, deep_merge, parse_set
from .errors import KBError
from .ids import parse_ref
from .records import IMMUTABLE, KB, Record, dumps, is_frozen

NEVER = {"id", "kind", "created", "schema_version", "renditions", "origin"}
LIST_FIELDS = {"subject", "tags", "evidence", "related", "derived_from", "supersedes", "retracts", "creators",
               "members"}
CHECKOUT_MARK = ".kb-checkout.json"


def _ref_of(x):
    return x["ref"] if isinstance(x, dict) else x


def set_fields(kb: KB, ident: str, sets=(), add=(), remove=(), replace_ref=(), approved: str | None = None) -> Record:
    rec = kb.get(ident)
    data = copy.deepcopy(rec.data)
    changes = parse_set(sets)
    touched = set(changes)
    for item in add:
        k, v = _kv(item)
        touched.add(k)
        if k not in LIST_FIELDS:
            raise KBError(f"--add works on list fields: {', '.join(sorted(LIST_FIELDS))}")
        lst = data.setdefault(k, [])
        if v not in [_ref_of(x) for x in lst]:
            lst.append(v)
    for item in remove:
        k, v = _kv(item)
        touched.add(k)
        data[k] = [x for x in data.get(k, []) if _ref_of(x) != v]
        if not data[k]:
            del data[k]
    for item in replace_ref:
        old, new = _kv(item)
        from .authoring import _known
        if not _known(kb, new):
            raise KBError(f"no record {new} here or in any reachable store")
        for k in ("derived_from", "evidence", "related"):
            if k in data:
                touched.add(k)
                data[k] = [new if _ref_of(x) == old else x for x in data[k]]
    bad = touched & NEVER
    if bad:
        raise KBError(f"cannot set {', '.join(sorted(bad))} (renditions: kb rendition / kb content)")
    if rec.frozen:
        frozen = touched & set(IMMUTABLE)
        if frozen:
            raise KBError(f"{', '.join(sorted(frozen))} of {rec.id} cannot change (it is frozen); "
                          f"use kb supersede {rec.id}")
    new = deep_merge(data, changes)
    new = {k: v for k, v in new.items() if v is not None}  # key=null deletes
    errors = schema.validate(new)
    if errors:
        raise KBError(f"{rec.id} not changed:\n  " + "\n  ".join(errors))
    return kb.save(Record(new, kb), "set", {"fields": sorted(touched)}, approved=approved)


def _kv(item: str) -> tuple[str, str]:
    if "=" not in item:
        raise KBError(f"expected key=value, got {item!r}")
    return tuple(item.split("=", 1))  # type: ignore[return-value]


def add_rendition(kb: KB, ident: str, path: Path, name: str | None = None, role: str = "derived",
                  fidelity: str | None = None, source: str | None = None, tool: str | None = None,
                  media_type: str | None = None, notes: str | None = None, filename: str | None = None) -> dict:
    rec = kb.get(ident)
    if not path.is_file():
        raise KBError(f"no such file: {path}")
    name = name or _safe_name(path.stem)
    if rec.has_rendition(name):
        raise KBError(f"{rec.id} already has a rendition {name!r}")
    if rec.frozen and role not in ("derived", "metadata"):
        raise KBError(f"{rec.id} is frozen; only derived or metadata renditions can be added after the fact")
    data = copy.deepcopy(rec.data)
    with kb.tx():
        locs, sha, size = store.place(kb, path, filename or _safe_name(name + path.suffix))
        r = {"name": name, "role": role, "media_type": media_type or store.media_type(filename or path.name),
             "sha256": sha, "size": size, "locations": locs}
        if role == "derived":
            if not source or not fidelity:
                raise KBError("a derived rendition needs --from SOURCE and --fidelity")
            src = rec.rendition(source)
            r.update(derived_from=source, fidelity=fidelity,
                     transform={"name": tool or ("llm" if fidelity == "llm" else "manual")})
            if src.get("sha256"):
                r["source_sha256"] = src["sha256"]
        if notes:
            r["notes"] = notes
        data.setdefault("renditions", []).append(r)
        kb.save(Record(data, kb), "rendition", {"added": name})
    return r


def add_location(kb: KB, ref: str, store_name: str | None = None, url: str | None = None,
                 file: Path | None = None) -> dict:
    """Add a mirror to a rendition: a content-store copy or a URL. Allowed on frozen items,
    because the content must hash to what the rendition records."""
    ident, name = parse_ref(ref)
    if name is None:
        raise KBError("name the rendition: ID#NAME")
    rec = kb.get(ident)
    data = copy.deepcopy(rec.data)
    r = next(x for x in data["renditions"] if x["name"] == name) if rec.has_rendition(name) else rec.rendition(name)
    sha = r.get("sha256")
    if store_name or url is None:
        src = file or store.resolve(kb, rec, r, fetch_urls=True)
        if src is None:
            raise KBError(f"{ref}: no local copy to store; give --file")
        got, _ = store.sha256_file(src)
        if sha and got != sha:
            raise KBError(f"{src} has sha256 {got[:12]}…, the rendition records {sha[:12]}…")
        name_ = store_name or store.default_store(kb)
        if not name_:
            raise KBError("no content-addressed store configured")
        store.put_cas(kb, name_, src, got)
        loc = {"type": "cas", "store": name_}
    else:
        loc = store.url_location(url, retrieved=False)
        if sha:
            _, got, _ = store.fetch(kb, url)
            if got != sha:
                raise KBError(f"{url} has sha256 {got[:12]}…, the rendition records {sha[:12]}…")
            from .records import now
            loc["retrieved"] = now()
    if loc not in r["locations"]:
        r["locations"].append(loc)
    kb.save(Record(data, kb), "rendition", {"located": name, "type": loc["type"]})
    return loc


def rendition_path(kb: KB, ref: str, fetch: bool = True) -> Path:
    """A local file with the rendition's content (materialized from the database if need be)."""
    ident, name = parse_ref(ref)
    rec = kb.get(ident)
    if name is None:
        raise KBError("name the rendition: ID#NAME (kb checkout ID writes a whole item to a directory)")
    p = store.resolve(kb, rec, rec.rendition(name), fetch_urls=fetch)
    if p is None:
        raise KBError(f"{ref} is not reachable from here")
    return p


def content_name(rec: Record) -> str:
    for r in rec.renditions:
        if r["role"] == "content":
            return r["name"]
    raise KBError(f"{rec.id} has no content rendition")


def set_content(kb: KB, ident: str, text: str, name: str | None = None) -> Record:
    """Replace the text of a content (or source) rendition of an editable item."""
    rec = kb.get(ident)
    name = name or content_name(rec)
    if rec.frozen:
        raise KBError(f"{rec.id} is frozen: its content cannot change; kb supersede {rec.id}")
    data = copy.deepcopy(rec.data)
    with kb.tx():
        try:
            r = next(x for x in data["renditions"] if x["name"] == name)
        except StopIteration:
            r = {"name": name, "role": "source" if rec.kind == "view" else "content",
                 "media_type": "text/markdown", "locations": [{"type": "db", "filename": name}]}
            data["renditions"].append(r)
        filename = next((l.get("filename") for l in r["locations"] if l["type"] == "db"), None) or f"{name}.md"
        locs, sha, size = store.place_bytes(kb, text.encode(), filename)
        r.update(sha256=sha, size=size, locations=locs)
        return kb.save(Record(data, kb), "content", {"rendition": name})


# -- checkout / checkin -------------------------------------------------------

def _filename(r: dict) -> str | None:
    for l in r.get("locations", []):
        if l["type"] == "db":
            return l.get("filename") or f"{r['name']}{Path(r['name']).suffix or ''}"
    return None


def checkout(kb: KB, ident: str, directory: Path | None = None) -> Path:
    """Write the item (record.json) and its in-database content files into *directory*."""
    rec = kb.get(ident)
    d = (directory or (kb.cfg.kbdir / "checkout" / rec.id)).resolve()
    if d.exists() and any(d.iterdir()) and not (d / CHECKOUT_MARK).exists():
        raise KBError(f"{d} is not empty")
    d.mkdir(parents=True, exist_ok=True)
    (d / "record.json").write_text(dumps(rec.data))
    files = {}
    for r in rec.renditions:
        fn = _filename(r)
        if fn is None:
            continue
        data = rec.content(r["name"])
        if data is None:
            continue
        p = d / fn
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        files[fn] = r["name"]
    (d / CHECKOUT_MARK).write_text(json.dumps({"store": str(kb.root), "id": rec.id, "digest": rec.digest(),
                                               "updated_at": rec.updated_at, "files": files}, indent=2) + "\n")
    return d


def _scan(d: Path) -> list[str]:
    out = []
    for p in sorted(d.rglob("*")):
        rel = str(p.relative_to(d))
        if p.is_file() and rel not in ("record.json", CHECKOUT_MARK) and not any(
                part.startswith(".") or part == "__pycache__" for part in Path(rel).parts):
            out.append(rel)
    return out


def checkin(kb: KB, directory: Path, dry_run: bool = False) -> dict:
    """Apply a checkout's changes: record.json fields (within policy) and content files."""
    d = directory.resolve()
    mark_p = d / CHECKOUT_MARK
    if not mark_p.exists():
        raise KBError(f"{d} is not a kb checkout (no {CHECKOUT_MARK})")
    mark = json.loads(mark_p.read_text())
    rec = kb.get(mark["id"])
    if rec.updated_at != mark.get("updated_at"):
        raise KBError(f"{rec.id} changed in the store since it was checked out; check out again and redo the edit")
    try:
        new = json.loads((d / "record.json").read_text())
    except json.JSONDecodeError as exc:
        raise KBError(f"{d / 'record.json'}: not valid JSON: {exc}") from None
    old = rec.data
    for k in NEVER - {"renditions"}:
        if new.get(k) != old.get(k):
            raise KBError(f"{k} cannot be changed in a checkout")
    if is_frozen(old):
        changed = [k for k in IMMUTABLE if new.get(k) != old.get(k)]
        if changed:
            raise KBError(f"{', '.join(changed)} of {rec.id} cannot change (frozen); kb supersede {rec.id}")
    report = {"id": rec.id, "fields": sorted(k for k in set(new) | set(old)
                                             if k != "renditions" and new.get(k) != old.get(k)),
              "content": [], "added": [], "removed": []}
    by_file = {(_filename(r)): r for r in new.get("renditions", []) if _filename(r)}
    present = set(_scan(d))
    staged: list[tuple[dict, bytes, str]] = []
    for fn, r in by_file.items():
        if fn not in present:
            if is_frozen(old) and r.get("role") in ("original", "content"):
                raise KBError(f"{fn} is missing, but {rec.id} is frozen")
            report["removed"].append(fn)
            continue
        data = (d / fn).read_bytes()
        import hashlib
        if hashlib.sha256(data).hexdigest() != r.get("sha256"):
            if is_frozen(old) and r.get("role") in ("original", "content"):
                raise KBError(f"{fn}: content of frozen {rec.id} cannot change; kb supersede {rec.id}")
            staged.append((r, data, fn))
            report["content"].append(fn)
    for fn in sorted(present - set(by_file)):
        if is_frozen(old):
            raise KBError(f"{fn}: files cannot be added to frozen {rec.id} this way (kb rendition add)")
        role = "source" if old["kind"] == "view" else "content"
        r = {"name": _safe_name(fn.replace("/", "-")), "role": role, "media_type": store.media_type(fn),
             "locations": [{"type": "db", "filename": fn}]}
        new.setdefault("renditions", []).append(r)
        staged.append((r, (d / fn).read_bytes(), fn))
        report["added"].append(fn)
    new["renditions"] = [r for r in new.get("renditions", []) if _filename(r) not in report["removed"]]
    if dry_run:
        for r, data, fn in staged:
            import hashlib
            r["sha256"], r["size"] = hashlib.sha256(data).hexdigest(), len(data)
        errors = schema.validate(new)
        if errors:
            raise KBError(f"{rec.id}:\n  " + "\n  ".join(errors))
        return report
    with kb.tx():
        for r, data, fn in staged:
            locs, sha, size = store.place_bytes(kb, data, fn)
            r.update(sha256=sha, size=size, locations=locs)
        saved = kb.save(Record(new, kb), "checkin", {k: v for k, v in report.items() if k != "id" and v})
    mark["digest"], mark["updated_at"] = saved.digest(), saved.updated_at
    mark["files"] = {_filename(r): r["name"] for r in saved.renditions if _filename(r)}
    mark_p.write_text(json.dumps(mark, indent=2) + "\n")
    return report


def edit(kb: KB, ident: str, what: str = "record") -> dict:
    """Open the item (or one content file) in $EDITOR, then check it back in."""
    rec = kb.get(ident)
    with tempfile.TemporaryDirectory(prefix=f"kb-edit-{rec.id}-") as tmp:
        d = checkout(kb, ident, Path(tmp) / rec.id)
        target = d / "record.json"
        if what != "record":
            r = rec.rendition(what if rec.has_rendition(what) else content_name(rec))
            target = d / _filename(r)
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        subprocess.run([*shlex.split(editor), str(target)], check=False)
        return checkin(kb, d)
