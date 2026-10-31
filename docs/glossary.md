# Glossary

The words kb uses, what they mean, and their names in the code. When a
document or a command's help disagrees with this page, this page is meant
to be right; please fix the other place.

**Conventions.**

- *Code* gives the module, class, field or command that implements a term.
- Field names are as they appear in an item's JSON.
- "kb" in lower case is the tool, "KB" the knowledge base.

## The big picture

**knowledge base (KB)**
: The whole system: the linked stores of a group and its members, plus the
  people and LLMs using them. There is no single "the KB" file; there are
  stores.

**store**
: One knowledge store: a directory holding the file `kb.sqlite` (canonical),
  plus `.kb/` for this machine's settings, cache and build area. Each store
  has a scope label, settings, items, content, history and an inbox.
  *Code:* `kb.items.Store` (usually held in a variable named `kb`),
  `kb.db` (`DB_NAME = "kb.sqlite"`, schema, guards), `kb init`.

**store ID**
: A store's random identity, `kb-` plus ten characters. Copies of one store
  (twins) share it. *Code:* setting `kb.id`, `cfg.id`.

**scope**
: A descriptive label given at `kb init` (`project`, `home`, `group`, `org`,
  `other`) that picks defaults for access, policy and autonomy. Behavior
  comes from edges, not from the label. *Code:* setting `kb.scope`.

**settings**
: A store's configuration, held in the store (`settings` table). It is
  overlaid by the user's `~/.config/kb/config.toml`, the machine's
  `.kb/local.toml` and `KB_*` environment variables. *Code:* `kb config`,
  `kb.config.from_settings`, `Store.setting/set_setting`.

**snapshot**
: A consistent single-file copy of a store (`VACUUM INTO`). It is used to
  publish a store read-only, or as a backup. *Code:* `kb store snapshot`,
  `Store.snapshot`.

## Items

**item** (knowledge item)
: The unit of knowledge: a JSON object of one *kind*, validated by JSON
  Schema, held in the store's `items` table. (Older text says "record"; it
  means item.) *Code:* `kb.items.Item`, `Store.items`.

**kind**
: The type of an item, which selects its schema: `observation`,
  `document`, `dataset`, `config`, `snapshot`, `software`, `kset`, `asset`,
  `source`, `topic`, `view`, `person`, `policy`. *Code:* `kb.ids.KINDS`,
  `schemas/v1/<kind>.json`.

**primary item**
: An item that states something as received or asserted: `observation`,
  `document`, `dataset`, `config`, `snapshot`, `software`, and `kset`
  selections. Primary items are frozen. They are corrected by superseding,
  never by editing. *Code:* `kb.ids.PRIMARY_KINDS`, `Item.is_primary`.

**derived item**
: An item built from inputs (`derived_from`): a `topic` (synthesis) or a
  `view` (presentation). Derived items are editable and go stale when
  their inputs change.

**frozen**
: Cannot change what it asserts. Frozen items are primary items, released
  or deprecated policies, the registered versions of assets, and the
  identity of source items. The
  database enforces this. *Code:* `kb.items.is_frozen`, the `items_frozen`
  trigger in `kb.db.GUARD`.

**core**
: The part of a frozen item that never changes: the fields in
  `IMMUTABLE` (and `POLICY_FROZEN`) plus which original and content
  renditions exist. *Code:* `kb.items.core`, the `core` column.

**fixed hashes**
: Content hashes that never change once recorded: those of original and
  content renditions, and, for assets, the digest of each version. A
  missing one may be filled in once. *Code:* `kb.items.fixed`, the `fixed`
  column.

**mutable fields**
: What may change on a frozen item, because it describes our relationship
  to it rather than what it asserts: `status`, `subject`, `tags`,
  `summary`, `evidence`, `related`, `identifiers`, `license` (plus added
  derived renditions and mirrors). *Code:* `kb item set`, `kb.transfer.MUTABLE`.

**current / superseded / retracted**
: An item is *current* unless another item names it in `supersedes`
  (replaced) or `retracts` (withdrawn). *Code:* `Store.is_current`,
  `Store.superseded_by`, `kb item supersede`, `kb item retract`.

**status**
: Our confidence in an item's content: `draft`, `unverified`, `verified`
  (needs `evidence`), `disputed`.

**access**
: Who may see an item, from most open to most closed: `public`,
  `collaboration`, `group`, `private`. A store also has an access level, and
  items may not be more restricted than their store.

**validity** (`valid`)
: What an item's content applies to: run ranges and/or a time window.

**subject**
: Slash-separated topic paths (`detector/tpc/noise`), searched by prefix.

