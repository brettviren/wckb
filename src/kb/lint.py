"""``kb lint``: advice, never blocking.

Findings here need judgment (is this a contradiction? should this old claim
be verified?) and are read by people and by the kb-curate skill.
"""

from datetime import datetime, timedelta, timezone

from . import graph
from .build import publications
from .checks import Problem
from .ids import parse_ref
from .items import Store, source_refs


def _warn(code, where, msg, hint=""):
    return Problem("warning", code, where, msg, hint)


def _overlap(a: list, b: list) -> bool:
    return any(x0 <= y1 and y0 <= x1 for x0, x1 in a for y0, y1 in b)


def lint(kb: Store) -> list[Problem]:
    out: list[Problem] = []
    days = int(kb.cfg.lint.get("unverified_days", 90))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    for s in graph.stale(kb):
        hint = {"rendition": f"kb rendition extract {s.id} --name {s.rendition} --force",
                "view": f"kb view build {s.id}",
                "topic": "revise the page with an LLM (kb-correct / kb-curate skill), then kb item stamp " + s.id,
                }.get(s.what, f"review {s.id}, then kb item stamp {s.id}")
        where = s.id + (f"#{s.rendition}" if s.rendition else "")
        out.append(_warn("stale", where, "; ".join(s.reasons), hint))

    current_obs = []
    for rec in kb.items.values():
        d = rec.data
        if not kb.is_current(rec.id):
            continue
        if not d.get("subject") and rec.kind != "person":
            out.append(_warn("subject", rec.id, "no subject; it will be hard to find", "add subject paths"))
        if d.get("summary", "").strip() == d.get("title", "").strip():
            out.append(_warn("summary", rec.id, "summary just repeats the title",
                             "write one to three sentences an LLM can read instead of the content"))
        if d.get("status") == "unverified" and d.get("created", {}).get("at", "9") < cutoff:
            out.append(_warn("unverified", rec.id, f"unverified for over {days} days",
                             "find evidence and verify, mark disputed, or retract"))
        for r in rec.renditions:
            locs = r.get("locations", [])
            if locs and all(l["type"] == "local" for l in locs):
                out.append(_warn("local-only", f"{rec.id}#{r['name']}", f"only on host {locs[0]['host']}",
                                 "put it in a content store (kb rendition locate) or add a URL others can reach"))
            if r["role"] == "original" and "sha256" not in r:
                out.append(_warn("unhashed", f"{rec.id}#{r['name']}", "no sha256: changes upstream cannot be detected",
                                 "fetch it (kb rendition verify --record); if the site refuses kb, add the copy "
                                 "you have: kb rendition add ID FILE --name original-copy"))
        if rec.kind == "view":
            for pub in publications(kb, rec)[-1:]:
                gone = [i for i in pub.get("inputs", []) if parse_ref(i)[0] in kb.items
                        and not kb.is_current(parse_ref(i)[0])]
                if gone:
                    out.append(_warn("published-stale", rec.id,
                                     f"latest published version {pub['version']} used superseded inputs: {', '.join(gone)}",
                                     f"kb view build {rec.id} && kb view publish {rec.id}"))
        if rec.kind == "observation" and d.get("subject") and d.get("valid", {}).get("runs"):
            current_obs.append(rec)

    for i, a in enumerate(current_obs):
        for b in current_obs[i + 1:]:
            shared = set(a.data["subject"]) & set(b.data["subject"])
            if shared and _overlap(a.data["valid"]["runs"], b.data["valid"]["runs"]):
                out.append(_warn("overlap", f"{a.id} {b.id}",
                                 f"both current, same subject ({', '.join(sorted(shared))}) and overlapping runs",
                                 "consistent, complementary or contradictory? if contradictory, supersede or dispute one"))
    return out


def dangling(kb: Store) -> list[tuple[str, str]]:
    """(item, ref) for references to items that are not in this store."""
    out = []
    for rec in kb.items.values():
        d = rec.data
        refs = d.get("members", []) + d.get("supersedes", []) + d.get("retracts", []) + d.get("related", [])
        refs += rec.inputs() + [e for e in d.get("evidence", []) if "://" not in e] + source_refs(d)
        for r in refs:
            if not kb.exists(r):
                out.append((rec.id, r))
    return out
