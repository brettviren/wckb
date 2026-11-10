"""Source items: the concrete sources (a DataLad dataset ...) that items depend on.

``kb source add TARGET ...`` describes a source as a source item.  Each
*source type* knows how to recognize a target (a local path or a URL) and how
to describe it; ``guess`` asks every type, and says why none matched when
that happens.  Several targets may name one source: for a DataLad dataset,
a local clone and the remote URL it is shared from.

A type may also *make* its source: given the URL of a service (an Indico
site), ``indico`` makes a DataLad dataset and fills it.  ``--target`` says
where a source made from a URL goes.

**Sharing** (the users of one computer; see :mod:`kb.sharing`).  With
``--shared``, a source made from a URL is made as its *owner* would: shared
with a group.  A local dataset given with ``--shared`` is someone's shared
source, so it gets a shared *clone* here, whose annex is the owner's.
``kb source share`` (and ``--unshare``) does either to a directory there is
already, by the role it has.

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
class Adding:
    """How to add a source: what ``kb source add`` was told besides the targets."""
    title: str | None = None
    summary: str | None = None
    check: bool = True
    name: str | None = None
    #: Where a source made from a URL goes (else derived from the URL), or a shared clone.
    target: Path | None = None
    shared: bool = False
    group: str | None = None
    #: Type-specific options (-o KEY=VALUE).
    options: dict[str, str] = field(default_factory=dict)


@dataclass
class Match:
    ok: bool
    reason: str                    # why it is (or is not) this type, for people
    warning: str = ""              # accepted, but something looks wrong


class SourceType:
    name = ""
    label = ""
    guessable = True
    #: -o KEY=VALUE options it takes: key -> what it means.
    options: dict[str, str] = {}

    def recognize(self, target: str, asked: bool = False) -> Match:
        """Is TARGET a source of this type?  ASKED: the type was given (--type), not guessed."""
        raise NotImplementedError

    def add(self, kb: Store, targets: list[str], how: Adding) -> Item:
        raise NotImplementedError

    def share(self, path: Path, how: Adding, unshare: bool = False) -> list[str]:
        """Share (or stop sharing) the source directory PATH: what was done."""
        raise KBError(f"sharing {self.name} sources is not supported (yet)")

    def put(self, kb: Store, rec: Item, root: Path, rel: str, message: str) -> dict:
        """Take the file at REL in the source's tree (at ROOT here) into the source, as this type does;
        the location that names it."""
        return {"type": "source", "source": rec.id, "path": rel}

    def locate(self, kb: Store, rec: Item) -> Path | None:
        """Where the source is on this machine, if it is."""
        return None


class DataladType(SourceType):
    name, label = "datalad", "a DataLad dataset: a local clone, a URL it can be cloned from, or both"

    def recognize(self, target, asked=False):
        from . import datasets, indico
        m = Match(*datasets.recognize(target))
        p = Path(target).expanduser()
        if m.ok and p.is_dir() and indico.sites(p) and not asked:
            return Match(False, f"a DataLad dataset of an Indico site ({', '.join(indico.sites(p))}): type indico")
        if m.ok and p.is_dir() and datasets.dataset_id(p) is None:
            m.warning = datasets.NO_ID_WARNING
        return m

    def add(self, kb, targets, how):
        from . import datasets
        if how.name:
            raise KBError(f"a {self.name} source has its own identity; --name is for sources without one")
        local = [t for t in targets if Path(t).expanduser().exists()]
        if how.shared and local:
            return self._add_clone(kb, targets, Path(local[0]).expanduser().resolve(), how)
        if how.target and local:
            raise KBError("--target is where a source made from a URL goes; these are here already "
                          "(with --shared, it is where the shared clone goes)")
        dest = how.target
        if how.shared:
            dest = dest or self._shared_dir(kb, targets[0])
            if self.name == "git":
                raise KBError("sharing git sources is not supported (yet)")
        local_root = Path(local[0]).expanduser().resolve() if local else None
        rec = datasets.describe(kb, targets, how.title, how.summary, check=how.check, kind=self.name, dest=dest,
                                extra=self._extra(local_root) if local_root else None)
        if how.shared:
            from . import sharing
            for line in sharing.share(datasets.locate(kb, rec), how.group):
                _tell(line)
        return rec

    def _extra(self, root: Path) -> dict:
        """Fields of a new source item for the dataset at ROOT, besides those every dataset has."""
        return {}

    def _shared_dir(self, kb, url: str) -> Path:
        """Where a shared source made from URL goes: sources.TYPE.shared_dir/NAME."""
        from .config import source_settings
        base = source_settings(kb.cfg.raw, self.name).get("shared_dir")
        if not base:
            raise KBError(f"where does the shared source go? Give --target DIR (a directory the group can reach), "
                          f"or set sources.{self.name}.shared_dir")
        return Path(base).expanduser() / self._dirname(url)

    def _dirname(self, url: str) -> str:
        from .datasets import _slug
        return _slug(Path(url.rstrip("/")).name.removesuffix(".git"))

    def _add_clone(self, kb, targets, origin: Path, how) -> Item:
        """A shared clone of the owner's dataset ORIGIN, here; the source item names ORIGIN (on this host)."""
        from . import datasets, sharing
        uuid = datasets.identity(origin, self.name)
        if uuid is None:
            raise KBError(f"{origin}: {self.recognize(str(origin), asked=True).reason}")
        have = datasets.locate(kb, uuid)
        if have is not None and sharing.origin_of(have) == origin and how.target is None:
            _tell(f"already cloned here: {have}")
        else:
            dest = how.target or (datasets.source_dir(kb.cfg.raw, self.name)
                                  / f"{datasets._slug(how.title or origin.name)}-{datasets.short(uuid)}")
            for line in sharing.clone(origin, dest):
                _tell(line)
            have = Path(dest).expanduser().resolve()
        rec = datasets.describe(kb, targets, how.title, how.summary, check=how.check, kind=self.name,
                                extra=self._extra(origin))
        datasets.remember(uuid, have)          # this user's clone, not the owner's directory
        return rec

    def put(self, kb, rec, root, rel, message):
        from . import content, datasets
        datasets.need()
        import datalad.api as dl
        why = datasets.ignored(root, rel)
        if why:       # datalad save would skip it without a word
            raise KBError(f"{rel} is ignored by git in {root} ({why}); un-ignore it there (a '!' pattern in the "
                          "dataset's .gitignore) and try again")
        res = dl.save(dataset=str(root), path=[str(root / rel)], message=message, result_renderer="disabled",
                      on_failure="ignore", return_type="list")
        bad = [r for r in res if r.get("status") in ("error", "impossible")]
        if bad:
            raise KBError(f"datalad save {rel} in {root}: {bad[0].get('message')}")
        return content.dataset_location(kb, root, rel)

    def share(self, path, how, unshare=False):
        from . import sharing
        if sharing.origin_of(path):            # a shared clone of someone's dataset
            return sharing.unclone(path) if unshare else sharing.adopt(path)
        return sharing.unshare(path) if unshare else sharing.share(path, how.group)

    def locate(self, kb, rec):
        from . import datasets
        return datasets.locate(kb, rec)


