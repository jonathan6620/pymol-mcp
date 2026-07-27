# Publication rendering

Use this workflow when matching a paper figure or producing final artwork.

## Work in two passes

1. Build and frame the scene with `execute_batch`.
2. Save the camera with `get_view`.
3. Render a 1000–1200 px style proof with `render_png`.
4. Inspect it. Adjust only the settings that differ from the reference.
5. Restore the saved camera if an experiment moved it.
6. Render the approved style at 2400–4000 px.
7. Deliver PNG for general use and convert to TIFF when the journal requests it.

Do not begin with a 4K ray trace. Geometry, orientation, colours, and outline
strength are much faster to judge at proof resolution.

## Clean outlined cartoon preset

Use this as a starting point for flat, publication-style structural cartoons:

```text
bg_color white
set orthoscopic, on
set ray_trace_mode, 1
set ray_trace_color, black
set ray_trace_gain, 0.18
set ray_trace_disco_factor, 0.05
set ray_trace_depth_factor, 0.1
set antialias, 2
set ambient, 0.45
set direct, 0.55
set specular, 0
set reflect, 0
set ray_shadows, off
set depth_cue, 0
set ray_trace_fog, 0
set cartoon_sampling, 14
set cartoon_flat_sheets, on
```

Treat these as a starting point, not a universal house style:

- Increase `ray_trace_gain` for heavier outlines.
- Lower `ray_trace_disco_factor` to reveal more curvature discontinuities.
- Use mode 0 for natural shaded rendering without outlines.
- Avoid mode 3 for paper matching unless the reference is deliberately
  posterized; it quantizes colours and can burn the background.
- Keep shadows and specular highlights off for flat scientific diagrams.
- Use orthoscopic projection for structural comparisons unless the reference
  clearly uses perspective.

## Typed rendering

Prefer `render_png` over textual `png` commands. It:

- passes width, height, DPI, and ray mode as typed fields;
- scales its wait time with output pixel count;
- checks the PNG signature and actual dimensions;
- returns the image for immediate visual inspection.

Typical proof:

```text
filename=/tmp/style-proof.png width=1200 height=1200 dpi=150 ray=true
```

Typical final:

```text
filename=/absolute/output/figure.png width=3000 height=3000 dpi=300 ray=true
```

Pixel dimensions determine raster sharpness; DPI is metadata. Choose dimensions
from the intended print size. For example, a 6-inch panel at 400 pixels per inch
needs about 2400 pixels.

## Reproducibility

Use `inspect_setting` before changing an uncertain render control — it reads the
per-object and per-atom layers that `get_setting` cannot see. Save the camera
with `get_view` once framing is approved. Keep the successful setup commands in
an `execute_batch` list or replay script. Verify both proof and final render
visually; correct dimensions do not prove that selections, representations, or
camera orientation are correct.

PyMOL ray output is raster. An SVG that merely embeds a PNG is not genuinely
vector geometry and cannot add detail. For scalable delivery, render enough
pixels for the final physical size or export 3D geometry to an external renderer
when that workflow is explicitly required.

## Build publication scenes from source

Use a checked-in or delivered `build_figure.pml` as the canonical description
of a publication scene. Do not make the live GUI or `.pse` the only source of
truth.

The script should:

1. `reinitialize` to remove inherited state.
2. Load an explicit absolute PDB/mmCIF path and object name.
3. Define the molecular subset and all representations.
4. Apply broad colors before feature-specific colors.
5. Create curated waters, labels and distance objects.
6. Set the camera, viewport and rendering controls.
7. Write a proof, final raster and `.pse`.

Run it with native PyMOL when a session is required:

```text
pymol -cq /absolute/path/build_figure.pml
```

For a water-mediated interface, identify crystallographic waters by residue ID
from the structure or paper. A geometric `solvent within N` query is useful for
discovery, but is too broad for the final panel. Create each dashed segment
between the actual donor, water and acceptor atoms, and hide automatic distance
labels unless the reference includes numerical distances.

Alternate locations can duplicate labels even when the selection names one
atom. Anchor labels with `not alt B` or the chosen conformer. Keep backbone,
featured bases and recognition residues in separate selections so changing one
representation does not expose the entire nucleic acid or protein.

## Verify the complete build

Do not overwrite the last known-good `.pse` until the candidate passes:

1. Check that the proof matches the isolated paper panel.
2. Open the candidate `.pse` in a fresh native PyMOL process.
3. Render a second proof from the reopened session.
4. Compare molecular content, camera and aspect ratio.
5. Confirm the final PNG dimensions and TIFF conversion.

Use the same viewport aspect ratio for the build and verification render.
Changing a 6:5 scene to 5:4 after reopening can make a correct saved camera look
different. File size is only a diagnostic; it does not prove that coordinates
or representations survived.

Setting the viewport freely is safe here because a `pymol -cq` build has no
window. Driving a live session through the MCP server is different: `viewport`
resizes the user's actual window, and so does an un-raytraced `png` at
non-matching dimensions. See "Set the viewport once" in SKILL.md before
rendering repeated proofs into a window someone is watching.
