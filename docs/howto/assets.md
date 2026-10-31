# Assets: files and file sets with versions, from several sources

Use an asset when material has **versions** or lives in **several
places**: a talk's materials that the presenter re-uploads to Indico, copies
on EOS kept as `v1/`, `v2/`, a DataLad dataset you overwrite and save, a
DocDB document. An asset is the stable reference. Its versions are
identified by their content, and its bindings say which sources can supply
them. [Decision 0002](../decisions/0002-assets-and-versions.md) explains
why it works this way.

## Make one and bind its sources

```
kb asset new --type fileset --title "TPC noise talk, collaboration meeting" \
   --summary "Slides and notes of the 28 Oct noise talk."
kb asset sources                       # source types and their status
```

**A directory tree.** Name the root, and map the name to a path on each
machine, so the binding works wherever the tree is mounted:

```
# .kb/local.toml (or [filesystems.*] in ~/.config/kb/config.toml)
[filesystems.eos-group]
path = "/eos/experiment/group"
```

```
kb asset bind as-… copies --source fs --set root=eos-group --set path=talks/noise \
   --versioning pattern --pattern '{version}'       # versions are the subdirectories v1/, v2/ ...
kb asset bind as-… current --source fs --set root=eos-group --set path=talks/noise-latest
```

**URLs**, for one file, or a set as a map from path to URL:

```
kb asset bind as-… indico --source url \
   --locator '{"urls": {"slides.pdf": "https://indico.example/.../slides.pdf"}}'
```

**Versioning modes:**

| mode | meaning |
|---|---|
| `none` | only current content |
| `pattern` | paths with `{version}` |
| `native` | the source's version IDs are the labels |
| `map` | an explicit table: `--map final=v3` |

## Scan and register

```
kb asset scan as-…
```

```
copies       new        = v1      2 file(s) [v1]  eos-group:talks/noise/v1
copies       new        = v2      2 file(s) [v2]  eos-group:talks/noise/v2
current      new        = v2      2 file(s)       eos-group:talks/noise-latest
indico       new                  1 file(s)       1 URLs
```

```
kb asset scan as-… --register
kb asset versions as-…
```

How offers are classed:

| status | meaning |
|---|---|
| `known` | the content matches a registered version, whatever the source calls it |
| `new` | content not seen before; `--register` records it |
| `conflict` | a source offers different content under a label that already means something else (e.g. someone rewrote `v1/`), or two different things under one label. Reported, never registered |
| `unreachable` | not mounted here, remote with `--no-remote`, or a planned source type |

Labels come from versioned sources. Otherwise the next free `vN` is used.

**Keeping content.** Content first seen in an unversioned source (a plain
directory, a URL, Indico) is copied into the store by default
(`--keep auto`), because that source will overwrite it. Use `--keep no` to
skip this, or `--keep yes` to always keep. `kb asset add as-… PATH` registers
local files as a new version and keeps them.

## Resolve: one verified version

```
kb asset resolve as-…                 # latest
kb asset resolve as-…@v1 --to /tmp/v1
kb asset resolve as-…@current         # what the highest-priority source has now, registered or not
kb asset resolve as-…@v2 --path slides.pdf --json
```

How a version is resolved:

1. Sources are tried in priority order: the store, then filesystems,
   DataLad, DocDB, URLs, Indico. A binding's `--priority` overrides this.
2. The first source whose bytes match the version's digest wins.
3. A copy that claims the version but has other content is reported as
   divergent and never used.

## Cite an asset from an item

A rendition can be supplied by an asset version. Its own sha256 still pins
the bytes:

```json
{"name": "original", "role": "original", "media_type": "application/pdf", "sha256": "…", "size": 4821133,
 "locations": [{"type": "asset", "asset": "as-…", "version": "v2", "path": "slides.pdf"}]}
```

`kb path DOC#original` then resolves through the asset, from whichever
source has v2.

## Moving assets between stores

Assets travel like any item. Versions are united by label (a shared label
must have the same digest), bindings are united by name, and content kept
in the store travels with the asset. Filesystem bindings use named roots,
so the receiver maps `eos-group` to its own mount.
