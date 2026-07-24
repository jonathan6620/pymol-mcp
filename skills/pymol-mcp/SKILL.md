---
name: pymol-mcp
description: Drive PyMOL through the pymol MCP server. Covers starting PyMOL when none is running, targeting one of several instances, what the command table does and does not accept, selection syntax, splitting DNA from RNA, enumerating chains, labelling, transparency, and verifying a change by rendering. Use whenever calling mcp__pymol__parse_and_execute, or when a PyMOL tool call fails to connect.
---

# Driving PyMOL through the MCP server

## Prefer the MCP control path

Use the typed `mcp__pymol__*` tools directly for discovery, commands, batches,
camera state and renders. They keep molecular operations inside the plugin's
allowlisted protocol and avoid unnecessary shell execution and localhost
sandbox approvals. Do not wrap `parse_and_execute` in `uv run python -c` merely
to reach the same server when the MCP tools are available.

This preference does not grant general host access. Launching a GUI, reading a
structure outside allowed filesystem roots, downloading coordinates, or invoking
native PyMOL may still require host approval. Explain that boundary once if the
user asks; do not imply the safe MCP command itself is the reason for a prompt.

Use a shell/client fallback only when the MCP tool is unavailable or when
diagnosing the transport itself. Keep the fallback narrowly scoped to the local
PyMOL server and return to direct MCP calls afterward.

## Starting PyMOL

Call `mcp__pymol__list_instances` first. It reports every running PyMOL, so it
answers both "is one running" and "which one do I mean" in a single call:

```
2 PyMOL instance(s) running:
  instance=9876, pid 4412: 1ubq
  instance=9877, pid 4488: 6vxx
```

If any are listed, PyMOL is running and a connection failure is something else.
**If none are, ask before launching** — PyMOL opens a window on the user's
desktop, and they may have closed it deliberately or be running it elsewhere.

### Launching it

Prefer the user's own launcher, which already has the flags right:

```bash
type pymolq >/dev/null 2>&1 && pymolq
```

`pymolq` may be a shell function rather than an alias, so `type` is the
reliable test. Failing that:

| Platform | Command |
|---|---|
| macOS / Linux | `pymol -q >/dev/null 2>&1 &` |
| Windows (cmd) | `start "" pymol.exe -q` |
| Windows (PowerShell) | `Start-Process pymol -ArgumentList '-q'` |

Three things must be right, and each has bitten someone:

- **Background it.** A foreground `pymol` never returns, so the call blocks
  until the user closes the window. Even `pymol -h` does this: it opens the GUI
  instead of printing help.
- **Discard its output.** PyMOL writes to the terminal it was launched from,
  which is the terminal Claude Code is drawing in, and it corrupts the display.
- **Never pass `-c`.** That is headless, so the user sees nothing, which defeats
  the purpose. `-q` alone keeps the GUI and still loads `~/.pymolrc.py`, which
  is what starts the socket listener.

### When pymol is not on PATH

Conda installs it inside an environment. Search with Python rather than a shell
glob: zsh aborts the whole command on the first pattern that matches nothing, so
an `ls` of several candidate paths finds none of them even when one exists.

```bash
python3 -c "
import glob, os
for pattern in [
    '~/*conda*/envs/*/bin/pymol', '/opt/*conda*/envs/*/bin/pymol',
    '/opt/homebrew/Caskroom/*/base/envs/*/bin/pymol',
    '/usr/local/*conda*/envs/*/bin/pymol',
    '/Applications/PyMOL.app/Contents/bin/pymol',
    '~/AppData/Local/*conda*/envs/*/Scripts/pymol.exe',
    'C:/ProgramData/*conda*/envs/*/Scripts/pymol.exe',
]:
    for hit in glob.glob(os.path.expanduser(pattern)):
        print(hit)
"
```

`make install PYMOL=/full/path/to/pymol` takes the same path when reinstalling.

### When macOS launches PyMOL but shows no window

The Conda PyMOL launcher may run as a Python process without macOS bringing its
window to the foreground. Do not repeatedly relaunch it. First find the live
process outside a restricted sandbox:

```bash
ps ax -o pid=,comm=,command= | rg -i 'pymol|python.*pymol'
```

