"""Source items: the concrete sources (a DataLad dataset ...) that items depend on.

``kb source add TARGET ...`` describes a source as a source item.  Each
*source type* knows how to recognize a target (a local path or a URL) and how
to describe it; ``guess`` asks every type, and says why none matched when
that happens.  Several targets may name one source: for a DataLad dataset,
a local clone and the remote URL it is shared from.

New types register a ``SourceType`` in ``TYPES`` (and add their name to the
``source_type`` enum of ``schemas/v1/source.json``).  A type that is not
``guessable`` (``filesystem``: any directory is one) is used only when asked
for with ``--type``; guessing says so instead of misclassifying.
"""

import copy
import re
from pathlib import Path

from dataclasses import dataclass, field

from .errors import KBError
from .items import Item, Store


@dataclass
class Match:
    ok: bool
    reason: str                    # why it is (or is not) this type, for people
    warning: str = ""              # accepted, but something looks wrong


class SourceType:
    name = ""
    label = ""
    guessable = True

    def recognize(self, target: str) -> Match:
        raise NotImplementedError

    def add(self, kb: Store, targets: list[str], title: str | None, summary: str | None, check: bool,
            name: str | None = None) -> Item:
        raise NotImplementedError

    def locate(self, kb: Store, rec: Item) -> Path | None:
        """Where the source is on this machine, if it is."""
        return None


class DataladType(SourceType):
    name, label = "datalad", "a DataLad dataset: a local clone, a URL it can be cloned from, or both"

    def recognize(self, target):
        from . import datasets
        m = Match(*datasets.recognize(target))
        p = Path(target).expanduser()
        if m.ok and p.is_dir() and datasets.dataset_id(p) is None:
            m.warning = datasets.NO_ID_WARNING
        return m

    def add(self, kb, targets, title, summary, check, name=None):
        from . import datasets
        if name:
            raise KBError(f"a {self.name} source has its own identity; --name is for sources without one")
        return datasets.describe(kb, targets, title, summary, check=check, kind=self.name)

    def locate(self, kb, rec):
        from . import datasets
        return datasets.locate(kb, rec)


class GitType(DataladType):
    """A plain git repository (no DataLad, no git-annex): identified by its first commit; cloned with git."""
    name, label = "git", "a git repository without DataLad: a local work tree, a URL, or both"

    def recognize(self, target):
        from . import datasets
        return Match(*datasets.recognize_git(target))


class FilesystemType(SourceType):
    """A directory tree, named: the name is its identity, so the same tree mounted at different places
    (EOS here, NFS there) is one source.  ``fs`` asset bindings with ``root = NAME`` use it."""
    name, label = "filesystem", "a named directory tree (local disk, NFS, EOS ...); only with --type filesystem"
    guessable = False

    def recognize(self, target):
        p = Path(target).expanduser()
        if p.is_dir():
            return Match(True, "a plain directory")
        return Match(False, "not a directory on this machine" if not p.exists() else "a file, not a directory")

    def add(self, kb, targets, title, summary, check, name=None):
        dirs = [Path(t).expanduser() for t in targets]
        bad = [str(d) for d in dirs if not d.is_dir()]
        if bad:
            raise KBError(f"not directories here: {', '.join(bad)}")
        root = name or re.sub(r"[^a-z0-9._-]+", "-", dirs[0].resolve().name.lower()).strip("-")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", root or ""):
            raise KBError(f"filesystem names are lower case letters, digits, '.', '_' and '-': {root!r} (use --name)")
        paths = [{"host": kb.cfg.host, "path": str(d.resolve())} for d in dirs]
        have = find(kb, self.name, root)
        if have:
            data = copy.deepcopy(have.data)
            for p in paths:
                if p not in data.setdefault("paths", []):
                    data["paths"].append(p)
            return have if data == have.data else kb.save(Item(data, kb), "source", {"paths": paths})
        from . import authoring
        return authoring.new(kb, "source", {
            "title": title or root, "summary": summary or f"The directory tree {root}.",
            "source_type": self.name, "source_id": root, "paths": paths})

    def locate(self, kb, rec):
        return filesystem_path(kb, rec.data["source_id"])


TYPES: dict[str, SourceType] = {t.name: t for t in (DataladType(), GitType(), FilesystemType())}


