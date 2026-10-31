# Corrections and how they propagate

Sources (observations, documents, datasets, configs, snapshots, software)
are never edited to say something else. A correction is a **new item**
that supersedes the wrong one. The old item stays as history, and
everything built on it becomes stale.

## Correct an item

```
kb item supersede obs-7k2m9q4xha \
  --title "Elevated coherent noise was in run 1243" \
  --summary "Correction: the noisy run was 1243 (elog 5521); 1234 was a transcription error." \
  --runs 1243 --by elog --how "elog entry 5521" --evidence https://elog.example/5521 \
  --text "Per elog 5521 the elevated coherent noise was during run 1243, not run 1234."
```

The new item inherits the subject, access and other fields unless you
override them. Attribution defaults to you unless `--by` is given. The old
item disappears from `kb ls` (unless `--all`), and `kb context` shows it
under "Superseded".

## Withdraw an item

```
kb item retract obs-7k2m9q4xha --reason "Duplicate of obs-9x...; recorded twice."
```

This writes a retraction observation. Nothing replaces the withdrawn
item.

## When only our confidence changes

```
kb item set obs-7k2m9q4xha status=disputed
kb item set obs-7k2m9q4xha status=verified --add evidence=snp-...
```

## When upstream content changed

```
kb rendition verify                  # re-fetch every URL original; 'changed' means the hash differs
kb add <url> --supersedes doc-3vq8r2m1kd --set doctype=presentation ... --extract
```

The presenter re-uploaded the slides at the same URL. The new upload is a
new version, so you add it and it supersedes the old one.

## Propagate

```
kb item stale
```

It lists three kinds of stale item:

| kind | meaning | fix |
|---|---|---|
| `rendition` | a derived rendition's source changed | `kb rendition extract ID --name NAME --force`, or `kb rendition extract --stale` |
| `topic` | an input was superseded, retracted or changed | an LLM revises the page (`kb item content TOPIC --file page.md`); swap the ref (`kb item set TOPIC --replace-ref OLD=NEW`); `kb item stamp TOPIC` |
| `view` | inputs or sources changed since the last build | update inputs as for topics, then `kb view build VIEW` |

Topics are never rewritten automatically. Revising prose needs judgment, so
it's done by Claude (the `kb-correct` skill) and reviewed by a person.
Views are rebuilt mechanically.

Published views are not changed. `kb lint` lists views whose latest
publication used superseded inputs, and `kb view publish` after a rebuild adds a
new version next to the old one.

If the corrected item had been pushed to other stores, the correction is
now a top promotion candidate (`kb edge candidates NEIGHBOR`). Send it, so
the correction reaches them too.

## Why not just edit?

The store refuses it. A database trigger rejects any change to what a
source asserts, whatever program tries. And if the store were edited
outside `kb`, `kb check` would report it as tampering.

The reason is that a reader, and especially an LLM, needs to see that an
earlier belief existed and was corrected. Otherwise nobody can explain why
an older view or decision looked the way it did. `kb log ID` and
`kb context ID` show the chain.
