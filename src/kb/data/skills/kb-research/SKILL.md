---
name: kb-research
description: Research a question (papers, docs, model cards, web pages) and record what is learned in the knowledge base (kb) as sources, one-claim observations and a topic that answers the question, so later searches find it and later work builds on it. Use when the user asks to research, survey, compare options, or "look into" something and keep the results, or says "research with kb".
---

# Research with the KB

Goal: an answer the user can use now, and knowledge the next person (or
LLM) can find, check and build on. kb holds three kinds of thing, each
sized for search and for connections:

- **sources** become `document` items, one per page or paper you actually
  relied on;
- **facts** become `observation` items, one checkable claim each, citing
  their source;
- **the answer** becomes one `topic`, a synthesis that cites every
  observation it rests on.

## 1. Frame it

- Restate the question and its constraints, and agree on a budget with the
  user: how many sources (6–12 is a good default), how deep.
- **Ask the KB first:** `kb context "<key words>"` and `kb search ...`
  (`--reach 1` to include neighbor stores). Build on what is there and cite
  it; don't re-file it.
- **Plan the vocabulary before filing anything.** List what exists
  (`kb ls --subjects`, `kb ls --tags`) and choose:
  - a small subject hierarchy for this question, reusing existing paths
    (`area/aspect/<thing>`, e.g. `search/embedding-models/<family>`,
    `search/vector-index/<library>`, `search/evaluation`, `search/kb-design`);
  - a few cross-cutting tags (facets a later search would filter on:
    `license-mit`, `in-process`, `pre-v1` ...), spelled the same way every
    time.

## 2. Sources become documents

One `kb add` per source you rely on; check what kb says it found and where
it kept the file.

```
kb add https://arxiv.org/abs/2402.01613 --subject search/embedding-models/nomic-embed
kb add https://github.com/asg017/sqlite-vec --set doctype=manual \
    --creators "Alex Garcia" --date 2026-11-01 --subject search/vector-index/sqlite-vec --tag readme
```

- **arXiv**: kb gets the title, authors, date and abstract from arXiv, and
  the PDF's text.
- **Web pages**: kb finds the page's title and description and extracts its
  text. A page rarely has a date. Give the date it shows (a release, "last
  updated"), else the day you read it, and say which in the summary.
  **Never a date from memory**: `date` cannot be changed afterwards.
- **A site that refuses kb** (HTTP 403): `kb add URL --no-fetch ...`; if you
  have the page another way, add that copy too.
- Correct a found title or summary that says too little (`kb item set ID
  summary="..."`).

## 3. Facts become observations: one claim each

An observation states **one** checkable fact or comparison, with its
numbers, units, versions and caveats. Not a paragraph about five things. A
claim that fits in one search chunk (under ~1500 characters) is found by
both word and meaning search, and can be cited, corrected or superseded
alone.

```
kb new observation \
  --title "nomic-embed-text-v1.5 needs task prefixes (search_query:/search_document:)" \
  --summary "Queries must start with 'search_query: ' and documents with 'search_document: '; without them retrieval quality drops. kb's [embed] document_prefix/query_prefix settings exist for this." \
  --text "<the claim in full: numbers, versions, where in the source, caveats>" \
  --subject search/embedding-models/nomic-embed --tag query-prefix \
  --by claude --how "read <source URL>" --when 2026-11-01 --start 2026-11-01 \
  --evidence doc-… --related obs-…
```

- **Title**: the claim itself, short ("X has 768 dims"), not "About X".
- **Summary**: 1–3 sentences (at most 400 characters) that let a reader
  decide relevance without opening it, and say why it matters here.
- **Subjects** from your plan, one or two; **tags** for the facets.
- **Attribution**: `--by claude --how "read <source>" --when <today>`.
  **Evidence**: the source document (`doc-…`, or `doc-…#text` when the fact
  is in its extracted text). Status stays `unverified`: you read it, you
  didn't verify it.
- **Validity**: `--start <today>` for claims about current versions, so
  readers know when they held.
- **Connections**: `--related` to the observations it should be read with
  (the same benchmark, the alternative). `related` is one-way, so add the
  link on both items (`kb item set ID --add related=obs-…`).
- Before filing, `kb search` for the claim. If it is there, cite it, or
  correct it with kb-correct if it is wrong.
- Comparisons across sources ("on MTEB v2 retrieval, A 61.8 > B 55.1") are
  observations too, citing every source.

## 4. The answer becomes a topic

Write the synthesis as markdown: the answer first, then the reasoning.
Put `[ID]` after every factual sentence (and table row). Then:

```
kb new topic --title "..." --summary "..." --subject <area>/<design> \
  --derived-from obs-… --derived-from obs-… --text-file page.md
kb item stamp top-…
```

Say what the research did *not* cover, and what would settle the open
points (a benchmark on our own data, say).

## 5. Check that it can be found

Ask the KB the questions a later reader would, in their words:

```
kb search "which embedding model needs prefixes"
kb search in-process
kb ls --tag license-mit
kb context search/vector-index
```

If the right items don't come up, fix the summaries, subjects or tags
(`kb item set`), not the question. Then run `kb check` (no errors) and
`kb lint` (mention its warnings).

## 6. Report

Give the user the answer (with the topic's ID), what you filed (counts, the
subject hierarchy and tags), what you couldn't do, and the kb problems you
hit. Mark items worth a wider audience with
`kb item set ID --add tags=promote` (kb-promote reviews them).

Follow the `capture` autonomy throughout: with `auto`, file as you go and
report; with `ask`, propose the list of items first.
