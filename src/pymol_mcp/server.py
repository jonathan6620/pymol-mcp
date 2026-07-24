#!/usr/bin/env python3
import json
import logging
import os
import re
import socket
import struct
import tempfile
import zlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from mcp.server.fastmcp import Context, FastMCP, Image

from pymol_mcp.models import (
    CommandDef,
    ErrorCategory,
    ParameterDef,
    ParseResult,
    SocketRequest,
    SocketResponse,
)

##############################################################################
# PYMOL COMMAND DEFINITIONS AND ERROR PATTERNS
##############################################################################

PYMOL_COMMANDS: dict[str, CommandDef] = {
    # MOLECULAR VISUALIZATION
    "show": CommandDef(
        description="Shows a representation for the specified selection",
        pattern=r"^show\s+([\w.]+)(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(
                name="representation",
                required=True,
                options=[
                    "lines",
                    "sticks",
                    "spheres",
                    "surface",
                    "mesh",
                    "dots",
                    "ribbon",
                    "cartoon",
                    "labels",
                    "nonbonded",
                    "nb_spheres",
                    "ellipsoids",
                    "volume",
                    "slice",
                    "extent",
                    "dots_as_spheres",
                    "cell",
                    "cgo",
                    "everything",
                    "dashes",
                    "angles",
                    "dihedrals",
                    "licorice",
                    "putty",
                ],
            ),
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "hide": CommandDef(
        description="Hides a representation for the specified selection",
        pattern=r"^hide\s+([\w.]+)(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(
                name="representation",
                required=True,
                options=[
                    "lines",
                    "sticks",
                    "spheres",
                    "surface",
                    "mesh",
                    "dots",
                    "ribbon",
                    "cartoon",
                    "labels",
                    "nonbonded",
                    "nb_spheres",
                    "ellipsoids",
                    "volume",
                    "slice",
                    "extent",
                    "dots_as_spheres",
                    "cell",
                    "cgo",
                    "everything",
                    "dashes",
                    "angles",
                    "dihedrals",
                    "licorice",
                    "putty",
                ],
            ),
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "color": CommandDef(
        description="Sets the color for the specified selection",
        pattern=r"^color\s+([\w.]+)(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="color", required=True),
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "bg_color": CommandDef(
        description="Sets the background color",
        pattern=r"^bg_color(?:\s+([\w.]+))?$",
        parameters=[
            ParameterDef(name="color", required=False, default="black"),
        ],
        check_selection=False,
    ),
    "as": CommandDef(
        description=(
            "Shows one representation while hiding all others for the "
            "specified selection"
        ),
        pattern=r"^as\s+([\w.]+)(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="representation", required=True),
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "set": CommandDef(
        description="Sets a PyMOL setting to a specified value",
        pattern=r"^set\s+([\w.]+)(?:\s*,\s*([^,]+))?(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="setting", required=True),
            ParameterDef(name="value", required=True),
            ParameterDef(name="selection", required=False),
        ],
        check_selection=False,
    ),
    "cartoon": CommandDef(
        description="Sets the cartoon type for the specified selection",
        pattern=r"^cartoon\s+([\w.]+)(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(
                name="type",
                required=True,
                options=[
                    "automatic",
                    "loop",
                    "rectangle",
                    "oval",
                    "tube",
                    "arrow",
                    "dumbbell",
                    "putty",
                ],
            ),
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "spectrum": CommandDef(
        description="Colors selection in a spectrum",
        pattern=r"^spectrum\s+([^,]+)(?:\s*,\s*([^,]+))?(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="expression", required=True),
            ParameterDef(name="palette", required=False, default="rainbow"),
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "label": CommandDef(
        description="Adds labels to atoms in the selection",
        pattern=r"^label\s+([^,]+)(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=True),
            ParameterDef(name="expression", required=False, default="name"),
        ],
        check_selection=True,
    ),
    "distance": CommandDef(
        description="Measures the distance between two selections",
        pattern=r"^distance(?:\s+([^,]+))?(?:\s*,\s*([^,]+))?(?:\s*,\s*([^,]+))?$",
        parameters=[
            ParameterDef(name="name", required=False),
            ParameterDef(name="selection1", required=False, default="(pk1)"),
            ParameterDef(name="selection2", required=False, default="(pk2)"),
        ],
        check_selection=True,
    ),
    "angle": CommandDef(
        description="Measures the angle between three selections",
        pattern=r"^angle(?:\s+([^,]+))?(?:\s*,\s*([^,]+))?(?:\s*,\s*([^,]+))?(?:\s*,\s*([^,]+))?$",
        parameters=[
            ParameterDef(name="name", required=False),
            ParameterDef(name="selection1", required=False, default="(pk1)"),
            ParameterDef(name="selection2", required=False, default="(pk2)"),
            ParameterDef(name="selection3", required=False, default="(pk3)"),
        ],
        check_selection=True,
    ),
    "dihedral": CommandDef(
        description="Measures the dihedral angle between four selections",
        pattern=r"^dihedral(?:\s+([^,]+))?(?:\s*,\s*([^,]+))?(?:\s*,\s*([^,]+))?(?:\s*,\s*([^,]+))?(?:\s*,\s*([^,]+))?$",
        parameters=[
            ParameterDef(name="name", required=False),
            ParameterDef(name="selection1", required=False, default="(pk1)"),
            ParameterDef(name="selection2", required=False, default="(pk2)"),
            ParameterDef(name="selection3", required=False, default="(pk3)"),
            ParameterDef(name="selection4", required=False, default="(pk4)"),
        ],
        check_selection=True,
    ),
    # VIEWING OPERATIONS
    "center": CommandDef(
        description="Centers the view on a selection",
        pattern=r"^center(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "orient": CommandDef(
        description="Orients the view to align with principal axes of the selection",
        pattern=r"^orient(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "zoom": CommandDef(
        description="Zooms the view on a selection",
        pattern=r"^zoom(?:\s+([^,]+))?(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
            ParameterDef(name="buffer", required=False, default="5"),
        ],
        check_selection=True,
    ),
    "reset": CommandDef(
        description="Resets the view, optionally resetting an object's matrix",
        pattern=r"^reset(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="object", required=False),
        ],
        check_selection=False,
    ),
    "turn": CommandDef(
        description="Rotates the camera around an axis",
        pattern=r"^turn\s+([xyz])(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="axis", required=True, options=["x", "y", "z"]),
            ParameterDef(name="angle", required=False, default="90"),
        ],
        check_selection=False,
    ),
    "move": CommandDef(
        description="Moves the camera along an axis",
        pattern=r"^move\s+([xyz])(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="axis", required=True, options=["x", "y", "z"]),
            ParameterDef(name="distance", required=False, default="1"),
        ],
        check_selection=False,
    ),
    "clip": CommandDef(
        description="Adjusts the clipping planes",
        pattern=r"^clip\s+([\w.]+)(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(
                name="mode",
                required=True,
                options=["near", "far", "slab", "atoms", "near_slab", "far_slab"],
            ),
            ParameterDef(name="distance", required=False, default="1"),
        ],
        check_selection=False,
    ),
    # FILE OPERATIONS
    "load": CommandDef(
        description="Loads a file into PyMOL",
        pattern=r"^load\s+([^,]+)(?:\s*,\s*([^,]+))?(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="filename", required=True),
            ParameterDef(name="object", required=False),
            ParameterDef(name="options", required=False),
        ],
        check_selection=False,
    ),
    "fetch": CommandDef(
        description="Fetches a structure from a database (e.g., PDB)",
        pattern=r"^fetch\s+([^,]+)(?:\s*,\s*([^,]+))?(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="code", required=True),
            ParameterDef(name="name", required=False),
            ParameterDef(name="options", required=False),
        ],
        check_selection=False,
    ),
    "save": CommandDef(
        description="Saves data to a file",
        pattern=r"^save\s+([^,]+)(?:\s*,\s*(.+))?(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="filename", required=True),
            ParameterDef(name="selection", required=False, default="all"),
            ParameterDef(name="state", required=False, default="-1"),
        ],
        check_selection=True,
    ),
    "png": CommandDef(
        description="Saves a PNG image",
        pattern=r"^png\s+([^,]+)(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="filename", required=True),
            ParameterDef(name="options", required=False),
        ],
        check_selection=False,
    ),
    "get_view": CommandDef(
        description="Returns the current 18-value camera view",
        pattern=r"^get_view$",
        parameters=[],
        check_selection=False,
    ),
    "set_view": CommandDef(
        description="Restores an 18-value camera view encoded as a JSON list",
        pattern=r"^set_view\s+(.+)$",
        parameters=[ParameterDef(name="view", required=True)],
        check_selection=False,
    ),
    "get_setting": CommandDef(
        description="Returns the current value of a named PyMOL setting",
        pattern=r"^get_setting\s+([A-Za-z_]\w*)$",
        parameters=[ParameterDef(name="name", required=True)],
        check_selection=False,
    ),
    # SELECTION OPERATIONS
    "select": CommandDef(
        description="Creates a named selection",
        pattern=r"^select\s+([^,]+)(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="name", required=True),
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=False,
    ),
    "deselect": CommandDef(
        description="Clears the current selection",
        pattern=r"^deselect$",
        parameters=[],
        check_selection=False,
    ),
    # OBJECT MANIPULATION
    "create": CommandDef(
        description="Creates a new object from a selection",
        pattern=r"^create\s+([^,]+)(?:\s*,\s*(.+))?(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="name", required=True),
            ParameterDef(name="selection", required=False, default="all"),
            ParameterDef(name="source_state", required=False, default="1"),
        ],
        check_selection=True,
    ),
    "extract": CommandDef(
        description="Extracts a selection to a new object",
        pattern=r"^extract\s+([^,]+)(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="name", required=True),
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "delete": CommandDef(
        description="Deletes objects or selections",
        pattern=r"^delete\s+(.+)$",
        parameters=[
            ParameterDef(name="name", required=True),
        ],
        check_selection=False,
    ),
    "remove": CommandDef(
        description="Removes atoms in a selection",
        pattern=r"^remove\s+(.+)$",
        parameters=[
            ParameterDef(name="selection", required=True),
        ],
        check_selection=True,
    ),
    "align": CommandDef(
        description="Aligns one selection to another",
        pattern=r"^align\s+([^,]+)(?:\s*,\s*([^,]+))?(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="mobile", required=True),
            ParameterDef(name="target", required=False, default="all"),
            ParameterDef(name="options", required=False),
        ],
        check_selection=True,
    ),
    "super": CommandDef(
        description="Superimposes one selection onto another",
        pattern=r"^super\s+([^,]+)(?:\s*,\s*([^,]+))?(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="mobile", required=True),
            ParameterDef(name="target", required=False, default="all"),
            ParameterDef(name="options", required=False),
        ],
        check_selection=True,
    ),
    "intra_fit": CommandDef(
        description="Fits all states within an object",
        pattern=r"^intra_fit\s+(.+)$",
        parameters=[
            ParameterDef(name="selection", required=True),
        ],
        check_selection=True,
    ),
    "intra_rms": CommandDef(
        description="Calculates RMSD between states within an object",
        pattern=r"^intra_rms\s+(.+)$",
        parameters=[
            ParameterDef(name="selection", required=True),
        ],
        check_selection=True,
    ),
    # UTILITY AND MODIFICATION
    "alter": CommandDef(
        description=(
            "Alters atomic properties in a selection "
            "(expression is evaluated per-atom by PyMOL)"
        ),
        pattern=r"^alter\s+([^,]+)(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=True),
            ParameterDef(name="expression", required=True),
        ],
        check_selection=True,
    ),
    "alter_state": CommandDef(
        description=(
            "Alters atomic coordinates in a state "
            "(expression is evaluated per-atom by PyMOL)"
        ),
        pattern=r"^alter_state\s+([^,]+)(?:\s*,\s*([^,]+))?(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="state", required=True),
            ParameterDef(name="selection", required=True),
            ParameterDef(name="expression", required=True),
        ],
        check_selection=True,
    ),
    "h_add": CommandDef(
        description="Adds hydrogens to a selection",
        pattern=r"^h_add(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "h_fill": CommandDef(
        description="Adds hydrogens and adjusts valences",
        pattern=r"^h_fill(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "bond": CommandDef(
        description="Creates a bond between two atoms",
        pattern=r"^bond\s+([^,]+)(?:\s*,\s*([^,]+))?(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="atom1", required=True),
            ParameterDef(name="atom2", required=True),
            ParameterDef(name="order", required=False, default="1"),
        ],
        check_selection=True,
    ),
    "unbond": CommandDef(
        description="Removes a bond between two atoms",
        pattern=r"^unbond\s+([^,]+)(?:\s*,\s*([^,]+))?$",
        parameters=[
            ParameterDef(name="atom1", required=True),
            ParameterDef(name="atom2", required=True),
        ],
        check_selection=True,
    ),
    "rebuild": CommandDef(
        description="Regenerates all displayed geometry",
        pattern=r"^rebuild(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=False,
    ),
    "refresh": CommandDef(
        description="Refreshes the display",
        pattern=r"^refresh$",
        parameters=[],
        check_selection=False,
    ),
    # UTILITY FUNCTIONS
    "util.cbc": CommandDef(
        description="Colors by chain (Color By Chain)",
        pattern=r"^util\.cbc(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "util.cbaw": CommandDef(
        description="Colors by atom, white carbons (Color By Atom, White)",
        pattern=r"^util\.cbaw(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "util.cbag": CommandDef(
        description="Colors by atom, green carbons (Color By Atom, Green)",
        pattern=r"^util\.cbag(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "util.cbac": CommandDef(
        description="Colors by atom, cyan carbons (Color By Atom, Cyan)",
        pattern=r"^util\.cbac(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "util.cbam": CommandDef(
        description="Colors by atom, magenta carbons (Color By Atom, Magenta)",
        pattern=r"^util\.cbam(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "util.cbay": CommandDef(
        description="Colors by atom, yellow carbons (Color By Atom, Yellow)",
        pattern=r"^util\.cbay(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "util.cbas": CommandDef(
        description="Colors by atom, salmon carbons (Color By Atom, Salmon)",
        pattern=r"^util\.cbas(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "util.cbab": CommandDef(
        description="Colors by atom, slate carbons (Color By Atom, slateBLue)",
        pattern=r"^util\.cbab(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "util.cbao": CommandDef(
        description="Colors by atom, orange carbons (Color By Atom, Orange)",
        pattern=r"^util\.cbao(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "util.cbap": CommandDef(
        description="Colors by atom, purple carbons (Color By Atom, Purple)",
        pattern=r"^util\.cbap(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "util.cbak": CommandDef(
        description="Colors by atom, pink carbons (Color By Atom, pinK)",
        pattern=r"^util\.cbak(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "util.chainbow": CommandDef(
        description="Colors chains in rainbow gradient (CHAINs in rainBOW)",
        pattern=r"^util\.chainbow(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "util.rainbow": CommandDef(
        description="Colors residues in rainbow from N to C terminus",
        pattern=r"^util\.rainbow(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "color_ss": CommandDef(
        description=(
            "Colors by secondary structure: helices red, sheets yellow, loops green"
        ),
        pattern=r"^color_ss(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
        composite=True,
    ),
    # MOLECULAR DYNAMICS AND ANALYSIS
    "spheroid": CommandDef(
        description="Displays atoms as smooth spheres",
        pattern=r"^spheroid(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "isomesh": CommandDef(
        description="Creates a mesh isosurface",
        pattern=r"^isomesh\s+([^,]+)(?:\s*,\s*([^,]+))?(?:\s*,\s*([^,]+))?(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="name", required=True),
            ParameterDef(name="map_object", required=True),
            ParameterDef(name="level", required=True),
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "isosurface": CommandDef(
        description="Creates a solid isosurface",
        pattern=r"^isosurface\s+([^,]+)(?:\s*,\s*([^,]+))?(?:\s*,\s*([^,]+))?(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="name", required=True),
            ParameterDef(name="map_object", required=True),
            ParameterDef(name="level", required=True),
            ParameterDef(name="selection", required=False, default="all"),
        ],
        check_selection=True,
    ),
    "sculpt_activate": CommandDef(
        description="Activates sculpting mode for an object",
        pattern=r"^sculpt_activate\s+(.+)$",
        parameters=[
            ParameterDef(name="object", required=True),
        ],
        check_selection=False,
    ),
    "sculpt_deactivate": CommandDef(
        description="Deactivates sculpting mode for an object",
        pattern=r"^sculpt_deactivate\s+(.+)$",
        parameters=[
            ParameterDef(name="object", required=True),
        ],
        check_selection=False,
    ),
    "sculpt_iterate": CommandDef(
        description="Performs sculpting iterations",
        pattern=r"^sculpt_iterate\s+([^,]+)(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="iterations", required=True),
            ParameterDef(name="object", required=False, default="all"),
        ],
        check_selection=False,
    ),
    # SCENES AND MOVIES
    "scene": CommandDef(
        description="Manages scenes for later recall",
        pattern=r"^scene\s+([^,]+)(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="key", required=True),
            ParameterDef(name="action", required=False, default="recall"),
        ],
        check_selection=False,
    ),
    "scene_order": CommandDef(
        description="Sets the order of scenes",
        pattern=r"^scene_order\s+(.+)$",
        parameters=[
            ParameterDef(name="scene_list", required=True),
        ],
        check_selection=False,
    ),
    "mset": CommandDef(
        description="Defines a sequence of states for movie playback",
        pattern=r"^mset\s+(.+)$",
        parameters=[
            ParameterDef(name="specification", required=True),
        ],
        check_selection=False,
    ),
    "mplay": CommandDef(
        description="Starts playing the movie",
        pattern=r"^mplay$",
        parameters=[],
        check_selection=False,
    ),
    "mstop": CommandDef(
        description="Stops the movie",
        pattern=r"^mstop$",
        parameters=[],
        check_selection=False,
    ),
    "frame": CommandDef(
        description="Sets or queries the current frame",
        pattern=r"^frame(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="frame_number", required=False),
        ],
        check_selection=False,
    ),
    "forward": CommandDef(
        description="Advances one frame",
        pattern=r"^forward$",
        parameters=[],
        check_selection=False,
    ),
    "backward": CommandDef(
        description="Goes back one frame",
        pattern=r"^backward$",
        parameters=[],
        check_selection=False,
    ),
    "rock": CommandDef(
        description="Toggles a rocking animation",
        pattern=r"^rock$",
        parameters=[],
        check_selection=False,
    ),
    # RENDERING
    "ray": CommandDef(
        description="Performs ray-tracing",
        pattern=r"^ray(?:\s+([^,]+))?(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="width", required=False),
            ParameterDef(name="height", required=False),
        ],
        check_selection=False,
    ),
    "draw": CommandDef(
        description="Uses OpenGL renderer (faster but lower quality)",
        pattern=r"^draw(?:\s+([^,]+))?(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="width", required=False),
            ParameterDef(name="height", required=False),
        ],
        check_selection=False,
    ),
    "mpng": CommandDef(
        description="Saves a series of PNG images for movie frames",
        pattern=r"^mpng\s+(.+)$",
        parameters=[
            ParameterDef(name="prefix", required=True),
        ],
        check_selection=False,
    ),
    # CRYSTALLOGRAPHY
    "symexp": CommandDef(
        description="Generates symmetry-related copies",
        pattern=r"^symexp\s+([^,]+)(?:\s*,\s*([^,]+))?(?:\s*,\s*([^,]+))?(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="prefix", required=True),
            ParameterDef(name="selection", required=True),
            ParameterDef(name="cutoff", required=False, default="20"),
            ParameterDef(name="segi", required=False),
        ],
        check_selection=True,
    ),
    "set_symmetry": CommandDef(
        description="Sets symmetry parameters for an object",
        pattern=r"^set_symmetry\s+([^,]+)(?:\s*,\s*([^,]+))?(?:\s*,\s*([^,]+))?(?:\s*,\s*([^,]+))?(?:\s*,\s*([^,]+))?(?:\s*,\s*([^,]+))?(?:\s*,\s*([^,]+))?$",
        parameters=[
            ParameterDef(name="selection", required=True),
            ParameterDef(name="a", required=True),
            ParameterDef(name="b", required=True),
            ParameterDef(name="c", required=True),
            ParameterDef(name="alpha", required=True),
            ParameterDef(name="beta", required=True),
            ParameterDef(name="gamma", required=True),
        ],
        check_selection=True,
    ),
    # OTHER
    "fab": CommandDef(
        description="Creates a peptide chain from a sequence",
        pattern=r"^fab\s+([^,]+)(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="sequence", required=True),
            ParameterDef(name="options", required=False),
        ],
        check_selection=False,
    ),
    "fragment": CommandDef(
        description="Loads a molecular fragment",
        pattern=r"^fragment\s+(.+)$",
        parameters=[
            ParameterDef(name="name", required=True),
        ],
        check_selection=False,
    ),
    "full_screen": CommandDef(
        description="Toggles fullscreen mode",
        pattern=r"^full_screen$",
        parameters=[],
        check_selection=False,
    ),
    "viewport": CommandDef(
        description="Sets the viewport size",
        pattern=r"^viewport\s+([^,]+)(?:\s*,\s*(.+))?$",
        parameters=[
            ParameterDef(name="width", required=True),
            ParameterDef(name="height", required=True),
        ],
        check_selection=False,
    ),
    "help": CommandDef(
        description="Shows help for a command",
        pattern=r"^help(?:\s+(.+))?$",
        parameters=[
            ParameterDef(name="command", required=False),
        ],
        check_selection=False,
    ),
}


