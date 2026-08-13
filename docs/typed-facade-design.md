# Design: a typed facade over the command table

Status: **implemented**, stages 0-5, commits b683430 through this one.
Kept as the record of why the design is shaped this way. Two things changed
under contact with the code -- see "What changed in implementation" at the end.

## The problem

> **Correction, after implementing.** The premise below -- that most of what the
> skill documents is selection workarounds, so typing the interface would delete
> the prose -- was wrong, and measurement settled it. Only 21% of the skill was
> ever selection-related; the other 79% is launching PyMOL, verifying renders,
> session recovery and `.pse` handling, none of which a typed API touches. The
> skill went 627 lines to 633 across the whole project. Compressing the
> selection sections afterwards took them from 144 lines to 97, which is a real
> but modest cut.
>
> The work was still worth doing, for a different reason than the one argued
> here. It added eight capabilities the server did not have (get_chains, count,
> list_residues, contacts, get_gaps, render_movie, enable, disable) and fixed two
> plugin bugs. But the lasting reason is where capability lands *next*.
>
> The skill had been acting as the overflow buffer for everything the API could
> not do. Three of its sections existed only because a tool was missing: chain
> enumeration ("run util.cbc for the IDs it prints while recolouring, then undo
> its colours"), counting ("run select and read the atom count out of the reply
> text") and gaps ("parse the structure file by hand"). None of that is PyMOL
> knowledge — it is the shape of a hole, written down.
>
> With a typed surface in place, the next gap gets filled as a tool instead of
> as another paragraph. That matters because a tool ships its schema to the
> caller, whereas a paragraph only works if someone read it. Judge the design
> below on extensibility, not on brevity.

The server exposes PyMOL's selection language as strings. That language has
sharp edges, and because a malformed selection is usually *valid* — just not the
one intended — every edge fails silently and has to be documented instead of
prevented. The `pymol-mcp` skill currently carries, among others:

- `resi -12` parses as "resi ≤ 12" and returns 719 atoms instead of 20. Each
  negative endpoint in a range needs its own backslash: `\-12-\-8`, not
  `\-12--8`.
- `byres A and name C1'` parses as `byres (A and name C1')`, so it answers
  "residues whose C1′ atom specifically satisfies A". On one interface that gave
  4 residues where the intended reading gave 30.
- There is no `iterate` or `print`, so the documented way to count is to run
  `select` and read the atom count out of the reply text.
- There is no way to enumerate chains, so the skill recommends `util.cbc`
  *for its side effect* of printing chain IDs, then overriding the colours it
  applied.
- There is no `enable`/`disable`, so comparing two loaded objects means
  `hide everything, <other>` and re-`show`ing afterwards.

Each of these is a paragraph of prose standing in for a type. The proposal is to
delete the prose by making the interface typed.

## What this is not

**Not FastAPI, and not Swagger.** MCP already is the schema-carrying protocol:
tool inputs are JSON Schema, which is the job Swagger does for HTTP. Adding
FastAPI would mean running an HTTP server that nothing calls over HTTP.

Pydantic, on the other hand, is already a dependency and already load-bearing —
`models.py` defines the socket messages, and FastMCP builds tool schemas from
type annotations. The proposal uses what is there rather than adding a
framework.

## What the current architecture already gives us

Three facts make this much cheaper than it looks.

**The transport is already structured.** `PyMOLConnection.send_command` sends
`SocketRequest{type, command, args, source}` and the plugin entry point is
`execute_structured_command(command_name, args)`. Nothing about the wire format
is string-oriented. The regex command table in `server.py` is a layer that turns
strings *into* this structure — it is not the structure itself.

**Structured returns need no protocol change.** `SocketResponse.result` is typed
`Any`. A handler returning a dict travels back to the server as-is.

**FastMCP already emits structured output.** Annotating a tool's return type
generates an `outputSchema` and returns both unstructured text and a validated
structured payload. Verified against the installed SDK (1.28.1):

```python
def get_chains(obj: str) -> ChainsOut: ...
# -> outputSchema generated; structured payload:
# {"object": "bac", "chains": [{"chain": "C", "kind": "rna",
#   "residues": 643, "first": 1, "last": 957, "gaps": [[603, 853]]}]}
```

This is the same machinery that broke `render_png` (commit `ae6a0ab`), which
currently sets `structured_output=False` because `Image` is not serialisable.
That workaround is a floor, not a ceiling — see below.

## Images and other non-JSON content

`render_png` today returns `[str, Image]` with `structured_output=False`, so its
metadata — path, dimensions, DPI, whether it was ray-traced — reaches the caller
only as English prose to be re-parsed:

```
Rendered /tmp/fig.png (1200x900, 300 DPI, ray=on)
```

Images do not have to be excluded from the typed path. The mistake in the
original bug was trying to put an `Image` *inside* the structured payload, which
pydantic cannot serialise. The two channels are separate, and the right split is
to use both:

- **bytes** travel as MCP `ContentBlock`s (`ImageContent`), which is what the
  client renders;
- **metadata** travels as `structuredContent`, validated against a model.

The SDK supports exactly this. Annotate the return as `CallToolResult` with the
validation model attached, and return both:

```python
class RenderMeta(BaseModel):
    path: str
    width: int
    height: int
    dpi: float
    ray: bool

@mcp.tool()
def render_png(...) -> Annotated[CallToolResult, RenderMeta]:
    return CallToolResult(
        content=[TextContent(type="text", text=f"Rendered {path}"),
                 ImageContent(type="image", data=b64, mimeType="image/png")],
        structuredContent=RenderMeta(...).model_dump(mode="json"),
    )
```

Verified against SDK 1.28.1:

- an `outputSchema` is generated from `RenderMeta`, so the metadata is typed;
- `convert_result` passes the `CallToolResult` through with both content blocks
  intact;
- `structuredContent` is validated — a missing field raises `ValidationError`
  rather than shipping a malformed payload;
- the image bytes are carried **once**, in `ImageContent`. Putting base64 into
  `structuredContent` as well would double a ~540 kB payload for no gain.

The same shape covers any future tool returning bytes plus facts — a saved
`.pse` with its size and object count, an exported movie, a coordinates dump.

**Consequence for `render_png`:** the current `structured_output=False` is not
the end state. It should be revisited as `Annotated[CallToolResult, RenderMeta]`
so the render metadata stops being prose. That is a self-contained change and a
good first exercise of this design, independent of the selection work below.

## The model layer

New module, `src/pymol_mcp/api.py`. Input models describe selections without
requiring the caller to write selection algebra; output models describe results.

```python
class ResidueRange(BaseModel):
    chain: str
    start: int
    end: int

    def to_selection(self) -> str:
        """Render to PyMOL syntax, escaping negative endpoints."""
        lo = rf"\{self.start}" if self.start < 0 else str(self.start)
        hi = rf"\{self.end}" if self.end < 0 else str(self.end)
        return f"chain {self.chain} and resi {lo}-{hi}"


class Molecule(str, Enum):
    PROTEIN = "protein"
    RNA = "rna"
    DNA = "dna"
    SOLVENT = "solvent"
    IONS = "ions"


class Selector(BaseModel):
    """A selection expressed as fields rather than a string."""
    object: str | None = None
    chain: str | None = None
    residues: list[int] | ResidueRange | None = None
    molecule: Molecule | None = None
    raw: str | None = None          # escape hatch, see below
```

`Molecule.RNA` renders to `polymer.nucleic and not resn DA+DC+DG+DT+DI` — the
idiom the skill currently has to teach, applied centrally and correctly.

Both sketches above were checked against a real structure before being written
down. `ResidueRange(chain="E", start=-12, end=-8).to_selection()` yields
`chain E and resi \-12-\-8`, which selects the intended 5 nucleotides, where
the hand-written `\-12--8` silently selects 20.

Output models are ordinary:

```python
class Residue(BaseModel):
    chain: str
    resi: int
    resn: str

class ChainInfo(BaseModel):
    chain: str
    kind: Literal["protein", "rna", "dna", "ligand", "solvent"]
    residues: int
    first: int
    last: int
    gaps: list[tuple[int, int]] = []

class Counts(BaseModel):
    atoms: int
    residues: int
    chains: int
```

### The `raw` escape hatch

`Selector.raw` passes a selection string through untouched. It exists because
PyMOL's algebra is genuinely more expressive than any model we would ship, and
removing the escape hatch would make the typed path a downgrade for expert use.
It should be the *documented* exception, not the default: when `raw` is set, the
other fields are ignored and the existing validation applies.

## Which commands become typed

The split is not "all 83" — it is by whether the command *returns information*
or *has an effect*.

**Introspection — typed, and new.** These do not exist today at all, and are the
ones replacing skill prose. All require plugin-side handlers, because only the
plugin has PyMOL's Python.

| Tool | Returns | Replaces |
|---|---|---|
| `get_chains(object)` | `list[ChainInfo]` | the `util.cbc` side-effect hack |
| `count(selector)` | `Counts` | reading atom counts out of `select` replies |
| `list_residues(selector)` | `list[Residue]` | the absent `iterate` |
| `contacts(a, b, within, whole_residues)` | `list[Residue]` | the `byres` precedence trap |
| `get_gaps(object, chain)` | `list[tuple[int, int]]` | parsing the structure file by hand |
| `inspect_setting(name, selector)` | `SettingReport` | "you have to look at a render" |
| `unset_setting(name, selector, scope)` | `SettingReport` | the four-row clearing table |
| `get_representations(selector)` | `Representations` | "you cannot query what was shown" |
| `get_history(limit, command, failed_only)` | `History` | `grep`ing `history.jsonl` from a shell |
| `export_session(filename, session_id, redact_paths)` | `SessionExport` | manually correlating global JSONL, replay scripts and live scene state |
| `save_file(filename, selector)` | `SaveMeta` | save, reopen in a fresh PyMOL, count objects |

`contacts` is the interesting one: `whole_residues: bool` is exactly the
distinction `byres` placement encodes, expressed as a field that cannot be
mis-parenthesised.

**Effects — typed wrappers over existing commands.** `select`, `color`, `show`,
`hide`, `zoom` take a `Selector` instead of a string. These do not need new
plugin handlers; the server renders `Selector.to_selection()` and dispatches the
command that already exists.

**Everything else — unchanged.** `ray`, `png`, `save`, `load`, `fetch`, `scene`,
the movie commands, `set`, the `util.*` family. They have no selection-algebra
problem and typing them buys nothing.

> **Wrong about two of those.** `set` and `save` both turned out to have a
> selection-algebra problem, and a nastier one than the cases this design was
> built for, because in both the failing form is a *bare word*. `set`/`unset`
> write the object layer when handed a bare identifier and the atom layer
> otherwise; `save` reads a bare `all` as an object name, matches nothing, and
> writes an empty `.pse`. Neither errors. See "Layers, and the bare-word trap"
> below.

**Explicitly out of scope:** `alter` and `alter_state` keep their current
AST-validated expression path. Modelling arbitrary per-atom arithmetic is a
project of its own and the existing validator is the security boundary.

## Migration path

The 83-command table does not go away, and `parse_and_execute` keeps working
unchanged. That matters: it is the path a user takes when they already know the
PyMOL syntax, and the skill's whole "translate the request into literal PyMOL"
contract depends on it.

Five stages, each shippable alone:

0. **Retype `render_png`'s metadata** as `Annotated[CallToolResult, RenderMeta]`.
   Smallest possible slice, touches one tool, proves the structured-output path
   in production before anything depends on it.
1. **Add introspection tools.** Pure addition — new plugin dispatcher entries,
   new typed MCP tools. Nothing existing changes. This alone removes most of the
   skill's selection prose, because those sections exist to work around missing
   introspection rather than to explain PyMOL.
2. **Add `enable`/`disable`** to the dispatcher. One line each, closes the
   object-comparison gap.
3. **Add typed effect wrappers** alongside the string commands — `select_typed`,
   `color_typed`, or a single `apply(command, selector, args)`. Both paths live
   together; neither is deprecated.
4. **Reassess.** If the typed path dominates in practice, consider marking parts
   of the regex table legacy. Do not decide that up front.

The table stays the source of truth for `list_commands`, and
`TestServerPluginSync` keeps enforcing that every server command has a plugin
handler. New typed tools need adding to that check.

## Security

The plugin is deliberately `exec`/`eval`-free: `build_command_dispatcher` maps
names to fixed `cmd.*` calls, `check_atom_expression` AST-validates `alter`
expressions, and `_reject_control_characters` guards against newline injection
into anything string-shaped.

A typed facade **tightens** this rather than loosening it. Selections built from
integers and enum members cannot carry a newline. The `raw` escape hatch is the
one path that still can, and it inherits the existing validation.

New introspection handlers must not become a hole: they should call `cmd.*`
directly and return plain data. `list_residues` in particular must not accept a
caller-supplied expression to evaluate per atom — it returns fixed fields
(`chain`, `resi`, `resn`), which is the whole point.

## Testing

`conftest.py` replaces the entire `mcp` package with a `MagicMock`, so no test
in the suite exercises decorator or schema behaviour. That is how the
`render_png` structured-output bug survived 356 passing tests.

Typed tools are schema-bearing by definition, so they need tests that see the
real SDK. Follow `TestServerModuleIntegrity`, which runs the real import in a
subprocess to escape the stub. At minimum, per tool: `outputSchema` is generated
and non-null, and a representative return value round-trips through
`convert_result`.

Model-level tests (`ResidueRange.to_selection()` escaping both endpoints,
`Molecule.RNA` rendering) are pure functions and can run under the stub.

## Open questions

1. **One `apply` tool or one tool per command?** Many small typed tools give
   better schemas and better discoverability; one polymorphic tool keeps the
   surface small. Leaning toward small tools for introspection, one `apply` for
   effects.
2. **Does `Selector` need boolean composition** (`and`/`or`/`not` between
   selectors), or is `raw` sufficient for anything compound? Composition is
   where selector models usually grow unbounded.
3. **Should `contacts` return pairs rather than residues?** Interface analysis
   usually wants "which of A is near which of B", not just the set of A.
4. **How much does this actually shrink the skill?** Estimate: most of
   *Selecting the pieces*, all of *Negative residue numbers*, all of
   *Enumerating and colouring chains*, and the `iterate`/`print` row of the
   limitations table. It removes **none** of the domain content in a
   structure-specific skill — chain-letter collisions between files, numbering
   offsets, which model is a hybrid. Those are data semantics and no API fixes
   them.


## What changed in implementation

Two departures from the proposal above, both found by verifying against a real
structure rather than by reasoning.

**`contacts` lost its `whole_residues` flag.** The proposal made it the typed
form of where `byres` goes in a string. In practice it was a no-op: the tool
returns residues, so collapsing atoms to residues erases the distinction and
both settings returned the same 30. The real difference is *which atom* you are
asking about, so `Selector` gained `atom_names` instead. On the test complex,
RNA chain C within 4 A of DNA is 30 residues unrestricted and 4 restricted to
`C1'` -- the two readings of `byres A and name C1'`, now distinguished by the
selection rather than by operator placement.

**Effects became two tools, not five.** `apply(command, selection, value)` and
`select(name, selection)` rather than typed wrappers per command. The typed
value is the same `Selector` argument every time, so per-command tools would
have added surface without expressiveness.

Also worth recording, because both cost time:

- `execute_structured_command` stringified every handler return, so structured
  data arrived as a Python repr for the server to parse -- exactly the string
  round-trip this design exists to remove. Structured results now travel in a
  `data` field.
- `cmd.iterate` echoes the value of each evaluated expression. A handler using
  `dict.setdefault(...)` printed once per atom and buried its own result under
  tens of thousands of lines. The expression must be an assignment.

The testing note above proved accurate: `conftest` stubbing FastMCP means
`@mcp.tool()` replaces a function with a `MagicMock`, so any test calling a
decorated tool exercises nothing. Implementations are private, with thin
decorated wrappers, following the existing `_render_png`/`render_png` split.

## Layers, and the bare-word trap

A second round of tools, added on the principle above: the gap gets filled as a
tool rather than as another paragraph. Five of them --- `inspect_setting`,
`unset_setting`, `get_representations`, `get_history`, `save_file` --- together
removing about 90 lines from the skill.

Everything below was found by probing a real PyMOL, not by reading the API docs,
and three of the four findings contradicted the plan they were meant to confirm.

**Punctuation picks the setting layer.** The design assumed the fix for a sticky
scoped `set` was to expose `unset`. It is not, because `unset` has exactly the
same trap: a bare identifier addresses the *object* layer, anything
parenthesised or compound addresses the *atoms*. With a global of 0.6 and an
override of 0.8 on `ala and name CA`:

```
unset via 'ala'               0.80 -> 0.80   silently does nothing
unset via 'all'               0.80 -> 0.80   silently does nothing
unset via '(ala)'             0.80 -> 0.60   cleared
unset via '(all)'             0.80 -> 0.60   cleared
unset via 'ala and name CA'   0.80 -> 0.60   cleared
```

So the skill's clearing table was also incomplete in a costly direction: it told
the reader `all` fails and not to use it as a blanket reset, when `(all)` is a
working blanket reset.

This is a hazard for the facade in general. `Selector.to_selection()` returns a
lone clause unwrapped, so `Selector(object="bac")` renders as the bare word
`bac` --- correct everywhere else, and exactly wrong here. `unset_setting`
therefore sends an explicit `scope` and lets the plugin apply it, rather than
letting the shape of a selection decide which layer gets written. Any future
layered command needs the same treatment.

**`unset` is not `set ..., 0`.** Overwriting pins the atoms at zero; clearing
removes the entry so they inherit the layer beneath. Invisible while the global
is 0, which is why the difference was never noticed and the workaround looked
adequate.

**`cmd.get` returns formatted strings.** `get_setting` has always returned
`'0.60000'`, not `0.6` --- its annotation is `Any` and nothing caught it.
`cmd.get_setting_tuple` is the typed reader, and it takes an object name for the
object layer. The atom layer is only reachable through `cmd.iterate`, via the
`s.` namespace. `inspect_setting` passes the setting name through `space=` and
reads it with `getattr` rather than interpolating it into the expression, which
is what keeps it outside `check_atom_expression`'s remit --- interpolation would
have been a real injection hole in a dispatcher that is otherwise exec-free.

**`save` was writing empty session files.** The handler passed a bare `"all"` to
`cmd.save`, whose own default is `'(all)'`. Same bare-word trap: PyMOL read it
as an object name, no object is called `all`, and a `.pse` came out as a ~1 kB
settings-only file --- saved, and reported as a success.

```
cmd.save(p, "all",   -1)  ->   1011 bytes, no objects
cmd.save(p, "(all)", -1)  ->  10506 bytes, both objects
```

Coordinate formats are unaffected --- a `.pdb` is byte-identical either way ---
which is why it only ever bit session files, and why the skill had recorded the
symptom ("the MCP save wrapper has produced small settings-only `.pse` files in
practice while still reporting success") without ever finding the cause. The
bug was found by `save_file`'s `objects_verified` check failing in a test, which
is the argument for returning facts instead of "executed successfully" in one
line.

**What did not need a change.** The 10 s `COMMAND_TIMEOUT` was a non-issue:
`cmd.iterate` over 28,000 atoms takes 6--15 ms, so a 500k-atom structure
extrapolates to ~0.3 s. The cost that does need managing is payload size, which
both new iterate-based tools handle by aggregating --- `get_representations` on
`(object, chain, mask)` triples, `inspect_setting` on distinct values --- so the
result is bounded by the number of groups rather than by the size of the
structure.

**On `Annotated[CallToolResult, Model]`.** `save_file` returns a plain model.
The `CallToolResult` indirection exists only because image bytes cannot go in
`structuredContent`; a saved file has no bytes to return, and copying the shape
without the reason would be cargo cult.

### Testing: proving a typed path equals the string path

`test_png_export.py` had already established the archetype without naming it:
drive both argument shapes into the same handler and assert the recorded
downstream `cmd.*` call is identical. That is now the house pattern for this
kind of change, at `tests/test_settings.py::TestUnsetScopeEquivalence`.

It only reaches so far, though. A stub `cmd` cannot reproduce setting layers,
representation bits, or what a `.pse` actually contains, and those are precisely
the claims these tools rest on. The equivalence classes in
`tests/test_integration.py` run against a real headless PyMOL: the clearing
table is executed rather than asserted in prose, `unset` is shown to differ from
`set ..., 0`, the hardcoded rep-bit table is checked against
`pymol.viewing.repres`, `get_history` is diffed against the file it reads, and
`save_file`'s object count is compared with what a fresh PyMOL process finds
when it reopens the file --- the verification ritual the skill used to ask for
by hand, now run once in CI.
