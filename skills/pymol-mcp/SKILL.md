---
name: pymol-mcp
description: Drive PyMOL through the pymol MCP server. Covers the typed tools (get_chains, count, list_residues, contacts, get_gaps, get_secondary_structure, get_sequence, measure, select, apply, clear_selections, inspect_setting, unset_setting, get_representations, get_history, save_file, render_png, render_movie) and when to prefer them over selection strings, starting PyMOL when none is running, targeting one of several instances, what the command table does and does not accept, selection syntax and its silent traps, splitting DNA from RNA, labelling, transparency and why a setting can be sticky at three different layers, reading back what was run, saving sessions that actually contain something, rendering stills and animations, and verifying a change by looking at it. Use whenever calling mcp__pymol__* tools, or when a PyMOL tool call fails to connect.
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

### Agents share an instance — partition by port before you start

**Every agent that talks to this server hits the same port by default.** A
subagent, a background task, and the main session all connect to the one PyMOL,
and none of them can see that the others exist. They will silently overwrite each
other's representations, colours, camera and objects.

The symptom is alarming and easy to misdiagnose: a render comes back showing a
**completely different figure** — one you built ten calls ago, or one you never
built at all — with no error anywhere. In one session a finished domain overlay
rendered as an unrelated base-pair close-up, and the first instinct was to blame
the user's GUI. It was another agent driving the same port.

Tells that it is port sharing rather than the user:

- `list_instances` reports **objects you never created** — a stray `hb1`/`hb2`
  distance object, an object under a name you did not choose.
- Representations revert wholesale to an **earlier** state of your own work,
  which a user click cannot easily do but a replayed setup batch can.
- Settings you just set read back correct from `inspect_setting` while the render
  disagrees.

The fix is to take your own window and never omit the port again:

```
# launch a second PyMOL, then find the new port
list_instances                      # 9876 = shared, 9877 = yours
```

Then pass `instance=9877` on **every** call — `parse_and_execute`,
`execute_batch`, `render_png`, the typed tools, all of them. Two instances make
omission an error rather than a silent guess, which is a feature here.

**Do this at the start whenever background agents are in play**, not after the
first corrupted render. Reloading and rebuilding a scene is cheap; not noticing
is what costs you, because a mislabelled figure can reach disk before you look at
it. Verify any figure you have already written out after discovering a shared
port — one had to be overwritten in the session that produced this note.

### Never kill a PID you have not matched against `list_instances`

Killing "the instance I just launched" from a `ps` listing is how you close the
window the user is working in. A launch that produced no listener leaves a
process that looks exactly like a healthy one in `ps`, and a user who restarted
PyMOL themselves — between two of your calls, invisibly — leaves a *live*
instance whose process age looks freshly started. Both were true at once in one
session, and the wrong process was killed.

`ps` cannot distinguish them; the port can. Before any `kill`:

```
list_instances          # gives instance=<port>, pid <n> for every live PyMOL
```

Kill only a PID that `list_instances` does **not** list, and say which port each
surviving instance holds. If the PID you meant to kill turns out to hold a port,
it is serving somebody — including possibly you, two calls ago.

The cheaper move is usually not to kill anything. A stalled launch with no
listener is harmless: it holds no port, so the next launch still works, and the
user can close the stray window themselves.

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

### Prefer a typed tool where one exists

Several tools take a `Selector` — fields, not a selection string — and return
structured data. Reach for them first; they remove whole classes of silent
mistake documented further down.

| Tool | Answers |
|---|---|
| `get_chains` | what is in this object, with spans and gaps |
| `count` | atoms, residues and chains in a selection |
| `list_residues` | which residues, as chain/resi/resn |
| `contacts` | which residues of A are near B |
| `get_gaps` | what is unmodelled in a chain |
| `get_secondary_structure` | helix/sheet/loop per residue, and as runs |
| `get_sequence` | one-letter sequence per chain, with numbering |
| `measure` | distance between two atoms, no scene change |
| `select` | name a selection, and report what it caught |
| `apply` | colour/show/hide/zoom a typed selection |
| `clear_selections` | delete every named selection before rendering |
| `inspect_setting` | a setting at all three layers, and which atoms override it |
| `unset_setting` | clear a scoped override, at a layer you choose |
| `get_representations` | what is currently shown, per object and chain |
| `get_history` | what was run, what failed, and where files went |
| `save_file` | save, and get back the path, size and object count |

