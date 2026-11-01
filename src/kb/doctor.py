"""``kb doctor``: find common problems and fix them, automatically where safe.

A *remedy* knows one problem: ``diagnose`` finds it and returns findings,
``apply`` fixes one finding.  Each finding has a mode:

    auto     safe and mechanical; ``kb doctor --fix`` applies it
    ask      changes something a person may want to weigh (network, tools,
             copying content); applied after confirmation (or --yes)
    manual   advice only: kb cannot fix it, the finding says how

Quick remedies are cheap enough to run on every ``kb status``; the rest run
with ``kb doctor``.  New remedies register with ``@remedy``; this list is
meant to grow with user testing.  When run by an agent, a fix follows the
autonomy of the remedy's action (``curate`` unless it says otherwise).
"""

import logging
import os
import shutil
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .errors import KBError
from .items import Store

log = logging.getLogger(__name__)

MODES = ("auto", "ask", "manual")


@dataclass
class Finding:
    code: str
    summary: str                  # what is wrong
    fix: str                      # what the fix does, or (manual) what to do
    mode: str = "manual"
    where: str = ""
    data: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "data"}


@dataclass
class Remedy:
    code: str
    title: str
    diagnose: Callable[[Store], list[Finding]]
    apply: Callable[[Store, Finding], str] | None = None
    quick: bool = True
    action: str = "curate"        # autonomy action governing agents' fixes


REMEDIES: dict[str, Remedy] = {}


def remedy(code: str, title: str, quick: bool = True, action: str = "curate"):
    """Register DIAGNOSE (decorated); attach a fixer with ``@<fn>.fixer``."""
    def deco(fn):
        r = Remedy(code, title, fn, quick=quick, action=action)
        REMEDIES[code] = r

        def fixer(apply_fn):
            r.apply = apply_fn
            return apply_fn
        fn.fixer = fixer
        return fn
    return deco


def diagnose(kb: Store, codes: list[str] | None = None, quick: bool = False) -> list[Finding]:
    unknown = set(codes or []) - set(REMEDIES)
    if unknown:
        raise KBError(f"no remedy {', '.join(sorted(unknown))} (kb doctor --list)")
    out = []
    for r in REMEDIES.values():
        if (codes and r.code not in codes) or (quick and not r.quick):
            continue
        try:
            out += r.diagnose(kb)
        except Exception as exc:          # one broken diagnosis must not hide the others
            out.append(Finding(r.code, f"could not diagnose: {exc}", "report this; run the other checks by hand"))
    return out


def _allowed(kb: Store, r: Remedy, approved: str | None) -> str | None:
    """Why an agent may not apply this fix (None: it may)."""
    if not kb.cfg.is_agent:
        return None
    level = kb.cfg.autonomy_for(r.action)
    if level == "never":
        return f"autonomy: {r.action} is reserved for the human"
    if level == "ask" and not approved:
        return f"autonomy: needs the human's approval (--approved \"<their words>\")"
    return None


def treat(kb: Store, findings: list[Finding], confirm: Callable[[Finding], bool],
          approved: str | None = None) -> list[dict]:
    """Apply fixes: auto ones always, ask ones when CONFIRM says yes.  One result per finding."""
    results = []
    for f in findings:
        r = REMEDIES.get(f.code)
        res = {"code": f.code, "where": f.where, "summary": f.summary}
        log.debug("%s %s: %s (%s)", f.code, f.where, f.summary, f.mode)
        if f.mode == "manual" or r is None or r.apply is None:
            results.append(res | {"result": "advice", "detail": f.fix})
            continue
        why = _allowed(kb, r, approved)
        if why:
            results.append(res | {"result": "skipped", "detail": why})
            continue
        if f.mode == "ask" and not confirm(f):
            results.append(res | {"result": "skipped", "detail": "not confirmed"})
            continue
        try:
            results.append(res | {"result": "fixed", "detail": r.apply(kb, f)})
            log.info("fixed %s %s: %s", f.code, f.where, results[-1]["detail"])
        except KBError as exc:
            log.error("fixing %s %s failed: %s", f.code, f.where, exc)
            results.append(res | {"result": "failed", "detail": str(exc)})
        kb.reload()
    return results


