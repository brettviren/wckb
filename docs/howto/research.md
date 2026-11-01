# Research with kb

Ask Claude to look into something, and keep what it learns: the sources it
read, the facts it found, and its answer, in a form the next person (or
LLM) can find, check and build on. The `kb-research` skill does this.

This guide follows a real run: "which text embedding models and vector
indexes would be good for kb?", researched by a Claude subagent into the
wckb store on 2026-11-01. It filed 17 sources, 23 observations and one
topic, `top-q4vh7h3cbb`.

## 1. Make the skill available

`kb-research` is a builtin skill, selected by default in new project and
home stores. For an existing store:

```
kb policy use kb-research
kb policy enact            # writes .claude/skills/kb-research/SKILL.md beside the store's CLAUDE.md
```

The store's `capture` autonomy decides whether Claude files as it goes
(`auto`) or proposes the items first (`ask`): `kb policy autonomy`.

## 2. Ask

Say what you want to know, the constraints that matter, and how far to go:

> Research which text embedding models and vector index libraries would
> suit kb: local CPU, private text, up to ~100k chunks, simple dependencies.
> Use about 10 sources and record it in kb.

Claude then follows the skill:

1. **Frame**: asks the KB first (`kb context`, `kb search`), and plans a
   subject hierarchy and a few tags before filing anything. Here that was
   `search/embedding-models/<family>`, `search/vector-index/<library>`,
   `search/evaluation` and `search/kb-design`, with tags such as
   `license-mit`, `in-process`, `matryoshka`, `query-prefix` and `pre-v1`.
2. **Sources become documents.** One `kb add` per source. kb gets title,
   authors, abstract and text from arXiv, and the title, description and
   text of web pages:

   ```
   kb add https://arxiv.org/abs/2402.01613 --subject search/embedding-models/nomic-embed
   kb add https://github.com/asg017/sqlite-vec --set doctype=manual --creators "Alex Garcia" \
       --date 2026-11-01 --subject search/vector-index/sqlite-vec --tag readme
   ```

3. **Facts become observations, one claim each**, with numbers, a summary
   that says why it matters, attribution (`--by claude --how "read <URL>"`),
   the source as evidence, a validity start date, and typed links to the
   observations it should be read with (`--link alternative-to=obs-…`,
   `--link compares-with=obs-…`; the first run used untyped `--related`):

   ```
   obs-3hnqws1va3   nomic-embed-text-v1.5 needs task prefixes (search_query:/search_document:)
   obs-rz4v6tdq8p   all-MiniLM-L6-v2 truncates input at 256 word pieces
   obs-v3epyrdpe8   sqlite-vec is pre-v1 and has no ANN index; kb requires >=0.1.9
   ```

4. **The answer becomes a topic** that cites an observation after every
   sentence, lists them in `derived_from`, and is stamped.
5. **It checks the answer can be found**, by asking the KB what a later
   reader would.

## 3. Use what it found

```
kb show top-q4vh7h3cbb                             # the answer, for people
kb item content top-q4vh7h3cbb                     # its page: every sentence cites an item
kb context search/vector-index                     # everything on vector indexes, topic first
kb search which embedding model needs prefixes     # a question works: items with all, then some, of its words
kb ls --tag in-process                             # filter by a facet
kb ls --tags                                       # the facets in use
kb open doc-3ds8nvhngr                             # a source's page, in your browser
kb open doc-3ds8nvhngr --text                      # the text kb searched
```

Each observation says where it came from (`kb show obs-…`: asserted by,
evidence) and when it held (`applies to`). The status is `unverified`, because
Claude read the sources but did not check them. When you have checked a
claim against its source, mark it with `kb item set obs-… status=verified`
(evidence is already there).

## Why items are sized like this

- **One claim per observation.** A search hit, a citation, a correction
  (`kb item supersede`) or a contradiction concerns one claim, not a
  paragraph of five. A claim under ~1500 characters is also one search
  chunk, so word and meaning search see it whole.
- **Summaries for the next reader.** A summary of 1–3 sentences says what
  the claim is and why it matters, so search results can be judged without
  opening each item.
- **Subjects for browsing, tags for facets.** `kb context SUBJECT` walks a
  hierarchy, and `kb ls --tag` cuts across it (all the MIT-licensed things,
  all the pre-v1 libraries).
- **Links.** Evidence ties a claim to its source, typed links tie it to the
  claims it supports, contradicts or is an alternative to, and the topic's
  `derived_from` ties the answer to every claim. Each item shows the links
  to it as well (`kb item links ID`: "supported by", "used to build"), and
  `kb item stale` knows which topics to revise when a claim is corrected.
- **Dates say what they date.** `--start` says when a claim was true. A
  document's `date` is when it was published, and only when the source
  shows it (it cannot be changed later). Other dates are qualified and
  correctable: kb records `accessed` (when it read the page) and the
  server's `modified`; give `--dated published=...` and the like yourself.

## What the first run taught kb

Running this research uncovered problems in kb, which are now fixed:

- web pages were stored untyped and unsearched;
- tags could not be searched or filtered;
- a question found nothing unless every word matched;
- the prompt hook named one topic five times;
- errors did not say which option was missing.

Its other lessons are now part of kb too ([questions](../questions.md) S12):
back links and typed links, recorded fetch failures, the main content of
web pages, qualified dates, and `kb new KIND --help`. When something
in kb gets in the way of a research run, say so in the report: that is how
kb improves.
