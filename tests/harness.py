"""Offline replay using downloaded, unmodified installed Klipper Python code.

No network connections. Motion, heaters, and printer hardware are test doubles.
The native parser, macro renderer/dispatch, offset state and virtual-SD error
handler execute locally. This does not simulate mechanics or MCU scheduling.
"""
import ast
import configparser
import copy
import importlib.util
import io
import json
import logging
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parent
AUDIT = ROOT / "fixtures"
SOURCE = ROOT / "vendor/klippy"
SNAPSHOT = json.loads((AUDIT / "mesh.json").read_text(encoding="utf-8-sig"))
RUNTIME = json.loads((AUDIT / "runtime.json").read_text(encoding="utf-8-sig"))
logging.disable(logging.CRITICAL)


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gcode = module("installed_gcode", SOURCE / "gcode.py")
macro = module("installed_gcode_macro", SOURCE / "extras/gcode_macro.py")
move = module("installed_gcode_move", SOURCE / "extras/gcode_move.py")
virtual = module("installed_virtual_sdcard", SOURCE / "extras/virtual_sdcard.py")
package = types.ModuleType("installed_extras")
package.__path__ = [str(SOURCE / "extras")]
sys.modules[package.__name__] = package
sys.modules.setdefault("pins", types.ModuleType("pins"))
sys.modules["installed_extras.manual_probe"] = types.ModuleType("installed_extras.manual_probe")
probe_native = module("installed_extras.probe", SOURCE / "extras/probe.py")
bed_native = module("installed_extras.bed_mesh", SOURCE / "extras/bed_mesh.py")
config_native = module("installed_configfile", SOURCE / "configfile.py")


class Config:
    error = gcode.CommandError

    def __init__(self, printer, name, values=None):
        self.printer, self.name, self.values = printer, name, values or {}

    def get_printer(self): return self.printer
    def get_name(self): return self.name
    def get(self, name, default=None): return self.values.get(name, default)
    def getfloat(self, name, default=None, **kwargs): return float(self.get(name, default))
    def get_prefix_options(self, prefix): return [k for k in self.values if k.startswith(prefix)]


class Mutex:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def test(self): return False


class Reactor:
    NOW, NEVER = 0, 999999999
    def mutex(self): return Mutex()
    def monotonic(self): return 1.0
    def pause(self, t): return t
    def unregister_timer(self, timer): pass
    def register_timer(self, *args): return object()


class Status:
    def __init__(self, data): self.data = copy.deepcopy(data)
    def get_status(self, eventtime=None): return self.data


class Stats(Status):
    def note_start(self): self.data["state"] = "printing"
    def note_error(self, error): self.data.update(state="error", message=error)
    def note_pause(self): self.data["state"] = "paused"
    def note_complete(self): self.data["state"] = "complete"
    def note_cancel(self): self.data["state"] = "cancelled"
    def reset(self): pass


class Toolhead(Status):
    def __init__(self, printer):
        super().__init__(RUNTIME["toolhead"])
        self.printer = printer
        self.data["homed_axes"] = "xyz"
        for name in ("axis_minimum", "axis_maximum"):
            self.data[name] = gcode.Coord(*self.data[name])
        self.pos = [52.5, 10., 5., 0.]

    def get_position(self): return self.pos[:]
    def move(self, pos, speed):
        self.pos = list(pos)
        self.printer.motion.append((list(pos), speed))

    def manual_move(self, pos, speed):
        target = [v if v is not None else self.pos[i] for i, v in enumerate(pos)]
        target += self.pos[len(target):]
        self.move(target, speed)
        self.printer.send_event("toolhead:manual_move")