A `Selector` takes `object`, `chain`, `residues`, `residue_range`, `molecule`,
`atom_names`, or `raw` as an escape hatch for anything the model cannot say.
`raw` is deliberately available — PyMOL's algebra is more expressive than the
model — but reaching for it puts the traps below back in play. A single field
renders as a bare word (`Selector(object="bac")` → `bac`), which matters only
for settings; see below.

### Commands that do NOT exist in the table

| You want | Table has | Use instead |
|---|---|---|
| `iterate` / `print` | neither | `mcp__pymol__list_residues`, returning chain/resi/resn |
| counts | neither | `mcp__pymol__count`, returning atoms, residues and chains |
| RNA vs DNA selector | neither | `Selector(molecule="rna")`, or residue names by hand |
| a setting's real value | `get_setting`, global layer only | `mcp__pymol__inspect_setting` |
| to clear a scoped setting | `unset`, where punctuation picks the layer | `mcp__pymol__unset_setting`, which takes a scope |

`bg_color white` works; it used to be missing and need `set bg_rgb, white`.

`alter` and `alter_state` expressions are restricted to arithmetic over atom
properties, because PyMOL evaluates them as Python. `b + 10`, `int(resi) + 100`
and `'A' if chain == 'B' else chain` are fine; attribute access, subscripting,
lambdas, comprehensions and any call outside `str int float abs round len min
max` are rejected.

Use `mcp__pymol__get_view` and `mcp__pymol__set_view` to preserve an exact
camera across experiments or restarts. Use `mcp__pymol__inspect_setting` to read
a setting — `get_setting` returns the global layer as a formatted string and
cannot see a per-object or per-atom override. The server still cannot enumerate
arbitrary atom properties, but the typed tools cover the common questions:
`count`, `list_residues`, `get_chains`, `get_gaps`, `contacts` and
`get_representations` all return structured data. Inspect a render for anything
genuinely visual.

## Measuring

`mcp__pymol__measure` takes two selections that must each match exactly one
atom, and returns the distance. Anything else is an error rather than a silent
average over whatever matched.

Prefer it to the `distance` command, which answers the same question but leaves
a labelled distance object in the scene that then appears in every subsequent
render. Reading a number should not change the picture.

**When you *do* want the dashes drawn**, `distance` is the right tool — a figure
about a hydrogen bond should show it. Then:

```
distance hb1, <sel1>, <sel2>      # each selection must be comma-free
color black, hb1                  # colour the object by its name
hide labels, hb1                  # keep the dash, drop the number
delete hb1                        # remove both
```

`hide labels` is the one to reach for when the caption carries the value and the
number would only clutter the frame. Drawing the *absent* contacts alongside the
real one is a good way to show a pair is open rather than missing — but they
crowd the labels fast, so colour the real bond black and the rest `grey60`, and
be ready to delete them.

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

### A ray render defaults to a *transparent* background

`bg_color white` sets the background's **colour** but not its **opacity**. With
`ray_opaque_background` left at its default `-1`, a ray render writes RGBA with
the background at **alpha 0** — white pixels that are fully transparent. The file
then shows a checkerboard in any viewer that displays transparency, and the user
reports the render as broken when the scene is fine.

```
set ray_opaque_background, 1
```

**You cannot catch this by looking at the returned image.** The MCP image block
composites onto white, so a transparent background is pixel-identical to an
opaque one in the tool result — it only appears when the user opens the file.
Check the bytes instead:

```
python3 -c "
from PIL import Image
im = Image.open('fig.png')
print(im.mode, im.getpixel((0,0)),
      im.getchannel('A').getextrema() if im.mode == 'RGBA' else 'no alpha')
"
```

`(255, 255, 255, 255)` with alpha extrema `(255, 255)` is a genuinely opaque
white background. `(255, 255, 255, 0)` is the broken one. Verify this for any
render you hand over as a deliverable.

