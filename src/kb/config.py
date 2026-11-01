"""Configuration of a store and of the machine using it, and which store a command uses.

Layers, last one wins:

1. The store's ``settings`` table (in ``kb.sqlite``): id, name, scope, access,
   limits, content stores (cas), neighbors, policy, autonomy ... (``kb config``,
   ``kb edge``, ``kb policy`` write it).
2. The *config files*, in this order (:func:`config_files`):

   - the user's ``config.toml`` (``$XDG_CONFIG_HOME/kb/``, else ``~/.config/kb/``);
     neighbors declared here apply to every store, which is how a personal
     "home" store becomes the parent of every project store; forges live here;
   - ``.kb/config.toml``, ``.kb.toml`` and ``kb.toml`` in the working directory
     and each directory above it, the farthest first: the nearest wins;

   all skipped with ``--no-init-file``; then the file given with
   ``-c/--config``, whatever else.
3. ``.kb/local.toml`` beside the store: this machine's paths.
4. ``KB_*`` environment variables.
5. Command-line options (applied by the CLI).

**Store contexts.**  A command works on the store of its *store context*: a
short name, usable as a config table label.  The name is ``--context``, else
``$KB_CONTEXT``, else ``kb_context`` from the config files, else
``git-directory``.  Two names are made into names (:func:`resolve_context`):

- ``current-directory``: the working directory's path, slashes made hyphens
  (``/home/ann/ana`` is ``home-ann-ana``);
- ``git-directory``: the same for the nearest directory, here or above, with a
  ``.git``.

The store's directory (its *location*) is ``context.<name>.location`` from
the config files, else the nearest ``kb.sqlite``, ``.kb/kb.sqlite`` or
``kb/kb.sqlite`` here or above, else ``$XDG_DATA_HOME/kb/contexts/<name>``.
``-C`` and ``$KB_ROOT`` name the directory outright.
"""

import logging
import os
import socket
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .db import DB_NAME   # the file that marks (and is) a store
from .errors import KBError

SUBDIR = "kb"     # a project's store may live in ./kb next to its working files
SCOPES = ("project", "home", "group", "org", "other")
RELATIONS = ("up", "peer", "down", "twin")
AUTONOMY_LEVELS = ("never", "ask", "auto")
AGENT_ENV = ("KB_AGENT", "CLAUDECODE", "AI_AGENT")
DEFAULT_MAX_INLINE = 1024 * 1024
ACCESS_LEVELS = ("public", "collaboration", "group", "private")
#: Context names that are made into names, from a directory.
#: How a context without a configured location or a store above is located.
DEFAULT_LOCATED = "the default for the context"
SPECIAL_CONTEXTS = ("current-directory", "git-directory")
DEFAULT_CONTEXT = "git-directory"
#: Config files looked for in the working directory and each one above, in the order applied.
CONFIG_NAMES = (".kb/config.toml", ".kb.toml", "kb.toml")
#: Where a store may be found, in a directory here or above.
STORE_NAMES = (DB_NAME, f".kb/{DB_NAME}", f"{SUBDIR}/{DB_NAME}")

log = logging.getLogger(__name__)


@dataclass
class Invocation:
    """What the command line says about configuration; the CLI sets ``INVOCATION``."""
    context: str | None = None
    config: Path | None = None
    no_init_file: bool = False


INVOCATION = Invocation()


@dataclass
class StoreContext:
    """Which store a command uses, and why."""
    name: str
    named_by: str            # --context, $KB_CONTEXT, a config file, or the default
    location: Path           # the store's directory
    located_by: str
    directory: Path | None = None   # the directory current-directory or git-directory was made from

    @property
    def exists(self) -> bool:
        return (self.location / DB_NAME).exists()


def context_name(path: Path) -> str:
    """A context name for a directory: its path with slashes made hyphens."""
    return str(path).strip("/").replace("/", "-")


def _nearest(here: Path, names) -> Path | None:
    for d in (here, *here.parents):
        for n in names:
            if (d / n).exists():
                return d / n
    return None


