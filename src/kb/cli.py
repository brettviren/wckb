"""The ``kb`` command line.  Parses arguments, calls logic modules, renders.

No data processing lives here: see items, authoring, checks, index, build...
"""

import functools
import json
import sys
from pathlib import Path

import click

from . import __version__, ui
from .errors import KBError
from .ids import KINDS


class Ctx:
    def __init__(self, root: Path | None):
        self.root = root
        self._kb = None
        self.context = None     # the store context, once the store is opened

    @property
    def kb(self):
        if self._kb is None:
            from .config import store_context
            from .items import Store
            self.context = store_context(root=self.root)
            root = self.context.location
            try:
                self._kb = Store.open(root)
            except KBError as exc:
                if "network filesystem" not in str(exc):
                    raise
                import logging
                logging.getLogger(__name__).warning("%s: opened read-only instead", exc)
                self._kb = Store.open(root, readonly=True)   # reading is fine; writes will say why not
            if sys.stdin.isatty():           # a person is here: ask where new DataLad clones go (if so configured)
                from . import datasets
                datasets.ASK = _ask_location
        return self._kb


pass_ctx = click.make_pass_decorator(Ctx)


def handle_errors(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except KBError as exc:
            import logging
            logging.getLogger(__name__).error("kb %s: %s", " ".join(sys.argv[1:2]), exc)
            raise click.ClickException(str(exc)) from None
    return wrapper


def command(group, *args, **kwargs):
    """A subcommand whose KBErrors become clean one-line failures."""
    def deco(fn):
        return group.command(*args, **kwargs)(handle_errors(fn))
    return deco


SECTIONS = [
    ("Everyday", ["init", "status", "add", "new", "show", "ls", "search", "context", "log"]),
    ("Keeping a store sound", ["check", "lint", "doctor", "config"]),
    ("Moving knowledge between stores", ["push", "pull", "sync"]),
    ("Commands by thing (kb THING --help)", ["item", "rendition", "asset", "source", "kset", "edge", "bundle",
                                             "inbox", "view", "data", "policy", "store"]),
    ("Integration", ["exchange", "forge", "secret"]),
]


class Group(click.Group):
    """Subcommands in the order they are defined (most used first), with whole first sentences."""

    order: list[str] = []

    def list_commands(self, ctx):
        return [n for n in self.order if n in self.commands] + [n for n in self.commands if n not in self.order]

    def rows(self, names, width=0):
        return [(n.ljust(width), self.commands[n].get_short_help_str(200)) for n in names
                if n in self.commands and not self.commands[n].hidden]

    def format_commands(self, ctx, formatter):
        rows = self.rows(self.list_commands(ctx))
        if rows:
            with formatter.section("Commands"):
                formatter.write_dl(rows)


class TopGroup(Group):
    """The top level: verbs used every day first, then one group per thing, in sections."""

    def list_commands(self, ctx):
        listed = [n for _, names in SECTIONS for n in names]
        return listed + sorted(set(self.commands) - set(listed))

    def format_commands(self, ctx, formatter):
        listed = {n for _, names in SECTIONS for n in names}
        width = max(map(len, self.commands))
        for title, names in SECTIONS + [("Other", sorted(set(self.commands) - listed))]:
            rows = self.rows(names, width)
            if rows:
                with formatter.section(title):
                    formatter.write_dl(rows)


@click.group(cls=TopGroup, context_settings={"help_option_names": ["-h", "--help"]})
@click.option("-C", "--kb", "root", type=click.Path(file_okay=False, path_type=Path),
              envvar="KB_ROOT", help="Store directory, whatever the store context says.")
@click.option("--context", "context_name", metavar="NAME",
              help="Store context (overrides $KB_CONTEXT and kb_context): a name, current-directory or "
                   "git-directory (the default).")
@click.option("-c", "--config", "config_file", type=click.Path(dir_okay=False, path_type=Path),
              help="A config file to apply after the others (even with --no-init-file).")
@click.option("--no-init-file", is_flag=True,
              help="Skip ~/.config/kb/config.toml and the .kb/config.toml, .kb.toml and kb.toml files here and above.")
@click.option("-l", "--log-level", type=click.Choice(["debug", "info", "warning", "error", "critical"],
                                                     case_sensitive=False),
              help="Log from this level (default: warning).")
@click.option("-L", "--log-sink", metavar="SINK",
              help="Log to this file (appended to), or stdout or stderr (default: ~/.cache/kb/logs/kb-DATE.log).")
@click.option("--debug", is_flag=True, help="Log everything to stdout: -l debug -L stdout.")
@click.version_option(__version__, prog_name="kb")
@click.pass_context
def main(ctx, root, context_name, config_file, no_init_file, log_level, log_sink, debug):
    """kb: knowledge stores shared by people and their LLMs.

    \b
    Find:     kb ls, kb search WORDS, kb context SUBJECT, kb show ID
    Capture:  kb add FILE|URL ..., kb new KIND ...
    Correct:  kb item set ID key=value, kb item supersede ID ...
    Share:    kb push --to NEIGHBOR ..., kb inbox list

    Every command has --help.  Words: docs/glossary.md.  How-tos: docs/howto/.

    The store is the store context's (--context): by default that of the
    git working tree here; see docs/howto/contexts.md.
    """
    from . import config, logs
    logs.setup("debug" if debug else (log_level or logs.DEFAULT_LEVEL), "stdout" if debug else log_sink)
    config.INVOCATION = config.Invocation(context=context_name, config=config_file, no_init_file=no_init_file)
    ctx.obj = Ctx(root)


def group(name, help_text, parent=None, hidden=False, order=()):
    """A command family; with no subcommand it shows its help.  ORDER: the subcommands listed first."""
    g = (parent or main).group(name, cls=Group, help=help_text, hidden=hidden, no_args_is_help=True)(lambda: None)
    g.order = list(order)
    return g


item_grp = group("item", "Items: change, correct, withdraw, edit, inspect derivation, schemas.",
                 order=["set", "edit", "supersede", "retract", "content", "checkout", "checkin",
                        "deps", "graph", "stale", "stamp", "schema"])
rendition_grp = group("rendition", "Renditions of an item: add, mirror, extract text, get a file, verify.",
                      order=["path", "add", "extract", "transforms", "locate", "verify"])
asset_grp = group("asset", "Assets: abstract files and file sets, their versions, and the sources that supply them.")
source_grp = group("source", "Data sources: the kinds kb knows, and the concrete sources (DataLad datasets ...) items use.",
                   order=["list", "show", "add", "clone", "link", "types"])
kset_grp = group("kset", "Knowledge sets: selections of items, the unit of transfer.")
edge_grp = group("edge", "Edges to neighbor stores (up, down, peer, twin), and what moved along them.",
                 order=["list", "add", "remove", "fetch", "candidates", "ledger"])
bundle_grp = group("bundle", "Bundles: a knowledge set and its items in one self-checking file.")
inbox_grp = group("inbox", "Received bundles: review them (people and LLMs), then accept or reject.",
                  order=["list", "receive", "show", "review", "accept", "reject"])
view_grp = group("view", "Views: build derived presentations and publish them.")
data_grp = group("data", "Describe and package data for use outside kb (Frictionless, RO-Crate).")
policy_grp = group("policy", "LLM policy: named, versioned instructions, skills, hooks; selection, enactment, autonomy.")
store_grp = group("store", "The store itself: snapshots, query tables, embeddings, schema migration.",
                  order=["snapshot", "reindex", "embed", "migrate"])
config_grp = group("config", "The store's settings (held in the store).")
exchange_grp = group("exchange", "Exchange repositories: git repositories of bundles, for transport and review.",
                     order=["init", "hooks"])
exchange_hooks_grp = group("hooks", "Install or remove kb's checks in a repository's git hooks (pre-commit, pre-push, post-merge).",
                           exchange_grp)
forge_grp = group("forge", "GitHub / Gitea / Forgejo / Gogs: authentication and pull requests.")
pr_grp = group("pr", "Pull requests on a forge repository.", forge_grp)
secret_grp = group("secret", "Secrets (tokens) from pass-like commands, 0600 files, or the environment.")
hook_grp = group("hook", "Entry points for Claude Code and git hooks (they read the event on stdin).", hidden=True)


# -- options shared by commands that make items -----------------------------

def item_options(fn):
    opts = [
        click.option("--title", help="Short title."),
        click.option("--summary", help="1-3 sentences for the next reader (an LLM); not the title again."),
        click.option("--subject", multiple=True, help="Subject path, e.g. detector/tpc/noise (repeatable)."),
        click.option("--tag", "tags", multiple=True, help="Free label (repeatable)."),
        click.option("--runs", multiple=True, help="Valid for runs: N or FIRST-LAST (repeatable)."),
        click.option("--start", help="Valid from (date)."),
        click.option("--end", help="Valid until (date)."),
        click.option("--status", type=click.Choice(["draft", "unverified", "verified", "disputed"])),
        click.option("--access", type=click.Choice(["public", "collaboration", "group", "private"])),
        click.option("--by", help="Who asserted it (person ID or name)."),
        click.option("--how", help="How it was asserted: 'verbal at collab meeting', 'elog 5521' ..."),
        click.option("--when", help="When it was asserted (date)."),
        click.option("--creators", multiple=True, help="Author (repeatable)."),
        click.option("--date", help="Date of the thing itself (e.g. of a talk)."),
        click.option("--identifier", "identifiers", multiple=True, help="scheme=value: doi=, arxiv=, indico=, inspire= ..."),
        click.option("--evidence", multiple=True, help="Supporting ref or URL (repeatable)."),
        click.option("--related", multiple=True, help="Related ref (repeatable)."),
        click.option("--derived-from", "derived_from", multiple=True, help="Input ref, for topics and views (repeatable)."),
        click.option("--license"),
        click.option("--set", "sets", multiple=True, help="Any field: key=value (JSON values parsed; dotted keys nest)."),
        click.option("--via", help="Tool or session making the item (e.g. claude-code)."),
    ]
    for o in reversed(opts):
        fn = o(fn)
    return fn


def _fields(kw) -> dict:
    from .authoring import fields_from_options
    idents = {}
    for item in kw.pop("identifiers", ()):
        if "=" not in item:
            raise KBError(f"--identifier wants scheme=value, got {item!r}")
        k, v = item.split("=", 1)
        idents[k] = v
    return fields_from_options(identifiers=idents, **kw)


def _text(text, text_file):
    if text is not None and text_file is not None:
        raise KBError("give --text or --text-file, not both")
    if text_file is not None:
        return sys.stdin.read() if str(text_file) == "-" else Path(text_file).read_text()
    return text


def _done(kb, rec, json_out=False):
    if json_out:
        ui.emit_json(rec.data)
    else:
        click.echo(rec.id)
        ui.info(f"stored {rec.kind} {rec.id} in {kb.cfg.name}")


# -- making items -------------------------------------------------------------

@command(main, "init")
@click.argument("path", type=click.Path(file_okay=False, path_type=Path), required=False)
@click.option("--scope", default="other", type=click.Choice(["project", "home", "group", "org", "other"]),
              show_default=True, help="What this store holds knowledge for; sets defaults for access, policy, autonomy.")
@click.option("--name", help="Store name (default: directory name, or the project's for ./kb).")
@click.option("--access", type=click.Choice(["public", "collaboration", "group", "private"]),
              help="Who may see this store (default: private for project/home, group for group).")
@click.option("--target", help="Where policy is enacted: a directory (CLAUDE.md, .claude/) or 'user' (~/.claude).")
@click.option("--up", type=click.Path(file_okay=False, exists=True, path_type=Path),
              help="An existing store to link as this one's 'up' neighbor (e.g. your home store).")
@click.option("--no-enact", is_flag=True, help="Select policy but do not enact it yet.")
@pass_ctx
def init_cmd(c, path, scope, name, access, target, up, no_enact):
    """Make PATH a kb store (one SQLite file, kb.sqlite); by default, the store of the store context.

    Without PATH the store goes where the context says (context.NAME.location),
    else in ~/.local/share/kb/contexts/NAME; its policy is enacted in the
    directory the context is named for (the git working tree, by default).

    \b
      kb init --scope project                         # this git working tree's store
      kb init ~/kb --scope home                       # your personal store
      kb init ./kb --scope project --up ~/kb          # a project store beside the working files
      kb init /data/kb/tpc --scope group              # a group store (one writer; shared by bundles)
    """
    from .scaffold import init
    try:
        if path is None:
            from .config import DEFAULT_LOCATED, resolve_context
            here = resolve_context(root=c.root)
            if here.exists:
                raise KBError(f"the context {here.name} has a store already: {here.location} ({here.located_by}); "
                              f"give PATH, or set context.{here.name}.location, to make another")
            path = here.location
            if here.directory is not None:
                name = name or here.directory.name
                if here.located_by == DEFAULT_LOCATED:
                    target = target or str(here.directory)
        for f in init(path, name, access, scope, target, False, not no_enact, up):
            click.echo(f"created {f}")
    except KBError as exc:
        raise click.ClickException(str(exc)) from None


@command(main, "new", no_args_is_help=True)
@click.argument("kind", type=click.Choice(KINDS))
@item_options
@click.option("--text", help="Content (note.md, or page.md for topics).")
@click.option("--text-file", type=click.Path(dir_okay=False, allow_dash=True), help="Content from a file (- for stdin).")
@click.option("--slug", help="Readable ID suffix (person items only).")
@click.option("--from-json", "from_json", type=click.File(), help="Partial item as JSON (- for stdin); kb fills the rest.")
@click.option("--json", "json_out", is_flag=True, help="Print the item as JSON.")
@pass_ctx
def new_cmd(c, kind, text, text_file, slug, from_json, json_out, via, **kw):
    """Make a new item of KIND.

    \b
      kb new observation --title "Noisy run 1234" --summary "..." \\
         --subject detector/tpc/noise --runs 1234 --by per-ann --how "verbal, collab mtg" \\
         --text "Coherent noise elevated on all planes during run 1234."
      kb new config --title ... --set system=production/sim --set 'parameters={"drift_mm_us":1.6}' --start 2025-01-01
      kb new person --slug ann --set name="Ann Example"
    """
    from . import authoring
    kb = c.kb
    fields = _fields(kw)
    if from_json is not None:
        payload = json.load(from_json)
        payload.setdefault("kind", kind)
        if payload["kind"] != kind:
            raise KBError(f"JSON says kind {payload['kind']}, command says {kind}")
        payload = authoring.deep_merge(payload, fields)
        rec = authoring.from_json(kb, payload, _text(text, text_file), via=via)
    else:
        rec = authoring.new(kb, kind, fields, _text(text, text_file), slug=slug, via=via)
    _done(kb, rec, json_out)


@command(main, "add", no_args_is_help=True)
@click.argument("sources", nargs=-1, required=True)
@click.option("--kind", default="document", type=click.Choice(["document", "dataset", "config", "snapshot", "software", "observation"]),
              show_default=True)
@item_options
@click.option("--place", "mode", default="auto", show_default=True,
              type=click.Choice(["auto", "db", "cas", "local", "datalad", "none"]),
              help="Where content goes: auto = left in its DataLad dataset if committed there, else in the store "
                   "if small, else the content store (cas), else local-only.")
@click.option("--cas", "cas_name", help="Content store (cas) to use (default: 'default').")
@click.option("--url", help="Canonical URL of a single local file (e.g. Indico behind SSO).")
@click.option("--no-fetch", is_flag=True, help="Record URLs without downloading (no hash).")
@click.option("--supersedes", "supersedes", help="This is a new version of that item.")
@click.option("--extract", is_flag=True, help="Run kb rendition extract afterwards.")
@click.option("--force", is_flag=True, help="Add even if an identifier is already in this store.")
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def add_cmd(c, sources, kind, mode, cas_name, url, no_fetch, supersedes, extract, force, json_out, via, **kw):
    """Add files or URLs as a new primary item (default kind: document).

    \b
      kb add slides.pdf --url https://indico.example/... --set doctype=presentation \\
         --title "TPC noise update" --summary "..." --creators "A. Person" --date 2026-10-28
      kb add https://arxiv.org/pdf/2601.01234 --identifier arxiv=2601.01234 --set doctype=preprint ...
      kb add noise.csv make_noise.py --kind dataset --set format=csv --title ... --runs 1200-1300
    """
    from . import authoring, transforms
    kb = c.kb
    fields = _fields(kw)
    if supersedes:
        fields["supersedes"] = [kb.get(supersedes).id]
    rec = authoring.add(kb, kind, list(sources), fields, mode=mode, cas_name=cas_name, url=url,
                        fetch=not no_fetch, force=force, via=via)
    if extract:
        try:
            r = transforms.extract(kb, rec)
            ui.info(f"extracted {rec.id}#{r['name']} with {r['transform']['name']}")
        except KBError as exc:
            ui.info(f"extract skipped: {exc}")
    _done(kb, rec, json_out)


@command(item_grp, "supersede", no_args_is_help=True)
@click.argument("ref")
@item_options
@click.option("--text", help="The corrected content.")
@click.option("--text-file", type=click.Path(dir_okay=False, allow_dash=True))
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def supersede_cmd(c, ref, text, text_file, json_out, via, **kw):
    """Correct an item: a new one that supersedes REF (fields inherited unless given)."""
    from .authoring import supersede
    kb = c.kb
    _done(kb, supersede(kb, ref, _fields(kw), _text(text, text_file), via=via), json_out)


@command(item_grp, "retract", no_args_is_help=True)
@click.argument("ref")
@click.option("--reason", required=True, help="Why it is withdrawn.")
@click.option("--by", help="Who withdraws it.")
@click.option("--evidence", multiple=True)
@click.option("--via")
@pass_ctx
def retract_cmd(c, ref, reason, by, evidence, via):
    """Withdraw REF without replacement (writes a retraction observation)."""
    from .authoring import retract
    kb = c.kb
    fields = {}
    if by:
        fields["asserted_by"] = [{"who": by}]
    if evidence:
        fields["evidence"] = list(evidence)
    _done(kb, retract(kb, ref, reason, fields, via=via))


@command(item_grp, "set", no_args_is_help=True)
@click.argument("ref")
@click.argument("assignments", nargs=-1)
@click.option("--add", "adds", multiple=True, help="Append to a list field: key=value.")
@click.option("--remove", "removes", multiple=True, help="Remove from a list field: key=value.")
@click.option("--replace-ref", multiple=True, help="OLD=NEW in derived_from, evidence and related.")
@pass_ctx
def set_cmd(c, ref, assignments, adds, removes, replace_ref):
    """Change fields of an item, within policy (key=value; key=null deletes).

    On primary items only status, subject, tags, summary, evidence, related,
    identifiers and license may change; the rest needs kb item supersede.
    """
    from .edit import set_fields
    rec = set_fields(c.kb, ref, assignments, adds, removes, replace_ref)
    click.echo(rec.id)


@command(rendition_grp, "add", no_args_is_help=True)
@click.argument("ref")
@click.argument("file", type=click.Path(dir_okay=False, exists=True, path_type=Path))
@click.option("--name", help="Rendition name (default: file stem).")
@click.option("--role", default="derived", type=click.Choice(["derived", "metadata", "content", "original"]), show_default=True)
@click.option("--fidelity", type=click.Choice(["lossless", "extracted", "llm"]))
@click.option("--from", "source", help="Rendition it was derived from.")
@click.option("--tool", help="What made it, e.g. claude-opus-5-5.")
@click.option("--media-type")
@click.option("--notes")
@pass_ctx
def rendition_add_cmd(c, ref, file, name, role, fidelity, source, tool, media_type, notes):
    """Register FILE as a rendition of REF (held in the store if small, else in the content store)."""
    from .edit import add_rendition
    r = add_rendition(c.kb, ref, file, name, role, fidelity, source, tool, media_type, notes)
    click.echo(f"{c.kb.get(ref).id}#{r['name']}")


@command(rendition_grp, "locate", no_args_is_help=True)
@click.argument("ref")
@click.option("--cas", "cas_name", help="Copy into this content store (the default one if neither option is given).")
@click.option("--url", help="Add this URL as a mirror (fetched and hash-checked).")
@click.option("--file", type=click.Path(dir_okay=False, exists=True, path_type=Path),
              help="Local copy to store, when the content is not reachable otherwise.")
@pass_ctx
def rendition_locate_cmd(c, ref, cas_name, url, file):
    """Add a mirror (content-store copy or URL) to rendition REF = ID#NAME; content must match its hash."""
    from .edit import add_location
    loc = add_location(c.kb, ref, cas_name, url, file)
    click.echo(json.dumps(loc))


@command(rendition_grp, "extract", no_args_is_help=True)
@click.argument("ref", required=False)
@click.option("--from", "source", help="Source rendition (default: the first original).")
@click.option("--transform", help="Transform name (see kb rendition transforms).")
@click.option("--name", default="text", show_default=True, help="Name of the derived rendition.")
@click.option("--force", is_flag=True, help="Redo even if up to date.")
@click.option("--stale", "all_stale", is_flag=True, help="Redo every stale derived rendition.")
@pass_ctx
def extract_cmd(c, ref, source, transform, name, force, all_stale):
    """Derive a text (or summary) rendition from REF's original."""
    from . import graph, transforms
    kb = c.kb
    if all_stale:
        for s in graph.stale_renditions(kb):
            rec = kb.get(s.id)
            r = rec.rendition(s.rendition)
            transforms.extract(kb, rec, r["derived_from"], r["transform"]["name"], s.rendition, force=True)
            click.echo(f"{s.id}#{s.rendition}")
        return
    if not ref:
        raise KBError("give an item or --stale")
    r = transforms.extract(kb, kb.get(ref), source, transform, name, force)
    click.echo(f"{kb.get(ref).id}#{r['name']}")
    ui.info(f"{r['transform']['name']} {r['transform'].get('version', '')}: {r['size']} bytes, fidelity {r['fidelity']}")


@command(rendition_grp, "transforms")
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def transforms_cmd(c, json_out):
    """List transforms and whether they are available here."""
    from .transforms import registry
    rows = [{"name": t.name, "available": t.available(), "fidelity": t.fidelity, "output": t.output_type,
             "media_types": list(t.media_types), "description": t.description} for t in registry(c.kb)]
    if json_out:
        return ui.emit_json(rows)
    for r in rows:
        mark = "yes" if r["available"] else "no "
        click.echo(f"{r['name']:<14} {mark}  {r['description']}")
        click.echo(f"{'':<19} {', '.join(r['media_types'])}")


# -- reading ------------------------------------------------------------------

@command(main, "show", no_args_is_help=True)
@click.argument("ref")
@click.option("--format", "fmt", default="text", show_default=True,
              type=click.Choice(["text", "md", "llm", "json", "view"]),
              help="text/md: for people; llm: compact markdown with the full content; json: the item JSON; "
                   "view: the rendered view model as JSON.")
@click.option("--json", "fmt", flag_value="json", help="Same as --format json.")
@click.option("--depth", default=4, show_default=True, help="How deep to unfold nested knowledge sets.")
@pass_ctx
def show_cmd(c, ref, fmt, depth):
    """One item: facts, links, where the material is, an excerpt (sets: as a tree)."""
    from . import render as r
    kb = c.kb
    rec = kb.get(ref)
    if fmt == "json":
        return ui.emit_json(rec.data)
    if fmt == "llm":
        from .context import _Out, _item_context
        return click.echo(_item_context(kb, rec, _Out(10**9)), nl=False)
    look = r.Lookup(kb)
    v = r.item_view(rec, look, kb, kb.cfg.raw)
    if rec.kind == "kset":
        v["tree"] = r.tree_view(rec.data, look, depth)
    if fmt == "view":
        return click.echo(r.to_json(v))
    click.echo(r.item_text(v, md=fmt == "md"))
    if rec.kind == "kset":
        click.echo("")
        click.echo("\n".join(r._tree_lines(v["tree"])))


@command(main, "ls")
@click.option("--kind", type=click.Choice(KINDS))
@click.option("--subject", help="Subject path or prefix.")
@click.option("--runs", help="Overlapping run range: N or FIRST-LAST.")
@click.option("--status", type=click.Choice(["draft", "unverified", "verified", "disputed"]))
@click.option("--since", help="Created on or after (date).")
@click.option("--all", "include_old", is_flag=True, help="Include superseded and retracted items.")
@click.option("--limit", type=int)
@click.option("-l", "--long", is_flag=True, help="Show summaries.")
@click.option("--subjects", is_flag=True, help="List subject paths in use, with counts.")
@click.option("--reach", default=0, help="Also list neighbor stores up to this many hops away.")
@click.option("--in", "only", multiple=True, help="Also list this neighbor store (repeatable).")
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def ls_cmd(c, kind, subject, runs, status, since, include_old, limit, long, subjects, reach, only, json_out):
    """List items (current ones unless --all), newest first."""
    from . import query
    from .context import parse_run_window
    kb = c.kb
    if subjects:
        counts = query.subjects(kb)
        if json_out:
            return ui.emit_json(counts)
        for subj, n in counts.items():
            click.echo(f"{n:>5}  {subj}")
        return
    if reach or only:
        from .federated import ls
        rows = ls(kb, reach, list(only), kind=kind, subject=subject, runs=parse_run_window(runs), status=status,
                  include_old=include_old, since=since, limit=limit)
        if json_out:
            return ui.emit_json(rows)
        return ui.item_list(rows, long, with_store=True)
    rows = query.ls(kb, kind, subject, parse_run_window(runs), status, include_old, since, limit)
    if json_out:
        return ui.emit_json(rows)
    ui.item_list(rows, long)


@command(main, "search", no_args_is_help=True)
@click.argument("words", nargs=-1, required=True)
@click.option("-s", "--semantic", "mode", flag_value="semantic", help="Match meaning (needs kb store embed).")
@click.option("--hybrid", "mode", flag_value="hybrid", help="Fuse word and meaning matches.")
@click.option("--all", "include_old", is_flag=True, help="Include superseded items.")
@click.option("--limit", default=20, show_default=True)
@click.option("--reach", default=0, help="Also search neighbor stores up to this many hops away.")
@click.option("--in", "only", multiple=True, help="Also search this neighbor store (repeatable).")
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def search_cmd(c, words, mode, include_old, limit, reach, only, json_out):
    """Search titles, summaries, subjects and text (words; a trailing * matches a prefix)."""
    from .federated import search
    rows = search(c.kb, " ".join(words), mode or "text", limit, include_old, reach, list(only))
    if json_out:
        return ui.emit_json(rows)
    for r in rows:
        click.echo(ui.item_line(r, store=r["store"] if (reach or only) else None))
        if r.get("snip"):
            click.echo(f"{'':<{29 if (reach or only) else 16}} {' '.join(r['snip'].split())}")


@command(main, "context", no_args_is_help=True)
@click.argument("target", nargs=-1, required=True)
@click.option("--runs", help="Only items valid for these runs: N or FIRST-LAST.")
@click.option("--budget", default=12000, show_default=True, help="Maximum characters of output.")
@click.option("--reach", default=0, help="Also include neighbor stores up to this many hops away.")
@click.option("--in", "only", multiple=True, help="Also include this neighbor store (repeatable).")
@pass_ctx
def context_cmd(c, target, runs, budget, reach, only):
    """What the KB knows about a subject, an item, or some words; compact markdown for LLMs."""
    from .context import parse_run_window
    from .federated import context
    click.echo(context(c.kb, " ".join(target), parse_run_window(runs), budget, reach, list(only)), nl=False)


@command(rendition_grp, "path", no_args_is_help=True)
@click.argument("ref")
@click.option("--no-fetch", is_flag=True, help="Don't download URL content.")
@pass_ctx
def path_cmd(c, ref, no_fetch):
    """A local file with a rendition's content (ID#NAME), fetched or materialized as needed."""
    from .edit import rendition_path
    click.echo(str(rendition_path(c.kb, ref, fetch=not no_fetch)))


@command(rendition_grp, "verify")
@click.argument("refs", nargs=-1)
@click.option("--record", is_flag=True, help="Store hashes for URL renditions that have none.")
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def verify_cmd(c, refs, record, json_out):
    """Re-fetch URL originals and compare with their recorded sha256."""
    from .content import verify
    kb = c.kb
    recs = [kb.get(r) for r in refs] if refs else list(kb.items.values())
    report = verify(kb, recs, record)
    if json_out:
        return ui.emit_json(report)
    for item in report:
        click.echo(f"{item['result']:<12} {item['ref']}  {item['url']}")
        if item.get("detail"):
            click.echo(f"{'':<13}{item['detail']}")
    if any(i["result"] == "changed" for i in report):
        sys.exit(1)


@command(item_grp, "deps", no_args_is_help=True)
@click.argument("ref")
@click.option("-r", "--reverse", is_flag=True, help="What uses REF, rather than what REF uses.")
@click.option("-t", "--transitive", is_flag=True)
@pass_ctx
def deps_cmd(c, ref, reverse, transitive):
    """Inputs of an item, or (-r) the items built on it."""
    from . import graph
    kb = c.kb
    ident = kb.get(ref).id
    ids = graph.rdeps(kb, ident, transitive) if reverse else graph.deps(kb, ident, transitive)
    for i in ids:
        r = kb.items.get(i)
        click.echo(ui.item_line({"id": i, "kind": r.kind if r else "?", "status": r.data.get("status") if r else None,
                                   "title": r.data.get("title") if r else "(missing)",
                                   "current": kb.is_current(i)}))


@command(item_grp, "graph")
@click.option("--format", "fmt", default="dot", type=click.Choice(["dot", "json"]), show_default=True)
@pass_ctx
def dag_cmd(c, fmt):
    """The derivation and supersession graph (dot | json)."""
    from .graph import dag
    click.echo(dag(c.kb, fmt))


@item_grp.command("schema")
@click.argument("kind", required=False)
@click.option("--markdown", is_flag=True, help="The whole reference as markdown.")
@handle_errors
def schema_cmd(kind, markdown):
    """Without KIND, list kinds; with KIND, print its JSON Schema."""
    from . import schema
    if markdown:
        from .schemadoc import render
        return click.echo(render(), nl=False)
    if not kind:
        click.echo(f"schema revision {schema.REVISION}")
        for k in KINDS:
            click.echo(f"  {k:<12} {schema.show(k).get('description', '')[:100]}")
        click.echo("shared definitions: kb item schema common;  common fields: kb item schema item")
        return
    ui.emit_json(schema.show(kind))


# -- integrity ----------------------------------------------------------------

@command(main, "check")
@click.argument("ids", nargs=-1)
@click.option("--deep", is_flag=True, help="Also re-hash every stored blob.")
@click.option("--hook", is_flag=True, hidden=True)
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def check_cmd(c, ids, deep, hook, json_out):
    """Consistency scan: schema, kinds, stored content, tampering, access, references. Exit 1 on errors.

    Append-only itself is enforced by the database on every write.
    """
    from . import checks
    if hook:
        return _hook_check(c)
    kb = c.kb
    probs = checks.check(kb, [kb.get(i).id for i in ids] or None, deep=deep)
    if json_out:
        ui.emit_json([p.as_dict() for p in probs])
    else:
        ui.problems(probs)
        ui.summary_counts(probs)
    if any(p.level == "error" for p in probs):
        sys.exit(1)


def _hook_check(c):
    from .hooks import run
    code, text = run("check", sys.stdin.read(), c.root)
    if text:
        print(text, file=sys.stderr)
    if code:
        sys.exit(code)


@command(main, "status")
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def status_cmd(c, json_out):
    """The store at a glance: items, inbox, edges, content stores, and what needs attention."""
    from .status import status
    v = status(c.kb, c.context)
    if json_out:
        return ui.emit_json(v)
    ui.status(v)


@command(main, "doctor")
@click.argument("codes", nargs=-1)
@click.option("--fix", is_flag=True, help="Apply fixes: 'auto' ones, and 'ask' ones you confirm.")
@click.option("-y", "--yes", is_flag=True, help="With --fix: apply 'ask' fixes without asking.")
@click.option("--quick", is_flag=True, help="Only the cheap diagnoses (those kb status runs).")
@click.option("--approved", help="The human's approval (agents, when the fix's autonomy is 'ask').")
@click.option("--list", "list_remedies", is_flag=True, help="List the problems kb doctor knows.")
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def doctor_cmd(c, codes, fix, yes, quick, approved, list_remedies, json_out):
    """Find common problems and fix them: automatically where safe, after asking otherwise.

    Each finding is 'auto' (safe; --fix applies it), 'ask' (--fix asks first)
    or 'manual' (advice). Name CODES to run only those diagnoses.

    \b
      kb doctor                 # what is wrong, and how it would be fixed
      kb doctor --fix           # fix it
      kb doctor stale --fix -y  # only stale things, without questions
    """
    from . import doctor
    if list_remedies:
        rows = [{"code": r.code, "title": r.title, "quick": r.quick, "fixes": r.apply is not None}
                for r in doctor.REMEDIES.values()]
        if json_out:
            return ui.emit_json(rows)
        for r in rows:
            click.echo(f"{r['code']:<15} {'quick' if r['quick'] else 'full ':<6} "
                       f"{'fixes' if r['fixes'] else 'advice':<7} {r['title']}")
        return
    kb = c.kb
    found = doctor.diagnose(kb, list(codes) or None, quick)
    if not fix:
        if json_out:
            ui.emit_json([f.as_dict() for f in found])
        elif found:
            ui.findings([f.as_dict() for f in found])
            n = {m: sum(f.mode == m for f in found) for m in doctor.MODES}
            ui.info(f"{len(found)} finding(s): {n['auto']} fixed by kb doctor --fix, {n['ask']} after asking, "
                    f"{n['manual']} need you")
        else:
            ui.info("no problems found")
        sys.exit(1 if found else 0)

    def confirm(f):
        if yes:
            return True
        if not sys.stdin.isatty():
            return False
        return click.confirm(f"{f.summary}\n  fix: {f.fix}\n  apply?", default=False)

    results = doctor.treat(kb, found, confirm, approved)
    if json_out:
        ui.emit_json(results)
    else:
        for r in results:
            mark = {"fixed": "green", "failed": "red", "skipped": "yellow"}.get(r["result"])
            click.echo(click.style(f"{r['result']:<8}", fg=mark) + f"{r['code']:<15} {r['summary']}")
            click.echo(f"{'':<23} {r['detail']}")
        if not results:
            ui.info("no problems found")
    sys.exit(1 if any(r["result"] != "fixed" for r in results) else 0)


@command(main, "lint")
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def lint_cmd(c, json_out):
    """Advice needing judgment: stale things, overlaps, old unverified claims ... (never fails)."""
    from .lint import lint
    probs = lint(c.kb)
    if json_out:
        return ui.emit_json([p.as_dict() for p in probs])
    ui.problems(probs)
    ui.summary_counts(probs)


@command(item_grp, "stale")
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def stale_cmd(c, json_out):
    """Derived renditions, topics and views whose inputs changed."""
    from .graph import stale
    items = stale(c.kb)
    if json_out:
        return ui.emit_json([s.as_dict() for s in items])
    for s in items:
        where = s.id + (f"#{s.rendition}" if s.rendition else "")
        click.echo(f"{s.what:<9} {where}")
        for r in s.reasons:
            click.echo(f"{'':<10}- {r}")


@command(item_grp, "stamp", no_args_is_help=True)
@click.argument("refs", nargs=-1, required=True)
@pass_ctx
def stamp_cmd(c, refs):
    """Pin a topic's inputs by digest after revising it."""
    from .graph import stamp
    for r in refs:
        click.echo(stamp(c.kb, r).id)


@command(store_grp, "reindex")
@pass_ctx
def reindex_cmd(c):
    """Rebuild the store's query tables (search, subjects, runs, references) from the items."""
    n = c.kb.reindex()
    ui.info(f"reindexed {n} items")


@command(store_grp, "embed")
@pass_ctx
def embed_cmd(c):
    """Embed new or changed text for semantic search (needs [embed] configured)."""
    from .embed import embed
    r = embed(c.kb)
    ui.info(f"{r['identity']}: embedded {r['embedded']}, removed {r['removed']}, {r['total']} chunks in all")


# -- views ----------------------------------------------------------------------

@command(view_grp, "build")
@click.argument("refs", nargs=-1)
@click.option("--stale", "only_stale", is_flag=True, help="Build every stale view, inputs first.")
@click.option("--world", is_flag=True, help="Re-extract stale renditions, reindex, build stale views.")
@click.option("--publish", "publish_too", is_flag=True, help="Publish what was built.")
@pass_ctx
def build_cmd(c, refs, only_stale, world, publish_too):
    """Build views: run their tool out of tree and write manifest.json."""
    from . import build, graph
    kb = c.kb
    if world or only_stale:
        if world:
            report = build.world(kb, ui.info, publish_too)
        else:
            stale = [r.id for r in kb.items.values() if r.kind == "view" and build.view_staleness(kb, r)]
            report = {"built": [], "failed": [], "published": []}
            for vid in graph.topo_order(kb, stale):
                try:
                    build.build(kb, vid, ui.info)
                    report["built"].append(vid)
                    if publish_too:
                        report["published"].append(build.publish(kb, vid)["path"])
                except KBError as exc:
                    report["failed"].append({"id": vid, "error": str(exc)})
        ui.emit_json(report)
        if report["failed"]:
            sys.exit(1)
        return
    if not refs:
        raise KBError("give views to build, or --stale / --world")
    for r in refs:
        man = build.build(kb, r, ui.info)
        click.echo(f"{man['view']}: {len(man['outputs'])} output(s) in "
                   f"{(kb.cfg.build_dir / man['view'] / 'out').relative_to(kb.root)}")
        if publish_too:
            click.echo(build.publish(kb, r)["path"])


@command(view_grp, "publish", no_args_is_help=True)
@click.argument("refs", nargs=-1, required=True)
@pass_ctx
def publish_cmd(c, refs):
    """Copy a freshly built view to the publish store as an immutable version."""
    from .build import publish
    for r in refs:
        e = publish(c.kb, r)
        click.echo(e.get("url") or e["path"])


# -- FAIR, migration, tooling --------------------------------------------------

@command(data_grp, "describe", no_args_is_help=True)
@click.argument("targets", nargs=-1, required=True)
@click.option("--schema", "scheme", default="frictionless", show_default=True, type=click.Choice(["frictionless"]),
              help="Description standard: frictionless = a Data Package (datapackage.json).")
@pass_ctx
def data_describe_cmd(c, targets, scheme):
    """Describe tabular data (CSV/TSV): a dataset item (the description is kept as its
    'datapackage' metadata rendition) or files (printed only).

    \b
      kb data describe dat-...
      kb data describe noise.csv gains.tsv
    """
    from . import fair
    files = [Path(t) for t in targets]
    if all(f.is_file() for f in files):
        return ui.emit_json(fair.describe_files(files))
    if len(targets) != 1:
        raise KBError("give one dataset item, or files")
    ui.emit_json(fair.datapackage(c.kb, targets[0]))


@command(data_grp, "export", no_args_is_help=True)
@click.argument("refs", nargs=-1, required=True)
@click.option("--format", "fmt", default="ro-crate", show_default=True, type=click.Choice(["ro-crate"]),
              help="Package format: ro-crate = RO-Crate 1.1.")
@click.option("-o", "--output", type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option("--no-copy", is_flag=True, help="Reference content by URL instead of copying files.")
@pass_ctx
def data_export_cmd(c, refs, fmt, output, no_copy):
    """Package items and their content for use outside kb."""
    from .fair import rocrate
    click.echo(rocrate(c.kb, list(refs), output, copy=not no_copy))


@command(store_grp, "migrate")
@click.option("--dry-run", is_flag=True)
@pass_ctx
def migrate_cmd(c, dry_run):
    """Upgrade every item to this kb's schema version (in one transaction)."""
    from .migrate import migrate
    ids = migrate(c.kb, dry_run=dry_run)
    ui.info(f"{'would migrate' if dry_run else 'migrated'} {len(ids)} item(s)")
    for i in ids:
        click.echo(i)


@command(hook_grp, "check")
@pass_ctx
def hook_check_cmd(c):
    """PostToolUse: validate an edit Claude made in a kb item checkout; problems go back to Claude (exit 2)."""
    _hook_check(c)


@command(hook_grp, "recall")
@pass_ctx
def hook_recall_cmd(c):
    """UserPromptSubmit: print the few items that look relevant to the prompt."""
    from .hooks import run
    _, text = run("recall", sys.stdin.read(), c.root)
    if text:
        click.echo(text)


@command(hook_grp, "session")
@pass_ctx
def hook_session_cmd(c):
    """SessionStart: one short status note about the store."""
    from .hooks import run
    _, text = run("session", sys.stdin.read(), c.root)
    if text:
        click.echo(text)


# -- neighbors and promotion ---------------------------------------------------

@command(edge_grp, "list")
@click.option("--depth", default=2, show_default=True, help="Follow neighbors of neighbors this far.")
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def neighbor_list_cmd(c, depth, json_out):
    """This store's neighborhood."""
    from .federation import describe
    kb = c.kb
    rows = describe(kb, depth)
    if json_out:
        return ui.emit_json(rows)
    click.echo(f"here: {kb.cfg.name} ({kb.cfg.scope}, access {kb.cfg.access}, id {kb.cfg.id})")
    for r in rows:
        avail = "" if r.get("available", True) else "  [not available here: kb edge fetch, or add --path]"
        extra = f"{r.get('scope', '?')}, access {r.get('access', '?')}, {r.get('items', '?')} items"
        click.echo(f"{'  ' * r['distance']}{r['label']:<24} {r['relation']:<5} {extra}{avail}")


@command(edge_grp, "add", no_args_is_help=True)
@click.argument("name")
@click.option("--relation", default="up", type=click.Choice(["up", "peer", "down", "twin"]), show_default=True,
              help="up: broader; down: narrower; peer: sibling; twin: a copy of this same store (kb sync).")
@click.option("--path", help="The store's directory on this machine (needed to write to it).")
@click.option("--url", help="A published read-only snapshot of it (kb edge fetch downloads it).")
@click.option("--exchange", type=click.Path(file_okay=False, exists=True),
              help="Land 'pr': a git clone of the neighbor's bundle exchange repository.")
@click.option("--shared", is_flag=True, help="Declare it in the store's settings, for everyone; default is this machine only (.kb/local.toml).")
@click.option("--land", "--mode", "mode", default="apply", type=click.Choice(["apply", "inbox", "pr"]),
              show_default=True, help="How pushes land there: applied, into its inbox for review, or as a pull request.")
@click.option("--push", "push_form", type=click.Choice(["select", "all", "none"]),
              help="What goes here -> there (default by relation: up select, down all, peer select).")
@click.option("--pull", "pull_form", type=click.Choice(["select", "all", "none"]),
              help="What comes there -> here (default: up all, down select, peer select).")
@click.option("--accept", type=click.Choice(["direct", "review"]), help="Pulled items: applied, or into the inbox for review.")
@click.option("--prefer", type=click.Choice(["ours", "theirs", "newer"]),
              help="Who wins on mutable fields when the same item arrives changed along another path.")
@click.option("--relabel", type=click.Choice(["public", "collaboration", "group", "private"]),
              help="Access level that more restricted items may be relabelled to when pushed here.")
@click.option("--description", default="")
@pass_ctx
def neighbor_add_cmd(c, name, relation, path, url, exchange, shared, mode, push_form, pull_form, accept,
                     prefer, relabel, description):
    """Link a neighbor store, and say how knowledge moves along the edge."""
    from .federation import add
    ui.emit_json(add(c.kb, name, relation, path, url, shared, mode, relabel, description, push=push_form,
                     pull=pull_form, accept=accept, prefer=prefer, exchange=exchange))


@command(edge_grp, "remove", no_args_is_help=True)
@click.argument("name")
@pass_ctx
def neighbor_remove_cmd(c, name):
    """Unlink a neighbor store."""
    from .federation import remove
    for f in remove(c.kb, name):
        click.echo(f"removed from {f}")


@command(edge_grp, "fetch")
@click.argument("names", nargs=-1)
@pass_ctx
def neighbor_fetch_cmd(c, names):
    """Download (or refresh) the published snapshots of URL-only neighbors, into the cache."""
    from .federation import fetch
    for name, what in fetch(c.kb, list(names) or None):
        click.echo(f"{name}: {what}")


@command(edge_grp, "candidates", no_args_is_help=True)
@click.argument("target")
@click.option("--all", "include_all", is_flag=True, help="Include items already pushed and unchanged.")
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def candidates_cmd(c, target, include_all, json_out):
    """Items worth pushing to neighbor TARGET, best first, with reasons and blockers."""
    from .push import candidates
    rows = candidates(c.kb, target, include_all)
    if json_out:
        return ui.emit_json([x.as_dict() for x in rows])
    for x in rows:
        click.echo(f"{x.id:<16} {x.state:<8} {x.score:>3}  {x.title}")
        if x.reasons:
            click.echo(f"{'':<30}{'; '.join(x.reasons)}")
        for b in x.blockers:
            click.echo(click.style(f"{'':<30}blocked: {b}", fg="yellow"))


@command(main, "push", no_args_is_help=True)
@click.argument("refs", nargs=-1)
@click.option("--to", "target", required=True, help="Neighbor to send to.")
@click.option("--deep", is_flag=True, help="Also send everything the items reference, recursively.")
@click.option("--with-deps", "deep", is_flag=True, hidden=True)
@click.option("--nested", default="follow", type=click.Choice(["follow", "inherit"]), show_default=True,
              help="Nested knowledge sets: use their own depth, or this one.")
@click.option("--all", "all_items", is_flag=True, help="Send every new or changed item (the 'all' form).")
@click.option("--access", type=click.Choice(["public", "collaboration", "group", "private"]),
              help="Relabel more restricted items to this level in the copy (a deliberate human decision).")
@click.option("--land", "--mode", "mode", type=click.Choice(["apply", "inbox", "pr"]),
              help="Override how it lands there: applied, its inbox, or a pull request (the edge's default otherwise).")
@click.option("--approved", help="The human's approval, in their words (needed by agents under 'ask' autonomy).")
@click.option("--title", help="Title of the knowledge set made for this transfer.")
@click.option("--message", "-m", help="A note for the receiver (the bundle's note, or the pull request's title).")
@click.option("--dry-run", is_flag=True)
@pass_ctx
def push_cmd(c, refs, target, deep, nested, all_items, access, mode, approved, title, message, dry_run):
    """Send knowledge to a neighbor: items (a new knowledge set is made of them), a ks- set, or --all.

    \b
      kb push --to home obs-... obs-...            # shallow: just these
      kb push --to home top-... --deep             # the topic and everything it cites
      kb push --to group ks-... --land inbox       # an existing set, for review in its inbox
      kb push --to group ks-... --land pr          # ... or as a pull request in its exchange repository
    """
    from .push import push
    ui.emit_json(push(c.kb, target, list(refs), deep=deep, nested=nested, all_items=all_items, access=access,
                      mode=mode, approved=approved, dry_run=dry_run, message=message, title=title))


@command(main, "pull", no_args_is_help=True)
@click.argument("refs", nargs=-1)
@click.option("--from", "source", help="Neighbor to receive from (default: the nearest reachable store having each item).")
@click.option("--dangling", is_flag=True, help="Every item referenced here but not present (from wherever it is).")
@click.option("--deep", is_flag=True, help="Also take everything the items reference, recursively.")
@click.option("--nested", default="follow", type=click.Choice(["follow", "inherit"]), show_default=True)
@click.option("--all", "all_items", is_flag=True, help="Take every new or changed item (the 'all' form; needs --from).")
@click.option("--land", type=click.Choice(["apply", "inbox"]),
              help="Override how it lands here (default from the edge's accept: direct = apply, review = inbox).")
@click.option("--approved", help="The human's approval, in their words (needed by agents under 'ask' autonomy).")
@click.option("--message", "-m")
@click.option("--dry-run", is_flag=True)
@pass_ctx
def pull_cmd(c, refs, source, dangling, deep, nested, all_items, land, approved, message, dry_run):
    """Receive knowledge from a neighbor, or fetch items referenced here from wherever they are.

    \b
      kb pull --from group                         # what the edge's pull form says
      kb pull --from group ks-...                  # a knowledge set
      kb pull obs-... doc-...                      # these, from the nearest store that has them
      kb pull --dangling                           # everything referenced here but missing
    """
    from . import push as p
    if source:
        if dangling:
            raise KBError("--dangling takes items from wherever they are; leave out --from")
        return ui.emit_json(p.pull(c.kb, source, list(refs), deep=deep, nested=nested, all_items=all_items,
                                   approved=approved, dry_run=dry_run, land=land, message=message))
    if not refs and not dangling:
        raise KBError("name items, or --dangling, or a neighbor with --from")
    if all_items or land or message:
        raise KBError("--all, --land and --message go with --from")
    ui.emit_json(p.obtain(c.kb, list(refs), deep=deep, dangling=dangling, approved=approved, dry_run=dry_run))


@command(main, "sync")
@click.argument("names", nargs=-1)
@pass_ctx
def sync_cmd(c, names):
    """Sync twins (copies of this same store): new and changed items both ways, later change wins."""
    from .sync import sync
    ui.emit_json(sync(c.kb, list(names) or None))


@command(edge_grp, "ledger")
@click.option("--limit", default=20, show_default=True)
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def transfers_cmd(c, limit, json_out):
    """The transfer ledger: what this store sent and received."""
    from .transfer import ledger
    rows = ledger(c.kb)[-limit:]
    if json_out:
        return ui.emit_json(rows)
    for e in rows:
        other = e.get("to") or e.get("to_name") if e["dir"] == "out" else e.get("from_name") or e.get("edge")
        acts = {}
        for it in e.get("items", []):
            acts[it["action"]] = acts.get(it["action"], 0) + 1
        arrow = "->" if e["dir"] == "out" else "<-"
        click.echo(f"{e['at'][:19]}  {arrow} {str(other):<12} {e['set']}  {e.get('land', e.get('direction', ''))}  "
                   + ", ".join(f"{n} {a}" for a, n in acts.items()) + f"  ({e.get('approved', '')})")


@command(kset_grp, "new", no_args_is_help=True)
@click.argument("members", nargs=-1, required=True)
@click.option("--deep", is_flag=True, help="Deep: members and everything they reference (default shallow).")
@click.option("--nested", default="follow", type=click.Choice(["follow", "inherit"]), show_default=True)
@click.option("--title")
@click.option("--summary")
@click.option("--purpose", default="transfer", show_default=True)
@pass_ctx
def kset_new_cmd(c, members, deep, nested, title, summary, purpose):
    """Make a knowledge set: a selection to transfer, bundle, or read."""
    from .push import make_kset
    rec = make_kset(c.kb, list(members), "deep" if deep else "shallow", nested, title, summary, purpose,
                    allow_dangling=False)
    click.echo(rec.id)


@command(kset_grp, "expand", no_args_is_help=True)
@click.argument("ref")
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def kset_expand_cmd(c, ref, json_out):
    """The items a knowledge set denotes here, and what it references that is missing."""
    from .transfer import expand
    kb = c.kb
    e = expand(kb.get(ref).data, lambda i: kb.items[i].data if i in kb.items else None)
    out = {"items": e.items, "missing": [{"from": f, "id": i, "member": m} for f, i, m in e.missing]}
    if json_out:
        return ui.emit_json(out)
    from .render import Lookup, _tree_lines, tree_view
    click.echo("\n".join(_tree_lines(tree_view(kb.get(ref).data, Lookup(kb)))))
    click.echo(f"\n{len(e.items)} item(s) denoted ({kb.get(ref).data['depth']}, references followed accordingly)")
    for f, i, m in e.missing:
        click.echo(f"{i:<16} missing ({'member' if m else 'referenced'} from {f}): kb pull {i}")


# -- store settings, content, history ------------------------------------------------

@command(config_grp, "show")
@pass_ctx
def config_show_cmd(c):
    """All store settings (this machine's .kb/local.toml and your config.toml apply on top)."""
    ui.emit_json(c.kb.settings())


@command(config_grp, "get", no_args_is_help=True)
@click.argument("key")
@pass_ctx
def config_get_cmd(c, key):
    """One setting (KEY or KEY.SUB)."""
    head, _, rest = key.partition(".")
    v = c.kb.setting(head)
    for part in rest.split(".") if rest else []:
        v = (v or {}).get(part)
    ui.emit_json(v)


@command(config_grp, "set", no_args_is_help=True)
@click.argument("key")
@click.argument("value")
@pass_ctx
def config_set_cmd(c, key, value):
    """Set KEY (or KEY.SUB) to VALUE (JSON if it parses, else a string); 'null' removes it.

    \b
      kb config set cas.default '{"path": "/eos/group/kb-cas"}'
      kb config set views.publish_path /eos/group/www/kb
      kb config set links.indico https://indico.example.org
    """
    kb = c.kb
    try:
        v = json.loads(value)
    except json.JSONDecodeError:
        v = value
    head, _, rest = key.partition(".")
    if head in ("policy", "autonomy", "neighbors", "policy_lock"):
        raise KBError(f"'{head}' is managed by kb {'edge' if head == 'neighbors' else 'policy'}")
    cur = kb.setting(head)
    if rest:
        cur = dict(cur or {})
        node = cur
        parts = rest.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        if v is None:
            node.pop(parts[-1], None)
        else:
            node[parts[-1]] = v
        v = cur
    kb.set_setting(head, v)
    ui.emit_json(kb.setting(head))


@command(store_grp, "snapshot", no_args_is_help=True)
@click.argument("dest", type=click.Path(path_type=Path))
@pass_ctx
def snapshot_cmd(c, dest):
    """Write a consistent single-file copy of the store, to publish read-only or as a backup.

    A neighbor added with --url reads such a copy (kb edge fetch downloads it).
    """
    click.echo(c.kb.snapshot(dest))


@command(item_grp, "content", no_args_is_help=True)
@click.argument("ref")
@click.argument("name", required=False)
@click.option("--file", "file", type=click.Path(dir_okay=False, allow_dash=True), help="Replace the content with this file (- for stdin).")
@pass_ctx
def content_cmd(c, ref, name, file):
    """Print an item's text content, or replace it (--file) on an editable item (topics, views, drafts)."""
    from .edit import content_name, set_content
    kb = c.kb
    rec = kb.get(ref)
    name = name or content_name(rec)
    if file is None:
        t = rec.text(name)
        if t is None:
            raise KBError(f"{rec.id}#{name} is not text held in the store (kb rendition path {rec.id}#{name})")
        return click.echo(t, nl=False)
    text = sys.stdin.read() if file == "-" else Path(file).read_text()
    click.echo(set_content(kb, rec.id, text, name).id)


@command(item_grp, "checkout", no_args_is_help=True)
@click.argument("ref")
@click.argument("directory", required=False, type=click.Path(file_okay=False, path_type=Path))
@pass_ctx
def checkout_cmd(c, ref, directory):
    """Write an item (item.json) and its content files into a directory for editing."""
    from .edit import checkout
    click.echo(checkout(c.kb, ref, directory))


@command(item_grp, "checkin", no_args_is_help=True)
@click.argument("directory", type=click.Path(file_okay=False, exists=True, path_type=Path))
@click.option("--dry-run", is_flag=True, help="Validate only.")
@pass_ctx
def checkin_cmd(c, directory, dry_run):
    """Apply a checkout's changes (fields within policy, changed/added/removed content files)."""
    from .edit import CHECKOUT_MARK, checkin
    from .items import Store
    mark = json.loads((directory / CHECKOUT_MARK).read_text()) if (directory / CHECKOUT_MARK).exists() else {}
    kb = Store.open(Path(mark["store"])) if mark.get("store") and c.root is None else c.kb
    ui.emit_json(checkin(kb, directory, dry_run))


@command(item_grp, "edit", no_args_is_help=True)
@click.argument("ref")
@click.option("--content", "what", flag_value="content", default=None, help="Edit the content, not the JSON.")
@pass_ctx
def edit_cmd(c, ref, what):
    """Edit an item in $EDITOR (its JSON, or --content), validated and applied within policy."""
    from .edit import edit
    ui.emit_json(edit(c.kb, ref, what or "item"))


@command(main, "log")
@click.argument("ref", required=False)
@click.option("--action", help="Only this kind of event (create, set, transfer-in, publish, review ...).")
@click.option("--limit", default=30, show_default=True)
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def log_cmd(c, ref, action, limit, json_out):
    """History: what happened to an item (or the store), when, by whom, with whose approval."""
    kb = c.kb
    rows = kb.events(kb.get(ref).id if ref else None, action, limit)
    if json_out:
        return ui.emit_json(rows)
    for e in rows:
        appr = f"  (approved: {e['approved']})" if e.get("approved") else ""
        det = e.get("detail") or {}
        brief = ", ".join(f"{k}={v}" for k, v in det.items() if isinstance(v, (str, int)) and k not in ("from",))[:80]
        click.echo(f"{e['at'][:19]}  {e['action']:<13} {e['item'] or '':<16} {e['actor']}{appr}  {brief}")


# -- assets: abstract files and file sets ------------------------------------------------

def _split_ref(ref: str) -> tuple[str, str]:
    a, _, v = ref.partition("@")
    return a, v or "latest"


@command(asset_grp, "new", no_args_is_help=True)
@click.option("--type", "asset_type", required=True, type=click.Choice(["file", "fileset"]))
@click.option("--title", required=True)
@click.option("--summary")
@click.option("--subject", multiple=True)
@click.option("--match", default="exact", type=click.Choice(["exact", "content"]), show_default=True,
              help="Versions differ by paths and content (exact), or by content only (renames are not new versions).")
@pass_ctx
def asset_new_cmd(c, asset_type, title, summary, subject, match):
    """Make an asset: a stable reference to a file or a file set, wherever its bytes are."""
    from . import assets
    fields = {"title": title, "summary": summary or title}
    if subject:
        fields["subject"] = list(subject)
    click.echo(assets.new(c.kb, asset_type, fields, match).id)


@command(asset_grp, "bind", no_args_is_help=True)
@click.argument("ref")
@click.argument("name")
@click.option("--source", "source_type", required=True, help="Source type (kb source types lists them): fs, url, datalad, kb ...")
@click.option("--locator", help="Source-specific identifiers as JSON, e.g. '{\"root\": \"eos-group\", \"path\": \"talks/x\"}'.")
@click.option("--set", "sets", multiple=True, help="Locator field key=value (repeatable), instead of --locator.")
@click.option("--versioning", default="none", type=click.Choice(["none", "native", "pattern", "map"]), show_default=True,
              help="How the source's versions relate to the asset's.")
@click.option("--pattern", help="With pattern versioning: where versions are, e.g. '{version}' or 'v{version}/slides.pdf'.")
@click.option("--map", "maps", multiple=True, help="NATIVE=LABEL (repeatable): the source's version id to the asset's label.")
@click.option("--priority", type=int, help="Lower is tried first.")
@click.option("--note")
@pass_ctx
def asset_bind_cmd(c, ref, name, source_type, locator, sets, versioning, pattern, maps, priority, note):
    """Say that source NAME can supply asset REF, and how its versions map.

    \b
      kb asset bind as-... eos --source fs --set root=eos-group --set path=talks/2026-10-28
      kb asset bind as-... history --source fs --set root=/data/slides --versioning pattern --pattern 'v{version}'
      kb asset bind as-... indico --source url --set url=https://indico.example/.../slides.pdf
    """
    from . import assets
    from .authoring import parse_set
    loc = json.loads(locator) if locator else {}
    loc.update(parse_set(sets))
    mapping = dict(m.split("=", 1) for m in maps) if maps else None
    rec = assets.bind(c.kb, ref, name, source_type, loc, versioning, pattern, mapping, priority, note)
    click.echo(f"{rec.id}: bound {name}")


@command(asset_grp, "unbind", no_args_is_help=True)
@click.argument("ref")
@click.argument("name")
@pass_ctx
def asset_unbind_cmd(c, ref, name):
    """Remove a binding (registered versions stay)."""
    from . import assets
    click.echo(assets.unbind(c.kb, ref, name).id)


@command(asset_grp, "scan", no_args_is_help=True)
@click.argument("ref")
@click.option("--binding", "only", multiple=True, help="Only these bindings.")
@click.option("--no-remote", is_flag=True, help="Don't contact remote sources (URLs ...).")
@click.option("--register", "do_register", is_flag=True, help="Register what is new.")
@click.option("--keep", default="auto", type=click.Choice(["auto", "yes", "no"]), show_default=True,
              help="Copy registered content into the store (auto: when it came from an unversioned source).")
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def asset_scan_cmd(c, ref, only, no_remote, do_register, keep, json_out):
    """What each source offers, related to the asset's versions: known, new, conflict, unreachable."""
    from . import assets
    kb = c.kb
    seen = assets.scan(kb, ref, list(only) or None, remote=not no_remote)
    rows = [s.as_dict() for s in seen]
    done = assets.register(kb, ref, seen, keep=keep) if do_register else None
    if json_out:
        return ui.emit_json({"offers": rows, "registered": done})
    for r in rows:
        ver = f" = {r['version']}" if r["version"] else ""
        nat = f" [{r['native']}]" if r["native"] else ""
        click.echo(f"{r['binding']:<12} {r['status']:<11}{ver:<8} {r['files']:>4} file(s){nat}  {r['where']}")
        if r["detail"]:
            click.echo(f"{'':<14}{r['detail']}")
    if done:
        for d in done:
            click.echo(f"registered {d['label']} ({d['files']} file(s) from {d['from']}" + (", kept in the store" if d.get("kept") else "") + ")")


@command(asset_grp, "add", no_args_is_help=True)
@click.argument("ref")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--label", help="Version label (default: the next vN).")
@click.option("--note")
@pass_ctx
def asset_add_cmd(c, ref, path, label, note):
    """Register a file (or a directory, for a file set) as a new version, kept in the store."""
    from . import assets
    d = assets.add_version(c.kb, ref, path, label, note)
    click.echo(f"{d['label']}: {d['files']} file(s)")


@command(asset_grp, "versions", no_args_is_help=True)
@click.argument("ref")
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def asset_versions_cmd(c, ref, json_out):
    """The asset's registered versions, and where each was seen."""
    from . import assets
    from .render import size, when
    a = assets.get_asset(c.kb, ref)
    vs = a.data.get("versions", [])
    if json_out:
        return ui.emit_json(vs)
    for v in vs:
        total = sum(f.get("size") or 0 for f in v.get("files", []))
        seen = ", ".join(sorted({s["binding"] + (f"[{s['native']}]" if s.get("native") else "") for s in v.get("sightings", [])}))
        click.echo(f"{v['label']:<10} {len(v.get('files', [])):>4} file(s) {size(total):>10}  "
                   f"{when(v.get('registered')):<22} seen in: {seen or '-'}")


@command(asset_grp, "resolve", no_args_is_help=True)
@click.argument("ref")
@click.option("--path", "member", help="One file of a file set.")
@click.option("--to", "dest", type=click.Path(file_okay=False, path_type=Path), help="Copy the files here.")
@click.option("--no-remote", is_flag=True)
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def asset_resolve_cmd(c, ref, member, dest, no_remote, json_out):
    """One well-defined, verified version: REF[@VERSION] (VERSION: a label, latest, current, digest:SHA)."""
    from . import assets
    a, v = _split_ref(ref)
    res = assets.resolve(c.kb, a, v, member, remote=not no_remote)
    if dest:
        assets.materialize(res, dest)
    if json_out:
        return ui.emit_json(res.as_dict())
    click.echo(f"{res.asset} {res.version or '(unregistered)'} from {res.binding} ({res.where})")
    for rel, p in res.files.items():
        click.echo(f"  {rel}  ->  {dest / rel if dest else p}")
    for n in res.notes:
        click.echo(f"  note: {n}")


@command(source_grp, "types")
@click.option("--json", "json_out", is_flag=True)
def source_types_cmd(json_out):
    """Kinds of data source: location sources (where a rendition's bytes are) and asset sources (what bindings use)."""
    from .assets import sources_table as asset_sources
    from .locations import sources_table as location_sources
    rows = location_sources() + asset_sources()
    if json_out:
        return ui.emit_json(rows)
    for r in rows:
        state = r["status"] if r["available"] or r["status"] != "implemented" else "unavailable"
        click.echo(f"{r['interface']:<9} {r['type']:<8} {state:<12} {r['what']}"
                   + (f"  locator {r['locator']}" if r.get("locator") else ""))
        if r.get("needs"):
            click.echo(f"{'':<31}needs: {r['needs']}")


def _ask_location(rec, suggestion):
    """Ask where a new DataLad clone goes (only when a person is at the terminal)."""
    click.echo(f"DataLad dataset {rec.data.get('title')} ({rec.id}) is not on this machine.", err=True)
    answer = click.prompt("Clone it to (empty: don't)", default=str(suggestion), err=True).strip()
    return Path(answer).expanduser() if answer else None


@command(source_grp, "list")
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def source_list_cmd(c, json_out):
    """Source items in this store (DataLad datasets ...), and whether each is on this machine."""
    from .sources import report_all
    rows = report_all(c.kb)
    if json_out:
        return ui.emit_json(rows)
    for r in rows:
        click.echo(f"{r['id']:<16} {r['type']:<8} {r['title']}")
        click.echo(f"{'':<16} {'here: ' + r['here'] if r['here'] else 'not on this machine'}; "
                   f"used by {len(r['used_by'])} item(s)" + (f"; same as {', '.join(r['same_as'])}" if r["same_as"] else ""))
    if not rows:
        ui.info("no sources yet (kb source add PATH|URL)")


@command(source_grp, "add", no_args_is_help=True)
@click.argument("targets", nargs=-1, required=True)
@click.option("--type", "source_type", help="The kind of source (kb source types); default: guessed from TARGETS.")
@click.option("--just-guess", is_flag=True, help="Only say which type would be used (or why none fits); add nothing.")
@click.option("--name", help="Identity for a source without one of its own (a filesystem tree; default: its directory name).")
@click.option("--title")
@click.option("--summary")
@click.option("--no-check", is_flag=True, help="Don't check that the TARGETS name one and the same source.")
@pass_ctx
def source_add_cmd(c, targets, source_type, just_guess, name, title, summary, no_check):
    """Describe a source (local paths and/or URLs naming it) as a source item, so items can refer to it.

    The type is guessed from TARGETS unless --type says: datalad (a DataLad
    dataset), git (a git repository without DataLad), filesystem (a plain
    directory: only with --type filesystem). Adding a source this store
    already describes gives it the new URLs and paths instead.

    \b
      kb source add ~/data/tpc-calib                     # a DataLad dataset you have (its remotes are recorded)
      kb source add ~/data/tpc-calib https://github.com/org/tpc-calib.git   # a clone and where it is shared
      kb source add https://github.com/org/tpc-calib.git  # cloned into sources.datalad.dir to learn its ID
      kb source add --type filesystem /eos/group/talks --name eos-talks   # a directory tree (never guessed)
      kb source add --just-guess https://example.org/x   # what kind of source is this?
    """
    from . import sources
    if just_guess:
        g = sources.guess(list(targets), only=source_type)
        if g.type:
            click.echo(g.type)
            ui.info(g.explain())
            for w in g.warnings:
                ui.info(f"warning: {w}")
            return
        click.echo(f"cannot guess: {g.explain()}")
        sys.exit(1)
    rec = sources.add(c.kb, list(targets), source_type, title, summary, check=not no_check, name=name)
    click.echo(rec.id)
    ui.info(f"{rec.data['title']}: {rec.data['source_type']} source {rec.data['source_id']}")


@command(source_grp, "show", no_args_is_help=True)
@click.argument("ref")
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def source_show_cmd(c, ref, json_out):
    """One source: its identity, where it can be had from, its clone here, and the items using it."""
    from . import sources
    rec = c.kb.get(ref)
    if rec.kind != "source":
        raise KBError(f"{rec.id} is a {rec.kind}, not a source (kb source list)")
    r = sources.report(c.kb, rec)
    if json_out:
        return ui.emit_json(r)
    click.echo(f"{r['id']}  {r['title']}  ({r['type']} {r['source_id']})")
    for u in r["urls"]:
        click.echo(f"  url    {u}")
    for p in r["paths"]:
        click.echo(f"  path   {p['host']}:{p['path']}")
    hint = f"kb source clone {r['id']}" if r["type"] == "datalad" else "mount it, or set filesystems." + r["source_id"] + ".path"
    click.echo(f"  here   {r['here'] or 'not on this machine (' + hint + ')'}")
    if r["same_as"]:
        click.echo(f"  same source as {', '.join(r['same_as'])} (one local copy serves them all)")
    click.echo(f"  used by {len(r['used_by'])} item(s)" + (": " + " ".join(r["used_by"][:8]) if r["used_by"] else ""))


@command(source_grp, "clone", no_args_is_help=True)
@click.argument("ref")
@click.option("--to", "dest", type=click.Path(file_okay=False, path_type=Path),
              help="Where (default: the existing clone, or sources.datalad.dir, or ask if sources.datalad.new = ask).")
@pass_ctx
def source_clone_cmd(c, ref, dest):
    """Make sure this machine has a clone of source REF (content is still fetched only when needed)."""
    from . import datasets
    path, made = datasets.clone(c.kb, ref, dest)
    click.echo(path)
    ui.info("cloned" if made else "already here")


@command(source_grp, "link", no_args_is_help=True)
@click.argument("ref")
@click.argument("path", type=click.Path(file_okay=False, exists=True, path_type=Path))
@pass_ctx
def source_link_cmd(c, ref, path):
    """Tell kb that PATH is a clone of source REF (checked by dataset ID); every store of yours will use it."""
    from . import datasets
    click.echo(datasets.link(c.kb, ref, path))


# -- bundles, inbox, review ----------------------------------------------------------

@command(bundle_grp, "make", no_args_is_help=True)
@click.argument("refs", nargs=-1, required=True)
@click.option("-o", "--output", type=click.Path(path_type=Path), default=".", show_default=True,
              help="File or directory to write.")
@click.option("--deep", is_flag=True, help="When naming items: also everything they reference.")
@click.option("--note", help="A note for the reviewers.")
@pass_ctx
def bundle_make_cmd(c, refs, output, deep, note):
    """Bundle a knowledge set (ks-...), or a new set made of the named items."""
    from . import bundle as bundles
    from .push import _selection
    kb = c.kb
    ks = _selection(kb, list(refs), "deep" if deep else "shallow", "follow", None)
    b = bundles.make(kb, ks.id, note=note)
    click.echo(bundles.write(b, output))
    ui.info(f"bundle {b['id'][:12]}: set {ks.id}, {len(b['items'])} item(s)")


def _load_bundle(c, what: str):
    """A bundle file, or an inbox entry (bundle id prefix or set id)."""
    from . import bundle as bundles
    p = Path(what)
    if p.exists():
        return bundles.read(p)
    return bundles.bundle_of(c.kb, what)


@command(bundle_grp, "show", no_args_is_help=True)
@click.argument("what")
@click.option("--format", "fmt", default="text", type=click.Choice(["text", "md", "json"]), show_default=True)
@click.option("--against/--no-against", default=True, help="Compare with this store (what accepting would do).")
@click.option("--depth", default=4, show_default=True)
@pass_ctx
def bundle_show_cmd(c, what, fmt, against, depth):
    """Render a bundle (file or inbox entry) for review: provenance, plan, tree, items with excerpts and links."""
    from . import render as r
    b = _load_bundle(c, what)
    store_kb = None
    if against:
        try:
            store_kb = c.kb
        except KBError:
            store_kb = None
    v = r.bundle_view(b, store_kb, depth)
    click.echo(r.to_json(v) if fmt == "json" else r.bundle_text(v, md=fmt == "md"), nl=fmt == "json")


@command(bundle_grp, "check", no_args_is_help=True)
@click.argument("what")
@pass_ctx
def bundle_check_cmd(c, what):
    """Verify a bundle and say what applying it here would do (exit 1 if it can't be applied cleanly)."""
    from . import bundle as bundles
    out = bundles.check(c.kb, _load_bundle(c, what))
    ui.emit_json(out)
    if not out["ok"]:
        sys.exit(1)


@command(bundle_grp, "apply", no_args_is_help=True)
@click.argument("file", type=click.Path(dir_okay=False, exists=True, path_type=Path))
@click.option("--access", type=click.Choice(["public", "collaboration", "group", "private"]))
@click.option("--approved")
@pass_ctx
def bundle_apply_cmd(c, file, access, approved):
    """Apply a bundle file directly (skipping the inbox; agents: per 'accept' autonomy)."""
    from . import bundle as bundles
    kb = c.kb
    who = bundles._guard(kb, approved)
    ui.emit_json(bundles.apply(kb, bundles.read(file), approved=who, access=access))


@command(inbox_grp, "list")
@click.option("--status", default="pending", type=click.Choice(["pending", "accepted", "rejected", "all"]),
              show_default=True)
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def inbox_list_cmd(c, status, json_out):
    """Inbox entries (pending ones unless --status says otherwise)."""
    from . import bundle as bundles
    rows = bundles.entries(c.kb, status)
    if json_out:
        return ui.emit_json([e.as_dict() for e in rows])
    for e in rows:
        verdicts = ", ".join(f"{r['reviewer_kind']}:{r['verdict']}" for r in e.reviews) or "no reviews"
        click.echo(f"{e.bundle[:12]}  {e.status:<9} {e.set_id:<16} from {e.sender_name or e.sender}  "
                   f"{e.received_at[:10]}  ({verdicts})")


@command(inbox_grp, "receive", no_args_is_help=True)
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--accept", "accept_all", is_flag=True,
              help="Accept them too (they were reviewed elsewhere, e.g. by a merged pull request).")
@click.option("--approved", help="The human's approval (agents under 'ask' accept autonomy).")
@pass_ctx
def inbox_receive_cmd(c, paths, accept_all, approved):
    """Put bundle files into the inbox for review; for a directory, every bundle under it.

    A directory is typically a clone of an exchange repository after merges.
    """
    from . import bundle as bundles
    for p in paths:
        if p.is_dir():
            ui.emit_json(bundles.import_dir(c.kb, p, accept_all, approved))
            continue
        e = bundles.receive(c.kb, bundles.read(p), note=f"received from {p}")
        if accept_all:
            bundles.accept(c.kb, e.bundle, approved)
            e = bundles.get(c.kb, e.bundle)
        click.echo(f"{e.bundle[:12]} {e.status}")


@command(inbox_grp, "show", no_args_is_help=True)
@click.argument("ref")
@click.option("--format", "fmt", default="text", type=click.Choice(["text", "md", "json"]), show_default=True)
@pass_ctx
def review_show_cmd(c, ref, fmt):
    """An inbox entry: the bundle rendered against this store, then the reviews so far."""
    from . import bundle as bundles
    from . import render as r
    kb = c.kb
    e = bundles.get(kb, ref)
    v = r.bundle_view(bundles.bundle_of(kb, ref), kb)
    v["inbox"] = e.as_dict()
    if fmt == "json":
        return click.echo(r.to_json(v))
    click.echo(r.bundle_text(v, md=fmt == "md"))
    click.echo(("## " if fmt == "md" else "") + f"Reviews ({e.status})")
    for rv in e.reviews:
        click.echo(f"- {rv['at'][:16]} {rv['reviewer']} ({rv['reviewer_kind']}): {rv['verdict']}. {rv['text'] or ''}")
    if not e.reviews:
        click.echo("- none yet")


@command(inbox_grp, "review", no_args_is_help=True)
@click.argument("ref")
@click.option("--verdict", required=True, type=click.Choice(["accept", "reject", "changes", "comment"]))
@click.option("--text", required=True, help="Your reasoning, for the person who decides.")
@click.option("--reviewer", help="Who (default: you, or 'claude' when run by an agent).")
@pass_ctx
def inbox_review_cmd(c, ref, verdict, text, reviewer):
    """Record a review (people and LLMs). An LLM's verdict is advice; deciding follows the 'accept' autonomy."""
    from . import bundle as bundles
    e = bundles.review(c.kb, ref, verdict, text, reviewer)
    click.echo(f"{e.bundle[:12]}: {len(e.reviews)} review(s)")


@command(inbox_grp, "accept", no_args_is_help=True)
@click.argument("ref")
@click.option("--note")
@click.option("--access", type=click.Choice(["public", "collaboration", "group", "private"]),
              help="Relabel more restricted items to this level (a deliberate decision).")
@click.option("--approved", help="The human's approval (agents under 'ask' accept autonomy).")
@pass_ctx
def review_accept_cmd(c, ref, note, access, approved):
    """Accept an entry: apply its bundle to this store in one transaction."""
    from . import bundle as bundles
    ui.emit_json(bundles.accept(c.kb, ref, approved, note, access))


@command(inbox_grp, "reject", no_args_is_help=True)
@click.argument("ref")
@click.option("--reason", required=True)
@click.option("--approved")
@pass_ctx
def review_reject_cmd(c, ref, reason, approved):
    """Reject an entry, saying why (kept with the entry and in the store's history)."""
    from . import bundle as bundles
    e = bundles.reject(c.kb, ref, reason, approved)
    click.echo(f"{e.bundle[:12]} {e.status}")


# -- git, forges, secrets -----------------------------------------------------------

def _repo_arg(fn):
    return click.option("--repo", type=click.Path(file_okay=False, exists=True, path_type=Path), default=".",
                        show_default=True, help="The repository.")(fn)


@command(exchange_hooks_grp, "install")
@click.argument("names", nargs=-1)
@_repo_arg
def git_hooks_install_cmd(names, repo):
    """Add the kb block to hook scripts (existing hooks are kept)."""
    from .githooks import DEFAULT, install
    for p in install(repo, list(names) or DEFAULT):
        click.echo(f"installed {p}")


@command(exchange_hooks_grp, "remove")
@click.argument("names", nargs=-1)
@_repo_arg
def git_hooks_remove_cmd(names, repo):
    """Remove the kb block from hook scripts."""
    from .githooks import HOOKS, remove
    for p in remove(repo, list(names) or HOOKS):
        click.echo(f"removed kb from {p}")


@command(exchange_hooks_grp, "status")
@_repo_arg
def git_hooks_status_cmd(repo):
    """Which git hooks run kb."""
    from .githooks import status
    for name, st in status(repo).items():
        click.echo(f"{name:<12} {st}")


@command(exchange_grp, "init", no_args_is_help=True)
@click.argument("path", type=click.Path(file_okay=False, path_type=Path))
def exchange_init_cmd(path):
    """Create an exchange repository (git, README, hooks) for a store's incoming bundles."""
    from .githooks import init_exchange
    for p in init_exchange(path):
        click.echo(f"created {p}")


def _git_hook(which, repo):
    import os
    for v in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX"):
        if which != "pre-commit" or v != "GIT_INDEX_FILE":
            os.environ.pop(v, None)    # set by git for hooks; would confuse kb's own git calls
    from . import githooks
    repo = Path(repo).resolve()
    if which == "pre-commit":
        code, text = githooks.run_pre_commit(repo)
    elif which == "pre-push":
        code, text = githooks.run_pre_push(repo, sys.stdin.read())
    else:
        code, text = githooks.run_post_merge(repo)
    if text:
        print(text, file=sys.stderr)
    sys.exit(code)


@command(hook_grp, "git-pre-commit")
@click.option("--repo", default=".")
def hook_git_pre_commit_cmd(repo):
    """git pre-commit: staged bundles verify; existing bundles are untouched."""
    _git_hook("pre-commit", repo)


@command(hook_grp, "git-pre-push")
@click.option("--repo", default=".")
@click.argument("args", nargs=-1)
def hook_git_pre_push_cmd(repo, args):
    """git pre-push: the same for every commit being pushed."""
    _git_hook("pre-push", repo)


@command(hook_grp, "git-post-merge")
@click.option("--repo", default=".")
@click.argument("args", nargs=-1)
def hook_git_post_merge_cmd(repo, args):
    """git post-merge: a one-line notice about bundles to import."""
    _git_hook("post-merge", repo)


@command(forge_grp, "list")
@pass_ctx
def forge_list_cmd(c):
    """Configured forges and where their tokens come from."""
    from . import forge, secrets
    for f in forge.configured(c.kb.cfg.raw):
        src = secrets.parse(f.token).describe() if f.token is not None else "env (" + ", ".join(
            s.split(":", 1)[1] for s in f.token_specs() if s.startswith("env:")) + ")"
        click.echo(f"{f.name:<12} {f.type:<8} {f.host:<24} token from {src}")


@command(forge_grp, "add", no_args_is_help=True)
@click.argument("name")
@click.option("--type", "ftype", required=True, type=click.Choice(["github", "gitea", "forgejo", "gogs"]))
@click.option("--host", required=True)
@click.option("--api", help="API base URL (default: https://HOST/api/v1, or api.github.com).")
@click.option("--token-from", help="Secret spec, e.g. 'cmd:pass show kb/forge/NAME' (recommended) or file:PATH.")
def forge_add_cmd(name, ftype, host, api, token_from):
    """Add a forge to your user config (tokens are personal; never in a store)."""
    from .forge import add_config
    try:
        p = add_config(name, ftype, host, api, token_from)
    except KBError as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(f"added [forges.{name}] to {p}; next: kb forge auth {name}")


@command(forge_grp, "auth", no_args_is_help=True)
@click.argument("name")
@click.option("--token-from", help="Try this secret spec instead of the configured one.")
@pass_ctx
def forge_auth_cmd(c, name, token_from):
    """Explain how to get and store a token for forge NAME, then test it."""
    from . import forge, secrets
    fc = forge.by_name(c.kb.cfg.raw, name)
    click.echo(f"Forge {fc.name}: {fc.type} at {fc.host}")
    click.echo(f"1. Create a token at {fc.token_page}")
    click.echo(f"   with {fc.scopes}.")
    click.echo(f"2. Store it in pass (recommended):  pass insert kb/forge/{fc.name}")
    click.echo(f"   and point kb at it:  token = {{command = \"pass show\", id = \"kb/forge/{fc.name}\"}}")
    click.echo(f"   in [forges.{fc.name}] of {forge.user_config_path()}")
    click.echo("   (or a file readable only by you: chmod 600 FILE; token = {file = \"FILE\"};")
    click.echo(f"    or the environment: KB_FORGE_TOKEN_{fc.name.upper()} / KB_FORGE_TOKEN)")
    click.echo("3. Testing...")
    try:
        cl = forge.client(fc, token_from)
        click.echo(f"   token works: logged in as {cl.whoami()} ({secrets.mask(cl.token)})")
    except KBError as exc:
        click.echo(f"   not yet: {exc}")
        sys.exit(1)


def _repo(c, forge_name, repo, remote):
    from . import forge
    r = forge.repo_for(c.kb.cfg.raw, c.kb.root, remote, forge_name, repo)
    return forge.client(r.forge), r


def _repo_options(fn):
    for o in reversed([
        click.option("--forge", "forge_name", help="Forge name (default: matched from the git remote's host)."),
        click.option("--repo", help="OWNER/NAME (default: from the git remote)."),
        click.option("--remote", default="origin", show_default=True),
    ]):
        fn = o(fn)
    return fn


@command(pr_grp, "create", no_args_is_help=True)
@click.option("--head", required=True, help="Branch with the changes (push it first, or use kb push --land pr).")
@click.option("--base", help="Target branch (default: the current branch).")
@click.option("--title", required=True)
@click.option("--body", default="")
@_repo_options
@pass_ctx
def pr_create_cmd(c, head, base, title, body, forge_name, repo, remote):
    """Open a pull request."""
    from . import gitutil
    cl, r = _repo(c, forge_name, repo, remote)
    ui.emit_json(cl.pr_create(r.owner, r.name, head, base or gitutil.current_branch(c.kb.root), title, body))


@command(pr_grp, "list")
@click.option("--state", default="open", type=click.Choice(["open", "closed", "all"]), show_default=True)
@click.option("--json", "json_out", is_flag=True)
@_repo_options
@pass_ctx
def pr_list_cmd(c, state, json_out, forge_name, repo, remote):
    """List pull requests."""
    cl, r = _repo(c, forge_name, repo, remote)
    rows = cl.pr_list(r.owner, r.name, state)
    if json_out:
        return ui.emit_json(rows)
    for p in rows:
        click.echo(f"#{p['number']:<5} {p['state']:<7} {p['head']} -> {p['base']}  {p['title']}  ({p['author']})")


@command(pr_grp, "show", no_args_is_help=True)
@click.argument("number", type=int)
@_repo_options
@pass_ctx
def pr_show_cmd(c, number, forge_name, repo, remote):
    """One pull request."""
    cl, r = _repo(c, forge_name, repo, remote)
    ui.emit_json(cl.pr_get(r.owner, r.name, number))


@command(pr_grp, "merge", no_args_is_help=True)
@click.argument("number", type=int)
@click.option("--method", default="merge", type=click.Choice(["merge", "squash", "rebase"]), show_default=True)
@click.option("--message")
@click.option("--approved", help="The human's approval (agents under 'ask' merge autonomy).")
@_repo_options
@pass_ctx
def pr_merge_cmd(c, number, method, message, approved, forge_name, repo, remote):
    """Merge a pull request (agents: per 'merge' autonomy)."""
    _guard_forge(c.kb, "merge", approved)
    cl, r = _repo(c, forge_name, repo, remote)
    ui.emit_json(cl.pr_merge(r.owner, r.name, number, method, message))


@command(pr_grp, "close", no_args_is_help=True)
@click.argument("number", type=int)
@click.option("--approved")
@_repo_options
@pass_ctx
def pr_close_cmd(c, number, approved, forge_name, repo, remote):
    """Close a pull request without merging (agents: per 'merge' autonomy)."""
    _guard_forge(c.kb, "merge", approved)
    cl, r = _repo(c, forge_name, repo, remote)
    ui.emit_json(cl.pr_close(r.owner, r.name, number))


@command(pr_grp, "comment", no_args_is_help=True)
@click.argument("number", type=int)
@click.option("--body", required=True)
@_repo_options
@pass_ctx
def pr_comment_cmd(c, number, body, forge_name, repo, remote):
    """Comment on a pull request."""
    cl, r = _repo(c, forge_name, repo, remote)
    ui.emit_json(cl.pr_comment(r.owner, r.name, number, body))


def _guard_forge(kb, action, approved):
    if not kb.cfg.is_agent:
        return
    level = kb.cfg.autonomy_for(action)
    if level == "never":
        raise KBError(f"autonomy: {action} of pull requests is reserved for the human")
    if level == "ask" and not approved:
        raise KBError(f"autonomy: {action} needs the human's approval; ask, then pass --approved \"<their words>\"")


@secret_grp.command("test", no_args_is_help=True)
@click.argument("spec")
@handle_errors
def secret_test_cmd(spec):
    """Resolve SPEC and show where it came from, masked: cmd:pass show NAME | file:PATH | env:VAR | value:..."""
    from . import secrets
    s = secrets.parse(spec)
    click.echo(f"{s.describe()} -> {secrets.mask(secrets.resolve(s))}")


# -- policy ---------------------------------------------------------------------

@command(policy_grp, "list")
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def policy_list_cmd(c, json_out):
    """Policies available here (this store, reachable stores, builtins); * = selected."""
    from . import policy
    kb = c.kb
    sel = {policy.parse_spec(x)[0]: x for x in policy.settings(kb).get("policy", {}).get("use", [])}
    rows = [{"name": e.name, "version": e.version, "type": e.type, "stage": e.stage, "source": e.source,
             "selected": e.name in sel, "summary": e.summary} for e in policy.available(kb)]
    if json_out:
        return ui.emit_json(rows)
    for r in rows:
        mark = "*" if r["selected"] else " "
        click.echo(f"{mark} {r['name']:<20} v{r['version']:<3} {r['type']:<12} {r['stage']:<10} {r['source']:<10} {r['summary']}")


@command(policy_grp, "show", no_args_is_help=True)
@click.argument("spec")
@pass_ctx
def policy_show_cmd(c, spec):
    """A policy's metadata and content (NAME, NAME@N or NAME@draft)."""
    from .policy import resolve
    e = resolve(c.kb, spec)
    click.echo(f"# {e.spec} ({e.type}, {e.stage}, from {e.source})" + (f", based on {e.based_on}" if e.based_on else ""))
    if e.summary:
        click.echo(e.summary)
    click.echo("")
    click.echo(e.content, nl=False)


def _read_content(file):
    if file is None:
        return None
    return sys.stdin.read() if str(file) == "-" else Path(file).read_text()


@command(policy_grp, "new", no_args_is_help=True)
@click.argument("name")
@click.option("--type", "ptype", required=True, type=click.Choice(["instructions", "skill", "hook", "settings"]))
@click.option("--file", "file", required=True, type=click.Path(dir_okay=False, allow_dash=True))
@click.option("--title")
@click.option("--summary")
@click.option("--order", type=int, help="Position of an instructions snippet (lower first).")
@click.option("--applies-to", multiple=True, help="Store scope it is meant for (repeatable).")
@pass_ctx
def policy_new_cmd(c, name, ptype, file, title, summary, order, applies_to):
    """Create a draft policy in this store."""
    from .policy import new
    rec = new(c.kb, name, ptype, _read_content(file), title, summary, order, applies_to)
    click.echo(f"{rec.id} {name}@{rec.data['version']} (draft)")


@command(policy_grp, "edit", no_args_is_help=True)
@click.argument("name")
@click.option("--file", "file", required=True, type=click.Path(dir_okay=False, allow_dash=True))
@pass_ctx
def policy_edit_cmd(c, name, file):
    """Replace the content of this store's draft of NAME."""
    from .policy import edit
    rec = edit(c.kb, name, _read_content(file))
    click.echo(f"{rec.id} {name}@{rec.data['version']} (draft)")


@command(policy_grp, "revise", no_args_is_help=True)
@click.argument("name")
@click.option("--file", "file", type=click.Path(dir_okay=False, allow_dash=True), help="New content (default: copy current).")
@pass_ctx
def policy_revise_cmd(c, name, file):
    """Start the next version as a draft here (forks builtin or neighbor policies)."""
    from .policy import revise
    rec = revise(c.kb, name, _read_content(file))
    click.echo(f"{rec.id} {name}@{rec.data['version']} (draft; edit, then kb policy release {name})")


@command(policy_grp, "release", no_args_is_help=True)
@click.argument("name")
@click.option("--approved", help="The human's approval (agents under 'ask' policy autonomy).")
@pass_ctx
def policy_release_cmd(c, name, approved):
    """Freeze this store's draft of NAME as a released version."""
    from .policy import release
    rec = release(c.kb, name, approved)
    click.echo(f"{rec.id} {name}@{rec.data['version']} released")


@command(policy_grp, "deprecate", no_args_is_help=True)
@click.argument("name")
@click.option("--approved")
@pass_ctx
def policy_deprecate_cmd(c, name, approved):
    """Mark the latest released version of NAME here as deprecated."""
    from .policy import deprecate
    rec = deprecate(c.kb, name, approved)
    click.echo(f"{rec.id} {name}@{rec.data['version']} deprecated")


@command(policy_grp, "use", no_args_is_help=True)
@click.argument("specs", nargs=-1, required=True)
@click.option("--approved")
@pass_ctx
def policy_use_cmd(c, specs, approved):
    """Select policies (NAME follows the latest release; NAME@N pins). Then kb policy enact."""
    from .policy import use
    click.echo(" ".join(use(c.kb, list(specs), approved)))


@command(policy_grp, "drop", no_args_is_help=True)
@click.argument("names", nargs=-1, required=True)
@click.option("--approved")
@pass_ctx
def policy_drop_cmd(c, names, approved):
    """Deselect policies. Then kb policy enact."""
    from .policy import drop
    click.echo(" ".join(drop(c.kb, list(names), approved)))


@command(policy_grp, "enact")
@click.option("--target", help="Override the target: a directory, or 'user' for ~/.claude.")
@click.option("--dry-run", is_flag=True, help="Show what would change.")
@pass_ctx
def policy_enact_cmd(c, target, dry_run):
    """Write the selected policies: CLAUDE.md block, skills, hooks; record them in policy.lock.json."""
    from .policy import enact
    r = enact(c.kb, target, dry_run)
    click.echo(f"target: {r['target']}")
    click.echo("policies: " + ", ".join(r["policies"]))
    for ch in r["changes"]:
        click.echo(f"{ch['action']:<7} {ch['path']}")
        if dry_run and ch["diff"]:
            click.echo(ch["diff"])
    if not r["changes"]:
        click.echo("nothing to change")


@command(policy_grp, "status")
@click.option("--json", "json_out", is_flag=True)
@pass_ctx
def policy_status_cmd(c, json_out):
    """What needs attention: unenacted selections, newer versions, hand edits of enacted files."""
    from .policy import status
    items = status(c.kb)
    if json_out:
        return ui.emit_json(items)
    for i in items:
        click.echo(f"{i.get('policy', '-'):<20} {i['what']}")
        click.echo(f"{'':<20} fix: {i['fix']}")
    if not items:
        click.echo("policy is enacted and current")


@command(policy_grp, "diff", no_args_is_help=True)
@click.argument("name")
@click.option("--against", help="Compare with this version instead of the enacted text.")
@pass_ctx
def policy_diff_cmd(c, name, against):
    """Enacted text vs its source version (or two versions)."""
    from .policy import diff
    click.echo(diff(c.kb, name, against) or "no difference", nl=False)


@command(policy_grp, "capture", no_args_is_help=True)
@click.argument("name")
@pass_ctx
def policy_capture_cmd(c, name):
    """Turn edits made to an enacted CLAUDE.md snippet or skill into a draft version here."""
    from .policy import capture
    rec = capture(c.kb, name)
    click.echo(f"{rec.id} {name}@{rec.data['version']} (draft; review, then kb policy release {name})")


@command(policy_grp, "autonomy")
@click.argument("assignments", nargs=-1)
@pass_ctx
def policy_autonomy_cmd(c, assignments):
    """Show autonomy, or set it (people only): capture=auto review=ask push=ask push.home=auto ..."""
    from .policy import AUTONOMY_MEANING, set_autonomy
    kb = c.kb
    if assignments:
        set_autonomy(kb, list(assignments))
        from .items import Store
        kb = Store.open(kb.root)
    from .federation import neighbors
    for key in ("capture", "review", "curate", "policy", "pull", "accept", "merge"):
        if key in kb.cfg.autonomy:
            click.echo(f"{key:<16} {kb.cfg.autonomy_for(key):<6} {AUTONOMY_MEANING[key]}")
    for n in [x.name for x in neighbors(kb)] or ["default"]:
        click.echo(f"{'push.' + n:<16} {kb.cfg.autonomy_for('push', n):<6} {AUTONOMY_MEANING['push']}")
    if assignments:
        click.echo("now run kb policy enact so CLAUDE.md shows the change")


if __name__ == "__main__":
    main()