### Transparency is dropped in renders larger than the viewport

A ray render **wider than the current viewport silently loses
`stick_transparency`** — ghosted geometry returns fully opaque, with no error and
no change to the setting (`inspect_setting` still reports the value).

Verified by holding camera, scene and settings fixed and changing only the size:
at the viewport's 1200×900 the ghosted residues rendered translucent; at
2400×1800 and 2800×2100 they were solid. `transparency_mode` is **not** the
cause — 1 and 3 fail identically. Observed on one Linux/GL build, so re-test
before relying on the exact threshold elsewhere.

The practical consequence: **a ghosted figure is capped at the viewport
dimensions.** Raising `viewport` first would lift the cap, but that resizes the
user's window, so ask rather than doing it mid-session — see the next section.
Do not chase this as a settings bug; re-applying the transparency appears to fix
it only because the next proof render is usually back at viewport size.

### Matching one figure to another: crop, then compensate the label size

When a second figure must sit beside a first at the same apparent scale — and
especially when the user has set the camera themselves, so you cannot `zoom` —
crop rather than re-frame. Measure both against the white background:

```bash
python3 -c "
from PIL import Image, ImageChops
im = Image.open('fig.png').convert('RGB')
bbox = ImageChops.difference(im, Image.new('RGB', im.size, (255,255,255))).getbbox()
W,H = im.size; x0,y0,x1,y1 = bbox
print('fill w=%.3f h=%.3f' % ((x1-x0)/W, (y1-y0)/H))
"
```

Match the **fill fraction**, not the pixel size. Crop the second image to the box
that reproduces the first's fraction, centred on its content, then resize back to
the same output dimensions.

**The catch: labels do not survive the upscale unchanged.** `label_size` is in
screen points, so it does *not* scale with the molecule — a crop-and-upscale of
1.84× makes the text 1.84× too large relative to the reference figure. Divide
before rendering:

```
label_size_for_crop  =  reference_label_size / upscale_factor
```

32 → 17 at 1.84×. `dash_width` and `label_connector_width` are also in pixels and
need the same division; stick radius is in Ångströms and does not.

Two practical notes. **Reuse one hard-coded crop box** across iterations rather
than recomputing it from the content bbox — the labels are part of the content,
so recomputing shifts the framing every time you retune them. And shrinking the
font means the labels sit closer to the atoms, so `label_position` usually has to
grow to keep them off the geometry; expect to tune the two together.

This is a workaround for the viewport cap above, and it costs resolution — the
result is an upscale, visibly softer on glyphs. Say so when handing it over.

### Global settings that outlive the figure you set them for

`ray_trace_mode` is **global**. Set it to 1 for one outlined figure and every
later render in the session inherits outlines. Same for `use_shaders`,
`transparency_mode`, `ray_opaque_background` and the `label_*` family. Reset
explicitly when you are done rather than relying on a restart, and prefer a
scoped `set ..., <selection>` where the setting supports one.

### Set the viewport once — it resizes the user's window

`viewport` is not a render parameter, it resizes the actual PyMOL window on the
desktop. So does an **un-raytraced** `png` at dimensions that do not match the
current viewport: PyMOL resizes the window to grab the framebuffer at the
requested size. Neither announces itself, and from the tool side both look like
ordinary render bookkeeping.

Issued once at the start this is invisible. Issued before every proof render —
which is easy to fall into when chasing a visual bug — the window jumps on every
call, and a user chasing a GUI problem is now also chasing a moving target. In
one session this produced a run of complaints (a stale drawing area, a white
band across the window, widget text that had apparently shrunk) that were
downstream of the resizing, not of anything molecular. The camera also appeared
to drift between renders, which was misread as the user rotating in the GUI.

The policy that keeps the window still:

- **`viewport` once per session**, at the start, and never again.
- **Un-raytraced proofs render at exactly the viewport dimensions.** Matching
  means no resize.
- **Any other size uses `ray=true`.** The ray-tracer renders offscreen at
  arbitrary dimensions and never touches the window, so a 3200 px publication
  render costs no resize at all.

