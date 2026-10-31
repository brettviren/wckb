"""Stores and their neighbors.

Every store has a scope label (project, home, group, ...: descriptive only,
it sets defaults at kb init) and may name neighbors.  An edge's relation
decides how knowledge moves along it by default:

    relation  push (here -> there)  pull (there -> here)  arriving items
    up        select                all                   land as configured
    down      all                   select
    peer      select                select                reviewed (branch)
    twin      git: the same store, kept in sync with git pull/merge/push

``select`` moves only an explicit knowledge set (shallow or deep), ``all``
moves every new or changed item, ``none`` nothing.  Every default can be
overridden per edge, e.g. ``pull = "select"`` for a less trusted up store.
Hierarchies and graphs are both just sets of such edges.

A neighbor is found by ``path`` (a store directory on this machine) or
``url`` (a published read-only snapshot of a store, downloaded into the
cache by ``kb neighbor fetch``).  Neighbors are opened read-only (SQLite
``mode=ro``) with their scratch files in the user cache, so reading never
writes to somebody else's store.
"""

import hashlib
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import tomli_w

from .config import CONFIG_NAME, RELATIONS, access_rank, cache_home, _read
from .errors import KBError
from .records import KB

PUSH_MODES = ("apply", "inbox", "pr")      # how a push lands: applied, into the inbox for review, as a PR of the bundle
FORMS = ("select", "all", "none")
ACCEPT = ("direct", "review")                          # pulled items: straight in, or on a branch
PREFER = ("ours", "theirs")                            # mutable-field conflicts from another path
EDGE_DEFAULTS = {
    "up":   {"push": "select", "pull": "all", "accept": "direct", "prefer": "theirs"},
    "down": {"push": "all", "pull": "select", "accept": "direct", "prefer": "ours"},
    "peer": {"push": "select", "pull": "select", "accept": "review", "prefer": "ours"},
    "twin": {"push": "none", "pull": "none", "accept": "direct", "prefer": "ours"},
}


@dataclass
class Neighbor:
    name: str
    relation: str
    path: Path | None = None
    url: str | None = None
    mode: str = "apply"            # how pushes land there: apply | inbox | pr
    relabel: str | None = None     # access level records may be relabelled to when pushed here
    description: str = ""
    push: str = "select"           # what goes here -> there: select | all | none
    pull: str = "all"              # what comes there -> here: select | all | none
    accept: str = "direct"         # how pulled items land here: direct | review (on a branch)
    prefer: str = "ours"           # mutable-field conflicts arriving from this edge
    remote: str | None = None      # twins: the git remote (or use path/url)
    branch: str | None = None      # twins: the branch to sync (default: current)

    @property
    def location(self) -> Path | None:
        """Where the store is on this machine: its path, else the cache clone of its URL."""
        if self.path is not None:
            return self.path
        if self.url:
            return clone_dir(self.url)
        return None

    @property
    def available(self) -> bool:
        loc = self.location
        return loc is not None and (loc / CONFIG_NAME).exists()

    @property
    def writable(self) -> bool:
        return self.path is not None and self.available

    exchange: Path | None = None   # pr landing: a git clone of the receiver's bundle exchange repository


def clone_dir(url: str) -> Path:
    """Where a URL neighbor's snapshot is kept on this machine."""
    tail = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".sqlite") or "store"
    return cache_home() / "snapshots" / f"{tail}-{hashlib.sha256(url.encode()).hexdigest()[:8]}"


def neighbors(kb: KB) -> list[Neighbor]:
    out = []
    for name, n in sorted(kb.cfg.neighbors.items()):
        rel = n.get("relation", "up")
        d = EDGE_DEFAULTS[rel]
        mode = n.get("land", n.get("mode", "apply"))
        for key, allowed, val in (("land", PUSH_MODES, mode), ("push", FORMS, n.get("push", d["push"])),
                                  ("pull", FORMS, n.get("pull", d["pull"])),
                                  ("accept", ACCEPT, n.get("accept", d["accept"])),
                                  ("prefer", PREFER, n.get("prefer", d["prefer"]))):
            if val not in allowed:
                raise KBError(f"neighbor {name}: {key} must be one of {', '.join(allowed)}")
        out.append(Neighbor(name, rel, n.get("path"), n.get("url"), mode, n.get("relabel"),
                            n.get("description", ""), n.get("push", d["push"]), n.get("pull", d["pull"]),
                            n.get("accept", d["accept"]), n.get("prefer", d["prefer"]),
                            n.get("remote"), n.get("branch"),
                            Path(n["exchange"]).expanduser() if n.get("exchange") else None))
    return out


