"""Native Klipper macro/motion-state/pause replay; motors/camera/SD are doubles."""
import copy
import json
from pathlib import Path
import unittest

from harness import Config, Harness, Reactor, Status, Toolhead, gcode, macro, move, config_native, module, SOURCE

ROOT = Path(__file__).resolve().parent
pause = module('timelapse_native_pause', SOURCE/'extras/pause_resume.py')


class Webhooks(Status):
    def register_endpoint(self, *args): pass


class SD:
    def __init__(self): self.active=True; self.resumes=0
    def is_active(self): return self.active
    def do_pause(self): self.active=False
    def do_resume(self): self.active=True; self.resumes+=1
    def do_cancel(self): self.active=False


class TimelapseHarness(Harness):
    def __init__(self, xyz=(140.,217.,5.)):
        self.objects, self.events = {}, {}
        self.reactor=Reactor()
        self.motion, self.messages, self.commands = [], [], []
        self.frames=0; self.fw=[]; self.timers=[]
        self.objects['toolhead']=Toolhead(self)
        self.objects['toolhead'].data.update(axis_minimum=gcode.Coord(-20,-20,-10,0),axis_maximum=gcode.Coord(225,232,230,0))
        self.objects['toolhead'].pos=list(xyz)+[25.]
        self.objects['extruder']=Status({'can_extrude':True})
        self.objects['bed_mesh']=Status({'mesh_matrix':[]})
        self.objects['print_stats']=Status({'state':'printing'})
        self.objects['webhooks']=Webhooks({'state':'ready'})
        self.objects['virtual_sdcard']=self.sd=SD()
        self.objects['gcode_macro HYPERLAPSE']=Status({'run':False})
        self.objects['gcode_macro _CLIENT_VARIABLE']=Status(dict(min_x=0,min_y=0,max_x=220,max_y=220))
        self.objects['configfile']=Status({'settings':{'printer':{'kinematics':'corexy'},'gcode_macro pause':{'rename_existing':'PAUSE_BASE'},'gcode_macro resume':{'rename_existing':'RESUME_BASE'},'firmware_retraction':{}}})
        self.objects['gcode']=self.gcode=gcode.GCodeDispatch(self)
        self.gcode.register_output_handler(self.messages.append)
        self.objects['gcode_macro']=macro.PrinterGCodeMacro(Config(self,'gcode_macro'))
        self.objects['gcode_move']=self.move=move.GCodeMove(Config(self,'gcode_move'))
        self.objects['pause_resume']=self.pause=pause.PauseResume(Config(self,'pause_resume'))
        for name in ['PAUSE','RESUME']:
            self.gcode.register_command(name+'_BASE', self.gcode.register_command(name,None))
        parser=config_native.PrinterConfig.__new__(config_native.PrinterConfig);parser.printer=self
        parsed=parser._build_config_wrapper('[include ../macros/safe_timelapse.cfg]\n',str(ROOT/'parent.cfg')).fileconfig
        self.parsed=parsed
        for name in parsed.sections():
            if name.startswith('gcode_macro '): self.objects[name]=macro.GCodeMacro(Config(self,name,dict(parsed[name])))
        for name,values in json.loads((ROOT/'fixtures/vendor-timelapse-setup.json').read_text()).items():
            self.objects[name]=macro.GCodeMacro(Config(self,name,values))
        callbacks={'M400':lambda c:self.commands.append('M400'),'UPDATE_DELAYED_GCODE':lambda c:self.timers.append(c.get_float('DURATION')),
                   '_TIMELAPSE_NEW_FRAME':self.frame,'G10':lambda c:self.fw.append('G10'),'G11':lambda c:self.fw.append('G11')}
        for name,callback in callbacks.items(): self.gcode.register_command(name,callback)
        self.send_event('klippy:connect');self.send_event('klippy:ready')
        self.gcode.run_script('_SET_TIMELAPSE_SETUP ENABLE=True PARK_ENABLE=True CUSTOM_POS_X=110 CUSTOM_POS_Y=110 CUSTOM_POS_DZ=2 PARK_POS=custom')

    def frame(self,c):
        self.frames+=1
        self.commands.append('FRAME')
    def run(self,command='TIMELAPSE_TAKE_FRAME'): self.gcode.run_script(command)
    def finish(self):
        self.objects['gcode_macro TIMELAPSE_TAKE_FRAME'].variables['takingframe']=False
        self.run('_SAFE_TIMELAPSE_RETURN')
    def xyz_moves(self,start):
        result=[]; prior=list(start)
        for p,s in self.motion:
            if p[:3] != prior[:3]: result.append((p[:3],s))
            prior=p
        return result


