# Design: a typed facade over the command table

Status: proposal, no code written.

## The problem

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
needs `structured_output=False` because `Image` is not serialisable. Every tool
that returns *data* rather than an image wants it on.

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

**Explicitly out of scope:** `alter` and `alter_state` keep their current
AST-validated expression path. Modelling arbitrary per-atom arithmetic is a
project of its own and the existing validator is the security boundary.

## Migration path

The 83-command table does not go away, and `parse_and_execute` keeps working
unchanged. That matters: it is the path a user takes when they already know the
PyMOL syntax, and the skill's whole "translate the request into literal PyMOL"
contract depends on it.

Four stages, each shippable alone:

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
