---
name: pymol-mcp
description: Drive PyMOL through the pymol MCP server. Covers what the command table does and does not accept, selection syntax, splitting DNA from RNA, enumerating chains, labelling, transparency, and verifying a change by rendering. Use whenever calling mcp__pymol__parse_and_execute.
---

# Driving PyMOL through the MCP server

## The server is not a natural-language interface

`mcp__pymol__parse_and_execute` matches input against a fixed table of command
patterns. Anything unrecognised is rejected, not interpreted.

- **One command per call.** Split multi-step requests.
- **Selections are a comma-separated second argument**: `show cartoon, chain A`,
  never `show cartoon for chain A`.
- **A selection containing a comma must use `+`**: `resi 1+2+3`.
- `fetch` takes a 4-character PDB code; `load` takes a file path.
- Call `mcp__pymol__list_commands` with a filter before guessing syntax. The
  table is ~80 commands and omits many real PyMOL commands.

### Commands that do NOT exist in the table

| You want | Table has | Use instead |
|---|---|---|
| `bg_color white` | no `bg_color` | `set bg_rgb, white` |
| `iterate` / `print` | neither | `select tmp, <sel>`, which returns an atom count |
| RNA vs DNA selector | neither | residue names, see below |

There is no way to read state back. `select` returning an atom count is the only
introspection available, and rendering an image is the only way to see the scene.

## Always verify by rendering

State drifts. The user may rotate the camera, reload the structure, or restyle
in the GUI between calls. This has happened mid-session, silently discarding
every setting. After any visual change, render and actually look at it:

```
png /path/to/scratchpad/check.png, width=1000, height=800, dpi=150, ray=1
```

Then `Read` the PNG. Never claim a visual change worked without looking.

## Selecting the pieces

```
polymer.protein                          # all amino acids
polymer.nucleic                          # RNA *and* DNA together
solvent or inorganic                     # waters, ions
```

Splitting RNA from DNA: the server has no selector for this, so go by residue
name. Standard PDB deoxy residues are DNA; everything else nucleic is RNA:

```
select dna, polymer.nucleic and resn DA+DC+DG+DT+DI
select rna, polymer.nucleic and not resn DA+DC+DG+DT+DI
```

Both calls return atom counts, so use them as a sanity check. Modified or
non-standard residues fall on the RNA side, so confirm the counts look sane.

## Enumerating and colouring chains

`util.cbc <sel>` colours by chain **and prints the chain IDs it found**. That is
the only way to enumerate chains through this server:

```
util.cbc dna
  -> util.cbc: color 26, (chain D)
     util.cbc: color 5, (chain E)
     ...
```

But `util.cbc` colours **carbons only**, which looks wrong on cartoon. Use it to
discover chains, then override with explicit per-chain colours:

```
color red, dna and chain D
color marine, dna and chain E
```

Caveat: chain is the strand unit. A nicked chain won't be split, and you cannot
detect gaps with the available commands.

## Showing protein as context without hiding what's inside it

Helices and sheets are thick, so a cartoon in front of a buried feature occludes
it regardless of colour. Thin the protein to a tube trace, which keeps the fold
legible as context while the feature behind reads unbroken:

```
cartoon tube, polymer.protein
set cartoon_tube_radius, 0.3
```

## Labelling residues

Label one atom per residue, never the whole selection. A 116-atom selection
produces 116 overlapping labels:

```
label sele and name CA+C1', resn+resi
```

`CA` anchors amino acids, `C1'` anchors nucleotides; one expression covers a
mixed protein/nucleic selection. The expression is Python evaluated per atom, so
`resi` alone gives numbers, `resn+resi` gives `GLY291`.

The label parser splits on the *first* comma only, so commas are legal in the
expression but not in the selection.

Contrast must match the background. Black labels on a black background look
like the command silently failed:

```
set label_size, 18
set label_color, black        # white background
set label_color, white        # black background
```

For clustered labels:
```
set label_position, (3,3,3)
set label_connector, on
set label_outline_color, white
```

## Housekeeping

- Delete temporary selections you create for counting: `delete tmp_sel`.
- `deselect` clears the magenta selection dots but keeps named selections. Don't
  run it unprompted, since the user may have selected something deliberately.
- `sele` is whatever is selected *now*. If the session reloaded, it is not what
  the user originally picked.

## Worked example: protein/DNA complex, DNA highlighted

```
set bg_rgb, white
hide everything, polymer.protein
hide everything, polymer.nucleic
hide everything, solvent or inorganic
select dna, polymer.nucleic and resn DA+DC+DG+DT+DI
show cartoon, dna
util.cbc dna                                   # read chain IDs from output
color red, dna and chain D                     # then one per chain
show cartoon, polymer.protein
color gray70, polymer.protein
cartoon tube, polymer.protein
set cartoon_tube_radius, 0.3
set cartoon_transparency, 0.4, polymer.protein
set ray_transparency_shadows, 0
set transparency_mode, 2
set depth_cue, 0
set ray_trace_fog, 0
png <scratchpad>/check.png, width=1000, height=800, dpi=150, ray=1
```

## Irreversible actions

`hide everything, <sel>` destroys the representation state for that selection.
There is no undo, and you cannot query what was shown before. When restoring
something you hid, you are *choosing* a representation, not recovering one. Say
so rather than implying the original came back.