ERROR_PATTERNS = {
    "SYNTAX_ERROR": [r"Syntax error", r"invalid syntax", r"Unknown command"],
    "SELECTION_ERROR": [
        r"Invalid selection",
        r"No atoms selected",
        r"Selection not found",
        r"Selection \S+ doesn't exist",
    ],
    "OBJECT_NOT_FOUND": [
        r"object \S+ not found",
        r"Object \S+ does not exist",
        r"Unable to find object named \S+",
    ],
    "ATOM_NOT_FOUND": [
        r"No atoms matched",
        r"No atoms in selection",
        r"Atom not found",
    ],
    "FILE_ERROR": [
        r"Unable to open file",
        r"No such file",
        r"Permission denied",
        r"Error reading file",
        r"Error writing file",
    ],
    "CONNECTION_ERROR": [
        r"Connection refused",
        r"Network error",
        r"Timeout",
        r"Failed to fetch",
    ],
    "PARAMETER_ERROR": [
        r"Incorrect number of parameters",
        r"Invalid parameter",
        r"Parameter out of range",
    ],
}

# Validate ERROR_PATTERNS at module load time
for _label, _patterns in ERROR_PATTERNS.items():
    ErrorCategory(label=_label, patterns=_patterns)

##############################################################################
# LOGGING
##############################################################################

