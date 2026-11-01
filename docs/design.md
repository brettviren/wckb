# kb: design

A knowledge base (KB) that a physics group, and the LLM sessions each member
runs, read from and write to. People sit at the edges. In the middle, LLMs
exchange knowledge in a form tuned for them rather than for human readers,
and people review what crosses into shared stores.

Related documents:

- [glossary.md](glossary.md): the terms used here, and their names in the code;
- [plan.md](plan.md): the implementation plan;
- [howto/](howto/): task guides;
- [questions.md](questions.md): open questions;
- [decisions/](decisions/): decisions, including
  [0001](decisions/0001-knowledge-store.md), the choice of a SQLite store,
  and [0002](decisions/0002-assets-and-versions.md), abstract files,
  versions and sources;
- [rendering.md](rendering.md): rendering for review and reading.

## 1. Goals and non-goals

Goals:

- **Stores at every scope**: a project, a person, a group, wider. They are
  linked, and knowledge moves between them deliberately.
- **LLM-to-LLM first.** Items are small, typed and machine-checkable, so an
  agent can reason over thousands without reading the underlying
  gigabytes.
- **Provenance everywhere.** Every claim records who said it, when and on
  what authority. Every derived thing records its inputs and can be
  checked for staleness. Every change and transfer is an event.
- **Corrections propagate.** When a fact turns out to be wrong, everything
  built on it can be found and revised or rebuilt.
- **Humans in control at the edges.** People decide what reaches wider
  audiences, and review what arrives. LLMs act within autonomy the people
  grant.

Non-goals for now:

- A server for every store. A store is a local file; shared stores have one
  writer and publish read-only snapshots.
- A data-repository platform. Large data stays where it is (EOS, Indico,
  arXiv, DataLad datasets), reached through data sources.

## 2. Architecture

```
  project store ──push (select)──►  home store  ──push (inbox / pr)──►  group store
  ./kb/kb.sqlite  ◄──pull (all)───  ~/kb/kb.sqlite                     (one writer)
                                                                           │ kb store snapshot
        each store: one SQLite file                                        ▼
        ┌───────────────────────────────────────────┐            read-only snapshot
        │ settings │ items (JSON + guards) │ blobs   │            (EOS / web) ◄── neighbors read it
        │ events (append-only) │ inbox │ reviews     │
        │ query tables: subjects, runs, refs, FTS ...│            exchange repository (git)
        └───────────────────────────────────────────┘            bundle files ◄─ pull requests
                │ renditions point at bytes through data sources
                ▼
        db (in the store) · cas (EOS/NFS content store) · url · local · datalad (DataLad)
```

The central decisions:

1. **A store is a SQLite file** (`kb.sqlite`), canonical for everything about
   its items. The JSON object validated by JSON Schema stays the item's
   contract. The database adds guards, transactions, history and fast
   queries ([decision 0001](decisions/0001-knowledge-store.md)).
2. **An item is a lightweight proxy with meaning.** It says what it is, who
   asserted it, what it applies to and where its material is. The material
   is held in *renditions*, reached through *data sources*.
3. **Layers by provenance.** An item with no `derived_from` is a *source*.
   Topics (synthesis) and views (presentation) derive from inputs and go
   stale when the inputs change.
4. **Knowledge moves in knowledge sets.** It is applied atomically, either
   directly or as a *bundle* that can be reviewed. Git and forges carry
   bundles; they are not the database.
5. **The LLM proposes, tools verify, humans approve.** Mechanical rules are
   enforced by the store (triggers) and `kb check`. Judgment is supported
   by `kb lint`, rendering and reviews. Decisions follow autonomy.

## 3. The store

A store is a directory:

```
kb.sqlite        the store (WAL mode; never write it on a network filesystem)
.kb/             this machine's: local.toml (paths), cache/, build/, checkout/
                 (a store may itself be a .kb/ directory: .kb/kb.sqlite)
content/         the store's content dataset (a DataLad dataset), if it has one here (§3)
.gitignore       kb's own entries added (kb.sqlite*, .kb/, content/), when the store sits in a git working tree
```

