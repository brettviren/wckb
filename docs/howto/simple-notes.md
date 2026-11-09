# Simple notes

For a thought, a reminder or a fact you want to find again, you don't need
a file, a title or a list of options. Give `kb add` the text:

```
kb add --note "TPC noise rose after the 10-28 HV trip; check the grounding."
```

`kb` prints the new item's ID (`doc-…`) and stores the note. That is all
that is required. Short form: `-n`.

## What kb fills in

| field | value | change it with |
|---|---|---|
| `kind`, `doctype` | `document`, `note` | |
| `title` | the first line of the note, cut at 80 characters | `--title` |
| `summary` | the whole text on one line, cut at 400 characters | `--summary` |
| `creators` | you (the store's configured user) | `--creators` |
| `date` | today | `--date` |
| `access` | the store's access level | `--access` |
| content | the text, as the `note` rendition | |

Everything else `kb add` takes also works, so the same command grows with
the note: `--subject detector/tpc/noise`, `--tag todo`, `--runs 1250`,
`--link supports=obs-…`, `--status draft`. `kb lint` suggests a subject
when a note has none.

## Longer notes

`-` reads the note from standard input, so a here-document or a pipe works
and nothing needs shell quoting:

```
kb add --note - --subject meetings/2026 <<'EOF'
Collab meeting 2026-10-29: run 1250 is excluded from the noise study.
HV trip at 03:10; the rest of the run is too short.
EOF
```

Put the point in the first line (it becomes the title) and the numbers and
the reason in the rest. Other LLMs read the summary first.

## Finding and reading it

```
kb ls --subjects                 # reuse existing subject paths
kb search "grounding"            # the note's text is searched
kb show doc-… --format llm       # the full text
kb item content doc-…            # just the text
```

## Correcting it

A note is a document, so it is append-only like the rest. To change the
text, make the corrected note and name the old one:

```
kb add --note "TPC noise rose after the 10-29 HV trip." --supersedes doc-…
```

You may still change `summary`, `subject`, `tags` and `status` in place with
`kb item set`. `kb item retract doc-… --reason "..."` withdraws a note.

## A note or an observation?

A note is for what you want to remember. If you are stating something
others will rely on (who said it, how, when, for which runs), write an
[observation](observations.md) instead: `kb new observation --by … --how …`.
Files, papers and URLs go in with `kb add FILE` ([documents](documents.md)).
