# 0001: Where knowledge items live: git + JSON files, or a database

- **Status:** **accepted** 2026-10-31. Spikes S1 and S2 are done (see §13);
  S3 (DataLad) is deferred, S4 (twins) is partly done.
- **Date:** 2026-10-31
- **Terminology note:** written before the [glossary](../glossary.md);
  "record" here means *item*, "source record" means *primary item*, and
  "blob store" means *content store* (cas).
- **Decides:** the canonical store for knowledge-item metadata (the
  JSON objects `kb` calls records), and, with it, the role of git, the
  transport for transfers, and how content files are held.
- **Does not decide:** the record schema (JSON Schema stays the contract for
  an item in every option), the CLI, or policy semantics.

## 1. The question

Today each knowledge item is a JSON file at `records/YYYY/MM/<id>/record.json`
in a git repository. Small content files sit next to it. A SQLite index is
rebuilt from the files for search.

Since that choice was made, three things changed:

1. **Items are only touched through `kb`.** The CLI creates, edits
   (within policy), validates and moves them. Nobody needs to open a JSON
   file, which was a large part of the argument for files.
2. **Knowledge moves selectively, by knowledge sets.** Transfers copy chosen
   items between stores that have *different* histories. Git is built to
   replicate one history, so every selective copy works *around* it:
   committed-only reads, temporary worktrees, ledgers that need
   union-merge attributes.
3. **Content can be delegated to DataLad.** If raw and derived files live
   in DataLad datasets (git + git-annex, with special remotes for EOS, the
   web, Indico ...), the KB no longer needs to be a repository of files.
   What's left in git is just the metadata.

Should the metadata stay as git + JSON files, or move to a database?

## 2. What the store must do

Collected from the design, how-tos and questions so far:

| # | requirement | weight |
|---|---|---|
| R1 | Fast, frequent single-writer use by one person and their LLM: create, set, query, search | high |
| R2 | Each item is a JSON object validated by JSON Schema; schema evolution with migrations | high |
| R3 | Sources append-only: what was asserted never changes; corrections supersede | high |
| R4 | History and audit: who changed what, when, by whose approval | high |
| R5 | Selective, atomic transfer of knowledge sets along a graph of stores; arrival along several paths; conflicts detected | high |
| R6 | Shared group stores with many contributors and a human review gate | high |
| R7 | One person's store on several machines or accounts ("twins") | medium |
| R8 | Offline work; backups | medium |
| R9 | Readability for review: a human or LLM must be able to see what a change *is* | medium |
| R10 | Low infrastructure: no servers to run for personal use; little for a group | medium |
| R11 | Content files (raw, derived, view sources and products) held by hash, possibly external | high (but see §6: may go to DataLad) |
| R12 | Portability: export to JSON/RO-Crate; no lock-in | medium |

## 3. What git + JSON costs today, concretely

This comes from the code, not from hypotheticals:

- **Identity is a path.** Records are sharded into `records/YYYY/MM/<id>/` to
  keep directories small. The path means nothing, yet it has rules of its
  own: a directory must match its ID, orphan directories are flagged, and
  stray files are flagged (`checks._check_files`, about 50 lines).
- **Two sources of truth.** The files are canonical and the SQLite index is
  derived. The index has to be kept fresh (`index.ensure` compares mtimes
  and counts), and every query path guards against staleness.
- **Append-only is enforced by diffing git revisions.** `check_immutability`
  lists records at a base ref, reads each old blob, *migrates* it to the
  current schema, and compares fields and rendition hashes. It is correct,
  but indirect: it only works where git history and a sensible base ref
  exist, and it fails open without them (no commits, no repository).
- **Selective transfer fights git.**
  - A transfer may read only *committed* items (`require_committed`).
  - It lands in another repository as a new commit.
  - For review, it builds a branch in a temporary `git worktree` so the
    receiver's checkout isn't disturbed (`_land_on_branch`).
  - Ledgers rely on `.gitattributes` `merge=union`, which a user's global
    ignore can silently defeat (question G7, found on this machine).
  - About 22 of transfer.py's references are to directories and git.