def neighbor(kb: KB, name: str) -> Neighbor:
    for n in neighbors(kb):
        if n.name == name:
            return n
    have = ", ".join(n.name for n in neighbors(kb)) or "none"
    raise KBError(f"no neighbor {name!r} (have: {have}); see kb neighbor add")


def open_store(path: Path, readonly: bool = True) -> KB:
    """The store at *path*; read-only stores are opened mode=ro, with scratch files in the user cache."""
    kb = KB.open(path, readonly=readonly)
    if readonly:
        kb.cfg.kbdir_override = cache_home() / "stores" / _safe(kb.cfg.id)
    return kb


def _safe(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in text)


@dataclass
class Reached:
    label: str            # neighbor name path, e.g. "home" or "home>group-x"
    kb: KB
    distance: int
    relation: str


def reachable(kb: KB, depth: int = 1, relations=("up", "peer"), only: list[str] | None = None) -> list[Reached]:
    """Stores reachable from *kb* within *depth* hops along *relations*, nearest first.

    Each store is visited once (by store ID), so cycles in the graph are harmless.
    Unavailable neighbors (not cloned here) are skipped.
    """
    seen = {kb.cfg.id}
    out: list[Reached] = []
    queue = deque([(kb, "", 0)])
    while queue:
        cur, label, dist = queue.popleft()
        if dist >= depth:
            continue
        for n in neighbors(cur):
            if n.relation not in relations or not n.available:
                continue
            if dist == 0 and only and n.name not in only:
                continue
            try:
                other = open_store(n.location)
            except KBError:
                continue
            if other.cfg.id in seen:
                continue
            seen.add(other.cfg.id)
            lab = f"{label}>{n.name}" if label else n.name
            out.append(Reached(lab, other, dist + 1, n.relation))
            queue.append((other, lab, dist + 1))
    return out


def fetch(kb: KB, names: list[str] | None = None) -> list[tuple[str, str]]:
    """Download the snapshots of URL-only neighbors (read-only copies in the cache)."""
    import shutil
    import urllib.request
    done = []
    for n in neighbors(kb):
        if names and n.name not in names:
            continue
        if n.path is None and n.url:
            dest = clone_dir(n.url) / CONFIG_NAME
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".partial")
            try:
                with urllib.request.urlopen(n.url, timeout=120) as resp, open(tmp, "wb") as f:
                    shutil.copyfileobj(resp, f)
                fresh = not dest.exists()
                tmp.replace(dest)
                for side in ("-wal", "-shm"):
                    (dest.parent / (CONFIG_NAME + side)).unlink(missing_ok=True)
                done.append((n.name, "fetched" if fresh else "updated"))
            except OSError as exc:
                tmp.unlink(missing_ok=True)
                done.append((n.name, f"failed: {exc}"))
    return done


LOCAL_HEADER = "# Per-machine settings for this store (paths as seen from this machine). kb neighbor rewrites this file.\n"


def _write_toml(path: Path, data: dict, header: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + tomli_w.dumps(data))