def resolve_context(cwd: Path | None = None, root: Path | None = None) -> StoreContext:
    """The store context of a command run in ``cwd``; ``root`` (``-C``, ``$KB_ROOT``) names the store's
    directory outright."""
    here = (cwd or Path.cwd()).resolve()
    raw, sources = cascade(here, with_sources=True)
    for name, named_by in ((INVOCATION.context, "--context"), (os.environ.get("KB_CONTEXT"), "$KB_CONTEXT"),
                           (raw.get("kb_context"), f"kb_context in {sources.get('kb_context')}"),
                           (DEFAULT_CONTEXT, "the default")):
        if name:
            break
    how = "-C"
    if root is None and os.environ.get("KB_ROOT"):
        root, how = Path(os.environ["KB_ROOT"]), "$KB_ROOT"
    root = Path(root).expanduser().resolve() if root is not None else None
    store = None if root else _nearest(here, STORE_NAMES)

    directory = None
    if name == "current-directory":
        directory = here
    elif name == "git-directory":
        git = _nearest(here, [".git"])
        directory = git.parent if git else None
    if name in SPECIAL_CONTEXTS:
        if root and named_by == "the default":
            name, named_by = context_name(root), f"the directory given with {how}"
        elif directory is not None:
            name = context_name(directory)
        elif root or (store and named_by == "the default"):
            # outside a git working tree, a store given (-C) or found above names the context
            name = context_name(root or store.parent)
            named_by = f"{named_by}, outside a git working tree: the store's directory"
        else:
            raise KBError(
                f"no store context: it is {name} ({named_by}), and {here} is not in a git working tree "
                f"(no .git here or above) and has no store here or above. Name one with --context NAME, "
                f"$KB_CONTEXT or kb_context = \"NAME\" in {user_config_path()} (NAME may be "
                f"current-directory), or use -C DIRECTORY")

    entry = (raw.get("context") or {}).get(name)
    located = entry.get("location") if isinstance(entry, dict) else None
    if root:
        ctx = StoreContext(name, named_by, root, how, directory)
    elif located:
        ctx = StoreContext(name, named_by, Path(located),
                           f"context.{name}.location in {sources.get(f'context.{name}.location')}", directory)
    elif store:
        ctx = StoreContext(name, named_by, store.parent, f"{store} found here or above", directory)
    else:
        ctx = StoreContext(name, named_by, data_home() / "contexts" / name, DEFAULT_LOCATED, directory)
    from . import logs
    logs.set_context(ctx.name)
    log.debug("store context %s (%s): %s (%s)", ctx.name, ctx.named_by, ctx.location, ctx.located_by)
    return ctx


def store_context(start: Path | None = None, root: Path | None = None) -> StoreContext:
    """The store context of a command in ``start`` (see :func:`resolve_context`), whose store must exist."""
    ctx = resolve_context(start, root)
    if not ctx.exists:
        if ctx.located_by in ("$KB_ROOT", "-C"):
            raise KBError(f"{ctx.located_by} {ctx.location} holds no {DB_NAME}")
        raise KBError(f"no store for the context {ctx.name} ({ctx.named_by}): {ctx.location} holds no "
                      f"{DB_NAME} ({ctx.located_by}). Make one there with kb init, or name another "
                      f"context (--context NAME), or set context.{ctx.name}.location in a config file")
    return ctx


def find_root(start: Path | None = None, root: Path | None = None) -> Path:
    """The directory of the store a command in ``start`` uses."""
    return store_context(start, root).location


def toml_text(data: dict, about: str = "") -> str:
    """DATA as TOML, in the form of a config file (TOML has no null: such keys are left out)."""
    import tomli_w

    def clean(v):
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items() if x is not None}
        if isinstance(v, list):
            return [clean(x) for x in v if x is not None]
        return v
    head = f"# {about}\n" if about else ""
    return head + tomli_w.dumps(clean(data))


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise KBError(f"{path}: {exc}") from exc


