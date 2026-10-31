"""Configuration of a store and of the machine using it.

Layers, last one wins:

1. The store's ``settings`` table (in ``kb.sqlite``): id, name, scope, access,
   limits, stores, neighbors, policy, autonomy ... (``kb config``,
   ``kb neighbor``, ``kb policy`` write it).
2. The user's ``config.toml`` (``$KB_CONFIG``, else ``$XDG_CONFIG_HOME/kb/``).
   Neighbors declared here apply to every store, which is how a personal
   "home" store becomes the parent of every project store; forges live here.
3. ``.kb/local.toml`` beside the store: this machine's paths.
4. ``KB_*`` environment variables.
5. Command-line options (applied by the CLI).
"""

import os
import socket
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import KBError

CONFIG_NAME = "kb.sqlite"   # the file that marks (and is) a store
SUBDIR = "kb"     # a project's store may live in ./kb next to its working files
SCOPES = ("project", "home", "group", "org", "other")
RELATIONS = ("up", "peer", "down", "twin")
AUTONOMY_LEVELS = ("never", "ask", "auto")
AGENT_ENV = ("KB_AGENT", "CLAUDECODE", "AI_AGENT")
DEFAULT_MAX_INLINE = 1024 * 1024
ACCESS_LEVELS = ("public", "collaboration", "group", "private")


def find_root(start: Path | None = None) -> Path:
    """The store: ``$KB_ROOT``, else the nearest ancestor holding ``kb.sqlite``
    or ``kb/kb.sqlite`` (a project store beside the working files)."""
    env = os.environ.get("KB_ROOT")
    if env:
        root = Path(env).expanduser().resolve()
        if not (root / CONFIG_NAME).exists():
            raise KBError(f"KB_ROOT={env} holds no {CONFIG_NAME}")
        return root
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / CONFIG_NAME).exists():
            return d
        if (d / SUBDIR / CONFIG_NAME).exists():
            return d / SUBDIR
    raise KBError(f"not inside a kb store (no {CONFIG_NAME} here or above); use -C, KB_ROOT or kb init")


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


def user_config_path() -> Path:
    env = os.environ.get("KB_CONFIG")
    if env:
        return Path(env).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "kb" / "config.toml"


@dataclass
class Config:
    root: Path
    name: str
    schema: str
    access: str
    max_inline_bytes: int
    stores: dict[str, Path | None]
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
        """Who is making records here: a person ref or a name."""
        return (os.environ.get("KB_USER") or self.raw.get("user")
                or os.environ.get("USER") or "unknown")


def load(root: Path | None = None) -> Config:
    """Configuration of the store at *root* (read-only)."""
    from .records import KB
    kb = KB.open(root, readonly=True)
    try:
        return kb.cfg
    finally:
        kb.close()


def from_settings(root: Path, settings: dict, keep: "Config | None" = None) -> Config:
    """Merge a store's settings with the user's and this machine's configuration."""
    raw = dict(settings)
    raw = _merge(raw, _read(user_config_path()))
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

    stores = {name: path(s.get("path")) for name, s in raw.get("stores", {}).items()}
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
        stores=stores,
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
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "kb"
