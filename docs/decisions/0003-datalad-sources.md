# 0003: DataLad datasets as sources, and source items

- **Status:** accepted 2026-10-31. Replaces the experimental `annex` location
  of [0001](0001-knowledge-store.md) §6 and settles spike S3's first part
  (questions S4, DS0, DS1, V9).
- **Builds on:** [0002](0002-assets-and-versions.md) (assets, bindings,
  versions).

## Decisions

1. **Zero or more DataLad datasets per store.** A dataset is not tied to a
   store or a project. Each one a store uses is described by a **source
   item** (kind `source`, ID prefix `src-`): `source_type = "datalad"`, the
   dataset ID as `source_id`, the URLs it can be cloned from (`urls`), and
   local file trees that hold it on particular hosts (`paths`). A dataset is
   given in any way DataLad accepts: a remote URL, a local tree, or both.
   The identity (`source_type`, `source_id`) is frozen; `urls` and `paths`
   grow, and are united when the item travels. `kb source add` is not
   DataLad-specific: each source type recognizes its targets (paths, URLs)
   and the type is guessed unless given; several targets may name one
   source (a local clone and its remote URL, checked to share history).
   A dataset is recognized by its `.datalad/config`, or by other signs of
   DataLad or git-annex control (git-annex initialized, annex settings in
   `.gitattributes`, `[DATALAD]` commits, subdatasets with DataLad IDs).
   One created without a dataset ID is identified by its first commit
   (`git:<sha>`), which every clone shares.

2. **Knowledge sets carry their sources.** Whatever a set's depth, the
   expansion includes the source items its items need for their content:
   those named by rendition locations (`{"type": "datalad", "source": "src-…",
   "path": …, "key": …}`) and by asset bindings
   (`locator = {"source": "src-…", "path": …}`). A receiving store can then
   clone the dataset when, and only when, content is needed.

3. **Clones are per machine and per user, found by dataset ID.** Where a
   dataset is on this machine is never part of an item. `kb.datasets.locate`
   looks, in order, at an explicit mapping (`datasets.<id>.path` in
   settings or config), kb's registry of clones (`datasets.toml` beside the
   user's config, shared by all the user's stores), the item's `paths` for
   this host, and the source directory. So a clone the user already has is
   reused, also when it was described by a different source item, in
   another store, for the same dataset: the two items are associated by
   their `source_id` (a transfer plan says so), and share the clone.

4. **Novel datasets go to a configured place, or kb asks.** Each source
   type that keeps local copies has a *source directory*,
   `sources.<type>.dir`, by default `~/.cache/wckb/<type>/`: DataLad
   clones go to `~/.cache/wckb/datalad/<name>-<id8>/`, URL downloads to
   `~/.cache/wckb/url/` (content-addressed, shared by all stores). With
   `sources.datalad.new = "ask"`, kb asks where to clone a dataset it has
   not seen, or, when nobody can be asked (an agent, a script), stops and
   says how to proceed (`kb source clone`, `kb source link`).

5. **DataLad is an optional dependency.** `pip install 'wckb[datalad]'`
   (plus git-annex). Without it, kb still reads content already present
   in a local clone; cloning and fetching need it, and `kb source types`
   and `kb doctor` say what is missing.

6. **Location and asset source both.** A file committed in a dataset is
   added with a `datalad` location (no copy into the store), its annex key
   recorded so the exact content can be fetched even after the file
   changes at HEAD. An asset can bind to a dataset: versions are its git
   tags (`native`), paths by a pattern, or the current tree (`none`).
   Files are listed from git without fetching; hashes come from `SHA256E`
   keys where a dataset uses them, otherwise from fetched content (DataLad's
   default backend is `MD5E`).

## Consequences

- The pattern of decision 4 applies to future sources: an Indico or DocDB
  server becomes a source item (its identity: the server; its places: API
  URLs), with a source directory for downloaded material.
- `datalad run` provenance (S5) is still open.
- DataLad's annex objects are read-only; tests and tools that delete
  clones need to make them writable first.
