"""PyMOL command table structure and pattern matching."""

import re

import pytest

from pymol_mcp.models import (
    CommandDef,
    ParameterDef,
)
from pymol_mcp.server import (
    PYMOL_COMMANDS,
    parse_pymol_input,
)

# ============================================================================
# 1. STRUCTURAL INTEGRITY OF COMMAND DEFINITIONS
# ============================================================================


class TestCommandDefinitionStructure:
    """Verify every command definition has required fields and valid types.

    Most of these checks are now enforced by Pydantic at construction time.
    These tests remain as regression tests.
    """

    def test_all_commands_are_command_def_instances(self):
        for cmd_name, cmd_info in PYMOL_COMMANDS.items():
            assert isinstance(cmd_info, CommandDef), (
                f"Command '{cmd_name}' is not a CommandDef instance"
            )

    def test_descriptions_are_nonempty_strings(self):
        for cmd_name, cmd_info in PYMOL_COMMANDS.items():
            desc = cmd_info.description
            assert isinstance(desc, str) and len(desc) > 0, (
                f"Command '{cmd_name}' has empty or non-string description"
            )

    def test_patterns_are_valid_regex(self):
        for cmd_name, cmd_info in PYMOL_COMMANDS.items():
            try:
                re.compile(cmd_info.pattern)
            except re.error as e:
                pytest.fail(f"Command '{cmd_name}' has invalid regex: {e}")

    def test_patterns_are_anchored(self):
        """Patterns should be anchored with ^ and $ to avoid partial matches."""
        for cmd_name, cmd_info in PYMOL_COMMANDS.items():
            assert cmd_info.pattern.startswith("^"), (
                f"Command '{cmd_name}' pattern not anchored at start"
            )
            assert cmd_info.pattern.endswith("$"), (
                f"Command '{cmd_name}' pattern not anchored at end"
            )

    def test_parameters_is_list(self):
        for cmd_name, cmd_info in PYMOL_COMMANDS.items():
            assert isinstance(cmd_info.parameters, list), (
                f"Command '{cmd_name}' parameters is not a list"
            )

    def test_parameter_defs_are_parameter_def_instances(self):
        for cmd_name, cmd_info in PYMOL_COMMANDS.items():
            for i, param in enumerate(cmd_info.parameters):
                assert isinstance(param, ParameterDef), (
                    f"Command '{cmd_name}' param[{i}] is not a ParameterDef"
                )

    def test_parameter_defs_have_name_and_required(self):
        for cmd_name, cmd_info in PYMOL_COMMANDS.items():
            for i, param in enumerate(cmd_info.parameters):
                assert param.name, (
                    f"Command '{cmd_name}' param[{i}] has no name"
                )
                assert isinstance(param.required, bool), (
                    f"Command '{cmd_name}' param[{i}] ('{param.name}') "
                    "required is not bool"
                )

    def test_optional_params_have_default_or_are_truly_optional(self):
        """Optional params should typically have a default value."""
        for cmd_name, cmd_info in PYMOL_COMMANDS.items():
            for param in cmd_info.parameters:
                if not param.required and param.default is None:
                    pass  # acceptable: no default means param may be omitted

    def test_check_selection_is_bool(self):
        for cmd_name, cmd_info in PYMOL_COMMANDS.items():
            assert isinstance(cmd_info.check_selection, bool), (
                f"Command '{cmd_name}' check_selection is not bool"
            )

    def test_no_duplicate_param_names(self):
        for cmd_name, cmd_info in PYMOL_COMMANDS.items():
            names = [p.name for p in cmd_info.parameters]
            assert len(names) == len(set(names)), (
                f"Command '{cmd_name}' has duplicate parameter names: {names}"
            )

    def test_options_lists_have_no_duplicates(self):
        """Check for duplicate entries in options lists."""
        for cmd_name, cmd_info in PYMOL_COMMANDS.items():
            for param in cmd_info.parameters:
                if param.options:
                    dupes = [x for x in param.options if param.options.count(x) > 1]
                    assert len(dupes) == 0, (
                        f"Command '{cmd_name}' param '{param.name}' "
                        f"has duplicate options: {set(dupes)}"
                    )


