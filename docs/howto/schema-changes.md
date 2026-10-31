# Changing the schema (maintainers)

The schemas are JSON Schema files in `src/kb/schemas/v<MAJOR>/` in the
`kb` source. Items carry `schema_version` (the major version). Each store
records the revision it needs in its settings (`kb config get kb` shows
`schema`).

The store's own database layout has a separate version (SQLite
`user_version`), upgraded by `kb` itself when it opens a store.

## Additive change: revision bump

A new optional field, a new enum value or a new kind:

1. Edit the schema file(s).
2. Bump `REVISION` in `src/kb/schema.py` (`1.0` becomes `1.1`).
3. Regenerate the reference:
   `uv run kb item schema --markdown > docs/reference/schema.md`.
4. Add a valid example to `tests/test_schema.py`.
5. Release `kb`, then raise each store's requirement once everyone has
   upgraded: `kb config set kb.schema 1.1`. Older `kb` versions then stop
   with "upgrade kb" instead of rejecting the new fields as typos.

No item changes.

## Breaking change: major bump with a migration

A rename, a restructure, or a stricter rule:

1. Copy `schemas/v1/` to `schemas/v2/` and make the change. Update the `$id`s.
2. Write the migration as a pure function from a v1 item to a v2 item, and
   register it as `MIGRATIONS[1]` in `src/kb/migrate.py`.
3. Bump `MAJOR` to 2 and `REVISION` to `2.0`, and test the migration on
   fixture items of every kind.
4. In each store:

   ```
   kb store snapshot /backup/before-v2.sqlite   # a backup first
   kb store migrate --dry-run                   # which items change
   kb store migrate                             # rewrites them and sets the store's schema to 2.0
   kb check
   ```

`kb store migrate` runs in **one transaction**: either every item is upgraded or
none is. Migrations restructure frozen items (sources, released policies)
by design, so the append-only guard is lifted inside that transaction and
restored before it commits. Each upgraded item gets a `migrate` event in
its history.

A migration must preserve meaning: it may reshape how an item says
something, never what it says. That's what reviewers of a migration should
check. Bundles made before a migration carry old-version items, which a
migrated store rejects (`kb bundle check` says so), so migrate stores that
exchange knowledge together.

## Guidelines

- Prefer additive changes. Put experimental fields under `x` first, and
  promote them once the group agrees.
- Every new required field needs `kb new`/`kb add` options or defaults, so
  writing items doesn't get harder.
- Keep descriptions in the schema. They're what `kb item schema KIND` shows to
  LLMs.