The policy a store enacts (CLAUDE.md, `.claude/skills/`,
`.claude/settings.json`) is written to its *target*: the store directory,
the project directory around a `./kb` store, or `~/.claude` (§16).

Tables in `kb.sqlite`:

| table | holds |
|---|---|
| `settings` | id, name, scope, schema revision, access, limits, content stores, views, links, embedding service, transforms, neighbors, policy selection, autonomy, enactment lock |
| `items` | each item's JSON, plus `digest`, `core`, `fixed` and `frozen` (guard columns) and update time and author |
| `blobs` | small content by sha256: notes, pages, extracted text, view sources, manifests |
| `events` | append-only history: create, set, content, checkin, extract, stamp, build, publish, transfer-in/out, inbox and review actions |
| `inbox`, `reviews` | received bundles; reviews by people and LLMs |
| `subjects`, `runs`, `refs`, `identifiers`, `renditions`, `fts` | query tables kept in step on every write (`fts`: a row per item head and per chunk of each text rendition) |

**The content dataset.** Large content with no other home (no content
store) goes to the store's content dataset: an ordinary DataLad dataset,
described by an ordinary `datalad` source item that the setting
`content.dataset` names, made on first need at `content/` beside
`kb.sqlite` (`content.path`, or `kb store content PATH`, puts it
elsewhere). Placement for large content is: content store, then content
dataset, then local.

Configuration is layered, last one wins:

1. the store's settings (`kb config`, `kb edge`, `kb policy`);
2. the config files: the user's `~/.config/kb/config.toml` (forges,
   user-wide neighbors), then `.kb/config.toml`, `.kb.toml` and `kb.toml`
   from the farthest directory above the working directory to the nearest
   (none of these with `--no-init-file`), then the file given with `-c`;
3. `.kb/local.toml` (this machine's paths);
4. `KB_*` environment variables.

**Finding the store.** A command uses the store of its *store context*, a
short name: `--context`, else `$KB_CONTEXT`, else `kb_context` in the config
files, else `git-directory`. `git-directory` and `current-directory` are
made into names from the nearest git working tree or the working
directory (`/home/ann/ana` gives `home-ann-ana`). The store is at
`context.NAME.location` if the config files say so, else at the nearest
`kb.sqlite`, `.kb/kb.sqlite` or `kb/kb.sqlite` here or above, else in
`$XDG_DATA_HOME/kb/contexts/NAME`. `-C` and `KB_ROOT` name the directory
outright. With no git working tree and no store above, and no name given,
`kb` stops and says how to give one. See [howto/contexts](howto/contexts.md).

**Logs.** Python logging, by default to `~/.cache/kb/logs/kb-DATE.log` from
level warning, each record with the store context's name (`-l`, `-L`,
`--debug`). Command output (data) goes to stdout and is not logged.

**Network filesystems.** SQLite's locking is unreliable on NFS, EOS FUSE,
CIFS and sshfs, so `kb` refuses to *write* a store there
(`KB_ALLOW_NETWORK_FS=1` overrides). Reading with `mode=ro` is always
allowed. Shared stores have one writer on local disk and publish snapshots
(§15.9).

## 4. Items

### 4.1 Identity and kinds

IDs are a kind prefix plus ten random Crockford base32 characters
(`obs-7k2m9q4xha`). They are global, need no allocator, and are kept when
items move between stores. Person IDs may be readable (`per-bviren`).

| kind | prefix | frozen? | holds |
|---|---|---|---|
| `observation` | `obs` | yes | an assertion: "run 1234 was noisy" |
| `document` | `doc` | yes | paper, slides, technote, thesis, minutes ... |
| `dataset` | `dat` | yes | machine-readable data with a description |
| `config` | `cfg` | yes | configuration parameters with validity |
| `snapshot` | `snp` | yes | an extract from an authoritative system |
| `software` | `sw` | yes | a code repository at a version |
| `kset` | `ks` | yes | a selection of items: the unit of transfer, or a reading list |
| `topic` | `top` | no | synthesis page citing its inputs |
| `view` | `view` | no | recipe for HTML/PDF: sources, inputs, build, manifest |
| `person` | `per` | no | identity, for attribution |
| `policy` | `pol` | once released | a named, versioned piece of LLM policy (§16) |
| `asset` | `as` | versions frozen | an abstract file or file set, its versions and the sources that supply them (§5.1) |

