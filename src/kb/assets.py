"""Assets: abstract files and file sets, their versions, and the sources that supply them.

An **asset** is a knowledge item that stands for "a file" or "a set of
files" without saying where the bytes are.  Its identity is its kb ID, which
outlives renames, moves and new content.  Three things hang off it:

* **versions**, each with an abstract *label* (v1, v2, 2026-10-28 ...) and a
  *digest* of its content manifest.  The digest is the version's real
  identity: once recorded it never changes (the store's triggers guard it),
  so a label can never quietly come to mean different bytes.
* **bindings** to concrete *sources*: a directory tree on a filesystem, a
  URL, the store itself (``kb``), and later DataLad, Indico, DocDB ...  Each
  binding carries the source's own concrete identifiers (its *locator*) and
  says how the source's versions relate to the asset's (*versioning*):
  ``none`` (the source has only its current content), ``native`` (its
  version ids are the asset's labels), ``pattern`` (versions found by a path
  pattern such as ``v{version}/``) or ``map`` (an explicit table).
* a **resolution procedure** (``resolve``) that turns (asset, version) into
  one well-defined, verified set of local files, whichever sources happen to
  hold it.

Matching a concrete file set to a version is by content: by default the
manifest of (relative path, sha256) pairs (``match = exact``), or the
multiset of sha256s alone (``match = content``: renames are not new
versions).  For a single-file asset the digest is simply the file's sha256.
"""

import copy
import hashlib
import json
import re
import shutil
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import content
from .errors import KBError
from .items import Store, Item, canonical, now

DEFAULT_PRIORITY = {"kb": 0, "fs": 10, "annex": 20, "docdb": 40, "url": 50, "indico": 60}


# -- manifests and digests ------------------------------------------------------------

def norm(path: str) -> str:
    """A relative path in canonical form: forward slashes, no '.' parts, no leading slash."""
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]
    if ".." in parts:
        raise KBError(f"path {path!r} leaves the file set")
    return "/".join(parts)


def digest_of(files: list[dict], asset_type: str, match: str = "exact") -> str:
    if asset_type == "file":
        if len(files) != 1:
            raise KBError(f"a file asset has exactly one file, not {len(files)}")
        return files[0]["sha256"]
    if match == "content":
        return hashlib.sha256(canonical(sorted(f["sha256"] for f in files))).hexdigest()
    return hashlib.sha256(canonical(sorted([norm(f["path"]), f["sha256"]] for f in files))).hexdigest()


# -- offers: what a source has for an asset ---------------------------------------------

@dataclass
class CFile:
    path: str
    size: int | None = None
    sha256: str | None = None
    local: Path | None = None                       # where the bytes are, if already local
    fetch: Callable[[], Path] | None = None         # how to get them otherwise

    def path_here(self) -> Path:
        if self.local is None:
            if self.fetch is None:
                raise KBError(f"{self.path}: no way to get its content")
            self.local = self.fetch()
        return self.local


@dataclass
class Offer:
    binding: str
    source: str
    native: str | None                  # the source's own version id, if it has one
    label: str | None                   # the asset's label this maps to, if the binding says
    files: list[CFile]
    where: str
    _digest: str | None = None

    def manifest(self, kb: Store) -> list[dict]:
        out = []
        for f in self.files:
            if f.sha256 is None:
                f.sha256, f.size = hash_file(kb, f.path_here())
            out.append({"path": norm(f.path), "sha256": f.sha256, "size": f.size,
                        "media_type": content.media_type(f.path)})
        return sorted(out, key=lambda x: x["path"])

    def digest(self, kb: Store, asset_type: str, match: str) -> str:
        if self._digest is None:
            self._digest = digest_of(self.manifest(kb), asset_type, match)
        return self._digest


_hash_cache: dict | None = None


