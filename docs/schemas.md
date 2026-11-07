# Schemas (v1): a short guide

The item schemas live in `src/kb/schemas/v1/` as hand-written JSON Schema
(draft 2020-12). Every item is a JSON object that must validate against
`item.json` (the fields every item has) plus the schema of its `kind`. Shared
types are in `common.json`. This page explains the shape and how each kind
specializes it. The field-by-field tables are generated into
[reference/schema.md](reference/schema.md), and the schemas themselves are
authoritative.

## Common structure

### The envelope: `item.json`

Every item has these fields:

| group | fields |
|---|---|
| identity | `schema_version` (always `1`), `id` (kind prefix + dash + suffix, e.g. `obs-…`), `kind` |
| description | `title` (1–200 chars), `summary` (1–400 chars, written for an LLM to read instead of the content) |
| provenance | `created` (`at`, `by`, optional `via` and `with` co-authors), `access`, `origin` (set on copies pushed from another store) |
| meaning | `subject` (slash paths such as `detector/tpc/noise`), `tags`, `valid` (run ranges and/or a time window), `status` (`draft`, `unverified`, `verified`, `disputed`), `identifiers` (doi, arxiv, inspire, indico, cds, url …) |
| dating | `date` (one date, fixed once written), `dates` (qualified dates: created, published, modified, accessed, captured) |
| relations | `supersedes`, `retracts`, `derived_from`, `evidence`, `related`, `links` (typed: supports, contradicts, refines, cites, part-of …) |
| content | `renditions` (the representations of the material and where their bytes are), `license`, `creators` |
| extension | `x`: free-form, the intended home for fields the schema does not know |

Required on every item: `schema_version`, `id`, `kind`, `title`, `summary`,
`created`, `access`. `access` is one of `public`, `collaboration`, `group`,
`private`, from most to least open.

### Shared definitions: `common.json`

The building blocks that the envelope and kinds reuse:

- **Identifiers and references.** `id` is a fixed set of prefixes: `obs`, `doc`,
  `dat`, `cfg`, `snp`, `sw`, `top`, `view`, `per`, `pol`, `ks`, `as`, `src`.
  A `ref` is an ID, optionally followed by `#rendition` to name one of the
  item's renditions.
- **Dates.** `date` may be reduced (`2026`, `2026-10`) or a full timestamp;
  `datetime` is a full ISO 8601 timestamp.
- **`created` and `assertion`.** `created` says who made the item and when
  (`by` is a person ref or a free-text name). `assertion` says who asserted a
  statement, how (email, meeting, elog) and when.
- **`valid`.** Runs as inclusive `[first, last]` ranges, a time window, or both.
- **`derivation`.** An input of a derived item: a ref, or a ref with the
  `sha256` digest it had when stamped.
- **`rendition`.** One representation of an item's material. It has a `name`,
  a `role` (`original`, `content`, `derived`, `metadata`, `source`), a
  `media_type`, optional `sha256` and `size`, one or more `locations`, and, when
  it was made from another rendition, `derived_from`, `fidelity` (`lossless`,
  `extracted`, `ocr`, `llm`) and `transform`.
- **`location`.** Where a rendition's bytes are. The type is one of `db` (kept
  in the store), `cas` (a named content store), `url`, `local` (a host and
  path), `datalad` (a source plus a path and git-annex key), `source` (a file in
  a source tree) or `asset` (a version of an asset). Each type says what else it
  needs.
- **`origin`.** Set by transfers: which store the copy came from, when it was
  pushed, by whom and whether it was approved.

## The kinds

Each kind adds its own required fields and properties on top of the envelope.
They fall into three groups by how they change over time.

- **Primary (append-only):** `observation`, `document`, `dataset`, `config`,
  `snapshot`, `software`, `kset`. The store refuses changes to what they
  assert. A correction is a new item that `supersedes` the old one.
- **Derived (edited freely, then stamped):** `topic`, `view`. Their text can
  change, but they keep `derived_from` lists with digests.
- **Maintained registries:** `person`, `policy`, `source`, `asset`. These are
  looked up and updated over time. Policies become frozen once released, and
  registered asset versions and sources keep their identities.

| kind | prefix | group | what it is | kind-specific required fields |
|---|---|---|---|---|
| `observation` | `obs` | primary | one assertion of fact by someone | `asserted_by`, `renditions` (with a content rendition), `status` |
| `document` | `doc` | primary | paper, technote, slides, minutes, notebook … | `doctype`, `creators`, and a `date` or `dates`; also needs renditions or identifiers |
| `dataset` | `dat` | primary | tables, histograms, ROOT/HDF5 files, condition series | `renditions` |
| `config` | `cfg` | primary | parameters of a system over a validity interval | `system`, `valid` |
| `snapshot` | `snp` | primary | an extract from an authoritative system, with its query | `system`, `query`, `retrieved`, `renditions` |
| `software` | `sw` | primary | a code repository at a version | `repository` |
| `kset` | `ks` | primary (frozen) | a selection of items: the unit of transfer and a reading list | `members`, `depth` |
| `topic` | `top` | derived | an LLM-maintained synthesis page about a subject | `subject`, `derived_from`, `renditions` (with content) |
| `view` | `view` | derived | a recipe that builds a human-facing product | `build`, `derived_from` |
| `person` | `per` | registry | someone who asserts, creates or reviews items | `name` |
| `policy` | `pol` | registry | a named, versioned element of LLM policy | `name`, `version`, `policy_type`, `stage`, `renditions` (with content) |
| `source` | `src` | registry | one concrete data source (a DataLad dataset, a git repo …) | `source_type`, `source_id` |
| `asset` | `as` | registry | an abstract file or file set, with versions and bindings | `asset_type` |

