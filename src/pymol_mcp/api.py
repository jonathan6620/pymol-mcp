"""Typed models for the structured tool surface.

`models.py` describes the wire protocol and the regex command table. This module
describes the *typed* API: selections expressed as fields rather than as PyMOL
selection-algebra strings, and structured results.

The point is to make silent selection mistakes impossible to express. PyMOL's
selection language fails quietly — a malformed selection is usually valid, just
not the one intended — so the traps it contains have to be either documented or
designed out. See docs/typed-facade-design.md.
"""

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# Standard PDB deoxyribonucleotide residue names. Everything else that is
# nucleic is treated as RNA, which puts modified and non-standard residues on
# the RNA side -- the same convention the untyped path documents.
DNA_RESN = "DA+DC+DG+DT+DI"


def escape_resi(value: int) -> str:
    r"""Render one residue number for a `resi` selector.

    `resi` reads `-` as a range operator, so a negative number is silently
    parsed as an open-ended range: `resi -12` means "everything up to 12". The
    minus has to be escaped. This is the single most common silent failure in
    the untyped path, and every range endpoint needs its own escape --
    `\-12-\-8`, not `\-12--8`.
    """
    return rf"\{value}" if value < 0 else str(value)


class Molecule(str, Enum):
    """A class of molecule, resolved to a selector centrally.

    Splitting RNA from DNA has no dedicated PyMOL selector, so it goes by
    residue name. Getting that idiom right once here replaces explaining it.
    """

    PROTEIN = "protein"
    NUCLEIC = "nucleic"
    RNA = "rna"
    DNA = "dna"
    SOLVENT = "solvent"
    IONS = "ions"

    def to_selection(self) -> str:
        return {
            Molecule.PROTEIN: "polymer.protein",
            Molecule.NUCLEIC: "polymer.nucleic",
            Molecule.RNA: f"polymer.nucleic and not resn {DNA_RESN}",
            Molecule.DNA: f"polymer.nucleic and resn {DNA_RESN}",
            Molecule.SOLVENT: "solvent",
            Molecule.IONS: "inorganic",
        }[self]


class ResidueRange(BaseModel):
    """An inclusive residue range, with escaping handled for you."""

    start: int
    end: int

    @model_validator(mode="after")
    def ordered(self) -> "ResidueRange":
        if self.end < self.start:
            raise ValueError(f"end ({self.end}) is before start ({self.start})")
        return self

    def to_selection(self) -> str:
        if self.start == self.end:
            return f"resi {escape_resi(self.start)}"
        return f"resi {escape_resi(self.start)}-{escape_resi(self.end)}"


class Selector(BaseModel):
    """A selection expressed as fields rather than as a selection string.

    Fields combine with `and`. `raw` is an escape hatch for the cases PyMOL's
    algebra expresses and this model does not; when set, it is used verbatim and
    every other field is ignored.
    """

    object: str | None = None
    chain: str | None = None
    residues: list[int] | None = None
    residue_range: ResidueRange | None = None
    molecule: Molecule | None = None
    atom_names: list[str] | None = Field(
        default=None,
        description=(
            "Restrict to named atoms, e.g. [\"CA\"] or [\"C1'\"]. This is what "
            "distinguishes 'residues whose backbone atom is in range' from "
            "'residues with any atom in range' -- a difference that is easy to "
            "get wrong when writing selections by hand."
        ),
    )
    raw: str | None = Field(
        default=None,
        description=(
            "Literal PyMOL selection, used verbatim in place of the other "
            "fields. For selections this model cannot express."
        ),
    )

    @model_validator(mode="after")
    def not_empty(self) -> "Selector":
        if self.raw is not None:
            if not self.raw.strip():
                raise ValueError("raw selection must not be blank")
            return self
        if not any(
            (
                self.object,
                self.chain,
                self.residues,
                self.residue_range,
                self.molecule,
                self.atom_names,
            )
        ):
            raise ValueError(
                "empty selector: set at least one field, or use raw='all'"
            )
        if self.residues and self.residue_range:
            raise ValueError("set residues or residue_range, not both")
        return self

    def to_selection(self) -> str:
        """Render to PyMOL selection syntax."""
        if self.raw is not None:
            return self.raw
        parts: list[str] = []
        if self.object:
            parts.append(self.object)
        if self.chain:
            parts.append(f"chain {self.chain}")
        if self.residues:
            parts.append("resi " + "+".join(escape_resi(r) for r in self.residues))
        if self.residue_range:
            parts.append(self.residue_range.to_selection())
        if self.molecule:
            parts.append(self.molecule.to_selection())
        if self.atom_names:
            parts.append("name " + "+".join(self.atom_names))
        # Parenthesise multi-token clauses so the result composes safely once
        # more than one is joined. A lone clause needs no wrapping, and adding
        # it only makes the selection harder to read back.
        if len(parts) == 1:
            return parts[0]
        return " and ".join(f"({p})" if " " in p else p for p in parts)


##############################################################################
# RESULT MODELS
##############################################################################


class Residue(BaseModel):
    chain: str
    resi: int
    resn: str


class ChainInfo(BaseModel):
    chain: str
    kind: Literal["protein", "rna", "dna", "ligand", "solvent", "mixed", "empty"]
    atoms: int
    residues: int
    first: int | None = None
    last: int | None = None
    gaps: list[tuple[int, int]] = Field(default_factory=list)


