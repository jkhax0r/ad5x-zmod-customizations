"""Offline execution of the installed Klipper parser, macros, pause and SD code.

Heaters, cleaning, motion hardware and clocks are test doubles. No network calls.
"""
import ast
import configparser
import copy
import io
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from harness import (Config, Harness, Reactor, Stats, Status, Toolhead,
                        SOURCE, gcode, macro, move, virtual, module, config_native)

pause_native = module('soak_native_pause', SOURCE / 'extras/pause_resume.py')
LIVE = json.loads((ROOT / 'fixtures/soak.json').read_text(encoding='utf-8'))
CP = configparser.RawConfigParser(interpolation=None)
CP.read(ROOT.parent / 'macros/optional_print_soak.cfg', encoding='utf-8')

class Clock(Reactor):
    now = 1000.0
    def monotonic(self): return self.now

class Head(Toolhead):
    def get_status(self, eventtime=None):
        return {**self.data, 'estimated_print_time': self.printer.reactor.now,
                'position': self.pos[:]}

class Hooks(Status):
    def register_endpoint(self, *args): pass

class SoakHarness(Harness):
    def __init__(self):
        self.objects, self.events = {}, {}
        self.reactor = Clock()
        self.motion, self.messages, self.commands = [], [], []
        self.timeline = []
        self.clean_calls = self.mesh_calls = self.print_calls = 0
        self.fail_clean = self.fail_mesh = False
        self.settings = copy.deepcopy(LIVE['configfile']['settings'])
        for k,v in LIVE.items(): self.objects[k] = Status(v)
        for k,c in LIVE['configfile']['config'].items():
            if k.startswith('gcode_macro '):
                self.objects[k] = Status({n[9:]:ast.literal_eval(v) for n,v in c.items() if n.startswith('variable_')})
        self.objects['webhooks'] = Hooks({'state':'ready','state_message':''})
        self.objects['heaters'] = Status({})
        self.objects['print_stats'] = Stats({'state':'printing','filename':'test.gcode'})
        self.objects['toolhead'] = Head(self)
        self.objects['extruder'].data.update(temperature=30.,target=0.,can_extrude=True)
        self.objects['heater_bed'].data.update(temperature=30.,target=0.)
        self.objects['gcode'] = self.gcode = gcode.GCodeDispatch(self)
        self.gcode.register_output_handler(self.messages.append)
        self.objects['gcode_macro'] = macro.PrinterGCodeMacro(Config(self,'gcode_macro'))
        self.objects['gcode_move'] = self.move = move.GCodeMove(Config(self,'gcode_move'))
        self.objects['virtual_sdcard'] = self.sd = virtual.VirtualSD(Config(self,'virtual_sdcard',self.settings['virtual_sdcard']))
        self.objects['pause_resume'] = pause_native.PauseResume(Config(self,'pause_resume'))
        values = {k:dict(CP.items(k)) for k in CP.sections() if k.startswith('gcode_macro ')}
        for n in ['_START_PRINT','_CANCEL_PRINT','PAUSE']:
            values['gcode_macro '+n] = LIVE['configfile']['config']['gcode_macro '+n]
        for k,v in values.items(): self.objects[k] = macro.GCodeMacro(Config(self,k,v))
        self.timer = 0
        callbacks = {
            '_ORIG_CLEAR_NOZZLE':self.clean,
            '_KAMP_BED_MESH_CALIBRATE':self.mesh,
            '_BED_MESH_CALIBRATE':self.mesh,
            'TURN_OFF_HEATERS':self.off,
            'M104':self.set_nozzle,'M140':self.set_bed,
            '_WAIT_TEMP':self.reheat,
            'TEMPERATURE_WAIT':self.wait_temp,
            'UPDATE_DELAYED_GCODE':self.update_timer,
            'TEST_PRINT':self.print_marker,
            'LOAD_GCODE_OFFSET':lambda c:None,
            'RESPOND':lambda c:self.messages.append(c.get('MSG','')),
        }
        names = ['M400','M117','M106','CHECK_MD5','_PREPARE_PRINT','ZCONTROL_AUTO','ZCONTROL_ON','ZSSH_RELOAD',
                 'SET_SKEW','_G28','BED_MESH_CLEAR','_GOTO_TRASH','_SMART_PARK','_UGOL_PARK','_PRINT_MESH',
                 'LINE_PURGE','_TEST_MIN_MAX','EXCLUDE_OBJECT_DEFINE','ZEXCLUDE','_USER_START_PRINT',
                 '_CLIENT_RETRACT','_CLIENT_EXTRUDE','SET_PAUSE_NEXT_LAYER','SET_PAUSE_AT_LAYER','_PAUSE_MACRO',
                 'SET_IDLE_TIMEOUT','_TOOLHEAD_PARK_PAUSE_CANCEL','_PRINT_CLEAR_NOZZLE','SET_FAN_SPEED','_COMMON_END_PRINT']
        for n in names:callbacks.setdefault(n,lambda c:None)
        for n,cb in callbacks.items():
            if n not in self.gcode.ready_gcode_handlers:
                def record(c,n=n,cb=cb):
                    self.commands.append(n)
                    return cb(c)
                self.gcode.register_command(n,record)
        self.send_event('klippy:connect')
        self.send_event('klippy:ready')
        self.objects['save_variables'].data['variables'].update(print_leveling=1,use_kamp=1,mesh_test=0,check_md5=1)
        self.objects['gcode_macro _SCREEN'].data['screen']=False
        self.file_name='/usr/data/gcodes/test.gcode'

    def clean(self,c):
        if self.fail_clean:raise gcode.CommandError('Injected cleaning fault')
        self.clean_calls+=1;self.timeline.append('clean')
        nozzle=151. if c.get_float('EXTRUDER_TEMP')>230 else 121.
        self.objects['extruder'].data.update(target=nozzle,temperature=nozzle)
        b=c.get_float('BED_TEMP');self.objects['heater_bed'].data.update(target=b,temperature=b)
    def mesh(self,c):
        if self.fail_mesh:raise gcode.CommandError('Injected probing fault')
        self.mesh_calls+=1;self.timeline.append('mesh')
    def reheat(self,c):
        self.timeline.append('reheat')
        self.objects['extruder'].data['target']=c.get_float('EXTRUDER_TEMP')
        self.objects['heater_bed'].data['target']=c.get_float('BED_TEMP')
    def wait_temp(self,c):
        sensor=c.get('SENSOR')
        assert c.get_float('MINIMUM')<=self.objects[sensor].data['temperature']<=c.get_float('MAXIMUM')
    def update_timer(self,c):self.timer=c.get_float('DURATION')
    def arm_file(self,script=''):
        super().arm_file(script)
        self.sd.current_file.name=self.file_name
    def state(self):return self.objects['gcode_macro _PRINT_SOAK_STATE'].variables
    def advance(self,seconds):
        self.reactor.now+=seconds
        self.gcode.run_script('_PRINT_SOAK_TICK')
    def continue_sd(self):self.sd.work_handler(self.reactor.now)
    def assert_clean_log(self,test):
        test.assertFalse(any('Unknown command' in s for s in self.messages),self.messages)

