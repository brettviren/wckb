# Topics: the synthesis layer

A topic is a page about a subject, such as "TPC coherent noise, run period
2026-Q4", maintained mostly by LLMs and read first by everyone else. It's
the most valuable thing in the KB, and the easiest to let drift.

## Rules

- Every factual sentence cites an item: `Run 1243 was noisy [obs-1kfvnqgzgv].`
- Every cited item is in `derived_from`.
- Cite current items. If you mention history, cite the old item
  *as history*: "earlier placed in run 1234 [obs-old], corrected to 1243
  [obs-new]".
- After every edit: `kb item stamp TOPIC`. This pins each input's digest, so
  `kb item stale` knows when the topic needs revising.

## Create

```
kb new topic --title "TPC coherent noise" \
  --summary "What is known about coherent noise in the TPC, by run period, with causes and mitigations." \
  --subject detector/tpc/noise --derived-from obs-1kfvnqgzgv --derived-from doc-3vq8r2m1kd \
  --text-file page.md
kb item stamp top-...
```

## Revise

```
kb item content top-... > page.md               # the current text
# ... edit page.md ...
kb item content top-... --file page.md          # put it back
kb item set top-... --add derived_from=obs-new
kb item stamp top-...
kb check
```

Topics aren't sources, so they can be edited freely; every change is an
event in their history (`kb log top-...`). Stamping pins the digest of each
input, so `kb item stale` can tell when the topic needs revising again.

## When to make one

`kb ls --subjects` shows item counts per subject. A subject with more
than a handful of items and no topic is a gap. The `kb-curate` skill
looks for these.
