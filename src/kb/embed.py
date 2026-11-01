"""Embeddings for semantic search.

The model sits behind an OpenAI-compatible ``/v1/embeddings`` endpoint (the
same convention as orgface), configured in ``[embed]``: ``url``, ``model``,
and optionally ``identity`` (the name its vectors are kept under; by default
the model's), ``document_prefix`` and ``query_prefix`` (for models trained
asymmetrically, such as nomic-embed-text: ``"search_document: "`` and
``"search_query: "``), ``api_key``, ``batch`` and ``vectors`` (the file).

What is embedded is what is indexed: each row of the store's ``fts`` table, an
item's head (title and summary) or a chunk of one of its text renditions.

**Vectors are a cache, kept apart from the store**, in ``vectors.sqlite`` in
the user's cache (``~/.cache/wckb/``), shared by all of the user's stores.
They are addressed by model identity and the SHA-256 of the text, so:

- text that is unchanged is never embedded again, whichever item or store
  it is in, and whatever reindexing happens to the store;
- each model has its own vectors; changing model keeps the old ones, and
  changing back finds them again;
- losing the file costs only the time to embed again.

Vectors of a store from before this layout (its ``vectors`` table) are
copied in once.
"""

import hashlib
import json
import logging
import math
import sqlite3
import struct
import urllib.error
import urllib.request
from pathlib import Path

from .errors import KBError
from .items import Store

log = logging.getLogger(__name__)

CHUNK_CHARS = 1500

SCHEMA = """
CREATE TABLE IF NOT EXISTS vectors (
  identity TEXT NOT NULL, text_hash TEXT NOT NULL, dim INTEGER NOT NULL, vec BLOB NOT NULL,
  PRIMARY KEY (identity, text_hash));
"""


