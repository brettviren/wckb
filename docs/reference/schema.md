# Item schema reference

Generated from the JSON Schemas in `src/kb/schemas/v1/` (revision 1.11) by
`kb item schema --markdown`; a test keeps this file in sync. `kb item schema KIND` prints
the schema itself, which is authoritative.

## Fields common to every item

| field | required | type | description |
|---|---|---|---|
| `schema_version` | yes | `1` | Major schema version; kb store migrate upgrades. |
| `id` | yes | id | Assigned by kb: kind prefix and random suffix. |
| `kind` | yes | string | Item kind; selects the kind schema. |
| `title` | yes | string | Short title. |
| `summary` | yes | string | One to three sentences an LLM can read instead of the content. Shown in listings and search. |
| `created` | yes | created | When, by whom and via what tool the item was made (not the date of its content). |
| `access` | yes | access | Who may see it: public, collaboration, group, private. |
| `subject` |  | list of subject | Slash-separated topic paths, e.g. detector/tpc/noise; reuse existing ones (kb ls --subjects). |
| `tags` |  | list of string | Free labels. |
| `valid` |  | valid | Validity interval of the content, as run ranges and/or a time window. |
| `status` |  | status | Confidence in the content: draft, unverified, verified (needs evidence), disputed. |
| `identifiers` |  | identifiers | External identifiers: doi, arxiv, inspire, indico, cds, url ... |
| `creators` |  | list of agent | Authors of the thing recorded (person refs or names). |
| `date` |  | date | The date the thing is known by: when it was published (else made). Fixed once written; qualified and correctable dates are in `dates`. |
| `dates` |  | object | Qualified dates of the thing, each saying what it dates; unlike `date`, they can be corrected. created: when it was made (written, drawn, produced); published: when it was made public (released, posted, presented); modified: when it last changed; accessed: when it was read or downloaded for this item; captured: when a photo or recording was taken. |
| `supersedes` |  | list of ref | Items this one replaces (corrections, new versions). |
| `retracts` |  | list of ref | Items this one withdraws without replacement. |
| `derived_from` |  | list of derivation | Inputs of a topic, view or derived item; stamped with digests. |
| `evidence` |  | list of ref_or_url | Refs or URLs supporting the content; required for verified. |
| `related` |  | list of ref | Other related items. |
| `links` |  | list of object | Typed relations to other items (a knowledge graph); each item shows the links to it under the inverse name. related is the untyped kind. |
| `license` |  | string | SPDX identifier or a local term such as collaboration-internal. |
| `renditions` |  | list of rendition | Representations of the item's material and where each one's bytes are (in the store, a content store, a URL, an asset ...). |
| `origin` |  | origin | Set on copies pushed from another store. |
| `x` |  | object | Free-form extensions. The only place unknown fields are allowed. |

## observation

ID prefix `obs-`; a source (append-only).

An assertion of fact made by someone: 'run 1234 was noisy', 'production used X=7 in 2025'. Its text is a content rendition (note.md). A primary item: correct it by superseding, never by editing.

| field | required | type | description |
|---|---|---|---|
| `asserted_by` | yes | list of assertion |  |
| `renditions` | yes | (see schema) |  |

Also required here: `status`.

## document

ID prefix `doc-`; a source (append-only).

A paper, preprint, technote, presentation, thesis, minutes or similar. The original usually lives elsewhere (Indico, arXiv, CDS) and is referenced by URL and identifiers; extracted text is a derived rendition.

| field | required | type | description |
|---|---|---|---|
| `doctype` | yes | `paper` \| `preprint` \| `technote` \| `note` \| `presentation` \| `poster` \| `thesis` \| `minutes` \| `report` \| `manual` \| `proposal` \| `book` \| `demo` \| `notebook` \| `prompt` \| `other` |  |
| `provenance` |  | `cited` \| `recorded` \| `rebuildable` | For an artifact (a technote, demo or notebook made here): how much of its making is recorded. cited: the items it presents (derived_from); recorded: also the steps that made it; rebuildable: a view remakes it. |
| `event` |  | object |  |
| `venue` |  | string | Journal, conference or series. |
| `version` |  | string |  |

Also required here: `creators`.

## dataset

ID prefix `dat-`; a source (append-only).

Machine-readable data: tables, histograms, ROOT/HDF5 files, per-run condition series. Prefer CSV/TSV with a datapackage.json metadata rendition, JSON/JSONL or Parquet.

| field | required | type | description |
|---|---|---|---|
| `format` |  | string | Short format name: csv, parquet, root, hdf5, jsonl ... |
| `variables` |  | list of object |  |
| `produced_by` |  | object |  |
| `renditions` | yes | (see schema) |  |

