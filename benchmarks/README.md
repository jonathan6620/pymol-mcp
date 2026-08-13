# PyMOL MCP benchmarks

These benchmarks measure whether MCP and skill changes improve complete PyMOL
workflows. Scenario specifications are safe to commit. Coordinates, raw session
logs, replay archives, renders, and detailed training traces remain local.

## Run the Figure 2b benchmark

Export a session from PyMOL, then run:

```bash
make test-benchmark BUNDLE=/absolute/path/to/session.zip
```

Use `SCENARIO=...`, `REFERENCE=...`, and `OUTPUT=...` for a different scenario,
a private local reference image, or an ignored aggregate report.

The runner extracts only `replay.pml` into a temporary directory, removes its
historical `png` and `save` commands, rejects commands outside the MCP replay
surface, and starts a fresh headless PyMOL. Public scenarios may use `fetch`;
private scenarios should use a local `load` path that is never copied into the
repository or replay ZIP.

The result has three independent groups of evidence:

- semantic scene state: objects, atom counts, selections, representations, and
  camera orientation;
- visual output: dimensions, opacity, background, and optional comparison with
  a local reference image supplied via `--reference`;
- privacy: the export has the expected metadata files and embeds no molecular
  coordinate/session payload.

The aggregate report also records history-entry, replayable-operation, failed-
operation, render-iteration, and camera-change counts. These counters are safe
to compare across skill or MCP versions; raw prompts and history remain local.

To save an aggregate report locally:

```bash
uv run python -m pymol_mcp.benchmark SESSION.zip \
  --output benchmark-results/barhl2.json
```

`benchmark-results/` and `benchmarks/private/` are ignored. Reports contain
scores and renderer versions, not coordinate paths or molecular content.

## Comparing MCP or skill versions

Run the same prompt and scenario against each version, then compare:

- total pass score and individual failed criteria;
- invalid MCP calls and failed replay entries;
- number of render iterations and user corrections;
- semantic replay equivalence;
- optional perceptual image error against a private local reference.

The first scenario captures the corrections learned during the BARHL2 Figure
2b exercise: published-side camera orientation and smooth traces for both DNA
backbone conformers. More scenarios should add one failure mode at a time and
use public accessions or synthetic structures unless a private local resolver is
configured.

## Sharing a lesson, not a session

`benchmarks/lessons/` contains reviewed lesson packages distilled from local
sessions. They identify the failure, the affected component, the correction,
and a public or synthetic regression scenario. They never contain the raw
session, molecular files, prompts, renders, or local paths.

Run `make lessons` before submitting one. See
`docs/contributing-session-lessons.md` for the promotion and review process.
