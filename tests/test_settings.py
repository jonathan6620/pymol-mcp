"""Scoped setting reads and clears.

The point of these tools is that PyMOL picks a setting's layer from the
punctuation of a selection: a bare identifier writes the object layer, anything
parenthesised or compound writes the atoms, and clearing the wrong one reports
success while changing nothing. Measured against a real PyMOL, with a global of
0.6 and an override of 0.8 on `ala and name CA`:

    unset via 'ala'               0.80 -> 0.80   silently does nothing
    unset via 'all'               0.80 -> 0.80   silently does nothing
    unset via '(ala)'             0.80 -> 0.60   cleared
    unset via '(all)'             0.80 -> 0.60   cleared
    unset via 'ala and name CA'   0.80 -> 0.60   cleared

The equivalence claim tested here is that the typed path with scope="atom"
produces the same downstream cmd.unset call as the string path written with the
parentheses by hand. TestSettingLayers in the integration suite proves the same
thing against a live PyMOL rather than a stub.
"""

import pytest
from conftest import load_plugin


@pytest.fixture(scope="module")
def plugin():
    return load_plugin("plugin_settings")


class RecordingCmd:
    """Records the cmd.* calls a handler makes, and serves fixed atom data."""

    def __init__(self, atoms=None, global_value=0.6, object_value=None):
        # (model, setting value) per atom
        self.atoms = atoms if atoms is not None else [("ala", 0.0)]
        self.global_value = global_value
        self.object_value = object_value
        self.unset_calls = []
        self.expressions = []

    def unset(self, *args):
        self.unset_calls.append(args)
        return 1

    def iterate(self, selection, expression, space=None):
        self.expressions.append(expression)
        for model, value in self.atoms:

            class Settings:
                pass

            settings = Settings()
            setattr(settings, space["_name"], value)
            eval(
                compile(expression, "<t>", "exec"),
                {**space, "model": model, "s": settings},
            )

    def get_setting_tuple(self, name, obj=None):
        if obj is None:
            return (3, (self.global_value,))
        if self.object_value is None:
            return (3, (self.global_value,))
        return (3, (self.object_value,))

    def get(self, name):
        # PyMOL formats settings as strings; that is the whole reason
        # inspect_setting exists alongside get_setting.
        if isinstance(self.global_value, tuple):
            return str(self.global_value)
        return "%.5f" % self.global_value

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def dispatch_for(plugin, cmd):
    return plugin.build_command_dispatcher(cmd)


class TestUnsetScopeEquivalence:
    def test_typed_atom_scope_equals_the_hand_parenthesised_string(self, plugin):
        """The equivalence claim, at the layer where it can be asserted exactly.

        A caller who knows the trap writes `(ala)` themselves. A caller using
        the typed tool passes scope="atom" and the selection `ala`. Both must
        reach cmd.unset identically -- otherwise the typed path is a different
        operation wearing the same name.
        """
        typed = RecordingCmd()
        dispatch_for(plugin, typed)["unset"](
            {"setting": "cartoon_transparency", "selection": "ala", "scope": "atom"}
        )

        by_hand = RecordingCmd()
        dispatch_for(plugin, by_hand)["unset"](
            {"setting": "cartoon_transparency", "selection": "(ala)"}
        )

        assert typed.unset_calls == by_hand.unset_calls
        assert typed.unset_calls == [("cartoon_transparency", "(ala)")]

    def test_object_scope_sends_the_bare_name(self, plugin):
        """The two scopes must stay distinguishable.

        If `object` also parenthesised, the enum would be decorative and the
        caller could never reach the object layer deliberately.
        """
        cmd = RecordingCmd()
        dispatch_for(plugin, cmd)["unset"](
            {"setting": "cartoon_transparency", "selection": "ala", "scope": "object"}
        )
        assert cmd.unset_calls == [("cartoon_transparency", "ala")]

    def test_absent_scope_passes_the_selection_through_verbatim(self, plugin):
        """The string path has to stay a faithful mirror of PyMOL syntax.

        `parse_and_execute("unset x, ala")` carries no scope, and must behave
        exactly as typing it into PyMOL would -- including addressing the
        object layer, which is what a bare name does there.
        """
        cmd = RecordingCmd()
        dispatch_for(plugin, cmd)["unset"](
            {"setting": "cartoon_transparency", "selection": "ala"}
        )
        assert cmd.unset_calls == [("cartoon_transparency", "ala")]

    def test_global_scope_sends_no_selection(self, plugin):
        cmd = RecordingCmd()
        dispatch_for(plugin, cmd)["unset"](
            {"setting": "cartoon_transparency", "scope": "global"}
        )
        assert cmd.unset_calls == [("cartoon_transparency",)]

    def test_no_selection_and_no_scope_clears_the_global(self, plugin):
        cmd = RecordingCmd()
        dispatch_for(plugin, cmd)["unset"]({"setting": "cartoon_transparency"})
        assert cmd.unset_calls == [("cartoon_transparency",)]

    def test_scope_without_a_selection_is_an_error(self, plugin):
        cmd = RecordingCmd()
        with pytest.raises(ValueError, match="needs a selection"):
            dispatch_for(plugin, cmd)["unset"](
                {"setting": "cartoon_transparency", "scope": "atom"}
            )
        assert cmd.unset_calls == []