A **reference** is an ID, optionally with a rendition name
(`doc-3vq8r2m1kd#text`). References are **store-independent**: an item may
cite one that isn't in its store (§15.4).

### 4.2 Fields

Every item has `schema_version`, `id`, `kind`, `title`, `summary` (at most
400 characters, written for the next LLM), `created` (at, by, via) and
`access`. Optional fields include:

- `subject` and `tags`;
- `valid` (runs and time);
- `status` (draft, unverified, verified, disputed);
- `identifiers`, `creators` and `date`;
- `supersedes`, `retracts`, `derived_from`, `evidence` and `related`;
- `license`, `origin` (set on copies) and `renditions`;
- `x` (free extensions).

Kind-specific fields are in [reference/schema.md](reference/schema.md),
generated from the schemas.

### 4.3 Schema representation

The schemas are hand-written JSON Schema (draft 2020-12) in
`kb/schemas/v<N>/`:

- `common.json` holds shared definitions;
- `item.json` holds the common fields;
- there is one file per kind, closed with `unevaluatedProperties: false`,
  so invented fields fail and extensions go under `x`.

Every write validates.

### 4.4 Frozen items: append-only by construction

Sources, knowledge sets and released policies are **frozen**. Their *core*
is the fields that state what was asserted (who, when, validity, the
kind's defining fields, the members of a set ...) together with which
original/content renditions exist. The core never changes, and a recorded
content hash never changes. A missing hash may be filled in once.

The `items_frozen` trigger enforces this in the database for every writer.
Further triggers forbid deleting frozen items, and changing or deleting
blobs and events. Changes made outside kb, with raw SQL, are found by
`kb check` (`tamper`: the guard columns disagree with the JSON).

Correction is by new items: `kb item supersede` replaces, and `kb item retract`
withdraws. Superseded and retracted items stop being *current*, and
anything derived from them goes stale. What may change on a frozen item is
our relationship to it: `status`, `subject`, `tags`, `summary`, `evidence`,
`related`, `identifiers` and `license`, plus added derived renditions and
mirrors (`kb item set`, `kb rendition add|locate`).

### 4.5 Schema evolution

- Additive changes bump the schema *revision*, and items don't change.
- Breaking changes bump the major version, with a migration (`dict ->
  dict`). `kb store migrate` rewrites all items in one transaction, lifting the
  core guard inside that transaction only.
- The database layout has its own version (`PRAGMA user_version`), checked
  on open.

### 4.6 Digest

An item's digest is the SHA-256 of its canonical JSON, without `summary`,
`tags`, `related`, `evidence`, `x` and `origin`, and with only its
original, content, metadata and source renditions. Derived items pin their
inputs by digest. A copy in another store has the same digest.

## 5. Renditions and data sources

```json
{"name": "original", "role": "original", "media_type": "application/pdf", "sha256": "9f2c…", "size": 48211337,
 "locations": [{"type": "url", "url": "https://indico.example/…/slides.pdf", "retrieved": "2026-10-30T14:02:11Z"},
               {"type": "cas", "name": "default"}]}
```

**Roles:**

- `original`: as received;
- `content`: authored with the item, e.g. a note or a topic page;
- `derived`: made by a transform, with `fidelity` lossless, extracted or llm;
- `metadata`: a descriptor, e.g. `datapackage.json` or a view manifest;
- `source`: a file a view is built from.

**Location sources** (`kb.locations`) serve location types behind one interface:
resolve to a local file, describe for people, link.

| type | where | |
|---|---|---|
| `db` | in the store's `blobs` table, with a file name | small content, at most 1 MiB by default |
| `cas` | a content-addressed directory, `<store>/sha256/ab/cd/<sha>` | EOS, NFS (plain files are fine there) |
| `url` | the web, fetched into the url source directory and hash-checked | Indico, arXiv, CDS ... |
| `local` | one host only | flagged by lint |
| `datalad` | a file in a DataLad dataset, named by a source item (`src-…`), path and annex key | optional extra; [decision 0003](decisions/0003-datalad-sources.md) |

New kinds of storage (S3, an institutional repository, a native Indico
source) register a `Source` subclass. Nothing else changes.

**Source items.** A concrete source that items depend on, such as a DataLad
dataset, is itself an item (kind `source`, `src-…`): its identity (type and
the source's own ID) and where it can be had (URLs, per-host paths).
Knowledge sets always carry the source items their items need. Where a
source is on this machine (a clone, a download cache) is per-machine state,
found by the source's identity, so one clone serves every item and store;
new local copies go to the type's *source directory* (`sources.<type>.dir`,
default `~/.cache/wckb/<type>/`), or kb asks
([decision 0003](decisions/0003-datalad-sources.md)).

**Adding content.** `kb add` places small files in the store, large ones in
the content store, or records them as local when there is none. URLs are
fetched to hash them, unless `--no-fetch`. `kb add --url URL FILE`
records a local copy with its canonical URL (for SSO-protected material).
Identifiers deduplicate. `kb rendition verify` re-fetches URLs and reports changed
content upstream: a new version is a new item that supersedes the old one.

**Working with files.** `kb rendition path ID#NAME` gives a local file with the
content, with its real file name. `kb item content ID [NAME] [--file F]`
reads or replaces text on editable items. `kb item checkout ID DIR` and
`kb item checkin DIR` round-trip an item and its files through a directory.
Fields change within policy, files are added or removed, and the check
hook validates edits made there.

### 5.1 Assets: abstract files and file sets

When material has versions or several sources (a talk's materials on
Indico, on EOS as `v1/`, `v2/`, in a DataLad dataset, in DocDB), items
point to an **asset** rather than to locations. An asset has three parts:

- **Versions**, each with a label and a digest of its content manifest.
  The digest is the version's identity and never changes.
- **Bindings** to sources (`kb`, `fs`, `url`, `datalad` today; `indico` and
  `docdb` planned). Each binding has a locator and a versioning mode:
  `none`, `native`, `pattern` or `map`.
- **Resolution.** For a version, the first source in priority order whose
  content has the version's digest. Divergent copies are noted and never
  used.

`kb asset scan` classes each source's offers as known, new, conflict or
unreachable, and `--register` records new versions. Content from
unversioned sources is kept in the store by default. A rendition location
`{"type": "asset", "asset": …, "version": …, "path": …}` lets any item cite
an asset version and still pin its bytes. See
[decision 0002](decisions/0002-assets-and-versions.md).

## 6. Transforms

A transform turns one rendition into another, recording its name, version
and input hash. `kb rendition extract ID` picks one by media type and stores the
result as a derived rendition (in the store if small, otherwise the content
store). `kb rendition transforms` lists what is available here.

| from | to | fidelity | tool | status |
|---|---|---|---|---|
| PDF | text | extracted | `pdftotext` (poppler) | built in |
| PDF | markdown | extracted | PyMuPDF | built in (extra `pdf`) |
| PDF/PPTX/DOCX | markdown | extracted | docling, marker, MinerU, markitdown | configured command transforms |
| PPTX | markdown | extracted | python-pptx: titles, text, tables, notes | built in (extra `pptx`) |
| DOCX, ODT, HTML, LaTeX, org, RST | markdown | lossless/extracted | pandoc | built in |
| ROOT | JSON structure summary | extracted | uproot (TTree and RNTuple, histograms) | built in (extra `root`) |
| CSV/TSV | `datapackage.json` | metadata | `kb data describe` | built in |
| items | RO-Crate | metadata | `kb data export` | built in |
| PDF pages | thumbnails | lossless | `pdftoppm` | planned (rendering.md) |
| arXiv | LaTeX source | original | arXiv e-print API | planned |
| HDF5, Jupyter | summary, markdown | extracted | h5py, nbconvert | planned |
| figures | description | llm | an LLM with vision | a skill, not a transform |

Command transforms are store settings (`kb config set transforms.NAME
'{"command": [...], "media_types": [...]}'`), so the group can adopt a better
converter without a kb release.

**A physics caveat.** For slides, most of the information is in the plots.
Text extraction is marked `extracted`. For material that is cited often,
an LLM-written `digest` (`fidelity: llm`) describes the figures. It is
never evidence.

## 7. Derivation, staleness and building

**Topics.** A topic is an LLM-maintained page whose every factual sentence
cites an item, and whose cited items are all in `derived_from`. Its text
changes with `kb item content TOPIC --file page.md`. `kb item stamp` pins the inputs
by digest. A topic goes stale when an input changes or stops being current,
and is then revised by an LLM, never silently.

**Views.** A view's files are `source` renditions. `kb view build VIEW`:

1. materializes them in `.kb/build/<id>/work`;
2. writes the inputs to `inputs.json`;
3. runs the tool (make, snakemake, quarto, pandoc, or a command), with
   `KB_INPUTS`, `KB_OUT` and `KB_WORK` set;
4. stores a manifest (input digests, source hashes, outputs, tool version)
   as a metadata rendition.

`kb view publish` copies the output to the publish store under a version named
by the manifest hash, and logs a `publish` event. Published versions are
never overwritten.

**Staleness and the world.** `kb item stale` lists derived renditions whose
source changed, topics with changed or superseded inputs, and views whose
manifest no longer matches. `kb view build --world` re-extracts, then rebuilds
stale views in dependency order.

**Build systems.**

- Encouraged: Make for simple views, Snakemake for pipelines, Quarto for
  documents.
- Allowed: pandoc, and a command (with a reason).
- Discouraged: build systems with their own global state.

Views should pin their environment (`pixi.lock`, `uv.lock`,
`environment.yml`) as sources.

## 8. Search

- `kb ls` is a faceted listing: kind, subject prefix, overlapping runs,
  status, `--all`.
- `kb search` is BM25 over title, summary, subjects and text renditions;
  `-s` searches by meaning (an OpenAI-compatible embedding service; vectors
  per model identity, only changed text embedded); `--hybrid` fuses the
  two by reciprocal rank.
- **What is indexed is items, never whole sources.** An Indico site's
  dataset, a DataLad dataset or a filesystem tree is not indexed; the items
  that use files from them are. Each item's head (title, summary,
  subjects) is one row of `fts`, and each chunk (about 1500 characters) of
  each of its text renditions is another, with the head's words repeated
  so a query can match words of both. A hit names its rendition, and for
  extracted text the original it came from.
