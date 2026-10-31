# Topics: the synthesis layer

A topic is a page about a subject, such as "TPC coherent noise, run period
2026-Q4", maintained mostly by LLMs and read first by everyone else. It's
the most valuable thing in the KB, and the easiest to let drift.

## Rules

- Every factual sentence cites a record: `Run 1243 was noisy [obs-1kfvnqgzgv].`
- Every cited record is in `derived_from`.
- Cite current records. If you mention history, cite the old record
  *as history*: "earlier placed in run 1234 [obs-old], corrected to 1243
  [obs-new]".
- After every edit: `kb stamp TOPIC`. This pins each input's digest, so
  `kb stale` knows when the topic needs revising.

## Create

```
kb new topic --title "TPC coherent noise" \
  --summary "What is known about coherent noise in the TPC, by run period, with causes and mitigations." \
  --subject detector/tpc/noise --derived-from obs-1kfvnqgzgv --derived-from doc-3vq8r2m1kd \
  --text-file page.md
kb stamp top-...
```

## Revise

```
kb content top-... > page.md               # the current text
# ... edit page.md ...
kb content top-... --file page.md          # put it back
kb set top-... --add derived_from=obs-new
kb stamp top-...
kb check
```

Topics aren't sources, so they can be edited freely; every change is an
event in their history (`kb log top-...`). Stamping pins the digest of each
input, so `kb stale` can tell when the topic needs revising again.

## When to make one

`kb ls --subjects` shows record counts per subject. A subject with more
than a handful of records and no topic is a gap. The `kb-curate` skill
looks for these.
