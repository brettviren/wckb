# wckb: developing the `kb` tool

This repository is the `kb` tool. It is *not* a knowledge base. The builtin
LLM policy for stores (instruction snippets, skills, hooks) is in
`src/kb/data/policies.json`, `src/kb/data/policies/` and
`src/kb/data/skills/`. `kb policy enact` writes it into each store's
target.

Terms are defined in `docs/glossary.md`. Use them in code (names, help
texts, messages) and docs; when you introduce or rename a concept, update
the glossary in the same change.

Open design questions live in `docs/questions.md`. When a change settles or
raises one, update that file.

Read `docs/design.md` before changing behavior. `docs/plan.md` says what's
in scope.

## Layout and layering

- `cli.py` parses arguments, calls logic modules and renders through `ui.py`.
  No data processing goes in either. Every subcommand with required
  arguments shows its help when given none.
- The command tree: everyday verbs at the top, then one family per thing
  (a glossary noun: `kb item`, `kb edge`, `kb inbox` ...); see "The command
  line" in `docs/glossary.md`. Put a new command in the family of the thing
  it acts on, and name it for what it does, not for the format or tool.
  Top-level sections are `cli.SECTIONS`; a family's listing order is its
  `order`. After changing commands run `uv run python tools/gen_cli_doc.py`
  (a test checks `docs/reference/cli.md`).
- The store is one SQLite file per store: `db` (DDL, guard triggers,
  transactions, the network-filesystem guard) and `items` (`Store`: items,
  blobs, events, settings, query tables kept in step on every write).
  Item JSON stays validated by JSON Schema on every write.
- Logic modules: `items` (model, digest, `Store`), `authoring` (new/add/supersede),
  `edit` (set, renditions, content, checkout/checkin), `locations` (location
  sources: db, cas, url, local, asset, datalad), `assets` (assets, bindings, asset
  sources), `datasets` (DataLad: source items, clones, content), `content` (placing content, hashing, fetch, verify), `checks`
  (consistency scan), `lint` (advice), `doctor` (remedies: diagnose and fix
  common problems), `status` (the store at a glance), `graph` (staleness, stamping), `build` (views),
  `query`/`search`/`embed`/`context`, `transforms`, `fair` (data describe and
  export), `migrate`,
  `scaffold`, `federation` (neighbors) and `federated` (cross-store
  queries), `transfer` (knowledge sets, atomic transfers, multi-path
  reconciliation), `bundle` (bundles, inbox, reviews), `render` (view models
  and formatters for people), `exchange` (bundle pull requests),
  `push` (candidates, push, pull, obtain), `sync` (twins), `policy` (policy elements, enactment, autonomy), `hooks`
  (Claude Code hook entry points), `githooks` (git hooks for exchange repositories), `forge`
  (GitHub/Gitea/Forgejo/Gogs APIs) and `secrets` (secret specs).
- Schemas: hand-written JSON Schema in `src/kb/schemas/v<N>/`. After
  changing them, run `uv run kb item schema --markdown > docs/reference/schema.md`
  (a test checks this). Additive changes bump `schema.REVISION`. Breaking
  changes bump `MAJOR` and add a migration in `migrate.MIGRATIONS` with a test.
- Builtin policy elements live in `src/kb/data/`. Bump an element's
  `version` in `policies.json` whenever its text changes.

## Conventions

- When user testing turns up a problem kb could detect or repair, add a
  remedy to `doctor.py` (diagnosis, and a fix if one is safe) with a test,
  and list it in `docs/howto/health.md`.
- `uv sync`, `uv run pytest`, `uv run kb`.
- Tests use real temporary stores (and git repositories where git is
  involved; see `tests/conftest.py`). Don't mock internals. Fakes are fine at external
  boundaries (the embedding service).
- Keep imports in `cli.py` lazy; LLMs call `kb` often and start-up time
  matters.
- Errors meant for users are `KBError`, and the CLI shows them as one line.
- Never print or log a resolved secret. Use `secrets.mask` for display.
- Tests run inside Claude Code sessions. `tests/conftest.py` clears the
  agent environment variables. Tests of autonomy set `KB_AGENT=1`
  explicitly.
