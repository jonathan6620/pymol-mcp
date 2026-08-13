# Replay benchmarking and privacy

Use this workflow to turn a completed PyMOL task into evidence for improving the
MCP interface or this skill without publishing molecular data.

## Keep audit, replay and shareable evidence separate

- Treat an unredacted `export_session` ZIP as local audit data. It contains no
  copied coordinates, but it can contain absolute paths, object names, selection
  strings and other identifying metadata.
- Use `redact_paths=true` only for a shareable analysis bundle. Its replay script
  is intentionally non-executable because path placeholders cannot resolve.
- Keep coordinates, `.pse` files, raw `history.jsonl`, replay ZIPs, renders and
  detailed agent traces outside the repository. In this repository use
  `benchmarks/private/`, `benchmark-results/`, or another ignored local root.
- Commit only code, aggregate metrics, synthetic fixtures, and scenarios based
  on public accessions. A private scenario can itself reveal chain names,
  residue identities or hypotheses, so keep its specification local too.
- Do not publish an ordinary coordinate-file hash as a private-source ID: it can
  be compared with known files. Use a random local identifier or keyed HMAC when
  a stable private reference is necessary.

Run `make privacy` before committing. Do not override its molecular/session-file
rejection merely because the current test structure happens to be public.

## Make GUI corrections replayable

Direct GUI actions never enter MCP history. When the user rotates or otherwise
corrects the scene manually:

1. Call `get_view` immediately without issuing `orient`, `zoom`, `turn` or
   `reset` first.
2. Reapply the returned 18-value camera with `set_view`. This leaves the visible
   view unchanged while recording it as replayable MCP state.
3. Render and inspect a proof from that exact view.
4. Save the `.pse`, then call `export_session` only after the final render is
   accepted.

If the user changed representations or colors in the GUI, inspect and reproduce
those changes through MCP as well; `set_view` captures only the camera.

## Validate replay in a fresh process

An export is not proven by a valid ZIP or a zero exit code. PyMOL can report
selection and fetch errors while exiting successfully. Replay from a clean,
headless process and assert the resulting scene.

In this repository:

```bash
make test-benchmark BUNDLE=/absolute/path/to/session.zip
```

Add a private local reference render when visual comparison is useful:

```bash
uv run python -m pymol_mcp.benchmark SESSION.zip \
  --reference /private/reference.png \
  --output benchmark-results/result.json
```

The evaluator removes historical `png` and `save` commands, rejects command
chaining and expression-evaluating replay commands, runs the replay in a
temporary directory, and scores a fresh saved scene. A public `fetch` needs
network access. A private
`load` reads the original local path and never copies the source into the ZIP or
repository; fail explicitly if that source cannot be resolved.

Judge scene equivalence semantically before image similarity:

- required objects and atom counts;
- named selections and selection-specific representation/color counts;
- camera orientation within tolerance;
- output dimensions and opaque background;
- optional perceptual image error against a local reference.

Do not require byte-identical PNG or `.pse` output across GUI/headless runs,
platforms or renderer versions.

## Build scenarios that diagnose the layer that failed

Keep committed scenario JSON limited to public or synthetic structures. Give
each criterion one observable failure mode. For a publication panel, include
the molecular subset, view side, conformers, highlighted atoms, backbone style,
background and replay outcome rather than only “looks like Figure N.”

Compare the same prompt and scenario across revisions. Retain aggregate metrics
such as final checks, failed history entries, replayable operations, render
iterations and camera changes. Keep prompts, raw histories and user data local.
When a local session exposes a reusable failure mode, distill it into a reviewed
lesson package linked to a public or synthetic scenario; never contribute a
`private_evaluation_only` lesson or its source artifacts.

Interpret failures by layer:

| Evidence | Likely layer |
|---|---|
| Rejected or malformed tool call | MCP schema, command table or skill syntax |
| Replay diverges from accepted live scene | missing logged state or GUI-only edit |
| Semantic checks pass but image check fails | camera, styling or renderer variance |
| Many renders/camera changes or user correction | skill workflow or missing visual criterion |
| Sensitive entry/path reaches a proposed commit | privacy/export boundary |

Add a network-free synthetic real-PyMOL integration test for evaluator changes.
Use public-accession scenarios as opt-in end-to-end benchmarks, since CI may not
have network access.
