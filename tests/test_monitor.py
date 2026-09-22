import contextlib
import datetime
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import database
import demo
import monitor
import notifier


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.patch = patch.object(database, 'DB_PATH', str(Path(self.temp.name) / 'test.db'))
        self.patch.start()
        database.init_db()
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None)
        self.patch.stop()
        self.temp.cleanup()

    def fetcher(self, vehicles, success=True):
        fetcher = Mock()
        fetcher.fetch.return_value = (vehicles, success)
        fetcher.last_inventory_vins = {v['vin'] for v in vehicles}
        for name in ('detail_confirmed_present', 'detail_confirmed_sold', 'detail_verify_ambiguous', 'detail_verify_skipped'):
            setattr(fetcher, name, set())
        return fetcher

    def test_incomplete_scan_does_not_delist(self):
        v = dict(demo.DEMO_VEHICLES[0])
        database.is_new_or_price_dropped(v)
        with patch.object(monitor, 'send_notification', return_value=True):
            self.assertFalse(monitor.run_scan(self.fetcher([], False)))
        self.assertEqual(len(database.get_currently_active_listings()), 1)

    def test_disabled_model_is_never_delisted(self):
        database.is_new_or_price_dropped(dict(demo.DEMO_VEHICLES[1]))
        with patch.object(monitor, 'TESLA_MODELS', ('my',)), patch.object(monitor, 'send_notification', return_value=True):
            monitor.run_scan(self.fetcher([]))
            monitor.run_scan(self.fetcher([]))
            monitor.run_scan(self.fetcher([]))
        self.assertEqual(len(database.get_currently_active_listings()), 1)

    def test_failed_delivery_survives_then_retries(self):
        v = dict(demo.DEMO_VEHICLES[0])
        with patch.object(monitor, '_should_realtime_push', return_value=True), patch.object(monitor, 'send_notification', return_value=False):
            self.assertTrue(monitor.run_scan(self.fetcher([v])))
        self.assertEqual(len(database.get_pending_alerts()), 1)
        with patch.object(monitor, 'send_notification', return_value=True) as send:
            self.assertEqual(monitor.flush_pending_alerts(), 1)
            send.assert_called_once()
        self.assertEqual(database.get_pending_alerts(), [])

    def test_unknown_fee_not_zero(self):
        v = dict(demo.DEMO_VEHICLES[1])
        self.assertIsNone(monitor._transport_fee_int(v))
        with patch.object(monitor, 'push_tier', return_value='other'), patch.object(monitor, 'PUSH_UNKNOWN_TRANSPORT', False):
            self.assertFalse(monitor._should_realtime_push(v))
        with patch.object(monitor, 'INCLUDE_TRANSPORT_IN_SORT', True):
            self.assertGreater(monitor._price_sort_key(v), monitor._price_sort_key(demo.DEMO_VEHICLES[0]))

    def test_retry_schedule(self):
        now = datetime.datetime(2026, 1, 1, 12, 0)
        with patch.object(monitor, 'TESLA_FAIL_BACKOFF_MINS', 60):
            self.assertFalse(monitor.should_run_tesla_scan(now, now + datetime.timedelta(minutes=59), True))
            self.assertTrue(monitor.should_run_tesla_scan(now, now + datetime.timedelta(minutes=60), True))

    def test_demo_never_sends_with_configured_credentials(self):
        with patch.object(notifier, 'TELEGRAM_TOKEN', 'configured'), patch.object(notifier, 'TELEGRAM_CHAT_ID', 'configured'), patch.object(notifier.requests, 'post', side_effect=AssertionError('network in demo')) as post:
            demo.run_demo()
            post.assert_not_called()
            self.assertEqual(notifier.TELEGRAM_TOKEN, 'configured')
