# Views: HTML and PDF products

A view is a recipe. Its sources (a document, scripts, a build file) are
held in the store as the view's `source` renditions. `kb build`
materializes them into a work area (`.kb/build/<id>/work`) and runs the
tool there. `kb publish` copies the result to the publish store as an
immutable version.

## Make one

```
kb new view --title "TPC noise, Q4 summary" \
  --summary "Two-page summary of TPC coherent noise in runs 1200-1300 for the collaboration meeting." \
  --subject detector/tpc/noise \
  --derived-from top-e6mv1m318t --derived-from dat-4h2k9m3qpz#original \
  --set build.tool=quarto --set build.target=index.qmd --set 'build.formats=["html","pdf"]'
kb checkout view-... ~/work/noise-view        # record.json plus the current sources
# ... write index.qmd, scripts/plot_noise.py, pixi.toml ... in ~/work/noise-view
kb checkin ~/work/noise-view                  # new and changed files become the view's sources
```

Edit sources the same way later: check out, edit, check in. Files you add
become sources, and files you delete are dropped. `kb checkin --dry-run`
validates without applying. Scripts find their inputs through the environment.
They never hard-code paths:

| variable | meaning |
|---|---|
| `KB_INPUTS` | JSON file: `{ref: {record, renditions: {name: path or null}}}`. `record` is the path of the input item's JSON. A rendition named in the ref (`dat-…#original`) is fetched as needed; others are filled in only when already local |
| `KB_OUT` | where final products must go |
| `KB_WORK` | the work area holding the materialized sources (the build's current directory) |
| `KB_ROOT` | the store's directory |

```python
# scripts/plot_noise.py
import json, os
import pandas as pd, matplotlib.pyplot as plt
inputs = json.load(open(os.environ["KB_INPUTS"]))
csv = inputs["dat-4h2k9m3qpz#original"]["renditions"]["original"]
df = pd.read_csv(csv)
df.plot(x="run", y="rms"); plt.savefig("noise.png")      # into KB_WORK; quarto picks it up
```

## Pick a tool

| tool | when | build field | what kb runs |
|---|---|---|---|
| `pandoc` | plain markdown, no code | `target`, `formats` | `pandoc TARGET -s -o $KB_OUT/STEM.FMT` per format |
| `quarto` | documents with code, HTML and PDF from one source | `target`, `formats` | `quarto render TARGET --to FMT --output-dir $KB_OUT` |
| `make` | a few scripts | optional `target` | `make KB_OUT=$KB_OUT [target]` |
| `snakemake` | real pipelines with many steps | optional `target` | `snakemake --cores all --config kb_out=$KB_OUT [target]` |
| `command` | anything else (justify it in review) | `command` (argv; `{out}`, `{inputs}`, `{work}`, `{view}` substituted) | that argv |

A minimal `Makefile` view:

```make
all:
	python3 scripts/table.py > $(KB_OUT)/table.md
	pandoc $(KB_OUT)/table.md -s -o $(KB_OUT)/index.html
```

Pin the software environment next to the sources (`pixi.toml`/`pixi.lock`,
`uv.lock`, `environment.yml`). Those files are sources, so changing them
marks the view stale.

## Build

```
kb build view-...                    # output in .kb/build/view-.../out/, log in build.log
kb stale                             # empty for this view now
```

The build records a manifest as a rendition of the view: the digest of
every input, the hashes of the sources and outputs, and the tool version.
Because it's stored with the view, anyone holding the store, or a copy of
the view pushed elsewhere, can tell when the view is stale.

## Publish

```
kb publish view-...
```

This copies `out/` to `<publish_path>/<view-id>/<version>/`, where the
version is a short hash of the manifest, and points `latest` at it. It also
logs a `publish` event (`kb log view-... --action publish`). Old versions are never
overwritten, so links you shared keep working. Publishing refuses a stale
view.

## Build the world

```
kb build --stale            # every stale view, in dependency order (views may use other views)
kb build --world            # re-extract stale renditions, reindex, then build stale views
kb build --world --publish  # ... and publish them (e.g. nightly in CI)
kb dag --format dot | dot -Tsvg > kb.svg   # see the graph
```

`kb` owns the graph *between* items (content hashes, topological order).
Each view's own tool owns the graph *inside* the view.
