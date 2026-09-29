"""Actual sampling loop and release logic with simulated MCU and motion."""
import ast
import logging
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

tree = ast.parse((Path(__file__).resolve().parent.parent / 'probe/probe.py').read_text(),
                 feature_version=(3, 8))
scope = {'math': math, 'logging': logging}
nodes = [n for n in tree.body if getattr(n, 'name', '') in
         ('ProbeSessionHelper', 'calc_probe_z_average')]
exec(compile(ast.Module(body=nodes, type_ignores=[]), '<probe>', 'exec'), scope)


class Command:
    def __init__(self, **params): self.params, self.messages = params, []
    def get(self, name, default): return self.params.get(name, default)
    def get_float(self, name, default, **limits):
        value = float(self.get(name, default))
        if value < limits.get('minval', -math.inf) or value <= limits.get('above', -math.inf):
            raise ValueError(name)
        return value
    def get_int(self, name, default, **limits):
        value = int(self.get(name, default))
        if value < limits.get('minval', value) or value > limits.get('maxval', value):
            raise ValueError(name)
        return value
    def error(self, message): return ValueError(message)
    def respond_info(self, message): self.messages.append(message)


class Rig:
    def __init__(self, heights, mode='adaptive', releases=None, states=None):
        self.contacts = iter(heights)
        self.releases = iter(releases or [.12] * 20)
        self.states = iter(states or [True, False] * 20)
        self.moves, self.speeds, self.homes = [], [], []
        self.pos = [10., 20., 2., 0.]
        self.zmax = 230.
        self.release_failure = False
        self.toolhead = SimpleNamespace(get_position=lambda: list(self.pos),
            wait_moves=lambda: None, get_last_move_time=lambda: 0.,
            get_status=lambda now: {'axis_maximum': [225., 232., self.zmax, 0.]},
            manual_move=self.move, dwell=lambda duration: None)
        self.pauses = []
        self.reactor = SimpleNamespace(monotonic=lambda: 0., pause=self.pauses.append)
        self.printer = SimpleNamespace(lookup_object=lambda name: self.toolhead,
            get_reactor=lambda: self.reactor,
            command_error=ValueError)
        self.probe = scope['ProbeSessionHelper'].__new__(scope['ProbeSessionHelper'])
        self.probe.printer = self.printer
        self.probe.multi_probe_pending = True
        self.probe.results = []
        self.probe.first_probe_speed = 10.
        self.probe.first_probe_extra_retract_dist = .3
        self.probe.probe_retract_mode = mode
        self.probe.probe_release_clearance = .05
        self.probe.probe_release_max_dist = 1.
        self.probe.probe_release_speed = 1.
        self.probe.mcu_probe = SimpleNamespace(query_endstop=lambda now: next(self.states), get_mcu=lambda: SimpleNamespace(estimated_print_time=lambda now: now))
        self.probe.get_probe_params = lambda cmd: dict(sample_retract_dist=.2,
            lift_speed=70., samples=2, probe_speed=.4, samples_tolerance=.0125,
            samples_tolerance_retries=1, samples_result='average')
        self.probe._probe = self.contact
        scope['homing'] = SimpleNamespace(HomingMove=self.make_home)
    def contact(self, speed):
        self.speeds.append(speed)
        self.pos[2] = next(self.contacts)
        return self.pos[:3]
    def move(self, coords, speed):
        for i, val in enumerate(coords):
            if val is not None: self.pos[i] = val
        self.moves.append((list(self.pos), speed))
    def make_home(self, printer, endstops):
        assert printer is self.printer
        assert endstops == [(self.probe.mcu_probe, 'probe')]
        return SimpleNamespace(homing_move=self.home)
    def home(self, target, speed, **kwargs):
        self.homes.append((list(target), speed, kwargs))
        if self.release_failure: raise ValueError('No trigger after full movement')
        self.pos[2] += next(self.releases)
        release = list(self.pos)
        self.pos[2] += .0025  # MCU stopping overshoot must not corrupt coordinates.
        return release
    def run(self, **params):
        self.cmd = Command(**dict({'PROBE_RELEASE_FINAL': 1, 'PROBE_RELEASE_SYNC': 'optimized'}, **params))
        self.probe.run_probe(self.cmd)
        return self.probe.results


