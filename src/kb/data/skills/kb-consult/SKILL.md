---
name: kb-consult
description: Answer a question from the group knowledge base (kb) before working it out from scratch. Use when the user asks about detector conditions, runs, configurations, past analyses, papers, talks or anything the group may already know, or says "check the KB".
---

# Consult the group KB

`kb` finds the store from the working directory (`./kb` in a project, or
`~/kb`). Elsewhere, use `$KB_ROOT` or ask the user for the path, and pass
`kb -C PATH`.

1. Start broad and cheap:
   `kb -C PATH context "<subject path or key words>" [--runs A-B]`.
   It lists topics first (the synthesis) and then current items with their
   summaries.
2. If that misses, search:
   - `kb search "words"` (exact words, BM25);
   - `kb search -s "what it means"` (semantic, if embeddings are set up);
   - `kb ls --subjects` to see the subject vocabulary.
3. Read only what you need: `kb context ID` for one item, its inputs and its
   users; `kb content ID text` for the extracted text of a document.
4. Answer with citations: every fact gets its item ID, e.g. "run 1243 was
   noisy [obs-7k2m9q4xha]". Say what each fact's status is
   (`unverified`/`verified`/`disputed`). Never cite superseded or retracted
   items as current; `kb context` marks them.
5. If the KB is silent or contradicts itself, say so. Contradictions are
   worth reporting to the user; they may want `kb-curate`.
6. If you learned something the KB lacks, offer to record it (`kb-capture`).
   Don't write without asking.