If the command line shows PyMOL and the requested structure, activate that PID:

```bash
osascript -e 'tell application "System Events" to set frontmost of first process whose unix id is 12345 to true'
```

Replace `12345` with the actual PID. This can prompt for macOS Automation or
Accessibility permission. If no PyMOL process exists, run the executable in the
foreground once with output visible to distinguish an immediate startup error
from a running window that merely was not activated.

### Wait for the listener, do not guess

`~/.pymolrc.py` sleeps 3 seconds before starting the listener, on top of PyMOL's
own startup, so a fixed `sleep` is either too short or wasteful. Poll
`list_instances` until the new one appears, or from a shell:

```bash
python3 -c "
import socket, time
for _ in range(30):
    live = []
    for port in range(9876, 9896):
        s = socket.socket(); s.settimeout(0.2)
        if s.connect_ex(('127.0.0.1', port)) == 0: live.append(port)
        s.close()
    if live: print('listening on', live); break
    time.sleep(1)
else:
    print('timed out')
"
```

The plugin module is loaded into the PyMOL process once. Updating its symlink,
installer or socket protocol does **not** update a window that is already open;
restart PyMOL after plugin changes before diagnosing the new client against the
old in-memory listener.

## Working with several instances

Each PyMOL claims its own port, so more than one can run at once and you can
drive any of them. Pass `instance=<port>` to `parse_and_execute`:

- **One instance running:** leave `instance` unset.
- **Several running:** every command needs `instance`. Omitting it is an error
  that lists the choices rather than picking one, because driving the window
  the user is not watching is indistinguishable from the command doing nothing.

Identify a window by what it has loaded, from `list_instances`, not by port
number. If the user says "the ubiquitin one", match it against the object names.

**Launching another PyMOL is now safe** — it takes the next free port instead of
failing to bind. But it is still a real window on someone's desktop, so ask, and
check `list_instances` first in case the one they want already exists.

## The server is not a natural-language interface

`mcp__pymol__parse_and_execute` matches input against a fixed table of command
patterns. Anything unrecognised is rejected, not interpreted. Use
`mcp__pymol__execute_batch` for a known ordered setup sequence; it applies the
same parser and allowlist to every item and can stop at the first failure.

- **One command per batch item.** Never combine commands with semicolons or
  newlines.
- **Selections are a comma-separated second argument**: `show cartoon, chain A`,
  never `show cartoon for chain A`.
- **A selection containing a comma must use `+`**: `resi 1+2+3`.
- `fetch` takes a 4-character PDB code; `load` takes a file path.
- Call `mcp__pymol__list_commands` with a filter before guessing syntax. The
  table is ~80 commands and omits many real PyMOL commands.

### Commands that do NOT exist in the table

| You want | Table has | Use instead |
|---|---|---|
| `iterate` / `print` | neither | `select tmp, <sel>`, which returns an atom count |
| RNA vs DNA selector | neither | residue names, see below |

`bg_color white` works; it used to be missing and need `set bg_rgb, white`.

`alter` and `alter_state` expressions are restricted to arithmetic over atom
properties, because PyMOL evaluates them as Python. `b + 10`, `int(resi) + 100`
and `'A' if chain == 'B' else chain` are fine; attribute access, subscripting,
lambdas, comprehensions and any call outside `str int float abs round len min
max` are rejected.

Use `mcp__pymol__get_view` and `mcp__pymol__set_view` to preserve an exact
camera across experiments or restarts. Use `mcp__pymol__get_setting` to inspect
one named setting. The server still cannot enumerate arbitrary atom properties;
use `select` for counts, parse the structure file for inventories, and inspect a
render for visual state.

## Always verify by rendering

After any visual change, render and actually look at it. Prefer the typed
`mcp__pymol__render_png` tool because it verifies the written dimensions and
returns the image directly:

```
filename=/path/to/scratchpad/check.png width=1000 height=800 dpi=150 ray=true
```

Never claim a visual change worked without looking.

For paper-matched figures, ray-tracing experiments, and publication exports,
read [references/publication-rendering.md](references/publication-rendering.md)
before styling or rendering.

