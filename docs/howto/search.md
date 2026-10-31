# Finding things

The store is a database, so there is nothing to browse. Use these commands.

## `kb context`: start here

```
kb context detector/tpc/noise              # by subject (prefix match)
kb context detector/tpc --runs 1240-1250   # ... restricted to items valid for those runs
kb context "grounding fix"                 # falls back to text search
kb context obs-1kfvnqgzgv                  # one item, its inputs, users and history
kb context detector/tpc --reach 1          # ... plus neighbor stores
```

The output is compact markdown:

1. topics (the synthesis);
2. current items, one line each with summary;
3. superseded and retracted items, marked as history.

It stops at `--budget` characters (default 12000), so it can go straight
into a prompt.

## `kb ls`: faceted listing

```
kb ls --kind document --subject detector/tpc -l   # -l adds summaries
kb ls --runs 1243 --all                           # include superseded
kb ls --status unverified --since 2026-10-01
kb ls --subjects                                  # the subject vocabulary with counts
kb ls --json                                      # for scripts and LLMs
```

## `kb search`

```
kb search coherent noise        # BM25 over title, summary, subjects and text held in the store
kb search induct*               # prefix
kb search -s "why was the detector noisy late october"   # by meaning
kb search --hybrid "grounding"  # both, fused by rank
```

Semantic search needs an embedding service with an OpenAI-compatible
`/v1/embeddings` API. Configure it for the store, or for you in
`~/.config/kb/config.toml` (`[embed]`):

```
kb config set embed '{"url": "http://127.0.0.1:8080", "model": "nomic-embed-text-v1.5", "identity": "nomic-embed-text"}'
```

Vectors from different model identities never mix. Run `kb store embed` after
new material arrives: only new or changed text is embedded. Vectors live in
the store's `vectors` table, and `kb store reindex` leaves them alone.

## Reading one item

```
kb show obs-…              # the item and its neighbourhood, compact
kb show obs-…            # for people: names, dates, links, where the material is, an excerpt
kb show ks-… --depth 2   # a knowledge set as a tree
kb show top-… --format md    # markdown; --format view gives the view model; --json the item
kb item content top-…           # the text itself
kb rendition path doc-…#original     # a file with the content, for other tools
kb log obs-…               # its history: created, changed, transferred, reviewed …
```

## The database directly

The store is `kb.sqlite`, a plain SQLite file. Query tables (`subjects`,
`runs`, `refs`, `identifiers`, `renditions`, `fts`) are kept in step on
every write. Items are in `items` (the JSON in column `json`), and history
is in `events`. `kb store reindex` rebuilds the query tables after a repair.

Read it, but never write it: write through `kb` only.

For browsing with facets in a web page, open a snapshot (or the database,
immutably):

```
kb store snapshot /tmp/kb-view.sqlite && uvx datasette /tmp/kb-view.sqlite
```

Some useful queries:

```sql
-- items valid for run 1243
SELECT i.id, i.kind, json_extract(i.json, '$.title') FROM items i JOIN runs n ON n.id = i.id
WHERE n.first <= 1243 AND n.last >= 1243;
-- what cites an item
SELECT src, rel FROM refs WHERE dst = 'obs-1kfvnqgzgv';
-- what happened last week
SELECT at, action, item, actor FROM events WHERE at >= '2026-10-24' ORDER BY seq;
```