def hash_file(kb: Store, path: Path) -> tuple[str, int]:
    """sha256 and size, cached by (path, size, mtime) in the user cache so large trees hash once."""
    global _hash_cache
    cache_file = kb.cfg.cache_dir / "fshash.json"
    if _hash_cache is None:
        try:
            _hash_cache = json.loads(cache_file.read_text())
        except (OSError, json.JSONDecodeError):
            _hash_cache = {}
    st = path.stat()
    key = str(path.resolve())
    hit = _hash_cache.get(key)
    if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
        return hit[2], st.st_size
    sha, size = content.sha256_file(path)
    _hash_cache[key] = [st.st_size, st.st_mtime_ns, sha]
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(_hash_cache))
    except OSError:
        pass
    return sha, size


# -- source drivers -------------------------------------------------------------------

class AssetSource:
    """A kind of place that can supply versions of assets."""
    type = ""
    status = "implemented"        # or "planned"
    describe_text = ""

    def check_locator(self, locator: dict, asset_type: str) -> None:
        pass

    def offers(self, kb: Store, asset: Item, binding: dict, remote: bool = True) -> list[Offer]:
        raise NotImplementedError

    def describe(self, kb: Store, binding: dict) -> str:
        return f"{self.type}: {json.dumps(binding['locator'])}"


def _label_for(binding: dict, native: str | None) -> str | None:
    v = binding.get("versioning", "none")
    if native is None or v == "none":
        return None
    if v == "map":
        return binding.get("map", {}).get(native)
    return binding.get("map", {}).get(native, native)    # native/pattern: the id is the label unless mapped


def _pattern_regex(pattern: str) -> re.Pattern:
    if pattern.count("{version}") != 1:
        raise KBError(f"pattern {pattern!r} must contain {{version}} exactly once")
    head, tail = pattern.split("{version}")
    parts = [re.escape(head), r"(?P<version>[^/]+)", re.escape(tail).replace(r"\*", "[^/]*")]
    return re.compile("^" + "".join(parts) + "$")


class FsSource(AssetSource):
    """A directory tree reachable from this kb.  locator: {root: NAME or absolute path, path: relative}.

    Named roots are mapped per machine in the setting ``filesystems.NAME.path``
    (store settings, user config, or .kb/local.toml), so the same binding works
    wherever the tree is mounted.
    """
    type = "fs"
    describe_text = "a directory tree (local disk, NFS, EOS mount ...)"

    def check_locator(self, locator, asset_type):
        if "root" not in locator:
            raise KBError("an fs binding needs root (a named filesystem or an absolute path) and path")

    def base(self, kb: Store, locator: dict) -> Path | None:
        root = locator["root"]
        if root.startswith("/") or root.startswith("~"):
            r = Path(root).expanduser()
        else:
            p = kb.cfg.raw.get("filesystems", {}).get(root, {}).get("path")
            if not p:
                return None
            r = Path(p).expanduser()
        return r / locator.get("path", "") if locator.get("path") else r

    def _files_under(self, d: Path) -> list[CFile]:
        out = []
        for p in sorted(d.rglob("*")):
            rel = p.relative_to(d)
            if p.is_file() and not any(part.startswith(".") for part in rel.parts):
                out.append(CFile(str(rel), local=p))
        return out

    def offers(self, kb, asset, binding, remote=True):
        base = self.base(kb, binding["locator"])
        where = f"{binding['locator']['root']}:{binding['locator'].get('path', '')}"
        if base is None or not base.exists():
            return []
        atype = asset.data["asset_type"]
        versioning = binding.get("versioning", "none")
        if versioning == "none":
            if atype == "file":
                if not base.is_file():
                    raise KBError(f"binding {binding['name']}: {base} is not a file")
                return [Offer(binding["name"], self.type, None, None, [CFile(base.name, local=base)], where)]
            if not base.is_dir():
                raise KBError(f"binding {binding['name']}: {base} is not a directory")
            return [Offer(binding["name"], self.type, None, None, self._files_under(base), where)]
        pattern = binding.get("pattern")
        if not pattern:
            raise KBError(f"binding {binding['name']}: versioning {versioning} on a filesystem needs a pattern "
                          "such as '{version}' (subdirectories) or 'slides-{version}.pdf'")
        rx = _pattern_regex(pattern.rstrip("/"))
        out = []
        for p in sorted(base.rglob("*")) if base.is_dir() else []:
            rel = str(p.relative_to(base))
            m = rx.match(rel)
            if not m:
                continue
            native = m["version"]
            if atype == "file" and p.is_file():
                out.append(Offer(binding["name"], self.type, native, _label_for(binding, native),
                                     [CFile(p.name, local=p)], f"{where}/{rel}"))
            elif atype == "fileset" and p.is_dir():
                out.append(Offer(binding["name"], self.type, native, _label_for(binding, native),
                                     self._files_under(p), f"{where}/{rel}"))
        return out

    def describe(self, kb, binding):
        base = self.base(kb, binding["locator"])
        v = binding.get("versioning", "none")
        extra = "" if v == "none" else f", versions by {binding.get('pattern', v)}"
        return (f"filesystem {binding['locator']['root']}:{binding['locator'].get('path', '')}"
                + (f" ({base})" if base else " (root not mapped on this machine)") + extra)