class TimelapseTests(unittest.TestCase):
    def test_inset_back_left_corner_with_two_mm_lift(self):
        for z in [5,.25,100]:
            h=TimelapseHarness((140,217,z))
            h.run('_SET_TIMELAPSE_SETUP CUSTOM_POS_X=10 CUSTOM_POS_Y=210 CUSTOM_POS_DZ=2 PARK_POS=custom')
            h.run();h.finish()
            self.assertEqual([p for p,s in h.xyz_moves((140,217,z))],[[140,217,z+2],[10,210,z+2],[140,217,z+2],[140,217,z]])

    def test_first_purge_lift_before_xy_and_return_before_lowering(self):
        h=TimelapseHarness();h.run();self.assertTrue(h.pause.is_paused)
        self.assertEqual(h.commands,['M400','FRAME'])
        self.assertEqual(h.frames,1);h.finish()
        self.assertEqual([p for p,s in h.xyz_moves((140,217,5))],[[140,217,7],[110,110,7],[140,217,7],[140,217,5]])
        self.assertFalse(h.pause.is_paused);self.assertEqual(h.sd.resumes,1)
        self.assertEqual(h.objects['toolhead'].pos,[140,217,5,25])

    def test_first_layer_clears_point_eight_mm_bead(self):
        h=TimelapseHarness((20,20,.25));h.run();h.finish()
        self.assertEqual([p for p,s in h.xyz_moves((20,20,.25))],[[20,20,2.25],[110,110,2.25],[20,20,2.25],[20,20,.25]])

    def test_tall_print_retains_full_relative_clearance(self):
        h=TimelapseHarness((30,40,100));h.run();h.finish()
        self.assertEqual(h.xyz_moves((30,40,100))[1][0],[110,110,102])

    def test_larger_fluidd_lift_and_minimum_floor(self):
        for setting,expected in [(8,13),(0,7),(-2,7)]:
            h=TimelapseHarness();h.run('_SET_TIMELAPSE_SETUP CUSTOM_POS_DZ=%s PARK_POS=custom'%setting)
            h.run();self.assertEqual(h.xyz_moves((140,217,5))[0][0][2],expected)

    def test_corners_outside_printable_bed_cannot_move(self):
        h=TimelapseHarness();h.run('_SET_TIMELAPSE_SETUP PARK_POS=back_left');h.run()
        self.assertEqual(h.motion,[]);self.assertEqual(h.frames,0);self.assertFalse(h.pause.is_paused)

    def test_service_area_start_cannot_cross_diagonally(self):
        h=TimelapseHarness((52.5,230,5));h.run();self.assertEqual(h.motion,[])

    def test_top_of_travel_skips_before_pause_or_retract(self):
        for height,mesh in [(228,[]),(223,[[6.]])]:
            h=TimelapseHarness((20,20,height));h.objects['bed_mesh'].data['mesh_matrix']=mesh;h.run()
            self.assertEqual(h.motion,[]);self.assertFalse(h.pause.is_paused);self.assertEqual(h.frames,0)

    def test_cold_unhomed_dry_run_is_read_only(self):
        h=TimelapseHarness();h.objects['toolhead'].data['homed_axes']='';h.objects['extruder'].data['can_extrude']=False
        state=copy.deepcopy(h.move.get_status());h.run('_SAFE_TIMELAPSE_PARK DRY_RUN=1')
        self.assertEqual(h.motion,[]);self.assertEqual(h.move.get_status(),state);self.assertFalse(h.pause.is_paused)
        self.assertIn('no motion',h.messages[-1]);h.run();self.assertEqual(h.motion,[])

    def test_existing_pause_is_not_stolen(self):
        h=TimelapseHarness();h.run('PAUSE_BASE');saved=copy.deepcopy(h.move.saved_states['PAUSE_STATE']);h.run()
        self.assertEqual(h.motion,[]);self.assertEqual(h.move.saved_states['PAUSE_STATE'],saved)

    def test_wait_and_cancel_never_resume_cancelled_job(self):
        h=TimelapseHarness();h.run();n=len(h.motion);h.run('_SAFE_TIMELAPSE_RETURN')
        self.assertEqual(len(h.motion),n);self.assertEqual(h.timers[-1],.5)
        h.run('CANCEL_PRINT');h.objects['print_stats'].data['state']='cancelled';h.finish();h.run('_SAFE_TIMELAPSE_RETURN')
        self.assertEqual(len(h.motion),n);self.assertEqual(h.sd.resumes,0);self.assertEqual(h.timers[-1],0)

    def test_lost_homing_abandons_without_return(self):
        h=TimelapseHarness();h.run();n=len(h.motion);h.objects['toolhead'].data['homed_axes']='';h.finish()
        self.assertEqual(len(h.motion),n);self.assertEqual(h.sd.resumes,0)

    def test_restores_modes_offsets_feed_and_extrusion(self):
        for mode in ['G90\nM82','G90\nM83','G91\nM82','G91\nM83']:
            h=TimelapseHarness();h.run('SET_GCODE_OFFSET X=2 Y=-3 Z=-.085\nG92 E123\nM220 S80\nM221 S95\nG1 F2400\n'+mode)
            h.motion.clear();before=copy.deepcopy(h.move.get_status());h.run();h.finish()
            self.assertEqual(h.move.get_status(),before)
            xyz=h.xyz_moves((140,217,5));self.assertEqual(xyz[1][0],[110,110,7])
            self.assertEqual([speed for p,speed in xyz],[10,100,100,10])

    def test_firmware_retraction_balanced(self):
        h=TimelapseHarness();h.run('_SET_TIMELAPSE_SETUP FW_RETRACT=True');h.run();h.finish()
        self.assertEqual(h.fw,['G10','G11'])

    def test_cold_frame_does_not_prime_after_heating(self):
        h=TimelapseHarness();h.objects['extruder'].data['can_extrude']=False;h.run()
        h.objects['extruder'].data['can_extrude']=True;h.finish();self.assertTrue(all(p[3]==25 for p,s in h.motion))

    def test_orca_enable_does_not_override_custom_center(self):
        h=TimelapseHarness();h.run('_SET_TIMELAPSE_SETUP PARK_ENABLE=True');h.run()
        self.assertEqual(h.xyz_moves((140,217,5))[1][0],[110,110,7])

    def test_disable_and_nonparking_frame(self):
        h=TimelapseHarness();h.run('_SET_TIMELAPSE_SETUP ENABLE=False');h.run();self.assertEqual(h.frames,0)
        h.run('_SET_TIMELAPSE_SETUP ENABLE=True PARK_ENABLE=False');h.run()
        self.assertEqual(h.frames,1);self.assertEqual(h.motion,[]);self.assertFalse(h.pause.is_paused)
        self.assertEqual(h.commands,['FRAME']);self.assertEqual(h.timers,[])

    def test_hyperlapse_mode_filter(self):
        h=TimelapseHarness();h.objects['gcode_macro HYPERLAPSE'].data['run']=True;h.run();self.assertEqual(h.frames,0)
        h.run('TIMELAPSE_TAKE_FRAME HYPERLAPSE=True');h.finish();self.assertEqual(h.frames,1)


if __name__=='__main__': unittest.main()