- **Twins are git merges of *mutable* JSON.** Concurrent edits of one
  record's summary on two machines become textual merge conflicts in
  `record.json`, which a person must resolve by hand.
- **Scale is fine but awkward.** Tens of thousands of tiny files are fine
  for git, but slow to scan, walk and re-hash (`kb check` re-hashes every
  in-git rendition).
- **Migrations rewrite every file.** That is reviewable, but it's a
  repository-wide diff.

What git + JSON *buys*, which any replacement must answer:

| benefit | used by |
|---|---|
| review of changes as diffs, merge requests, CI gates | R6, R9 |
| complete history and blame for free | R4 |
| replication, offline, backup (`clone`, `pull`, `push`) | R7, R8 |
| zero infrastructure, transparent files, robustness | R10, R12 |
| forges (GitHub, Gitea, Forgejo) as the collaboration surface | R6 |

## 4. Options

### A. Keep git + JSON (status quo)

Described above. Its strengths are review, history and replication. Its
weaknesses fall on exactly what the KB has become: an LLM-driven,
high-frequency, CLI-mediated store that moves selected items between
differently-shaped histories.

### B. SQLite canonical per store; transfers as bundles; content in DataLad

Each store is one SQLite database (WAL mode) on local disk.

- **Items.** A table of items: the full JSON object, validated by the same
  JSON Schema, plus columns extracted for querying (id, kind, status,
  access, subject, validity ...). The ID is the primary key, with no paths.
  JSON stays the contract: `kb show --json`, the export formats and the
  schemas are unchanged.
- **Events.** An append-only table records every create, set, supersede,
  retract, transfer in and transfer out, with a hybrid logical clock
  timestamp, the device and store, who did it and the approval. History,
  audit, `kb log ID` and point-in-time views come from it (R4). Ledgers
  become queries.
- **Append-only by construction (R3).** The API refuses to change a
  source's core, and SQL triggers raise if anything tries (`BEFORE UPDATE`
  on core columns of source kinds). This doesn't depend on git history
  existing.
- **Small content** (notes, topic pages, extracted text, under the inline
  limit) goes in a content-addressed blob table keyed by sha256. **Large,
  raw and derived content** goes to DataLad (§6).
- **Transfers** expand a knowledge set and apply it in **one transaction**
  (R5). The multi-path reconciliation already designed is kept, and gets
  better: with event clocks, "which value is newer" has an answer (B15).
- **Bundles** are a portable transfer artifact: one JSON file holding the
  knowledge set, its items, small blobs, references to large content,
  provenance and checksums. Between local stores, kb goes DB to DB
  directly. Across people and machines, the bundle is what travels.
- **Shared group stores (R6)** take bundles into an **inbox**.
  - The review gate is either `kb review` (list, show a readable rendering,
    accept or reject), or a git or forge repository *of bundle files* with
    pull requests and CI running `kb bundle check`.
  - Bundles are immutable, uniquely named files, so this git use never
    merges or conflicts: git as transport and review, not as a database.
  - Accepted bundles are applied to the group DB by its maintainer or a CI
    job (one writer). Members read the group store through a published
    read-only snapshot (a copy of the DB on EOS or a web server), or by
    pulling bundles.
- **Twins (R7)** become bidirectional `all` transfers between DBs,
  reconciled by event clocks, with no textual merges. A twin pair can still
  exchange bundles through a shared git repository when the machines can't
  see each other.
- **Backup (R8)**: `VACUUM INTO` / the online backup API, and optionally a
  periodic `kb export` to JSON Lines. The export can be committed to git as
  a human-readable, greppable, diffable archive: git's transparency without
  git being the store.
- **Settings.** Store configuration, neighbors and policy selection move
  into the DB (`kb config`, `kb neighbor`, `kb policy`). Machine-local
  paths stay in a local TOML file.

### C. A server RDBMS (PostgreSQL) for shared stores

This is option B's data model on PostgreSQL, for a group that needs
*concurrent online writers* with row-level access control.