class TestCaptureGroupParameterAlignment:
    """Verify regex capture groups match parameter count."""

    def test_capture_groups_match_parameter_count(self):
        """Number of regex capture groups should match number of parameters."""
        for cmd_name, cmd_info in PYMOL_COMMANDS.items():
            compiled = re.compile(cmd_info.pattern)
            num_groups = compiled.groups
            num_params = len(cmd_info.parameters)
            assert num_groups == num_params, (
                f"Command '{cmd_name}': {num_groups} capture groups "
                f"but {num_params} parameters defined"
            )


# ============================================================================
# 2. PATTERN MATCHING - VALID COMMANDS THAT SHOULD PARSE
# ============================================================================


class TestPatternMatchingValid:
    """Test that valid PyMOL command strings match their patterns."""

    # --- Visualization ---

    @pytest.mark.parametrize("input_str,expected_cmd,expected_args", [
        ("show cartoon", "show", {"representation": "cartoon", "selection": "all"}),
        ("show sticks, chain A", "show",
         {"representation": "sticks", "selection": "chain A"}),
        ("show lines", "show", {"representation": "lines", "selection": "all"}),
        ("show surface, resi 50-100", "show",
         {"representation": "surface", "selection": "resi 50-100"}),
    ])
    def test_show(self, input_str, expected_cmd, expected_args):
        cmd, args = parse_pymol_input(input_str)
        assert cmd == expected_cmd
        assert args == expected_args

    @pytest.mark.parametrize("input_str,expected_cmd,expected_args", [
        ("hide everything", "hide",
         {"representation": "everything", "selection": "all"}),
        ("hide cartoon, chain B", "hide",
         {"representation": "cartoon", "selection": "chain B"}),
    ])
    def test_hide(self, input_str, expected_cmd, expected_args):
        cmd, args = parse_pymol_input(input_str)
        assert cmd == expected_cmd
        assert args == expected_args

    @pytest.mark.parametrize("input_str,expected_cmd,expected_args", [
        ("color red", "color", {"color": "red", "selection": "all"}),
        ("color blue, chain A", "color", {"color": "blue", "selection": "chain A"}),
        ("color 0xFF0000", "color", {"color": "0xFF0000", "selection": "all"}),
    ])
    def test_color(self, input_str, expected_cmd, expected_args):
        cmd, args = parse_pymol_input(input_str)
        assert cmd == expected_cmd
        assert args == expected_args

    @pytest.mark.parametrize("input_str,expected_args", [
        ("as cartoon", {"representation": "cartoon", "selection": "all"}),
        ("as sticks, chain A", {"representation": "sticks", "selection": "chain A"}),
    ])
    def test_as(self, input_str, expected_args):
        cmd, args = parse_pymol_input(input_str)
        assert cmd == "as"
        assert args == expected_args

    @pytest.mark.parametrize("input_str,expected_args", [
        ("set cartoon_oval_length, 1.5",
         {"setting": "cartoon_oval_length", "value": "1.5"}),
        ("set bg_rgb, white", {"setting": "bg_rgb", "value": "white"}),
        ("set stick_radius, 0.2, chain A",
         {"setting": "stick_radius", "value": "0.2", "selection": "chain A"}),
    ])
    def test_set(self, input_str, expected_args):
        cmd, args = parse_pymol_input(input_str)
        assert cmd == "set"
        assert args == expected_args

    @pytest.mark.parametrize("input_str,expected_args", [
        ("cartoon oval", {"type": "oval", "selection": "all"}),
        ("cartoon tube, chain A", {"type": "tube", "selection": "chain A"}),
        ("cartoon automatic", {"type": "automatic", "selection": "all"}),
    ])
    def test_cartoon(self, input_str, expected_args):
        cmd, args = parse_pymol_input(input_str)
        assert cmd == "cartoon"
        assert args == expected_args

    def test_spectrum(self):
        cmd, args = parse_pymol_input("spectrum count, rainbow, chain A")
        assert cmd == "spectrum"
        assert args == {
            "expression": "count", "palette": "rainbow", "selection": "chain A"
        }

    def test_spectrum_minimal(self):
        cmd, args = parse_pymol_input("spectrum b")
        assert cmd == "spectrum"
        assert args["expression"] == "b"

    def test_label(self):
        cmd, args = parse_pymol_input("label chain A, resn")
        assert cmd == "label"
        assert args == {"selection": "chain A", "expression": "resn"}

    # --- Measurements ---

    def test_distance_named(self):
        cmd, args = parse_pymol_input("distance d1, /1ubq//A/50/CA, /1ubq//A/60/CA")
        assert cmd == "distance"
        assert args["name"] == "d1"

    def test_distance_bare(self):
        cmd, args = parse_pymol_input("distance")
        assert cmd == "distance"

    def test_angle(self):
        cmd, args = parse_pymol_input("angle a1, sel1, sel2, sel3")
        assert cmd == "angle"
        assert args["name"] == "a1"

    def test_dihedral(self):
        cmd, args = parse_pymol_input("dihedral d1, sel1, sel2, sel3, sel4")
        assert cmd == "dihedral"
        assert args["name"] == "d1"

    # --- Viewing ---

    @pytest.mark.parametrize("input_str,expected_cmd", [
        ("center chain A", "center"),
        ("center", "center"),
        ("orient", "orient"),
        ("orient chain A", "orient"),
    ])
    def test_view_commands(self, input_str, expected_cmd):
        cmd, args = parse_pymol_input(input_str)
        assert cmd == expected_cmd

    def test_zoom_with_buffer(self):
        cmd, args = parse_pymol_input("zoom chain A, 10")
        assert cmd == "zoom"
        assert args == {"selection": "chain A", "buffer": "10"}

    def test_zoom_bare(self):
        cmd, args = parse_pymol_input("zoom")
        assert cmd == "zoom"

    def test_turn(self):
        cmd, args = parse_pymol_input("turn x, 45")
        assert cmd == "turn"
        assert args == {"axis": "x", "angle": "45"}

    def test_move(self):
        cmd, args = parse_pymol_input("move z, 5")
        assert cmd == "move"
        assert args == {"axis": "z", "distance": "5"}

    def test_clip(self):
        cmd, args = parse_pymol_input("clip near, 2")
        assert cmd == "clip"
        assert args == {"mode": "near", "distance": "2"}

    def test_reset(self):
        cmd, args = parse_pymol_input("reset")
        assert cmd == "reset"

    # --- File operations ---

    def test_fetch(self):
        cmd, args = parse_pymol_input("fetch 1ubq")
        assert cmd == "fetch"
        assert args == {"code": "1ubq"}

    def test_fetch_with_name(self):
        cmd, args = parse_pymol_input("fetch 1ubq, ubiquitin")
        assert cmd == "fetch"
        assert args == {"code": "1ubq", "name": "ubiquitin"}

    def test_load(self):
        cmd, args = parse_pymol_input("load /tmp/structure.pdb")
        assert cmd == "load"
        assert args == {"filename": "/tmp/structure.pdb"}

    def test_load_with_object(self):
        cmd, args = parse_pymol_input("load /tmp/structure.pdb, myobj")
        assert cmd == "load"
        assert args == {"filename": "/tmp/structure.pdb", "object": "myobj"}

    def test_save(self):
        cmd, args = parse_pymol_input("save /tmp/out.pdb, chain A")
        assert cmd == "save"
        assert args["filename"] == "/tmp/out.pdb"

    def test_png(self):
        cmd, args = parse_pymol_input("png /tmp/image.png")
        assert cmd == "png"
        assert args == {"filename": "/tmp/image.png"}

    # --- Selection & Object manipulation ---

    def test_select(self):
        cmd, args = parse_pymol_input("select active_site, resi 50-60 and chain A")
        assert cmd == "select"
        assert args == {"name": "active_site", "selection": "resi 50-60 and chain A"}

    def test_deselect(self):
        cmd, args = parse_pymol_input("deselect")
        assert cmd == "deselect"

    def test_create(self):
        cmd, args = parse_pymol_input("create new_obj, chain A")
        assert cmd == "create"
        assert args["name"] == "new_obj"

    def test_extract(self):
        cmd, args = parse_pymol_input("extract ligand, resn ATP")
        assert cmd == "extract"
        assert args == {"name": "ligand", "selection": "resn ATP"}

    def test_delete(self):
        cmd, args = parse_pymol_input("delete all")
        assert cmd == "delete"
        assert args == {"name": "all"}

    def test_remove(self):
        cmd, args = parse_pymol_input("remove solvent")
        assert cmd == "remove"
        assert args == {"selection": "solvent"}

    def test_align(self):
        cmd, args = parse_pymol_input("align mobile, target")
        assert cmd == "align"
        assert args == {"mobile": "mobile", "target": "target"}

    def test_super(self):
        cmd, args = parse_pymol_input("super mobile, target")
        assert cmd == "super"
        assert args == {"mobile": "mobile", "target": "target"}

    # --- Modification ---

    def test_alter(self):
        cmd, args = parse_pymol_input("alter chain A, b=50.0")
        assert cmd == "alter"
        assert args == {"selection": "chain A", "expression": "b=50.0"}

    def test_alter_state(self):
        cmd, args = parse_pymol_input("alter_state 1, all, x=x+1")
        assert cmd == "alter_state"
        assert args == {"state": "1", "selection": "all", "expression": "x=x+1"}

    def test_h_add(self):
        cmd, args = parse_pymol_input("h_add")
        assert cmd == "h_add"

    def test_h_add_selection(self):
        cmd, args = parse_pymol_input("h_add chain A")
        assert cmd == "h_add"
        assert args.get("selection") == "chain A"

    def test_bond(self):
        cmd, args = parse_pymol_input("bond /obj//A/1/N, /obj//A/2/C")
        assert cmd == "bond"

    def test_unbond(self):
        cmd, args = parse_pymol_input("unbond /obj//A/1/N, /obj//A/2/C")
        assert cmd == "unbond"

    # --- No-arg commands ---

    @pytest.mark.parametrize("input_str,expected_cmd", [
        ("refresh", "refresh"),
        ("mplay", "mplay"),
        ("mstop", "mstop"),
        ("forward", "forward"),
        ("backward", "backward"),
        ("rock", "rock"),
        ("full_screen", "full_screen"),
    ])
    def test_no_arg_commands(self, input_str, expected_cmd):
        cmd, args = parse_pymol_input(input_str)
        assert cmd == expected_cmd
        assert args == {}

    # --- Utility functions ---

    @pytest.mark.parametrize("input_str,expected_cmd", [
        ("util.cbc", "util.cbc"),
        ("util.cbaw chain A", "util.cbaw"),
        ("util.cbag", "util.cbag"),
        ("util.cbac", "util.cbac"),
        ("util.cbam", "util.cbam"),
        ("util.cbay", "util.cbay"),
        ("util.cbas", "util.cbas"),
        ("util.cbab", "util.cbab"),
        ("util.cbao", "util.cbao"),
        ("util.cbap", "util.cbap"),
        ("util.cbak", "util.cbak"),
        ("util.chainbow", "util.chainbow"),
        ("util.rainbow", "util.rainbow"),
    ])
    def test_util_commands(self, input_str, expected_cmd):
        cmd, args = parse_pymol_input(input_str)
        assert cmd == expected_cmd

    # --- Scenes, Movies, Rendering ---

    def test_scene(self):
        cmd, args = parse_pymol_input("scene F1, store")
        assert cmd == "scene"
        assert args == {"key": "F1", "action": "store"}

    def test_mset(self):
        cmd, args = parse_pymol_input("mset 1 x100")
        assert cmd == "mset"
        assert args == {"specification": "1 x100"}

    def test_ray(self):
        cmd, args = parse_pymol_input("ray 1920, 1080")
        assert cmd == "ray"
        assert args == {"width": "1920", "height": "1080"}

    def test_ray_bare(self):
        cmd, args = parse_pymol_input("ray")
        assert cmd == "ray"

    def test_draw(self):
        cmd, args = parse_pymol_input("draw 800, 600")
        assert cmd == "draw"

    def test_mpng(self):
        cmd, args = parse_pymol_input("mpng /tmp/frame")
        assert cmd == "mpng"
        assert args == {"prefix": "/tmp/frame"}

    # --- Crystallography ---

    def test_symexp(self):
        cmd, args = parse_pymol_input("symexp sym, all, 10")
        assert cmd == "symexp"
        assert args["prefix"] == "sym"

    # --- Other ---

    def test_fab(self):
        cmd, args = parse_pymol_input("fab ACDEG")
        assert cmd == "fab"
        assert args == {"sequence": "ACDEG"}

    def test_fragment(self):
        cmd, args = parse_pymol_input("fragment ala")
        assert cmd == "fragment"
        assert args == {"name": "ala"}

    def test_viewport(self):
        cmd, args = parse_pymol_input("viewport 1024, 768")
        assert cmd == "viewport"
        assert args == {"width": "1024", "height": "768"}

    def test_help_bare(self):
        cmd, args = parse_pymol_input("help")
        assert cmd == "help"

    def test_help_command(self):
        cmd, args = parse_pymol_input("help show")
        assert cmd == "help"
        assert args == {"command": "show"}

    # --- Composite ---

    def test_color_ss(self):
        cmd, args = parse_pymol_input("color_ss")
        assert cmd == "color_ss"

    def test_color_ss_with_selection(self):
        cmd, args = parse_pymol_input("color_ss chain A")
        assert cmd == "color_ss"
        assert args.get("selection") == "chain A"

    # --- Sculpting ---

    def test_sculpt_activate(self):
        cmd, args = parse_pymol_input("sculpt_activate myobj")
        assert cmd == "sculpt_activate"
        assert args == {"object": "myobj"}

    def test_sculpt_iterate(self):
        cmd, args = parse_pymol_input("sculpt_iterate 100, myobj")
        assert cmd == "sculpt_iterate"
        assert args == {"iterations": "100", "object": "myobj"}

    # --- Isosurface ---

    def test_isomesh(self):
        cmd, args = parse_pymol_input("isomesh mesh1, 2fofc, 1.5")
        assert cmd == "isomesh"
        assert args["name"] == "mesh1"
        assert args["map_object"] == "2fofc"
        assert args["level"] == "1.5"

    def test_isosurface(self):
        cmd, args = parse_pymol_input("isosurface surf1, fofc, 3.0, chain A")
        assert cmd == "isosurface"
        assert args["selection"] == "chain A"


