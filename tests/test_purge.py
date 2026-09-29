"""Exercise the installed Klipper renderer/config parser; no printer/network access."""
import ast
import configparser
import copy
import io
import json
from pathlib import Path
import random
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT.parent/'extras'))
from ad5x_purge import AD5XPurge
from harness import Config, Harness, Reactor, Status, Toolhead, gcode, macro, move, config_native

LIVE = json.loads((ROOT / 'fixtures/purge.json').read_text())
RAW = configparser.RawConfigParser(interpolation=None)
RAW.read(ROOT.parent / 'macros/safe_line_purge.cfg')


class PurgeHarness(Harness):
    def __init__(self, bounds=(70., 80., 140., 160.), native=True):
        self.objects, self.events = {}, {}
        self.reactor = Reactor()
        self.motion, self.messages, self.commands = [], [], []
        self.sensor_enabled = True
        self.retracts = 0
        self.accel = 20000.
        for name, value in LIVE.items(): self.objects[name] = Status(value)
        self.objects['virtual_sdcard'].data['file_path'] = None
        for name, value in LIVE['configfile']['config'].items():
            if name.startswith('gcode_macro '):
                self.objects[name] = Status({k[9:]:ast.literal_eval(v) for k,v in value.items() if k.startswith('variable_')})
        self.objects['toolhead'] = Toolhead(self)
        self.objects['toolhead'].data.update(axis_minimum=gcode.Coord(-20,-20,-10,0), axis_maximum=gcode.Coord(225,232,230,0), max_velocity=600., max_accel=self.accel)
        self.objects['extruder'].data.update(can_extrude=True, target=250., temperature=250.)
        self.objects['gcode'] = self.gcode = gcode.GCodeDispatch(self)
        self.gcode.register_output_handler(self.messages.append)
        self.objects['gcode_macro'] = macro.PrinterGCodeMacro(Config(self,'gcode_macro'))
        self.objects['gcode_move'] = self.move = move.GCodeMove(Config(self,'gcode_move'))
        self.move.last_position = [52.5, 230., 5., 0.]
        self.objects['toolhead'].pos = self.move.last_position[:]
        if bounds is not None: self.set_bounds(bounds)
        parser = config_native.PrinterConfig.__new__(config_native.PrinterConfig)
        parser.printer = self
        self.parsed = parser._build_config_wrapper('[include ../macros/safe_line_purge.cfg]\n', str(ROOT/'parent.cfg')).fileconfig
        for name in self.parsed.sections():
            self.objects[name] = (AD5XPurge(Config(self,name)) if name == 'ad5x_purge' else macro.GCodeMacro(Config(self,name,dict(self.parsed[name]))))
        if native: self.objects['firmware_retraction'] = Status({})
        else: self.objects.pop('firmware_retraction',None)
        def respond(c): self.messages.append(c.get('MSG',''))
        def disable(c): self.sensor_enabled=False
        def enable(c): self.sensor_enabled=True
        def retract(c): self.retracts+=1
        def accel(c): self.accel=c.get_float('ACCEL')
        callbacks={'RESPOND':respond, '_DISABLE_SENSOR':disable, '_ENABLE_SENSOR':enable, 'G10':retract,
                   'SET_VELOCITY_LIMIT':accel, '_TEST_MIN_MAX':lambda c:None,
                   'EXCLUDE_OBJECT_DEFINE':lambda c:None, 'ZEXCLUDE':lambda c:None}
        for name,callback in callbacks.items():
            def record(c,name=name,callback=callback):
                self.commands.append(name)
                callback(c)
            self.gcode.register_command(name,record)
        self.send_event('klippy:connect');self.send_event('klippy:ready')

    def set_bounds(self,b):
        x0,y0,x1,y1=b
        self.objects['exclude_object'].data['objects']=[{'name':'BORDER1','polygon':[[x0,y0],[x1,y0],[x1,y1],[x0,y1]]}]

    def plan(self):
        self.gcode.run_script('LINE_PURGE DRY_RUN=1')
        line=next(m for m in reversed(self.messages) if m.startswith('Purge placement:'))
        return dict(re.findall(r'(\w+)=([^ ]+)',line))


