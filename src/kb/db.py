"""The SQLite file that is a store.

One file, ``kb.sqlite``, holds everything canonical about a store:

    settings   store configuration (id, name, scope, access, neighbors, policy, autonomy ...)
    items      knowledge items: the JSON object plus columns kb keeps for querying and guarding
    blobs      small content, addressed by sha256 (notes, pages, extracted text, view sources)
    events     append-only history: every create, change, transfer, publication, review
    inbox      received bundles awaiting (or past) review
    reviews    human and LLM reviews of inbox bundles

and derived tables kept in step on every write (subjects, runs, refs,
identifiers, renditions, fts).  ``fts`` has a row per chunk of each text
rendition (with the item's title, summary and subjects in every row), so a hit
names the rendition it is in.  Vectors are not here: see kb.embed.

Guards live in the database itself, so they hold whatever program writes:
frozen items (sources, released policies) cannot change their core or be
deleted; blobs and events cannot be changed or deleted.
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .errors import KBError

DB_NAME = "kb.sqlite"
#: 2: fts by rendition and chunk; vectors moved out of the store (kept as vectors_v1 until copied).
DB_VERSION = 2
#: A settings key the upgrade leaves for Store.open: rebuild the query tables, then forget it.
REINDEX = "_reindex"

DDL = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  json TEXT NOT NULL,
  digest TEXT NOT NULL,
  core TEXT NOT NULL,
  fixed TEXT NOT NULL,
  frozen INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  updated_by TEXT
);
CREATE TABLE IF NOT EXISTS blobs (sha256 TEXT PRIMARY KEY, size INTEGER NOT NULL, data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  item TEXT,
  action TEXT NOT NULL,
  actor TEXT,
  approved TEXT,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS events_item ON events(item);
CREATE INDEX IF NOT EXISTS events_action ON events(action);
CREATE TABLE IF NOT EXISTS inbox (
  bundle TEXT PRIMARY KEY,
  set_id TEXT NOT NULL,
  sender TEXT,
  sender_name TEXT,
  received_at TEXT NOT NULL,
  status TEXT NOT NULL,
  data BLOB NOT NULL,
  decided_at TEXT,
  decided_by TEXT,
  note TEXT
);
CREATE TABLE IF NOT EXISTS reviews (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  bundle TEXT NOT NULL,
  at TEXT NOT NULL,
  reviewer TEXT NOT NULL,
  reviewer_kind TEXT NOT NULL,
  verdict TEXT NOT NULL,
  text TEXT
);

-- derived, kept in step on every write
CREATE TABLE IF NOT EXISTS subjects (id TEXT, subject TEXT);
CREATE TABLE IF NOT EXISTS runs (id TEXT, first INTEGER, last INTEGER);
CREATE TABLE IF NOT EXISTS refs (src TEXT, dst TEXT, rel TEXT);
CREATE TABLE IF NOT EXISTS identifiers (id TEXT, scheme TEXT, value TEXT);
CREATE TABLE IF NOT EXISTS renditions (id TEXT, name TEXT, role TEXT, media_type TEXT, fidelity TEXT,
  sha256 TEXT, size INTEGER, locations TEXT);
CREATE INDEX IF NOT EXISTS subjects_s ON subjects(subject);
CREATE INDEX IF NOT EXISTS subjects_id ON subjects(id);
CREATE INDEX IF NOT EXISTS runs_id ON runs(id);
CREATE INDEX IF NOT EXISTS refs_src ON refs(src);
CREATE INDEX IF NOT EXISTS refs_dst ON refs(dst);
CREATE INDEX IF NOT EXISTS identifiers_id ON identifiers(id);
CREATE INDEX IF NOT EXISTS renditions_id ON renditions(id);
-- one row per chunk: rendition '' chunk 0 is the item's head (title and summary); hash: sha256 of what is
-- embedded for the row (kb.embed), by which vectors are found
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(id UNINDEXED, rendition UNINDEXED, chunk UNINDEXED,
  hash UNINDEXED, title, summary, subjects, body, tokenize='porter unicode61');

-- guards
CREATE TRIGGER IF NOT EXISTS items_keep BEFORE DELETE ON items
  WHEN old.frozen = 1
  BEGIN SELECT RAISE(ABORT, 'kb: append-only: frozen items are never deleted; retract instead'); END;
CREATE TRIGGER IF NOT EXISTS blobs_immutable BEFORE UPDATE ON blobs
  BEGIN SELECT RAISE(ABORT, 'kb: blobs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS blobs_keep BEFORE DELETE ON blobs
  BEGIN SELECT RAISE(ABORT, 'kb: blobs are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS events_immutable BEFORE UPDATE ON events
  BEGIN SELECT RAISE(ABORT, 'kb: events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_keep BEFORE DELETE ON events
  BEGIN SELECT RAISE(ABORT, 'kb: events are append-only'); END;
"""