It costs a server, authentication and backups, and it loses offline access
to the shared store. It's not needed while a group store has one writer
applying reviewed bundles. Keeping the SQL portable, behind one repository
layer, keeps it available later.

### D. Dolt (a SQL database with git semantics)

Branch, diff, merge, clone, push and pull on tables. It would keep
review-by-merge on structured data.

Against it:

- it's a large external binary (Go) with its own server and remote model;
- merges are cell-level, which still doesn't solve *selective* copy between
  stores with different histories, the main pain point;
- the community and tooling are much smaller than SQLite's.

Interesting, but it trades our problem for a dependency.

### E. git-annex or DataLad metadata as the item store

git-annex metadata and `datalad-metalad` attach key/value or JSON-LD
metadata to files. That's designed to describe *files*, not first-class
knowledge items such as observations, topics or sets that may have no
file at all. Rejected as the item store. It may be used for DataLad's own
content description.

## 5. Evaluation

Scores run from 0 to 3; higher is better.

| requirement | A git+JSON | B SQLite + bundles + DataLad | C Postgres (shared) | D Dolt |
|---|---|---|---|---|
| R1 fast single-writer use | 1 (files + index rebuild) | **3** | 2 (network) | 2 |
| R2 schema and migrations | 2 (rewrite files) | **3** (transactional migration) | 3 | 2 |
| R3 append-only | 2 (git diff, fails open) | **3** (API + triggers) | 3 | 2 |
| R4 history and audit | **3** (git log) | 3 (event table; must build) | 3 | 3 |
| R5 selective transfer, multi-path | 1 (worktrees, committed-only) | **3** (transactions, clocks) | 3 | 1 |
| R6 shared stores with review | **3** (merge requests) | 2 (inbox; merge requests of bundles) | 2 (needs a review UI) | 3 |
| R7 twins | 2 (textual merges) | **2** (clocked sync; must build) | n/a | 3 |
| R8 offline, backup | **3** | 3 | 1 | 3 |
| R9 readable review | **3** (JSON diffs) | 2 (rendered bundles, export) | 1 | 2 |
| R10 low infrastructure | **3** | **3** | 1 | 2 |
| R11 content files | 2 (git + blob store) | **3** (DataLad) | 2 | 1 |
| R12 portability | 3 | 3 (JSON export, same schema) | 3 | 2 |
| **weighted fit** | medium | **highest** | for later, if needed | low |

The scores reward B where the KB has moved (R1, R3, R5, R11). Where B scores
lower (R6, R9), the gap is narrowed by using git where git is strong: as a
transport and review surface for *immutable bundle files*.

## 6. DataLad's place

DataLad (git + git-annex) is a good fit for the *bytes*:

- **Content addressing matches.** git-annex `SHA256E` keys embed the
  SHA-256 that `kb` already records per rendition, so a rendition maps to an
  annex key with no new identifier.
- **External stores without copies.** Special remotes cover the web
  (arXiv, public URLs), S3, rclone (EOS via WebDAV), RIA stores on shared
  filesystems, and, through datalad-next's `uncurl` remote and credential
  framework, authenticated HTTP. That is the likely route to Indico
  attachments, if Indico accepts API tokens for downloads. This needs a
  spike. `datalad get` and `datalad drop` move content on demand.
- **Provenance.** `datalad run` and `rerun` record the command, inputs and
  outputs of a computation. That overlaps with our transform records and
  view manifests and may replace parts of them.
- **Nesting.** Subdatasets could mirror the store graph (project datasets
  inside a home dataset), or not. To investigate.

With content in DataLad, a rendition location becomes
`{"type": "annex", "dataset": "<datalad dataset id>", "path": "...", "key": "SHA256E-s…--<sha256>.pdf"}`
next to the existing `url`, `local` and small in-DB `blob` locations. The
current `git` (file next to record.json) and `blob` (our own
content-addressed store) location types are retired.