class KbSource(AssetSource):
    """Versions whose bytes the store itself holds (in the database or its content store)."""
    type = "kb"
    describe_text = "the store itself (small files in the database, large ones in a content store)"

    def offers(self, kb, asset, binding, remote=True):
        out = []
        for v in asset.data.get("versions", []):
            kept = (v.get("sightings") and any(s["binding"] == binding["name"] for s in v["sightings"]))
            if not kept or not v.get("files"):
                continue
            files = []
            for f in v["files"]:
                rend = {"name": f["path"], "sha256": f["sha256"], "locations": [{"type": "db", "filename": f["path"]}]}
                p = content.resolve(kb, None, rend, fetch_urls=False)
                if p is None:
                    rend["locations"] = [{"type": "cas", "name": binding["locator"].get("cas") or "default"}]
                    p = content.resolve(kb, None, rend, fetch_urls=False)
                if p is None:
                    break
                files.append(CFile(f["path"], f.get("size"), f["sha256"], p))
            else:
                out.append(Offer(binding["name"], self.type, v["label"], v["label"], files, "this store"))
        return out

    def describe(self, kb, binding):
        return "this store"


class UrlSource(AssetSource):
    """Web resources: locator {url} for a file, {urls: {path: url}} for a file set. Unversioned."""
    type = "url"
    describe_text = "web URLs (current content only)"

    def check_locator(self, locator, asset_type):
        if asset_type == "file" and "url" not in locator:
            raise KBError("a url binding for a file needs url")
        if asset_type == "fileset" and "urls" not in locator:
            raise KBError("a url binding for a file set needs urls: {path: url}")

    def offers(self, kb, asset, binding, remote=True):
        if not remote:
            return []
        loc = binding["locator"]
        pairs = [(Path(urllib.parse.urlparse(loc["url"]).path).name or "file", loc["url"])] \
            if asset.data["asset_type"] == "file" else sorted(loc["urls"].items())
        files = [CFile(path, fetch=(lambda u=url: content.fetch(kb, u)[0])) for path, url in pairs]
        return [Offer(binding["name"], self.type, None, None, files, loc.get("url") or f"{len(pairs)} URLs")]

    def describe(self, kb, binding):
        loc = binding["locator"]
        return loc.get("url") or f"{len(loc.get('urls', {}))} URLs"


class PlannedSource(AssetSource):
    status = "planned"

    def __init__(self, type_, text, locator_doc):
        self.type, self.describe_text, self.locator_doc = type_, text, locator_doc

    def offers(self, kb, asset, binding, remote=True):
        raise KBError(f"source type {self.type} is planned, not implemented yet ({self.describe_text})")


SOURCES: dict[str, AssetSource] = {}


def register_source(s: AssetSource) -> None:
    SOURCES[s.type] = s


for _s in (KbSource(), FsSource(), UrlSource(),
           PlannedSource("annex", "a DataLad dataset: versions are commits or tags (spike S3)",
                         "{dataset, path}"),
           PlannedSource("indico", "Indico materials: attachments of an event or contribution",
                         "{server, event, contribution?, attachment?}"),
           PlannedSource("docdb", "a DocDB document: each version has its own file set",
                         "{server, docid}")):
    register_source(_s)