## config

ID prefix `cfg-`; a source (append-only).

Configuration parameters of some system over a validity interval: 'production simulation 2025 used drift velocity 1.6 mm/us'. Small parameter sets go inline in 'parameters'; whole files as renditions.

| field | required | type | description |
|---|---|---|---|
| `system` | yes | subject | What was configured, as a path: production/sim, daq/trigger ... |
| `parameters` |  | object |  |
| `asserted_by` |  | list of assertion |  |

Also required here: `valid`.

## snapshot

ID prefix `snp-`; a source (append-only).

An extract from an authoritative system (conditions DB, run DB, elog, slow control), recording the query and when it was made. The system remains the source of truth.

| field | required | type | description |
|---|---|---|---|
| `system` | yes | subject |  |
| `query` | yes | string | The query, API call or URL that produced the extract. |
| `retrieved` | yes | datetime |  |
| `renditions` | yes | (see schema) |  |

## software

ID prefix `sw-`; a source (append-only).

A code repository at a version, referenced for provenance of datasets, configs and views.

| field | required | type | description |
|---|---|---|---|
| `repository` | yes | url |  |
| `commit` |  | string |  |
| `version` |  | string |  |
| `language` |  | string |  |

## topic

ID prefix `top-`; not a source (editable).

Synthesis: an LLM-maintained page (page.md) about a subject. Every factual statement cites an item by ID and every cited item is listed in derived_from. A derived item: edit freely (kb item content), then kb item stamp.

| field | required | type | description |
|---|---|---|---|
| `derived_from` | yes | (see schema) |  |

Also required here: `renditions`, `subject`.

## view

ID prefix `view-`; not a source (editable).

Recipe for a human-facing product (HTML site, PDF note, slides). Its source files are renditions with role source; kb view build runs the build tool and stores a manifest; kb view publish copies the output.

| field | required | type | description |
|---|---|---|---|
| `build` | yes | object |  |
| `audience` |  | string |  |

Also required here: `derived_from`.

## person

ID prefix `per-`; not a source (editable).

Someone who asserts, creates or reviews items.

| field | required | type | description |
|---|---|---|---|
| `name` | yes | string |  |
| `affiliation` |  | string |  |
| `email` |  | string |  |
| `handles` |  | object |  |

## policy

ID prefix `pol-`; not a source (editable).

A named, versioned element of LLM policy: an instructions snippet for CLAUDE.md, a skill, a hook, or settings such as autonomy. Content is a content rendition. Editable while stage is draft; once released it is frozen, and a change is a new version that supersedes it.

| field | required | type | description |
|---|---|---|---|
| `name` | yes | string | Stable name shared by all versions, e.g. kb-routine or group-review. |
| `version` | yes | integer | Version number; each release supersedes the previous one with version + 1. |
| `policy_type` | yes | `instructions` \| `skill` \| `hook` \| `settings` | instructions: a CLAUDE.md snippet; skill: a SKILL.md; hook: Claude Code hook settings (JSON); settings: autonomy and similar (TOML). |
| `stage` | yes | `draft` \| `released` \| `deprecated` | draft (editable), released (frozen), deprecated (still usable, not recommended). |
| `applies_to` |  | list of string | Store scopes this policy is meant for: project, home, group ...; empty means any. |
| `order` |  | integer | Position of an instructions snippet in CLAUDE.md; lower first. |
| `based_on` |  | string | The policy this one was forked from: builtin:NAME@V or STORE:NAME@V. |
| `renditions` | yes | (see schema) |  |

## kset

ID prefix `ks-`; a source (append-only).

A selection of items, itself an item: the unit of knowledge transfer between stores, and usable as a reading list. Shallow means the members (and members of nested sets); deep also follows every reference from them, recursively. A primary item: frozen once written.

| field | required | type | description |
|---|---|---|---|
| `members` | yes | list of ref | The items selected (refs; they need not be in this store). |
| `depth` | yes | `shallow` \| `deep` | shallow: the members only; deep: the members and everything they reference, recursively. |
| `nested` |  | `follow` \| `inherit` | When a member (or a reference reached deeply) is itself a knowledge set: follow its own depth, or inherit this set's. |
| `follow` |  | list of `members` \| `supersedes` \| `retracts` \| `derived_from` \| `evidence` \| `related` | Reference fields a deep descent follows (default: all of them). |
| `purpose` |  | string | What the set is for: transfer, reading-list, release ... |

## asset

ID prefix `as-`; not a source (editable).

