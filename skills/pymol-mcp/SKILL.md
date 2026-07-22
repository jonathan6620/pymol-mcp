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

There is no way to read state back *through the server*. `select` returning an
atom count is the only introspection available, and rendering an image is the
only way to see the scene. Two things off to the side help: the structure file
is on disk and you have ordinary file tools, and everything you have already run
is logged. See "Read the structure file directly" and "What you ran is on disk".

## Always verify by rendering

After any visual change, render and actually look at it:

```
png /path/to/scratchpad/check.png, width=1000, height=800, dpi=150, ray=1
```

Then `Read` the PNG. Never claim a visual change worked without looking.

**Delete named selections before rendering.** A `select` leaves magenta
indicator dots on every selected atom, and they show up in the ray-traced image
sitting exactly where the atoms are. Hide a component while its selection still
exists and the render looks like the hide silently failed. `delete <name>` (or
`deselect`) first, then render.

### State drifts, including representations

The user may rotate the camera, reload, or restyle in the GUI between calls, and
none of it is visible to you. Observed in a single session: the camera moved
between most renders; the whole session was cleared mid-task; a component that
had been hidden came back; sphere representations vanished; ions that had been
hidden reappeared. Do not assume a command you ran two calls ago still holds —
if it matters, re-run it or re-render.

### When the render comes back blank

An all-background image has two very different causes, and there is no way to
tell them apart by looking. Check which one it is before reaching for view
commands:

```
select tmp_all, all
```

A count of `0` means **nothing is loaded** — the session was cleared, and no
amount of `zoom` or `reset` will help. A non-zero count means the geometry is
there and the camera is pointed wrong, so `zoom visible` is the fix.

## Framing the view

The table has `zoom`, `center`, `orient`, `reset` and `viewport`. Two notes:

- `zoom` defaults to `selection=all`, and "all" includes **hidden** atoms.
  Tested on a structure whose hidden RNA was 13,792 of ~19,000 atoms: a bare
  `zoom` left the visible protein filling about a fifth of the frame height,
  while `zoom visible` from the identical state filled about three quarters.
  Use `zoom visible`.
- `visible` also picks up anything else still shown, including stray ions, which
  will pad the framing. Hide those first if you want a tight crop.
- `hide` never recentres anything. Follow a hide with `zoom visible` whenever the
  thing you kept is a small part of the assembly.

## Read the structure file directly

The no-`iterate` limitation applies only to the server. If you know the path,
parse the CIF/PDB yourself and you get the whole inventory at once — chain IDs,
residue names, per-chain atom counts, and which chains are protein, DNA, RNA or
ions. That is strictly better than inferring chains from `util.cbc`, and it lets
you sanity-check a `select` count before trusting it:

```
chain A:   4117 atoms  protein
chain D:    204 atoms  DNA
chain E:    884 atoms  DNA        # 204+884+225 = 1313, matches select count
chain I:  13792 atoms  RNA
```

Small 2–4 atom "chains" are usually ions, and they are what `solvent or
inorganic` catches.

## What you ran is on disk

The plugin logs every command to `~/.pymol-mcp/` as it runs, which is the one
piece of session state you *can* read back:

| File | Use it for |
|---|---|
| `history.jsonl` | What was run, in order, and which commands failed |
| `session-<timestamp>.pml` | Handing the user a script that rebuilds the figure |

Cannot remember whether a setting was applied, or which of several attempts
actually worked? Check, rather than re-running blind:

```
tail -20 ~/.pymol-mcp/history.jsonl
grep '"ok": false' ~/.pymol-mcp/history.jsonl
```

Each record has `command`, `args`, `source` (the literal syntax), `ok`, and
either `output` or `error`. `load`, `save` and `png` also carry a `file` entry
with the **absolute** path and whether it was read or written, so this answers
"where did that PNG go" when the original command used a relative path.

The `.pml` is the deliverable when someone asks how a figure was made. It holds
only the commands that succeeded, so it replays cleanly:

```
pymol -r ~/.pymol-mcp/session-20260722-114646.pml
```

Two caveats. The history is per PyMOL launch, so a restart starts a new `.pml`
while `history.jsonl` keeps appending. Anything the user did in the GUI is not
recorded, because it never went through the server, which is another reason a
replay can diverge from what is on screen.

## Recovering a session that was cleared

If the structure vanishes and you did not load it yourself, you cannot ask PyMOL
what it was. Check the session history the plugin writes:

```
grep '"command": "\(fetch\|load\)"' ~/.pymol-mcp/history.jsonl | tail -5
```

Every `load` records the absolute path it read, so this identifies the file even
when the original command used a relative path. There is no `~/.pymol` history
and no `~/.pymolhistory`; those were checked and do not exist.

Failing that, the Claude Code transcripts also record every command sent:

```
grep -o '"user_input":"\(fetch\|load\)[^"]*"' ~/.claude/projects/**/*.jsonl
```

Before trusting a hit, fingerprint it: parse the file's atom counts and check
they match the `select` counts you saw earlier in the session. Reloading the
wrong structure and carrying on is worse than admitting you lost it.

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

### Negative residue numbers must be escaped

`resi` treats `-` as a range operator, so a negative residue number is silently
read as an open-ended range instead: `resi -12` means "everything up to 12", not
"residue −12". It does not error — it just selects far too much. Escape the
minus sign with a backslash:

```
select t, chain E and resi \-12      -> 20     one nucleotide, correct
select t, chain E and resi -12       -> 719    parsed as "resi <= 12"
```

Nucleic acid chains hit this constantly, since numbering conventionally runs
negative upstream of a reference point. The escaped form composes normally in a
`+` list:

```
select cww, chain E and resi \-12+\-11+\-10+1+2
```

Because the failure is silent, always check the returned atom count against
what you expect (roughly 20 atoms per nucleotide, 8 per amino acid) before
acting on a selection with negative numbering in it.

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
