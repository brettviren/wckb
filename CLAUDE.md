# wckb: developing the `kb` tool

This repository is the `kb` tool. It is *not* a knowledge base. The builtin
LLM policy for stores (instruction snippets, skills, hooks) is in
`src/kb/data/policies.json`, `src/kb/data/policies/` and
`src/kb/data/skills/`. `kb policy enact` writes it into each store's
target.

Open design questions live in `docs/questions.md`. When a change settles or
raises one, update that file.

Read `docs/design.md` before changing behavior. `docs/plan.md` says what's
in scope.

## Layout and layering

- `cli.py` parses arguments, calls logic modules and renders through `ui.py`.
  No data processing goes in either. Every subcommand with required
  arguments shows its help when given none.
- The store is one SQLite file per store: `db` (DDL, guard triggers,
  transactions, the network-filesystem guard) and `records` (`KB`: items,
  blobs, events, settings, query tables kept in step on every write).
  Item JSON stays validated by JSON Schema on every write.
- Logic modules: `records` (model, digest, `KB`), `authoring` (new/add/supersede),
  `edit` (set, renditions, content, checkout/checkin), `sources` (data sources:
  db, cas, url, local, annex), `store` (placing content, hashing, fetch), `checks`
  (consistency scan), `lint` (advice), `graph` (staleness, stamping), `build` (views),
  `index`/`search`/`embed`/`context`, `transforms`, `fair`, `migrate`,
  `scaffold`, `federation` (neighbors) and `federated` (cross-store
  queries), `transfer` (knowledge sets, atomic transfers, multi-path
  reconciliation), `bundle` (bundles, inbox, reviews), `render` (view models
  and formatters for people), `exchange` (bundle pull requests),
  `push` (candidates, push, pull, obtain), `sync` (twins), `policy` (policy elements, enactment, autonomy), `hooks`
  (Claude Code hook entry points), `githooks` (git hooks for exchange repositories), `forge`
  (GitHub/Gitea/Forgejo/Gogs APIs) and `secrets` (secret specs).
- Schemas: hand-written JSON Schema in `src/kb/schemas/v<N>/`. After
  changing them, run `uv run kb schema --markdown > docs/reference/schema.md`
  (a test checks this). Additive changes bump `schema.REVISION`. Breaking
  changes bump `MAJOR` and add a migration in `migrate.MIGRATIONS` with a test.
- Builtin policy elements live in `src/kb/data/`. Bump an element's
  `version` in `policies.json` whenever its text changes.

## Conventions

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