class PlacementTests(unittest.TestCase):
    def test_selects_each_of_four_sides(self):
        for side,b in [('front',(20,100,200,180)),('left',(100,20,180,200)),('right',(20,20,120,200)),('back',(20,20,200,120))]:
            with self.subTest(side=side):
                h=PurgeHarness(b);self.assertEqual(h.plan()['side'],side)
                self.assertEqual(h.motion,[]);self.assertEqual(h.commands,['RESPOND'])

    def test_widest_side_wins_even_when_front_already_fits(self):
        h=PurgeHarness((80,40,120,160));self.assertEqual(h.plan()['side'],'right')

    def test_ties_are_deterministic(self):
        h=PurgeHarness((60,60,160,160));self.assertEqual(h.plan()['side'],'front')

    def test_clipped_front_left_rejected_and_right_selected(self):
        h=PurgeHarness((1,1,100,210));self.assertEqual(h.plan()['side'],'right')

    def test_all_edges_clipped_rejects_before_any_motion_or_sensor_change(self):
        h=PurgeHarness((.223,.223,219.777,219.777))
        with self.assertRaisesRegex(gcode.CommandError,'no safe outer side'):h.gcode.run_script('LINE_PURGE')
        self.assertEqual(h.motion,[]);self.assertEqual(h.commands,[])
        self.assertTrue(h.sensor_enabled);self.assertEqual(h.accel,20000.)

    def test_clipped_right_back_rejected_and_opposite_side_selected(self):
        for bounds,side in [((1,80,219,219),'front'),((80,1,219,219),'left')]:
            with self.subTest(side=side):
                h=PurgeHarness(bounds)
                plan=h.plan()
                self.assertEqual(plan['side'],side)
                self.assertGreaterEqual(float(plan['clearance']),20.)
                h.gcode.run_script('LINE_PURGE')
                low=[pos for pos,speed in h.motion if abs(pos[2]-.8)<1e-6]
                self.assertGreaterEqual(len(low),3)
                for pos in low:
                    gap=bounds[1]-pos[1] if side=='front' else bounds[0]-pos[0]
                    self.assertGreaterEqual(gap,20.-1e-6)

    def test_dry_run_is_valid_with_cold_unhomed_printer_and_changes_no_state(self):
        h=PurgeHarness()
        h.objects['extruder'].data.update(can_extrude=False,temperature=25.,target=0.)
        h.objects['toolhead'].data['homed_axes']=''
        before=copy.deepcopy(h.objects['extruder'].data)
        self.assertIn(h.plan()['side'],['front','left','right','back'])
        self.assertEqual(h.motion,[]);self.assertEqual(h.commands,['RESPOND'])
        self.assertEqual(h.objects['extruder'].data,before)
        self.assertTrue(h.sensor_enabled);self.assertEqual(h.accel,20000.)

    def test_nearly_full_bed_brim_rectangle_is_not_ignored(self):
        h=PurgeHarness(None)
        with self.assertRaisesRegex(gcode.CommandError,'no safe outer side'):h.plan()
        self.assertEqual(h.motion,[])

    def test_margin_and_bead_inset_boundary(self):
        h=PurgeHarness((1,23,219,219));self.assertEqual(h.plan()['side'],'front')
        h=PurgeHarness((1,22.999,219,219))
        with self.assertRaisesRegex(gcode.CommandError,'no safe outer side'):h.plan()

    def test_shorter_bed_dimension_includes_ten_mm_wipe(self):
        h=PurgeHarness((1,1,29,100))
        h.objects['gcode_macro _CLIENT_VARIABLE'].data['max_x']=30.
        with self.assertRaisesRegex(gcode.CommandError,'no safe outer side'):h.plan()

    def test_long_stroke_fails_before_moving(self):
        h=PurgeHarness();h.objects['gcode_macro _KAMP_Settings'].data['purge_amount']=210.
        with self.assertRaisesRegex(gcode.CommandError,'no safe outer side'):h.plan()
        self.assertEqual(h.motion,[])

    def test_missing_footprint_does_not_use_unsafe_fixed_purge(self):
        h=PurgeHarness();h.objects['exclude_object'].data['objects']=[]
        with self.assertRaisesRegex(gcode.CommandError,'no print footprint'):h.gcode.run_script('LINE_PURGE')
        self.assertEqual(h.commands,[])

    def test_lower_height_expands_bead_allowance(self):
        h=PurgeHarness((1,25,219,219));h.objects['gcode_macro _KAMP_Settings'].data['purge_height']=.2
        with self.assertRaisesRegex(gcode.CommandError,'no safe outer side'):h.plan()

    def test_minimum_clearance_when_user_margin_is_zero(self):
        h=PurgeHarness((1,5,219,219));h.objects['gcode_macro _KAMP_Settings'].data['purge_margin']=0
        with self.assertRaisesRegex(gcode.CommandError,'no safe outer side'):h.plan()

    def test_hot_and_homed_guards_before_sensor_changes(self):
        for name,key,value in [('extruder','can_extrude',False),('toolhead','homed_axes','')]:
            h=PurgeHarness();h.objects[name].data[key]=value
            with self.assertRaisesRegex(gcode.CommandError,'homed axes and a hot nozzle'):h.gcode.run_script('LINE_PURGE')
            self.assertEqual(h.commands,['RESPOND']);self.assertEqual(h.motion,[])

    def test_native_parser_preserves_every_macro_body(self):
        h=PurgeHarness()
        for section in RAW.sections():
            if section == 'ad5x_purge': continue
            self.assertEqual(h.parsed.get(section,'gcode').strip(),RAW.get(section,'gcode').strip())

    def test_actual_purge_paths_fit_bed_and_stay_outside_footprint(self):
        boxes=[(20,100,200,180),(100,20,180,200),(20,20,120,200),(20,20,200,120),(190,100,215,190),(5,100,25,190)]
        rng=random.Random(53)
        for _ in range(120):
            x0,y0=rng.uniform(0,180),rng.uniform(0,180)
            boxes.append((x0,y0,rng.uniform(x0+1,220),rng.uniform(y0+1,220)))
        accepted=0
        for b in boxes:
            h=PurgeHarness(b)
            try:p=h.plan()
            except gcode.CommandError:continue
            accepted+=1
            before=h.move.get_status(1.)
            h.gcode.run_script('LINE_PURGE')
            low=[pos for pos,speed in h.motion if abs(pos[2]-.8)<1e-6]
            self.assertGreaterEqual(len(low),3)
            for pos in low:
                self.assertTrue(3.-1e-6<=pos[0]<=217.+1e-6 and 3.-1e-6<=pos[1]<=217.+1e-6,(b,p,pos))
                if p['side']=='front':gap=b[1]-pos[1]
                elif p['side']=='left':gap=b[0]-pos[0]
                elif p['side']=='right':gap=pos[0]-b[2]
                else:gap=pos[1]-b[3]
                self.assertGreaterEqual(gap,20.-1e-6,(b,p,pos))
            self.assertTrue(h.sensor_enabled);self.assertEqual(h.accel,20000.)
            self.assertEqual(h.retracts,1)
            self.assertGreaterEqual(h.motion[-1][0][2],5.)
            after=h.move.get_status(1.)
            for key in ['absolute_coordinates','absolute_extrude','extrude_factor']:
                self.assertEqual(before[key],after[key])
        self.assertGreater(accepted,80)

    def test_direct_retraction_without_firmware_retraction(self):
        h=PurgeHarness(native=False);h.gcode.run_script('LINE_PURGE')
        self.assertEqual(h.retracts,0)
        self.assertTrue(any(h.motion[i][0][3]<h.motion[i-1][0][3] for i in range(1,len(h.motion))))


if __name__=='__main__':unittest.main(verbosity=2)