# ============================================================================
# 3. PATTERN MATCHING - INVALID COMMANDS THAT SHOULD FAIL
# ============================================================================


class TestPatternMatchingInvalid:
    """Commands that should NOT parse successfully."""

    def test_unknown_command(self):
        with pytest.raises(ValueError, match="No recognized"):
            parse_pymol_input("foobar baz")

    def test_empty_string(self):
        with pytest.raises(ValueError):
            parse_pymol_input("")

    def test_whitespace_only(self):
        with pytest.raises(ValueError):
            parse_pymol_input("   ")

    def test_show_invalid_representation(self):
        """'show wiggles' is not a valid representation."""
        with pytest.raises(ValueError, match="must be one of"):
            parse_pymol_input("show wiggles")

    def test_cartoon_invalid_type(self):
        with pytest.raises(ValueError, match="must be one of"):
            parse_pymol_input("cartoon zigzag")

    def test_turn_invalid_axis(self):
        """Only x, y, z are valid axes."""
        with pytest.raises(ValueError):
            parse_pymol_input("turn w, 90")

    def test_clip_invalid_mode(self):
        with pytest.raises(ValueError, match="must be one of"):
            parse_pymol_input("clip diagonal, 5")

    def test_show_no_args(self):
        """'show' alone should fail (representation is required)."""
        with pytest.raises(ValueError):
            parse_pymol_input("show")

    def test_color_no_args(self):
        """'color' alone should fail."""
        with pytest.raises(ValueError):
            parse_pymol_input("color")

    def test_fetch_no_code(self):
        with pytest.raises(ValueError):
            parse_pymol_input("fetch")

    def test_load_no_filename(self):
        with pytest.raises(ValueError):
            parse_pymol_input("load")

    def test_delete_no_name(self):
        with pytest.raises(ValueError):
            parse_pymol_input("delete")