# -- the remedies -----------------------------------------------------------------------
# Order matters a little: repairs of the store come before work that relies on it.

@remedy("network-fs", "The store is on a network filesystem")
def _network_fs(kb):
    from .db import fs_type, is_network_fs
    t = fs_type(kb.root)
    if is_network_fs(kb.root) and not os.environ.get("KB_ALLOW_NETWORK_FS"):
        return [Finding("network-fs", f"the store is on {t}; SQLite locking is unreliable there, so writes are refused",
                        "move the store to local disk and publish read-only copies (kb store snapshot) where others "
                        "read it; the content store (cas) may stay on the network filesystem")]
    return []


@remedy("query-tables", "Query tables out of step with the items")
def _query_tables(kb):
    n_items = len(kb.items)
    n_fts = kb.conn.execute("SELECT count(DISTINCT id) FROM fts").fetchone()[0]
    if n_items != n_fts:
        return [Finding("query-tables", f"search covers {n_fts} of {n_items} item(s)",
                        "rebuild the query tables (kb store reindex)", "auto")]
    return []


@_query_tables.fixer
def _reindex(kb, f):
    return f"reindexed {kb.reindex()} item(s)"


@remedy("gitignore", "A store inside a git working tree is not ignored")
def _gitignore(kb):
    from .db import DB_NAME
    from .scaffold import _in_git_worktree
    if shutil.which("git") is None or not _in_git_worktree(kb.root):
        return []
    out = []
    tracked = subprocess.run(["git", "-C", str(kb.root), "ls-files", "--error-unmatch", DB_NAME],
                             capture_output=True, text=True).returncode == 0
    if tracked:
        out.append(Finding("gitignore", f"{DB_NAME} is tracked by the git repository around the store",
                           f"untrack it: git -C {kb.root} rm --cached {DB_NAME} (share knowledge with bundles instead)",
                           where=str(kb.root)))
    ignored = subprocess.run(["git", "-C", str(kb.root), "check-ignore", "-q", DB_NAME]).returncode == 0
    if not ignored and not tracked:
        out.append(Finding("gitignore", "the store is inside a git working tree and not ignored",
                           f"write {kb.root / '.gitignore'} ignoring everything in the store directory", "auto",
                           str(kb.root)))
    return out


@_gitignore.fixer
def _write_gitignore(kb, f):
    p = kb.root / ".gitignore"
    old = p.read_text() if p.exists() else ""
    p.write_text(old + ("" if not old or old.endswith("\n") else "\n")
                 + "# a kb store: not versioned here (share it with kb bundles)\n*\n")
    return f"wrote {p}"


@remedy("kb-on-path", "kb is not on PATH for Claude Code and git hooks")
def _kb_on_path(kb):
    if shutil.which("kb"):
        return []
    return [Finding("kb-on-path", "the kb command is not on PATH, so the enacted hooks and LLM sessions cannot run it",
                    "install it as a tool: uv tool install --editable <path to wckb> (or put its bin on PATH)")]


def _login_slug() -> str:
    import getpass
    import re
    try:
        login = getpass.getuser()
    except Exception:          # no login name in this environment
        login = "yourname"
    return re.sub(r"[^a-z0-9-]+", "-", login.lower()).strip("-") or "yourname"


@remedy("user", "Who is making items is not configured")
def _user(kb):
    from .config import user_config_path
    if os.environ.get("KB_USER") or kb.cfg.raw.get("user"):
        return []
    slug = _login_slug()
    login = kb.cfg.user
    mine = [r.id for r in kb.items.values() if r.kind == "person"
            and (r.id == f"per-{slug}" or r.data.get("created", {}).get("by") == login)]
    if len(mine) == 1:
        return [Finding("user", f"items are attributed to the login name {login!r}, not to a person item",
                        f'set user = "{mine[0]}" in {user_config_path()}', "ask", mine[0])]
    return [Finding("user", f"items are attributed to the login name {login!r}, not to a person item",
                    f'make your person item: kb new person --slug {slug} --set name="Your Name"; '
                    f"then kb doctor user --fix sets user = \"per-{slug}\" in {user_config_path()}")]


