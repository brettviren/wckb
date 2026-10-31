## KB habits for every substantial request

Treat the KB as your working memory for this scope. The point is that the
human doesn't have to remind you.

- **Before answering**, when the request touches facts about this
  project, its data, its decisions or its history, run
  `kb context "<key words or subject>"` (add `--reach 1` for neighbor stores
  when the local store is thin). Use what it returns and cite item IDs.
  If a "KB may be relevant" note was injected into the prompt, follow it up
  only if it is relevant.
- **While working**, when a fact, decision, number, pitfall or result is
  established that someone would want later, file it in this store. Follow
  the `capture` autonomy:
  - `auto`: file it with `kb new` (or `kb add` for files) and say so in one
    line;
  - `ask`: propose the item in one line and file it if the human agrees.

  Prefer few, specific, attributed items over many vague ones, and don't
  file what is already there (`kb search` first).
- **When something you find contradicts the KB**, say so and offer
  `kb supersede` (see kb-correct). Don't silently work around it.
- **Mark what deserves a wider audience** with `kb set ID --add tags=promote`.
  Promotion itself is a separate review (kb-promote).
- Keep it light: a lookup or a filing is one command. Don't narrate KB
  mechanics at length.
