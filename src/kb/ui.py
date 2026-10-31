"""Rendering for the CLI.  No data processing here, only presentation."""

import json
import sys

import click


def emit_json(obj) -> None:
    click.echo(json.dumps(obj, indent=2, default=str, ensure_ascii=False))


def record_line(row, show_current: bool = True, store: str | None = None) -> str:
    """One line per record: [store] id kind status title (summary on --long)."""
    get = row.get if isinstance(row, dict) else (lambda k, d=None: row[k] if k in row.keys() else d)
    status = get("status") or "-"
    mark = "" if not show_current or get("current", 1) else "  [superseded]"
    prefix = f"{store + ':':<12} " if store else ""
    also = get("also_in")
    if also:
        mark += f"  (also in {', '.join(also)})"
    return f"{prefix}{get('id'):<16} {get('kind'):<11} {status:<10} {get('title')}{mark}"


def records(rows, long: bool = False, with_store: bool = False) -> None:
    for r in rows:
        click.echo(record_line(r, store=r.get("store") if with_store else None))
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
    click.echo(msg, err=True)


def stderr(msg: str) -> None:
    print(msg, file=sys.stderr)