**origin**
: Set on an item copied from another store: the last sender (`store`,
  `name`, `scope`), the `path` of store IDs it travelled, the knowledge `set`
  that carried it, when, by whom, and who approved.

## Identifiers and references

**ID** (item ID)
: A kind prefix plus ten random Crockford base32 characters, e.g.
  `obs-7k2m9q4xha`. Global: kept when items move between stores. Persons
  may use readable IDs (`per-bviren`).

  | prefix | kind | prefix | kind |
  |---|---|---|---|
  | `obs` | observation | `ks` | kset |
  | `doc` | document | `as` | asset |
  | `dat` | dataset | `top` | topic |
  | `cfg` | config | `view` | view |
  | `snp` | snapshot | `per` | person |
  | `sw` | software | `pol` | policy |
  | `src` | source | | |

  *Code:* `kb.ids`.

**reference** (ref)
: An ID, optionally with a rendition name: `doc-3vq8r2m1kd#text`.
  References are store-independent: the item named need not be in the
  same store. *Code:* `kb.ids.parse_ref`.

**dangling reference**
: A reference to an item not in this store. It is reported as a warning
  by `kb check` and fetched by `kb pull`.

**identifiers**
: External identifiers of what an item describes: `doi`, `arxiv`,
  `inspire`, `indico`, `cds`, `url` and others. Used for links and to avoid
  duplicates.

**content hash**
: The SHA-256 of a file's bytes. It identifies content wherever it is.
  *Code:* a rendition's `sha256`, `kb.content.sha256_file`.

**item digest**
: The SHA-256 of an item's meaning: canonical JSON, without `summary`,
  `tags`, `related`, `evidence`, `x` and `origin`. Derived items pin their
  inputs by digest, and a copy in another store has the same digest.
  *Code:* `kb.items.digest`, `Item.digest()`, `Store.ref_digest`.

**version digest**
: An asset version's identity. For a file asset, the file's content hash.
  For a file set, the hash of its manifest. *Code:* `kb.assets.digest_of`.

**bundle ID**
: The SHA-256 of a bundle's canonical JSON. A bundle is named by it.

## Content

**rendition**
: One representation of an item's material, such as the original PDF, its
  extracted text, a note or a view's source file. It has a `name`, a
  `role`, a `media_type`, a content hash and one or more locations.

**role** (of a rendition)
: `original` (as received), `content` (authored with the item: a note, a
  topic page), `derived` (made by a transform), `metadata` (a descriptor: a
  data package, a view manifest), `source` (a file a view is built from).
  A view's *source files* have nothing to do with *data sources*.

**fidelity** (of a derived rendition)
: `lossless`, `extracted` (mechanical, may lose figures) or `llm` (an
  interpretation, never evidence).

**transform**
: A tool that makes one rendition from another (pdftotext, pandoc,
  python-pptx, uproot ...). *Code:* `kb.transforms`, `kb rendition extract`.

**content**
: The bytes of a rendition. *Code:* `kb.content` (placing, hashing,
  fetching, verifying content).

**blob**
: Content held in the store's own database (`blobs` table), addressed by
  hash. Blobs are never changed or deleted. *Code:* `Store.blob`,
  `Store.put_blob`.

**content store** (cas)
: A content-addressed directory (`<path>/sha256/ab/cd/<sha256>`), often on
  EOS or NFS, for content too large for the database. It is named in
  settings (`cas.NAME.path`, with per-machine paths in `.kb/local.toml`).
  *Code:* `cfg.cas`, location type `cas`, `kb add --place cas --cas NAME`.

**location**
: Where a rendition's bytes are: a JSON object whose `type` is served by a
  location source.

  | type | where |
  |---|---|
  | `db` | a blob in this store (with a `filename`) |
  | `cas` | a content store (`name`) |
  | `url` | the web (with `retrieved`) |
  | `local` | one host's path |
  | `asset` | an asset version (and `path`) |
  | `datalad` | a file in a DataLad dataset (a source item, a path, an annex key) |

**data source**
: Anything that can supply content. There are two interfaces:

  - **location sources** resolve one rendition location to a local file
    (*Code:* `kb.locations`, `LocationSource`, `for_location`, `resolve`);
  - **asset sources** enumerate the versions of assets (*Code:*
    `kb.assets.AssetSource`, `SOURCES`, `kb source types`).