def set_user(person: str) -> Path:
    """Write ``user = PERSON`` as a top-level key of the user's config.toml (other content is kept)."""
    import re
    f = user_config_path()
    text = f.read_text() if f.exists() else ""
    head, sep, rest = text.partition("\n[")          # top-level keys come before the first table
    line = f'user = "{person}"'
    if re.search(r"(?m)^user\s*=.*$", head):
        head = re.sub(r"(?m)^user\s*=.*$", line, head)
    else:
        head = line + ("\n" + head if head else "")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(head + sep + rest if sep else head + ("" if head.endswith("\n") else "\n"))
    return f


def user_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "kb" / "config.toml"


def data_home() -> Path:
    """The user's kb data: ``$XDG_DATA_HOME/kb`` (stores of contexts without a location)."""
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "kb"


def config_files(cwd: Path | None = None) -> list[Path]:
    """The config files that apply in ``cwd``, in the order applied (see the module's docs)."""
    inv = INVOCATION
    files = []
    if not inv.no_init_file:
        user = user_config_path()
        if user.is_file():
            files.append(user)
        here = (cwd or Path.cwd()).resolve()
        found = []
        for d in (here, *here.parents):
            found[:0] = [d / n for n in CONFIG_NAMES if (d / n).is_file()]
        files += [f for f in found if f.resolve() != user.resolve()]
    if inv.config:
        given = Path(inv.config).expanduser()
        if not given.is_file():
            raise KBError(f"--config {inv.config}: no such file")
        files.append(given)
    return files


def _read_config(path: Path) -> dict:
    """A config file, with relative store locations made relative to the directory it is for."""
    data = _read(path)
    base = path.parent.parent if path.parent.name == ".kb" else path.parent
    for ctx in (data.get("context") or {}).values():
        if isinstance(ctx, dict) and ctx.get("location"):
            loc = Path(os.path.expandvars(str(ctx["location"]))).expanduser()
            ctx["location"] = str(loc if loc.is_absolute() else (base / loc).resolve())
    return data


def cascade(cwd: Path | None = None, with_sources: bool = False):
    """The config files that apply in ``cwd``, merged; with ``with_sources`` also which file set
    each top-level key and each ``context.NAME.location``."""
    raw, sources = {}, {}
    for f in config_files(cwd):
        data = _read_config(f)
        log.debug("config file %s", f)
        for k in data:
            sources[k] = f
        for name, ctx in (data.get("context") or {}).items():
            if isinstance(ctx, dict) and "location" in ctx:
                sources[f"context.{name}.location"] = f
        raw = _merge(raw, data)
    return (raw, sources) if with_sources else raw


@dataclass
class Config:
    root: Path
    name: str
    schema: str
    access: str
    max_inline_bytes: int
    cas: dict[str, Path | None]          # content-addressed stores, by name
    publish_path: Path | None
    publish_url: str | None
    embed: dict | None
    transforms: dict[str, dict]
    lint: dict
    id: str = ""
    scope: str = "other"
    neighbors: dict[str, dict] = field(default_factory=dict)
    policy: dict = field(default_factory=dict)
    autonomy: dict = field(default_factory=dict)
    raw: dict = field(repr=False, default_factory=dict)
    kbdir_override: Path | None = None   # derived files elsewhere, for stores opened read-only

    @property
    def kbdir(self) -> Path:
        return self.kbdir_override or self.root / ".kb"

    @property
    def cache_dir(self) -> Path:
        return self.kbdir / "cache"

    @property
    def build_dir(self) -> Path:
        return self.kbdir / "build"

    @property
    def host(self) -> str:
        return self.raw.get("host") or socket.gethostname()

    @property
    def is_agent(self) -> bool:
        """True when an LLM agent (Claude Code, or KB_AGENT=1) is running kb."""
        return any(os.environ.get(v) for v in AGENT_ENV)

    def autonomy_for(self, action: str, target: str | None = None) -> str:
        """never | ask | auto for *action* (e.g. push) towards *target*."""
        a = self.autonomy.get(action, "ask")
        if isinstance(a, dict):
            a = a.get(target or "", a.get("default", "ask"))
        if a not in AUTONOMY_LEVELS:
            raise KBError(f"autonomy.{action}: {a!r} is not one of {', '.join(AUTONOMY_LEVELS)}")
        return a

    @property
    def user(self) -> str:
        """Who is making items here: a person ref or a name."""
        return (os.environ.get("KB_USER") or self.raw.get("user")
                or os.environ.get("USER") or "unknown")