def driver(binding: dict) -> AssetSource:
    try:
        return SOURCES[binding["type"]]
    except KeyError:
        raise KBError(f"no asset source type {binding['type']!r} (have: {', '.join(SOURCES)})") from None


def ordered_bindings(asset: Item, only: list[str] | None = None) -> list[dict]:
    bs = [b for b in asset.data.get("bindings", []) if not only or b["name"] in only]
    return sorted(bs, key=lambda b: (b.get("priority", DEFAULT_PRIORITY.get(b["type"], 30)),
                                     asset.data["bindings"].index(b)))


# -- authoring --------------------------------------------------------------------------

def get_asset(kb: Store, ref: str) -> Item:
    rec = kb.get(ref)
    if rec.kind != "asset":
        raise KBError(f"{rec.id} is a {rec.kind}, not an asset")
    return rec


def new(kb: Store, asset_type: str, fields: dict, match: str = "exact") -> Item:
    from . import authoring
    return authoring.new(kb, "asset", fields | {"asset_type": asset_type, "match": match,
                                                 "versions": [], "bindings": []})


def _save(kb: Store, asset: Item, data: dict, action: str, detail: dict) -> Item:
    return kb.save(Item(data, kb), action, detail)


def bind(kb: Store, ref: str, name: str, source_type: str, locator: dict, versioning: str = "none",
         pattern: str | None = None, mapping: dict | None = None, priority: int | None = None,
         note: str | None = None) -> Item:
    asset = get_asset(kb, ref)
    if any(b["name"] == name for b in asset.data.get("bindings", [])):
        raise KBError(f"{asset.id} already has a binding {name!r}")
    b = {"name": name, "type": source_type, "locator": locator, "versioning": versioning}
    d = driver(b)
    d.check_locator(locator, asset.data["asset_type"])
    if pattern:
        _pattern_regex(pattern.rstrip("/"))
        b["pattern"] = pattern
    if mapping:
        b["map"] = mapping
    if priority is not None:
        b["priority"] = priority
    if note:
        b["note"] = note
    data = copy.deepcopy(asset.data)
    data.setdefault("bindings", []).append(b)
    return _save(kb, asset, data, "bind", {"binding": name, "type": source_type})


def unbind(kb: Store, ref: str, name: str) -> Item:
    asset = get_asset(kb, ref)
    data = copy.deepcopy(asset.data)
    before = len(data.get("bindings", []))
    data["bindings"] = [b for b in data.get("bindings", []) if b["name"] != name]
    if len(data["bindings"]) == before:
        raise KBError(f"{asset.id} has no binding {name!r}")
    return _save(kb, asset, data, "unbind", {"binding": name})


# -- scanning and registering -------------------------------------------------------------

@dataclass
class ScanResult:
    offer: Offer
    status: str                    # known | new | conflict | unreachable
    version: str | None = None     # the version it is (known) or claims to be (conflict)
    digest: str | None = None
    detail: str = ""

    def as_dict(self):
        c = self.offer
        return {"binding": c.binding, "source": c.source, "native": c.native, "label": c.label,
                "where": c.where, "files": len(c.files), "status": self.status, "version": self.version,
                "digest": self.digest, "detail": self.detail}


