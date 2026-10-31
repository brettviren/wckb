# Datasets and snapshots

## Prefer data plus a script to a picture

Store the numbers and the code that made them. Figures are made in views
([views.md](views.md)).

```
kb add noise_by_run.csv make_noise_table.py --kind dataset --set format=csv \
  --title "Coherent noise RMS by run, 1200-1300" \
  --summary "Per-run, per-plane coherent noise RMS in ADC for runs 1200-1300, from the nightly noise job." \
  --subject detector/tpc/noise --runs 1200-1300 --license collaboration-internal
kb data describe dat-...        # Frictionless datapackage.json: column names and inferred types
```

`kb data describe` adds a `metadata` rendition describing each CSV/TSV, so
any tool (or LLM) knows the columns without reading the data. Edit
`datapackage.json` to add units and descriptions, then
`kb item set ID summary=...` if needed. Variables can also be listed on the
item:

```
kb item set dat-... 'variables=[{"name":"rms","unit":"ADC","description":"coherent noise RMS"}]'
```

Describing the data (`variables`, `produced_by`, `summary`) is allowed on a
source at any time. Changing the data itself is not: add a new dataset that
supersedes the old one.

## Formats

In order of preference:

1. CSV/TSV with a data package;
2. JSON/JSONL;
3. Parquet for larger tables;
4. ROOT or HDF5 for big structured data.

Big files go to the content-addressed store (`cas`) or stay where they are
(EOS) and are referenced by URL.

ROOT files get a structure summary, so the KB knows what is inside without
opening them:

```
kb rendition extract dat-... --name structure    # trees/RNTuples with branches and entries, histograms with contents
```

## Many small items, or one dataset?

Per-run condition values arriving by the hundreds belong in **one dataset**
(a CSV or JSONL per period), not hundreds of observations. Observations are
for statements people make. Datasets are for measurements machines produce.

## Snapshots of authoritative systems

The conditions DB, run DB and elog stay the source of truth. A snapshot
records what you extracted, how and when:

```
kb add hv_runs_1200_1300.csv --kind snapshot \
  --set system=conditions/hv --set query="SELECT run, hv FROM hv WHERE run BETWEEN 1200 AND 1300" \
  --set retrieved=2026-10-30T10:00:00Z \
  --title "HV per run, 1200-1300" --summary "Cathode HV per run from the conditions DB." --runs 1200-1300
```

Snapshots make good `--evidence` for verifying observations.

## Provenance

Point datasets at the code and configuration that made them:

```
kb new software --set repository=https://gitlab.example/group/noise-job --set commit=3f2a9c1 \
  --title "Nightly noise job" --summary "Computes per-run coherent noise RMS from raw data."
kb item set dat-... 'produced_by={"software": "sw-...", "command": "noise-job --runs 1200-1300"}'
```
