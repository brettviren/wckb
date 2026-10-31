---
name: kb-curate
description: Periodic semantic review of the group knowledge base (kb): resolve overlapping or contradictory claims, revise stale topics, follow up old unverified claims, improve weak summaries. Use when the user asks to tidy, review, lint or curate the KB.
---

# Curate the KB

`kb check` covers the mechanical rules. This skill covers judgment, and a
person decides.

1. Run `kb reindex && kb lint --json`, and group the findings by code.
2. **`overlap`** (two current observations, same subject, overlapping runs):
   read both with `kb context ID`, then classify them:
   - **consistent** (same claim twice): keep the better-attributed one, and
     propose `kb retract` for the other with "duplicate of ...";
   - **complementary** (different facets): no change, but the topic page
     should mention both;
   - **contradictory**: don't decide yourself. Present both to the user
     with their attribution and evidence. Mark them
     `kb set ID status=disputed` if the user agrees, then supersede once
     resolved.
3. **`stale`** topics: revise as in kb-correct step 3, then `kb stamp`.
4. **`unverified`** older than the threshold: look for evidence (a document,
   snapshot or elog URL) in the KB. Propose `verified` with `--evidence`,
   `disputed`, or retraction. List what you couldn't settle for the user.
5. **`summary` and `subject`** findings: write proper summaries and subjects
   (allowed on sources). Reuse existing subject paths (`kb ls --subjects`).
6. **Missing topics.** If `kb ls --subjects` shows a subject with many
   items and no topic, propose one.
7. Report what you changed (`kb log --limit 50` lists it), what needs a
   human decision, and what `kb lint` still says.