- **Text from what is not text.** `kb add` extracts text from PDFs, slides
  and the like with a transform (a `derived` rendition, `text`, with its
  `derived_from` and `source_sha256`). Its text is indexed wherever it is
  kept, as long as it is on this machine. When the original changes, the
  `stale` remedy re-extracts (and so re-indexes); `unextracted` catches what
  was never extracted.
- **Pages and OCR.** Text of several pages separates them with form feeds
  (PDF text, page by page), so each index row has its page and a hit names
  it. Images, and PDF pages with no text of their own, are read by OCR
  when an engine is available (`kb.ocr`: tesseract, or a configured
  command); that text has fidelity `ocr`.
- **Indexes are not precious.** `fts` is rebuilt from the items
  (`kb store reindex`; an upgrade of the database does it once). Vectors are
  a cache outside the store (`~/.cache/wckb/vectors.sqlite`), addressed by
  model identity and the hash of the text, so reindexing, re-chunking an
  unchanged text or changing model never throws vectors away. sqlite-vec
  indexes them per model when SQLite can load it. `kb store reindex
  --fetch` fetches text that is not here to index it.
- `kb context` is a compact markdown pack for LLMs: topics first, then
  current items, then history, within a character budget.
- All of these take `--reach N` / `--in NAME` to include neighbor stores
  (§15.3).