class Embedder:
    def __init__(self, url: str, model: str, identity: str | None = None, api_key: str | None = None,
                 batch: int = 32, timeout: float = 120, document_prefix: str = "", query_prefix: str = ""):
        self.url = url.rstrip("/").removesuffix("/v1")
        self.model = model
        self.identity = identity or model
        self.api_key = api_key
        self.batch = batch
        self.timeout = timeout
        self.document_prefix = document_prefix
        self.query_prefix = query_prefix

    @classmethod
    def from_config(cls, kb: Store) -> "Embedder":
        c = kb.cfg.embed
        if not c or "url" not in c or "model" not in c:
            raise KBError("no embedding service: kb config set embed '{\"url\": ..., \"model\": ...}' (or [embed] in your config.toml)")
        return cls(c["url"], c["model"], c.get("identity"), c.get("api_key"), int(c.get("batch", 32)),
                   document_prefix=c.get("document_prefix", ""), query_prefix=c.get("query_prefix", ""))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self([self.document_prefix + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self([self.query_prefix + text])[0]

    def __call__(self, texts: list[str]) -> list[list[float]]:
        out = []
        for i in range(0, len(texts), self.batch):
            out += self._post(texts[i:i + self.batch])
        return out

    def _post(self, texts):
        body = json.dumps({"model": self.model, "input": texts}).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(f"{self.url}/v1/embeddings", data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.load(resp)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise KBError(f"embedding service {self.url}: {exc}") from exc
        return [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]


def _documents(embedder, texts: list[str]) -> list[list[float]]:
    """Any callable taking texts is an embedder too (tests use one); Embedder adds its prefixes."""
    return embedder.embed_documents(texts) if hasattr(embedder, "embed_documents") else embedder(texts)


def _query(embedder, text: str) -> list[float]:
    return embedder.embed_query(text) if hasattr(embedder, "embed_query") else embedder([text])[0]


def _identity(embedder) -> str:
    return getattr(embedder, "identity", None) or getattr(embedder, "model", None) or type(embedder).__name__


def chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Paragraph-packed chunks of about *size* characters."""
    out, cur = [], ""
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        while len(para) > size:
            if cur:
                out.append(cur)
                cur = ""
            out.append(para[:size])
            para = para[size:]
        if len(cur) + len(para) + 2 > size and cur:
            out.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out


def head_text(title: str, summary: str) -> str:
    """What is embedded for an item's head row."""
    return f"{title}\n{summary}"


def _pack(v):
    return struct.pack(f"{len(v)}f", *v)


def _unpack(b, dim):
    return struct.unpack(f"{dim}f", b)


def _norm(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def vectors_path(kb: Store) -> Path:
    from .config import cache_home
    p = (kb.cfg.embed or {}).get("vectors")
    return Path(p).expanduser() if p else cache_home() / "vectors.sqlite"


def _vdb(kb: Store) -> sqlite3.Connection:
    path = vectors_path(kb)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=10000")
    db.executescript(SCHEMA)
    _adopt_old(kb, db)
    return db


def _adopt_old(kb: Store, db: sqlite3.Connection) -> None:
    """Copy vectors a store kept itself (before database version 2) into the cache, once."""
    if not kb.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'vectors_v1'").fetchone():
        return
    rows = kb.conn.execute("SELECT identity, text_hash, dim, vec FROM vectors_v1").fetchall()
    db.executemany("INSERT OR IGNORE INTO vectors VALUES (?,?,?,?)", [tuple(r) for r in rows])
    log.info("copied %d vectors of %s into %s", len(rows), kb.root, vectors_path(kb))
    if not kb.readonly:
        kb.conn.execute("DROP TABLE vectors_v1")


def index_texts(kb: Store) -> dict[str, str]:
    """What the store's index asks to have embedded: text by its hash."""
    out = {}
    for r in kb.conn.execute("SELECT rendition, hash, title, summary, body FROM fts"):
        out[r["hash"]] = head_text(r["title"], r["summary"]) if r["rendition"] == "" else r["body"]
    return out


def missing(kb: Store, identity: str) -> tuple[int, int]:
    """(texts of the index without a vector of IDENTITY, texts in all)."""
    want = {r[0] for r in kb.conn.execute("SELECT hash FROM fts")}
    db = _vdb(kb)
    try:
        have = {r[0] for r in db.execute("SELECT text_hash FROM vectors WHERE identity = ?", (identity,))}
    finally:
        db.close()
    return len(want - have), len(want)


def embed(kb: Store, embedder: Embedder | None = None) -> dict:
    """Embed what the index has that this model has no vector for; vectors of other text and other models
    are kept (they are a cache)."""
    embedder = embedder or Embedder.from_config(kb)
    identity = _identity(embedder)
    db = _vdb(kb)
    try:
        want = index_texts(kb)
        have = {r[0] for r in db.execute("SELECT text_hash FROM vectors WHERE identity = ?", (identity,))}
        todo = [(h, t) for h, t in want.items() if h not in have]
        log.debug("%s: %d of %d texts to embed", identity, len(todo), len(want))
        if todo:
            vecs = _documents(embedder, [t for _, t in todo])
            db.execute("BEGIN IMMEDIATE")
            db.executemany("INSERT OR REPLACE INTO vectors VALUES (?,?,?,?)",
                           [(identity, h, len(v), _pack(_norm(v))) for (h, _), v in zip(todo, vecs)])
            db.execute("COMMIT")
        cached = db.execute("SELECT count(*) FROM vectors WHERE identity = ?", (identity,)).fetchone()[0]
    finally:
        db.close()
    return {"embedded": len(todo), "total": len(want), "identity": identity, "cached": cached,
            "file": str(vectors_path(kb))}


def semantic(kb: Store, query: str, limit: int = 20, embedder: Embedder | None = None) -> list[tuple]:
    """(item ID, best cosine similarity, rendition of the best chunk) ordered best first."""
    embedder = embedder or Embedder.from_config(kb)
    identity = _identity(embedder)
    rows = kb.conn.execute("SELECT id, rendition, hash FROM fts").fetchall()
    db = _vdb(kb)
    try:
        hashes = {r["hash"] for r in rows}
        vecs = {h: (dim, blob) for h, dim, blob in db.execute(
            "SELECT text_hash, dim, vec FROM vectors WHERE identity = ?", (identity,)) if h in hashes}
    finally:
        db.close()
    if not vecs:
        raise KBError(f"no embeddings by {identity} for this store yet: run kb store embed")
    q = _norm(_query(embedder, query))
    best: dict[str, tuple[float, str]] = {}
    for r in rows:
        v = vecs.get(r["hash"])
        if v is None:
            continue
        dim, blob = v
        if dim != len(q):
            raise KBError(f"vectors of {identity} have {dim} dimensions, the query's {len(q)}: "
                          "a different model under the same identity?")
        s = sum(a * b for a, b in zip(q, _unpack(blob, dim)))
        if s > best.get(r["id"], (-2.0, ""))[0]:
            best[r["id"]] = (s, r["rendition"])
    return sorted(((i, s, rn) for i, (s, rn) in best.items()), key=lambda t: -t[1])[:limit]


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
