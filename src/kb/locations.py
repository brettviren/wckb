"""Data sources: where a rendition's bytes can be found, behind one interface.

A rendition lists locations; each location's ``type`` names the source that
serves it.  Sources resolve a location to a local file (fetching or
materializing as needed), describe it for people, and say whether it is
reachable.  New kinds of storage (a DataLad dataset, an S3 bucket, an
institutional repository) are added by registering a LocationSource subclass;
nothing else in kb needs to change.

    db     content held in the store's own database (small files)
    cas    a content-addressed directory store (EOS, NFS ...), by sha256
    url    a web resource; fetched into the cache and checked by hash
    local  a file on one host only
    asset  a file of an asset (an abstract file or file set) at a version; the
           asset's own bindings say which sources supply it (kb.assets)
    datalad  a file in a DataLad dataset, described by a source item (see kb.datasets)
    source   a file in another source that is a directory tree (a git repository, a named filesystem
             tree), described by a source item (see kb.sources)
"""

import shutil
import subprocess
from pathlib import Path

from .errors import KBError


def cache_path(kb, sha: str) -> Path:
    """Where content from the database is materialized for tools (the store's own cache)."""
    return kb.cfg.cache_dir / "sha256" / sha[:2] / sha


def url_cache_path(kb, sha: str) -> Path:
    """Where downloaded URL content is kept: the url source directory (sources.url.dir), shared by stores."""
    from .config import source_dir
    return source_dir(kb.cfg.raw, "url") / "sha256" / sha[:2] / sha


def blob_path(root: Path, sha: str) -> Path:
    return root / "sha256" / sha[:2] / sha[2:4] / sha


class LocationSource:
    type = ""
    label = ""

    def resolve(self, kb, rec, rendition: dict, loc: dict, fetch: bool) -> Path | None:
        raise NotImplementedError

    def describe(self, kb, loc: dict) -> str:
        return self.label

    def available(self, kb, rendition: dict, loc: dict) -> bool:
        return self.resolve(kb, None, rendition, loc, fetch=False) is not None

    def link(self, loc: dict) -> str | None:
        """A URL a person can follow, if there is one."""
        return None


class DbSource(LocationSource):
    type, label = "db", "in this store"

    def resolve(self, kb, rec, rendition, loc, fetch):
        sha = rendition.get("sha256")
        if not sha:
            return None
        p = cache_path(kb, sha)
        if loc.get("filename"):   # keep the file's name: tools (and people) care about it
            p = p.with_name(sha + ".d") / Path(loc["filename"]).name
        if p.is_file():
            return p
        data = kb.blob(sha)
        if data is None:
            return None
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".partial")
        tmp.write_bytes(data)
        tmp.replace(p)
        return p

    def available(self, kb, rendition, loc):
        return bool(rendition.get("sha256")) and kb.has_blob(rendition["sha256"])

    def describe(self, kb, loc):
        return "in this store" + (f" as {loc['filename']}" if loc.get("filename") else "")


class CasSource(LocationSource):
    type, label = "cas", "content-addressed store"

    def resolve(self, kb, rec, rendition, loc, fetch):
        sha = rendition.get("sha256")
        root = kb.cfg.cas.get(loc["name"])
        if not sha or root is None:
            return None
        p = blob_path(root, sha)
        return p if p.is_file() else None

    def describe(self, kb, loc):
        root = kb.cfg.cas.get(loc["name"])
        return f"content store {loc['name']}" + (f" ({root})" if root else " (not mounted here)")


class UrlSource(LocationSource):
    type, label = "url", "on the web"

    def resolve(self, kb, rec, rendition, loc, fetch):
        sha = rendition.get("sha256")
        if sha and url_cache_path(kb, sha).is_file():
            return url_cache_path(kb, sha)
        if not fetch:
            return None
        from .content import fetch as do_fetch
        path, got, _ = do_fetch(kb, loc["url"])
        if sha and got != sha:
            who = f"{rec.id}#{rendition['name']}" if rec is not None else rendition["name"]
            raise KBError(f"{who}: {loc['url']} now has sha256 {got[:12]}…, recorded {sha[:12]}… "
                          "(content changed upstream; see kb rendition verify)")
        return path

    def available(self, kb, rendition, loc):
        return True   # assume reachable; kb rendition verify checks

    def describe(self, kb, loc):
        return loc["url"] + (f" (needs {loc['auth']})" if loc.get("auth") else "")

    def link(self, loc):
        return loc["url"]


class LocalSource(LocationSource):
    type, label = "local", "on one machine"

    def resolve(self, kb, rec, rendition, loc, fetch):
        if loc["host"] != kb.cfg.host:
            return None
        p = Path(loc["path"])
        return p if p.is_file() else None

    def describe(self, kb, loc):
        return f"only on {loc['host']}: {loc['path']}"


