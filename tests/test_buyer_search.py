"""Exercise the desktop criteria through the real scan and notification path."""
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from buyer_search import BuyerSearch
from demo import DEMO_VEHICLES
import database
import monitor
import vehicle_tiers


class BuyerSearchTests(unittest.TestCase):
    def test_personal_search_and_uncertain_fee(self):
        y, three = DEMO_VEHICLES
        search = BuyerSearch(model='Model 3', budget=30000, interior='Black', awd=True)
        self.assertTrue(search.match(three))
        self.assertFalse(search.match(y))
        self.assertFalse(replace(search, clean=True).match(three))
        self.assertTrue(replace(search, include_fee=True).match(three))
        self.assertFalse(replace(search, include_fee=True, allow_unknown=False).match(three))
        self.assertFalse(search.match(dict(three, mileage=85000)))
        self.assertFalse(search.match(dict(three, trim='Rear-Wheel Drive')))

    def test_state_is_scoped_to_criteria(self):
        search = BuyerSearch()
        self.assertEqual(search.state_key(), replace(search, interval=30).state_key())
        self.assertNotEqual(search.state_key(), replace(search, zip_code='98101', region='WA').state_key())
        with self.assertRaises(ValueError):
            replace(search, lat='32.1')

    def test_fee_change_triggers_once_even_with_same_sticker(self):
        search = BuyerSearch(budget=33000, include_fee=True)
        car = dict(DEMO_VEHICLES[0])
        fetcher = Mock(last_inventory_vins={car['vin']}, detail_confirmed_present=set(),
                       detail_confirmed_sold=set(), detail_verify_ambiguous=set(), detail_verify_skipped=set())
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp, \
                patch.object(database, 'DB_PATH', str(Path(temp)/'demo.db')), \
                patch.object(monitor, 'BUYER_SEARCH', search), \
                patch.object(vehicle_tiers, 'BUYER_SEARCH', search), \
                patch.object(monitor, 'send_notification', return_value=True) as send, \
                redirect_stdout(StringIO()):
            database.init_db()
            fetcher.fetch.return_value = ([car], True)
            self.assertTrue(monitor.run_scan(fetcher))
            send.assert_not_called()
            car['transport_fee'] = 50
            self.assertTrue(monitor.run_scan(fetcher))
            self.assertTrue(monitor.run_scan(fetcher))
            send.assert_called_once()
            self.assertEqual(send.call_args.args[1], 'match')
