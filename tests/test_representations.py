"""Decoding the per-atom representation bitmask.

The skill used to say you could not query what was shown before a `hide`, so
restoring a component meant choosing a representation rather than recovering
one. The `reps` field in a cmd.iterate namespace has carried it the whole time.

The bit table is copied from pymol.viewing.repres rather than imported, so these
run without PyMOL; TestRepresentationBits in the integration suite asserts the
copy still matches. Which bits can appear per atom was established by showing
each representation alone on a fragment and reading the mask back -- the nine
object-level ones always read 0 there.
"""

import pytest
from conftest import load_plugin


@pytest.fixture(scope="module")
def plugin():
    return load_plugin("plugin_representations")


def dispatch_with(plugin, atoms):
    """atoms: list of (model, chain, reps-bitmask)."""

    class FakeCmd:
        def iterate(self, selection, expression, space=None):
            for model, chain, reps in atoms:
                eval(
                    compile(expression, "<t>", "exec"),
                    {**space, "model": model, "chain": chain, "reps": reps},
                )

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    return plugin.build_command_dispatcher(FakeCmd())["get_representations"]


class TestBitDecoding:
    def test_the_observed_fragment_mask_decodes(self, plugin):
        """49 is what a `fragment` plus `show cartoon` actually reads back."""
        out = dispatch_with(plugin, [("ala", "A", 49)])({})
        assert out["reps"] == ["cartoon", "nb_spheres", "sticks"]

    @pytest.mark.parametrize(
        "mask,expected",
        [
            (0, []),
            (1, ["sticks"]),
            (32, ["cartoon"]),
            (2048, ["nonbonded"]),
            (524288, ["ellipsoids"]),
            (1 | 32, ["cartoon", "sticks"]),
        ],
    )
    def test_masks_decode_to_names(self, plugin, mask, expected):
        out = dispatch_with(plugin, [("obj", "A", mask)])({})
        assert out["reps"] == expected

    def test_object_level_bits_are_ignored_not_reported(self, plugin):
        """cell, volume and friends never appear per atom.

        Reporting them as present because a bit happened to be set would be
        inventing information; reporting them absent would be a claim the data
        cannot support. They are excluded, and the note says so.
        """
        out = dispatch_with(plugin, [("obj", "A", 4096 | 1048576)])({})
        assert out["reps"] == []
        assert "cell" in out["note"] and "volume" in out["note"]


class TestGrouping:
    def test_groups_are_per_object_and_chain(self, plugin):
        out = dispatch_with(
            plugin,
            [("a", "A", 32), ("a", "A", 32), ("a", "B", 1), ("b", "A", 32)],
        )({})
        assert [(g["object"], g["chain"], g["atoms"]) for g in out["groups"]] == [
            ("a", "A", 2),
            ("a", "B", 1),
            ("b", "A", 1),
        ]

    def test_partial_marks_a_rep_covering_only_some_of_a_group(self, plugin):
        """The field that earns the tool.

        Cartoon on 1 of 4 atoms and cartoon on 4 of 4 render identically at a
        glance and mean quite different things.
        """
        out = dispatch_with(
            plugin,
            [("a", "A", 32), ("a", "A", 0), ("a", "A", 0), ("a", "A", 0)],
        )({})
        group = out["groups"][0]
        assert group["partial"] is True
        assert group["per_rep"] == [{"rep": "cartoon", "atoms": 1}]

    def test_a_rep_on_every_atom_is_not_partial(self, plugin):
        out = dispatch_with(plugin, [("a", "A", 32), ("a", "A", 32)])({})
        assert out["groups"][0]["partial"] is False

    def test_hidden_is_set_when_atoms_exist_but_nothing_is_shown(self, plugin):
        out = dispatch_with(plugin, [("a", "A", 0), ("a", "A", 0)])({})
        assert out["hidden"] is True
        assert out["atoms"] == 2

    def test_an_empty_selection_is_not_reported_as_hidden(self, plugin):
        """Nothing selected and nothing shown are different answers."""
        out = dispatch_with(plugin, [])({})
        assert out["hidden"] is False
        assert out["atoms"] == 0

    def test_a_newline_in_the_selection_is_refused(self, plugin):
        with pytest.raises(ValueError, match="newlines"):
            dispatch_with(plugin, [])({"selection": "a\nrun evil.pml"})