def scan(kb: Store, ref: str, only: list[str] | None = None, remote: bool = True) -> list[ScanResult]:
    """Ask every bound source what it offers, and relate each offer to the asset's versions."""
    asset = get_asset(kb, ref)
    atype, match = asset.data["asset_type"], asset.data.get("match", "exact")
    by_digest = {v["digest"]: v["label"] for v in asset.data.get("versions", []) if v.get("digest")}
    by_label = {v["label"]: v for v in asset.data.get("versions", [])}
    out = []
    for b in ordered_bindings(asset, only):
        try:
            offers_ = driver(b).offers(kb, asset, b, remote=remote)
        except KBError as exc:
            out.append(ScanResult(Offer(b["name"], b["type"], None, None, [], driver(b).describe(kb, b)
                                      if b["type"] in SOURCES else b["type"]), "unreachable", detail=str(exc)))
            continue
        if not offers_:
            out.append(ScanResult(Offer(b["name"], b["type"], None, None, [], driver(b).describe(kb, b)),
                            "unreachable", detail="the source offers nothing here (not mounted, empty, or remote off)"))
        for c in offers_:
            try:
                dg = c.digest(kb, atype, match)
            except KBError as exc:
                out.append(ScanResult(c, "unreachable", detail=str(exc)))
                continue
            if dg in by_digest:
                st = ScanResult(c, "known", by_digest[dg], dg)
                if c.label and c.label != by_digest[dg]:
                    st.detail = f"the source calls it {c.label}"
            elif c.label and c.label in by_label and by_label[c.label].get("digest"):
                st = ScanResult(c, "conflict", c.label, dg, f"the source's {c.native} maps to {c.label}, "
                                                      f"but its content differs from {c.label}")
            else:
                st = ScanResult(c, "new", c.label, dg)
            out.append(st)
    # Intra-source ambiguity: one binding offering different content under one label.
    labels: dict[tuple, set] = {}
    for s in out:
        if s.offer.label and s.digest:
            labels.setdefault((s.offer.binding, s.offer.label), set()).add(s.digest)
    for s in out:
        if s.offer.label and len(labels.get((s.offer.binding, s.offer.label), ())) > 1:
            s.status, s.detail = "conflict", f"{s.offer.binding} offers different content as {s.offer.label}"
    return out


def _next_label(asset: dict) -> str:
    used = {v["label"] for v in asset.get("versions", [])}
    n = len(asset.get("versions", [])) + 1
    while f"v{n}" in used:
        n += 1
    return f"v{n}"


def register(kb: Store, ref: str, seen: list[ScanResult] | None = None, keep: str = "auto",
             label: str | None = None, note: str | None = None) -> list[dict]:
    """Item new versions found by a scan (and fill digests of versions known only by label).

    keep: copy the content into the store (``yes``), don't (``no``), or ``auto``:
    keep it when it came from a source without versions (it may be overwritten
    there, as Indico materials or a plain directory are) and it fits.
    """
    asset = get_asset(kb, ref)
    seen = seen if seen is not None else scan(kb, ref)
    data = copy.deepcopy(asset.data)
    atype, match = data["asset_type"], data.get("match", "exact")
    versions = data.setdefault("versions", [])
    by_digest = {v.get("digest"): v for v in versions if v.get("digest")}
    by_label = {v["label"]: v for v in versions}
    done, kept = [], []
    bindings = {b["name"]: b for b in data.get("bindings", [])}
    when = now()
    for s in seen:
        if s.status == "known" or (s.status == "new" and s.digest in by_digest):
            v = by_digest[s.digest]                    # also: offered by several sources in this scan
            if not any(x["binding"] == s.offer.binding and x.get("native") == s.offer.native
                       for x in v.get("sightings", [])):
                v.setdefault("sightings", []).append(_sighting(s.offer, when))
            continue
        if s.status != "new":
            continue
        c = s.offer
        lab = c.label or (label if label and label not in by_label else None) or _next_label(data)
        if lab in by_label and not by_label[lab].get("digest"):
            v = by_label[lab]                            # known by label only: first sight fills it in
        elif lab in by_label:
            continue
        else:
            v = {"label": lab}
            versions.append(v)
            by_label[lab] = v
        v["files"] = c.manifest(kb)
        v["digest"] = s.digest
        v["registered"] = when
        if note:
            v["note"] = note
        v.setdefault("sightings", []).append(_sighting(c, when))
        by_digest[s.digest] = v
        unversioned = bindings.get(c.binding, {}).get("versioning", "none") == "none"
        total = sum(f.get("size") or 0 for f in v["files"])
        if keep == "yes" or (keep == "auto" and unversioned and c.source != "kb"
                             and (total <= kb.cfg.max_inline_bytes * 10 or content.default_cas(kb))):
            kept.append((v, c))
        done.append({"label": lab, "digest": s.digest, "files": len(v["files"]), "from": c.binding,
                     "native": c.native})
    if not done and not any(s.status == "known" for s in seen):
        return []
    with kb.tx():
        for v, c in kept:
            _keep(kb, data, v, c)
            done[[d["label"] for d in done].index(v["label"])]["kept"] = True
        kb.save(Item(data, kb), "register", {"versions": [d["label"] for d in done]})
    return done