**DataLad doesn't settle the item-store question by itself.** It is git
underneath, so it could hold JSON items. But items in a DataLad dataset
inherit every cost in §3. The clean division of labor is this: DataLad owns
files (raw, derived, view sources and products), and the KB owns knowledge
items (metadata, semantics, sets, transfers, policy). The two meet at
rendition locations.

View sources (Makefiles, scripts, Quarto documents) are files that tools
build, so they belong in a DataLad dataset per store, and `datalad run`
records the builds.

## 7. Policy content

As suggested:

- Builtin policies (instructions, skills, hooks) ship *in the wckb package*,
  versioned with it.
- Group or personal policies stay policy items, in the DB, with their text
  in the blob table. They're transferred like any knowledge (a group
  publishes a policy, and members pull it).
- Selection, autonomy and the enactment lock move from `policy.toml` and
  `policy.lock.json` into the DB.
- Enactment is unchanged: it writes CLAUDE.md, skills and hooks.

## 8. Recommendation

**Adopt B:**

- a SQLite database is the canonical store of knowledge items in every
  store;
- an event table provides history and clocks;
- small content lives in a blob table, large and derived content in
  DataLad;
- transfers apply in transactions, and travel as bundles between people;
- git and forges remain as transport and review for bundles (group inboxes)
  and policy development, not as the item database.

Design the SQL layer so PostgreSQL (option C) can be swapped in for a group
that needs concurrent online writers.

Reasons, briefly:

1. The KB's center of gravity is now an LLM writing and querying through a
   CLI, and moving *selected* knowledge between stores. A database does
   both natively; git does neither.
2. Append-only and audit become properties of the store, enforced
   everywhere, rather than properties of a diff against a base ref that may
   not exist.
3. Selective transfer becomes a transaction, and the many-paths problem
   gets real clocks (B15) instead of the "same sender" heuristic.
4. What git does best (immutable artifacts, review, distribution) is kept,
   and used where it's strong: bundles, policy and DataLad content.
5. Now is the cheapest time. There's no baseline commit, no data and no
   users yet.

**What we give up, and how it's covered:**

| lost | mitigation |
|---|---|
| reviewing raw JSON diffs | `kb bundle show` renders a bundle as readable markdown (items, changes, provenance); merge requests of bundle files show that rendering; `kb diff` between two states of an item from events |
| `git log` / `blame` for free | the events table; `kb log ID`, `kb log --since` |
| editing a file by hand | `kb edit ID` opens the JSON in `$EDITOR` and validates on save (policy still applies) |
| grep over files | `kb search`; `kb export --jsonl`; Datasette on the DB |
| `git clone` of a whole store | `kb clone` (copy of the DB file or a snapshot); bundles for increments |

## 9. Consequences for the code

Most of the public API and CLI stay. The storage layer under them changes.

| module | change |
|---|---|
| `records` | becomes a repository over SQLite: items, events, blobs; `Record.dir` goes away; JSON objects unchanged |
| `schema`, `authoring`, `edit`, `context`, `search`, `lint`, `fair` | mostly unchanged APIs; storage calls swap |
| `index` | merged into the DB (FTS5 in the same file); no rebuild step |
| `checks` | the per-item rules stay. Git diffing is replaced by triggers and API guards. `check` becomes a consistency scan plus `kb bundle check` |
| `transfer`, `push` | simplify: transactions instead of worktrees and commits; bundles added; ledgers become event queries |
| `sync` | twins become clocked bidirectional transfers |
| `store`, `transforms`, `build` | renditions point to the blob table or DataLad; view sources live in a DataLad dataset; `datalad run` is evaluated for builds |
| `githooks` | apply to bundle and policy repositories, not item stores |
| `policy` | builtins from the package; selection, autonomy and the lock in the DB |
| `federation`, `forge`, `secrets`, `hooks` | unchanged, except where neighbors are stored |
| tests | fixtures change (no git per store); most behavioral tests carry over |

Rough size: one to two weeks of focused work for the storage swap, bundles,
events and inbox, plus a DataLad spike in parallel. It's cheaper now than at
any later point.

## 10. Risks

