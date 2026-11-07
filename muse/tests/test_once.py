import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from coach_monitor import Monitor
from runtime import write_json, file_lock
from finish_run import finish_run, load_pending

class OnceTests(unittest.TestCase):
    def fixture(self, directory):
        root = pathlib.Path(directory)
        cfg = json.loads((pathlib.Path(__file__).parents[1]/'config.sample.json').read_text())
        cfg.update(state_dir=directory, queue_json=str(root/'queue.json'), audio=False)
        cfg['close']['wait_before_standby_sec'] = 0
        session = {'session_id':'fixture', 'token':'fake', 'state':'live', 'queue_session':'run-test'}
        write_json(root/'session.json',session)
        write_json(root/'queue.json',{'session':'run-test','messages':[]})
        return cfg,session

    def test_repeated_cycle_no_double_send(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg,session=self.fixture(directory)
            cfg["send_mode"] = "per_km"
            result={'ok':True,'session':{},'status':{'distance_km':0.3,'duration_secs':100,'last_point_age_secs':1}}
            with patch.object(Monitor,'poll',return_value=result), patch.object(Monitor,'publish',return_value=True) as publish, patch('coach_monitor.time.sleep') as sleep:
                Monitor(cfg,session,[],{}).run_once()
                count=publish.call_count
                self.assertEqual(count,2)
                Monitor(cfg,session,[],{}).run_once()
                self.assertEqual(publish.call_count,count)
                sleep.assert_not_called()
            state=json.loads((pathlib.Path(directory)/'coach_state.json').read_text())
            self.assertTrue(state['start_announced'])
            self.assertEqual(state['sent'],1)

    def test_finish_key_is_queue_not_livetrack(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg,session=self.fixture(directory)
            root=pathlib.Path(directory)
            write_json(root/'finish_state.json',dict(session_id='fixture',queue_session='old-run',text='old',reason='end',stage='closed',wait_until=0))
            self.assertIsNone(load_pending(cfg,session))
            def publish(config,*args,**kwargs):
                if args[0]=='init': write_json(root/'queue.json',{'session':args[1],'messages':[]})
                return {'ok':True}
            with patch('finish_run.publish_retry',side_effect=publish):
                self.assertTrue(finish_run(cfg,session,'new','end',lambda x:None,once=True))
            self.assertEqual(json.loads((root/'finish_state.json').read_text())['queue_session'],'run-test')

    def test_finish_wait_does_not_sleep(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg,session=self.fixture(directory)
            cfg['close']['wait_before_standby_sec']=60
            with patch('finish_run.publish_retry',return_value={'ok':True}) as publish, patch('finish_run.time.sleep') as sleep:
                self.assertFalse(finish_run(cfg,session,'text','end',lambda x:None,once=True))
                self.assertFalse(finish_run(cfg,session,'text','end',lambda x:None,once=True))
                self.assertEqual([c.args[1] for c in publish.call_args_list],['post','history-append'])
                sleep.assert_not_called()

    def test_hook_lock_skips(self):
        import hook_once
        with tempfile.TemporaryDirectory() as directory:
            cfg,session=self.fixture(directory)
            path=pathlib.Path(directory)/'config.json';path.write_text(json.dumps(cfg))
            with file_lock(pathlib.Path(directory)/'.hook.lock'), patch.object(sys,'argv',['hook','--config',str(path)]), patch('hook_once.subprocess.run') as run:
                hook_once.main()
                run.assert_not_called()

    def test_replay_after_ack_before_cycle_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg,session=self.fixture(directory)
            cfg["send_mode"] = "per_km"
            response={'ok':True,'session':{},'status':{'distance_km':0.3,'duration_secs':100,'last_point_age_secs':1}}
            import subprocess
            child=subprocess.CompletedProcess([],0,json.dumps(response),'')
            with patch('coach_monitor.subprocess.run',return_value=child), patch.object(Monitor,'publish',return_value=True) as publish:
                first=Monitor(cfg,session,[],{})
                with patch.object(first,'save_cycle',side_effect=RuntimeError('crash')):
                    with self.assertRaises(RuntimeError): first.run_once()
                self.assertEqual(publish.call_count,2)
                Monitor(cfg,session,[],{}).run_once()
                self.assertEqual(publish.call_count,2)

    def test_watcher_once_prepares_without_spawning(self):
        import types, importlib, datetime
        credentials=types.ModuleType('dynamic_credentials')
        credentials.add_surrogate_to_request=lambda *a,**k:None
        credentials.read_json_response=lambda response:None
        with patch.dict(sys.modules,{'dynamic_credentials':credentials}):
            watcher=importlib.import_module('session_watcher')
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            cfg={'state_dir':directory}
            (root/'config.json').write_text(json.dumps(cfg))
            args=types.SimpleNamespace(config=str(root/'config.json'),dry_run=False,monitor='fixture')
            sess={'session_id':'fixture','token':'fake','received_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'start_id':'start'}
            with patch.object(watcher,'WATCHER_STATE',root/'watcher.json'), patch.object(watcher,'fetch_session',return_value=(200,sess)), patch.object(watcher,'publisher_call',return_value={'ok':True}) as publish, patch.object(watcher,'post_start_status',return_value=True) as status, patch.object(watcher.subprocess,'Popen') as popen:
                w=watcher.Watcher(args)
                w.cycle_once();w.cycle_once()
                self.assertEqual(publish.call_count,2)
                popen.assert_not_called()
                self.assertEqual(status.call_args.args,('start','monitoring'))
                local=json.loads((root/'session.json').read_text());local['state']='closed';write_json(root/'session.json',local)
                w.cycle_once()
                self.assertEqual(status.call_args.args,('start','closed'))