The query tables live in the store and are kept in step on every write.
`kb store reindex` rebuilds them after a repair. Datasette can be pointed at
`kb.sqlite`, or better at a snapshot of it.

## 9. FAIR

| principle | how |
|---|---|
| **F**indable | Global IDs, external identifiers (DOI, arXiv, INSPIRE, Indico), required title and summary, subjects and validity, full-text and semantic search |
| **A**ccessible | Every rendition says where its bytes are (data sources) and is verifiable by hash; an `access` level on every item; local-only content flagged |
| **I**nteroperable | JSON Schema; open formats preferred; RO-Crate 1.1 export; Frictionless data packages |
| **R**eusable | Provenance on every claim and derivation; an event history; licenses; correction chains showing what is current |

**Formats.** Preferred: text, markdown and org; CSV/TSV with a data package;
JSON/JSONL; Parquet; HDF5 or ROOT for big structured data (with a summary
rendition); LaTeX source over PDF; scripts that make figures over images.

**Access.** The levels are `public`, `collaboration`, `group` and
`private`. `kb check` rejects an item more restricted than its store, and a
derived item more open than any input. Transfers refuse to move an item to
a more open store unless it is relabeled deliberately.

## 10. LLM integration

| layer | contains | enforced by |
|---|---|---|
| JSON Schema | the shape of items | every write |
| store triggers | append-only, immutable content and history | the database |
| `kb check` | consistency, tampering, access, evidence | people, CI, the session hook |
| `kb lint` | stale items, overlaps, old unverified claims, local-only files | read by people and kb-curate |
| CLAUDE.md block | store context, the autonomy table, the stable rules | the model |
| skills | procedures, loaded only when needed | the model |
| agent hooks | check checkouts on edit, recall on each prompt, status at session start | Claude Code |
| autonomy | what the model may do unasked | the model; kb for push, pull, accept, merge and policy |