| risk | mitigation |
|---|---|
| The SQLite file is a single point of failure | WAL mode, the online backup API, a scheduled `kb export` to JSONL (optionally committed to git), snapshots kept beside DataLad content |
| SQLite on a network filesystem (EOS, NFS) corrupts under concurrent writers | Never *write* a DB on a network filesystem. Shared stores take bundles into one writer, and members read published snapshots. `kb` refuses to open for writing on a network filesystem |
| Group review becomes less familiar than merge requests | Keep merge requests, *of bundles*, with a rendered summary and CI. `kb review` for groups without a forge |
| DataLad adds a dependency (git-annex binary) | Only needed for large or external content. Small-content stores work without it. Conda, Debian and Homebrew packages exist |
| Indico access through DataLad is unproven | Spike S3 below. The fallback is the current URL plus hash plus cache |
| Event clocks and sync are new code to get right | Hybrid logical clocks are well understood. Property tests over random concurrent edits |

## 11. Before deciding: spikes

Each is small and answers one question:

| spike | question | done when |
|---|---|---|
| **S1. SQLite store** | Does the item/event/blob schema with triggers carry today's tests? | Items, events and blobs tables; append-only triggers; FTS5; the authoring, check, search and context tests pass on it |
| **S2. Bundles and inbox** | Is group review over bundles acceptable to people? | Bundle format and checksum; `kb bundle make/show/check/apply`; `kb review`; a forge merge request of a bundle file shows a readable summary |
| **S3. DataLad content** | Can DataLad hold renditions, including Indico and EOS? | A dataset with `addurls` from arXiv and Indico (token auth via datalad-next credentials, if Indico allows it); an EOS remote via rclone/WebDAV; get/drop; key ↔ sha256 mapping; `datalad run` for one view build |
| **S4. Twins with clocks** | Is clocked bidirectional sync predictable? | Two DBs with concurrent edits; deterministic reconciliation; property tests |

**Proposed decision rule:** if S1 and S2 succeed, adopt B and baseline on
it. S3 decides whether content goes to DataLad now or stays on the current
URL/blob mechanism for the pilot. S4 can follow the baseline.

## 12. Questions this raises

These are added to [questions.md](../questions.md), section S:

- S1: Where does a project's DB live? `proj/.kb/kb.sqlite` (ignored by the
  project's git), with knowledge reaching the project's collaborators by
  bundles. Or should a JSONL export be committed with the code?
- S2: Who is a group store's single writer: a maintainer, a CI job or a
  small service? How is the read-only snapshot published?
- S3: Should bundles be signed (minisign/ssh signatures) so the inbox can
  verify who sent what?
- S4: One DataLad dataset per store, or one per project or collection?
  How does the dataset graph relate to the store graph?
- S5: Can `datalad run` replace our view manifests and transform records,
  or should it complement them?

## References