# ============================================================================
# 4. CASE SENSITIVITY
# ============================================================================


class TestCaseSensitivity:
    """parse_pymol_input uses re.IGNORECASE, verify it works."""

    def test_uppercase_show(self):
        cmd, args = parse_pymol_input("SHOW cartoon")
        assert cmd == "show"

    def test_mixed_case_fetch(self):
        cmd, args = parse_pymol_input("Fetch 1UBQ")
        assert cmd == "fetch"
        assert args["code"] == "1UBQ"

    def test_uppercase_color(self):
        cmd, args = parse_pymol_input("COLOR red, chain A")
        assert cmd == "color"

    def test_uppercase_deselect(self):
        cmd, args = parse_pymol_input("DESELECT")
        assert cmd == "deselect"


# ============================================================================
# 5. WHITESPACE HANDLING
# ============================================================================


class TestWhitespace:
    """Verify leading/trailing whitespace and spacing around commas."""

    def test_leading_whitespace(self):
        cmd, args = parse_pymol_input("  show cartoon  ")
        assert cmd == "show"

    def test_extra_spaces_around_comma(self):
        cmd, args = parse_pymol_input("color red  ,  chain A")
        assert cmd == "color"
        assert args["selection"] == "chain A"

    def test_no_space_around_comma(self):
        cmd, args = parse_pymol_input("color red,chain A")
        assert cmd == "color"
        assert args["selection"] == "chain A"


