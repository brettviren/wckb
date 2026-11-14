"""The command tree: find the commands whose names, help texts and options fit some words.

``kb cli search WORDS`` ranks the runnable commands of the tree (families
themselves are not runnable) and suggests a command line for each: its
path, its required arguments and options, and the options that matched.

Matching is by word.  Words are lowercased and lightly stemmed, so
"notes" finds "note" and "embedding" finds "embed"; a word that begins
another (four letters or more) matches it less well, and a few synonyms
("delete" for "retract", "find" for "search" ...) less well still.  Each
word weighs by how rare it is in the tree, and a command that has all the
words ranks above one that has some.
"""

import math
import re
from dataclasses import dataclass, field

STOPWORDS = frozenset("""
a an and are as at be by can do does for from has have how i in into is it its me my of on or our so
than that the their then there these this those to too use using want was way we what when where which
who why will with would you your kb command commands
""".split())

# Words people use for what kb's commands call otherwise.  Each group is a set of interchangeable stems.
SYNONYMS = [
    {"delete", "remove", "withdraw", "retract", "drop", "erase"},
    {"find", "search", "look", "lookup", "query", "grep"},
    {"fix", "repair", "correct", "doctor", "heal", "mend"},
    {"copy", "clone", "mirror", "duplicate"},
    {"download", "fetch", "obtain", "pull", "get", "retrieve"},
    {"upload", "push", "send", "publish", "share"},
    {"list", "ls", "show", "display", "print"},
    {"change", "edit", "modify", "update", "set", "alter"},
    {"create", "new", "make", "add", "start"},
    {"import", "add", "ingest", "capture", "file"},
    {"export", "package"},
    {"check", "verify", "validate", "lint", "test"},
    {"link", "relate", "connect", "relation"},
    {"history", "log", "event"},
    {"tag", "label"},
    {"install", "enact", "setup"},
    {"token", "secret", "credential", "password"},
    {"web", "url", "page", "site", "website"},
    {"replace", "supersede", "correct"},
    {"stale", "outdated", "old"},
    {"open", "launch", "view"},
]

# How much a match in each part of a command counts.
W_NAME, W_SHORT, W_HELP, W_PARENT = 3.0, 2.0, 1.0, 1.0
W_OPT_NAME, W_OPT_HELP, W_ARG = 1.5, 1.0, 0.5
# How well a word matches: the same stem, one beginning the other, a synonym.
Q_EXACT, Q_PREFIX, Q_SYNONYM = 1.0, 0.6, 0.4
MAX_OPTIONS = 3


def stem(word: str) -> str:
    """A light English stemmer: enough for plurals and -ing/-ed forms of command words."""
    w = word
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("es") and (w[-3] in "sxz" or w[-4:-2] in ("ch", "sh")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]                                          # embeddings -> embedding -> embed
    for suffix in ("ing", "ed"):
        rest = w[:-len(suffix)]
        if w.endswith(suffix) and (len(rest) >= 4 or len(rest) == 3 and rest[-1] == rest[-2]):   # added, not embed
            w = rest
            if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "lsz":
                w = w[:-1]                                  # embedd -> embed, but pull, pass stay
            break
    return w


def words(text: str) -> list[str]:
    """The stems of the words in TEXT that carry meaning."""
    return [stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in STOPWORDS]


_SYN = {}
for _group in SYNONYMS:
    for _w in _group:
        _SYN.setdefault(stem(_w), set()).update(stem(x) for x in _group)


def quality(term: str, token: str) -> float:
    """How well a query stem matches a stem of the tree."""
    if term == token:
        return Q_EXACT
    short, long = sorted((term, token), key=len)
    if len(short) >= 4 and long.startswith(short):
        return Q_PREFIX
    if token in _SYN.get(term, ()):
        return Q_SYNONYM
    return 0.0


@dataclass
class Param:
    """An option or argument of a command, as text to match and to show."""
    shown: str                    # --runs RUNS, ID
    help: str
    fields: list[tuple[float, set[str]]]
    required: bool
    option: bool


@dataclass
class Entry:
    """A runnable command and the text it is found by."""
    path: list[str]
    short: str
    params: list[Param]
    fields: list[tuple[float, set[str]]] = field(default_factory=list)

    def tokens(self) -> set[str]:
        return {t for _, ts in self.fields for t in ts} | {t for p in self.params for _, ts in p.fields for t in ts}