Pin it across sessions by appending to `~/.pymolrc.py`, **after** the managed
`# >>> pymol-mcp auto-start >>>` block — the installer overwrites anything
inside it:

```python
from pymol import cmd
cmd.viewport(1200, 900)
```

### GUI text size is not adjustable

If widget text looks too small, the reachable setting is `display_scale_factor`,
and it will not fix it. It scales the internal GUI's *control geometry* but not
the object panel's glyphs, which are a fixed bitmap font — so
`display_scale_factor 2` yields big icons around text that is exactly as small
as before, which reads as worse than leaving it alone. There is no
`internal_gui_font_size`; the name is rejected as an unknown setting.

`internal_gui_control_size` (default 18) and `internal_gui_width` (default 220)
are real and do what they say, so offer them for panel rows and truncated object
names. For the text itself, say plainly that PyMOL does not expose it rather
than cycling through settings on the user's window.

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

**But drift cuts both ways: sometimes the moved camera is the deliverable.** When
the user says "use my view" or "keep my orientation", they have framed it in the
GUI and every `orient`, `zoom`, `turn` or `reset` you issue destroys it — there is
no undo, and you cannot reconstruct it because you never saw it. From then on,
render with no camera command at all, and adjust framing by cropping the image
instead (see the crop-to-match section above).

Capture it immediately with `get_view` so a later restart or accidental `orient`
is recoverable via `set_view`. Doing this *after* framing and *before* the first
render is cheap insurance; not doing it cost a good composition in one session.
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

**Use `mcp__pymol__save_file` rather than the `save` command.** It returns the
path, byte size, object list and atom count, and for a `.pse` also
`objects_verified` — the objects whose names are present in the written bytes.
An empty `objects_verified` alongside a non-empty `objects` means the file does
not contain the session you think it does.

A publication `.pse` still deserves a fresh-process open: `objects_verified`
proves the names are in the bytes, not that the file loads. If it comes back
empty, rebuild from the source coordinates rather than resaving the same live
state. A native headless save is a valid fallback:

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

## A selection-scoped `set` is sticky and survives everything

`set <name>, <value>, <selection>` writes a **per-object** setting that lives on
the atoms, not on the current representation. It is not cleared by `hide`, by
`show`, by recolouring, or by a later **global** `set <name>, <value>` — global
and per-object are separate layers, and the per-object one wins.

The one that bites is `cartoon_transparency`. Set it on a selection for one
close-up:

```
set cartoon_transparency, 0.75, chain C     # push the RNA back, once
```

and every later figure of that object renders washed out, with no obvious cause
— the colours are right, they are just faded. `set cartoon_transparency, 0`
alone does **not** fix it.

**Find it with `mcp__pymol__inspect_setting` — check `overridden` — and clear it
with `mcp__pymol__unset_setting`.** Clearing is not the same as setting 0:
`set ..., 0` pins the atoms at zero, clearing lets them inherit the layer
beneath.

Writing `set` or `unset` by hand, **punctuation picks the layer**: a bare
identifier (`ala`, `all`) addresses the object layer, anything parenthesised or
compound addresses the atoms. Clearing the wrong one reports success and changes
nothing, so `(all)` is a working blanket reset and bare `all` is not.
`unset_setting` sends the layer explicitly, which is why it does not ask you to
remember that.

A **global** `set cartoon_transparency, 0.6` — no selection at all — works
reliably and applies to every atom that carries no override. Combined with the
layering above that is a genuinely good pattern rather than a workaround: set
the global to ghost everything, and pin the features you want crisp to 0 with a
selection-scoped `set`. Ghosted backdrop, solid highlights, no representation
changes needed.

> **Do not judge "did the setting apply?" by eye at moderate values.** Chasing
> this, a cartoon at `0.5` against a light background was repeatedly misread as
> opaque, and a whole false mechanism — "the atoms are stuck, later `set`s are
> being ignored" — was built on top of that misreading. Setting the same
> selection to `0.95` made it vanish instantly and showed the `set` had been
> working the entire time.
>
> If you need to know whether a scoped `set` landed, **test with an extreme
> value (0.95), not a plausible one.** Then dial back to the value you actually
> want. Mid-range transparency differences are not reliably readable in a ray
> render, which makes them a poor diagnostic and an easy way to talk yourself
> into a bug that is not there.