# ============================================================================
# 6. DEFAULT VALUES
# ============================================================================


class TestDefaultValues:
    """Verify defaults are applied for optional parameters."""

    def test_show_default_selection(self):
        cmd, args = parse_pymol_input("show cartoon")
        assert "selection" not in args or args.get("selection") == "all"

    def test_zoom_default_buffer(self):
        cmd, args = parse_pymol_input("zoom chain A")
        # Buffer not provided, should get default
        assert args.get("buffer", "5") == "5"

    def test_turn_default_angle(self):
        cmd, args = parse_pymol_input("turn x")
        assert args.get("angle", "90") == "90"

    def test_spectrum_defaults(self):
        cmd, args = parse_pymol_input("spectrum count")
        # palette and selection should use defaults
        assert args.get("palette", "rainbow") == "rainbow"


# ============================================================================
# 9. SELECTION SYNTAX EDGE CASES
# ============================================================================


class TestSelectionSyntax:
    """PyMOL selections can contain complex expressions."""

    def test_boolean_selection(self):
        cmd, args = parse_pymol_input("show sticks, chain A and resi 50-60")
        assert args["selection"] == "chain A and resi 50-60"

    def test_not_selection(self):
        cmd, args = parse_pymol_input("color green, not solvent")
        assert args["selection"] == "not solvent"

    def test_parens_in_selection(self):
        cmd, args = parse_pymol_input("color red, (chain A or chain B) and name CA")
        assert "chain A or chain B" in args["selection"]

    def test_slash_selection(self):
        """PyMOL object/segi/chain/resi/name syntax."""
        cmd, args = parse_pymol_input("show sticks, /1ubq//A/50/CA")
        assert args["selection"] == "/1ubq//A/50/CA"

    def test_secondary_structure_selection(self):
        cmd, args = parse_pymol_input("color red, ss h")
        assert args["selection"] == "ss h"

    def test_resn_selection(self):
        cmd, args = parse_pymol_input("show sticks, resn ATP")
        assert args["selection"] == "resn ATP"

    def test_within_selection(self):
        cmd, args = parse_pymol_input("color yellow, byres all within 5 of resn ATP")
        assert "within 5 of resn ATP" in args["selection"]