def _sighting(c: Offer, when: str) -> dict:
    e = {"binding": c.binding, "at": when}
    if c.native:
        e["native"] = c.native
    return e


def _kb_binding(data: dict) -> dict:
    for b in data.get("bindings", []):
        if b["type"] == "kb":
            return b
    b = {"name": "kb", "type": "kb", "locator": {}, "versioning": "native"}
    data.setdefault("bindings", []).insert(0, b)
    return b


def _keep(kb: Store, data: dict, v: dict, c: Offer) -> None:
    """Copy a version's files into the store, and say so with a seen-entry for the kb binding."""
    b = _kb_binding(data)
    for f in c.files:
        p = f.path_here()
        locs, _, _ = content.place(kb, p, norm(f.path), "auto")
        if locs and locs[0]["type"] == "local":
            raise KBError(f"{f.path} is too large for the store and no content store is configured")
        if locs and locs[0]["type"] == "cas":
            b["locator"]["cas"] = locs[0]["name"]
    v.setdefault("sightings", []).append({"binding": b["name"], "native": v["label"], "at": now()})


def add_version(kb: Store, ref: str, path: Path, label: str | None = None, note: str | None = None) -> dict:
    """Register local files (a file, or a directory for a file set) as a new version kept in the store."""
    asset = get_asset(kb, ref)
    atype = asset.data["asset_type"]
    if atype == "file":
        if not path.is_file():
            raise KBError(f"{path} is not a file")
        files = [CFile(path.name, local=path)]
    else:
        if not path.is_dir():
            raise KBError(f"{path} is not a directory")
        files = FsSource()._files_under(path)
    c = Offer("kb", "kb", label, label, files, str(path))
    dg = c.digest(kb, atype, asset.data.get("match", "exact"))
    known = {v.get("digest"): v["label"] for v in asset.data.get("versions", [])}
    if dg in known:
        raise KBError(f"this content is already version {known[dg]} of {asset.id}")
    done = register(kb, ref, [ScanResult(c, "new", label, dg)], keep="yes", label=label, note=note)
    return done[0]


# -- resolving -------------------------------------------------------------------------------

@dataclass
class Resolution:
    asset: str
    version: str | None
    digest: str
    binding: str
    source: str
    where: str
    files: dict[str, Path]
    notes: list[str] = field(default_factory=list)

    def as_dict(self):
        return {"asset": self.asset, "version": self.version, "digest": self.digest, "binding": self.binding,
                "source": self.source, "where": self.where, "files": {k: str(v) for k, v in self.files.items()},
                "notes": self.notes}


def _target(asset: Item, version: str) -> dict | None:
    vs = asset.data.get("versions", [])
    if version == "latest":
        if not vs:
            raise KBError(f"{asset.id} has no registered versions yet (kb asset scan --register)")
        return vs[-1]
    if version == "current":
        return None
    if version.startswith("digest:"):
        for v in vs:
            if v.get("digest") == version[7:]:
                return v
        raise KBError(f"{asset.id} has no version with digest {version[7:19]}…")
    for v in vs:
        if v["label"] == version:
            return v
    raise KBError(f"{asset.id} has no version {version!r} (have: {', '.join(v['label'] for v in vs) or 'none'})")