def add(kb: KB, name: str, relation: str, path: str | None = None, url: str | None = None,
        shared: bool = False, mode: str = "apply", relabel: str | None = None,
        description: str = "", **edge) -> dict:
    """Declare a neighbor.  Shared declarations go in the store's settings (no machine paths);
    personal ones, and every path, go in .kb/local.toml."""
    edge = {k: v for k, v in edge.items() if v is not None}
    if relation not in RELATIONS:
        raise KBError(f"relation must be one of {', '.join(RELATIONS)}")
    if mode not in PUSH_MODES:
        raise KBError(f"land must be one of {', '.join(PUSH_MODES)}")
    if not path and not url:
        raise KBError("give --path (the store on this machine) and/or --url (a published snapshot)")
    for key, allowed in (("push", FORMS), ("pull", FORMS), ("accept", ACCEPT), ("prefer", PREFER)):
        if key in edge and edge[key] not in allowed:
            raise KBError(f"{key} must be one of {', '.join(allowed)}")
    if shared and not url:
        raise KBError("a shared neighbor needs a --url others can use; keep path-only neighbors local")
    if relabel is not None:
        access_rank(relabel)
    if edge.get("exchange"):
        edge["exchange"] = str(Path(edge["exchange"]).expanduser().resolve())
    if path:
        p = Path(path).expanduser().resolve()
        if not (p / CONFIG_NAME).exists():
            raise KBError(f"{p} is not a kb store (no {CONFIG_NAME})")
        if p == kb.root.resolve():
            raise KBError("a store cannot be its own neighbor")
        other = KB.open(p, readonly=True)
        if relation == "twin" and other.cfg.id != kb.cfg.id:
            raise KBError(f"{p} is a different store (id {other.cfg.id}); twins are copies of one store")
        if relation != "twin" and other.cfg.id == kb.cfg.id:
            raise KBError(f"{p} is a copy of this store (same id): declare it as a twin")
        path = str(p)
    entry = {"relation": relation, "land": mode} | edge
    if url:
        entry["url"] = url
    if relabel:
        entry["relabel"] = relabel
    if description:
        entry["description"] = description
    local_file = kb.root / ".kb" / "local.toml"
    local = _read(local_file)
    if shared:
        nbs = dict(kb.setting("neighbors", {}) or {})
        nbs[name] = {k: v for k, v in entry.items() if k != "exchange"}
        kb.set_setting("neighbors", nbs)
        if path or edge.get("exchange"):
            mine = local.setdefault("neighbors", {}).setdefault(name, {})
            if path:
                mine["path"] = path
            if edge.get("exchange"):
                mine["exchange"] = edge["exchange"]
            _write_toml(local_file, local, LOCAL_HEADER)
    else:
        local.setdefault("neighbors", {})[name] = entry | ({"path": path} if path else {})
        _write_toml(local_file, local, LOCAL_HEADER)
    from . import config as config_mod
    kb.cfg = config_mod.from_settings(kb.root, kb.settings(), keep=kb.cfg)
    return entry | ({"path": path} if path else {})


def remove(kb: KB, name: str) -> list[str]:
    removed = []
    nbs = dict(kb.setting("neighbors", {}) or {})
    if name in nbs:
        del nbs[name]
        kb.set_setting("neighbors", nbs)
        removed.append("store settings")
    f = kb.root / ".kb" / "local.toml"
    data = _read(f)
    if name in data.get("neighbors", {}):
        del data["neighbors"][name]
        _write_toml(f, data, LOCAL_HEADER)
        removed.append(".kb/local.toml")
    if not removed:
        raise KBError(f"neighbor {name!r} is not declared in this store (user-wide neighbors live in your config.toml)")
    from . import config as config_mod
    kb.cfg = config_mod.from_settings(kb.root, kb.settings(), keep=kb.cfg)
    return removed


def describe(kb: KB, depth: int = 2) -> list[dict]:
    """The neighborhood as rows: label, relation, distance, store, scope, access, where."""
    rows = []
    for n in neighbors(kb):
        rows.append({"label": n.name, "relation": n.relation, "distance": 1, "available": n.available,
                     "where": str(n.location) if n.location else None, "url": n.url, "mode": n.mode})
    for r in reachable(kb, depth, relations=RELATIONS):
        for row in rows:
            if row["label"] == r.label:
                row.update(store=r.kb.cfg.id, scope=r.kb.cfg.scope, access=r.kb.cfg.access,
                           records=len(r.kb.records))
                break
        else:
            rows.append({"label": r.label, "relation": r.relation, "distance": r.distance, "available": True,
                         "where": str(r.kb.root), "store": r.kb.cfg.id, "scope": r.kb.cfg.scope,
                         "access": r.kb.cfg.access, "records": len(r.kb.records)})
    return rows


def locate(kb: KB, ident: str, depth: int = 3) -> "Reached | None":
    """The nearest reachable store (any relation) holding record *ident*."""
    for r in reachable(kb, depth, relations=("up", "peer", "down")):
        if ident in r.kb.records:
            return r
    return None
