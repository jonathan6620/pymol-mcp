"""Typed API model tests.

These are pure functions, so they run fine under the conftest FastMCP stub.
Every expected selection string here was checked against a real structure
(a protein/RNA/DNA complex with negative DNA numbering) before being written
down -- see docs/typed-facade-design.md.
"""

import pytest

from pymol_mcp.api import (
    Counts,
    Molecule,
    ResidueRange,
    Selector,
    escape_resi,
)


class TestEscaping:
    """`resi` reads `-` as a range operator, so negatives must be escaped."""

    def test_positive_unchanged(self):
        assert escape_resi(12) == "12"

    def test_negative_escaped(self):
        assert escape_resi(-12) == r"\-12"

    def test_zero(self):
        assert escape_resi(0) == "0"


class TestResidueRange:
    def test_positive_range(self):
        assert ResidueRange(start=250, end=292).to_selection() == "resi 250-292"

    def test_both_endpoints_negative_are_both_escaped(self):
        """The trap: escaping only the low end silently widens the selection.

        `resi \\-12--8` selects about 20 nucleotides because the second minus is
        read as the range operator again. `resi \\-12-\\-8` selects 5.
        """
        assert ResidueRange(start=-12, end=-8).to_selection() == r"resi \-12-\-8"

    def test_negative_to_positive(self):
        assert ResidueRange(start=-5, end=1).to_selection() == r"resi \-5-1"

    def test_single_residue_is_not_a_range(self):
        assert ResidueRange(start=951, end=951).to_selection() == "resi 951"

    def test_reversed_range_rejected(self):
        with pytest.raises(ValueError, match="before start"):
            ResidueRange(start=10, end=1)


class TestMolecule:
    def test_rna_excludes_deoxy_residues(self):
        assert Molecule.RNA.to_selection() == (
            "polymer.nucleic and not resn DA+DC+DG+DT+DI"
        )

    def test_dna_selects_deoxy_residues(self):
        assert Molecule.DNA.to_selection() == (
            "polymer.nucleic and resn DA+DC+DG+DT+DI"
        )

    def test_protein(self):
        assert Molecule.PROTEIN.to_selection() == "polymer.protein"

    def test_every_member_renders(self):
        for member in Molecule:
            assert member.to_selection()


class TestSelector:
    def test_chain_and_range(self):
        sel = Selector(
            chain="E", residue_range=ResidueRange(start=-12, end=-8)
        ).to_selection()
        assert sel == r"(chain E) and (resi \-12-\-8)"

    def test_object_and_molecule(self):
        sel = Selector(object="bac", molecule=Molecule.RNA).to_selection()
        assert sel == (
            "bac and (polymer.nucleic and not resn DA+DC+DG+DT+DI)"
        )

    def test_residue_list_escapes_each_member(self):
        sel = Selector(chain="E", residues=[-12, -8, 1]).to_selection()
        assert sel == r"(chain E) and (resi \-12+\-8+1)"

    def test_raw_passthrough_is_verbatim(self):
        assert Selector(raw="byres (chain C within 4 of chain E)").to_selection() == (
            "byres (chain C within 4 of chain E)"
        )

    def test_raw_wins_over_other_fields(self):
        assert Selector(chain="A", raw="all").to_selection() == "all"

    def test_empty_selector_rejected(self):
        with pytest.raises(ValueError, match="empty selector"):
            Selector()

    def test_blank_raw_rejected(self):
        with pytest.raises(ValueError, match="must not be blank"):
            Selector(raw="   ")

    def test_residues_and_range_together_rejected(self):
        with pytest.raises(ValueError, match="not both"):
            Selector(
                chain="A", residues=[1], residue_range=ResidueRange(start=1, end=2)
            )


class TestResultModels:
    def test_counts_round_trip(self):
        c = Counts(selection="polymer.protein", atoms=4900, residues=602, chains=1)
        assert c.model_dump(mode="json")["atoms"] == 4900
