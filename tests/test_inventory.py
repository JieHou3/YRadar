import unittest

from fetchers.tesla_fetcher import TeslaFetcher, TeslaFetchBlocked


class FakePage:
    def __init__(self, responses):
        self.responses = list(responses)

    async def evaluate(self, _javascript):
        return self.responses.pop(0)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.fetcher = TeslaFetcher()

    def test_transport_unknown_is_none(self):
        result = self.fetcher.transport_assessment({})
        self.assertIsNone(result['fee'])
        self.assertEqual(result['status'], 'unknown')

    def test_bare_zero_placeholder_is_unknown(self):
        result = self.fetcher.transport_assessment({'TransportationFee': 0})
        self.assertIsNone(result['fee'])
        self.assertEqual(result['status'], 'unknown')

    def test_zero_placeholder_uses_distance_band(self):
        result = self.fetcher.transport_assessment({
            'TransportationFee': 0,
            'vrlList': [{'lat': 32.0, 'lon': -117.0}],
            'TransportFees': {'fees': [{
                'type': 'transportation', 'units': 'miles',
                'query': {'min': 0, 'max': 1000}, 'amount': 500,
            }]},
        }, target_coords=(32.8, -117.2))
        self.assertEqual(result['fee'], 500)
        self.assertEqual(result['status'], 'estimated')
        self.assertEqual(result['source'], 'distance_band')

    def test_exempt_vrl_confirms_zero_fee(self):
        result = self.fetcher.transport_assessment({
            'TransportationFee': 0,
            'Vrl': 'ABC',
            'TransportFees': {'exemptVRL': ['ABC']},
        })
        self.assertEqual(result['fee'], 0)
        self.assertEqual(result['source'], 'exempt')

    def test_envelope_keeps_requested_model(self):
        envelope = self.fetcher._prepare_envelope(
            self.fetcher._fallback_query_obj('m3'), 0, 24, True, 0, 'm3')
        self.assertEqual(envelope['query']['model'], 'm3')

    def test_model3_is_normalized_and_unknown_history_stays_unknown(self):
        raw = {
            'VIN': '5YJ3E1EA1SF123456',
            'InventoryPrice': 30000,
            'Odometer': 12000,
            'TrimName': 'Model 3 Long Range AWD',
            '_yradar_model_code': 'm3',
            'INTERIOR': ['PREMIUM_WHITE'],
        }
        cars = self.fetcher.parse_results([raw])
        self.assertEqual(len(cars), 1)
        self.assertEqual(cars[0]['model'], 'Model 3')
        self.assertIn('/m3/order/', cars[0]['url'])
        self.assertIn('titleStatus=used', cars[0]['url'])
        self.assertEqual(cars[0]['clean_status'], 'unknown')
        self.assertIsNone(cars[0]['transport_fee'])
        self.assertEqual(cars[0]['hw4_status'], 'likely')

    def test_clean_history_is_explicit(self):
        raw = {
            'VIN': '7SAYGDEE9SF123456',
            'InventoryPrice': 35000,
            'Odometer': 1000,
            'TrimName': 'Model Y Long Range AWD',
            '_yradar_model_code': 'my',
            'INTERIOR': ['PREMIUM_WHITE'],
            'VehicleHistory': 'CLEAN',
        }
        car = self.fetcher.parse_results([raw])[0]
        self.assertEqual(car['clean_status'], 'clean')

    def test_clean_history_list_is_normalized(self):
        raw = {
            'VIN': '7SAYGDEE9SF123456',
            'InventoryPrice': 35000,
            'Odometer': 1000,
            'TrimName': 'Model Y Long Range AWD',
            '_yradar_model_code': 'my',
            'INTERIOR': ['PREMIUM_WHITE'],
            'VehicleHistory': ['CLEAN'],
            'OptionCodeData': ['unexpected scalar'],
        }
        self.assertEqual(self.fetcher.parse_results([raw])[0]['clean_status'], 'clean')

    def test_model_specific_order_routes(self):
        vin = '5YJ3E1EA1SF123456'
        self.assertIn('/m3/order/', self.fetcher._order_url(vin, 'Model 3'))
        self.assertIn('/my/order/', self.fetcher._order_url(vin, 'Model Y'))

    def test_model3_order_page_is_present(self):
        vin = '5YJ3E1EA1SF123456'
        verdict = self.fetcher._classify_order_page(
            f'https://www.tesla.com/m3/order/{vin}?titleStatus=used',
            f'Place Order {vin}', vin)
        self.assertEqual(verdict, 'present')

    def test_blocked_inventory_redirect_is_ambiguous(self):
        verdict = self.fetcher._classify_order_page(
            'https://www.tesla.com/inventory/used/m3',
            '<html>Access Denied - CAPTCHA</html>',
            '5YJ3E1EA1SF123456')
        self.assertEqual(verdict, 'ambiguous')


class PaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_pages_do_not_hide_later_new_vin(self):
        fetcher = TeslaFetcher()
        fetcher.MAX_PAGES = 4
        old = {'VIN': '5YJ3E1EA1PF100001', 'InventoryPrice': 100}
        new = {'VIN': '5YJ3E1EA1PF100002', 'InventoryPrice': 100}
        page = FakePage([
            {'status': 200, 'results': [old], 'total': 99},
            {'status': 200, 'results': [old], 'total': 99},
            {'status': 200, 'results': [new], 'total': 99},
            {'status': 200, 'results': [], 'total': 99},
        ])
        merged = []
        seen = {old['VIN']}
        added, complete = await fetcher._paginate_inventory(
            page, fetcher._fallback_query_obj('m3'), 1000, 'test',
            merged, seen, 'm3')
        self.assertTrue(complete)
        self.assertEqual(added, 1)
        self.assertEqual(merged[0]['VIN'], new['VIN'])

    async def test_max_page_truncation_is_incomplete(self):
        fetcher = TeslaFetcher()
        fetcher.MAX_PAGES = 1
        page = FakePage([
            {'status': 200, 'results': [
                {'VIN': '5YJ3E1EA1PF100001', 'InventoryPrice': 100}
            ], 'total': 99},
        ])
        _, complete = await fetcher._paginate_inventory(
            page, fetcher._fallback_query_obj('m3'), 1000, 'test',
            [], set(), 'm3')
        self.assertFalse(complete)

    async def test_403_raises_without_discarding_already_merged_rows(self):
        fetcher = TeslaFetcher()
        fetcher.MAX_PAGES = 2
        row = {'VIN': '5YJ3E1EA1PF100001', 'InventoryPrice': 100}
        page = FakePage([
            {'status': 200, 'results': [row], 'total': 99},
            {'status': 403, 'results': [], 'total': 0},
        ])
        merged = []
        with self.assertRaises(TeslaFetchBlocked):
            await fetcher._paginate_inventory(
                page, fetcher._fallback_query_obj('m3'), 1000, 'test',
                merged, set(), 'm3')
        self.assertEqual([item['VIN'] for item in merged], [row['VIN']])

    async def test_malformed_200_response_is_incomplete(self):
        fetcher = TeslaFetcher()
        page = FakePage([
            {'status': 200, 'schema_valid': False, 'results': [], 'total': 0},
        ])
        _, complete = await fetcher._paginate_inventory(
            page, fetcher._fallback_query_obj('m3'), 1000, 'test',
            [], set(), 'm3')
        self.assertFalse(complete)

    async def test_rate_limit_is_incomplete(self):
        fetcher = TeslaFetcher()
        page = FakePage([
            {'status': 429, 'schema_valid': False, 'results': [], 'total': 0},
        ])
        _, complete = await fetcher._paginate_inventory(
            page, fetcher._fallback_query_obj('m3'), 1000, 'test',
            [], set(), 'm3')
        self.assertFalse(complete)


if __name__ == '__main__':
    unittest.main()
