---
name: kb-review
description: Review bundles of knowledge waiting in a knowledge base (kb) store's inbox: render them, judge them, record a review, and accept or reject per autonomy. Use when the user asks what arrived in the KB, to review incoming knowledge, or to process the inbox.
---

# Review the inbox

Bundles arrive from other stores (`kb push --land inbox`, pulls from
peers, `kb inbox receive FILE`). Your job is to make the human's decision
quick and well informed. Unless autonomy allows it, the decision is
theirs.

1. **What is waiting:** `kb inbox list`. Each entry shows the sender, the
   set and the reviews so far.
2. **Read one:** `kb review show REF`. Use `--format md` for a compact
   version, or `--format json` for the full view model. It shows:
   - who sent it, when, and their note;
   - **what accepting would do**: new, changed, already here, conflicting,
     and references left dangling;
   - the set as a tree;
   - each item with its facts, links, where its material is, and an
     excerpt.

   Open the material itself when it matters: follow the links, or use
   `kb path ID#NAME` once accepted.
3. **Judge each item:**
   - Is it worth having *here*?
   - Is it attributed (who, how, when) and summarized for the next reader?
   - Are claims marked verified backed by real evidence?
   - Does it duplicate something here (`kb search` its title)?
   - Does it contradict something current here?
   - Is its access appropriate for this store?
   - For changed items, are the changes improvements?
   - Conflicts: never paper over them. Explain both sides.
4. **Record your review:**
   `kb review note REF --verdict accept|reject|changes|comment --text "<reasons, item by item where needed>"`.
   Your review is labelled as an LLM's and is advice. Be specific: the human
   will skim it.
5. **Decide only per `accept` autonomy:**
   - `never`: stop at the review and tell the human what you recommend
     and the command (`kb review accept REF` / `kb review reject REF --reason ...`);
   - `ask`: once they agree, run it with `--approved "<their words>"`;
   - `auto`: accept clean bundles with no conflicts and report; still ask
     about anything doubtful.
6. **Afterwards:** `kb check`. If accepted items left dangling references,
   say whether to fetch them (`kb obtain --dangling`).