def resolve(kb: Store, ref: str, version: str = "latest", path: str | None = None, remote: bool = True) -> Resolution:
    """One well-defined, verified file set (or file) for (asset, version).

    1. The target: a registered version (by label, ``latest`` or ``digest:...``),
       or ``current``: whatever the highest-priority source offers now.
    2. Sources are tried in priority order (bindings' priority, else kb, fs,
       annex, docdb, url, indico).  A source's offer is considered if its label
       is the target's, or it is unversioned (it may hold any version).
    3. With the target's digest known, the first offer whose content has that
       digest wins.  Offers with the right label but other content are
       divergent: noted, and never used.
    4. With only a label known (registered by name, not yet seen), all offers
       under that label must agree; disagreement is an error to resolve by
       registering one of them (kb asset scan, then register).
    """
    asset = get_asset(kb, ref)
    atype, match = asset.data["asset_type"], asset.data.get("match", "exact")
    target = _target(asset, version)
    notes: list[str] = []
    chosen: Offer | None = None
    if target is None:                                    # current
        for b in ordered_bindings(asset):
            if b["type"] in SOURCES and SOURCES[b["type"]].status == "implemented" and b["type"] != "kb":
                try:
                    offers_ = driver(b).offers(kb, asset, b, remote)
                except KBError as exc:
                    notes.append(f"{b['name']}: {exc}")
                    continue
                if offers_:
                    chosen = max(offers_, key=lambda c: _natural(c.native or ""))
                    break
        if chosen is None:
            raise KBError(f"no source offers {asset.id} here" + (f" ({'; '.join(notes)})" if notes else ""))
        dg = chosen.digest(kb, atype, match)
        known = {v.get("digest"): v["label"] for v in asset.data.get("versions", [])}
        label = known.get(dg)
        if label is None:
            notes.append("this content is not a registered version (kb asset scan --register)")
    else:
        label, want = target["label"], target.get("digest")
        agreeing: list[tuple[Offer, str]] = []
        others: list[Offer] = []
        for b in ordered_bindings(asset):
            if b["type"] not in SOURCES or SOURCES[b["type"]].status != "implemented":
                continue
            try:
                offers_ = driver(b).offers(kb, asset, b, remote)
            except KBError as exc:
                notes.append(f"{b['name']}: {exc}")
                continue
            for c in offers_:
                if c.label is not None and c.label != label:
                    others.append(c)          # differently labelled: only checked if nothing else matches
                    continue
                try:
                    dg = c.digest(kb, atype, match)
                except KBError as exc:
                    notes.append(f"{b['name']}: {exc}")
                    continue
                if want is not None:
                    if dg == want:
                        chosen = c
                        break
                    if c.label == label:
                        notes.append(f"{b['name']} ({c.where}) offers {label} with different content: divergent, not used")
                else:
                    agreeing.append((c, dg))
            if chosen is not None:
                break
        if chosen is None and want is not None:
            for c in others:                  # the same content, under another name in some source
                try:
                    if c.digest(kb, atype, match) == want:
                        chosen = c
                        notes.append(f"found as {c.label} in {c.binding}: same content as {label}")
                        break
                except KBError:
                    continue
        if want is None and chosen is None:
            labelled = [(c, d) for c, d in agreeing if c.label == label]
            if not labelled:
                raise KBError(f"no source offers {asset.id} {label} here" + (f" ({'; '.join(notes)})" if notes else ""))
            if len({d for _, d in labelled}) > 1:
                raise KBError(f"{asset.id} {label} is ambiguous: sources offer different content under that label: "
                              + "; ".join(f"{c.binding} ({c.where}): {d[:12]}…" for c, d in labelled)
                              + ". Register the right one (kb asset scan --register).")
            chosen = labelled[0][0]
            notes.append(f"{label} was known only by name; its content is now pinned by registering it")
        if chosen is None:
            raise KBError(f"no reachable source holds {asset.id} {label} (digest {want[:12]}…)"
                          + (f": {'; '.join(notes)}" if notes else ""))
        dg = chosen.digest(kb, atype, match)
    files = {norm(f.path): f.path_here() for f in chosen.files}
    if path is not None:
        key = norm(path) if atype == "fileset" else next(iter(files))
        if key not in files:
            raise KBError(f"{asset.id} {label or 'current'} has no file {path!r}")
        files = {key: files[key]}
    return Resolution(asset.id, label, dg, chosen.binding, chosen.source, chosen.where, files, notes)


def _natural(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def materialize(res: Resolution, dest: Path) -> list[Path]:
    out = []
    for rel, src in res.files.items():
        d = dest / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, d)
        out.append(d)
    return out


def sources_table() -> list[dict]:
    return [{"interface": "asset", "type": s.type, "status": s.status, "what": s.describe_text,
             "locator": getattr(s, "locator_doc", "")} for s in SOURCES.values()]