@_user.fixer
def _set_user(kb, f):
    from .config import set_user
    return f"user = \"{f.where}\" in {set_user(f.where)}"


@remedy("policy", "Policy not enacted, outdated or edited by hand", action="policy")
def _policy(kb):
    from . import policy
    out = []
    items = policy.status(kb)
    edited = any("edited" in i["what"] for i in items)
    for i in items:
        auto = i["fix"] == "kb policy enact" and not edited
        out.append(Finding("policy", i["what"], "enact the selected policy (kb policy enact)" if auto else i["fix"],
                           "auto" if auto else "manual", i.get("policy", "")))
    return out


@_policy.fixer
def _enact(kb, f):
    from . import policy
    r = policy.enact(kb)
    return f"enacted {', '.join(r['policies'])} in {r['target']}" if r["changes"] else "already enacted"


@remedy("cas", "Content stores not available on this machine")
def _cas(kb):
    used: dict[str, int] = {}
    for rec in kb.items.values():
        for r in rec.renditions:
            for loc in r.get("locations", []):
                if loc.get("type") == "cas":
                    used[loc["name"]] = used.get(loc["name"], 0) + 1
    out = []
    for name, n in sorted(used.items()):
        p = kb.cfg.cas.get(name)
        if p is None:
            out.append(Finding("cas", f"content store {name!r} holds {n} rendition(s) but has no path here",
                               f"add [cas.{name}] path = \"...\" to {kb.cfg.kbdir / 'local.toml'} "
                               f"(this machine) or kb config set cas.{name} '{{\"path\": \"...\"}}' (everyone)", where=name))
        elif not p.is_dir():
            out.append(Finding("cas", f"content store {name!r} is configured at {p}, which is not there",
                               "mount it, or fix the path in .kb/local.toml", where=name))
    return out


@remedy("edges", "Neighbor stores that cannot be reached")
def _edges(kb):
    from .federation import neighbors
    out = []
    for n in neighbors(kb):
        if n.available:
            continue
        if n.path is None and n.url:
            out.append(Finding("edges", f"neighbor {n.name}: its snapshot has not been downloaded",
                               f"download it (kb edge fetch {n.name})", "auto", n.name))
        else:
            out.append(Finding("edges", f"neighbor {n.name}: no store at {n.path}",
                               f"mount it, or relink it (kb edge remove {n.name}; kb edge add {n.name} --path ...)",
                               where=n.name))
    return out


@_edges.fixer
def _fetch(kb, f):
    from .federation import fetch
    res = dict(fetch(kb, [f.where]))
    if res.get(f.where, "").startswith("failed"):
        raise KBError(res[f.where])
    return f"{f.where}: {res.get(f.where)}"


@remedy("secret-perms", "Token files readable by others", action="curate")
def _secret_perms(kb):
    from . import forge, secrets
    out = []
    for fc in forge.configured(kb.cfg.raw):
        if fc.token is None:
            continue
        try:
            s = secrets.parse(fc.token)
        except KBError:
            continue
        if s.scheme != "file":
            continue
        p = Path(os.path.expandvars(str(s.value))).expanduser()
        if p.exists() and p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            out.append(Finding("secret-perms", f"forge {fc.name}: token file {p} is mode "
                                               f"{stat.S_IMODE(p.stat().st_mode):04o}",
                               f"chmod 600 {p} (better still: keep it in pass, token = {{command = \"pass show\", "
                               f"id = \"kb/forge/{fc.name}\"}})", "ask", str(p)))
    return out


@_secret_perms.fixer
def _chmod(kb, f):
    Path(f.where).chmod(0o600)
    return f"chmod 600 {f.where}"