- DataLad: datalad-next credentials and the `uncurl` special remote for
  authenticated URL access:
  [datalad-next](https://github.com/christian-monch/datalad-next),
  [PyPI](https://pypi.python.org/pypi/datalad-next/),
  [credential properties](https://docs.datalad.org/projects/next/en/latest/generated/generated/datalad_next.utils.get_specialremote_credential_properties.html),
  [uncurl walkthrough](https://knowledge-base.psychoinformatics.de/kbi/0028/index.html).
- SQLite session extension (changesets with conflict handlers, a possible
  basis for twin sync; exposed in Python by APSW, not by the stdlib
  `sqlite3`): [APSW session tour](https://rogerbinns.github.io/apsw/example-session.html),
  [sqlite3changeset_apply](https://sqlite.org/session/sqlite3changeset_apply.html).

## 13. Outcome (2026-10-31)

**Decision:** option B. Knowledge items live in a SQLite store. Spikes S1
and S2 were carried out in the code itself, since no store existed yet and
nothing needed migrating.

### S1: the SQLite store

- One file, `kb.sqlite`, per store (`kb.db`). It holds:
  - `settings`;
  - `items` (the JSON plus guard columns `core`, `fixed` and `frozen`);
  - `blobs` (small content, by sha256);
  - append-only `events`;
  - `inbox` and `reviews`;
  - derived query tables kept in step on every write (subjects, runs, refs,
    identifiers, renditions, FTS5, vectors).
- **Guards in the database.** Triggers refuse any change to a frozen item's
  core (sources, released policies), and any change to a recorded content
  hash. A missing hash may be filled in once (`kb verify --record`). The
  triggers also refuse deleting frozen items and changing or deleting
  blobs and events. A migration lifts the core guard inside its own
  transaction. Anything changed outside kb (raw SQL) is caught by
  `kb check` as `tamper`.
- **Writes are transactions.** An item, its content and its event commit
  together. Transfers apply a whole knowledge set in one transaction.
- **Writing on a network filesystem is refused** (NFS, EOS FUSE, CIFS,
  sshfs, ...). `KB_ALLOW_NETWORK_FS=1` overrides. Read-only opening
  (`mode=ro`) is always allowed.
- **Files when files are natural.** `kb checkout ID DIR` / `kb checkin DIR`
  (and `kb edit`) round-trip an item and its content through a directory,
  within policy. The Claude check hook validates edits in such a directory.
  View sources are `source` renditions edited this way.
- **Snapshots.** `kb snapshot DEST` (`VACUUM INTO`) writes a consistent
  single file to publish read-only or keep as a backup. Copying
  `kb.sqlite` alone is not safe while the store is in use, because recent
  writes sit in the WAL file. Neighbors reached by URL are such snapshots.
- **Settings in the store:** `kb config` and `kb neighbor`, and `kb policy`
  for policy selection, autonomy and the enactment lock. Per-machine paths
  stay in `.kb/local.toml`.

### Data sources: a polymorphic interface

Where a rendition's bytes are is the business of `kb.sources`. Each
location type is served by a `Source` that resolves it to a local file,
describes it for people, and offers a link. Types now:

- `db` (in the store, materialized with its file name);
- `cas` (a content-addressed directory store; formerly `blob`);
- `url`;
- `local`;
- `annex` (a DataLad dataset). Experimental: a clone mapped through the
  setting `datasets.<id>.path`, fetched with `datalad get`.

New sources (Indico, S3, an institutional repository) are added by
registering a class. S3, the DataLad spike, is deferred until we know
DataLad better. Nothing else in kb depends on which sources exist.

### S2: bundles, inbox, review, rendering

- **Bundles** (`kb.bundle`): a knowledge set, its items and their small
  content, in one self-checking JSON file named by its own hash. Bundles
  also carry the person items they mention, as rendering context only.
- **Inbox and reviews.** `kb push --land inbox` (or `kb inbox receive`,
  `kb inbox import`, or a pull from an edge with `accept = review`) queues
  a bundle. `kb review note` records reviews from people and LLMs, with
  the reviewer's kind stored. `kb review accept` applies the bundle in one
  transaction; `kb review reject` records why not. Agents may accept only
  per the new `accept` autonomy (`never` in group stores, `ask` in home and
  project stores).
- **Exchange repositories** (`kb exchange init`): git repositories of
  bundle files. `kb push --land pr` proposes a bundle by pull request,
  whose description is the bundle rendered against the receiver. After the
  merge, `kb inbox import EXCHANGE --accept` brings it in. The git hooks
  now guard bundle files: they must verify, and they are immutable.
- **Rendering for people.** See [rendering.md](../rendering.md). There is a
  view model with three formatters (text, markdown, JSON). It shows names,
  dates, run ranges, links, where the material is, excerpts, a set tree,
  plan and conflicts, and field-level changes, with no hashes. A TUI, a
  web view and slide thumbnails are planned there.

### S4 (partly): twins

Twins are copies of one store (same id), synced item by item in both
directions by `kb sync`. When both sides changed an item, the later change
wins (`prefer = newer`, by update time). Proper clocks remain question B15.

### What git does now

- Exchange repositories: transport and review for bundles.
- Policy development (the wckb repository itself).
- Forges, for pull requests.

Stores are not git repositories. A store inside a project's working tree
gets a `.gitignore` of `*`.
