---
name: kb-capture
description: Capture knowledge from the current work session into the group knowledge base (kb) as observations, configs or datasets. Use when the user says capture, save to the KB, share with the group, or at the end of a session that established facts others would need.
---

# Capture a session into the KB

Goal: a few high-value, well-attributed items, not a session dump.

1. **Select.** Review the session and list candidate facts that would help
   someone else in the group:
   - facts about detector or run conditions;
   - configuration values actually used;
   - results with the data or script that made them;
   - pitfalls found and their fixes;
   - decisions made, and why.

   Leave out anything already in the KB (`kb context` and `kb search` each
   candidate), anything speculative unless marked as such, and anything
   personal. Show the list to the user and ask which to keep. Expect them to
   drop some; three good items beat ten weak ones.
2. **Write each item with kb**, never by hand:

   ```
   kb new observation --title "..." --summary "..." \
       --subject detector/tpc/noise --runs 1243 \
       --by per-<user> --how "found while <activity>" --when <date> \
       --text "<the statement, with numbers, units and how it was established>"
   ```

   - Use `kb new config` with `--set system=... --set 'parameters={...}'`
     for configuration values.
   - Use `kb add FILE --kind dataset ...` for data, with the script as a
     second file.
   - Set `--by` to the person who asserted it. If it is your own inference,
     use `--by claude --how "inferred from ..."`.
   - Status stays `unverified` unless you add `--evidence` pointing at an
     authoritative source.
3. **Check.** `kb check` must pass. Then run `kb lint`. An `overlap`
   warning means another item covers the same subject and runs: tell the
   user whether yours agrees, adds detail or contradicts it.
4. **Report** the items you filed (`kb show ID` shows each as a person
   reads it). Mark the ones that deserve a wider audience with
   `kb item set ID --add tags=promote`. Promotion is a separate review
   (kb-promote).