class Chains(BaseModel):
    object: str
    chains: list[ChainInfo]


class Counts(BaseModel):
    selection: str
    atoms: int
    residues: int
    chains: int


class ResidueList(BaseModel):
    selection: str
    residues: list[Residue]
    truncated: bool = False


class Gaps(BaseModel):
    object: str
    chain: str
    first: int | None = None
    last: int | None = None
    modelled: int = 0
    gaps: list[tuple[int, int]] = Field(default_factory=list)


class SecondaryStructureRun(BaseModel):
    chain: str
    ss: Literal["H", "S", "L"]
    start: int
    end: int
    length: int


class ResidueSS(BaseModel):
    chain: str
    resi: int
    ss: Literal["H", "S", "L"]


class SecondaryStructure(BaseModel):
    selection: str
    residues: list[ResidueSS]
    runs: list[SecondaryStructureRun]
    helix: int
    sheet: int
    loop: int
    pattern: str = Field(
        description="Run-length summary, e.g. '22H3L15H' for helix-turn-helix."
    )


class ChainSequence(BaseModel):
    chain: str
    first: int
    last: int
    seq: str


class Sequence(BaseModel):
    selection: str
    chains: list[ChainSequence]


class Measurement(BaseModel):
    selection1: str
    selection2: str
    distance: float


class ClearedSelections(BaseModel):
    deleted: list[str]
    count: int


class SettingGroup(BaseModel):
    """One distinct value of a setting, and the atoms carrying it."""

    value: Any = None
    atoms: int
    objects: list[str]


class ObjectSetting(BaseModel):
    object: str
    value: Any = None


class SettingReport(BaseModel):
    """A setting read at all three layers it can live on.

    `display` is what the plain `get_setting` command returns -- the global
    layer, formatted as a string. `global_value`, `object_values` and `values`
    are the three layers proper, innermost last: an atom-level value wins over
    an object-level one, which wins over the global.

    `overridden` is the field to check. It means at least one atom in the
    selection carries a value that differs from the layer it would otherwise
    inherit, which is the condition that makes a render look wrong while every
    global reads clean.
    """

    name: str
    selection: str
    atoms: int
    display: str
    global_value: Any = None
    object_values: list[ObjectSetting] = Field(default_factory=list)
    values: list[SettingGroup] = Field(default_factory=list)
    uniform: bool
    overridden: bool
    truncated: bool = False
    note: str | None = None


class RepCount(BaseModel):
    rep: str
    atoms: int


class RepGroup(BaseModel):
    """What is shown for one object's chain.

    `partial` means some but not all of the group carries some representation.
    In a render that is indistinguishable from the whole group carrying it,
    and it is usually the thing you actually wanted to know.
    """

    object: str
    chain: str
    atoms: int
    reps: list[str]
    per_rep: list[RepCount]
    partial: bool


class Representations(BaseModel):
    """Current representation state, aggregated by object and chain."""

    selection: str
    atoms: int
    reps: list[str]
    groups: list[RepGroup]
    hidden: bool
    note: str | None = None


class HistoryFile(BaseModel):
    """A path a command read or wrote, recorded absolute."""

    path: str
    direction: Literal["in", "out"]


class HistoryEntry(BaseModel):
    """One history record.

    Almost every field is optional because two different writers produce these.
    Commands carry `command`/`args`/`source`; listener events carry
    `event`/`detail` and no command at all. A model that insisted on `command`
    would fail to parse exactly the records written when something went wrong.
    """

    ts: str
    ok: bool = True
    command: str | None = None
    event: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None
    output: str | None = None
    error: str | None = None
    detail: str | None = None
    replayable: bool = True
    file: HistoryFile | None = None


class History(BaseModel):
    """Recent history, newest last.

    `script` is the replay .pml for this PyMOL session, or the most recent one
    in the directory if this session has not written any commands yet.
    """

    enabled: bool
    directory: str | None = None
    script: str | None = None
    entries: list[HistoryEntry]
    total: int
    truncated: bool = False


class SaveMeta(BaseModel):
    """What a save actually wrote.

    A plain model rather than the Annotated[CallToolResult, ...] shape the
    renders use: that indirection exists only because image bytes cannot go in
    structuredContent, and a saved file has no bytes to return.

    `objects_verified` lists the objects whose names were found in a .pse's
    bytes. Necessary, not sufficient -- but a settings-only session file
    reports success identically to a complete one, and this distinguishes them.
    """

    path: str
    bytes: int
    format: str
    selection: str
    objects: list[str]
    object_count: int
    atoms: int
    states: int
    objects_verified: list[str] = Field(default_factory=list)


class RenderMeta(BaseModel):
    """Metadata for a rendered still. The image itself travels as ImageContent."""

    path: str
    width: int
    height: int
    dpi: float
    ray: bool


class MovieMeta(BaseModel):
    """Metadata for a rendered movie. The GIF travels as ImageContent."""

    path: str
    mode: Literal["spin", "states"]
    frames: int
    fps: int
    width: int
    height: int
    bytes: int
    ray: bool
    truncated: bool = False
    dropped_frames: int = 0
    note: str | None = None