def program(minutes=20,bed=85,nozzle=250,ready=1):
    return f'PRINT_SOAK MINUTES={minutes} BED_TEMP={bed} EXTRUDER_TEMP={nozzle}\nSTART_PRINT BED_TEMP={bed} EXTRUDER_TEMP={nozzle} SOAK_READY={ready}\nTEST_PRINT'

class SoakTests(unittest.TestCase):
    def test_new_sd_job_clears_leftover_idle_pause_before_soaking(self):
        h=SoakHarness()
        h.gcode.run_script('PAUSE_BASE')
        self.assertTrue(h.objects['pause_resume'].is_paused)
        self.assertFalse(h.sd.is_active())
        h.run_sd(program())
        self.assertEqual((h.clean_calls,h.mesh_calls,h.print_calls),(1,0,0),h.messages)
        self.assertEqual(h.state()['phase'],1,h.messages)
        h.advance(1199)
        self.assertEqual(h.mesh_calls,0)
        h.advance(2);h.continue_sd()
        self.assertEqual((h.clean_calls,h.mesh_calls,h.print_calls),(1,1,1),h.messages)
        h.assert_clean_log(self)

    def test_soak_rejects_idle_and_actually_paused_file(self):
        h=SoakHarness()
        with self.assertRaisesRegex(gcode.CommandError,'active printer-file job'):
            h.gcode.run_script('PRINT_SOAK MINUTES=20 BED_TEMP=85 EXTRUDER_TEMP=250')
        self.assertEqual(h.clean_calls,0)
        h.run_sd(program())
        self.assertTrue(h.objects['pause_resume'].is_paused)
        self.assertFalse(h.sd.is_active())
        with self.assertRaisesRegex(gcode.CommandError,'active printer-file job'):
            h.gcode.run_script('PRINT_SOAK MINUTES=20 BED_TEMP=85 EXTRUDER_TEMP=250')
        self.assertEqual((h.clean_calls,h.mesh_calls,h.print_calls),(1,0,0))
        self.assertTrue(h.objects['pause_resume'].is_paused)
        self.assertEqual(h.state()['phase'],1)

    def test_large_print_pauses_then_meshes_without_second_hot_clean(self):
        h=SoakHarness();h.run_sd(program())
        self.assertEqual((h.clean_calls,h.mesh_calls,h.print_calls),(1,0,0),h.messages)
        self.assertTrue(h.objects['pause_resume'].is_paused)
        self.assertEqual(h.state()['phase'],1)
        h.advance(1199);self.assertEqual(h.state()['phase'],1)
        h.advance(2);self.assertEqual(h.state()['phase'],2)
        h.continue_sd()
        self.assertEqual((h.clean_calls,h.mesh_calls,h.print_calls),(1,1,1),h.messages)
        self.assertEqual(h.timeline[:3],['clean','mesh','reheat'])
        self.assertEqual(h.state()['phase'],0)
        h.assert_clean_log(self)

    def test_normal_start_does_not_pause_or_add_a_wait(self):
        h=SoakHarness();h.run_sd('START_PRINT BED_TEMP=85 EXTRUDER_TEMP=250\nTEST_PRINT')
        self.assertEqual((h.clean_calls,h.mesh_calls,h.print_calls),(1,1,1),h.messages)
        self.assertFalse(h.objects['pause_resume'].is_paused)
        self.assertEqual(h.timer,0);h.assert_clean_log(self)

    def test_zero_minutes_uses_normal_cleaning(self):
        h=SoakHarness();h.run_sd(program(minutes=0))
        self.assertEqual((h.clean_calls,h.mesh_calls,h.print_calls),(1,1,1),h.messages)
        self.assertEqual(h.state()['phase'],0);h.assert_clean_log(self)

    def test_cancel_during_soak_disarms_timer_and_heaters(self):
        h=SoakHarness();h.run_sd(program());h.gcode.run_script('CANCEL_PRINT')
        self.assertEqual(h.state()['phase'],0)
        self.assertEqual(h.timer,0)
        self.assertEqual(h.objects['extruder'].data['target'],0)
        self.assertEqual(h.objects['heater_bed'].data['target'],0)
        h.advance(1300);self.assertEqual(h.print_calls,0)
        self.assertFalse(h.sd.is_active());h.assert_clean_log(self)

    def test_manual_resume_cannot_skip_wait_or_restore_old_hot_target(self):
        h=SoakHarness();h.run_sd(program());h.gcode.run_script('RESUME')
        self.assertTrue(h.objects['pause_resume'].is_paused)
        self.assertEqual(h.objects['extruder'].data['target'],151)
        self.assertEqual(h.print_calls,0);h.assert_clean_log(self)

    def test_target_change_during_soak_aborts(self):
        h=SoakHarness();h.run_sd(program());h.objects['heater_bed'].data['target']=60
        h.advance(5)
        self.assertEqual(h.state()['phase'],0)
        self.assertEqual(h.objects['extruder'].data['target'],0)
        self.assertEqual(h.print_calls,0);h.assert_clean_log(self)

    def test_missing_soak_or_legacy_proposed_parameter_cannot_silently_print(self):
        for suffix in ['SOAK_READY=1','SOAK_MINUTES=20']:
            h=SoakHarness();h.run_sd('START_PRINT BED_TEMP=85 EXTRUDER_TEMP=250 '+suffix+'\nTEST_PRINT')
            self.assertEqual(h.objects['print_stats'].data['state'],'error',h.messages)
            self.assertEqual((h.mesh_calls,h.print_calls),(0,0))

    def test_mismatched_start_temperature_stops_before_meshing(self):
        h=SoakHarness();script=program().replace('START_PRINT BED_TEMP=85','START_PRINT BED_TEMP=60')
        h.run_sd(script);h.advance(1201);h.continue_sd()
        self.assertEqual((h.mesh_calls,h.print_calls),(0,0))
        self.assertEqual(h.objects['extruder'].data['target'],0)

    def test_homing_loss_aborts(self):
        h=SoakHarness();h.run_sd(program());h.objects['toolhead'].data['homed_axes']=''
        h.advance(5);self.assertEqual(h.state()['phase'],0)
        self.assertEqual(h.objects['heater_bed'].data['target'],0)

    def test_filename_encoding_preserves_spaces_quotes_semicolon_and_unicode(self):
        h=SoakHarness();h.file_name='/usr/data/gcodes/Jim\'s "plate"; PETG Ω.gcode'
        h.run_sd(program());self.assertEqual(h.state()['job'],h.file_name,h.messages)
        h.advance(1201);h.continue_sd();self.assertEqual(h.print_calls,1,h.messages)

    def test_lower_native_probe_temperature_and_full_mesh(self):
        h=SoakHarness();h.objects['save_variables'].data['variables']['use_kamp']=0
        h.run_sd(program(bed=60,nozzle=230));self.assertEqual(h.state()['nozzle'],121)
        h.advance(1201);h.continue_sd()
        self.assertEqual((h.clean_calls,h.mesh_calls,h.print_calls),(1,1,1),h.messages)
        h.assert_clean_log(self)

    def test_probe_failure_after_resume_uses_native_sd_error_shutdown(self):
        h=SoakHarness();h.run_sd(program());h.advance(1201);h.fail_mesh=True;h.continue_sd()
        self.assertEqual(h.objects['print_stats'].data['state'],'error')
        self.assertEqual(h.print_calls,0)
        self.assertEqual(h.objects['extruder'].data['target'],0)

    def test_invalid_minutes_rejected_before_cleaning(self):
        for m in [-1,26,'nan','inf','oops']:
            h=SoakHarness();h.run_sd(program(minutes=m))
            self.assertEqual(h.clean_calls,0)
            self.assertEqual(h.print_calls,0)

    def test_native_config_parser_merges_include_without_losing_rename_or_probe_settings(self):
        base=configparser.RawConfigParser(interpolation=None)
        for name in ['probe','bed_mesh','gcode_macro PAUSE','gcode_macro START_PRINT','gcode_macro KAMP',
                     'gcode_macro _FULL_BED_LEVEL','gcode_macro RESUME','gcode_macro CANCEL_PRINT']:
            base[name]=LIVE['configfile']['config'][name]
        out=io.StringIO();base.write(out)
        parser=config_native.PrinterConfig.__new__(config_native.PrinterConfig)
        parser.printer=SoakHarness()
        merged=parser._build_config_wrapper(out.getvalue()+'\n[include ../macros/optional_print_soak.cfg]\n',str(ROOT/'parent.cfg')).fileconfig
        self.assertEqual(dict(merged.items('probe')),dict(base.items('probe')))
        self.assertEqual(merged.get('gcode_macro RESUME','rename_existing'),'RESUME_BASE')
        self.assertEqual(merged.get('gcode_macro CANCEL_PRINT','rename_existing'),'CANCEL_PRINT_BASE')
        self.assertEqual(merged.get('gcode_macro START_PRINT','gcode').strip(),CP.get('gcode_macro START_PRINT','gcode').strip())
        for section in CP.sections():
            if CP.has_option(section,'gcode'):
                self.assertEqual(merged.get(section,'gcode').strip(),CP.get(section,'gcode').strip(),section)

    def test_stale_completed_soak_is_rejected(self):
        h=SoakHarness();h.run_sd(program());h.advance(1201);h.reactor.now+=121;h.continue_sd()
        self.assertEqual(h.mesh_calls,0)
        self.assertEqual(h.print_calls,0)
        self.assertEqual(h.objects['extruder'].data['target'],0)

if __name__=='__main__':unittest.main(verbosity=2)