def load(root: Path | None = None) -> Config:
    """Configuration of the store at *root* (read-only)."""
    from .items import Store
    kb = Store.open(root, readonly=True)
    try:
        return kb.cfg
    finally:
        kb.close()


def from_settings(root: Path, settings: dict, keep: "Config | None" = None) -> Config:
    """Merge a store's settings with the user's and this machine's configuration."""
    raw = dict(settings)
    raw = _merge(raw, cascade())
    raw = _merge(raw, _read(root / ".kb" / "local.toml"))

    env = os.environ
    if "KB_USER" in env:
        raw["user"] = env["KB_USER"]
    if "KB_PUBLISH_PATH" in env:
        raw.setdefault("views", {})["publish_path"] = env["KB_PUBLISH_PATH"]
    for key in ("url", "model", "identity"):
        if f"KB_EMBED_{key.upper()}" in env:
            raw.setdefault("embed", {})[key] = env[f"KB_EMBED_{key.upper()}"]

    kbsec = raw.get("kb", {})
    access = kbsec.get("access", "group")
    if access not in ACCESS_LEVELS:
        raise KBError(f"store setting kb.access must be one of {', '.join(ACCESS_LEVELS)}")

    def path(p):
        if not p:
            return None
        p = Path(os.path.expandvars(str(p))).expanduser()
        return p if p.is_absolute() else root / p

    cas = {name: path(s.get("path")) for name, s in raw.get("cas", {}).items()}
    scope = kbsec.get("scope", "other")
    if scope not in SCOPES:
        raise KBError(f"store setting kb.scope must be one of {', '.join(SCOPES)}")
    neighbors = {}
    for name, n in raw.get("neighbors", {}).items():
        n = dict(n)
        if n.get("relation", "up") not in RELATIONS:
            raise KBError(f"neighbor {name}: relation must be one of {', '.join(RELATIONS)}")
        n.setdefault("relation", "up")
        if n.get("path"):
            n["path"] = path(n["path"])
            if n["path"].resolve() == root.resolve():
                continue  # a user-wide neighbor that is this very store
        neighbors[name] = n
    views = raw.get("views", {})
    return Config(
        root=root,
        name=kbsec.get("name", root.name),
        schema=str(kbsec.get("schema", "1.0")),
        access=access,
        max_inline_bytes=int(raw.get("limits", {}).get("max_inline_bytes", DEFAULT_MAX_INLINE)),
        cas=cas,
        publish_path=path(views.get("publish_path")),
        publish_url=views.get("publish_url"),
        embed=raw.get("embed"),
        transforms=raw.get("transforms", {}),
        lint=raw.get("lint", {}),
        id=kbsec.get("id", kbsec.get("name", root.name)),
        scope=scope,
        neighbors=neighbors,
        policy=raw.get("policy", {}),
        autonomy=raw.get("autonomy", {}),
        raw=raw,
        kbdir_override=keep.kbdir_override if keep else None,
    )


def access_rank(level: str) -> int:
    """0 for public up to 3 for private."""
    return ACCESS_LEVELS.index(level)


def cache_home() -> Path:
    """The user's kb cache: $XDG_CACHE_HOME/wckb (snapshots of neighbors, source directories ...)."""
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "wckb"


def source_settings(raw: dict, source_type: str) -> dict:
    """Settings of one source type: ``[sources.<type>]`` (store, user config or .kb/local.toml)."""
    return raw.get("sources", {}).get(source_type, {}) or {}


def source_dir(raw: dict, source_type: str) -> Path:
    """Where a source type keeps local copies on this machine (URL downloads, DataLad clones):
    ``sources.<type>.dir``, by default under the user cache."""
    d = source_settings(raw, source_type).get("dir")
    return Path(os.path.expandvars(str(d))).expanduser() if d else cache_home() / source_type
