"""Rendering for the CLI.  No data processing here, only presentation."""

import json
import sys

import click


def emit_json(obj) -> None:
    click.echo(json.dumps(obj, indent=2, default=str, ensure_ascii=False))


def item_line(row, show_current: bool = True, store: str | None = None) -> str:
    """One line per item: [store] id kind status title (summary on --long)."""
    get = row.get if isinstance(row, dict) else (lambda k, d=None: row[k] if k in row.keys() else d)
    status = get("status") or "-"
    mark = "" if not show_current or get("current", 1) else "  [superseded]"
    prefix = f"{store + ':':<12} " if store else ""
    also = get("also_in")
    if also:
        mark += f"  (also in {', '.join(also)})"
    return f"{prefix}{get('id'):<16} {get('kind'):<11} {status:<10} {get('title')}{mark}"


def item_list(rows, long: bool = False, with_store: bool = False) -> None:
    for r in rows:
        click.echo(item_line(r, store=r.get("store") if with_store else None))
        if long:
            summary = r["summary"] if not isinstance(r, dict) else r.get("summary")
            click.echo(f"{'':<16} {summary}")


def problems(probs) -> None:
    for p in probs:
        color = "red" if p.level == "error" else "yellow"
        click.echo(click.style(f"{p.level}: ", fg=color) + f"{p.where}: [{p.code}] {p.message}")
        if p.hint:
            click.echo(f"    hint: {p.hint}")


def summary_counts(probs) -> None:
    e = sum(p.level == "error" for p in probs)
    w = sum(p.level == "warning" for p in probs)
    msg = f"{e} error(s), {w} warning(s)"
    click.echo(click.style(msg, fg="red" if e else ("yellow" if w else "green")), err=True)


def info(msg: str) -> None:
    """A message for the person (on stderr), logged at level info as well."""
    import logging
    from .logs import tell
    tell(logging.getLogger("kb.cli"), logging.INFO, msg)


def stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


MODE_COLOR = {"auto": "green", "ask": "yellow", "manual": "cyan"}


def findings(rows: list[dict], indent: str = "") -> None:
    """Doctor findings: mode, code, what is wrong, and the fix."""
    for f in rows:
        mode = click.style(f"{f['mode']:<7}", fg=MODE_COLOR.get(f["mode"]))
        click.echo(f"{indent}{mode}{f['code']:<15} {f['summary']}")
        click.echo(f"{indent}{'':<22} fix: {f['fix']}")


def status(v: dict) -> None:
    s, it = v["store"], v["items"]
    click.echo(click.style(f"{s['name']}", bold=True) + f"  {s['scope']} store, access {s['access']}, id {s['id']}")
    click.echo(f"  at {s['root']}, as {s['user']} on {s['host']}")
    if v.get("context"):
        x = v["context"]
        click.echo(f"context   {x['name']} (named by {x['named_by']}; store: {x['located_by']})")
    click.echo("config    " + (", ".join(v.get("config_files") or []) or "no config files"))
    kinds = ", ".join(f"{n} {k}" for k, n in it["by_kind"].items()) or "none yet"
    click.echo(f"items     {it['current']} current ({kinds})" + (f"; {it['replaced']} superseded or retracted"
                                                                  if it["replaced"] else ""))
    if v["last_change"]:
        c = v["last_change"]
        click.echo(f"changed   {c['at'][:16].replace('T', ' ')} {c['action']} {c['item'] or ''} by {c['actor']}")
    pend = v["inbox"]["pending"]
    click.echo(f"inbox     {pend} pending" + (" (kb inbox list)" if pend else ""))
    for e in v["edges"]:
        state = "writable" if e["writable"] else ("readable" if e["available"] else "not available here")
        click.echo(f"edge      {e['name']} ({e['relation']}, pushes land by {e['land']}): {state}")
    if not v["edges"]:
        click.echo("edges     none (kb edge add NAME --relation up --path DIR)")
    for name, ok in v["content_stores"].items():
        click.echo(f"cas       {name}: {'mounted' if ok else 'not available here'}")
    cd = v.get("content_dataset")
    if cd:
        click.echo(f"content   {cd['path'] or 'not on this machine'} ({cd['id']})")
    for src in v.get("sources", []):
        click.echo(f"source    {src['id']} {src['type']} {src['title']}: "
                   + ("on this machine" if src["here"] else "not on this machine"))
    if v["attention"]:
        click.echo(click.style(f"\nNeeds attention ({len(v['attention'])}):", bold=True))
        findings(v["attention"], "  ")
        click.echo("\nkb doctor --fix applies the 'auto' fixes and asks about the others; kb doctor runs a full diagnosis.")
    else:
        click.echo("\nNothing needs attention (kb doctor runs a full diagnosis).")
