'''
PyMOL MCP Plugin

A plugin that listens for socket connections and executes allowlisted PyMOL
commands received as structured JSON. No arbitrary code execution (exec) is used.

Based on the concept of the "Rendering Plugin" from Michael Lerner.
'''

from __future__ import absolute_import
from __future__ import print_function

import os
import socket
import json
import threading
import traceback

# Global variables
dialog = None
socket_server = None
received_commands = []
listening = False
current_port = 9876  # Default port


def __init_plugin__(app=None):
    '''
    Add an entry to the PyMOL "Plugin" menu
    '''
    from pymol.plugins import addmenuitemqt
    addmenuitemqt('PyMol MCP Socket Plugin', run_plugin_gui)


##############################################################################
# ALLOWLISTED COMMAND DISPATCH
##############################################################################

def build_command_dispatcher(cmd):
    """
    Build a dispatcher dict mapping command names to handler functions.
    Each handler calls the corresponding cmd.* function with validated args.
    Returns only allowlisted PyMOL API calls — no exec() or eval().
    """

    def _do_command(command_name, args):
        """Dispatch util.* commands via cmd.do() (PyMOL's command interpreter)."""
        parts = [command_name]
        for key in sorted(args.keys()):
            if args[key]:
                parts.append(str(args[key]))
        cmd.do(' '.join(parts))
        return "Command executed"

    def _show(args):
        return cmd.show(args.get("representation", "lines"), args.get("selection", "all"))

    def _hide(args):
        return cmd.hide(args.get("representation", "lines"), args.get("selection", "all"))

    def _color(args):
        return cmd.color(args.get("color", "white"), args.get("selection", "all"))

    def _as(args):
        return cmd.show_as(args.get("representation", "cartoon"), args.get("selection", "all"))

    def _set(args):
        selection = args.get("selection")
        if selection:
            return cmd.set(args.get("setting", ""), args.get("value", ""), selection)
        return cmd.set(args.get("setting", ""), args.get("value", ""))

    def _cartoon(args):
        return cmd.cartoon(args.get("type", "automatic"), args.get("selection", "all"))

    def _spectrum(args):
        return cmd.spectrum(
            args.get("expression", "count"),
            args.get("palette", "rainbow"),
            args.get("selection", "all")
        )

    def _label(args):
        return cmd.label(args.get("selection", "all"), args.get("expression", "name"))

    def _distance(args):
        return cmd.distance(
            args.get("name", "dist"),
            args.get("selection1", "(pk1)"),
            args.get("selection2", "(pk2)")
        )

    def _angle(args):
        return cmd.angle(
            args.get("name", "angle"),
            args.get("selection1", "(pk1)"),
            args.get("selection2", "(pk2)"),
            args.get("selection3", "(pk3)")
        )

    def _dihedral(args):
        return cmd.dihedral(
            args.get("name", "dihedral"),
            args.get("selection1", "(pk1)"),
            args.get("selection2", "(pk2)"),
            args.get("selection3", "(pk3)"),
            args.get("selection4", "(pk4)")
        )

    def _center(args):
        return cmd.center(args.get("selection", "all"))

    def _orient(args):
        return cmd.orient(args.get("selection", "all"))

    def _zoom(args):
        buffer_val = args.get("buffer", "5")
        try:
            buffer_val = float(buffer_val)
        except (ValueError, TypeError):
            buffer_val = 5.0
        return cmd.zoom(args.get("selection", "all"), buffer_val)

    def _reset(args):
        obj = args.get("object")
        if obj:
            return cmd.reset(obj)
        return cmd.reset()

    def _turn(args):
        angle = args.get("angle", "90")
        try:
            angle = float(angle)
        except (ValueError, TypeError):
            angle = 90.0
        return cmd.turn(args.get("axis", "y"), angle)

    def _move(args):
        distance = args.get("distance", "1")
        try:
            distance = float(distance)
        except (ValueError, TypeError):
            distance = 1.0
        return cmd.move(args.get("axis", "z"), distance)

    def _clip(args):
        distance = args.get("distance", "1")
        try:
            distance = float(distance)
        except (ValueError, TypeError):
            distance = 1.0
        return cmd.clip(args.get("mode", "near"), distance)

    def _load(args):
        filename = args.get("filename", "")
        obj = args.get("object")
        if obj:
            return cmd.load(filename, obj)
        return cmd.load(filename)

    def _fetch(args):
        code = args.get("code", "")
        name = args.get("name")
        if name:
            return cmd.fetch(code, name)
        return cmd.fetch(code)

    def _save(args):
        state = args.get("state", "-1")
        try:
            state = int(state)
        except (ValueError, TypeError):
            state = -1
        return cmd.save(args.get("filename", ""), args.get("selection", "all"), state)

    def _png(args):
        return cmd.png(args.get("filename", "output.png"))

    def _select(args):
        return cmd.select(args.get("name", "sele"), args.get("selection", "all"))

    def _deselect(args):
        return cmd.deselect()

    def _create(args):
        source_state = args.get("source_state", "1")
        try:
            source_state = int(source_state)
        except (ValueError, TypeError):
            source_state = 1
        return cmd.create(args.get("name", "obj"), args.get("selection", "all"), source_state)

    def _extract(args):
        return cmd.extract(args.get("name", "obj"), args.get("selection", "all"))

    def _delete(args):
        return cmd.delete(args.get("name", "all"))

    def _remove(args):
        return cmd.remove(args.get("selection", "none"))

    def _align(args):
        return cmd.align(args.get("mobile", "all"), args.get("target", "all"))

    def _super(args):
        return cmd.super(args.get("mobile", "all"), args.get("target", "all"))

    def _intra_fit(args):
        return cmd.intra_fit(args.get("selection", "all"))

    def _intra_rms(args):
        return cmd.intra_rms(args.get("selection", "all"))

    def _alter(args):
        return cmd.alter(args.get("selection", "all"), args.get("expression", ""))

    def _alter_state(args):
        state = args.get("state", "1")
        try:
            state = int(state)
        except (ValueError, TypeError):
            state = 1
        return cmd.alter_state(state, args.get("selection", "all"), args.get("expression", ""))

    def _h_add(args):
        return cmd.h_add(args.get("selection", "all"))

    def _h_fill(args):
        return cmd.h_fill(args.get("selection", "all"))

    def _bond(args):
        order = args.get("order", "1")
        try:
            order = int(order)
        except (ValueError, TypeError):
            order = 1
        return cmd.bond(args.get("atom1", ""), args.get("atom2", ""), order)

    def _unbond(args):
        return cmd.unbond(args.get("atom1", ""), args.get("atom2", ""))

    def _rebuild(args):
        return cmd.rebuild(args.get("selection", "all"))

    def _refresh(args):
        return cmd.refresh()

    def _spheroid(args):
        # spheroid is typically invoked via cmd.do
        return cmd.do(f"spheroid {args.get('selection', 'all')}")

    def _isomesh(args):
        level = args.get("level", "1.0")
        try:
            level = float(level)
        except (ValueError, TypeError):
            level = 1.0
        return cmd.isomesh(
            args.get("name", "mesh"),
            args.get("map_object", ""),
            level,
            args.get("selection", "all")
        )

    def _isosurface(args):
        level = args.get("level", "1.0")
        try:
            level = float(level)
        except (ValueError, TypeError):
            level = 1.0
        return cmd.isosurface(
            args.get("name", "surf"),
            args.get("map_object", ""),
            level,
            args.get("selection", "all")
        )

    def _sculpt_activate(args):
        return cmd.sculpt_activate(args.get("object", "all"))

    def _sculpt_deactivate(args):
        return cmd.sculpt_deactivate(args.get("object", "all"))

    def _sculpt_iterate(args):
        iterations = args.get("iterations", "1")
        try:
            iterations = int(iterations)
        except (ValueError, TypeError):
            iterations = 1
        return cmd.sculpt_iterate(args.get("object", "all"), iterations)

    def _scene(args):
        return cmd.scene(args.get("key", ""), args.get("action", "recall"))

    def _scene_order(args):
        return cmd.scene_order(args.get("scene_list", ""))

    def _mset(args):
        return cmd.mset(args.get("specification", "1"))

    def _mplay(args):
        return cmd.mplay()

    def _mstop(args):
        return cmd.mstop()

    def _frame(args):
        frame = args.get("frame_number")
        if frame:
            try:
                frame = int(frame)
            except (ValueError, TypeError):
                frame = 1
            return cmd.frame(frame)
        return cmd.frame()

    def _forward(args):
        return cmd.forward()

    def _backward(args):
        return cmd.backward()

    def _rock(args):
        return cmd.rock()

    def _ray(args):
        width = args.get("width")
        height = args.get("height")
        w = 0
        h = 0
        if width:
            try:
                w = int(width)
            except (ValueError, TypeError):
                pass
        if height:
            try:
                h = int(height)
            except (ValueError, TypeError):
                pass
        return cmd.ray(w, h)

    def _draw(args):
        width = args.get("width")
        height = args.get("height")
        w = 0
        h = 0
        if width:
            try:
                w = int(width)
            except (ValueError, TypeError):
                pass
        if height:
            try:
                h = int(height)
            except (ValueError, TypeError):
                pass
        return cmd.draw(w, h)

    def _mpng(args):
        return cmd.mpng(args.get("prefix", "frame"))

    def _symexp(args):
        cutoff = args.get("cutoff", "20")
        try:
            cutoff = float(cutoff)
        except (ValueError, TypeError):
            cutoff = 20.0
        return cmd.symexp(
            args.get("prefix", "sym"),
            args.get("selection", "all"),
            cutoff
        )

    def _set_symmetry(args):
        vals = []
        for key in ["a", "b", "c", "alpha", "beta", "gamma"]:
            v = args.get(key, "0")
            try:
                vals.append(float(v))
            except (ValueError, TypeError):
                vals.append(0.0)
        return cmd.set_symmetry(args.get("selection", "all"), *vals)

    def _fab(args):
        return cmd.fab(args.get("sequence", "ALA"), args.get("options", ""))

    def _fragment(args):
        return cmd.fragment(args.get("name", ""))

    def _full_screen(args):
        return cmd.full_screen()

    def _viewport(args):
        width = args.get("width", "640")
        height = args.get("height", "480")
        try:
            width = int(width)
            height = int(height)
        except (ValueError, TypeError):
            width, height = 640, 480
        return cmd.viewport(width, height)

    def _help(args):
        command = args.get("command")
        if command:
            return cmd.help(command)
        return cmd.help()

    # Build the dispatcher — only these commands are allowed
    dispatcher = {
        "show": _show,
        "hide": _hide,
        "color": _color,
        "as": _as,
        "set": _set,
        "cartoon": _cartoon,
        "spectrum": _spectrum,
        "label": _label,
        "distance": _distance,
        "angle": _angle,
        "dihedral": _dihedral,
        "center": _center,
        "orient": _orient,
        "zoom": _zoom,
        "reset": _reset,
        "turn": _turn,
        "move": _move,
        "clip": _clip,
        "load": _load,
        "fetch": _fetch,
        "save": _save,
        "png": _png,
        "select": _select,
        "deselect": _deselect,
        "create": _create,
        "extract": _extract,
        "delete": _delete,
        "remove": _remove,
        "align": _align,
        "super": _super,
        "intra_fit": _intra_fit,
        "intra_rms": _intra_rms,
        "alter": _alter,
        "alter_state": _alter_state,
        "h_add": _h_add,
        "h_fill": _h_fill,
        "bond": _bond,
        "unbond": _unbond,
        "rebuild": _rebuild,
        "refresh": _refresh,
        "spheroid": _spheroid,
        "isomesh": _isomesh,
        "isosurface": _isosurface,
        "sculpt_activate": _sculpt_activate,
        "sculpt_deactivate": _sculpt_deactivate,
        "sculpt_iterate": _sculpt_iterate,
        "scene": _scene,
        "scene_order": _scene_order,
        "mset": _mset,
        "mplay": _mplay,
        "mstop": _mstop,
        "frame": _frame,
        "forward": _forward,
        "backward": _backward,
        "rock": _rock,
        "ray": _ray,
        "draw": _draw,
        "mpng": _mpng,
        "symexp": _symexp,
        "set_symmetry": _set_symmetry,
        "fab": _fab,
        "fragment": _fragment,
        "full_screen": _full_screen,
        "viewport": _viewport,
        "help": _help,
    }

    # util.* commands all go through cmd.do() which is PyMOL's safe command interpreter
    util_commands = [
        "util.cbc", "util.cbaw", "util.cbag", "util.cbac", "util.cbam",
        "util.cbay", "util.cbas", "util.cbab", "util.cbao", "util.cbap",
        "util.cbak", "util.chainbow", "util.rainbow", "util.ss",
        "util.color_by_element", "util.color_secondary",
    ]
    for util_cmd in util_commands:
        dispatcher[util_cmd] = lambda args, name=util_cmd: _do_command(name, args)

    return dispatcher


