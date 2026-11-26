from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urlencode
from unittest.mock import Mock, patch

from buyer_search import BuyerSearch, NEW_INVENTORY
import database
from fetchers.new_inventory import matches_inventory_response, parse_snapshot
import monitor
import notifier


class NewInventoryTests(unittest.TestCase):
    def test_browser_response_is_scoped_to_requested_search(self):
        query = {'condition': 'new', 'model': 'm3', 'zip': '92092'}
        url = 'https://www.tesla.com/inventory/api/v4/inventory-results'
        matching = url + '?' + urlencode({'query': json.dumps({'query': query, 'offset': 0})})
        self.assertTrue(matches_inventory_response(matching, 'm3', '92092'))
        self.assertFalse(matches_inventory_response(matching, 'my', '92092'))
        self.assertFalse(matches_inventory_response(matching, 'm3', '90001'))
        self.assertFalse(matches_inventory_response(matching.replace('new', 'used'), 'm3', '92092'))

    def test_empty_snapshot_requires_explicit_zero(self):
        self.assertEqual(parse_snapshot({'results': [], 'total_matches_found': 0}, 'my', '92092'), ([], 0))
        for payload in ({}, {'results': []}, {'results': [], 'total_matches_found': 3}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_snapshot(payload, 'my', '92092')

    def test_preserves_case_sensitive_detail_link_and_unknown_mileage(self):
        url = 'https://www.tesla.com/m3/order/Synthetic_abc?titleStatus=new'
        raw = dict(VIN='Synthetic_abc', URL=url, InventoryPrice=37000, IsDemo=True)
        cars, total = parse_snapshot({'results': [raw], 'total_matches_found': 15}, 'm3', '92092')
        car = cars[0]
        self.assertEqual(total, 15)
        self.assertEqual(car['url'], url)
        self.assertNotEqual(car['vin'], raw['VIN'])
        self.assertEqual(car['inventory_kind'], 'Demo vehicle')
        self.assertIsNone(car['mileage'])
        self.assertTrue(BuyerSearch(inventory=NEW_INVENTORY).match(car))
        self.assertFalse(BuyerSearch().match(car))

    def test_scan_updates_and_reminders_use_isolated_new_inventory_rules(self):
        search = BuyerSearch(inventory=NEW_INVENTORY)
        self.assertNotEqual(search.state_key(), BuyerSearch().state_key())
        raw = dict(VIN='Synthetic_def', InventoryPrice=37000)
        vehicles, _ = parse_snapshot({'results': [raw], 'total_matches_found': 1}, 'my', '92092')
        fetcher = Mock(last_inventory_vins=set(), detail_confirmed_present=set(),
                       detail_confirmed_sold=set(), detail_verify_ambiguous=set(), detail_verify_skipped=set())
        with TemporaryDirectory(dir=Path(__file__).parent) as folder, \
                patch.object(database, 'DB_PATH', str(Path(folder) / 'new.db')), \
                patch.object(monitor, 'BUYER_SEARCH', search), \
                patch.object(monitor, 'send_notification', return_value=True) as send, \
                redirect_stdout(StringIO()):
            database.init_db()
            fetcher.fetch.return_value = (vehicles, True)
            self.assertTrue(monitor.run_scan(fetcher))
            first = send.call_args.args
            self.assertIn('Listed cash price', notifier.build_notification_text(*first))
            self.assertNotIn('VIN:', notifier.build_notification_text(*first))
            fetcher.fetch.return_value = ([dict(vehicles[0], price=36500)], True)
            self.assertTrue(monitor.run_scan(fetcher))
            self.assertTrue(monitor.run_scan(fetcher))
            self.assertIn('Cash price drop', notifier.build_notification_text(*send.call_args.args))
            self.assertEqual([call.args[1] for call in send.call_args_list], ['new', 'price_drop'])
            fetcher.fetch.return_value = ([], True)
            self.assertTrue(monitor.run_scan(fetcher))
            self.assertTrue(monitor.run_scan(fetcher))
            self.assertEqual(len(database.get_currently_active_listings()), 1)
