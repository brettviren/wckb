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
kb search coherent noise        # BM25 over titles, summaries, subjects and the text of items
kb search induct*               # prefix
kb search -s "why was the detector noisy late october"   # by meaning
kb search --hybrid "grounding"  # both, fused by reciprocal rank
```

**What is searched.** Items, never whole sources: an Indico site's dataset
or a DataLad dataset is not indexed, only the items that use its files.
For each item, kb searches its title, summary and subjects, and the text of
its text renditions: notes, pages, text files, and text extracted from
PDFs, slides and the like. It reads that text wherever it is kept (the
store, a content store, a dataset), as long as it is on this machine.

**Text from PDFs, slides and images.** `kb add` extracts it (`--no-extract`
to skip). The text becomes a rendition of the item, `text`, derived from
the `original`. Text of a PDF is kept page by page, so a hit names the page:

```
doc-1q80e6th8z   document    -          Purity talk
                 in #text (text/plain), page 2, from #original (application/pdf)
                 The purity monitor saw [electron] lifetime drop.
```

**OCR.** Images, and PDF pages that have no text of their own (scans), are
read by OCR when an OCR engine is available. Such text has fidelity `ocr`.
The engine is `tesseract` (`apt install tesseract-ocr`) or one you
configure. Any program that prints an image's text will do, such as a
script that asks a local model behind llama.cpp or ollama:

```toml
[ocr]
language = "eng"                 # tesseract's -l
# engine = "qwen-vl"             # use this one (default: the best available)

[ocr.engines.qwen-vl]
command = ["ocr-with-ollama", "--model", "qwen2.5vl", "{input}"]
priority = 10                    # lower is preferred; tesseract is 50
version = "qwen2.5vl"
```

**Text that is not here yet.** Text whose content is not on this machine
(a URL never fetched, a dataset not cloned) is not indexed.
`kb store reindex --fetch` fetches it and indexes it.

`kb doctor` extracts what was never extracted (`unextracted`), and
re-extracts text whose original changed (`stale`).

**Meaning.** Semantic search needs an embedding service with an
OpenAI-compatible `/v1/embeddings` API. Configure it for the store, or for
you in `~/.config/kb/config.toml`:

```toml
[embed]
url = "http://127.0.0.1:8080"
model = "nomic-embed-text-v1.5"
identity = "nomic-embed-text"            # what its vectors are kept under (default: the model)
document_prefix = "search_document: "    # for models trained with prefixes, as nomic's are
query_prefix = "search_query: "
```

Run `kb store embed` after new material arrives. It embeds only text that
this model has no vectors for.

Vectors are a cache in `~/.cache/wckb/vectors.sqlite`, shared by your
stores (`embed.vectors` names another file). Each vector is keyed by its
model identity and the hash of its text, so:

- reindexing a store loses nothing;
- the same text in two stores is embedded once;
- switching model keeps the old model's vectors, and switching back finds
  them again.

If you change prefixes, change `identity` too, since the vectors change.

Nearest vectors are found with sqlite-vec (a dependency of kb) when SQLite
can load it, else by comparing every vector in Python
(`embed.sqlite_vec = false` turns sqlite-vec off).

## Discovering kinds, fields and values

```
kb new --help                     # the kinds
kb new observation --help         # an observation's fields: required ones, options, allowed values
kb add --kind document --help     # the same for kb add
kb item link --help               # the relations of typed links, and their inverses
kb ls --subjects; kb ls --tags    # the subjects and tags in use
kb source types; kb rendition transforms
```

## Opening what you found

`kb open ID` opens an item's file with the application your desktop uses for
its type (`xdg-open`; `open` on macOS). It opens the original, even when the
words matched in text extracted from it; `--text` opens that text instead,
and `ID#NAME` names a rendition. A file not on this machine is fetched first
(a download, or `datalad get` in its dataset). The path is printed, and
`--print` prints it without opening anything; kb also says the file's media
type and which opener it used. Another opener:

```toml
# ~/.config/kb/config.toml
[open]
command = ["gio", "open"]      # {path} marks where the path goes; else it comes last
```

Openers can depend on where you are. `open.rules` are tried in order; the
first whose `env` variables are all set (and none of its `unless` ones) is
used, else `open.command`, else the desktop's. An opener that has to talk to
your terminal gets `terminal = true`: it runs attached to the terminal and
kb waits for it, where other openers run in the background. For example,
[kitty-open](https://sw.kovidgoyal.net/kitty/kittens/transfer/)-style
scripts, which push the file over SSH to the machine you sit at with
`kitten transfer` and open it there:

```toml
[[open.rules]]
name = "kitty over ssh"
env = ["SSH_CONNECTION", "KITTY_LISTEN_ON"]   # an SSH session from kitty, remote control forwarded
command = ["kitty-open"]
terminal = true                               # kitten transfer talks to kitty through the terminal

[[open.rules]]
name = "plain ssh"
env = "SSH_CONNECTION"
unless = "DISPLAY"                            # no X forwarding: just print the path
command = ["true"]
```

Put rules in your user config (`~/.config/kb/config.toml`) when they are
about your terminal and machines; with a home directory shared between
machines, the same file serves all of them, and the rules pick per session.

## Reading one item

```
kb show obs-…              # the item and its neighbourhood, compact
kb show obs-…            # for people: names, dates, links, where the material is, an excerpt
kb show ks-… --depth 2   # a knowledge set as a tree
kb show top-… --format md    # markdown; --format view gives the view model; --json the item
kb item content top-…           # the text itself
kb rendition path doc-…#original     # a file with the content, for other tools
kb open doc-…              # open its file in the application for its type (the original, got if need be)
kb open doc-… --text       # the text extracted from it, which search matched
kb open doc-… --print      # only the path
kb log obs-…               # its history: created, changed, transferred, reviewed …
kb item links obs-…        # its links both ways: evidence, corrections, derivation, typed links (supported by …)
kb item links obs-… --depth 2   # and theirs
```

## The database directly

The store is `kb.sqlite`, a plain SQLite file. Query tables (`subjects`,
`runs`, `refs`, `identifiers`, `renditions`, `fts`) are kept in step on
every write; `fts` has a row per item head and per chunk of each text
rendition (columns `id`, `rendition`, `chunk`). Items are in `items` (the JSON in column `json`), and history
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