**source item**
: An item (kind `source`, `src-…`) describing one concrete source that
  renditions and asset bindings depend on, today a DataLad dataset: its
  `source_type`, its own global identity `source_id` (frozen), the `urls`
  it can be had from and `paths` holding it on given hosts. Knowledge sets
  always carry the source items their items need. Two source items with the
  same identity describe the same source and share its local copy.
  `kb source add` guesses a target's source type (or takes `--type`).
  *Code:* kind `source`, `kb.sources` (types, `guess`, `add`),
  `kb.datasets`, `kb source list|show|add`.

**local copy / clone**
: Where a source is on this machine: a DataLad clone, downloaded URL
  content. Never part of an item; found by the source's identity
  (`kb.datasets.locate`: explicit `datasets.<id>.path`, kb's clone registry
  `datasets.toml`, the item's paths for this host, the source directory).
  *Code:* `kb source clone`, `kb source link`.

**source directory**
: Where a source type keeps new local copies: `sources.<type>.dir`, by
  default `~/.cache/wckb/<type>/` (`url`, `datalad`). With
  `sources.datalad.new = "ask"`, kb asks where a novel dataset goes.
  *Code:* `kb.config.source_dir`.

**DataLad dataset**
: A git + git-annex repository managed by DataLad, identified by a dataset
  ID (a UUID). Not to be confused with kb's `dataset` item kind (data
  described as knowledge). *Code:* `kb.datasets`, location type and asset
  source `datalad`; optional extra `wckb[datalad]`.

**placing**
: Deciding where new content goes: `auto` (left in its DataLad dataset if
  committed there, else in the store if small, else a content store, else
  local-only), `db`, `cas`, `local`, `datalad`, or `none` (only a hash, for
  content at a URL). *Code:* `kb.content.place`,
  `kb add --place`.

**materialize**
: Write content to a local file so tools can read it. Content from the
  database goes into `.kb/cache/` under its real file name; downloads and
  clones go to source directories. *Code:*
  `kb rendition path ID#NAME`.

**checkout / checkin**
: The file round trip for the few cases where files are natural.
  `kb item checkout ID DIR` writes `item.json` and the item's content files;
  `kb item checkin DIR` validates and applies the changes, within policy.

## Assets: files, file sets, versions

**asset**
: An item standing for one file or a file set, independently of where the
  bytes are. Its ID is the abstract, generous identity. *Code:* kind
  `asset` (`as-`), `kb.assets`, `kb asset`.

**file / file set**
: An asset's type: `file` (exactly one file) or `fileset` (files with
  relative paths). *Code:* `asset_type`.

**version**
: A state of an asset, made of a `label`, a version `digest`, a manifest
  (`files`) and sightings. A registered version is frozen.

**label**
: A version's human handle (`v1`, `2026-10-28`, a DocDB version number).
  The digest, not the label, identifies the content.

**manifest**
: A version's list of files: relative `path`, `sha256`, `size`,
  `media_type`.

**match**
: How an asset compares file sets: `exact` (paths and content) or
  `content` (content only, so a rename is not a new version).

**binding**
: A link from an asset to an asset source, with a `name`, a source
  `type`, a `locator`, a `versioning` mode, and optionally a `pattern`, a
  `map` and a `priority`.

**locator**
: A binding's source-specific concrete identifiers: a named filesystem
  root and path, a URL, a DataLad dataset and path, an Indico event and
  attachment, a DocDB document.

**filesystem root** (named)
: A name for a directory tree, mapped to a path per machine (setting
  `filesystems.NAME.path`), so one binding works wherever the tree is
  mounted.

**versioning** (of a binding)
: How a source's own versions relate to the asset's:

  - `none`: only current content;
  - `native`: the source's version IDs are labels;
  - `pattern`: versions found by a path pattern with `{version}`;
  - `map`: an explicit table from native IDs to labels.

**native version**
: A source's own version identifier: a subdirectory name, a DocDB version
  number, a git commit.

**offer**
: What a source presents for an asset: a native version (if any), the
  mapped label (if any) and files. *Code:* `kb.assets.Offer`.

**scan**
: Asking every bound source for its offers, and classing each as `known`
  (matches a version), `new`, `conflict` (claims a label whose digest
  differs, or one source offers different content under one label) or
  `unreachable`. *Code:* `kb asset scan`, `kb.assets.scan`, `ScanResult`.

**register**
: Record new offers as versions, and sightings of known ones. *Code:*
  `kb asset scan --register`, `kb asset add`, `kb.assets.register`.

**sighting**
: A record on a version of where it was seen: binding, native version,
  time. *Code:* the version's `sightings`.

**keep**
: Copy a registered version's content into the store, so it stays
  resolvable when an unversioned source overwrites it (`--keep auto|yes|no`).
  Kept versions are offered by the binding of type `kb`.

