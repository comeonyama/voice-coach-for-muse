import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coach_monitor import Monitor
from configuration import validate_config
from runtime import write_json

class CadenceTests(unittest.TestCase):
    def fixture(self, directory, **extra):
        cfg = dict(state_dir=directory, poll_script='fixture', publish_script='fixture',
                   send_mode='interval', send_interval_sec=180, **extra)
        session = dict(session_id='test', token='fake', queue_session='run-test', start_id='start', state='live')
        return cfg, session

    def test_distance_boundaries_and_jump_only_one_message(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg, session = self.fixture(directory)
            cfg.update(send_mode='distance', send_distance_km=.5, start_announce_text='')
            monitor = Monitor(cfg, session, [], {})
            self.assertFalse(monitor.send_due(.299, 1000))
            self.assertTrue(monitor.send_due(.3, 1000))
            monitor.sent=1; monitor.last_send_distance_km=.3
            self.assertFalse(monitor.send_due(.799, 1000))
            self.assertTrue(monitor.send_due(.8, 1000))
            monitor.save_cycle()
            response=dict(ok=True, session={}, status=dict(distance_km=2.1, duration_secs=800))
            with patch.object(Monitor, 'poll', return_value=response), patch.object(Monitor, 'say', return_value=True) as say:
                monitor.run_once()
                self.assertEqual(say.call_count, 1)
                self.assertEqual(monitor.last_send_distance_km, 2.1)
                monitor.run_once()
                self.assertEqual(say.call_count, 1)
            self.assertFalse(monitor.send_due(2.599, 1000))
            self.assertTrue(monitor.send_due(2.6, 1000))

    def test_failed_distance_send_keeps_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg, session = self.fixture(directory)
            cfg.update(send_mode='distance',send_distance_km=.5,start_announce_text='')
            monitor=Monitor(cfg,session,[],{})
            monitor.sent=1; monitor.last_send_distance_km=.3; monitor.save_cycle()
            response=dict(ok=True,session={},status=dict(distance_km=.8,duration_secs=300))
            with patch.object(Monitor,'poll',return_value=response), patch.object(Monitor,'say',return_value=False):
                monitor.run_once()
            self.assertEqual(monitor.last_send_distance_km,.3)
            self.assertTrue(monitor.send_due(.8,1000))

    def test_old_state_migration_and_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg, session = self.fixture(directory)
            cfg['send_mode']='per_km'
            write_json(Path(directory)/'coach_state.json', dict(queue_session='run-test', last_send_km=2, sent=3))
            monitor=Monitor(cfg,session,[],{}); monitor.restore_cycle()
            self.assertEqual(monitor.last_send_distance_km, 2)
            self.assertEqual(monitor.cadence(), ('distance',1.0))
            self.assertFalse(monitor.send_due(2.999, 1000))
            self.assertTrue(monitor.send_due(3.0, 1000))
            monitor.save_cycle()
            restored=Monitor(cfg,session,[],{}); restored.restore_cycle()
            self.assertEqual(restored.last_send_distance_km,2)

    def test_override_and_config_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg,session=self.fixture(directory)
            for value, expected in [(None,('interval',180)),('invalid',('interval',180)),
                                    ('per_km',('distance',1)),('per_500m',('distance',.5)),('interval:180',('interval',180))]:
                with self.subTest(value=value):
                    session['speech_interval']=value
                    monitor=Monitor(cfg,session,[],{})
                    self.assertEqual(monitor.cadence(),expected)
            cfg.update(send_mode='distance', send_distance_km=.5)
            session['speech_interval']='interval:180'
            self.assertEqual(Monitor(cfg,session,[],{}).cadence(),('interval',180))

    def test_live_preference_reloaded_and_different_run_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg, session=self.fixture(directory)
            monitor=Monitor(cfg,session,[],{}); monitor.sent=1
            monitor.last_send=100; monitor.last_send_distance_km=.3
            write_json(Path(directory)/'session.json', dict(session,speech_interval='per_500m'))
            self.assertTrue(monitor.send_due(.8,110))
            write_json(Path(directory)/'session.json', dict(session,speech_interval='interval:180'))
            self.assertFalse(monitor.send_due(.8,279))
            self.assertTrue(monitor.send_due(.8,280))
            write_json(Path(directory)/'session.json', dict(session,speech_interval='bad'))
            self.assertEqual(monitor.cadence(),('interval',180))
            write_json(Path(directory)/'session.json', dict(session,queue_session='other',speech_interval='per_km'))
            self.assertEqual(monitor.cadence(),('interval',180))

    def test_interval_first_cycle_and_persisted_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg, session = self.fixture(directory)
            response = dict(ok=True, session={}, status=dict(distance_km=.3, duration_secs=100))
            with patch.object(Monitor, 'poll', return_value=response), patch.object(Monitor, 'say', return_value=True) as say:
                for now, count in [(1000, 1), (1179, 1), (1180, 2), (1359, 2), (1360, 3)]:
                    with self.subTest(now=now), patch('coach_monitor.time.time', return_value=now):
                        Monitor(cfg, session, [], {}).run_once()
                        self.assertEqual(say.call_count, count)
                state = json.loads((Path(directory)/'coach_state.json').read_text())
                self.assertEqual(state['sent'], 2)
                self.assertEqual(state['last_send'], 1360)

    def test_interval_waits_for_live_data_after_deadline(self):
        for empty in ({}, dict(distance_km=0, duration_secs=0, current_hr=None),
                      dict(distance_km=0, duration_secs=0, current_hr=0)):
            for live in (dict(distance_km=.1), dict(duration_secs=200), dict(current_hr=100)):
                with self.subTest(empty=empty, live=live), tempfile.TemporaryDirectory() as directory:
                    cfg, session = self.fixture(directory)
                    response = dict(ok=True, session={}, status=empty)
                    with patch.object(Monitor, 'poll', return_value=response), patch.object(Monitor, 'say', return_value=True) as say:
                        for now in (1000, 1180, 1300):
                            with patch('coach_monitor.time.time', return_value=now):
                                Monitor(cfg, session, [], {}).run_once()
                        self.assertEqual(say.call_count, 1)  # start announcement only
                        response['status'] = live
                        with patch('coach_monitor.time.time', return_value=1320):
                            Monitor(cfg, session, [], {}).run_once()
                        self.assertEqual(say.call_count, 2)

    def test_interval_legacy_zero_anchor_uses_saved_start(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg, session = self.fixture(directory)
            write_json(Path(directory)/'coach_state.json', dict(queue_session='run-test',
                       last_send=0, started_at=1000, start_announced=True))
            response = dict(ok=True, session={}, status=dict(distance_km=.3, duration_secs=100))
            with patch.object(Monitor, 'poll', return_value=response), patch.object(Monitor, 'say', return_value=True) as say:
                for now, count in [(1100, 0), (1180, 1)]:
                    with patch('coach_monitor.time.time', return_value=now):
                        Monitor(cfg, session, [], {}).run_once()
                    self.assertEqual(say.call_count, count)

    def test_interval_continuous_run_waits_first_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg, session = self.fixture(directory, max_messages=1, poll_interval_sec=180)
            response = dict(ok=True, session={}, status=dict(distance_km=.3, duration_secs=100))
            with patch('coach_monitor.time.time', return_value=1000) as clock, patch.object(Monitor, 'poll', return_value=response), patch.object(Monitor, 'say', return_value=True) as say:
                def sleep(seconds):
                    # Advance the fake clock; only the announcement may have
                    # been sent before the first full interval has passed.
                    if clock.return_value == 1000:
                        self.assertEqual(say.call_count, 1)
                    clock.return_value += seconds
                with patch('coach_monitor.time.sleep', side_effect=sleep):
                    Monitor(cfg, session, [], {}).run()
                self.assertEqual(say.call_count, 2)

    def test_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg,_=self.fixture(directory)
            for mode in ('per_km','distance','interval'):
                for step in (.5,1): validate_config(dict(cfg,send_mode=mode,send_distance_km=step))
            for step in (0,-1,.7,True,'0.5'):
                with self.assertRaises(ValueError): validate_config(dict(cfg,send_distance_km=step))

    def test_watcher_updates_only_preference_on_live_same_start(self):
        credentials=types.ModuleType('dynamic_credentials')
        credentials.add_surrogate_to_request=lambda *a,**k:None
        credentials.read_json_response=lambda *a,**k:None
        with patch.dict(sys.modules,{'dynamic_credentials':credentials}):
            watcher=importlib.import_module('session_watcher')
        with tempfile.TemporaryDirectory() as directory:
            cfg,session=self.fixture(directory)
            root=Path(directory); write_json(root/'config.json',cfg); write_json(root/'session.json',session)
            args=types.SimpleNamespace(config=str(root/'config.json'),dry_run=False,monitor='fixture')
            with patch.object(watcher,'WATCHER_STATE',root/'watcher.json'), patch.object(watcher,'fetch_session',return_value=(200,dict(session,speech_interval='per_500m'))), patch.object(watcher,'publisher_call') as publish:
                w=watcher.Watcher(args); w.cycle_once()
                updated=json.loads((root/'session.json').read_text())
                self.assertEqual(updated,dict(session,speech_interval='per_500m'))
                self.assertEqual(Monitor(cfg,session,[],{}).cadence(),('distance',.5))
                publish.assert_not_called()
                write_json(root/'session.json',dict(session,state='closed'))
                with patch.object(watcher,'post_start_status',return_value=True): w.cycle_once()
                self.assertEqual(json.loads((root/'session.json').read_text())['state'],'closed')
                self.assertNotIn('speech_interval',json.loads((root/'session.json').read_text()))
