# Storage, size and sharing

## What goes where

| content | where |
|---|---|
| items (metadata), notes, topic pages, view sources, extracted text, small data (1 MiB or less) | the store's database, `kb.sqlite` (location `db`) |
| large files you have | a content-addressed store (location `cas`): `<path>/sha256/ab/cd/<sha256>` |
| material that has a home (Indico, arXiv, CDS, EOS) | its URL (location `url`), plus a hash, plus optionally a `cas` copy |
| files only on one machine | location `local`, flagged by `kb lint` |
| files in a DataLad dataset | location `annex` (experimental) |
| URL cache, materialized files, view builds | `.kb/`, beside the store, this machine only |
| published views | the publish store |

The inline limit is the store setting `limits.max_inline_bytes`
(`kb config get limits`), and `kb check` enforces it.

Each location type is served by a **data source**, an interface in
`kb.sources`. Sources resolve a location to a local file (fetching or
materializing as needed) and describe it for people. New kinds of storage
plug in there.

DataLad is the first planned addition: an `annex` location names a dataset
and a path. You map the dataset's local clone with
`kb config set datasets.<dataset-id>.path ~/data/ds`, or per machine in
`.kb/local.toml`. Content is then fetched with `datalad get`. This is
experimental until spike S3 (see `docs/decisions/0001-knowledge-store.md`).

## The database lives on local disk

SQLite must not be written on a network filesystem: EOS, NFS, SMB, sshfs or
an rclone mount. Locking there is unreliable, and corruption follows. `kb`
refuses to open a store for writing on one (`KB_ALLOW_NETWORK_FS=1`
overrides, at your own risk). Reading a store there works, read-only.

Content-addressed stores are different. They hold plain files that are
written once and never changed, so EOS and NFS are fine for them.

## Content-addressed stores

Declare a store for everyone, then give its path as mounted on each machine:

```
kb config set stores.default '{"path": "/eos/experiment/group/kb-cas"}'
```

```toml
# .kb/local.toml on a laptop that mounts EOS elsewhere
[stores.default]
path = "/Volumes/eos/experiment/group/kb-cas"
```

Any shared filesystem works: EOS, NFS, CVMFS (read only), or an rclone
mount of S3. Plain copying, `rsync` and backups are safe.

## Local-only files

Without a content store, a large file you add is recorded as `local` (your
host and path). That's better than nothing, because the hash and
description are shared, but `kb lint` flags it until the content is
somewhere others can reach. Add a mirror. This is allowed even on sources,
because the content must match the recorded hash:

```
kb rendition locate dat-...#original --store default     # copy into the content store
kb rendition locate dat-...#original --url https://...   # or point at an upload
```

## Sharing a store: snapshots

To let others read a store (a group store especially), publish a
**snapshot**: a consistent single-file copy.

```
kb snapshot /eos/experiment/group/www/kb/tpc.sqlite
```

Don't copy `kb.sqlite` itself. Recent writes may still be in its `-wal`
file. Readers declare the snapshot as a neighbor by URL and fetch it:

```
kb neighbor add tpc --relation up --url https://example.org/group/kb/tpc.sqlite --shared
kb neighbor fetch tpc
```

Writes to a shared store go through bundles ([review](review.md)), never
through the snapshot.

## Backups

`kb snapshot DEST` is also the backup: it's safe while the store is in use.
A nightly job writing dated snapshots next to the content store is enough
for most stores.

`kb check --deep` re-hashes every stored blob. Run it on a schedule.

## Size

A store holds metadata and small text, so tens of thousands of items are
small. High-volume series belong in one dataset per period, not thousands
of items ([datasets.md](datasets.md)).