**Tooling for LLMs:**

- every listing accepts `--json`;
- `kb context` gathers in one call what would otherwise take many;
- `kb new`, `kb add` and `kb new --from-json -` write the plumbing, so the
  model supplies meaning;
- `kb show --format md|json` shows items as people see them;
- `kb log` shows what happened.

Skills: `kb-consult`, `kb-capture`, `kb-ingest`, `kb-research`, `kb-correct`, `kb-view`,
`kb-curate`, `kb-promote`, `kb-review` (inbox) and `kb-review-prs` (bundle
pull requests).

## 11. Workflows

1. **Routine.** In a project with its own store, Claude consults the store
   before answering (and the recall hook surfaces relevant items on each
   prompt). It files what gets established as it works, per `capture`
   autonomy, and marks items for a wider audience (`tags=promote`).
2. **Promotion.** At a milestone, or when asked, `kb-promote` ranks
   candidates (`kb edge candidates home`), shows a short list, and pushes
   what the human approves. From home to a group, the push lands in the
   group's inbox or as a pull request.
3. **Review.** In the receiving store, `kb-review` renders each bundle,
   judges it item by item, records a review, and leaves the decision to
   the human (or accepts, per `accept` autonomy).
4. **Consulting** from anywhere: `kb context ... --reach 2`.
5. **Correcting:** supersede or retract, then follow `kb item stale`. A
   correction of something already pushed becomes the top promotion
   candidate.
6. **Views:** create one, check out and add its sources, build, publish.

## 12. Scale

SQLite handles millions of rows. A store of tens of thousands of items with
their small content is a few hundred megabytes. Large content stays outside
(content stores, URLs, DataLad). Blobs and events are never deleted, and
when compaction becomes necessary is question S6. High-volume series belong
in one dataset per period, not in thousands of observations.

## 13. Implementation language

`kb` is written in Python, because the group uses it and the ecosystem
exists: jsonschema, PyMuPDF, python-pptx, uproot, and SQLite in the
standard library. Imports are lazy, and commands start in about 0.1 s. The
contract is JSON Schema plus SQLite, both language-neutral.