# The append-only guard on items. Kept separate so a schema migration can lift
# it inside its transaction (migrations restructure frozen items by design).
GUARD = """CREATE TRIGGER IF NOT EXISTS items_frozen BEFORE UPDATE ON items
  WHEN old.frozen = 1 AND (new.core != old.core OR new.frozen = 0 OR EXISTS (
    SELECT 1 FROM json_each(old.fixed) o
    WHERE o.value IS NOT NULL AND json_extract(new.fixed, '$."' || o.key || '"') IS NOT o.value))
  BEGIN SELECT RAISE(ABORT, 'kb: append-only: this item is frozen; supersede it instead'); END"""
# A frozen item keeps its core; a recorded content hash never changes (an unrecorded one may be filled once).

NETWORK_FS = ("nfs", "nfs4", "cifs", "smb", "smb3", "smbfs", "afs", "9p", "sshfs", "fuse.sshfs",
              "fuse.eosxd", "fuse.eos", "fuse.rclone", "fuse.s3fs", "davfs", "fuse.davfs2", "ceph")


def fs_type(path: Path) -> str | None:
    """Filesystem type holding *path* (Linux; None elsewhere)."""
    try:
        lines = Path("/proc/self/mountinfo").read_text().splitlines()
    except OSError:
        return None
    path = str(path.resolve())
    best, kind = "", None
    for line in lines:
        parts = line.split()
        try:
            mnt = parts[4]
            fstype = parts[parts.index("-") + 1]
        except (IndexError, ValueError):
            continue
        if (path == mnt or path.startswith(mnt.rstrip("/") + "/")) and len(mnt) >= len(best):
            best, kind = mnt, fstype
    return kind


def is_network_fs(path: Path) -> bool:
    t = fs_type(path)
    return bool(t) and (t in NETWORK_FS or t.startswith("nfs") or t.startswith("fuse.eos"))


def connect(path: Path, readonly: bool = False, create: bool = False) -> sqlite3.Connection:
    if not path.exists() and not create:
        raise KBError(f"no store database at {path}")
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None)
    else:
        if is_network_fs(path.parent) and not os.environ.get("KB_ALLOW_NETWORK_FS"):
            raise KBError(f"{path.parent} is on a network filesystem ({fs_type(path.parent)}); SQLite must not be "
                          "written there. Keep stores on local disk; share them by bundles and snapshots "
                          "(KB_ALLOW_NETWORK_FS=1 overrides, at your own risk)")
        conn = sqlite3.connect(path, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if not readonly:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > DB_VERSION:
            raise KBError(f"{path} was written by a newer kb (database version {version}); upgrade kb")
        if version < DB_VERSION:
            if version == 1:          # fts is rebuilt (Store.open reindexes); vectors are kept to be copied out
                conn.execute("DROP TABLE IF EXISTS fts")
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'vectors'").fetchone():
                    conn.execute("ALTER TABLE vectors RENAME TO vectors_v1")
                conn.execute("INSERT OR REPLACE INTO settings VALUES (?, 'true')", (REINDEX,))
            conn.executescript(DDL)
            conn.execute(GUARD)
            conn.execute(f"PRAGMA user_version = {DB_VERSION}")
    return conn


class Tx:
    """Nestable transactions: the outermost is BEGIN IMMEDIATE ... COMMIT, inner ones savepoints."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.depth = 0

    @contextmanager
    def __call__(self):
        if self.depth == 0:
            self.conn.execute("BEGIN IMMEDIATE")
        else:
            self.conn.execute(f"SAVEPOINT kb{self.depth}")
        self.depth += 1
        try:
            yield self.conn
        except BaseException:
            self.depth -= 1
            if self.depth == 0:
                self.conn.execute("ROLLBACK")
            else:
                self.conn.execute(f"ROLLBACK TO kb{self.depth}")
                self.conn.execute(f"RELEASE kb{self.depth}")
            raise
        self.depth -= 1
        if self.depth == 0:
            self.conn.execute("COMMIT")
        else:
            self.conn.execute(f"RELEASE kb{self.depth}")