@remedy("stale", "Derived renditions, views and topics whose inputs changed")
def _stale(kb):
    from . import graph
    out = []
    for s in graph.stale(kb):
        why = "; ".join(s.reasons)
        if s.what == "rendition":
            out.append(Finding("stale", f"{s.id}#{s.rendition}: {why}", "re-extract it with its transform", "auto",
                               f"{s.id}#{s.rendition}", {"what": "rendition", "id": s.id, "rendition": s.rendition}))
        elif s.what == "view":
            out.append(Finding("stale", f"view {s.id}: {why}", f"rebuild it (kb view build {s.id})", "ask", s.id,
                               {"what": "view", "id": s.id}))
        else:
            out.append(Finding("stale", f"{s.what} {s.id}: {why}",
                               f"revise it (an LLM with the kb-correct or kb-curate skill), then kb item stamp {s.id}",
                               where=s.id))
    return out


@_stale.fixer
def _refresh(kb, f):
    if f.data["what"] == "rendition":
        from . import transforms
        rec = kb.get(f.data["id"])
        r = rec.rendition(f.data["rendition"])
        transforms.extract(kb, rec, r["derived_from"], r["transform"]["name"], f.data["rendition"], force=True)
        return f"re-extracted {f.where}"
    from . import build
    man = build.build(kb, f.data["id"])
    return f"built {man['view']}: {len(man['outputs'])} output(s)"


def _datalad_sources(kb):
    from .datasets import ANNEX_TYPES
    return [r for r in kb.items.values() if r.kind == "source" and r.data.get("source_type") in ANNEX_TYPES]


@remedy("datalad-support", "DataLad sources in use, but DataLad support is not installed")
def _datalad_support(kb):
    from .datasets import INSTALL, available
    srcs = _datalad_sources(kb)
    why = available() if srcs else None
    if why:
        return [Finding("datalad-support", f"{len(srcs)} DataLad source(s) here, but {why}",
                        f"install it: {INSTALL}; until then only content already on this machine is reachable")]
    return []