## 14. Open questions

See [questions.md](questions.md).

## 15. Federation

### 15.1 Scopes

`kb init --scope project|home|group|org|other` labels a store and sets its
defaults (access, policy, autonomy). The labels are descriptive; behavior
comes from edges.

| scope | typical location | access | push autonomy | accept autonomy |
|---|---|---|---|---|
| project | `<project>/kb/` | private | ask | ask |
| home | `~/kb` | private | ask | ask |
| group, org | a maintainer's or service's local disk | group, collaboration | never | never |

### 15.2 Edges

A neighbor is an edge from this store, with a relation (`up`, `down`,
`peer`, `twin`) and a behavior for each direction. Every default can be
overridden per edge.

| relation | push (here → there) | pull (there → here) | pulled items land | conflicts in mutable fields |
|---|---|---|---|---|
| `up` | `select` | `all` | applied | theirs |
| `down` | `all` | `select` | applied | ours |
| `peer` | `select` | `select` | in the inbox | ours |
| `twin` | sync | sync | applied | newer |

The forms are `select` (an explicit knowledge set), `all` (every new or
changed item) and `none`. `land` says how *pushes* arrive at the
neighbor:

- `apply`: straight in;
- `inbox`: a bundle the receiver reviews;
- `pr`: a bundle proposed by pull request in its exchange repository.

A neighbor is found by `path` (its store directory here, needed for
writing) or `url` (a published snapshot, downloaded by `kb edge
fetch`). Shared declarations go in the store's settings, and machine paths
in `.kb/local.toml`. `[neighbors.*]` in the user's config applies to
every store, which is how `~/kb` becomes every project's `up`.

### 15.3 Reading across stores

`--reach N` follows `up` and `peer` edges N hops, and `--in NAME` names a
neighbor. Results are labelled by store, and an item held in several
stores shows once, with "also in". Neighbors are opened read-only, with
scratch files in the user cache.

### 15.4 References and obtaining

References are global IDs and may dangle: `kb check` warns, and `kb pull
ID…|--dangling` pulls them from the nearest reachable store that has them.
`kb new` still refuses a reference found nowhere (a likely typo) unless
`--allow-dangling` is given.

### 15.5 Knowledge sets

A `kset` has members, a `depth`, a `nested` rule and a `follow` list:

- `shallow` depth: the members;
- `deep` depth: the members and everything they reference, recursively;
- `nested`: whether a nested set uses its own depth (`follow`) or the outer
  one (`inherit`);
- `follow`: the reference fields a deep descent takes.

Sets are frozen and take the access of their most restricted member.
`kb kset new`, `kb kset expand` and `kb show KSET` (a tree).

### 15.6 Transfers

`kb push` and `kb pull` expand a set and apply it to the
receiver in **one transaction**. The receiver's checks must pass, or
nothing changes. Copies keep their ID and get an `origin`: the last
sender, the `path` of store IDs travelled, the set, when, by whom, and who
approved. Both stores log `transfer-out` and `transfer-in` events
(`kb edge ledger`).

### 15.7 Arrival along several paths

- The **core** must be identical: a difference is a conflict, and the whole
  transfer is refused.
- **Lists** are united.
- **Single values** take the incoming value from the same sender as before
  (an update), when the edge prefers theirs, or, between twins, when it is
  newer. Otherwise the receiver keeps its own, and the disagreement is
  reported.
- **Editable items** are replaced by the same sender, and otherwise
  conflict.

### 15.8 Promotion

`kb edge candidates N` ranks corrections of items already sent, items
marked `promote`, verified and well-described items, and items changed
since they were sent. It shows blockers (drafts, local-only content, access
more restricted than the target) and leaves out `no-promote`, items that
came from the target, and sets.

### 15.9 Shared stores, snapshots and twins

A group store has one writer: a maintainer, or a job applying accepted
bundles. Members read a **snapshot** (`kb store snapshot DEST`, a consistent
`VACUUM INTO` copy) published on EOS or the web, and reach it by URL.
Copying `kb.sqlite` itself is not safe while it is in use.