**resolve** (an asset)
: Turn (asset, version) into one verified file set: the first source in
  priority order whose content has the version's digest. A copy with the
  right label but other content is *divergent*: noted, and never used.
  *Code:* `kb asset resolve`, `kb.assets.resolve`, `Resolution`.

**version selector**
: Which version to resolve: a label, `latest` (the last registered),
  `current` (what the highest-priority source offers now) or `digest:SHA`.
  Written as `as-…@SELECTOR`.

## Derivation and building

**input**
: An item a derived item is built from (`derived_from`). Pinned by digest
  once stamped or built.

**stamp**
: Pin a topic's inputs by their current digests. *Code:* `kb item stamp`.

**stale**
: A derived thing whose inputs changed or stopped being current: a derived
  rendition, a topic or a view. *Code:* `kb item stale`, `kb.graph`.

**manifest** (of a view)
: What a build used and made: input digests, source-file hashes, tool
  version, outputs. Kept as a `metadata` rendition. Not to be confused
  with an asset version's manifest.

**build / publish / world**
: `kb view build` runs a view's tool in `.kb/build/<id>/`. `kb view publish` copies
  the output to the publish store as an immutable version.
  `kb view build --world` re-extracts stale renditions and rebuilds stale views
  in dependency order.

## Moving knowledge

**neighbor / edge**
: A *neighbor* is another store linked from this one; the *edge* is the
  link, with a relation and a transfer behavior. Commands manage edges
  (`kb edge add NAME`); settings and code speak of neighbors. *Code:* `kb.federation.Neighbor`, `kb edge`.

**relation**
: `up` (broader), `down` (narrower), `peer`, `twin` (a copy of this same
  store).

**form** (push / pull)
: What moves along an edge by default: `select` (an explicit knowledge
  set), `all` (every new or changed item) or `none`.

**land**
: How a push arrives: `apply` (straight in), `inbox` (a bundle for
  review) or `pr` (a bundle proposed by pull request in an exchange
  repository).

**accept** (of an edge)
: How pulled items arrive here: `direct`, or `review` (into the inbox).

**prefer**
: Who wins on mutable fields when an item arrives along another path:
  `ours`, `theirs`, or `newer` (between twins).

**reach**
: How far reads follow `up` and `peer` edges (`--reach N`, `--in NAME`).

**knowledge set** (kset)
: An item that selects items: `members`, `depth` (`shallow`: the members;
  `deep`: also everything they reference), `nested` (`follow` a nested set's
  own depth, or `inherit`) and `follow` (which reference fields a deep
  descent takes). It is the unit of transfer. *Code:* kind `kset` (`ks-`),
  `kb kset`, `kb.transfer.expand`.

**expansion**
: The items a knowledge set denotes in a store, plus what it references
  that is missing. *Code:* `kb kset expand`, `Expansion`.

**transfer**
: Applying a knowledge set's items from a sender (a store or a bundle) to
  a receiving store in one transaction. *Code:* `kb.transfer.transfer`,
  `plan`, `PlanEntry` (`new`, `update`, `same`, `conflict`).

**push / pull / obtain**
: A transfer started by the sender (`kb push --to NEIGHBOR`), by the
  receiver from a named neighbor (`kb pull --from NEIGHBOR`), or by the
  receiver for referenced items from whichever reachable store has them
  (*obtain*: `kb pull ID ...` or `kb pull --dangling`, without `--from`).
  *Code:* `kb.push.push`, `pull`, `obtain`.

**conflict**
: In a transfer: the receiver holds an item with the same ID that asserts
  something different, or an editable item that diverged along another
  path. Conflicts refuse the whole transfer. (For assets, see *scan*.)

**sync**
: Twins exchanging new and changed items both ways; the newer change
  wins. *Code:* `kb sync`.

**candidate** (for promotion)
: An item worth pushing to a neighbor, ranked with reasons and blockers.
  *Code:* `kb edge candidates`, `kb.push.Candidate`.

**bundle**
: A knowledge set, its items, their small content and the people they
  mention, in one self-checking JSON file (`*.kbundle.json`). *Code:*
  `kb.bundle`, `kb bundle`.

**inbox**
: Received bundles awaiting a decision (`pending`, `accepted`,
  `rejected`). *Code:* the `inbox` table, `kb inbox`.

**review**
: A person's or an LLM's assessment of an inbox bundle: a `verdict`
  (`accept`, `reject`, `changes`, `comment`) and reasons. An LLM's review is
  advice. *Code:* the `reviews` table, `kb inbox review|show|accept|reject`.

**exchange repository**
: A git repository of bundle files, where git is transport and review
  (pull requests) and never the store. *Code:* `kb exchange init`,
  `kb.exchange`, `kb.githooks`.