class DataladSource(LocationSource):
    """A file in a DataLad dataset: {source: src-..., path, key?}.  The source item says which dataset;
    kb.datasets finds (or makes) the local clone and gets the content."""
    type, label = "datalad", "DataLad dataset"

    def resolve(self, kb, rec, rendition, loc, fetch):
        from . import datasets
        src = kb.items.get(loc["source"])
        if src is None:
            return None
        fetch = fetch and datasets.available() is None       # without DataLad, only what is already here
        root = datasets.locate(kb, src, clone=fetch)
        if root is None:
            return None
        p = datasets.key_content(root, loc["key"], fetch) if loc.get("key") else None
        if p is None:
            f = root / loc["path"]
            if f.exists():
                p = f
            elif fetch:
                p = datasets.get(root, loc["path"])
        if p is None:
            return None
        sha = rendition.get("sha256")
        if sha:
            from .content import sha256_file
            if sha256_file(p)[0] != sha:
                return None          # the dataset now has other bytes at that path
        return p

    def available(self, kb, rendition, loc):
        return loc["source"] in kb.items

    def describe(self, kb, loc):
        from . import datasets
        src = kb.items.get(loc["source"])
        if src is None:
            return f"DataLad dataset {loc['source']} (source not in this store): {loc['path']}"
        root = datasets.locate(kb, src)
        return (f"DataLad dataset {src.data.get('title')}: {loc['path']}"
                + (f" (clone at {root})" if root else " (not cloned here; fetched on demand)"))

    def link(self, loc):
        return None


class TreeSource(LocationSource):
    """A file in a source item's directory tree (git, filesystem): {source: src-..., path}.  The source's
    type finds the tree on this machine; the file must still have the recorded bytes."""
    type, label = "source", "a source's directory tree"

    def resolve(self, kb, rec, rendition, loc, fetch):
        from .sources import TYPES
        src = kb.items.get(loc["source"])
        t = TYPES.get(src.data.get("source_type")) if src is not None else None
        root = t.locate(kb, src) if t else None
        if root is None and fetch and src is not None and src.data.get("source_type") == "git":
            from . import datasets
            root = datasets.locate(kb, src, clone=True)
        if root is None:
            return None
        p = Path(root) / loc["path"]
        if not p.is_file():
            return None
        sha = rendition.get("sha256")
        if sha:
            from .content import sha256_file
            if sha256_file(p)[0] != sha:
                return None          # the tree now has other bytes at that path
        return p

    def available(self, kb, rendition, loc):
        return loc["source"] in kb.items

    def describe(self, kb, loc):
        src = kb.items.get(loc["source"])
        if src is None:
            return f"source {loc['source']} (not in this store): {loc['path']}"
        return f"{src.data.get('source_type')} source {src.data.get('title')}: {loc['path']}"


class AssetLocation(LocationSource):
    """A rendition supplied by an asset (an abstract file or file set) at a version: see kb.assets."""
    type, label = "asset", "an asset"

    def resolve(self, kb, rec, rendition, loc, fetch):
        from .assets import resolve as resolve_asset
        if loc["asset"] not in kb.items:
            return None
        try:
            res = resolve_asset(kb, loc["asset"], loc["version"], loc.get("path"), remote=fetch)
        except KBError:
            return None
        p = next(iter(res.files.values()))
        sha = rendition.get("sha256")
        if sha:
            from .content import sha256_file
            if sha256_file(p)[0] != sha:
                return None          # the asset's file is not the bytes this rendition recorded
        return p

    def available(self, kb, rendition, loc):
        return loc["asset"] in kb.items

    def describe(self, kb, loc):
        rec = kb.items.get(loc["asset"])
        what = f"asset {loc['asset']}" + (f" ({rec.data.get('title')})" if rec else " (not in this store)")
        return f"{what}, version {loc['version']}" + (f", {loc['path']}" if loc.get("path") else "")


REGISTRY: dict[str, LocationSource] = {}

# Resolution order: cheapest and most local first; URLs (network) last.
ORDER = ["db", "cas", "local", "asset", "datalad", "source", "url"]


def register(source: LocationSource, position: int | None = None) -> None:
    REGISTRY[source.type] = source
    if source.type not in ORDER:
        ORDER.insert(len(ORDER) - 1 if position is None else position, source.type)


for _s in (DbSource(), CasSource(), UrlSource(), LocalSource(), AssetLocation(), DataladSource(), TreeSource()):
    register(_s)


def sources_table() -> list[dict]:
    from . import datasets
    out = []
    for t in ORDER:
        if t in REGISTRY:
            why = datasets.available() if t == "datalad" else None
            out.append({"interface": "location", "type": t, "status": "implemented", "what": REGISTRY[t].label,
                        "available": why is None, "needs": f"{why}: {datasets.INSTALL}" if why else ""})
    return out


def for_location(loc: dict) -> LocationSource:
    try:
        return REGISTRY[loc["type"]]
    except KeyError:
        raise KBError(f"no data source for location type {loc.get('type')!r}") from None


def resolve(kb, rec, rendition: dict, fetch: bool = True) -> Path | None:
    """A local file holding the rendition's content: first whatever is already here, trying sources in
    ORDER, then (with FETCH) whatever must be fetched or cloned, in the same order (URLs last)."""
    locs = sorted(rendition.get("locations", []),
                  key=lambda l: ORDER.index(l["type"]) if l["type"] in ORDER else len(ORDER))
    sha = rendition.get("sha256")
    if sha and url_cache_path(kb, sha).is_file() and not any(l["type"] == "db" for l in locs):
        return url_cache_path(kb, sha)
    for passes in ([False, True] if fetch else [False]):
        for loc in locs:
            p = for_location(loc).resolve(kb, rec, rendition, loc, fetch=passes)
            if p is not None:
                return p
    return None


def describe(kb, rendition: dict) -> list[str]:
    return [for_location(l).describe(kb, l) for l in rendition.get("locations", [])]


def links(rendition: dict) -> list[str]:
    return [u for u in (for_location(l).link(l) for l in rendition.get("locations", [])) if u]
