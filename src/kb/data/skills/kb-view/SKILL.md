---
name: kb-view
description: Create, rebuild or publish a human-facing view (HTML page, PDF note, slides) from the group knowledge base (kb), with figures and tables generated from KB datasets. Use when the user wants a report, summary page, technote or plots built from KB content.
---

# Make a view

A view is an item whose `source` renditions are its files: a document plus
any scripts. `kb view build` materializes them in a work area, runs the tool, and
pins every input in a manifest.

1. **Gather.** Run `kb context <subject>` and pick the input items.
   Prefer topics and current, verified items. Ask the user who the audience
   is and which format they want (html, pdf).
2. **Scaffold:**

   ```
   kb new view --title "..." --summary "..." --subject ... \
     --derived-from top-... --derived-from dat-...#data ... \
     --set build.tool=quarto --set build.target=index.qmd --set 'build.formats=["html","pdf"]'
   ```

   Then write its source files: `kb item checkout VIEW DIR`, create the files in
   DIR, and `kb item checkin DIR` (files added there become sources):
   - `index.qmd` or `index.md`: the document. Cite item IDs.
   - `scripts/*.py` to make figures and tables. Read inputs through
     `$KB_INPUTS`, a JSON map of ref to `{item, renditions: {name: path}}`,
     never through hard-coded paths. Write outputs under `$KB_OUT`
     (`$KB_WORK` is the work area holding the sources).
   - Pin the environment (`pixi.toml`, `uv.lock` or `environment.yml`) if
     the scripts need more than the standard library.

   Choose the tool:
   - `quarto` for documents that run code;
   - `pandoc` for plain markdown;
   - `make` for a few scripts;
   - `snakemake` for real pipelines;
   - `command` only with a reason.

   With make or snakemake, the final products must land in `$KB_OUT`.
3. **Build:** `kb view build VIEW`. On failure, read `.kb/build/VIEW/build.log`.
   Look at what you made before calling it done: open the HTML, or check
   the PDF's page count and figures.
4. **Publish** only when the user asks: `kb view publish VIEW`. Give them the path
   or URL it prints. Published versions are immutable; republishing after a
   rebuild makes a new version beside the old one.
5. **Refresh** an existing view: `kb item stale` says why it is stale. Update the
   inputs (`kb item set`) or sources (checkout and checkin), rebuild, and show the
   user what changed.
