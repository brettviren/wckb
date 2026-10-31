"""Schema migrations between major versions.

A migration is a pure function from a record of version N to version N+1,
registered in ``MIGRATIONS[N]``.  ``upgrade`` applies the chain to one record
(used when comparing against old git revisions), ``migrate`` rewrites the
whole KB in one reviewable change.  There are none yet: v1 is the first.
"""

import json
from collections.abc import Callable

from . import schema
from .errors import KBError
from .records import KB, Record

MIGRATIONS: dict[int, Callable[[dict], dict]] = {}


def upgrade(data: dict, to: int = schema.MAJOR, migrations=None) -> dict:
    migrations = MIGRATIONS if migrations is None else migrations
    v = data.get("schema_version", 1)
    while v < to:
        if v not in migrations:
            raise KBError(f"no migration from schema version {v}")
        data = migrations[v](dict(data))
        v += 1
        data["schema_version"] = v
    return data


def migrate(kb: KB, to: int = schema.MAJOR, migrations=None, dry_run: bool = False) -> list[str]:
    """Upgrade every item to version *to*, in one transaction; returns the IDs changed.

    Migrations restructure items, frozen ones included, so the append-only
    guard is lifted for the duration of the transaction and restored before
    it commits.
    """
    from . import db as dbm
    todo = [r for r in kb.records.values() if r.data.get("schema_version", 1) < to]
    if dry_run or not todo:
        return [r.id for r in todo]
    with kb.tx():
        kb.conn.execute("DROP TRIGGER IF EXISTS items_frozen")
        for rec in todo:
            kb.save(Record(upgrade(rec.data, to, migrations), kb), "migrate", {"to": to},
                    validate=(to == schema.MAJOR))
        kb.conn.execute(dbm.GUARD)
        cur = kb.setting("kb", {})
        cur["schema"] = f"{to}.0" if to != schema.MAJOR else schema.REVISION
        kb.conn.execute("INSERT OR REPLACE INTO settings VALUES ('kb', ?)", (json.dumps(cur),))
    kb.reload()
    return [r.id for r in todo]