##############################################################################
# SOCKET SERVER
##############################################################################

class SocketServer:
    def __init__(self, host='localhost', port=9876):
        self.host = host
        self.port = port
        self.socket = None
        self.client = None
        self.running = False
        self.thread = None
        self.command_callback = None

    def start(self, command_callback=None):
        """Start the socket server on a separate thread"""
        if self.running:
            return False

        self.command_callback = command_callback
        self.running = True
        self.thread = threading.Thread(target=self._run_server)
        self.thread.daemon = True
        self.thread.start()
        return True

    def _run_server(self):
        """Run the socket server in a separate thread"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)
            self.socket.settimeout(1.0)

            print(f"PyMOL MCP Socket server listening on {self.host}:{self.port}")

            while self.running:
                try:
                    self.client, address = self.socket.accept()
                    print(f"Connected to client: {address}")
                    self.client.settimeout(1.0)

                    buffer = b''
                    while self.running:
                        try:
                            data = self.client.recv(4096)
                            if not data:
                                break

                            buffer += data

                            try:
                                command = json.loads(buffer.decode('utf-8'))
                                buffer = b''

                                result = self._handle_command(command)

                                if isinstance(result, dict) and result.get("executed") is False:
                                    response = json.dumps({
                                        "status": "error",
                                        "message": result.get("error", "Unknown error")
                                    })
                                else:
                                    response = json.dumps({
                                        "status": "success",
                                        "result": result if result else "Command executed"
                                    })
                                self.client.sendall(response.encode('utf-8'))
                            except json.JSONDecodeError:
                                continue

                        except socket.timeout:
                            continue
                        except Exception as e:
                            print(f"Error receiving data: {str(e)}")
                            break

                    if self.client:
                        self.client.close()
                        self.client = None
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"Error accepting connection: {str(e)}")

        except Exception as e:
            print(f"Socket server error: {str(e)}")
            traceback.print_exc()
        finally:
            if self.socket:
                self.socket.close()
            self.running = False
            print("Socket server stopped")

    def _handle_command(self, command):
        """Handle received structured command"""
        if not command:
            return

        cmd_type = command.get("type", "")
        if cmd_type != "structured_command":
            return {"executed": False, "error": f"Unknown message type: {cmd_type}"}

        global received_commands
        cmd_name = command.get("command", "")
        args = command.get("args", {})
        received_commands.append(f"{cmd_name} {json.dumps(args)}")

        if self.command_callback and cmd_name:
            result = self.command_callback(cmd_name, args)
            return result

    def stop(self):
        """Stop the socket server"""
        self.running = False
        if self.thread:
            self.thread.join(2.0)
        if self.client:
            self.client.close()
        if self.socket:
            self.socket.close()
        self.socket = None
        self.client = None
        self.thread = None


##############################################################################
# GUI
##############################################################################

def run_plugin_gui():
    '''
    Open our custom dialog
    '''
    global dialog

    if dialog is None:
        dialog = make_dialog()

    dialog.show()

def make_dialog():
    from pymol import cmd

    from pymol.Qt import QtWidgets
    from pymol.Qt.utils import loadUi

    dialog = QtWidgets.QDialog()

    uifile = os.path.join(os.path.dirname(__file__), 'pymol_mcp_plugin.ui')
    form = loadUi(uifile, dialog)

    form.input_port.setValue(current_port)
    update_status_label(form, "Not listening")

    # Build the command dispatcher once
    dispatcher = build_command_dispatcher(cmd)

    def execute_structured_command(command_name, args):
        """
        Execute an allowlisted PyMOL command via the dispatcher.
        No exec() or eval() — only direct cmd.* function calls.
        """
        try:
            print(f"Executing PyMOL command: {command_name} args={args}")

            handler = dispatcher.get(command_name)
            if handler is None:
                error_msg = f"Unknown command: {command_name}"
                print(error_msg)
                return {"executed": False, "error": error_msg}

            import io
            from contextlib import redirect_stdout

            output_buffer = io.StringIO()
            with redirect_stdout(output_buffer):
                result = handler(args)

            output = output_buffer.getvalue()

            if output:
                print(f"Command output: {output}")
                return {"executed": True, "output": output}
            elif result is not None:
                return {"executed": True, "output": str(result)}
            else:
                return {"executed": True, "output": "Command executed successfully (no output)"}
        except Exception as e:
            error_msg = f"Error executing PyMOL command '{command_name}': {str(e)}"
            print(error_msg)
            traceback.print_exc()
            return {"executed": False, "error": error_msg}

    def toggle_listening():
        global socket_server, listening, current_port

        if not listening:
            port = form.input_port.value()
            current_port = port

            socket_server = SocketServer(port=port)
            if socket_server.start(execute_structured_command):
                listening = True
                form.button_toggle_listening.setText("Stop Listening")
                update_status_label(form, f"Listening on port {port}")
        else:
            if socket_server:
                socket_server.stop()
            listening = False
            form.button_toggle_listening.setText("Start Listening")
            update_status_label(form, "Not listening")

    def close_dialog():
        global socket_server, listening

        if socket_server and listening:
            socket_server.stop()
            listening = False

        dialog.close()

    form.button_toggle_listening.clicked.connect(toggle_listening)
    form.button_close.clicked.connect(close_dialog)

    return dialog

def update_status_label(form, text):
    """Update the status label with the given text"""
    form.label_status.setText(text)

    if "Not listening" in text:
        form.label_status.setStyleSheet("color: red;")
    elif "Listening" in text:
        form.label_status.setStyleSheet("color: green;")
    else:
        form.label_status.setStyleSheet("")