class IndicoType(DataladType):
    """The DataLad dataset of an Indico site, made and filled by git-annex-remote-indico (see kb.indico)."""
    name = "indico"
    label = ("the DataLad dataset of an Indico site: the URL of a site, category or event (ingested into the "
             "site's dataset, made if needed), or such a dataset")

    @property
    def options(self):
        from .indico import OPTIONS
        return OPTIONS

    def recognize(self, target, asked=False):
        from . import datasets, indico
        p = Path(target).expanduser()
        if p.exists():
            m = Match(*datasets.recognize(target))
            if not m.ok:
                return m
            found = indico.sites(p)
            return (Match(True, f"a DataLad dataset of the Indico site {', '.join(found)}") if found
                    else Match(False, "a DataLad dataset without an Indico remote"))
        if indico.looks_like(target):
            return Match(True, f"a URL of the Indico site {indico.site(target)}")
        if asked and indico.is_url(target):
            return Match(True, f"a URL, taken for one of the Indico site {indico.site(target)} (--type indico)")
        return Match(False, "not an Indico URL (no host label 'indico'; --type indico says it is one)"
                     if indico.is_url(target) else "no such path, and not a URL")

    def add(self, kb, targets, how):
        from . import indico
        urls = [t for t in targets if not Path(t).expanduser().exists()]
        if not urls:
            return super().add(kb, targets, how)
        if len(urls) < len(targets):
            raise KBError("give either Indico URLs or a dataset of one, not both")
        bad = [u for u in urls if not indico.is_url(u)]
        if bad:
            raise KBError(f"not URLs: {', '.join(bad)}")
        bases = {indico.site(u) for u in urls}
        if len(bases) > 1:
            raise KBError(f"one Indico site at a time: {', '.join(sorted(bases))}")
        base = bases.pop()
        root = self._dataset(kb, base, how)
        work = _origin_or(root)            # a shared clone's ingests go to the owner's dataset
        indico.ingest(work, urls, how.options)
        if work != root:
            import datalad.api as dl
            dl.update(dataset=str(root), how="merge", result_renderer="disabled", on_failure="ignore")
        from . import datasets
        rec = datasets.describe(kb, [str(root)], how.title or indico.host(base), how.summary or
                                f"Categories and events of the Indico site {base}, with their files "
                                "(git-annex-remote-indico).", check=how.check, kind=self.name,
                                extra={"service": base})
        if work != root:
            datasets.remember(rec.data["source_id"], root)
        return rec

    def _dataset(self, kb, base: str, how) -> Path:
        """The site's dataset here: --target, or the one this store's indico source for the site has here,
        else made (shared if asked) at --target or in the source directory."""
        from . import datasets, indico, sharing
        if how.target is None:
            for rec in kb.items.values():
                if (rec.kind == "source" and rec.data.get("source_type") == self.name
                        and rec.data.get("service") == base):
                    here = datasets.locate(kb, rec)
                    if here is not None:
                        return here
        dest = Path(how.target).expanduser().absolute() if how.target else (
            self._shared_dir(kb, base) if how.shared
            else datasets.source_dir(kb.cfg.raw, self.name) / indico.host(base))
        if dest.is_dir() and (dest / ".git").exists():
            if base not in indico.sites(dest) and indico.sites(dest):
                raise KBError(f"{dest} is the dataset of another Indico site ({', '.join(indico.sites(dest))})")
            return dest.resolve()
        indico.need()
        for line in sharing.create(dest, how.group, shared=how.shared):
            _tell(line)
        return dest.resolve()

    def _dirname(self, url):
        from .indico import host
        return host(url)

    def _extra(self, root):
        from .indico import sites
        found = sites(root)
        return {"service": found[0]} if found else {}


