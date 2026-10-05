"""离线回归检查：python verify_changes.py；不创建窗口，不发送请求。"""
import threading
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

import requests
import main


METHODS = (
    '_process_watch_result', '_flush_scheduled_signs', '_schedule_sign_later',
    '_do_sign_with_log', '_do_sign', '_do_qr_sign', '_fetch_qr_data',
    '_qr_probe_cooldown', '_schedule_next_watch', '_queue_next_watch',
    '_cancel_next_watch', '_run_next_watch', '_parse_end_time',
    '_extract_remaining_seconds', '_login_task', 'sign',
    'sign_location', '_do_location_sign', '_fetch_room_location',
)
Core = type('Core', (), {name: getattr(main.DuifenyiApp, name) for name in METHODS})


class RegressionChecks(unittest.TestCase):
    def setUp(self):
        main.Course.check_list = []
        main.Course.flag = True
        main.Course.id = 'course'
        self.app = a = Core()
        a.host = 'https://offline.invalid'
        a.req_timeout = 8
        a._cached_uid = 'student'
        a.is_monitoring = True
        a.log_mode = 'simple'
        a._session_lock = threading.Lock()
        a._expired_signs = set()
        a._scheduled_signs = {}
        a._countdown_logged = set()
        a._qr_first_seen = {}
        a._signed_states = set()
        a._qr_probe_counter = 0
        a._active_trigger_seconds = 0
        a._monitor_generation = 1
        a._watch_job = None
        a._watch_retry_at = 0.0
        a.check_interval_min = a.check_interval_max = 1.0
        a.log = Mock()
        a.log_countdown = Mock()
        a.log_celebration = Mock()
        a.update_last_line = Mock()
        a.watching_sign = Mock()
        a.ui_call = lambda fn, *args, **kwargs: fn(*args, **kwargs)
        a.x = Mock()
        a._fetch_qr_data = Mock(return_value='image')
        a._get_qr_state = Mock(return_value='fallback-state')
        a.sign = Mock(return_value=True)
        self.timers = {}
        self.serial = 0

        def after(delay, callback):
            self.serial += 1
            key = f'job-{self.serial}'
            self.timers[key] = (delay, callback)
            return key

        a.after = after
        a.after_cancel = lambda key: self.timers.pop(key, None)

    @staticmethod
    def activity(kind='1', identifier='active', **extra):
        return {'ID': identifier, 'StatusID': '2', 'CheckInType': kind,
                'CheckInCode': '123456', **extra}

    def poll(self, *rows):
        self.app._process_watch_result({'msg': '1', 'rows': list(rows)})

    def test_temporary_failure_retries_same_activity(self):
        self.app.sign.side_effect = ['retry', True]
        self.poll(self.activity())
        self.assertNotIn('active', main.Course.check_list)
        self.assertNotIn('active', self.app._expired_signs)
        self.poll(self.activity())
        self.assertIn('active', main.Course.check_list)
        self.assertEqual(self.app.sign.call_count, 2)

    def test_network_timeout_is_retryable(self):
        self.app.sign = Core.sign.__get__(self.app)
        self.app.x.get.side_effect = requests.exceptions.Timeout('offline timeout')
        self.poll(self.activity())
        self.assertEqual(main.Course.check_list, [])
        self.assertFalse(self.app._expired_signs)

    def test_confirmed_expiry_is_not_retried(self):
        self.app.sign.return_value = 'expired'
        self.poll(self.activity())
        self.poll(self.activity())
        self.app.sign.assert_called_once()
        self.assertIn('active', self.app._expired_signs)

    def test_rate_limit_has_one_timer_and_full_backoff(self):
        self.app._schedule_next_watch()
        self.app.sign.return_value = 'ratelimit'
        with patch.object(main.time, 'monotonic', return_value=100.0):
            self.poll(self.activity())
            self.app._schedule_next_watch()
        self.assertEqual(len(self.timers), 1)
        self.assertEqual(next(iter(self.timers.values()))[0], 6000)
        self.assertEqual(main.Course.check_list, [])

    def test_cancel_and_stale_generation(self):
        self.app._schedule_next_watch()
        self.app._cancel_next_watch()
        self.assertFalse(self.timers)
        self.app._queue_next_watch(1000, 0)
        self.assertFalse(self.timers)

    def test_delayed_failures_and_rate_limits_keep_schedule(self):
        entry = ('1', '123456', '签到码', datetime.now().timestamp() - 1)
        for result in ('retry', 'ratelimit'):
            with self.subTest(result=result):
                self.app._scheduled_signs = {'active': entry}
                self.app.sign.return_value = result
                self.app._flush_scheduled_signs()
                self.assertEqual(self.app._scheduled_signs['active'], entry)
                self.assertNotIn('active', main.Course.check_list)

    def test_login_special_characters_are_preserved(self):
        captured = {}
        password = 'a+b&c%41=中文'

        def post(url, **kwargs):
            prepared = requests.Request('POST', url, data=kwargs['data']).prepare()
            captured.update(parse_qs(prepared.body))
            return SimpleNamespace(status_code=200, json=lambda: {'msgbox': '测试拒绝'})

        self.app.x.post.side_effect = post
        self.app.pwd_login_btn = SimpleNamespace(configure=Mock())
        self.app._login_task('user+name', password)
        self.assertEqual(captured['loginname'], ['user+name'])
        self.assertEqual(captured['password'], [password])

    def test_qr_primary_path_needs_no_image_or_decoder(self):
        self.poll(self.activity('2'))
        self.app.sign.assert_called_once_with('active', is_qr=True)
        self.app._fetch_qr_data.assert_not_called()
        self.app._get_qr_state.assert_not_called()
        self.assertIn('active', main.Course.check_list)

    def test_qr_timeout_and_limit_do_not_trigger_decoder(self):
        for result in ('retry', 'ratelimit'):
            with self.subTest(result=result):
                self.app.sign.return_value = result
                self.assertEqual(self.app._do_qr_sign('active'), result)
                self.app._fetch_qr_data.assert_not_called()
                self.app._get_qr_state.assert_not_called()

    def test_qr_fallback_reuses_one_image_response(self):
        self.app.sign.side_effect = [False, True]
        self.poll(self.activity('2'))
        self.app._fetch_qr_data.assert_called_once_with()
        self.app._get_qr_state.assert_called_once_with('image')
        self.assertEqual(self.app.sign.call_args_list[-1].args, ('fallback-state',))
        self.assertIn('active', main.Course.check_list)

    def test_qr_fallback_failure_is_not_blacklisted(self):
        self.app.sign.return_value = False
        self.app._fetch_qr_data.return_value = None
        self.poll(self.activity('2'))
        self.assertNotIn('active', main.Course.check_list)
        self.assertNotIn('active', self.app._expired_signs)

    def test_qr_fetch_distinguishes_outage_and_empty_image(self):
        self.app.x.post.return_value = SimpleNamespace(status_code=503)
        self.assertIsNone(Core._fetch_qr_data(self.app))
        self.app.x.post.side_effect = requests.exceptions.Timeout()
        self.assertIsNone(Core._fetch_qr_data(self.app))
        self.app.x.post.side_effect = None
        self.app.x.post.return_value = SimpleNamespace(
            status_code=200, json=lambda: {'msg': '1', 'data': ''})
        self.assertEqual(Core._fetch_qr_data(self.app), '')

    def test_only_latest_qr_activity_is_submitted(self):
        self.poll(
            self.activity('2', 'old', CreaterDate='2026-06-11 08:00:00'),
            self.activity('2', 'new', CreaterDate='2026-06-11 09:00:00'),
        )
        self.app.sign.assert_called_once_with('new', is_qr=True)
        self.assertIn('old', self.app._expired_signs)

    def test_delayed_qr_does_not_fetch_images_in_advance(self):
        self.app._active_trigger_seconds = 10
        self.poll(self.activity('2'))
        self.app.sign.assert_not_called()
        self.app._fetch_qr_data.assert_not_called()
        self.assertIn('active', self.app._scheduled_signs)

    def test_location_rejection_is_attempted_only_once(self):
        self.app._active_lon = '113.123456'
        self.app._active_lat = '23.654321'
        self.app.x.post.return_value = SimpleNamespace(
            status_code=200,
            json=lambda: {'msgbox': '不在教室范围，距离：167999.56米！'},
        )
        self.poll(self.activity('3'))
        self.poll(self.activity('3'))
        self.assertEqual(self.app.x.post.call_count, 1)
        self.assertIn('active', self.app._expired_signs)

    def test_location_failure_does_not_discard_next_activity(self):
        self.app._active_lon = '113.123456'
        self.app._active_lat = '23.654321'
        self.app.x.post.return_value = SimpleNamespace(
            status_code=200, json=lambda: {'msgbox': '不在教室范围'})
        self.poll(self.activity('3', 'first'))
        self.poll(self.activity('3', 'second'))
        self.assertEqual(self.app.x.post.call_count, 2)

    def test_location_timeout_also_stops_this_activity(self):
        self.app._active_lon = '113.123456'
        self.app._active_lat = '23.654321'
        self.app.x.get.side_effect = requests.exceptions.Timeout()
        self.poll(self.activity('3'))
        self.poll(self.activity('3'))
        self.assertEqual(self.app.x.get.call_count, 1)
        self.assertIn('active', self.app._expired_signs)

    def test_delayed_location_failure_is_not_rescheduled(self):
        self.app._active_lon = '113.123456'
        self.app._active_lat = '23.654321'
        self.app.x.post.return_value = SimpleNamespace(
            status_code=200, json=lambda: {'msgbox': '不在教室范围'})
        self.app._scheduled_signs['active'] = (
            '3', '', '定位', datetime.now().timestamp() - 1)
        self.app._flush_scheduled_signs()
        self.poll(self.activity('3'))
        self.assertEqual(self.app.x.post.call_count, 1)
        self.assertNotIn('active', self.app._scheduled_signs)

    def test_location_rate_limit_keeps_original_backoff_behavior(self):
        self.app._active_lon = '113.123456'
        self.app._active_lat = '23.654321'
        self.app.x.post.return_value = SimpleNamespace(
            status_code=200, json=lambda: {'msgbox': '操作频繁，请等待'})
        self.poll(self.activity('3'))
        self.assertNotIn('active', self.app._expired_signs)
        self.assertGreaterEqual(next(iter(self.timers.values()))[0], 5990)


if __name__ == '__main__':
    unittest.main(verbosity=2)