An abstract file or file set: a stable reference independent of where the bytes are. Versions are labelled and identified by a digest of their content; bindings say which sources (a filesystem tree, a URL, the store itself, DataLad, Indico, DocDB ...) can supply them and how each source's own versions map to the asset's. Registered versions are frozen; versions and bindings may be added.

| field | required | type | description |
|---|---|---|---|
| `asset_type` | yes | `file` \| `fileset` | file: one file; fileset: a set of files with relative paths. |
| `match` |  | `exact` \| `content` | How a concrete file set is matched to a version: exact (paths and content) or content (content only; renames don't make a new version). |
| `versions` |  | list of version |  |
| `bindings` |  | list of binding |  |

## source

ID prefix `src-`; not a source (editable).

One concrete data source that renditions and asset bindings refer to, such as a DataLad dataset. Its identity (source_type, source_id) never changes; the places it can be had from (urls, paths) are united as it travels. Where it is on a given machine (a local clone) is that machine's business: see kb source show.

| field | required | type | description |
|---|---|---|---|
| `source_type` | yes | `datalad` \| `git` \| `filesystem` \| `indico` | The kind of source (kb source types lists them). |
| `service` |  | string | For a source kb fills from a web service: the service's URL (for indico, the Indico site, such as https://indico.bnl.gov). |
| `source_id` | yes | string | The source's own global identity: for DataLad, the dataset ID (a UUID), or git:<first commit> for a dataset without one; for a git repository, git:<first commit>; for a filesystem, its root name (as in filesystems.NAME and fs bindings); for indico (a DataLad dataset filled from an Indico site), the dataset ID. |
| `urls` |  | list of string | Where it can be cloned or fetched from (for DataLad: anything datalad clone accepts: https, ssh, ria+...). |
| `paths` |  | list of object | Local file trees holding it, by host (a shared filesystem path is usable by everyone on that host). |

## Shared definitions

### rendition

| field | required | type | description |
|---|---|---|---|
| `name` | yes | string |  |
| `role` | yes | `original` \| `content` \| `derived` \| `metadata` \| `source` | original: as received; content: authored with the item; derived: made from another rendition; metadata: a descriptor; source: a file a view is built from. |
| `media_type` | yes | string |  |
| `sha256` |  | sha256 |  |
| `size` |  | integer |  |
| `locations` | yes | list of location |  |
| `derived_from` |  | string | Name of the rendition (in this item) this was made from. |
| `source_sha256` |  | sha256 |  |
| `fidelity` |  | `lossless` \| `extracted` \| `ocr` \| `llm` |  |
| `transform` |  | object |  |
| `notes` |  | string |  |

### location

Where a rendition's bytes are. Each type is served by a data source (kb.sources); new types plug in there.

- `db`: `filename`
- `cas`: `name`, `filename`
- `url`: `url`, `retrieved`, `auth`, `failed`
- `local`: `host`, `path`
- `datalad`: `source`, `path`, `key`
- `source`: `source`, `path`
- `asset`: `asset`, `version`, `path`

### valid

Validity interval of the item's content, in runs and/or time.

| field | required | type | description |
|---|---|---|---|
| `runs` |  | list of run_range |  |
| `time` |  | object |  |

### assertion

Who asserted something, how and when.

| field | required | type | description |
|---|---|---|---|
| `who` | yes | agent |  |
| `how` |  | string | Channel: verbal at meeting X, email, elog entry, chat ... |
| `when` |  | date |  |

### identifiers

External identifiers. Known keys are listed; others are allowed.

| field | required | type | description |
|---|---|---|---|
| `doi` |  | string |  |
| `arxiv` |  | string |  |
| `inspire` |  | string |  |
| `indico` |  | string | event/<id> or event/<id>/contribution/<id> |
| `cds` |  | string |  |
| `url` |  | url |  |

### derivation

An input: a ref, or, once stamped, the ref with the digest it had.

A ref string, or `{ref, digest}` once stamped.

### origin

Where an item copied from another store came from; set by transfers (push, pull).

| field | required | type | description |
|---|---|---|---|
| `store` | yes | string | ID of the store it was transferred from (the last hop). |
| `name` |  | string | Name of that store. |
| `scope` |  | string |  |
| `pushed_at` | yes | datetime | When it was transferred. |
| `pushed_by` |  | agent | Who ran the transfer. |
| `approved` |  | string | Who or what approved the push: a person, or the autonomy policy. |
| `path` |  | list of string | Store IDs it travelled through, oldest first, ending with the sending store. |
| `set` |  | string | The knowledge set (ks-...) whose transfer brought it. |
