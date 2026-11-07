"""Synthetic points only: no Garmin, publisher or device access."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import livetrack_poll as poller
from coach_monitor import Monitor
from configuration import validate_config
from runtime import write_json


def point(distance, altitude, nested=False):
    fields = dict(totalDistanceMeters=distance, totalDurationSecs=distance * .36,
                  heartRateBeatsPerMin=130)
    if altitude is not None:
        fields['altitude'] = altitude
    return {'fitnessPointData': fields} if nested else fields


class PollAltitudeTests(unittest.TestCase):
    def poll(self, path, points):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['poll', '--session-id', 'test', '--token', 'fake', '--state', str(path)]), \
                patch.object(poller, 'bootstrap', return_value=(None, '')), \
                patch.object(poller, 'http_json', side_effect=[(200, {}), (200, {'trackPoints': points})]), \
                contextlib.redirect_stdout(output):
            poller.main()
        return json.loads(output.getvalue())

    def test_jitter_gradual_climb_and_restart_match_single_batch(self):
        points = [point(0, 100), point(100, 100.5), point(200, 99.5),
                  point(300, 100), point(400, 101), point(500, 102, True),
                  point(600, 105), point(800, 101), point(1000, 101)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            all_at_once = self.poll(root/'all.json', points)
            self.poll(root/'once.json', points[:5])
            resumed = self.poll(root/'once.json', points[5:])
            split = resumed['new_splits'][0]
            self.assertEqual((split['elev_gain_m'], split['elev_loss_m']), (5, 4))
            self.assertEqual(resumed['status']['total_ascent_m'], 5)
            self.assertEqual(resumed['status']['current_altitude_m'], 101)
            self.assertEqual(resumed, all_at_once)
            self.assertEqual(self.poll(root/'once.json', [])['status']['total_ascent_m'], 5)

    def test_multiple_km_crossings_allocate_without_double_counting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'state.json'
            result = self.poll(path, [point(0, 100), point(500, 110), point(2500, 150)])
            self.assertEqual([s['elev_gain_m'] for s in result['new_splits']], [20, 20])
            state = json.loads(path.read_text())
            self.assertEqual(state['km_elev_gain_m'], 10)
            self.assertEqual(result['status']['total_ascent_m'], 50)

    def test_old_state_defaults_without_inventing_prior_altitude(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'state.json'
            write_json(path, dict(session_id='test', last_point={'dist_m':500, 'dur_s':180},
                                 km_done=0, points_total=1))
            result = self.poll(path, [point(600, 100), point(1000, 110)])
            self.assertTrue(result['ok'])
            self.assertEqual(result['new_splits'][0]['elev_gain_m'], 10)
            self.assertEqual(result['status']['total_ascent_m'], 10)

    def test_missing_and_nonfinite_altitude_break_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'state.json'
            result = self.poll(path, [point(0, 100), point(100, 105), point(200, None),
                                     point(300, 200), point(400, 205), point(500, 'NaN'),
                                     point(600, 300), point(1000, None)])
            self.assertIsNone(result['status']['current_altitude_m'])
            self.assertEqual(result['status']['total_ascent_m'], 10)
            self.assertEqual(result['new_splits'][0]['elev_gain_m'], 10)


class MonitorAltitudeTests(unittest.TestCase):
    def fixture(self, directory):
        cfg = dict(state_dir=directory, poll_script='fixture', publish_script='fixture',
                   queue_json=str(Path(directory)/'queue.json'), audio=False,
                   target_pace={'fast_limit_sec':330}, milestones_km=[], start_announce_text='')
        session = dict(session_id='test', token='fake', state='live', queue_session='run-test')
        write_json(Path(directory)/'queue.json', {'session':'run-test', 'messages':[]})
        return cfg, session

    def comment(self, monitor, distance=1.05, pace=300, duration=360):
        return monitor.pick_comment(distance, duration, None, pace, 10000)

    def test_fast_advice_flat_uphill_downhill_and_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg, session = self.fixture(directory)
            for gain, loss, enabled, expected in [(0, 0, True, True), (30, 0, True, True),
                                                  (0, 30, True, False), (25, 30, True, True),
                                                  (0, 30, False, True)]:
                with self.subTest(gain=gain, loss=loss, enabled=enabled):
                    cfg['altitude'] = {'enabled':enabled}
                    monitor = Monitor(cfg, session, [], {})
                    monitor.last_elevation_split = dict(km=1, elev_gain_m=gain, elev_loss_m=loss)
                    self.assertEqual(self.comment(monitor)[1], expected)
                    self.assertEqual(monitor.last_fast_advice, 10000 if expected else 0)

    def test_downhill_threshold_freshness_and_missing_split(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg, session = self.fixture(directory)
            cfg['altitude'] = {'downhill_net_loss_m':40}
            monitor = Monitor(cfg, session, [], {})
            monitor.last_elevation_split = dict(km=1, elev_gain_m=0, elev_loss_m=30)
            self.assertTrue(self.comment(monitor)[1])
            cfg['altitude']['downhill_net_loss_m'] = 20
            for distance in (1.3, 2.0):
                monitor.last_fast_advice = 0
                self.assertTrue(self.comment(monitor, distance=distance)[1])
            monitor.last_fast_advice = 0
            monitor.last_elevation_split = None
            self.assertTrue(self.comment(monitor)[1])

    def test_climb_only_as_fallback_in_matching_km_and_no_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg, session = self.fixture(directory)
            monitor = Monitor(cfg, session, ['通常素材'], {})
            monitor.last_elevation_split = dict(km=1, elev_gain_m=30, elev_loss_m=0)
            self.assertEqual(self.comment(monitor, pace=360)[0], '通常素材')
            text, _ = self.comment(monitor, pace=360)
            self.assertIn('約30メートル登りました', text)
            monitor.used.add(text)
            self.assertNotIn('登りました', self.comment(monitor, pace=360)[0])
            monitor.used.clear()
            self.assertNotIn('登りました', self.comment(monitor, distance=2, pace=360)[0])
            cfg['altitude'] = {'enabled':False}
            self.assertNotIn('登りました', self.comment(monitor, pace=360)[0])

    def test_important_advice_and_hydration_precede_climb(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg, session = self.fixture(directory)
            cfg['hydration'] = {'enabled':True, 'every_min':40, 'min_run_min':75}
            monitor = Monitor(cfg, session, [], {})
            monitor.last_elevation_split = dict(km=1, elev_gain_m=30, elev_loss_m=0)
            self.assertTrue(self.comment(monitor)[1])
            self.assertIn('給水', self.comment(monitor, pace=360, duration=4800)[0])

    def test_finish_threshold_disabled_and_no_live(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg, session = self.fixture(directory)
            for ascent, enabled, live, expected in [(49, True, True, False), (50, True, True, True),
                                                    (100, False, True, False), (100, True, False, False)]:
                with self.subTest(ascent=ascent, enabled=enabled, live=live):
                    cfg['altitude'] = {'enabled':enabled, 'finish_ascent_m':50}
                    monitor = Monitor(cfg, session, [], {})
                    monitor.total_ascent_m = ascent
                    monitor.saw_live = live
                    with patch('coach_monitor.finish_run', return_value=True) as finish:
                        monitor.finish(5 if live else 0, 360, 'ended')
                    self.assertEqual('獲得標高' in finish.call_args.args[2], expected)

    def test_once_restore_and_end_without_status(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg, session = self.fixture(directory)
            split = dict(km=1, elev_gain_m=0, elev_loss_m=30)
            response = dict(ok=True, session={}, status=dict(distance_km=1.05, duration_secs=360,
                            total_ascent_m=65), new_splits=[split])
            with patch.object(Monitor, 'poll', return_value=response), patch.object(Monitor, 'publish', return_value=True):
                Monitor(cfg, session, [], {}).run_once()
            resumed = Monitor(cfg, session, [], {})
            resumed.restore_cycle()
            self.assertEqual(resumed.last_elevation_split, split)
            self.assertEqual(resumed.total_ascent_m, 65)
            with patch.object(Monitor, 'poll', return_value={'ok':False, 'session':{'ended':True}}), \
                    patch('coach_monitor.finish_run', return_value=True) as finish:
                resumed.run_once()
            self.assertIn('獲得標高は約65メートル', finish.call_args.args[2])

    def test_seed_ascent_from_poller_state(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg, session = self.fixture(directory)
            write_json(Path(directory)/'poll_state.json', dict(session_id='test', total_ascent_m=80,
                       last_point=dict(dist_m=5000, dur_s=1800)))
            monitor = Monitor(cfg, session, [], {})
            self.assertEqual(monitor.total_ascent_m, 80)
            self.assertTrue(monitor.saw_live)

    def test_invalid_altitude_settings_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg, _ = self.fixture(directory)
            for altitude in ('invalid', {'enabled':'false'}, {'climb_gain_m':-1},
                             {'downhill_net_loss_m':0}, {'finish_ascent_m':float('nan')}):
                with self.subTest(altitude=altitude), self.assertRaises(ValueError):
                    validate_config(dict(cfg, altitude=altitude))


if __name__ == '__main__':
    unittest.main()
