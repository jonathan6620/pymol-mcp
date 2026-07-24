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
from typing import Literal

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