**ledger**
: The record of transfers in and out of a store: `transfer-in` and
  `transfer-out` events. *Code:* `kb edge ledger`.

## History, checks and rendering

**event**
: An append-only entry in a store's history: when, which item, what action,
  by whom, approved by whom, details. *Code:* the `events` table, `kb log`.
  Actions: `create`, `set`, `content`, `checkin`, `rendition`, `extract`,
  `stamp`, `stage`, `datapackage`, `verify`, `build`, `publish`, `bind`,
  `unbind`, `register`, `migrate`, `settings`, `transfer-in`,
  `transfer-out`, `inbox-receive`, `inbox-accept`, `inbox-reject`,
  `review`.

**check**
: The store's consistency scan: schema, tampering, missing content, access,
  evidence, dangling references (warnings). *Code:* `kb check`, `kb.checks`.

**tamper**
: An item whose guard columns disagree with its JSON, i.e. it was changed
  outside kb.

**lint**
: Advice needing judgment: stale things, overlaps, old unverified claims,
  local-only content. *Code:* `kb lint`.

**status**
: A store at a glance: items, inbox, edges, content stores, and the
  findings of the quick diagnoses. *Code:* `kb status`, `kb.status`.

**doctor / remedy / finding**
: `kb doctor` diagnoses common problems and fixes them. A *remedy* knows
  one problem: it diagnoses (returning *findings*) and may fix. A finding's
  mode is `auto` (safe; `--fix` applies it), `ask` (applied after
  confirmation) or `manual` (advice). *Quick* remedies also run in
  `kb status`. *Code:* `kb.doctor`, `REMEDIES`, `@remedy`, `Finding`.

**query tables**
: Derived tables kept in step on every write: `subjects`, `runs`, `refs`,
  `identifiers`, `renditions`, `fts`, `vectors`. *Code:* `kb.query`,
  `Store.reindex`.

**render / view model**
: Turning items, sets and bundles into something people can judge.
  Rendering first builds a view model (plain data: names, dates, links,
  excerpts, a tree, no hashes), then a formatter makes text, markdown or
  JSON. *Code:* `kb.render`, `kb show`, `kb bundle show`. (A *view*, the
  item kind, is something else: a built HTML/PDF product.)

## Policy and autonomy

**policy element**
: A named, versioned piece of LLM guidance: `instructions` (a CLAUDE.md
  section), `skill`, `hook` or `settings`. Builtins ship with kb; stores add
  `policy` items (`draft`, `released`, `deprecated`). *Code:* `kb.policy`,
  `kb policy`.

**enact**
: Write the selected policy into a target: a CLAUDE.md block, skills and
  hooks. *Code:* `kb policy enact`.

**autonomy**
: What an LLM may do unasked, per action (`capture`, `review`, `curate`,
  `policy`, `push`, `pull`, `accept`, `merge`), at level `never`, `ask` (with
  `--approved "<the human's words>"`) or `auto`. Set only by people.

**agent hooks / git hooks**
: Claude Code hooks (`kb hook check|recall|session`), managed as policy;
  git hooks (`kb exchange hooks`) guard exchange repositories.

## The command line

**verbs and things**
: `kb` has two kinds of commands. *Verbs* used every day stand at the top
  (`init`, `status`, `add`, `new`, `show`, `ls`, `search`, `context`, `log`,
  `check`, `lint`, `doctor`, `config`, `push`, `pull`, `sync`). Everything else lives in one
  command family per *thing*, named by a noun from this glossary: `kb item`,
  `kb rendition`, `kb asset`, `kb source`, `kb kset`, `kb edge`,
  `kb bundle`, `kb inbox`, `kb view`, `kb data`, `kb policy`, `kb store`,
  plus `kb exchange`, `kb forge` and `kb secret` for integration. A family
  run without a subcommand lists its commands. A new command joins the
  family of the thing it acts on, and is named for what it does rather
  than for the format or tool that does it.

**describe / export** (data)
: Describe data for use outside kb, in a community standard
  (`kb data describe --schema frictionless`: a Frictionless Data Package
  for CSV/TSV files or a dataset item), or package items with their
  content (`kb data export --format ro-crate`). *Code:* `kb.fair`.

## Words kb avoids

| don't say | say |
|---|---|
| record | item |
| source record / source item | primary item |
| blob store | content store (cas); "blob" means content in the store's database |
| the KB file / the database | the store (`kb.sqlite`) |
| clone of a store | snapshot (read-only copy) or twin (a synced copy) |
| candidate (for assets) | offer |
