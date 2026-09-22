import unittest

from vehicle_tiers import classify_tesla_tier, push_tier, total_price


class TierTests(unittest.TestCase):
    def test_favorite_requires_awd_and_confirmed_clean(self):
        base = {
            'vin': '7SAYGDEE9SF123456', 'model_code': 'my',
            'trim': 'Long Range AWD', 'color_int': 'White',
            'price': 35000,
        }
        self.assertNotEqual(classify_tesla_tier(base), 'favorite')
        base['clean_status'] = 'clean'
        self.assertEqual(classify_tesla_tier(base), 'favorite')
        base['trim'] = 'Long Range RWD'
        self.assertNotEqual(classify_tesla_tier(base), 'favorite')

    def test_model3_unknown_hardware_is_still_alert_eligible(self):
        car = {
            'vin': '5YJ3E1EA1PF100001', 'model_code': 'm3',
            'trim': 'Rear-Wheel Drive', 'color_int': 'Black',
            'clean_status': 'unknown', 'price': 30000,
        }
        self.assertEqual(classify_tesla_tier(car), 'other')
        self.assertEqual(push_tier(car), 'other')

    def test_unknown_transport_is_not_zero(self):
        self.assertIsNone(total_price({'price': 30000, 'transport_fee': None}))
        self.assertEqual(total_price({'price': 30000, 'transport_fee': 0}), 30000)


if __name__ == '__main__':
    unittest.main()