class AdaptiveZeroTests(unittest.TestCase):
    def test_release_after_every_contact_result_excludes_fast(self):
        rig = Rig([-.24, 0., .005], releases=[.24, .10, .11])
        result = rig.run()
        self.assertEqual(result, [[10., 20., .0025]])
        self.assertEqual(rig.speeds, [10., .4, .4])
        self.assertEqual(len(rig.homes), 3)
        for target, speed, options in rig.homes:
            self.assertEqual(options, dict(probe_pos=True, triggered=False, check_triggered=True))
            self.assertEqual(speed, 1.)
        self.assertAlmostEqual(rig.moves[0][0][2], .0525)
        self.assertAlmostEqual(rig.moves[-1][0][2], .1675)
        self.assertEqual(len(rig.cmd.messages), 3)
        self.assertIn('release_delta=0.24000', rig.cmd.messages[0])
        self.assertIn('slow_2', rig.cmd.messages[-1])

    def test_fixed_mode_is_previous_behavior_and_never_queries_pin(self):
        rig = Rig([-.24, 0., .005], mode='fixed', states=[False])
        result = rig.run()
        self.assertEqual(result, [[10., 20., .0025]])
        self.assertEqual(rig.homes, [])
        self.assertEqual(len(rig.moves), 2)
        self.assertAlmostEqual(rig.moves[0][0][2], .26)
        self.assertAlmostEqual(rig.moves[1][0][2], .2)

    def test_command_mode_and_clearance_override(self):
        rig = Rig([-.24, 0., .005], mode='fixed')
        rig.run(PROBE_RETRACT_MODE='adaptive', PROBE_RELEASE_CLEARANCE=.075)
        self.assertIn('clearance=0.07500', rig.cmd.messages[0])

    def test_release_failure_stops_before_slow_samples(self):
        rig = Rig([-.24, 0., .005])
        rig.release_failure = True
        with self.assertRaises(ValueError): rig.run()
        self.assertEqual(rig.speeds, [10.])
        self.assertEqual(rig.probe.results, [])
        self.assertIn('release_failed', rig.cmd.messages[-1])

    def test_already_clear_fails_without_inventing_release_height(self):
        rig = Rig([-.24], states=[False])
        with self.assertRaisesRegex(ValueError, 'already clear'): rig.run()
        self.assertEqual(rig.homes, [])
        self.assertEqual(rig.probe.results, [])
        self.assertIn('already_clear', rig.cmd.messages[0])

    def test_retriggered_after_clearance_stops(self):
        rig = Rig([-.24], states=[True, True])
        with self.assertRaisesRegex(ValueError, 're-triggered'): rig.run()
        self.assertEqual(rig.probe.results, [])

    def test_axis_limit_stops_before_release_motion(self):
        rig = Rig([-.24])
        rig.zmax = .5
        with self.assertRaisesRegex(ValueError, 'Z travel'): rig.run()
        self.assertEqual(rig.homes, [])

    def test_invalid_parameters_rejected_before_contact(self):
        for params in [dict(PROBE_RETRACT_MODE='unknown'),
                       dict(PROBE_RELEASE_CLEARANCE=-.1),
                       dict(PROBE_RELEASE_CLEARANCE=float('nan')),
                       dict(PROBE_RELEASE_MAX_DIST=0),
                       dict(PROBE_RELEASE_MAX_DIST=float('inf')),
                       dict(PROBE_RELEASE_SPEED=-1)]:
            with self.subTest(params=params):
                rig = Rig([])
                with self.assertRaises(ValueError): rig.run(**params)
                self.assertEqual(rig.speeds, [])

    def test_slow_retry_releases_each_contact_fast_still_only_once(self):
        rig = Rig([-.24, 0., .1, .002, .004])
        self.assertEqual(rig.run(), [[10., 20., .003]])
        self.assertEqual(len(rig.homes), 5)
        self.assertEqual(rig.speeds, [10., .4, .4, .4, .4])
        self.assertIn('slow_4', rig.cmd.messages[-1])

    def test_fast_disabled_still_releases_slow_contacts(self):
        rig = Rig([0., .004])
        self.assertEqual(rig.run(FIRST_PROBE_SPEED=0), [[10., 20., .002]])
        self.assertEqual(len(rig.homes), 2)

    def test_legacy_sync_remains_available_for_timing_comparison(self):
        rig = Rig([-.24, 0., .005])
        rig.run(PROBE_RELEASE_SYNC='legacy')
        self.assertIn('sync=legacy', rig.cmd.messages[0])

    def test_optimized_sync_avoids_repriming_queries(self):
        rig = Rig([-.24, 0., .005])
        waits = []
        rig.toolhead.wait_moves = lambda: waits.append(True)
        def unexpected_get_last():
            raise AssertionError('Probe query re-primed the motion queue')
        rig.toolhead.get_last_move_time = unexpected_get_last
        rig.run()
        self.assertEqual(len(waits), 3)  # Only after each clearance move.
        self.assertIn('check_before_ms=', rig.cmd.messages[0])
        self.assertIn('sync=optimized', rig.cmd.messages[0])

    def test_final_release_can_be_skipped_but_contact_is_recorded(self):
        rig = Rig([-.24, 0., .005])
        result = rig.run(PROBE_RELEASE_FINAL=0)
        self.assertEqual(result, [[10., 20., .0025]])
        self.assertEqual(len(rig.homes), 2)
        self.assertEqual(rig.pos[2], .005)
        self.assertIn('PROBE_CONTACT slow_2', rig.cmd.messages[-1])

    def test_retry_still_retracts_when_final_release_disabled(self):
        rig = Rig([-.24, 0., .1, .002, .004])
        self.assertEqual(rig.run(PROBE_RELEASE_FINAL=0), [[10., 20., .003]])
        self.assertEqual(len(rig.homes), 4)
        self.assertIn('PROBE_CONTACT slow_4', rig.cmd.messages[-1])

    def test_queued_check_waits_and_rechecks_if_query_returns_early(self):
        rig = Rig([-.24, 0., .005], states=[True, False, False] * 3)
        rig.toolhead.get_last_move_time = lambda: 1.
        waits = []
        rig.toolhead.wait_moves = lambda: waits.append(True)
        rig.run(PROBE_RELEASE_SYNC='queued')
        self.assertEqual(len(waits), 3)
        self.assertIn('post_remaining_ms=1000.0', rig.cmd.messages[0])

    def test_queued_check_detects_retrigger_after_early_query(self):
        rig = Rig([-.24], states=[True, False, True])
        rig.toolhead.get_last_move_time = lambda: 1.
        with self.assertRaisesRegex(ValueError, 're-triggered'):
            rig.run(PROBE_RELEASE_SYNC='queued')

    def test_queued_check_does_not_add_wait_after_completed_query(self):
        rig = Rig([-.24, 0., .005])
        waits = []
        rig.toolhead.wait_moves = lambda: waits.append(True)
        rig.run(PROBE_RELEASE_SYNC='queued')
        self.assertEqual(waits, [])
        self.assertIn('sync=queued', rig.cmd.messages[0])

    def test_zero_clearance_settles_and_checks_without_queueing_motion(self):
        rig = Rig([-.24, 0., .005])
        def unexpected(*args):
            raise AssertionError('Zero clearance queued unnecessary motion')
        rig.toolhead.manual_move = unexpected
        rig.toolhead.dwell = unexpected
        rig.toolhead.get_last_move_time = unexpected
        rig.toolhead.wait_moves = unexpected
        query_times = []
        def query(now):
            query_times.append(now)
            return next(rig.states)
        rig.probe.mcu_probe.query_endstop = query
        rig.reactor.monotonic = lambda: len(rig.pauses)*.020
        result = rig.run(PROBE_RELEASE_CLEARANCE=0., PROBE_RELEASE_SYNC='queued', PROBE_RELEASE_FINAL=0)
        self.assertEqual(result, [[10., 20., .0025]])
        self.assertEqual(rig.pauses, [.020, .040])
        self.assertEqual(query_times, [0., .020, .020, .040])
        self.assertEqual(len(rig.homes), 2)
        self.assertIn('halt_z=-0.11750 final_z=-0.11750', rig.cmd.messages[0])

    def test_zero_clearance_retrigger_stops_before_next_probe(self):
        rig = Rig([-.24, 0., .005], states=[True, True])
        with self.assertRaisesRegex(ValueError, 're-triggered'):
            rig.run(PROBE_RELEASE_CLEARANCE=0., PROBE_RELEASE_SYNC='queued')
        self.assertEqual(rig.speeds, [10.])
        self.assertEqual(rig.probe.results, [])
        self.assertEqual(rig.pauses, [.020])

    def test_zero_clearance_keeps_release_failure_and_limit_guards(self):
        for failure in ('limit', 'release', 'already_clear'):
            rig = Rig([-.24])
            if failure == 'limit': rig.zmax = .5
            elif failure == 'release': rig.release_failure = True
            else: rig.states = iter([False])
            with self.assertRaises(ValueError):
                rig.run(PROBE_RELEASE_CLEARANCE=0., PROBE_RELEASE_SYNC='queued')
            self.assertEqual(rig.speeds, [10.])
            self.assertEqual(rig.probe.results, [])

    def test_release_messages_are_written_to_python_log(self):
        rig = Rig([-.24, 0., .005])
        with self.assertLogs(level='INFO') as captured: rig.run()
        self.assertEqual(len(captured.output), 3)
        self.assertIn('PROBE_RELEASE fast', captured.output[0])


if __name__ == '__main__': unittest.main(verbosity=2)
