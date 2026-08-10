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

Launching PyMOL is a visible desktop action. After the user approves it, prefer
the typed `launch_pymol` tool. The MCP server owns the child process, waits for
the socket listener, and returns the new instance port. This works in managed
command environments that reap children of a disposable shell.

If the typed tool is unavailable, first check for the user's own launcher:

```bash
type pymolq
```

`pymolq` may be a shell function rather than an alias, so `type` is the
reliable test. Do not suppress this check's error and mistake a background
shell's status for a successful launch.

In a managed command runner, start the resolved executable **in the foreground**
and let the runner yield a persistent session ID:

```bash
/full/path/to/pymol -q
```

Do not append `&`: disposable execution shells commonly reap their background
process group as soon as the shell returns. A TTY is not required. In an
ordinary user-owned terminal, the traditional platform launchers remain valid:

| Platform | Command |
|---|---|
| macOS / Linux | `pymol -q >/dev/null 2>&1 &` |
| Windows (cmd) | `start "" pymol.exe -q` |
| Windows (PowerShell) | `Start-Process pymol -ArgumentList '-q'` |

Three things must be right, and each has bitten someone:

- **Keep it owned.** Use `launch_pymol`, a persistent foreground command-runner
  session, or a user-owned terminal. Even `pymol -h` may open the GUI instead
  of printing help.
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


## Continue by visual task

For rendering and visual verification, publication exports, viewport and transparency behavior, saved sessions, scene recovery, selection syntax, chain styling, labels, animations and representation safety, read [references/visualization-and-selection.md](references/visualization-and-selection.md).

Always render and inspect the result after a visual change. Use [references/publication-rendering.md](references/publication-rendering.md) for paper-matched figures and publication deliverables.

Before any irreversible representation change, record the current state with `mcp__pymol__get_representations`; before overwriting a file, confirm the path and verify the written artifact.