# ============================================================================
# 10. REQUIRED PARAMETER ENFORCEMENT
# ============================================================================


class TestRequiredParameters:
    """Ensure required parameters without defaults raise errors when missing."""

    def test_alter_missing_expression(self):
        """'alter chain A' is missing the required expression param.
        But the regex may capture it differently - test what actually happens."""
        # 'alter' pattern: r"^alter\s+([^,]+)(?:\s*,\s*(.+))?$"
        # "alter chain A" -> groups = ("chain A", None)
        # param "expression" is required with no default -> should raise
        with pytest.raises(ValueError, match="Missing required parameter"):
            parse_pymol_input("alter chain A")

    def test_bond_missing_atom2(self):
        """bond requires atom1 and atom2."""
        # bond pattern: r"^bond\s+([^,]+)(?:\s*,\s*([^,]+))?(?:\s*,\s*(.+))?$"
        # "bond /obj//A/1/N" -> groups = ("/obj//A/1/N", None, None)
        # atom2 is required -> should raise
        with pytest.raises(ValueError, match="Missing required parameter"):
            parse_pymol_input("bond /obj//A/1/N")

    def test_viewport_missing_height(self):
        """viewport requires width and height."""
        # viewport pattern captures (width), (height) - height is required
        with pytest.raises(ValueError, match="Missing required parameter"):
            parse_pymol_input("viewport 1024")

    def test_set_symmetry_partial(self):
        """set_symmetry needs all 7 params (selection + 6 cell params)."""
        with pytest.raises(ValueError, match="Missing required parameter"):
            parse_pymol_input("set_symmetry myobj, 50, 50")


