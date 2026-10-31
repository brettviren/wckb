"""Data sources: where a rendition's bytes can be found, behind one interface.

A rendition lists locations; each location's ``type`` names the source that
serves it.  Sources resolve a location to a local file (fetching or
materializing as needed), describe it for people, and say whether it is
reachable.  New kinds of storage (a DataLad dataset, an S3 bucket, an
institutional repository) are added by registering a Source subclass;
nothing else in kb needs to change.

    db     content held in the store's own database (small files)
    cas    a content-addressed directory store (EOS, NFS ...), by sha256
    url    a web resource; fetched into the cache and checked by hash
    local  a file on one host only
    asset  a file of an asset (an abstract file or file set) at a version; the
           asset's own bindings say which sources supply it (kb.assets)
    annex  a file in a DataLad dataset (experimental; see decisions/0001, spike S3)
"""

import shutil
import subprocess
from pathlib import Path

from .errors import KBError


def cache_path(kb, sha: str) -> Path:
    return kb.cfg.cache_dir / "sha256" / sha[:2] / sha


def blob_path(root: Path, sha: str) -> Path:
    return root / "sha256" / sha[:2] / sha[2:4] / sha


class Source:
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


class DbSource(Source):
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


class CasSource(Source):
    type, label = "cas", "content-addressed store"

    def resolve(self, kb, rec, rendition, loc, fetch):
        sha = rendition.get("sha256")
        root = kb.cfg.stores.get(loc["store"])
        if not sha or root is None:
            return None
        p = blob_path(root, sha)
        return p if p.is_file() else None

    def describe(self, kb, loc):
        root = kb.cfg.stores.get(loc["store"])
        return f"store {loc['store']}" + (f" ({root})" if root else " (not mounted here)")


class UrlSource(Source):
    type, label = "url", "on the web"

    def resolve(self, kb, rec, rendition, loc, fetch):
        sha = rendition.get("sha256")
        if sha and cache_path(kb, sha).is_file():
            return cache_path(kb, sha)
        if not fetch:
            return None
        from .store import fetch as do_fetch
        path, got, _ = do_fetch(kb, loc["url"])
        if sha and got != sha:
            who = f"{rec.id}#{rendition['name']}" if rec is not None else rendition["name"]
            raise KBError(f"{who}: {loc['url']} now has sha256 {got[:12]}…, recorded {sha[:12]}… "
                          "(content changed upstream; see kb verify)")
        return path

    def available(self, kb, rendition, loc):
        return True   # assume reachable; kb verify checks

    def describe(self, kb, loc):
        return loc["url"] + (f" (needs {loc['auth']})" if loc.get("auth") else "")

    def link(self, loc):
        return loc["url"]


class LocalSource(Source):
    type, label = "local", "on one machine"

    def resolve(self, kb, rec, rendition, loc, fetch):
        if loc["host"] != kb.cfg.host:
            return None
        p = Path(loc["path"])
        return p if p.is_file() else None

    def describe(self, kb, loc):
        return f"only on {loc['host']}: {loc['path']}"


class AnnexSource(Source):
    """A file in a DataLad dataset. The dataset's local clone is mapped in settings:
    ``datasets.<dataset id> = {path = "..."}``; content is fetched with ``datalad get``."""
    type, label = "annex", "DataLad dataset"

    def _root(self, kb, loc) -> Path | None:
        ds = kb.cfg.raw.get("datasets", {}).get(loc["dataset"], {})
        return Path(ds["path"]).expanduser() if ds.get("path") else None

    def resolve(self, kb, rec, rendition, loc, fetch):
        root = self._root(kb, loc)
        if root is None or not loc.get("path"):
            return None
        p = root / loc["path"]
        if p.exists() and p.resolve().is_file():
            return p
        if fetch and shutil.which("datalad"):
            r = subprocess.run(["datalad", "get", "-d", str(root), str(p)], capture_output=True, text=True)
            if r.returncode == 0 and p.resolve().is_file():
                return p
        return None

    def describe(self, kb, loc):
        root = self._root(kb, loc)
        return f"DataLad dataset {loc['dataset']}" + (f": {loc.get('path', '')}" if loc.get("path") else "") + \
            ("" if root else " (no local clone mapped)")


class AssetLocation(Source):
    """A rendition supplied by an asset (an abstract file or file set) at a version: see kb.assets."""
    type, label = "asset", "an asset"

    def resolve(self, kb, rec, rendition, loc, fetch):
        from .assets import resolve as resolve_asset
        if loc["asset"] not in kb.records:
            return None
        try:
            res = resolve_asset(kb, loc["asset"], loc["version"], loc.get("path"), remote=fetch)
        except KBError:
            return None
        p = next(iter(res.files.values()))
        sha = rendition.get("sha256")
        if sha:
            from .store import sha256_file
            if sha256_file(p)[0] != sha:
                return None          # the asset's file is not the bytes this rendition recorded
        return p

    def available(self, kb, rendition, loc):
        return loc["asset"] in kb.records

    def describe(self, kb, loc):
        rec = kb.records.get(loc["asset"])
        what = f"asset {loc['asset']}" + (f" ({rec.data.get('title')})" if rec else " (not in this store)")
        return f"{what}, version {loc['version']}" + (f", {loc['path']}" if loc.get("path") else "")


REGISTRY: dict[str, Source] = {}

# Resolution order: cheapest and most local first; URLs (network) last.
ORDER = ["db", "cas", "local", "asset", "annex", "url"]


def register(source: Source, position: int | None = None) -> None:
    REGISTRY[source.type] = source
    if source.type not in ORDER:
        ORDER.insert(len(ORDER) - 1 if position is None else position, source.type)


for _s in (DbSource(), CasSource(), UrlSource(), LocalSource(), AssetLocation(), AnnexSource()):
    register(_s)


def source(loc: dict) -> Source:
    try:
        return REGISTRY[loc["type"]]
    except KeyError:
        raise KBError(f"no data source for location type {loc.get('type')!r}") from None


def resolve(kb, rec, rendition: dict, fetch: bool = True) -> Path | None:
    """A local file holding the rendition's content, trying sources in ORDER."""
    locs = sorted(rendition.get("locations", []),
                  key=lambda l: ORDER.index(l["type"]) if l["type"] in ORDER else len(ORDER))
    sha = rendition.get("sha256")
    if sha and cache_path(kb, sha).is_file() and not any(l["type"] == "db" for l in locs):
        return cache_path(kb, sha)
    for loc in locs:
        p = source(loc).resolve(kb, rec, rendition, loc, fetch=fetch and loc["type"] == "url")
        if p is not None:
            return p
    if fetch:
        for loc in locs:
            if loc["type"] == "annex":
                p = source(loc).resolve(kb, rec, rendition, loc, fetch=True)
                if p is not None:
                    return p
    return None


def describe(kb, rendition: dict) -> list[str]:
    return [source(l).describe(kb, l) for l in rendition.get("locations", [])]


def links(rendition: dict) -> list[str]:
    return [u for u in (source(l).link(l) for l in rendition.get("locations", [])) if u]