def find(kb: Store, source_type: str, source_id: str) -> Item | None:
    return next((r for r in kb.items.values() if r.kind == "source" and r.data.get("source_type") == source_type
                 and r.data.get("source_id") == source_id), None)


def filesystem_path(kb: Store, name: str) -> Path | None:
    """The named filesystem tree on this machine: the setting filesystems.NAME.path, else a path its
    source item lists for this host."""
    p = kb.cfg.raw.get("filesystems", {}).get(name, {}).get("path")
    if p:
        return Path(p).expanduser()
    rec = find(kb, "filesystem", name)
    for e in (rec.data.get("paths", []) if rec else []):
        if e.get("host") == kb.cfg.host and Path(e["path"]).is_dir():
            return Path(e["path"])
    return None


@dataclass
class Guess:
    type: str | None
    reasons: dict[str, list[str]] = field(default_factory=dict)    # type -> why, per target
    warnings: list[str] = field(default_factory=list)               # of the type guessed

    def explain(self) -> str:
        return "; ".join(f"{t}: {', '.join(rs)}" for t, rs in self.reasons.items())


def guess(targets: list[str], only: str | None = None) -> Guess:
    """The one guessable source type that recognizes every target (or ONLY, if given), or None with
    the reasons.  Types that are never guessed say how to ask for them."""
    if not targets:
        raise KBError("give a path or URL")
    if only is not None and only not in TYPES:
        raise KBError(f"no source type {only!r} (have: {', '.join(TYPES)})")
    hits, reasons, warnings = [], {}, {}
    for t in TYPES.values():
        if only is not None and t.name != only:
            continue
        ms = [t.recognize(x) for x in targets]
        if t.guessable or only == t.name:
            reasons[t.name] = [f"{x}: {m.reason}" for x, m in zip(targets, ms)]
            if all(m.ok for m in ms):
                hits.append(t.name)
                warnings[t.name] = [f"{x}: {m.warning}" for x, m in zip(targets, ms) if m.warning]
        elif all(m.ok for m in ms):
            reasons[t.name] = [f"{'this appears to be' if len(targets) == 1 else 'these appear to be'} "
                               f"{ms[0].reason}, but kb requires --type {t.name} to accept it "
                               "(to avoid misclassifying)"]
    if len(hits) == 1:
        return Guess(hits[0], {hits[0]: reasons[hits[0]]}, warnings.get(hits[0], []))
    return Guess(None, reasons)


def report(kb: Store, rec: Item) -> dict:
    """A source item and its state here: where it is on this machine, which items use it."""
    t = TYPES.get(rec.data.get("source_type"))
    here = t.locate(kb, rec) if t else None
    used = [r[0] for r in kb.conn.execute("SELECT DISTINCT src FROM refs WHERE dst = ? AND rel = 'source'", (rec.id,))]
    same = [r.id for r in kb.items.values() if r.kind == "source" and r.id != rec.id
            and (r.data.get("source_type"), r.data.get("source_id")) == (rec.data.get("source_type"), rec.data.get("source_id"))]
    return {"id": rec.id, "title": rec.data.get("title"), "type": rec.data.get("source_type"),
            "source_id": rec.data.get("source_id"), "urls": rec.data.get("urls", []),
            "paths": rec.data.get("paths", []), "here": str(here) if here else None,
            "used_by": used, "same_as": same}


def report_all(kb: Store) -> list[dict]:
    return [report(kb, r) for r in kb.items.values() if r.kind == "source"]


def add(kb: Store, targets: list[str], source_type: str | None = None, title: str | None = None,
        summary: str | None = None, check: bool = True, name: str | None = None) -> Item:
    """Describe TARGETS (paths and URLs naming one source) as a source item, of SOURCE_TYPE or the guessed one."""
    if source_type is None:
        g = guess(targets)
        if g.type is None:
            raise KBError(f"cannot tell what kind of source this is ({g.explain()}); give --type "
                          f"({', '.join(TYPES)})")
        source_type = g.type
    if source_type not in TYPES:
        raise KBError(f"no source type {source_type!r} (have: {', '.join(TYPES)})")
    return TYPES[source_type].add(kb, targets, title, summary, check, name)
