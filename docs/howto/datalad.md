# DataLad datasets

[DataLad](https://www.datalad.org/) datasets (git + git-annex) are a natural
home for raw and derived files. kb leaves the files there and refers to
them, so knowledge items stay small and the bytes are fetched only when
someone needs them. See [decision 0003](../decisions/0003-datalad-sources.md)
for the design.

## Install

DataLad support is optional:

```
uv tool install --editable '.[datalad]'     # or: pip install 'wckb[datalad]'
kb source types                             # datalad: implemented (and what is missing, if anything)
```

git-annex must be on PATH too. Without DataLad, kb still reads content
already present in a local clone, but cannot clone or fetch.

## Describe a dataset: source items

Each dataset a store uses is described by a **source item** (`src-…`): the
dataset's ID, the URLs it can be cloned from and local trees that hold it.

```
kb source add ~/data/tpc-calib                        # a dataset you have; its git remotes are recorded
kb source add https://github.com/org/tpc-calib.git    # cloned (see below) to learn its ID
kb source list                                        # sources here, and whether each is cloned on this machine
kb source show src-…                                  # URLs, paths, the clone here, items that use it
```

Adding the same dataset again (another URL, another path) extends the
existing item.

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