# On stdio transport, stdout is the JSON-RPC channel and anything on stderr is
# reported by the client as a server error -- Claude Code surfaces every line,
# so routine INFO chatter shows up as errors in its UI. Stay silent by default;
# set PYMOL_MCP_LOG_LEVEL (e.g. DEBUG, INFO) to get diagnostics on stderr.
logger = logging.getLogger("PyMOLMCPServer")

_log_level = os.environ.get("PYMOL_MCP_LOG_LEVEL", "").strip().upper()
if _log_level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
    logging.basicConfig(level=_log_level)
else:
    # A handler on the root logger keeps logging.lastResort from writing this
    # server's records -- or any library's -- to stderr.
    logging.getLogger().addHandler(logging.NullHandler())

##############################################################################
# PYMOL SOCKET CONNECTION
##############################################################################


class PyMOLConnection:
    def __init__(self, host: str = "localhost", port: int = 9876) -> None:
        if not (1 <= port <= 65535):
            raise ValueError(f"Port must be between 1 and 65535, got {port}")
        self.host = host
        self.port = port
        self.sock: socket.socket | None = None
        self._recv_buffer = b""

    def connect(self) -> bool:
        if self.sock:
            return True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to PyMOL at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Connection error: {e}")
            self.sock = None
            return False

    def disconnect(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Disconnect error: {e}")
            finally:
                self.sock = None
                self._recv_buffer = b""

    def send_command(
        self,
        command: str,
        args: dict[str, Any],
        source: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        Sends a structured command to PyMOL via the socket plugin.
        Instead of sending raw code, sends {"type": "structured_command",
        "command": "show", "args": {"representation": "sticks", ...}}.

        `source` is the literal PyMOL syntax the command came from. The plugin
        records it in its session history. Leave it unset for internal traffic
        such as the health-check ping, which should not appear in the history.
        """
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to PyMOL")
        sock = self.sock
        if sock is None:  # Narrow the attribute after connect() for type checkers.
            raise ConnectionError("Not connected to PyMOL")
        request = SocketRequest(command=command, args=args, source=source)
        try:
            # exclude_none keeps the payload byte-identical to before `source`
            # existed whenever it is unset, so an older plugin sees no change.
            payload = request.model_dump(exclude_none=True)
            sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            sock.settimeout(timeout or _command_timeout(command, args))
            while b"\n" not in self._recv_buffer:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("No response from PyMOL")
                self._recv_buffer += chunk
            line, self._recv_buffer = self._recv_buffer.split(b"\n", 1)
            return json.loads(line.decode("utf-8"))
        except socket.timeout:
            self.disconnect()
            raise TimeoutError("PyMOL response timed out")
        except Exception as e:
            self.disconnect()
            raise RuntimeError(f"PyMOL command error: {e}")


# Each PyMOL claims the first free port here; see PORT_RANGE in the plugin.
# Discovery is a scan rather than a registry file: nothing to clean up when an
# instance is killed, and a scan of 20 localhost ports costs about a
# millisecond because a closed port refuses immediately.
PORT_RANGE = range(9876, 9896)
SCAN_TIMEOUT = 0.2
COMMAND_TIMEOUT = 10.0
RENDER_TIMEOUT = 300.0
MAX_RENDER_TIMEOUT = 1800.0


def _command_timeout(command: str, args: dict[str, Any]) -> float:
    """Scale render waits by output pixels; keep interactive commands snappy."""
    if command not in {"ray", "draw", "png", "mpng"}:
        return COMMAND_TIMEOUT
    if command == "mpng":
        return MAX_RENDER_TIMEOUT
    try:
        width = int(args.get("width", 0))
        height = int(args.get("height", 0))
    except (TypeError, ValueError):
        return RENDER_TIMEOUT
    if width <= 0 or height <= 0:
        return RENDER_TIMEOUT
    megapixels = width * height / 1_000_000
    return min(MAX_RENDER_TIMEOUT, max(RENDER_TIMEOUT, 60.0 + 60.0 * megapixels))


def discover_instances() -> list[dict[str, Any]]:
    """Find every listening PyMOL and ask each to identify itself.

    Returns dicts with port, pid and loaded objects, ordered by port. An
    instance that answers nothing usable is still reported, since it is
    reachable and the user may want to know it is there.
    """
    found: list[dict[str, Any]] = []
    for port in PORT_RANGE:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(SCAN_TIMEOUT)
        try:
            if probe.connect_ex(("localhost", port)) != 0:
                continue
            request = json.dumps({"type": "instance_info"}) + "\n"
            probe.sendall(request.encode("utf-8"))
            response = b""
            while b"\n" not in response:
                chunk = probe.recv(4096)
                if not chunk:
                    raise ConnectionError("Instance closed without a response")
                response += chunk
            line, _ = response.split(b"\n", 1)
            reply = json.loads(line.decode("utf-8"))
            result = reply.get("result") or {}
            found.append(
                {
                    "port": port,
                    "pid": result.get("pid"),
                    "objects": result.get("objects", []),
                }
            )
        except Exception as e:  # noqa: BLE001 - one bad instance must not stop the scan
            logger.info(f"Instance probe failed on {port}: {e}")
            found.append({"port": port, "pid": None, "objects": []})
        finally:
            probe.close()
    return found


_connections: dict[int, PyMOLConnection] = {}


def get_pymol_connection(port: int | None = None) -> PyMOLConnection:
    """Return a live connection to the requested PyMOL, or the obvious one.

    With no port: use the only running instance. If several are running the
    choice is ambiguous, so refuse and list them rather than picking one and
    silently driving a window the user is not looking at.
    """
    if port is None:
        instances = discover_instances()
        if not instances:
            raise RuntimeError(
                "No PyMOL is listening. Start PyMOL, then retry; the plugin "
                "claims a port automatically."
            )
        if len(instances) > 1:
            listed = ", ".join(
                f"{i['port']} ({', '.join(i['objects']) or 'nothing loaded'})"
                for i in instances
            )
            raise RuntimeError(
                f"{len(instances)} PyMOL instances are running: {listed}. "
                "Pass instance=<port> to choose one."
            )
        port = instances[0]["port"]

    if port is None:  # The branches above either assign a port or raise.
        raise RuntimeError("No PyMOL instance selected.")

    existing = _connections.get(port)
    if existing is not None:
        try:
            existing.send_command("refresh", {})
            return existing
        except Exception:
            try:
                existing.disconnect()
            except Exception:
                pass
            _connections.pop(port, None)

    conn = PyMOLConnection(port=port)
    if not conn.connect():
        raise RuntimeError(f"Could not connect to PyMOL on port {port}.")
    _connections[port] = conn
    return conn


##############################################################################
# PARSING USER INPUT TO STRUCTURED COMMANDS
##############################################################################


def parse_pymol_input(input_text: str) -> ParseResult:
    """
    Matches user input against known PYMOL_COMMANDS patterns.
    Returns a ParseResult (supports tuple unpacking: cmd, args = ...).
    Raises ValueError if no command matches or if there's a parameter error.
    """
    text_stripped = input_text.strip()
    for cmd_name, cmd_info in PYMOL_COMMANDS.items():
        pattern = re.compile(cmd_info.pattern, re.IGNORECASE)
        match = pattern.match(text_stripped)
        if match:
            groups = match.groups()
            param_values: dict[str, Any] = {}
            for i, param_def in enumerate(cmd_info.parameters):
                value = None
                if i < len(groups) and groups[i] is not None:
                    value = groups[i].strip()
                elif param_def.required and param_def.default is None:
                    raise ValueError(
                        f"Missing required parameter '{param_def.name}' "
                        f"for command {cmd_name}"
                    )
                elif value is None and param_def.default is not None:
                    value = param_def.default
                if param_def.options and value and value not in param_def.options:
                    raise ValueError(
                        f"Parameter '{param_def.name}' must be one of "
                        f"{param_def.options}"
                    )
                if value is not None:
                    param_values[param_def.name] = value
            return ParseResult(command=cmd_name, args=param_values)
    first_word = text_stripped.split()[0] if text_stripped.split() else ""
    raise ValueError(
        "No recognized PyMOL command pattern matched this input. Input must be "
        "literal PyMOL syntax, one command per call -- natural language is not "
        f"accepted. '{first_word}' is not a known command; call list_commands "
        "to see the available ones."
    )


def analyze_pymol_output(output_text: str) -> str | None:
    """
    Attempts to map known error patterns in the PyMOL output to a user-friendly error.
    Returns None if no known error patterns are matched.
    """
    for error_label, patterns in ERROR_PATTERNS.items():
        for p in patterns:
            if re.search(p, output_text, re.IGNORECASE):
                return f"{error_label} detected: {p}"
    return None


##############################################################################
# MCP SERVER SETUP
##############################################################################


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    try:
        logger.info("Starting PyMOL MCP server (structured command mode).")
        # Report what is out there rather than connecting. With several
        # instances running there is no single right one to open at startup,
        # and the caller picks per command.
        try:
            found = discover_instances()
            logger.info(
                "PyMOL instances found: "
                + (", ".join(str(i["port"]) for i in found) or "none")
            )
        except Exception as e:
            logger.warning(f"Instance discovery failed: {e}")
        yield {}
    finally:
        for conn in list(_connections.values()):
            try:
                conn.disconnect()
            except Exception as e:
                logger.info(f"Error closing connection: {e}")
        _connections.clear()
        logger.info("PyMOL MCP server shut down.")


SERVER_INSTRUCTIONS = """\
PyMOL integration with structured command dispatch (no arbitrary code execution).

This server does NOT accept natural language. `parse_and_execute` matches its
input against a fixed table of PyMOL command patterns, so you must translate the
user's request into literal PyMOL syntax before calling it.

Three rules cover most mistakes:
  1. One command per call. Split a multi-step request into separate calls.
  2. A selection is a comma-separated second argument, not a prepositional
     phrase: `show cartoon, chain A` -- not `show cartoon for chain A`.
  3. `fetch` downloads by PDB accession code; `load` reads a local file path.
     "Load PDB 1UBQ" means `fetch 1ubq`.

Call `list_commands` for the full command table with exact patterns. Prefer it
over guessing: unrecognized input is rejected, not interpreted.

Load the `pymol-mcp` skill if it is available. It covers the table's gaps (no
`iterate`, no `print`), selection idioms, how to enumerate chains, and the
render-then-look loop for confirming a change actually landed."""

mcp = FastMCP(
    "PyMOLMCPServer", instructions=SERVER_INSTRUCTIONS, lifespan=server_lifespan
)

##############################################################################
# MCP TOOL: parse_and_execute
##############################################################################


def _execute_user_command(
    ctx: Context,
    user_input: str,
    instance: int | None = None,
    connection: PyMOLConnection | None = None,
) -> tuple[str, bool, PyMOLConnection | None]:
    """Parse and execute one command, preserving structured success state."""
    try:
        result = parse_pymol_input(user_input)
        command_name = result.command
        args = result.args
    except ValueError as ve:
        return (
            f"No recognized PyMOL command or parameter issue: {ve}",
            False,
            connection,
        )
    except Exception as e:
        return f"Parsing error: {e}", False, connection

    if command_name == "help":
        cmd_obj = args.get("command", "")
        if cmd_obj and cmd_obj in PYMOL_COMMANDS:
            return (
                f"Help for {cmd_obj}: {PYMOL_COMMANDS[cmd_obj].description}",
                True,
                connection,
            )
        return (
            "Available commands: " + ", ".join(sorted(PYMOL_COMMANDS.keys())),
            True,
            connection,
        )

    try:
        conn = connection or get_pymol_connection(instance)
        if command_name == "color_ss":
            sel = args.get("selection", "all")
            for color, ss in [("red", "h"), ("yellow", "s"), ("green", "l+")]:
                ss_sel = f"(ss {ss}) and ({sel})" if sel != "all" else f"ss {ss}"
                response = conn.send_command(
                    "color",
                    {"color": color, "selection": ss_sel},
                    source=f"color {color}, {ss_sel}",
                )
                parsed = SocketResponse(**response)
                if parsed.status != "success":
                    return (
                        f"Command error: {parsed.message or 'Unknown error'}",
                        False,
                        conn,
                    )
            return (
                f"Colored by secondary structure ({sel}): "
                "helices=red, sheets=yellow, loops=green",
                True,
                conn,
            )

        response = conn.send_command(command_name, args, source=user_input.strip())
        parsed = SocketResponse(**response)
        if parsed.status != "success":
            message = parsed.message or "Unknown error"
            check_error = analyze_pymol_output(message)
            if check_error:
                return f"Command failed: {check_error}", False, conn
            return f"Command error: {message}", False, conn

        result_value = parsed.result
        output = (
            result_value.get("output", "")
            if isinstance(result_value, dict)
            else str(result_value)
            if result_value
            else ""
        )
        check_error = analyze_pymol_output(output)
        if check_error:
            return (
                "PyMOL command completed but possible error:\n"
                f"{check_error}\nRaw Output:\n{output}",
                False,
                conn,
            )
        return output or "Command executed (no output).", True, conn
    except Exception as e:
        return f"Execution error: {e}", False, connection


@mcp.tool()
def parse_and_execute(
    ctx: Context, user_input: str, instance: int | None = None
) -> str:
    """
    Executes a single PyMOL command given in literal PyMOL syntax.

    NOT a natural-language interface. `user_input` is matched against a fixed
    table of command patterns; anything else is rejected rather than guessed at.
    Translate the user's request into PyMOL syntax yourself, then call this once
    per command. Use `list_commands` to look up exact syntax.

    `instance` is the port of the PyMOL to drive. Leave it unset when only one
    is running. With several running an unset instance is an error rather than
    a guess, since driving the window the user is not watching looks exactly
    like the command doing nothing. Call `list_instances` to see the choices.

    Translating requests:
      "Load PDB 1UBQ and show it as cartoon"
          -> parse_and_execute("fetch 1ubq")
          -> parse_and_execute("as cartoon, 1ubq")
      "Colour chain A red"        -> "color red, chain A"
      "Show sticks for residues 1-50"  -> "show sticks, resi 1-50"
      "Open /data/model.pdb"      -> "load /data/model.pdb"
      "Select the binding site"   -> "select site, byres (polymer within 5 of ligand)"

    Common mistakes:
      - Multiple commands in one call. "fetch 1ubq and show cartoon" fails;
        the whole string is read as one filename/code.
      - `load` for a PDB ID. `load` takes a file path; use `fetch` for a
        4-character accession code like 1ubq.
      - Selections as prose. Write `show cartoon, chain A`, not
        `show cartoon for chain A` -- the selection is a second argument
        after a comma.
      - Conversational filler. "please show cartoon" does not match; send
        "show cartoon".

    Selections use full PyMOL algebra (`chain A and resi 1-50`, `not solvent`,
    `byres (... within 5 of ...)`). Commas separate arguments, so a selection
    containing a comma must be rewritten with `+` (`resi 1+2+3`).

    Returns PyMOL's output, or a message describing the parse/execution failure.
    """
    output, _, _ = _execute_user_command(ctx, user_input, instance)
    return output


def _execute_batch(
    ctx: Context,
    commands: list[str],
    instance: int | None = None,
    stop_on_error: bool = True,
) -> str:
    """Execute up to 100 ordinary allowlisted PyMOL commands in order."""
    if not commands:
        return "No commands supplied."
    if len(commands) > 100:
        return "Batch rejected: at most 100 commands are allowed."

    results = []
    connection = None
    for index, command in enumerate(commands, start=1):
        if not isinstance(command, str) or not command.strip():
            output = "Parsing error: each batch item must be a non-empty string"
            succeeded = False
        else:
            output, succeeded, connection = _execute_user_command(
                ctx, command, instance, connection
            )
        results.append(f"{index}. {command!r}: {output}")
        if not succeeded and stop_on_error:
            results.append(f"Stopped after command {index}.")
            break
    return "\n".join(results)


@mcp.tool()
def execute_batch(
    ctx: Context,
    commands: list[str],
    instance: int | None = None,
    stop_on_error: bool = True,
) -> str:
    """Execute up to 100 ordinary allowlisted PyMOL commands in order."""
    return _execute_batch(ctx, commands, instance, stop_on_error)


def _direct_output(response: dict[str, Any]) -> str:
    parsed = SocketResponse(**response)
    if parsed.status != "success":
        raise RuntimeError(parsed.message or "Unknown PyMOL error")
    if isinstance(parsed.result, dict):
        return str(parsed.result.get("output", ""))
    return str(parsed.result or "")


@mcp.tool()
def get_view(ctx: Context, instance: int | None = None) -> list[float]:
    """Return the current PyMOL camera as an 18-value list."""
    response = get_pymol_connection(instance).send_command("get_view", {})
    value = json.loads(_direct_output(response))
    if not isinstance(value, list) or len(value) != 18:
        raise RuntimeError("PyMOL returned an invalid camera view")
    return [float(item) for item in value]


@mcp.tool()
def set_view(
    ctx: Context, view: list[float], instance: int | None = None
) -> str:
    """Restore a camera previously returned by get_view."""
    if len(view) != 18:
        return "View rejected: exactly 18 numeric values are required."
    response = get_pymol_connection(instance).send_command(
        "set_view", {"view": [float(item) for item in view]}
    )
    _direct_output(response)
    return "Camera view restored."


@mcp.tool()
def get_setting(
    ctx: Context, name: str, instance: int | None = None
) -> Any:
    """Return one named PyMOL setting without changing the scene."""
    if not re.fullmatch(r"[A-Za-z_]\w*", name):
        raise ValueError(
            "Setting name must contain only letters, numbers, and underscores"
        )
    response = get_pymol_connection(instance).send_command(
        "get_setting", {"name": name}
    )
    return json.loads(_direct_output(response))["value"]


def _png_dimensions(path: Path) -> tuple[int, int]:
    """Validate the PNG chunk stream and return IHDR dimensions."""
    with path.open("rb") as handle:
        if handle.read(8) != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError(f"PyMOL did not create a valid PNG: {path}")
        dimensions = None
        saw_idat = False
        first_chunk = True
        while True:
            chunk_header = handle.read(8)
            if len(chunk_header) != 8:
                raise RuntimeError(f"PNG is truncated before IEND: {path}")
            length, chunk_type = struct.unpack(">I4s", chunk_header)
            data = handle.read(length)
            crc_bytes = handle.read(4)
            if len(data) != length or len(crc_bytes) != 4:
                raise RuntimeError(f"PNG contains a truncated chunk: {path}")
            expected_crc = struct.unpack(">I", crc_bytes)[0]
            actual_crc = zlib.crc32(chunk_type)
            actual_crc = zlib.crc32(data, actual_crc) & 0xFFFFFFFF
            if actual_crc != expected_crc:
                raise RuntimeError(f"PNG contains an invalid chunk checksum: {path}")
            if first_chunk:
                if chunk_type != b"IHDR" or length != 13:
                    raise RuntimeError(f"PNG does not begin with a valid IHDR: {path}")
                dimensions = struct.unpack(">II", data[:8])
                first_chunk = False
            elif chunk_type == b"IHDR":
                raise RuntimeError(f"PNG contains more than one IHDR: {path}")
            if chunk_type == b"IDAT":
                saw_idat = True
            if chunk_type == b"IEND":
                if length != 0 or not saw_idat or dimensions is None:
                    raise RuntimeError(f"PNG has an invalid IEND sequence: {path}")
                return dimensions


def _render_png(
    ctx: Context,
    filename: str,
    width: int = 1200,
    height: int = 1200,
    dpi: float = 300.0,
    ray: bool = True,
    instance: int | None = None,
) -> list[Any]:
    """Render a typed PNG and return both verified metadata and the image."""
    if not (1 <= width <= 10_000 and 1 <= height <= 10_000):
        raise ValueError("Width and height must each be between 1 and 10000")
    if width * height > 64_000_000:
        raise ValueError("Render rejected: output may not exceed 64 megapixels")
    if not (1 <= dpi <= 2400):
        raise ValueError("DPI must be between 1 and 2400")

    path = Path(filename).expanduser().resolve()
    if path.suffix.lower() != ".png":
        raise ValueError("render_png requires a .png filename")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".png", dir=path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    temporary_path.unlink()
    args = {
        "filename": str(temporary_path),
        "width": width,
        "height": height,
        "dpi": dpi,
        "ray": int(ray),
        "quiet": 1,
    }
    try:
        response = get_pymol_connection(instance).send_command(
            "png",
            args,
            source=f"png {path}, width={width}, height={height}, "
            f"dpi={dpi}, ray={int(ray)}, quiet=1",
        )
        output = _direct_output(response).strip()
        try:
            succeeded = float(output) > 0
        except ValueError:
            succeeded = False
        if not succeeded:
            raise RuntimeError(f"PyMOL reported that PNG rendering failed: {output!r}")
        actual_width, actual_height = _png_dimensions(temporary_path)
        if (actual_width, actual_height) != (width, height):
            raise RuntimeError(
                "PyMOL wrote unexpected dimensions: "
                f"{actual_width}x{actual_height}, requested {width}x{height}"
            )
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return [
        f"Rendered {path} ({actual_width}x{actual_height}, {dpi:g} DPI, "
        f"ray={'on' if ray else 'off'})",
        Image(path=path),
    ]


# structured_output=False is required, not stylistic. FastMCP builds an output
# model from the return annotation and serialises the result through pydantic,
# which cannot encode an Image and fails the whole call. Annotating the return
# as list[ContentBlock] does not help -- the str and Image are then rejected by
# validation instead. Suppressing the output model routes the result through
# _convert_to_content, which knows how to turn Image into ImageContent.
@mcp.tool(structured_output=False)
def render_png(
    ctx: Context,
    filename: str,
    width: int = 1200,
    height: int = 1200,
    dpi: float = 300.0,
    ray: bool = True,
    instance: int | None = None,
) -> list[Any]:
    """Render a typed PNG and return both verified metadata and the image."""
    return _render_png(ctx, filename, width, height, dpi, ray, instance)


##############################################################################
# MCP TOOL: list_commands
##############################################################################


def _describe_command(name: str, cmd: CommandDef) -> str:
    """Renders one command's full detail from its definition."""
    lines = [f"{name} -- {cmd.description}", f"  pattern: {cmd.pattern}"]
    for p in cmd.parameters:
        bits = ["required" if p.required else "optional"]
        if p.default is not None:
            bits.append(f"default={p.default}")
        if p.options:
            bits.append("one of: " + ", ".join(p.options))
        lines.append(f"  {p.name} ({'; '.join(bits)})")
    if cmd.composite:
        lines.append("  note: composite -- expands to several PyMOL calls")
    return "\n".join(lines)


@mcp.tool()
def list_instances(ctx: Context) -> str:
    """
    Lists the running PyMOL instances and what each has loaded.

    Each PyMOL claims its own port, so several can run at once. Pass a port as
    `instance` to `parse_and_execute` to drive that specific one. Use this when
    a command reports the choice is ambiguous, or when the user refers to a
    particular window.

    The loaded object names are what distinguish one window from another; a
    port number on its own identifies nothing to a human.
    """
    try:
        instances = discover_instances()
    except Exception as e:
        return f"Instance discovery failed: {e}"

    if not instances:
        return (
            "No PyMOL is listening. Start PyMOL and it will claim a port "
            f"in {PORT_RANGE.start}-{PORT_RANGE.stop - 1} automatically."
        )

    lines = [f"{len(instances)} PyMOL instance(s) running:"]
    for inst in instances:
        objects = ", ".join(inst["objects"]) if inst["objects"] else "nothing loaded"
        pid = f", pid {inst['pid']}" if inst["pid"] else ""
        lines.append(f"  instance={inst['port']}{pid}: {objects}")
    if len(instances) == 1:
        lines.append("\nOnly one, so `instance` can be left unset.")
    else:
        lines.append("\nSeveral running: pass instance=<port> on every command.")
    return "\n".join(lines)


@mcp.tool()
def list_commands(ctx: Context, filter: str = "") -> str:
    """
    Lists the PyMOL commands `parse_and_execute` accepts.

    Without `filter`, returns every command name with a one-line description.
    With `filter` (a substring matched against names and descriptions), returns
    full detail for the matches: the exact regex the input must satisfy, plus
    each parameter's name, whether it is required, its default, and its allowed
    values. Use it to confirm syntax before calling `parse_and_execute`.

    Examples: filter="color" for the colouring commands, filter="cartoon" for
    cartoon-related ones, filter="fetch" for the exact fetch signature.
    """
    if not filter.strip():
        listing = "\n".join(
            f"  {name} -- {cmd.description}"
            for name, cmd in sorted(PYMOL_COMMANDS.items())
        )
        return (
            f"{len(PYMOL_COMMANDS)} commands. Input must be literal PyMOL "
            "syntax, one command per call.\n"
            "Call list_commands with a filter for exact syntax and parameters."
            f"\n\n{listing}"
        )

    needle = filter.strip().lower()
    matches = {
        name: cmd
        for name, cmd in sorted(PYMOL_COMMANDS.items())
        if needle in name.lower() or needle in cmd.description.lower()
    }
    if not matches:
        return (
            f"No command matches '{filter}'. Call list_commands with no filter "
            "to see all available commands."
        )
    return "\n\n".join(_describe_command(n, c) for n, c in matches.items())


##############################################################################
# ENTRY POINT
##############################################################################


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