For a paper panel, crop or inspect the exact target panel before issuing styling
commands. Record its composition—camera direction, molecular subset,
representations, colors, labels, waters and contacts—and reproduce those
features in low-resolution proofs. A generic view of the right PDB is not a
figure match. Do not start a 3200-pixel ray render until a 1000–1600-pixel proof
visually matches the panel's framing and information density.

Set a real viewport before the first proof render. A newly opened or backgrounded
PyMOL window can retain a tiny viewport; `png width=... height=...` may then
produce an all-white image or a single-pixel molecule even though atoms are
loaded:

```
viewport 1200, 900
zoom visible
```

Then render, inspect the image, and only proceed to the expensive publication
render after the proof has the intended composition.

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
there. First set a non-trivial `viewport`, then use `zoom visible`; either a
one-pixel viewport or a bad camera can otherwise look blank.

## Treat `.pse` as a separate deliverable

A successful `save file.pse` response is not proof that the session contains
coordinates. Verify all three:

1. The file is plausibly sized for the structure.
2. A fresh PyMOL process can open it.
3. `list_instances` reports the expected object names and a proof render is
   non-blank.

If reopening reports no objects, stop resaving the same live state. Export or
recover the source PDB/mmCIF, rebuild from that source, and create the session
with native PyMOL. The MCP save wrapper has produced small settings-only `.pse`
files in practice while still reporting success. A native headless save is a
valid fallback:

```
pymol -cq -d "load /abs/model.pdb, model; <styling>; save /abs/model.pse; quit"
```

Keep a reconstruction `.pml` and the source coordinate file beside important
publication sessions. Do not overwrite the last known-good `.pse` until the new
one passes the fresh-process check.

### Build publication scenes reproducibly

Treat a publication scene as a build, not as mutable GUI state:

```
source PDB/mmCIF + build_figure.pml -> verified PSE + PNG + TIFF
```

Start the script with `reinitialize`, load the source coordinates explicitly,
and include every representation, color, label, contact, camera and render
command. Run the script with native PyMOL when producing a `.pse`.

For interaction panels:

- Use curated water residue IDs; a broad `solvent within N` selection usually
  includes irrelevant waters.
- Draw water-mediated contacts as their actual water-to-water and
  water-to-atom segments, not one long residue-to-base distance.
- Keep DNA backbone, featured bases and recognition side chains as separate
  selections and representations.
- Add `not alt B` when anchoring labels if alternate conformers are present.
- Preserve the target panel's aspect ratio when verifying a saved camera.

See [references/publication-rendering.md](references/publication-rendering.md)
for the complete build and verification sequence.

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
- `orient` uses the coordinates of its selection to calculate principal axes.
  Duplicate objects, such as separate alternate-conformer overlays, change that
  calculation and can rotate an otherwise identical scene. Orient the original
  structure first, then create overlays, and do not call `orient` again.

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

For a complex figure, keep the successful commands as an ordered reconstruction
recipe rather than relying on the live window. Include loading, representations,
colors, object creation, orientation and the final render. This makes recovery
from an accidentally closed window deterministic; replay broad colors before
the narrow highlight colors they would otherwise overwrite.

Also capture `get_view` after framing. Restoring that 18-value list with
`set_view` is faster and more exact than repeating `orient` and manual turns.

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

### Showing alternate DNA conformers and their backbones

Alternate locations often share blank-alt atoms and diverge only at `alt A` or
`alt B`. Build one object from the shared atoms plus each conformer:

```
create dna_conf_a, polymer.nucleic and not alt B
create dna_conf_b, polymer.nucleic and not alt A
hide everything, dna_conf_a
hide everything, dna_conf_b
show ribbon, dna_conf_a
show ribbon, dna_conf_b
color forest, dna_conf_a
color yellow, dna_conf_b
```

Use `ribbon` when the goal is a backbone-only trace over an all-atom line model.
`cartoon tube` on nucleic acid can add unwanted base ladders. Create these
objects only after orienting the original structure, as described under
"Framing the view".

Coloring a whole DNA selection after coloring featured bases will overwrite the
feature color. Apply conformer/backbone colors first, then recolor and show the
specific bases as sticks or spheres. Expose recognition residues similarly with
one atom of backbone context so side chains remain connected:

```
show sticks, (chain A and resi 278+282+285) and (sidechain or name CA)
```

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