class TestSettingNameIsNeverInterpolated:
    def test_the_name_travels_in_space_not_in_the_expression(self, plugin):
        """The one genuinely new attack surface, asserted directly.

        Building the expression as "s.%s" % name would put a caller-supplied
        string into code PyMOL evaluates per atom. Passing it through `space`
        and reading it with getattr keeps the expression constant, which is
        what keeps this handler outside check_atom_expression's remit.
        """
        cmd = RecordingCmd()
        dispatch_for(plugin, cmd)["inspect_setting"](
            {"name": "cartoon_transparency", "selection": "ala"}
        )
        assert cmd.expressions == ["rows.append((model, getattr(s, _name)))"]
        assert "cartoon_transparency" not in cmd.expressions[0]

    @pytest.mark.parametrize(
        "name", ["ambient\nrun evil.pml", "1ambient", "", "a-b", "s.__class__"]
    )
    def test_bad_names_are_refused(self, plugin, name):
        cmd = RecordingCmd()
        dispatch = dispatch_for(plugin, cmd)
        with pytest.raises(ValueError, match="invalid setting name"):
            dispatch["inspect_setting"]({"name": name})
        with pytest.raises(ValueError, match="invalid setting name"):
            dispatch["unset"]({"setting": name, "selection": "ala"})

    def test_a_newline_in_the_selection_is_refused(self, plugin):
        cmd = RecordingCmd()
        with pytest.raises(ValueError, match="newlines"):
            dispatch_for(plugin, cmd)["unset"](
                {"setting": "ambient", "selection": "ala\nrun evil.pml"}
            )
        assert cmd.unset_calls == []


class TestInspectSetting:
    def test_a_uniform_setting_reports_one_group(self, plugin):
        cmd = RecordingCmd(atoms=[("ala", 0.6)] * 4, global_value=0.6)
        out = dispatch_for(plugin, cmd)["inspect_setting"]({"name": "x"})
        assert out["uniform"] is True
        assert out["overridden"] is False
        assert out["values"] == [{"value": 0.6, "atoms": 4, "objects": ["ala"]}]

    def test_a_partial_override_reports_two_groups_not_an_average(self, plugin):
        """Averaging would hide the thing worth knowing.

        One atom at 0.8 among three at 0.6 is a scoped `set` that outlived its
        figure. A mean of 0.65 describes nothing that exists.
        """
        cmd = RecordingCmd(
            atoms=[("ala", 0.6), ("ala", 0.8), ("ala", 0.6), ("ala", 0.6)],
            global_value=0.6,
        )
        out = dispatch_for(plugin, cmd)["inspect_setting"]({"name": "x"})
        assert out["uniform"] is False
        assert out["overridden"] is True
        assert [g["value"] for g in out["values"]] == [0.6, 0.8]
        assert [g["atoms"] for g in out["values"]] == [3, 1]

    def test_display_is_the_string_get_setting_would_have_returned(self, plugin):
        """cmd.get formats settings, so `get_setting` has always returned text.

        Keeping it as `display` means the typed tool is a superset rather than
        a different answer to the same question.
        """
        cmd = RecordingCmd(atoms=[("ala", 0.6)], global_value=0.6)
        out = dispatch_for(plugin, cmd)["inspect_setting"]({"name": "x"})
        assert out["display"] == "0.60000"
        assert out["global_value"] == 0.6

    def test_tuple_values_are_returned_as_lists(self, plugin):
        """bg_rgb and friends are tuples, which are not JSON."""
        cmd = RecordingCmd(
            atoms=[("ala", (0.0, 0.0, 0.0))], global_value=(0.0, 0.0, 0.0)
        )
        out = dispatch_for(plugin, cmd)["inspect_setting"]({"name": "bg_rgb"})
        assert out["values"][0]["value"] == [0.0, 0.0, 0.0]
        assert out["global_value"] == [0.0, 0.0, 0.0]

    def test_many_distinct_values_are_capped_and_flagged(self, plugin):
        cmd = RecordingCmd(atoms=[("ala", i / 100.0) for i in range(30)])
        out = dispatch_for(plugin, cmd)["inspect_setting"]({"name": "x"})
        assert out["truncated"] is True
        assert len(out["values"]) == 20


class TestFloatPrecisionDoesNotFakeAnOverride:
    """The two readers disagree about float width, and it matters.

    A per-atom read through `s.` widens the stored C float to a double, so a
    global of 0.6 comes back from the atoms as 0.6000000238418579 while
    get_setting_tuple returns 0.6. Compared raw, every setting in every scene
    would report `overridden: True` -- and `overridden` is the one field a
    caller is expected to act on.
    """

    def test_a_widened_float_is_not_treated_as_an_override(self, plugin):
        cmd = RecordingCmd(
            atoms=[("ala", 0.6000000238418579)] * 3, global_value=0.6
        )
        out = dispatch_for(plugin, cmd)["inspect_setting"]({"name": "x"})
        assert out["overridden"] is False
        assert out["values"][0]["value"] == 0.6

    def test_a_real_difference_is_still_an_override(self, plugin):
        cmd = RecordingCmd(
            atoms=[("ala", 0.800000011920929)] * 3, global_value=0.6
        )
        out = dispatch_for(plugin, cmd)["inspect_setting"]({"name": "x"})
        assert out["overridden"] is True
        assert out["values"][0]["value"] == 0.8

    def test_values_that_differ_only_in_float_width_form_one_group(self, plugin):
        """Otherwise one setting reports as two groups with the same number."""
        cmd = RecordingCmd(
            atoms=[("ala", 0.6), ("ala", 0.6000000238418579)], global_value=0.6
        )
        out = dispatch_for(plugin, cmd)["inspect_setting"]({"name": "x"})
        assert out["values"] == [{"value": 0.6, "atoms": 2, "objects": ["ala"]}]
        assert out["uniform"] is True

    def test_small_values_are_not_flattened_to_zero(self, plugin):
        """Six significant figures, not six decimal places."""
        cmd = RecordingCmd(atoms=[("ala", 1e-8)], global_value=1e-8)
        out = dispatch_for(plugin, cmd)["inspect_setting"]({"name": "x"})
        assert out["values"][0]["value"] == 1e-8