**`get_setting` cannot diagnose this, and never could.** It reports the global
layer only, formatted as a string: with the HNH visibly transparent on screen,
`get_setting cartoon_transparency` returned `0.00000`. A clean reading there
says nothing about the scene. Use `inspect_setting`, which returns the same
value as `display` alongside the object and atom layers that actually explain
the render.

The same is true of `stick_transparency`, `sphere_transparency`,
`cartoon_tube_radius` and any other per-object setting. If a render is
inexplicably pale or oddly shaped and the colours are correct, suspect a
leftover scoped `set` from an earlier view.

### When transparency works, and when to hide instead

Transparency has a reputation here for not decluttering, and the failure is
real: `cartoon_transparency 0.85` on a 643-nucleotide RNA cartoon still let the
RNA dominate the frame, and hiding it outright was the only fix.

But that is one case, not a rule about transparency in general.

**Default to transparent full cartoon.** It looks better than a tube and in
practice it usually reads fine — a domain-sized selection at `0.5`, or a whole
chain pushed to `0.85`–`0.9`, will normally sit back far enough. Raise the
transparency before you change the representation.

Only if cartoon still competes at high transparency is it worth thinning the
geometry, and then `cartoon tube` with a small `cartoon_tube_radius` is the
fallback — broad ribbons and helices carry more edge and shading than a 0.2 Å
tube, so there is less left to read as clutter. Treat that as a last resort for
a crowded frame, not a default.

> **Do not trust a single side-by-side here, including your own.** The
> comparison that produced the original "tube is better" advice was confounded:
> cartoon was judged at `0.75` and the tube at `0.88`, so it measured
> transparency, not representation. Change **one** thing, hold camera and
> transparency fixed, and re-render — and expect the answer to depend on the
> view rather than to generalise.

**Transparency works when you want to see *both* layers; it fails when you want
one layer gone.** Three situations:

**1. Superposed copies of the same thing — full cartoon, both transparent.**
This is where it works best. Two conformations of one domain, opaque, means
whichever is in front simply hides the other and the comparison is lost. At
`cartoon_transparency 0.5` on *both*, the two interpenetrate and you can read
where they agree and where they diverge in a single view:

```
set cartoon_transparency, 0.5, objA and chain A and resi 540-606
set cartoon_transparency, 0.5, objB and chain A and resi 540-606
```

Keep whatever you are highlighting — sticks, spheres, ligands — **opaque**. Solid
sticks against two ghosted cartoons stay perfectly crisp and give the eye
something to anchor on.

**2. Whole-chain context you want present but silent — push the transparency up
first.**

```
set cartoon_transparency, 0.88, <backdrop selection>
```

Two whole protein chains at `0.88` sat behind a pair of highlighted domains
without competing. Reach for the tube only if that is still too busy:

```
cartoon tube, <backdrop selection>
set cartoon_tube_radius, 0.15
```

Whichever you use, **look at the result** rather than assuming — and if you are
comparing the two, change only the representation and hold the transparency
value fixed.

**3. Something big and simply in the way — `hide` it.** If you would not miss it
from the figure, transparency is the wrong tool and will only cost you render
time.

For any of these, turn off transparent shadows or the ghosted geometry casts
solid ones:

```
set ray_transparency_shadows, 0
```

### `transparency_mode 2` deletes what is behind the transparent thing

That line used to read `set transparency_mode, 2` here, paired with the shadow
setting above as though the two went together. They do not, and mode 2 has a
failure that looks exactly like the transparency never being applied.

Mode 2's **real-time** path does not depth-sort correctly, so geometry *behind*
a transparent cartoon is dropped rather than blended. The ray-traced render of
the identical scene is correct. On a protein/DNA complex at
`cartoon_transparency 0.6`, a DNA strand crossing the protein vanished in the
GUI across the whole crossing and reappeared on either side, while the ray
render showed it running through continuously.

This is the single most convincing way to be told transparency is broken when
it is not, so establish which it is before touching the setting:

```
inspect_setting cartoon_transparency, <the transparent selection>
```

`overridden: true` with a uniform value on the expected atom count means the
setting is applied and you are looking at a *rendering* problem, not a settings
problem. Then render the same camera twice, `ray=false` and `ray=true`. If the
occluded geometry appears only in the ray render, it is this.

The fix for the interactive view is the shader-based path:

```
set transparency_mode, 3
```

**Do not reach for `use_shaders 1` alongside it.** Doing so on a Linux/GL box
corrupted the internal GUI — distorted icons in the right-hand panel and a white
band across the drawing area. `transparency_mode 3` with `use_shaders 0` kept
the transparency fix and dropped the corruption. `use_shaders` is global, so
turning it on for one figure changes every scene afterwards.

This is also a genuine mechanism for "it worked on my other machine": the
real-time path depends on the GPU and driver, so an identical session with an
identical setting legitimately renders differently on a Mac and on Linux. The
setting is not what differs. Check `inspect_setting` before believing otherwise.

## Fog hides the thing you zoomed in on

PyMOL fades distant geometry by default. On a whole-complex view that reads as
depth; on a close-up it silently deletes context — a neighbouring domain can
wash out to near-white and look absent rather than distant.

```
set depth_cue, 0
set ray_trace_fog, 0
```

Turn both off for any zoomed figure, then decide whether you want the fade
back. The failure is easy to misread as a colouring mistake: the geometry is
there, correctly coloured, and simply not visible.

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

**Read it with `mcp__pymol__get_history`**, not with a shell — the directory is
only known inside PyMOL, which resolves `PYMOL_MCP_HISTORY` from its own
environment. `failed_only=True` finds the attempt that did not work;
`command="load"` finds what was loaded. `limit` counts matching records, so a
filter never comes back empty just because recent traffic was something else.

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
get_history(command="load")
get_history(command="fetch")
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

Splitting RNA from DNA has no dedicated selector. Use
`Selector(molecule="rna" | "dna" | "protein")`, which applies the residue-name
idiom for you. By hand it is `polymer.nucleic and resn DA+DC+DG+DT+DI` for DNA,
and `not resn ...` for RNA; modified residues fall on the RNA side.

### `byres` swallows the rest of the expression

Use `mcp__pymol__contacts`, which always returns whole residues and narrows via
`atom_names`, so operator placement never arises.

By hand, `byres` binds looser than `and`: `byres A and name C1'` means
`byres (A and name C1')` — "residues whose C1′ satisfies A" — not "residues
satisfying A, then their C1′ atoms". On one interface that is 4 residues rather
than 30. Both forms return a plausible number and neither errors, so
parenthesise the group explicitly: `(byres A) and name C1'`. Same for
`bychain`, `bymolecule`, `byobject`.

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

### Reading secondary structure

`mcp__pymol__get_secondary_structure` returns it per residue plus a run-length
`pattern` like `22H3L15H` — which is what identifies a helix-turn-helix, where
"37 helical residues" does not.

By hand there are two traps. `color_ss` only *colours* by structure, so reading
it back means looking at a render. And **`ss L` silently matches nothing**: a
loop is the absence of an assignment, not the value `L`.

```
resi 248-287 and ss H                     -> 37   correct
resi 248-287 and ss L                     ->  0   wrong, silently
resi 248-287 and not (ss H or ss S)       ->  3   correct
```

### Negative residue numbers must be escaped

Anything built from a `Selector`/`ResidueRange` is already correct. By hand:

`resi` reads `-` as a range operator, so `resi -12` silently means "everything
up to 12" — 719 atoms rather than 20. It does not error. Escape every negative
endpoint, including the second one in a range:

```
resi \-12        one residue          resi -12       719 atoms, wrong
resi \-12-\-8    5 nucleotides        resi \-12--8   ~20, wrong
resi \-5-1       6 nucleotides        (high end positive, one escape)
```

Escaped numbers compose in `+` lists: `resi \-12+\-11+1+2`. Because the failure
is silent, check the returned count (~20 atoms per nucleotide, ~8 per residue)
before acting on it.

## Enumerating and colouring chains

