import os
import sys
import tempfile
import unittest

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

import database


def vehicle(price=35000):
    return {
        'vin': '7SAYGDEE1SA123456', 'year': 2025, 'model': 'Model Y',
        'model_code': 'my', 'trim': 'Long Range AWD', 'price': price,
        'mileage': 12000, 'url': 'https://www.tesla.com/my/order/test',
        'dealer_name': 'Tesla', 'dealer_address': 'Irvine, CA',
        'source': 'Tesla Official', 'transport_fee': None,
        'transport_fee_status': 'unknown', 'transport_fee_source': 'unknown',
        'distance_miles': 48.2, 'distance_source': 'inventory_coordinates',
        'clean_status': 'clean', 'hw4_status': 'confirmed',
        'hw4_source': 'model year', 'hw4_reason': '2025 Model Y',
        'matrix_status': 'confirmed', 'matrix_source': 'model year',
        'matrix_reason': '2025 Model Y', 'wheel_inches': 19, 'seat_count': 5,
    }


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.path.dirname(__file__))
        self.old_path = database.DB_PATH
        database.DB_PATH = os.path.join(self.temp.name, 'test.db')
        database.init_db()

    def tearDown(self):
        database.DB_PATH = self.old_path
        self.temp.cleanup()

    def test_unknown_fee_and_analysis_sources_are_persisted(self):
        database.is_new_or_price_dropped(vehicle(), queue_alert=True)
        row = database.get_currently_active_listings()[0]
        self.assertIsNone(row['transport_fee'])
        self.assertEqual(row['transport_fee_status'], 'unknown')
        self.assertEqual(row['hw4_source'], 'model year')
        self.assertEqual(row['matrix_status'], 'confirmed')

    def test_failed_alert_retries_and_delivered_alert_stops(self):
        database.is_new_or_price_dropped(vehicle(), queue_alert=True)
        pending = database.get_pending_alerts()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['vehicle']['model'], 'Model Y')
        database.mark_alert_failed(pending[0]['id'], 'temporary')
        self.assertEqual(database.get_pending_alerts()[0]['attempt_count'], 1)
        database.mark_alert_delivered(pending[0]['id'])
        self.assertEqual(database.get_pending_alerts(), [])

    def test_same_drop_can_recur_after_price_rises(self):
        database.is_new_or_price_dropped(vehicle(35000), queue_alert=False)
        database.is_new_or_price_dropped(vehicle(34000), queue_alert=True)
        database.is_new_or_price_dropped(vehicle(35000), queue_alert=False)
        database.is_new_or_price_dropped(vehicle(34000), queue_alert=True)
        alerts = database.get_pending_alerts()
        self.assertEqual([a['status_type'] for a in alerts], ['price_drop', 'price_drop'])

    def test_existing_vehicle_refreshes_mutable_core_fields(self):
        original = vehicle(35000)
        database.is_new_or_price_dropped(original)
        refreshed = vehicle(36000)
        refreshed.update({
            'year': 2026, 'model': 'Model 3', 'model_code': 'm3',
            'trim': 'Performance', 'mileage': 13000,
            'url': 'https://www.tesla.com/m3/order/test',
            'dealer_name': 'Tesla Updated', 'dealer_address': 'Carlsbad, CA',
            'source': 'Tesla Official',
        })
        database.is_new_or_price_dropped(refreshed)
        row = database.get_currently_active_listings()[0]
        for key in ('year', 'model', 'model_code', 'trim', 'mileage', 'url',
                    'dealer_name', 'dealer_address', 'source'):
            self.assertEqual(row[key], refreshed[key])

    def test_outbox_omits_raw_provider_record(self):
        item = vehicle()
        item['raw_record'] = {'private_provider_field': 'must not persist'}
        database.is_new_or_price_dropped(item, queue_alert=True)
        pending = database.get_pending_alerts()
        self.assertEqual(len(pending), 1)
        self.assertNotIn('raw_record', pending[0]['vehicle'])
        self.assertEqual(pending[0]['vehicle']['hw4_status'], 'confirmed')


if __name__ == '__main__':
    unittest.main()