class Harness:
    command_error = config_error = gcode.CommandError

    def __init__(self, override=None, stock_mode=False):
        self.objects, self.events = {}, {}
        self.reactor = Reactor()
        self.motion, self.messages, self.commands = [], [], []
        self.cancel_calls = self.purge_calls = self.print_calls = self.corrections = 0
        self.probe_calls = self.reheat_calls = 0
        self.fail_probe = self.fail_reheat = self.fail_cancel = False
        self.cancel_during_probe = False
        self.deltas = [-.0125, -.0025, -.01, -.025, -.01, -.015]
        self.settings = copy.deepcopy(SNAPSHOT["configfile"]["settings"])
        if stock_mode:
            self.settings.update(json.loads((AUDIT / "vendor-mesh-macros.json").read_text(encoding="utf-8")))
        for name, data in {**SNAPSHOT, **RUNTIME}.items():
            self.objects[name] = Status(data)
        # Runtime fixture: an active startup, with the verified saved mesh and offset.
        self.objects["print_stats"] = Stats(SNAPSHOT["print_stats"])
        self.objects["heaters"] = Status({})
        self.objects["toolhead"] = Toolhead(self)
        self.objects["gcode"] = self.gcode = gcode.GCodeDispatch(self)
        self.gcode.register_output_handler(self.messages.append)
        self.objects["gcode_macro"] = macro.PrinterGCodeMacro(Config(self, "gcode_macro"))
        self.objects["gcode_move"] = self.move = move.GCodeMove(Config(self, "gcode_move"))
        # Snapshot carries all active macro bodies. Only dependencies under test
        # are interpreted; vendor motion/IFS procedures are recorded test doubles.
        names = [] if stock_mode else [k for k in self.settings if k.startswith("gcode_macro _autoz")]
        names += (["gcode_macro _probe_point"] if stock_mode else ["gcode_macro autoz_check"])
        names += ["gcode_macro _find_point", "gcode_macro _mesh_test",
                  "gcode_macro _start_print", "gcode_macro _set_gcode_offset_fast",
                  "gcode_macro set_gcode_offset", "gcode_macro _test_point"]
        self.macro_values = {k: copy.deepcopy(self.settings[k]) for k in names}
        if override:
            cp = configparser.RawConfigParser(strict=False)
            cp.read(override, encoding="utf-8")
            for sec in cp.sections():
                values = dict(cp.items(sec))
                if sec.lower().startswith("gcode_macro "):
                    key = sec.lower()
                    self.macro_values[key] = {**self.settings.get(key, {}), **values}
                    self.settings[key] = self.macro_values[key]
                elif sec == "virtual_sdcard":
                    self.settings[sec].update(values)
        for key, values in self.macro_values.items():
            name = "gcode_macro " + key.split(" ", 1)[1].upper()
            self.objects[name] = macro.GCodeMacro(Config(self, name, values))
        self.objects["gcode_macro _START_PRINT"].variables.update(RUNTIME["gcode_macro _START_PRINT"])
        self.objects["configfile"] = Status({"settings": self.settings})
        self.objects["virtual_sdcard"] = self.sd = virtual.VirtualSD(
            Config(self, "virtual_sdcard", self.settings["virtual_sdcard"]))
        # Native low-level offset command is renamed by the real macro loader.
        for handler in self.events.get("klippy:connect", []): handler()
        callbacks = {"PROBE": self.probe, "TURN_OFF_HEATERS": self.off,
                     "CANCEL_PRINT": self.cancel, "LINE_PURGE": self.purge,
                     "_WAIT_TEMP": self.reheat, "_ORIG_CLEAR_NOZZLE": self.clean,
                     "LOAD_GCODE_OFFSET": self.load_offset, "SAVE_VARIABLE": self.save_variable,
                     "TEST_PRINT": self.print_marker, "M104": self.set_nozzle,
                     "M140": self.set_bed}
        dependencies = ["RESPOND", "M400", "LOAD_CELL_TARE", "_STOP_FAN", "_PREPARE_PRINT",
                        "CLEAR_PAUSE", "ZCONTROL_AUTO", "ZCONTROL_ON", "ZSSH_RELOAD", "CHECK_MD5",
                        "SET_SKEW", "_G28", "BED_MESH_CLEAR", "BED_MESH_PROFILE", "_START_MESH",
                        "_UGOL_PARK", "_PRINT_CLEAR_NOZZLE", "_SMART_PARK", "_PRINT_MESH",
                        "_TEST_MIN_MAX", "EXCLUDE_OBJECT_DEFINE", "ZEXCLUDE", "SET_PIN"]
        for name in dependencies:
            callbacks.setdefault(name, lambda cmd: None)
        # Keep native macro variable handler for cancellation flag assignment.
        if "gcode_macro _CANCEL_PRINT" not in ["gcode_macro " + n.split(" ")[1].upper() for n in self.macro_values]:
            obj = macro.GCodeMacro(Config(self, "gcode_macro _CANCEL_PRINT", {
                "gcode": "", "variable_cancel_send": "0"}))
            self.objects["gcode_macro _CANCEL_PRINT"] = obj
        for name, callback in callbacks.items():
            if name not in self.gcode.ready_gcode_handlers:
                def record(cmd, name=name, callback=callback):
                    self.commands.append(name)
                    return callback(cmd)
                self.gcode.register_command(name, record)
        self.send_event("klippy:ready")
        self.gcode.run_script("_SET_GCODE_OFFSET Z=-0.08")
        self.objects["extruder"].data["target"] = 121.
        self.objects["heater_bed"].data["target"] = 55.
        self.arm_file()

    def lookup_object(self, name, default=...):
        if default is ...: return self.objects[name]
        return self.objects.get(name, default)
    def lookup_objects(self): return list(self.objects.items())
    def load_object(self, config, name): return self.objects[name]
    def get_reactor(self): return self.reactor
    def get_start_args(self): return {}
    def register_event_handler(self, event, func): self.events.setdefault(event, []).append(func)
    def send_event(self, event, *args):
        for fn in self.events.get(event, []): fn(*args)
    def invoke_shutdown(self, msg): raise AssertionError(msg)
    def request_exit(self, result): raise AssertionError(result)
    def arm_file(self, script=""):
        self.sd.current_file = io.StringIO(script)
        self.sd.current_file.name = "/usr/data/gcodes/test.gcode"
        self.sd.file_position = self.sd.next_file_position = 0
        self.sd.file_size = len(script)
        self.sd.work_timer = object()
        self.sd.must_pause_work = False
        self.objects["print_stats"].note_start()

    def run_sd(self, script):
        self.arm_file(script + "\n")
        self.sd.work_handler(1.)

    def state(self): return self.objects["gcode_macro _AUTOZ_STATE"].variables
    def off(self, cmd):
        self.objects["extruder"].data["target"] = 0.
        self.objects["heater_bed"].data["target"] = 0.
    def cancel(self, cmd):
        self.cancel_calls += 1
        if self.fail_cancel: raise gcode.CommandError("injected cancel helper failure")
        self.sd.do_cancel()
    def purge(self, cmd): self.purge_calls += 1
    def print_marker(self, cmd): self.print_calls += 1
    def set_nozzle(self, cmd): self.objects["extruder"].data["target"] = cmd.get_float("S")
    def set_bed(self, cmd): self.objects["heater_bed"].data["target"] = cmd.get_float("S")
    def save_variable(self, cmd):
        self.objects["save_variables"].data["variables"][cmd.get("VARIABLE")] = ast.literal_eval(cmd.get("VALUE"))
    def load_offset(self, cmd):
        value = self.objects["save_variables"].data["variables"]["gcode_offsets"]["z"]
        self.gcode.run_script_from_command(f"_SET_GCODE_OFFSET Z={value}")
    def clean(self, cmd):
        # Vendor cleaner restores the entry offset and loaded mesh. Its real
        # hardware motions/heating are intentionally outside this simulation.
        pass
    def reheat(self, cmd):
        self.reheat_calls += 1
        if self.fail_reheat: raise gcode.CommandError("injected reheat failure")
        self.objects["extruder"].data["target"] = cmd.get_float("EXTRUDER_TEMP")
        self.objects["heater_bed"].data["target"] = cmd.get_float("BED_TEMP")
    def probe(self, cmd):
        if self.fail_probe: raise gcode.CommandError("Probe samples exceed samples_tolerance")
        if self.cancel_during_probe:
            self.sd.do_cancel()
        index = self.probe_calls
        self.probe_calls += 1
        x, y = self.objects["toolhead"].get_position()[:2]
        bm = SNAPSHOT["bed_mesh"]
        ix, iy = round(x / 6.71), round(y / 6.71)
        expected = bm["probed_matrix"][iy][ix] - .25
        self.objects["probe"].data["last_z_result"] = expected + self.deltas[index % len(self.deltas)]