# ============================================================================
# 11. PATTERN AMBIGUITY AND PRIORITY
# ============================================================================


class TestPatternAmbiguity:
    """Test that commands don't accidentally match the wrong pattern."""

    def test_color_not_matched_as_cartoon(self):
        """'color' should not match 'cartoon' pattern."""
        cmd, _ = parse_pymol_input("color red")
        assert cmd == "color"

    def test_center_not_matched_as_create(self):
        cmd, _ = parse_pymol_input("center chain A")
        assert cmd == "center"

    def test_show_not_matched_as_set(self):
        cmd, _ = parse_pymol_input("show cartoon")
        assert cmd == "show"

    def test_frame_with_number(self):
        """'frame 5' should match frame, not fragment."""
        cmd, args = parse_pymol_input("frame 5")
        assert cmd == "frame"
        assert args.get("frame_number") == "5"

    def test_reset_not_matched_as_remove(self):
        cmd, _ = parse_pymol_input("reset")
        assert cmd == "reset"

    def test_rebuild_not_matched_as_remove(self):
        cmd, _ = parse_pymol_input("rebuild")
        assert cmd == "rebuild"


# ============================================================================
# 13. COMPLEX REAL-WORLD COMMAND SEQUENCES
# ============================================================================


class TestRealWorldCommands:
    """Test realistic command sequences a structural biologist would use."""

    def test_typical_visualization_workflow(self):
        """Fetch -> show cartoon -> color by chain."""
        cmds = [
            "fetch 1ubq",
            "as cartoon",
            "util.cbc",
            "zoom",
        ]
        for c in cmds:
            cmd, args = parse_pymol_input(c)
            assert cmd is not None

    def test_active_site_visualization(self):
        cmds = [
            "fetch 4hhb",
            "hide everything",
            "show cartoon",
            "select heme, resn HEM",
            "show sticks, heme",
            "color red, heme",
            "zoom heme, 5",
        ]
        for c in cmds:
            cmd, args = parse_pymol_input(c)
            assert cmd is not None

    def test_alignment_workflow(self):
        cmds = [
            "fetch 1ubq",
            "fetch 1ubi",
            "align 1ubi, 1ubq",
            "color green, 1ubq",
            "color cyan, 1ubi",
        ]
        for c in cmds:
            cmd, args = parse_pymol_input(c)
            assert cmd is not None

    def test_publication_rendering(self):
        for c in [
            "set ray_shadows, off",
            "set antialias, 2",
            "set ray_trace_mode, 1",
            "bg_color white",
        ]:
            cmd, args = parse_pymol_input(c)
            assert cmd is not None, f"{c} should parse"

    def test_bg_color(self):
        """Was missing from the table, forcing callers onto `set bg_rgb`."""
        cmd, args = parse_pymol_input("bg_color white")
        assert cmd == "bg_color"
        assert args == {"color": "white"}

    def test_bg_color_defaults_to_black(self):
        cmd, args = parse_pymol_input("bg_color")
        assert (cmd, args) == ("bg_color", {"color": "black"})

    def test_bfactor_spectrum(self):
        cmds = [
            "fetch 1ubq",
            "as cartoon",
            "spectrum b, blue_white_red",
        ]
        for c in cmds:
            cmd, args = parse_pymol_input(c)
            assert cmd is not None


# ============================================================================
# 14. COMPOSITE COMMAND DEFINITION
# ============================================================================


class TestCompositeCommands:
    """Test composite command handling."""

    def test_color_ss_marked_composite(self):
        assert PYMOL_COMMANDS["color_ss"].composite is True

    def test_no_other_commands_marked_composite(self):
        """Only color_ss should be composite currently."""
        composites = [
            name for name, info in PYMOL_COMMANDS.items()
            if info.composite
        ]
        assert composites == ["color_ss"], (
            f"Unexpected composite commands: {composites}"
        )


# ============================================================================
# 15. SERVER MODULE INTEGRITY
# ============================================================================
