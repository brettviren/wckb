"""Embeddings for semantic search.

The model sits behind an OpenAI-compatible ``/v1/embeddings`` endpoint (the
same convention as orgface), configured in ``[embed]``.  Vectors live in
``.kb/vectors.sqlite``, apart from the index, so ``kb reindex`` never throws
them away; each chunk is embedded once per text hash and model identity.
"""

import hashlib
import json
import math
import sqlite3
import struct
import urllib.error
import urllib.request

from .errors import KBError
from .records import KB

CHUNK_CHARS = 1500

SCHEMA = """
CREATE TABLE IF NOT EXISTS vectors (
  identity TEXT, id TEXT, chunk INTEGER, text_hash TEXT, dim INTEGER, vec BLOB,
  PRIMARY KEY (identity, id, chunk));
"""


class Embedder:
    def __init__(self, url: str, model: str, identity: str | None = None, api_key: str | None = None,
                 batch: int = 32, timeout: float = 120):
        self.url = url.rstrip("/").removesuffix("/v1")
        self.model = model
        self.identity = identity or model
        self.api_key = api_key
        self.batch = batch
        self.timeout = timeout

    @classmethod
    def from_config(cls, kb: KB) -> "Embedder":
        c = kb.cfg.embed
        if not c or "url" not in c or "model" not in c:
            raise KBError("no embedding service: kb config set embed '{\"url\": ..., \"model\": ...}' (or [embed] in your config.toml)")
        return cls(c["url"], c["model"], c.get("identity"), c.get("api_key"), int(c.get("batch", 32)))

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


def _pack(v):
    return struct.pack(f"{len(v)}f", *v)


def _unpack(b, dim):
    return struct.unpack(f"{dim}f", b)


def _norm(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _db(kb: KB) -> sqlite3.Connection:
    return kb.conn   # vectors live in the store's database (table "vectors")


def record_chunks(kb: KB) -> dict[tuple[str, int], str]:
    from .records import text_of
    out = {}
    for rec in kb.records.values():
        head = f"{rec.data.get('title', '')}\n{rec.data.get('summary', '')}"
        for i, c in enumerate([head] + chunks(text_of(kb, rec))):
            out[(rec.id, i)] = c
    return out


def embed(kb: KB, embedder: Embedder | None = None) -> dict:
    """Embed new or changed chunks; drop vectors of chunks that are gone."""
    embedder = embedder or Embedder.from_config(kb)
    db = _db(kb)
    db.execute("BEGIN IMMEDIATE")
    have = {(r[0], r[1]): r[2] for r in db.execute(
        "SELECT id, chunk, text_hash FROM vectors WHERE identity = ?", (embedder.identity,))}
    want = record_chunks(kb)
    todo = []
    for key, text in want.items():
        h = hashlib.sha256(text.encode()).hexdigest()
        if have.get(key) != h:
            todo.append((key, text, h))
    gone = [k for k in have if k not in want]
    db.executemany("DELETE FROM vectors WHERE identity=? AND id=? AND chunk=?",
                   [(embedder.identity, *k) for k in gone])
    if todo:
        vecs = embedder([t for _, t, _ in todo])
        db.executemany("INSERT OR REPLACE INTO vectors VALUES (?,?,?,?,?,?)", [
            (embedder.identity, k[0], k[1], h, len(v), _pack(_norm(v))) for (k, _, h), v in zip(todo, vecs)])
    db.execute("COMMIT")
    return {"embedded": len(todo), "removed": len(gone), "total": len(want), "identity": embedder.identity}


def semantic(kb: KB, query: str, limit: int = 20, embedder: Embedder | None = None) -> list[tuple[str, float]]:
    """(record ID, best cosine similarity) ordered best first."""
    embedder = embedder or Embedder.from_config(kb)
    if not kb.conn.execute("SELECT 1 FROM vectors WHERE identity = ? LIMIT 1", (embedder.identity,)).fetchone():
        raise KBError("no embeddings yet: run kb embed")
    q = _norm(embedder([query])[0])
    best: dict[str, float] = {}
    for rid, dim, blob in _db(kb).execute(
            "SELECT id, dim, vec FROM vectors WHERE identity = ?", (embedder.identity,)):
        if dim != len(q):
            raise KBError(f"vectors for {embedder.identity} have {dim} dimensions, query has {len(q)}")
        s = sum(a * b for a, b in zip(q, _unpack(blob, dim)))
        if s > best.get(rid, -2):
            best[rid] = s
    return sorted(best.items(), key=lambda kv: -kv[1])[:limit]