class GitType(DataladType):
    """A plain git repository (no DataLad, no git-annex): identified by its first commit; cloned with git."""
    name, label = "git", "a git repository without DataLad: a local work tree, a URL, or both"

    def recognize(self, target, asked=False):
        from . import datasets
        return Match(*datasets.recognize_git(target))

    def share(self, path, how, unshare=False):
        from . import sharing
        return sharing.unshare(path) if unshare else sharing.share(path, how.group)

    def put(self, kb, rec, root, rel, message):
        from .datasets import _git
        _git(root, "add", "--", rel)
        if _git(root, "diff", "--cached", "--quiet", "--", rel, check=False).returncode:
            _git(root, "commit", "-q", "-m", message, "--", rel)
        return SourceType.put(self, kb, rec, root, rel, message)      # a file in the tree, not a dataset


class FilesystemType(SourceType):
    """A directory tree, named: the name is its identity, so the same tree mounted at different places
    (EOS here, NFS there) is one source.  ``fs`` asset bindings with ``root = NAME`` use it."""
    name, label = "filesystem", "a named directory tree (local disk, NFS, EOS ...); only with --type filesystem"
    guessable = False

    def recognize(self, target, asked=False):
        p = Path(target).expanduser()
        if p.is_dir():
            return Match(True, "a plain directory")
        return Match(False, "not a directory on this machine" if not p.exists() else "a file, not a directory")

    def share(self, path, how, unshare=False):
        from . import sharing
        return sharing.unshare(path) if unshare else sharing.share(path, how.group)

    def add(self, kb, targets, how):
        title, summary, name = how.title, how.summary, how.name
        if how.target or how.shared:
            raise KBError("a filesystem source is a directory as it is: --target and --shared don't apply "
                          "(kb source share DIR shares it)")
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


