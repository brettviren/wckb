---
name: kb-ingest
description: Add a document (paper, preprint, slides, technote, thesis), dataset, configuration or conditions snapshot to the group knowledge base (kb), extract its text, and fold it into topic pages. Use when the user gives a file, URL, arXiv/DOI/Indico reference to add to the KB.
---

# Ingest material into the KB

1. **Is it already there?** Run `kb search` on the title and
   `kb ls --kind document --subject ...`. `kb add` also refuses duplicates
   by identifier.
2. **Add it** as a primary item. `kb add` hashes the content and decides
   where it lives: small files go in the store, big ones in the content store
   or the store's content dataset, and URLs stay URLs. It also **finds
   metadata** and says what it found: from arXiv (an arXiv URL or
   `--identifier arxiv=ID`: title, authors, date, abstract, DOI), a PDF's
   document information, an image's EXIF. Options you give win over what it
   finds; check the found title and summary, and correct them.

   ```
   kb add https://arxiv.org/abs/2601.01234 --subject detector/tpc/noise   # arXiv gives the rest
   ```

   ```
   kb add https://indico.example/event/123/.../slides.pdf --kind document \
     --set doctype=presentation --title "..." --summary "..." \
     --creators "A. Person" --date 2026-10-28 --subject detector/tpc/noise \
     --identifier indico=event/123/contribution/4 --runs 1200-1300
   ```

   - If the URL needs SSO and the user has the file: `kb add FILE --url URL ...`.
   - Web pages (READMEs, model cards, docs): kb finds the page's title and
     description, and its date if the page or server gives one. Otherwise
     `--date` is the date the page shows (a release, "last updated"), else
     the day you read it, and say which in the summary. Never a date from
     memory: `date` cannot be changed later. The text is extracted from the
     HTML for search.
   - For papers, add `--identifier arxiv=... --identifier doi=...` when kb
     didn't find them.
   - `--target DIR/` copies the file into a source (a DataLad dataset is
     `datalad save`d) and records it there.
   - Datasets: `--kind dataset --set format=csv`, then `kb data describe ID`.
   - Files committed in a DataLad dataset are added by reference (kb records
     the dataset as a source item; nothing is copied). If a file there is
     not saved yet, ask the user to `datalad save` it first rather than
     copying it into the store.
   - Snapshots from the conditions DB, elog etc.:
     `--kind snapshot --set system=... --set query="..." --set retrieved=<datetime>`.
   - **Material that has versions or lives in several places** (a talk's
     materials re-uploaded on Indico, copies on EOS as `v1/`, `v2/`, a
     DataLad dataset): make an **asset** (`kb asset new --type fileset`),
     bind its sources (`kb asset bind ... --source fs|url|datalad ...`), then run
     `kb asset scan --register`. Point the document's rendition at it with
     the location `{"type": "asset", "asset": ID, "version": LABEL, "path": FILE}`.
     See `kb asset --help`.
3. **Text:** `kb add` extracts text from PDFs and slides, and reads images
   and scanned pages by OCR when an engine is installed (`kb rendition
   transforms` lists what is available). Check the result with
   `kb item content ID text`. If nothing was extracted, `kb doctor
   unextracted --fix` retries. For slides, text extraction loses the plots.
   If the figures matter, read the slides (or ask the user) and write a
   digest:

   ```
   # write digest.md somewhere, then register it as an LLM-made rendition:
   kb rendition add ID digest.md --name digest --role derived --fidelity llm --from original
   ```
4. **Write a summary that helps the next LLM.** Say what the material
   establishes, with key numbers. If it doesn't, update it with
   `kb item set ID summary="..."`.
5. **Fold it into topics.** Run `kb ls --kind topic --subject <subject>`.
   - If a topic exists: get its page (`kb item content TOPIC > page.md`), add
     what's new citing `[ID]` for each statement, and put it back
     (`kb item content TOPIC --file page.md`). Add the ID to `derived_from`
     (`kb item set TOPIC --add derived_from=<new-id>`), then `kb item stamp TOPIC`.
   - If none exists and the material plus related items deserve one, make
     it with `kb new topic --title ... --subject ... --derived-from ID ...
     --text-file page.md`.
6. **Observations in the material.** Statements of fact that other people
   will need, such as "run 1250 excluded because ...", become their own
   observation items, citing the document as evidence: `--evidence DOC-ID`
   (or `DOC-ID#text` when the fact is in its extracted text).
7. Run `kb check` and `kb lint`, and show the user the result
   (`kb show ID` for the new items).