### Primary kinds

- **`observation`**: what someone asserted. `asserted_by` is a list of
  assertions (who, how, when). Its text is a content rendition. Because it is
  primary, it is corrected by superseding, never by editing.
- **`document`**: a paper or other document, usually held elsewhere (Indico,
  arXiv, CDS) and linked by URL and identifiers. `doctype` is an enum from
  `paper` to `prompt` and `other`. `event` (title, date, Indico reference, URL)
  and `venue` and `version` describe where it appeared. `provenance` says how
  much of its making is recorded when it is an artifact made here (`cited`,
  `recorded`, `rebuildable`).
- **`dataset`**: machine-readable data. `format` is a short name such as `csv`
  or `parquet`. `variables` lists the columns with name, unit and type.
  `produced_by` links the software, config and command that made it. The schema
  prefers CSV with a `datapackage.json` rendition, JSONL or Parquet.
- **`config`**: parameters of a system over a validity interval. `system` is a
  subject path (such as `production/sim`). Small parameter sets go inline in
  `parameters`. Whole files go in renditions.
- **`snapshot`**: an extract from a system that remains the source of truth
  (conditions DB, run DB, elog). It records the `query` that produced it and
  when (`retrieved`).
- **`software`**: a code repository reference: `repository` (a URL), plus
  `commit`, `version` and `language`. Datasets, configs and views cite it for
  provenance.
- **`kset`**: a selection of items. `members` are refs, which need not be in
  this store. `depth` is `shallow` (members only) or `deep` (members and
  everything they reference, recursively). `nested` says whether a nested set
  follows its own depth or inherits this one's. `follow` restricts which
  reference fields a deep walk uses. A kset is frozen once written.

### Derived kinds

- **`topic`**: a synthesis page (`page.md`) about a `subject`. Every factual
  statement cites an item by ID, and every cited item is listed in
  `derived_from`. The page is stamped after it is edited.
- **`view`**: a recipe for an HTML site, PDF or slides. `build` says which tool
  runs it (`make`, `snakemake`, `quarto`, `pandoc` or an arbitrary `command`),
  its `target`, `formats` and `command`. Its source files are renditions with
  role `source`. `audience` says who the output is for.

### Registry kinds

- **`person`**: `name`, with optional `affiliation`, `email` and `handles`
  (an object of external handles). IDs are readable, not random, for
  persons. Other items refer to people by these refs.
- **`policy`**: a named, versioned piece of LLM policy. `name` is shared by all
  versions, and `version` goes up by one at each release. `policy_type` is
  `instructions` (a CLAUDE.md snippet), `skill` (a SKILL.md), `hook` (Claude
  Code hook settings) or `settings` (TOML). `stage` is `draft` (editable),
  `released` (frozen) or `deprecated`. `order` places instructions snippets,
  `applies_to` lists the store scopes it is for, and `based_on` names the
  policy it was forked from.
- **`source`**: a data source whose identity never changes. `source_type` is
  `datalad`, `git`, `filesystem` or `indico`. `source_id` is the source's own
  global identity (a DataLad UUID, or `git:<first commit>`). `urls` and `paths`
  say where it can be had: the URLs are shared by all copies, and the paths are
  per host.
- **`asset`**: an abstract file or file set, independent of where its bytes
  are. `asset_type` is `file` or `fileset`, and `match` says how a set of files
  is matched to a version (`exact`, or `content` only). `versions` are labelled
  and identified by a digest, with a manifest, files and sightings. `bindings`
  say which sources can supply it, how their versioning maps (`none`, `native`,
  `pattern`, `map`) and in what order to try them.

## Notes

- Unknown top-level fields are not forbidden by these files. `item.json` says
  the `x` object is "the only place unknown fields are allowed", but no
  `additionalProperties: false` appears at the top level. Whether the store
  rejects them is a question to check in `schema.py`, not something these files
  settle.
- Several rules are conditional and are not visible in a field table: a
  `document` must be dated and must have renditions or identifiers, `observation`,
  `topic` and `policy` must have a content rendition, and `verified` status
  needs evidence.
- Schema changes follow the rules in `CLAUDE.md`: additive changes bump the
  revision, breaking ones bump the major version and need a migration.