TYPES: dict[str, SourceType] = {t.name: t for t in (DataladType(), IndicoType(), GitType(), FilesystemType())}


def _tell(line: str) -> None:
    import logging
    from .logs import tell
    tell(logging.getLogger(__name__), logging.INFO, line)


def _origin_or(root: Path) -> Path:
    from .sharing import origin_of
    return origin_of(root) or root


def containing(kb: Store, path: Path) -> tuple[Item, Path] | None:
    """The source that holds PATH here: the innermost tree of a source item of this store, else a DataLad
    dataset (described now, as its type); with its root here.  A git repository that is not a source here
    is not taken (kb would commit into it): kb source add makes it one."""
    path = Path(path).parent.resolve() / Path(path).name
    best = None
    for rec in kb.items.values():
        t = TYPES.get(rec.data.get("source_type")) if rec.kind == "source" else None
        root = t.locate(kb, rec) if t else None
        if root is not None and path.is_relative_to(root.resolve()):
            if best is None or len(root.resolve().parts) > len(best[1].parts):
                best = (rec, root.resolve())
    if best:
        return best
    from . import datasets, indico
    root = datasets.dataset_root(path.parent)
    if root is None:
        return None
    kind = "indico" if indico.sites(root) else "datalad"
    return datasets.ensure_source(kb, root, kind), root


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
        ms = [t.recognize(x, asked=only == t.name) for x in targets]
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


def types_table() -> list[dict]:
    """The source item types (what kb source add makes), as kb source types lists them."""
    import shutil
    from . import datasets
    needs = {"datalad": datasets.available(), "indico": datasets.available() or (
        None if shutil.which("git-indico") else "git-annex-remote-indico is not installed"),
        "git": None if shutil.which("git") else "git is not on PATH", "filesystem": None}
    return [{"interface": "item", "type": t.name, "status": "implemented", "available": needs.get(t.name) is None,
             "what": t.label + ("" if t.guessable else " (never guessed)"),
             "options": dict(t.options), "needs": needs.get(t.name)} for t in TYPES.values()]


def _type(targets: list[str], source_type: str | None) -> SourceType:
    if source_type is None:
        g = guess(targets)
        if g.type is None:
            raise KBError(f"cannot tell what kind of source this is ({g.explain()}); give --type "
                          f"({', '.join(TYPES)})")
        source_type = g.type
    if source_type not in TYPES:
        raise KBError(f"no source type {source_type!r} (have: {', '.join(TYPES)})")
    return TYPES[source_type]


def add(kb: Store, targets: list[str], source_type: str | None = None, title: str | None = None,
        summary: str | None = None, check: bool = True, name: str | None = None, *,
        target: Path | None = None, shared: bool = False, group: str | None = None,
        options: dict[str, str] | None = None) -> Item:
    """Describe TARGETS (paths and URLs naming one source) as a source item, of SOURCE_TYPE or the guessed one;
    a type that makes its source from a URL makes it (at TARGET, shared with GROUP if SHARED)."""
    t = _type(targets, source_type)
    options = dict(options or {})
    unknown = [k for k in options if k not in t.options]
    if unknown:
        raise KBError(f"{t.name} sources take no option {', '.join(unknown)}"
                      + (f" (they take: {', '.join(t.options)})" if t.options else ""))
    how = Adding(title, summary, check, name, Path(target).expanduser() if target else None, shared, group, options)
    return t.add(kb, targets, how)


def share(kb: Store | None, target: str, source_type: str | None = None, group: str | None = None,
          unshare: bool = False) -> tuple[str, Path, list[str]]:
    """Share (or stop sharing) the source directory TARGET, or the local copy of the source item TARGET:
    (type, directory, what was done)."""
    path = Path(target).expanduser()
    if not path.exists() and kb is not None and target.startswith("src-"):
        rec = kb.get(target)
        t = TYPES.get(rec.data.get("source_type"))
        here = t.locate(kb, rec) if t else None
        if here is None:
            raise KBError(f"{rec.id} is not on this machine")
        path, source_type = here, source_type or t.name
    if not path.is_dir():
        raise KBError(f"{target}: not a directory here (or a source item that is on this machine)")
    t = _type([str(path)], source_type)
    path = path.resolve()
    return t.name, path, t.share(path, Adding(group=group), unshare)
