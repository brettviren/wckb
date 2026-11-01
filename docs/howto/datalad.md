# DataLad datasets

[DataLad](https://www.datalad.org/) datasets (git + git-annex) are a natural
home for raw and derived files. kb leaves the files there and refers to
them, so knowledge items stay small and the bytes are fetched only when
someone needs them. See [decision 0003](../decisions/0003-datalad-sources.md)
for the design.

## Install

DataLad comes with kb. git-annex must be on PATH too (`apt install
git-annex`):

```
kb source types                             # datalad: implemented (and what is missing, if anything)
```

Without git-annex, kb still reads content already present in a local clone,
but cannot clone or fetch.

## Describe a dataset: source items

Each dataset a store uses is described by a **source item** (`src-…`): the
dataset's ID, the URLs it can be cloned from and local trees that hold it.

```
kb source add ~/data/tpc-calib                        # a dataset you have; its git remotes are recorded
kb source add ~/data/tpc-calib https://github.com/org/tpc-calib.git   # your clone and where it is shared
kb source add https://github.com/org/tpc-calib.git    # cloned (see below) to learn its ID
kb source list                                        # sources here, and whether each is cloned on this machine
kb source show src-…                                  # URLs, paths, the clone here, items that use it
```

`kb source add` works for any kind of source: it guesses the type from
what you give it (a DataLad dataset directory; a URL whose repository has a
git-annex branch), or takes `--type datalad`. A directory counts as a
dataset when it has `.datalad/config`, or other signs of DataLad or
git-annex control (git-annex initialized, annex settings in
`.gitattributes`, `[DATALAD]` commits, subdatasets in `.gitmodules`). A
dataset created without a dataset ID is identified by its first commit,
which every clone shares; kb accepts it with a warning (often `datalad
create` stopped early, for example because a git ignore rule matched
`.gitattributes`). A git repository with no sign of DataLad or git-annex is
a `git` source (identified by its first commit, cloned with plain git, no
DataLad needed; assets can bind to it with `--source git`, versions from
tags). A plain directory is a `filesystem` source, but only with
`--type filesystem` ([assets](assets.md)). `--just-guess` only says what
it would use, or why it can't tell, and adds nothing:

```
kb source add --just-guess https://github.com/org/tpc-calib.git
```

Given a clone and a URL together, kb clones nothing, and checks that the URL
shares the clone's history (`--no-check` skips that). Adding the same
dataset again (another URL, another path) extends the existing item.

## Add files from a dataset

A file that is committed in a dataset is added *by reference*:

```
kb add ~/data/tpc-calib/runs/1243/gains.csv --kind dataset --title "Gains, run 1243" --summary "..."
kb show dat-…        # original: DataLad dataset tpc-calib: runs/1243/gains.csv (clone at ...)
```

kb records the source item, the path and the git-annex key. If the store
has no source item for the dataset yet, `kb add` makes one. Files not yet
saved in the dataset are placed as usual (database, content store); run
`datalad save` first to keep them in the dataset. `--place datalad` insists
on the dataset.

`kb rendition path ID#original` gives a local file, fetching the content
(`datalad get`) when needed.

## Sharing: knowledge sets carry their sources

A knowledge set, bundle, push or pull always includes the source items its
items need, whatever its depth. The receiver gets the dataset's URLs and
clones it only when content is first needed.

If the receiver already has a clone of the same dataset (even described by
its own, different source item), kb finds it by dataset ID and uses it; a
transfer plan notes "the same datalad source as src-… here".

## Where clones go

kb looks for a local clone, in order:

1. an explicit mapping, `datasets.<dataset-id>.path`, in your
   `config.toml`, the store's settings or `.kb/local.toml`;
2. kb's registry of clones (`datasets.toml` beside your `config.toml`), which
   kb keeps up to date and every store of yours shares;
3. a path the source item lists for this host;
4. the source directory.

A dataset you have elsewhere: `kb source link src-… ~/data/tpc-calib`.

New clones go to the **source directory**, `sources.datalad.dir`, by default
`~/.cache/wckb/datalad/`:

```toml
# ~/.config/kb/config.toml
[sources.datalad]
dir = "~/data/datalad"       # where new clones go
new = "ask"                  # or "clone" (the default): ask where each new dataset goes
```

With `new = "ask"`, kb asks at the terminal, and stops with instructions
when nobody can be asked (an LLM, a script):

```
kb source clone src-… [--to DIR]
```

URL downloads follow the same pattern: `sources.url.dir`, by default
`~/.cache/wckb/url/`.

## Indico sites

An Indico site becomes a DataLad dataset filled by
[git-annex-remote-indico](https://github.com/brettviren/git-annex-remote-indico)
(install it: `uv tool install git+https://github.com/brettviren/git-annex-remote-indico`).
Give `kb source add` the URL of a category or event:

```
kb source add https://indico.bnl.gov/category/201/ -o since=2025-01-01
kb source add https://indico.bnl.gov/event/27078/            # into the same dataset
datalad get ~/.cache/wckb/indico/indico.bnl.gov/indico.bnl.gov/event/27078
```

- A URL is an Indico URL when a label of its host name is `indico`;
  otherwise say `--type indico`.
- kb makes the site's dataset once, in `sources.indico.dir/HOST` (by default
  `~/.cache/wckb/indico/indico.bnl.gov`), or at `--target DIR`. Each add
  ingests what the URL names under `datalad run`, so `datalad rerun`
  refreshes it.
- The source item (type `indico`, identity the dataset ID) records the site
  as `service`. Later URLs of the site go into the dataset that item has here.
- `-o KEY=VALUE` passes options to `git indico ingest`: `since`, `until`,
  `max-size`, `type`, `materials`, `events` (`kb source types` lists them).
- Tokens are git-annex-remote-indico's (git config `indico.<url>.tokencommand`),
  not kb's.

## Sharing among the users of one computer

Several people on one computer can share a dataset, so that each file is
stored once. One user owns the **shared dataset** (a person, or an account
such as `datalad`). Everyone else works in a **shared clone** of it, whose
annex is the owner's. Both need a Unix group they are all in.

As the owner, make the source shared:

```
kb source add --shared --target /srv/datalad/indico/bnl https://indico.bnl.gov/category/201/ -o since=2025-01-01
kb source add --shared --target /srv/datalad/tpc-calib https://github.com/org/tpc-calib.git
```

`--shared` with a URL makes the source as its owner would. The dataset gets
`core.sharedRepository=group`, the group's permissions and setgid
directories. `--group` names the group; by default it is the group of the
target's directory. Without `--target`, shared sources go in
`sources.<type>.shared_dir`.

As a user, make your shared clone:

```
kb source add --shared /srv/datalad/indico/bnl
```

`--shared` with a local dataset makes a DataLad clone with `--reckless
ephemeral`, in your source directory or at `--target`. The clone has
`core.sharedRepository` set. If the dataset belongs to another user, kb
adds it to git's `safe.directory`. `datalad get` in your clone stores the
file once, in the owner's dataset. Adding another URL of an Indico site
whose dataset you have as a shared clone ingests it into the owner's
dataset, then merges it into your clone.

`kb source share` does the same to a directory you already have, by its
role:

```
kb source share /srv/datalad/indico/bnl              # the owner's: (re)share it, repair permissions
kb source share ~/indico/bnl                         # a shared clone made by hand: set it up
kb source share --unshare /srv/datalad/indico/bnl    # the owner's: the group may read, no longer add
kb source share --unshare ~/indico/bnl               # a shared clone: give it an annex of its own
kb source share --type filesystem /eos/group/talks   # a plain directory tree (only with --type)
```

Things to know:

- **Don't drop in a shared clone.** `datalad drop` there removes the file
  for everyone. Drop in the owner's dataset.
- **What you get, the group can read.** git-annex-remote-indico refuses the
  files of protected events in a shared dataset or clone, unless
  `indico.<url>.shareprotected` is true.
- **The owner's dataset doesn't know what clones got** until
  `git annex fsck --fast` runs in it.

## Versioned material: assets from a dataset

An [asset](assets.md) can bind to a dataset. Its versions are the dataset's
git tags, paths matching a pattern, or the current tree:

```
kb asset bind as-… calib --source datalad --set source=src-… --set path=talks/noise --versioning native
kb asset scan as-… --register        # each tag is a version
kb asset resolve as-…@v1 --to /tmp/v1
```

Listing uses git only; hashes come free when the dataset uses `SHA256E`
annex keys (DataLad's default is `MD5E`, in which case content is fetched
and hashed once).

## Troubleshooting

`kb doctor` knows:

- `datalad-support`: sources in use but DataLad not installed;
- `gitattributes`: your git ignore rules ignore `.gitattributes`, which
  DataLad needs;
- `clones`: datasets used here but not cloned on this machine
  (`kb doctor clones --fix`).