@remedy("gitattributes", "git ignores .gitattributes, which DataLad datasets need")
def _gitattributes(kb):
    from .datasets import locate
    for rec in _datalad_sources(kb):
        root = locate(kb, rec)
        if root is None:
            continue
        r = subprocess.run(["git", "-C", str(root), "check-ignore", "--no-index", "-v", ".gitattributes"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            rule = r.stdout.split("\t")[0]
            return [Finding("gitattributes", f"your git ignore rules ignore .gitattributes ({rule}); DataLad "
                            "cannot create datasets or change annex settings while they do",
                            "remove that pattern (usually from ~/.config/git/ignore or core.excludesFile)")]
        return []
    return []


@remedy("clones", "DataLad datasets and git repositories used here but not cloned on this machine", quick=False)
def _clones(kb):
    from .datasets import REPO_TYPES, available, locate
    out = []
    used = {r[0] for r in kb.conn.execute("SELECT DISTINCT dst FROM refs WHERE rel = 'source'")}
    for rec in kb.items.values():
        if rec.kind != "source" or rec.data.get("source_type") not in REPO_TYPES:
            continue
        if rec.data["source_type"] != "git" and available() is not None:
            continue
        if rec.id in used and locate(kb, rec) is None:
            out.append(Finding("clones", f"{rec.id} ({rec.data.get('title')}) is not cloned here",
                               f"clone it (kb source clone {rec.id}); content is fetched only when needed", "ask",
                               rec.id))
    return out


@_clones.fixer
def _clone(kb, f):
    from .datasets import clone
    path, _ = clone(kb, f.where)
    return f"cloned {f.where} to {path}"


@remedy("unextracted", "Items with a PDF, slides or the like but no text from it, so search can't see inside")
def _unextracted(kb):
    from .transforms import unextracted
    todo = [(rec.id, src, t.name) for rec in kb.items.values() if kb.is_current(rec.id)
            for src, t in unextracted(kb, rec)]
    if todo:
        names = ", ".join(f"{i}#{s}" for i, s, _ in todo[:5]) + (" ..." if len(todo) > 5 else "")
        return [Finding("unextracted", f"{len(todo)} rendition(s) without extracted text: {names}",
                        "extract their text (kb rendition extract ID; may fetch content)", "auto",
                        data={"ids": sorted({i for i, _, _ in todo})})]
    return []


@_unextracted.fixer
def _extract(kb, f):
    from .transforms import extract_missing
    made, failed = [], []
    for ident in f.data["ids"]:
        m, bad = extract_missing(kb, kb.get(ident))
        made += m
        failed += bad
    return f"extracted {len(made)} text rendition(s)" + (f"; failed: {'; '.join(failed)}" if failed else "")


@remedy("embeddings", "Text in the search index without embeddings for semantic search")
def _embeddings(kb):
    c = kb.cfg.embed
    if not c or "model" not in c:
        return []
    from .embed import missing
    identity = c.get("identity") or c["model"]
    n, total = missing(kb, identity)
    if n > 0:
        return [Finding("embeddings", f"{n} of {total} indexed text(s) have no embeddings ({identity})",
                        "embed them (kb store embed; contacts the embedding service)", "ask")]
    return []


@_embeddings.fixer
def _embed(kb, f):
    from .embed import embed
    r = embed(kb)
    return f"embedded {r['embedded']} text(s)"


@remedy("schema-version", "Items written by an older kb", quick=False)
def _schema_version(kb):
    from .migrate import migrate
    ids = migrate(kb, dry_run=True)
    if ids:
        return [Finding("schema-version", f"{len(ids)} item(s) use an older schema",
                        "upgrade them in one transaction (kb store migrate)", "ask")]
    return []


@_schema_version.fixer
def _migrate(kb, f):
    from .migrate import migrate
    return f"migrated {len(migrate(kb))} item(s)"


@remedy("dangling", "References to items not in this store", quick=False, action="pull")
def _dangling(kb):
    from .federation import neighbors
    from .lint import dangling
    refs = sorted({r for _, r in dangling(kb)})
    if not refs:
        return []
    if not any(n.available for n in neighbors(kb)):
        return [Finding("dangling", f"{len(refs)} reference(s) to items not here, and no reachable neighbor",
                        "link the store that has them (kb edge add), then kb pull --dangling")]
    return [Finding("dangling", f"{len(refs)} reference(s) to items not here: {', '.join(refs[:5])}"
                                + (" ..." if len(refs) > 5 else ""),
                    "fetch them from the nearest store that has them (kb pull --dangling)", "ask")]


@_dangling.fixer
def _obtain(kb, f):
    from .push import obtain
    reps = obtain(kb, dangling=True, approved="kb doctor, confirmed")
    got = sum(len(r.get("items", {}).get("new", [])) for r in reps if "items" in r)
    left = next((r["not_found"] for r in reps if "not_found" in r), [])
    return f"fetched {got} item(s)" + (f"; not found anywhere: {', '.join(left)}" if left else "")


@remedy("local-only", "Content only on one machine while a content store is available", quick=False)
def _local_only(kb):
    default = kb.cfg.cas.get("default")
    if default is None or not default.is_dir():
        return []
    out = []
    for rec in kb.items.values():
        for r in rec.renditions:
            locs = r.get("locations", [])
            if locs and all(l["type"] == "local" for l in locs) and locs[0].get("host") == kb.cfg.host:
                ref = f"{rec.id}#{r['name']}"
                out.append(Finding("local-only", f"{ref} is only on this machine ({locs[0].get('path')})",
                                   f"copy it into the content store default (kb rendition locate {ref})", "ask", ref))
    return out


@_local_only.fixer
def _locate(kb, f):
    from .edit import add_location
    add_location(kb, f.where, "default")
    return f"copied {f.where} into content store default"


@remedy("check", "Errors found by the consistency scan", quick=False)
def _check(kb):
    from .checks import check
    by_code: dict[str, list] = {}
    for p in check(kb):
        if p.level == "error":
            by_code.setdefault(p.code, []).append(p)
    return [Finding("check", f"{len(ps)} {code} error(s), e.g. {ps[0].where}: {ps[0].message}",
                    (ps[0].hint + "; " if ps[0].hint else "") + "details: kb check", where=code)
            for code, ps in sorted(by_code.items())]
