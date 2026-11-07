# wckb: `kb`, a shared knowledge base for people and their LLMs

This package provides the `kb` command to manage "knowledge".

## Modeling knowledge 

Knowledge is modeled as a collection of knowledge items.  Each item is an
instance of a well defined type or "kind" of knowledge.  That is, an item is a
data object governed by a schema.  All kinds of knowledge items hold their kind
version of the associated schema, a unique instance identifier in addition to
type-specific attribute sets as dictated by the schema.

The different kinds of knowledge are distinguished based on several aspects.
Each aspect defines a group of attributes.  The groups include identity,
description, provenance, meaning, dating, relations and content.  Common
attributes across all kinds provide: the schema kind and its version, a unique
identifier, a short title, longer summary, the timestamp marking the creation of
the item, and an access level.  Access is one of public, collaboration, group,
private, from most to least open.  More information on the schemas is in docs/schemas.md.

One key aspect of knowledge is its content as provided by the rendition attribute.  A rendition is itself an object that describes some material.  The material for a knowledge is typically a file or URL.  Material has a "source".




 manages knowledge stores: SQLite files of small, schema-checked JSON items
that a group, and the Claude sessions each member runs, read from and write
to.

- Items cover observations ("run 1234 was noisy"), documents (papers,
  slides), datasets, configurations, conditions snapshots, software, topic
  pages (synthesis), views (built HTML/PDF), policies and knowledge sets.
- Small content is held in the store. Large content lives in a content store
  (EOS, NFS), at a URL (Indico, arXiv) or, experimentally, in a DataLad
  dataset, all behind one data-source interface. Every rendition carries a
  SHA-256.
- Sources are append-only, enforced by the database. Corrections supersede.
  Every change and transfer is an event (`kb log`), and `kb item stale` shows what
  needs revising or rebuilding.
- Stores at every scope (project, home, group) are linked as `up`, `down`,
  `peer` or `twin` neighbors. LLMs read across them. Knowledge moves as
  knowledge sets with `kb push` and `kb pull`: applied
  atomically, or as **bundles** reviewed in the receiver's inbox by people
  and LLMs, or as pull requests in an exchange repository.
- **Assets** stand for files and file sets independently of where they
  live. Versions are identified by content, sources (directory trees, URLs,
  the store; DataLad, Indico and DocDB planned) are bound with their own
  versioning, and resolution yields one verified file set.
- Items, sets and bundles render for people (`kb show`,
  `kb bundle show`, `kb inbox show`): names, dates, links, excerpts and
  trees, with no hashes.
- Policy (CLAUDE.md instructions, skills and hooks) is named, versioned and
  enacted by `kb policy`. Autonomy (push, pull, accept, merge ...) is
  granted by the human.
- Forges (GitHub, Gitea, Forgejo), with tokens from `pass`-style commands.

```
uv tool install git+<this repo>      # or: uv tool install --editable .
kb init ~/kb --scope home            # your personal store
cd ~/work/proj && kb init kb --scope project --up ~/kb   # a project store beside your code
kb new observation --title "Noisy run 1234" --summary "..." --subject detector/tpc/noise \
   --runs 1234 --by "A. Colleague" --how "verbal, collab meeting" --text "..."
kb context detector/tpc              # what the KB knows, compactly
```

## Documentation

- [docs/glossary.md](docs/glossary.md): the terms kb uses (store, item, rendition,
  asset, knowledge set, bundle ...) and their names in the code.
- [docs/design.md](docs/design.md): the design and the reasons for it.
- [docs/plan.md](docs/plan.md): implementation plan and phases.
- [docs/questions.md](docs/questions.md): open questions, with today's
  defaults.
- [docs/decisions/](docs/decisions/): decision records (0001: the SQLite
  store; 0002: assets, versions and sources).
- [docs/rendering.md](docs/rendering.md): rendering for review, now and
  planned (TUI, web).
- [docs/howto/](docs/howto/): task guides. Start with
  [getting-started](docs/howto/getting-started.md).
- [docs/reference/schema.md](docs/reference/schema.md): every item field
  (generated).
- [docs/reference/cli.md](docs/reference/cli.md): every command (generated).

## Development

```
uv sync                # development environment (includes the pptx and root extras)
uv run pytest          # tests
uv run kb --help
```

Optional extras: `pdf` (PyMuPDF), `pptx` (python-pptx), `root` (uproot).
`pdftotext` and `pandoc` are used when they are on `PATH`.
