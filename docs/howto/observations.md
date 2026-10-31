# Observations and configurations

## An observation is an attributed assertion

Anything someone states as fact is an observation, whether it came from a
meeting, an email, the elog or your own analysis. The item keeps **who
said it, how, when, and what it applies to**.

```
kb new observation \
  --title "Run 1250 excluded from the noise study" \
  --summary "Run 1250 was excluded from the Q4 noise study because the HV trip at 03:10 left half the run unusable." \
  --subject detector/tpc/noise --subject runs/quality --runs 1250 \
  --by per-ann --how "email to the group list" --when 2026-10-29 \
  --text "Run 1250 is excluded: HV trip at 03:10 UTC, recovered at 05:40; the remaining 40 minutes are too short."
```

- **`--by`**: the person who asserted it. Use a person ID (`per-ann`) if
  the KB keeps person items, else a name. If Claude inferred it, use
  `--by claude --how "inferred from <what>"`.
- **`--runs`**: one run (`1250`), a range (`1200-1300`), or several (repeat
  the option). Use `--start/--end` for time windows. Validity is what makes
  "what do we know about run 1250?" a simple query.
- **`--subject`**: reuse existing paths (`kb ls --subjects`). Lowercase,
  `/`-separated.
- **Status** starts `unverified`. Use `--status verified` only together with
  `--evidence <ref-or-URL>` pointing at an authoritative source (elog entry,
  conditions snapshot, document); `kb check` enforces this.

The text goes in `note.md`, and the summary is what other LLMs read first.
Put the numbers, units and the "because" in the text.

## Configurations

Use a `config` item for what a system was configured with, rather than
what was observed:

```
kb new config --title "Drift velocity in 2025 production simulation" \
  --summary "The 2025 production simulation used a drift velocity of 1.6 mm/us." \
  --subject simulation/production --start 2025-01-01 --end 2025-12-31 \
  --set system=production/sim --set 'parameters={"drift_velocity_mm_us": 1.6}' \
  --by per-bob --how "production config repo, tag prod-2025.3"
```

Small parameter sets go inline in `parameters`. For whole configuration
files, `kb add the.fcl --kind config --set system=... --start ...`.

## Writing JSON directly

Claude often finds it easier to give a partial item as JSON. `kb` fills
in the ID, timestamps and plumbing:

```
kb new config --from-json - <<'EOF2'
{"title": "...", "summary": "...", "system": "daq/trigger",
 "valid": {"runs": [[1200, 1300]]}, "parameters": {"threshold_adc": 25}}
EOF2
```

## Changing an observation later

You may change our *relationship* to it: status, subject, tags, summary,
evidence and related items:

```
kb item set obs-7k2m9q4xha status=verified --add evidence=https://elog.example/5521
```

What it *says* can't change: the store refuses it. Write a correction
instead ([corrections.md](corrections.md)). Every change is an event in the
item's history (`kb log obs-7k2m9q4xha`).