`mcp__pymol__get_chains` returns every chain with its molecule type, atom and
residue counts, numbering span and gaps:

```
A protein 4900 atoms  602 res    5..606   no gaps
C rna    13792 atoms  643 res    1..957   6 gaps
D dna      489 atoms   24 res  -13..22    1 gap
F ligand     2 atoms
```

That replaces the old trick of running `util.cbc` for the chain IDs it happens
to print while recolouring, then undoing its colours. It also reports gaps,
which no command could previously reveal -- a nicked chain is still one chain,
and the break shows up only in the numbering.

**The `kind` field can be wrong. Do not branch on it.** On PDB entry `4faq`,
which RCSB records as nucleic-acid-only, `get_chains` reports chain A as
`kind: "protein"`. Counting settles it:

```
count 4faq and polymer.protein     # 0 atoms
count 4faq and molecule=rna        # 8478 atoms
```

The atom and residue counts, the span and the gaps have all been reliable; it is
the type label that misfired. If a decision depends on whether a chain is protein
or nucleic, spend one `count` call confirming it rather than trusting the label —
the failure is silent, and a `polymer.protein` selection that returns nothing
looks exactly like a chain that is genuinely absent.

Colour per chain explicitly once you know what is there:

```
color red, dna and chain D
color marine, dna and chain E
```

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
set label_position, -5 -5 5     # spaces, NOT (-5,-5,5) -- see below
set label_connector, on
set label_connector_color, grey40
set label_outline_color, white
```

### Float3 settings need spaces, not commas

`set` matches `^set\s+([\w.]+)(?:\s*,\s*([^,]+))?(?:\s*,\s*(.+))?$`, and the
**value group forbids commas**. So the usual PyMOL form fails:

```
set label_position, (3,3,3)     # could not convert string to float: '(3'
set label_position, 2 2 2       # works
```

Confirm with `inspect_setting label_position`, which reports back
`[ 2.00000, 2.00000, 2.00000 ]`. The same applies to any float3 setting.

Two limits worth knowing before you promise a layout. `label_position` is
**global** — every label shifts by the same vector, so labels cannot be placed
individually from here; that is a GUI drag. And the offset is in **model**
coordinates, not screen, so which direction clears the frame depends on the
current camera and is found by trial: `4 4 4` and `-5 -5 5` push opposite ways.

### The label expression accepts string literals

`resn+resi` on a DNA residue gives `DG-6`, which reads badly in a figure. The
natural fix `resn[1:]+resi` is **rejected** — the expression allowlist forbids
subscripting. A literal is accepted:

```
label bac and chain C and resi 291 and name C1', "C291 RNA"
```

The parser splits on the first comma only, so the literal may itself contain
commas.

## Animations

`mcp__pymol__render_movie` returns an animated GIF as an image block, so it
appears inline. MCP has no video content type; a GIF is an image, which is the
whole reason this works. Pass a `.webp` filename for much better compression if
the client renders animated WebP.

```
render_movie(filename="/tmp/spin.gif", mode="spin", frames=24, width=480)
render_movie(filename="/tmp/traj.gif", mode="states", frames=20, start_state=1)
```

`mode="spin"` turns the camera a full 360° about `axis`; `mode="states"` steps
through object states, for trajectories and NMR ensembles.

Defaults are small and un-raytraced on purpose. A ray-traced frame takes
seconds, so `ray=True` with 24 frames is a minute of waiting — worth it for a
final, not for a look. Caps apply at 120 frames, 4 MP per frame and 8 MB
encoded; when one bites, the metadata says so in `truncated`, `dropped_frames`
and `note` rather than quietly returning a shorter movie.

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
set transparency_mode, 3                       # not 2; see the note above
set depth_cue, 0
set ray_trace_fog, 0
png <scratchpad>/check.png, width=1000, height=800, dpi=150, ray=1
```

## Irreversible actions

`hide everything, <sel>` destroys the representation state for that selection,
and there is no undo.

**Call `mcp__pymol__get_representations` first if you intend to put it back** —
it reports what is shown per object and chain. Without that record you are
*choosing* a representation when you restore, not recovering one. Say so rather
than implying the original came back.
