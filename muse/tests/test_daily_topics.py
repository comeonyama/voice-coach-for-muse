"""Daily stock behavior with synthetic content and local state only."""
import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coach_monitor import Monitor
from runtime import write_json


class DailyTopicsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = dict(state_dir=str(self.root), poll_script='fixture', publish_script='fixture',
                        queue_json=str(self.root / 'queue.json'), audio=False, milestones_km=[],
                        progress_every=0, motivational_every=0, timezone_offset_hours=9)
        self.session = dict(session_id='fixture', token='fake', state='live', queue_session='run-test')
        write_json(self.root / 'queue.json', {'session':'run-test', 'messages':[]})
        self.today = patch.object(Monitor, '_today', return_value='2026-11-08')
        self.today.start()
        self.addCleanup(self.today.stop)

    def monitor(self, topics=None, day='2026-11-08', neta=(), **stock):
        if topics is not None:
            stock['daily_topics'] = topics
        if day is not None:
            stock['daily_topics_date'] = day
        return Monitor(self.cfg, self.session, list(neta), stock)

    def pick(self, monitor):
        return monitor.pick_comment(1.2, 300, None, None, 10000)[0]

    def test_order_and_used_skip(self):
        mon = self.monitor(['使用済みの話題', '今日の話題'], neta=['ラン用の話題'], tips=['汎用の話題'])
        mon.used.add('使用済みの話題')
        self.assertEqual([self.pick(mon), self.pick(mon), self.pick(mon)],
                         ['ラン用の話題', '今日の話題', '汎用の話題'])
        self.assertEqual(mon.daily, [])

    def test_stale_missing_and_invalid_topics_are_ignored(self):
        for topics, day in [(['昨日の話題'], '2026-11-07'), (['未来の話題'], '2026-11-09'),
                            (['日付なし'], None), ('文字列', '2026-11-08'),
                            ({'x':'話題'}, '2026-11-08'), ([None, '話題'], '2026-11-08'),
                            (['話題'], 20261108), (None, '2026-11-08')]:
            with self.subTest(topics=topics, day=day):
                mon = self.monitor(topics, day, tips=['汎用の話題'])
                self.assertEqual(mon.daily, [])
                self.assertEqual(self.pick(mon), '汎用の話題')

    def test_once_state_restores_remaining_topics_and_used(self):
        stock = ['話題一', '話題二', '話題三']
        mon = self.monitor(stock, tips=['汎用の話題'])
        self.assertEqual(self.pick(mon), '話題一')
        mon.used.add('話題一')
        mon.save_cycle()
        checkpoint = json.loads((self.root / 'coach_state.json').read_text())
        self.assertEqual(checkpoint['daily'], ['話題二', '話題三'])
        resumed = self.monitor(stock, tips=['汎用の話題'])
        resumed.restore_cycle()
        self.assertEqual(resumed.daily, ['話題二', '話題三'])
        self.assertIn('話題一', resumed.used)
        self.assertEqual(self.pick(resumed), '話題二')

    def test_real_once_cycles_consume_daily_in_order(self):
        self.cfg.update(send_mode='per_km', start_announce_text='')
        stock = ['一つ目の話題', '二つ目の話題']
        responses = [
            {'ok':True, 'session':{}, 'status':{'distance_km':0.3, 'duration_secs':100}},
            {'ok':True, 'session':{}, 'status':{'distance_km':1.3, 'duration_secs':400}},
        ]
        with patch.object(Monitor, 'poll', side_effect=responses), \
             patch.object(Monitor, 'publish', return_value=True) as publish:
            for _ in responses:
                self.assertEqual(self.monitor(stock).run_once(), 0)
        self.assertEqual(publish.call_count, 2)
        self.assertTrue(publish.call_args_list[0].args[2].endswith('一つ目の話題'))
        self.assertTrue(publish.call_args_list[1].args[2].endswith('二つ目の話題'))
        state = json.loads((self.root / 'coach_state.json').read_text())
        self.assertEqual(state['daily'], [])
        self.assertTrue({'一つ目の話題', '二つ目の話題'} <= set(state['used']))

    def test_saved_old_or_invalid_date_never_restores_topics(self):
        mon = self.monitor(['古い話題'], tips=['汎用の話題'])
        mon.save_cycle()
        for topics, day in [(['古い話題'], '2026-11-07'), (None, None), ('bad', '2026-11-08')]:
            with self.subTest(topics=topics, day=day):
                resumed = self.monitor(topics, day, tips=['汎用の話題'])
                resumed.restore_cycle()
                self.assertEqual(self.pick(resumed), '汎用の話題')

    def test_changed_same_day_stock_is_not_hidden_by_checkpoint(self):
        mon = self.monitor(['元の話題'])
        self.assertEqual(self.pick(mon), '元の話題')
        mon.save_cycle()
        resumed = self.monitor(['更新後の話題'])
        resumed.restore_cycle()
        self.assertEqual(self.pick(resumed), '更新後の話題')

    def test_crossing_midnight_does_not_speak_stale_topics(self):
        mon = self.monitor(['日付が変わる前の話題'], tips=['汎用の話題'])
        with patch.object(Monitor, '_today', return_value='2026-11-09'):
            self.assertEqual(self.pick(mon), '汎用の話題')
            self.assertEqual(mon.daily, [])

    def test_pending_delivery_does_not_resend_yesterdays_topic(self):
        mon = self.monitor(['昨日の話題'], tips=['汎用の話題'])
        state = mon.cycle_state()
        state['inflight'] = {'deliveries': [{'id':'run-001', 'text':'案内。昨日の話題', 'ok':False, 'daily_topic':'昨日の話題'}]}
        write_json(self.root / 'coach_state.json', state)
        with patch.object(Monitor, '_today', return_value='2026-11-09'):
            resumed = self.monitor(['今日の話題'], '2026-11-09', tips=['汎用の話題'])
            resumed.restore_cycle()
            resumed.once = True
            resumed.delivery_index = 0
            resumed.cycle_base = resumed.cycle_state()
            with patch.object(resumed, 'publish', return_value=True) as publish:
                self.assertTrue(resumed.say('案内。' + self.pick(resumed)))
            self.assertEqual(publish.call_args.args, ('post', 'run-001', '案内。今日の話題'))

    def test_other_pool_with_same_suffix_is_not_reclassified(self):
        mon = self.monitor(['昨日の話題'])
        state = mon.cycle_state()
        state['inflight'] = {'deliveries': [{'id':'run-001', 'text':'案内。昨日の話題', 'ok':False}]}
        write_json(self.root / 'coach_state.json', state)
        with patch.object(Monitor, '_today', return_value='2026-11-09'):
            resumed = self.monitor(['今日の話題'], '2026-11-09')
            resumed.restore_cycle()
            self.assertEqual(resumed.inflight['deliveries'][0]['text'], '案内。昨日の話題')

    def test_confirmed_old_delivery_does_not_consume_new_daily_stock(self):
        self.cfg['send_mode'] = 'per_km'
        mon = self.monitor(['昨日の話題'])
        state = mon.cycle_state()
        state.update(start_announced=True, last_send=100,
                     inflight={'response': {'ok':True, 'session':{}, 'status':{'distance_km':0.3, 'duration_secs':100}},
                               'deliveries': [{'id':'run-001', 'text':'案内。昨日の話題', 'ok':True, 'daily_topic':'昨日の話題'}]})
        write_json(self.root / 'coach_state.json', state)
        with patch.object(Monitor, '_today', return_value='2026-11-09'):
            resumed = self.monitor(['今日の話題'], '2026-11-09')
            with patch.object(resumed, 'publish', return_value=True) as publish:
                self.assertEqual(resumed.run_once(), 0)
            publish.assert_not_called()
            saved = json.loads((self.root / 'coach_state.json').read_text())
            self.assertEqual(saved['daily'], ['今日の話題'])
            self.assertIn('昨日の話題', saved['used'])
            self.assertNotIn('今日の話題', saved['used'])

    def test_unchanged_pending_delivery_keeps_existing_text(self):
        mon = self.monitor(['今日の話題'])
        state = mon.cycle_state()
        state['inflight'] = {'deliveries': [{'id':'run-001', 'text':'元の案内。今日の話題', 'ok':False}]}
        write_json(self.root / 'coach_state.json', state)
        resumed = self.monitor(['今日の話題'])
        resumed.restore_cycle()
        resumed.once = True
        resumed.delivery_index = 0
        resumed.cycle_base = resumed.cycle_state()
        with patch.object(resumed, 'publish', return_value=True) as publish:
            self.assertTrue(resumed.say('別の案内'))
        self.assertEqual(publish.call_args.args, ('post', 'run-001', '元の案内。今日の話題'))

    def test_legacy_checkpoint_loads_current_daily_stock(self):
        write_json(self.root / 'coach_state.json', {'queue_session':'run-test', 'neta':[], 'tips':[]})
        mon = self.monitor(['新しい素材'])
        mon.restore_cycle()
        self.assertEqual(self.pick(mon), '新しい素材')

    def test_timezone_offset_determines_date(self):
        real_datetime = datetime.datetime
        class FixedDateTime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return real_datetime(2026, 11, 7, 16, tzinfo=datetime.timezone.utc).astimezone(tz)
        self.today.stop()
        with patch('coach_monitor.datetime.datetime', FixedDateTime):
            self.cfg['timezone_offset_hours'] = 9
            self.assertEqual(self.monitor(['日本時間の話題'], '2026-11-08').daily, ['日本時間の話題'])
            self.cfg['timezone_offset_hours'] = 0
            self.assertEqual(self.monitor(['日本時間の話題'], '2026-11-08').daily, [])
        self.today.start()


if __name__ == '__main__':
    unittest.main()
