"""Offline tests for the minimal stock MESH_TEST=3 nine-point replacement.

Uses captured installed Klipper parsing, macros, offsets, and virtual SD code.
Hardware, vendor cleaning/parking and temperature waits are test doubles.
Never connects to the printer. Run this file, not the superseded draft suite.
"""
import configparser
import copy
import json
import unittest

from harness import AUDIT, ROOT, Harness, SNAPSHOT, config_native, gcode

OVERRIDE = ROOT.parent / "macros/stock_mesh_average.cfg"
VENDOR = json.loads((AUDIT / "vendor-mesh-macros.json").read_text(encoding="utf-8"))


class StockAverageTests(unittest.TestCase):
    def harness(self): return Harness(OVERRIDE, stock_mode=True)

    def run_hook(self, h):
        h.run_sd("_MESH_TEST\nLINE_PURGE\nTEST_PRINT")

    def assert_stopped(self, h):
        self.assertEqual((h.purge_calls, h.print_calls), (0, 0))
        self.assertEqual(h.objects["extruder"].data["target"], 0.)
        self.assertEqual(h.objects["heater_bed"].data["target"], 0.)
        self.assertFalse(h.sd.is_active())

    def test_only_two_vendor_macros_are_overridden(self):
        cp = configparser.RawConfigParser()
        cp.read(OVERRIDE, encoding="utf-8")
        self.assertEqual(set(cp.sections()), {"gcode_macro _FIND_POINT", "gcode_macro _TEST_POINT"})
        h = self.harness()
        for name in ("_PROBE_POINT", "_MESH_TEST", "_START_PRINT"):
            key = ("gcode_macro " + name).lower()
            self.assertEqual(h.macro_values[key]["gcode"], VENDOR[key]["gcode"])
        self.assertEqual(h.settings["virtual_sdcard"], SNAPSHOT["configfile"]["settings"]["virtual_sdcard"])

    def test_stock_search_dispatches_1089_center_first_calls(self):
        h = Harness(stock_mode=True)
        calls = []
        h.gcode.register_command("_PROBE_POINT", None)
        h.gcode.register_command("_PROBE_POINT", lambda cmd: calls.append(cmd.get_command_parameters().copy()))
        h.run_sd("_MESH_TEST")
        self.assertEqual(len(calls), 1089)
        self.assertAlmostEqual(float(calls[0]["X"]), 107.36)
        self.assertAlmostEqual(float(calls[0]["Y"]), 107.36)

    def test_stock_probe_point_flag_skips_later_calls(self):
        h = Harness(stock_mode=True)
        node = SNAPSHOT["bed_mesh"]["probed_matrix"][16][16]
        h.run_sd(f"SAVE_GCODE_STATE NAME=_mesh_test\n_PROBE_POINT X=107.36 Y=107.36 Z={node}\n_PROBE_POINT X=107.36 Y=107.36 Z={node}")
        self.assertEqual(h.probe_calls, 1)

    def test_nine_nodes_one_clean_one_adjustment_one_reheat(self):
        h = self.harness()
        h.deltas = [-.02, .01, -.03, .005, -.015, .025, -.04, .02, 0.]
        calls = []
        original = h.gcode.ready_gcode_handlers["_PROBE_POINT"]
        h.gcode.ready_gcode_handlers["_PROBE_POINT"] = lambda cmd: (calls.append(cmd.get_commandline()), original(cmd))[-1]
        self.run_hook(h)
        self.assertEqual(len(calls), 9)
        self.assertEqual(h.probe_calls, 9, h.messages)
        self.assertEqual(h.commands.count("_ORIG_CLEAR_NOZZLE"), 1)
        self.assertEqual(h.reheat_calls, 1)
        self.assertAlmostEqual(h.move.homing_position[2], -.085)
        self.assertAlmostEqual(h.objects["gcode_macro _TEST_POINT"].variables["temp_z_offset"], -.005)
        self.assertEqual((h.purge_calls, h.print_calls), (1, 1))
        self.assertEqual(h.objects["save_variables"].data["variables"]["gcode_offsets"]["z"], -.08)
        coords = [(float(line.split("X=")[1].split()[0]), float(line.split("Y=")[1].split()[0])) for line in calls]
        expected = [(x, y) for y in (53.68, 107.36, 161.04) for x in (53.68, 107.36, 161.04)]
        for actual, target in zip(coords, expected):
            self.assertAlmostEqual(actual[0], target[0])
            self.assertAlmostEqual(actual[1], target[1])

    def test_equal_deltas_match_stock_single_point_result(self):
        original = Harness(stock_mode=True)
        averaged = self.harness()
        original.deltas = averaged.deltas = [-.08] * 9
        node = SNAPSHOT["bed_mesh"]["probed_matrix"][16][16]
        original.run_sd(f"SAVE_GCODE_STATE NAME=_mesh_test\n_PROBE_POINT X=107.36 Y=107.36 Z={node}")
        self.run_hook(averaged)
        self.assertAlmostEqual(averaged.move.homing_position[2], original.move.homing_position[2])
        self.assertAlmostEqual(averaged.move.homing_position[2], -.16)

    def test_arithmetic_mean_uses_all_nine_results(self):
        h = self.harness()
        h.deltas = [.2] + [0.] * 8
        self.run_hook(h)
        self.assertEqual(h.print_calls, 1)
        self.assertAlmostEqual(h.move.homing_position[2], -.08 + .2 / 9)

    def test_mesh_shape_is_subtracted_at_each_location(self):
        h = self.harness()
        h.deltas = [0.] * 9
        self.run_hook(h)
        self.assertAlmostEqual(h.move.homing_position[2], -.08)
        self.assertEqual(h.objects["bed_mesh"].data, SNAPSHOT["bed_mesh"])

    def test_stock_limit_checked_before_averaging(self):
        for delta in (.310001, -.310001, float("nan"), float("inf")):
            with self.subTest(delta=delta):
                h = self.harness()
                h.deltas = [delta] + [0.] * 8
                self.run_hook(h)
                self.assertEqual(h.probe_calls, 1)
                self.assertEqual(h.reheat_calls, 0)
                self.assertAlmostEqual(h.move.homing_position[2], -.08)
                self.assert_stopped(h)

    def test_values_inside_stock_limit_pass(self):
        for delta in (.309999, -.309999):
            with self.subTest(delta=delta):
                h = self.harness()
                h.deltas = [delta] * 9
                self.run_hook(h)
                self.assertEqual(h.print_calls, 1, h.messages)
                self.assertAlmostEqual(h.move.homing_position[2], -.08 + delta)

    def test_probe_error_stops_before_recording(self):
        h = self.harness()
        h.fail_probe = True
        self.run_hook(h)
        self.assertEqual(h.objects["gcode_macro _TEST_POINT"].variables["average_deltas"], [])
        self.assertEqual(h.cancel_calls, 0)  # retain stock SD error behavior
        self.assert_stopped(h)

    def test_missing_point_fails_final_count(self):
        h = self.harness()
        original = h.gcode.ready_gcode_handlers["_PROBE_POINT"]
        calls = []
        def skip_one(cmd):
            calls.append(True)
            if len(calls) != 5: original(cmd)
        h.gcode.ready_gcode_handlers["_PROBE_POINT"] = skip_one
        self.run_hook(h)
        self.assertEqual(h.probe_calls, 8)
        self.assertAlmostEqual(h.move.homing_position[2], -.08)
        self.assert_stopped(h)

    def test_duplicate_finalize_cannot_apply_twice(self):
        h = self.harness()
        h.deltas = [.02] * 9
        h.run_sd("_MESH_TEST\n_TEST_POINT FINALIZE=1\nLINE_PURGE\nTEST_PRINT")
        self.assertAlmostEqual(h.move.homing_position[2], -.06)
        self.assert_stopped(h)

    def test_reheat_exception_stops_stock_startup(self):
        h = self.harness()
        h.fail_reheat = True
        self.run_hook(h)
        self.assertEqual(h.probe_calls, 9)
        self.assert_stopped(h)

    def test_repeated_startups_keep_native_global_offset_loading(self):
        h = self.harness()
        h.deltas = [.02] * 9
        h.run_sd("_START_PRINT\nTEST_PRINT")
        h.run_sd("_START_PRINT\nTEST_PRINT")
        self.assertEqual(h.probe_calls, 18, h.messages)
        self.assertEqual(h.print_calls, 2)
        self.assertAlmostEqual(h.move.homing_position[2], -.06)
        h.gcode.run_script("SET_GCODE_OFFSET Z_ADJUST=0.005")
        self.assertAlmostEqual(h.objects["save_variables"].data["variables"]["gcode_offsets"]["z"], -.075)

    def test_native_parser_loads_staged_include_and_keeps_probe_settings(self):
        parser = config_native.PrinterConfig.__new__(config_native.PrinterConfig)
        parser.printer = self.harness()
        merged = parser._build_config_wrapper("[include user.cfg.stock_average]\n", str(ROOT / "test-parent.cfg")).fileconfig
        self.assertEqual(merged.get("probe", "first_probe_speed"), "5")
        self.assertEqual(merged.get("probe", "samples"), "3")
        self.assertEqual(merged.get("probe", "samples_tolerance"), "0.02")
        self.assertTrue(merged.has_section("bed_mesh merged_33x33_85"))
        self.assertFalse(merged.has_section("gcode_macro _AUTOZ_STATE"))
        self.assertFalse(merged.has_section("gcode_macro _START_PRINT"))

    def test_missing_mesh_rejects_hook(self):
        h = self.harness()
        h.objects["bed_mesh"].data["profile_name"] = ""
        h.run_sd("_FIND_POINT\nLINE_PURGE\nTEST_PRINT")
        self.assertEqual(h.probe_calls, 0)
        self.assert_stopped(h)


if __name__ == "__main__":
    unittest.main(verbosity=2)