def _metavar(param) -> str:
    """How a value of PARAM is shown: its metavar, a few short choices, or its name."""
    import click
    if param.metavar:
        return param.metavar
    t = param.type
    if isinstance(t, click.Choice):
        choices = "|".join(str(c) for c in t.choices)
        if len(choices) <= 32:
            return choices
    elif isinstance(param, click.Option) and isinstance(t, click.types.IntParamType):
        return "N"
    elif isinstance(param, click.Option) and isinstance(t, click.Path):
        return "PATH"
    return (param.name or "value").upper()


def _param(p) -> Param | None:
    import click
    if getattr(p, "hidden", False):
        return None
    if isinstance(p, click.Argument):
        shown = _metavar(p) + ("..." if p.nargs == -1 else "")
        shown = shown if p.required else f"[{shown}]"
        return Param(shown, "", [(W_ARG, set(words(p.name)))], p.required, False)
    if not isinstance(p, click.Option) or "--help" in p.opts or "--version" in p.opts:
        return None
    name = max(p.opts, key=len)
    mv = "" if p.is_flag or p.count else _metavar(p)
    help_text = (p.help or "").split("\f")[0]
    fields = [(W_OPT_NAME, set(words(" ".join(p.opts + p.secondary_opts).replace("-", " "))) | set(words(mv)))]
    choices = [str(c) for c in p.type.choices] if isinstance(p.type, click.Choice) else []
    fields.append((W_OPT_HELP, set(words(help_text + " " + " ".join(choices)))))
    return Param(f"{name} {mv}".strip(), " ".join(help_text.split()), fields, p.required, True)


def entries(root) -> list[Entry]:
    """The runnable commands under ROOT (a click group), hidden ones left out.

    ROOT's own options (given before any command) are an entry with an empty path.
    """
    import click
    out = [Entry([], "Options for every command, given before it.", [x for x in map(_param, root.params) if x])]

    def walk(cmd, path, parent_help):
        if getattr(cmd, "hidden", False):
            return
        if isinstance(cmd, click.Group):
            here = parent_help + " " + (cmd.help or "") if path else ""
            for name, sub in cmd.commands.items():
                walk(sub, path + [name], here)
            return
        text = (cmd.help or "").split("\f")[0]
        short = cmd.get_short_help_str(200)
        e = Entry(path, short, [x for x in map(_param, cmd.params) if x])
        e.fields = [(W_NAME, set(words(" ".join(path)))), (W_SHORT, set(words(short))),
                    (W_HELP, set(words(text))), (W_PARENT, set(words(parent_help)))]
        out.append(e)

    walk(root, [], "")
    return out


def _best(term: str, fields) -> float:
    return max((w * quality(term, t) for w, ts in fields for t in ts), default=0.0)


def search(root, query: str, limit: int = 8, prog: str = "kb") -> list[dict]:
    """Commands of ROOT that fit the words of QUERY, best first, each with a suggested command line.

    Each row: command, line (the command line suggested), summary (its help's first sentence),
    score, matched (the query's stems it matched) and options (those that matched, with their help).
    """
    terms = list(dict.fromkeys(words(query)))
    if not terms:
        return []
    found = entries(root)
    vocab = [e.tokens() for e in found]
    n = len(found)

    def idf(term):
        df = sum(1 for toks in vocab if any(quality(term, t) >= Q_PREFIX for t in toks))
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    weights = {t: idf(t) for t in terms}
    rows = []
    for e in found:
        score, matched, opt_scores = 0.0, [], []
        for term in terms:
            own = _best(term, e.fields)
            per_param = [_best(term, p.fields) for p in e.params]
            best = max([own] + per_param)
            if best:
                score += weights[term] * best
                matched.append(term)
            for i, s in enumerate(per_param):     # an option is suggested for a word it matches well
                if s >= max(own, W_OPT_HELP * Q_PREFIX) and e.params[i].option:
                    opt_scores.append((s * weights[term], i))
        chosen = []
        for _, i in sorted(opt_scores, reverse=True):
            if i not in chosen and not e.params[i].required:
                chosen.append(i)
        chosen = chosen[:MAX_OPTIONS]
        if not matched or (not e.path and not chosen):
            continue
        score *= len(matched) / len(terms)
        line = [prog, *e.path]
        line += [p.shown for p in e.params if p.option and p.required]
        line += [p.shown for p in e.params if not p.option]
        line += [e.params[i].shown for i in chosen]
        if not e.path:
            line.append("COMMAND ...")
        rows.append({
            "command": " ".join([prog, *e.path]),
            "line": " ".join(line),
            "summary": e.short,
            "score": round(score, 3),
            "matched": matched,
            "options": [{"option": e.params[i].shown, "help": e.params[i].help} for i in chosen],
        })
    rows.sort(key=lambda r: (-r["score"], len(r["command"].split()), r["command"]))
    return rows[:limit]