**Twins** are copies of one store (same id), such as a home store on two
machines. `kb sync` moves new and changed items both ways, and the newer
change wins.

## 16. Policy

Policy elements are named and versioned:

- instructions (CLAUDE.md sections);
- skills;
- hooks (Claude Code settings; the command must be `kb hook ...`);
- settings (reserved).

Builtins ship in the package, and stores add their own as `policy` items:
drafts are editable, releases are frozen, and a new version supersedes the
old one. Resolution searches this store, then reachable stores, then the
builtins. A group publishes policies, and members adopt them by name.

The store's settings hold the selection (`NAME`, `NAME@N`, `NAME@draft`),
the target and the autonomy. `kb policy enact`:

- writes a generated block into CLAUDE.md (store context, the autonomy
  table, the selected instructions), leaving the rest of the file alone;
- writes the skills;
- writes the kb-managed hooks;
- records everything in the enactment lock.

`kb policy status` reports drift, and `kb policy capture` turns hand edits
of enacted text into a new draft version.

## 17. Autonomy

The human grants per-action levels `never`, `ask` and `auto` (`kb policy
autonomy`; agents may not change them). The table is rendered into
CLAUDE.md.

| action | meaning | enforced by kb |
|---|---|---|
| capture | file new items here unprompted | no (instructions) |
| review | start a promotion review unprompted | no |
| curate | revise topics and summaries | no |
| policy | change selection, release, deprecate | yes |
| push (per neighbor) | send items | yes |
| pull | receive items into this store | yes |
| accept | accept inbox bundles (reviewing is always allowed) | yes |
| merge | merge or close forge pull requests | yes |

`kb` detects an agent from `CLAUDECODE`, `AI_AGENT` or `KB_AGENT`:

- `never` refuses;
- `ask` needs `--approved "<the human's words>"`, which is recorded in
  events and in copies' `origin`;
- `auto` proceeds and is recorded.

This is a guardrail and an audit trail, not a security boundary.

## 18. Bundles, inbox, review and rendering

A **bundle** is a knowledge set, its items, their small content and the
people they mention, in one self-checking JSON file named by its hash
(`kb bundle make|show|check|apply`).

The **inbox** holds received bundles (`kb inbox list|receive|import`).
Reviews from people and LLMs are recorded with the reviewer's kind
(`kb inbox review`). `kb inbox accept` applies a bundle in one transaction,
and `kb inbox reject` records why not.

**Rendering** turns items, sets and bundles into a view model for people,
with three formatters (text, markdown, JSON):

- names, dates and run ranges;
- links;
- where each item's material is;
- excerpts of the material;
- the set as a tree;
- what accepting would do;
- field-level changes and conflicts;
- no hashes.

Planned: a TUI, a web view and slide thumbnails. See
[rendering.md](rendering.md).

## 19. Git, exchange repositories, forges and secrets

- **Exchange repositories** are git repositories of bundle files
  (`kb exchange init`). `kb push --land pr` commits a bundle on a branch
  (through a temporary worktree), pushes it, and opens a pull request whose
  description is the bundle rendered against the receiver. After the
  merge, `kb inbox receive EXCHANGE --accept` brings it in. Bundles never
  conflict, so this use of git never merges content.
- **Git hooks** (`kb exchange hooks install`) guard exchange repositories:
  added bundles must verify, and existing bundles are immutable (enforced
  on pre-commit and pre-push).
- **Forges:** `kb forge pr create|list|show|comment|merge|close` speaks to
  GitHub, Gitea and Forgejo (Gogs has no pull-request API). The forge is
  matched to the repository by its remote's host.
- **Secrets** are given as specs:
  - `cmd:pass show NAME` (recommended);
  - `file:PATH` (warned about if more open than 0600);
  - `env:VAR`;
  - `value:...` (with a warning).

  `kb forge add` refuses to write a literal token, and `kb forge auth`
  explains token creation and tests the token. Tokens are resolved inside
  kb and never shown to the model.
