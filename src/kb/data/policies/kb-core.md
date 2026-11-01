## Working with the knowledge base

A knowledge base (KB) of people and their LLM sessions. You are one of its
writers. Other LLMs will read what you write, often without the context you
have now, so write for them.

A store is one SQLite file (`kb.sqlite`) holding knowledge items: JSON
objects validated by JSON Schema (`kb item schema KIND`). Everything goes through
the `kb` command (`kb --help`, `kb COMMAND --help`). Never open the
database directly.

- Kinds of item:
  - `observation`: an assertion, such as "run 1234 was noisy";
  - `document`: a paper, slides or a note;
  - `dataset`;
  - `config`;
  - `snapshot`: an extract from the conditions DB, elog, ...;
  - `software`;
  - `topic`: a synthesis page;
  - `view`: a recipe for HTML/PDF;
  - `person`;
  - `policy`: guidance like this;
  - `kset`: a selection of items, the unit of transfer.
- `kb status` shows the store at a glance and what needs attention;
  `kb doctor` diagnoses problems, and `kb doctor --fix` repairs them (within
  the `curate` autonomy).
- Find things with `kb context`, `kb ls` and `kb search` (which also searches
  text extracted from PDFs, slides and images); read one item with
  `kb show ID` (`--format llm` for its full content). Its history is `kb log ID`.
- Content is held in *renditions*: small text in the store, large files in a
  content store, the store's content dataset or at a URL, always with a
  sha256. `kb open ID` opens an item's file (`--text`: the text search
  matched; `--print`: just its path), and `kb item content ID [NAME]` prints
  text.

Rules:

1. **Primary items are append-only.** The store refuses changes to what an
   observation, document, dataset, config, snapshot or software item
   asserts. To correct one, use `kb item supersede ID`; to withdraw one, use
   `kb item retract ID`. You may change `status`, `subject`, `tags`, `summary`,
   `evidence` and `related` with `kb item set`.
2. **Attribute every assertion.** Observations and configs: `--by`, `--how`,
   `--when`; if you inferred it yourself, `--by claude --how "inferred from
   <what>"`. Documents: their authors are `--creators`. kb records who filed
   anything (and `--via` the tool).
3. **`verified` needs evidence** from an authoritative source. Hearsay is
   `unverified`, and a model's reading of a figure is not evidence.
4. **Write the summary for the next LLM:** one to three sentences, not the
   title again.
5. **Set `subject` and `valid`.** Reuse subject paths (`kb ls --subjects`).
   Give run ranges or time windows whenever the claim has them.
6. **Cite in topics:** `[ID]` for every factual sentence, with every cited
   item in `derived_from`. Then run `kb item stamp ID`. Change a topic's text
   with `kb item content ID --file page.md`.
7. **Store data and scripts, not pictures of plots.** `kb add` decides where
   large files go.
8. **Respect `access`.** Never move content to a more open level than its
   inputs.
9. **Run `kb check` when you finish a batch of changes.** Mention `kb lint`
   findings.
