"""Typed operations replace unsafe expression construction and comma splitting."""

from unittest.mock import MagicMock

import pytest
from conftest import load_plugin

from pymol_mcp import server
from pymol_mcp.api import Selector


@pytest.mark.parametrize("vector", [[1, 2], [1, 2, 3, 4], [1, 2, float('nan')],
                                   [True, 0, 0], "[1,2,3]"])
def test_plugin_rejects_invalid_translation_before_mutation(vector):
    cmd = MagicMock()
    dispatch = load_plugin("bad_translation").build_command_dispatcher(cmd)
    with pytest.raises(ValueError):
        dispatch["translate"]({"selection": "all", "vector": vector})
    cmd.translate.assert_not_called()


def test_translate_uses_model_axes_and_explicit_state():
    cmd = MagicMock()
    cmd.count_atoms.return_value = 2
    dispatch = load_plugin("translation").build_command_dispatcher(cmd)
    data = dispatch["translate"]({"selection": "chain A", "vector": [1, 2, 3],
                                 "state": 2})
    cmd.translate.assert_called_once_with([1.0, 2.0, 3.0], selection="chain A",
                                          state=2, camera=0)
    assert data["atoms"] == 2
    assert data["state"] == 2


def test_empty_translation_does_not_mutate():
    cmd = MagicMock()
    cmd.count_atoms.return_value = 0
    dispatch = load_plugin("empty_translation").build_command_dispatcher(cmd)
    with pytest.raises(ValueError, match="no selected atoms"):
        dispatch["translate"]({"selection": "none", "vector": [1, 0, 0]})
    cmd.translate.assert_not_called()


@pytest.mark.parametrize("scope,selection", [("global", Selector(object="model")),
    ("object", Selector(chain="A")), ("atom", None)])
def test_setting_scope_errors_happen_before_sending(monkeypatch, scope, selection):
    connect = MagicMock()
    monkeypatch.setattr(server, "get_pymol_connection", connect)
    with pytest.raises(ValueError):
        server._set_setting("label_position", [1, 2, 3], scope, selection)
    connect.assert_not_called()


def test_plugin_atom_scope_wraps_bare_object_and_preserves_vector():
    cmd = MagicMock()
    dispatch = load_plugin("vector_setting").build_command_dispatcher(cmd)
    dispatch["set"]({"setting": "label_position", "value": [1, 2, 3],
                     "selection": "model", "scope": "atom"})
    cmd.set.assert_called_once_with("label_position", [1.0, 2.0, 3.0], "(model)")


def test_plugin_rejects_object_scope_that_is_a_selection():
    cmd = MagicMock()
    cmd.get_names.return_value = ["model"]
    dispatch = load_plugin("object_setting").build_command_dispatcher(cmd)
    with pytest.raises(ValueError, match="existing object"):
        dispatch["set"]({"setting": "label_position", "value": [1, 2, 3],
                         "selection": "model and chain A", "scope": "object"})
    cmd.set.assert_not_called()


@pytest.mark.parametrize("state", [-1, True, 1.5])
def test_translate_rejects_invalid_state_before_sending(monkeypatch, state):
    connect = MagicMock()
    monkeypatch.setattr(server, "get_pymol_connection", connect)
    with pytest.raises(ValueError):
        server._translate(Selector(object="model"), [1, 2, 3], state)
    connect.assert_not_called()
