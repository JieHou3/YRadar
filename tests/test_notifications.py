import contextlib
import io
import os
import sys
import unittest
from unittest import mock

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

import notifier


def model3():
    return {
        'vin': '5YJ3E1EA1SF123456', 'year': 2025, 'model': 'Model 3',
        'trim': 'Long Range AWD <demo>', 'price': 32900, 'mileage': 74000,
        'url': 'https://www.tesla.com/m3/order/x?a=1&b=2',
        'source': 'Tesla Official', 'dealer_address': 'San Diego <CA>',
        'color_ext': 'Blue & Silver', 'color_int': 'Black',
        'transport_fee': None, 'transport_fee_status': 'unknown',
        'hw4_status': 'confirmed', 'hw4_source': 'tesla_listing',
        'hw4_reason': 'Synthetic explicit hardware listing', 'matrix_status': 'confirmed',
        'matrix_source': 'tesla_listing', 'matrix_reason': 'Synthetic explicit lighting listing',
        'wheel_inches': 18, 'seat_count': 5,
    }


class NotificationTests(unittest.TestCase):
    def test_renderer_is_english_model_aware_and_html_escaped(self):
        text = notifier.build_notification_text(model3())
        self.assertIn('2025 Model 3', text)
        self.assertIn('transport unknown', text)
        self.assertIn('HW4: <b>confirmed</b>', text)
        self.assertIn('Matrix headlights: <b>confirmed</b>', text)
        self.assertIn('18-inch', text)
        self.assertIn('5-seat', text)
        self.assertIn('&lt;demo&gt;', text)
        self.assertIn('a=1&amp;b=2', text)

    def test_html_chunks_do_not_cut_formatting_tags(self):
        text = '\n'.join(f'<b>row {i}</b>' for i in range(20))
        chunks = notifier._split_telegram_html(text, limit=80)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertEqual(chunk.count('<b>'), chunk.count('</b>'))

    def test_model3_fallback_does_not_apply_model_y_serial_rules(self):
        item = model3()
        for key in ('hw4_status', 'hw4_source', 'hw4_reason',
                    'matrix_status', 'matrix_source', 'matrix_reason'):
            item.pop(key)
        item['year'] = 2023
        item['vin'] = '5YJ3E1EA1PF800001'
        text = notifier.build_notification_text(item)
        self.assertIn('HW4: <b>unknown</b>', text)
        self.assertIn('Matrix headlights: <b>unknown</b>', text)
        self.assertNotIn('matrix headlights ❌', text)

    def test_review_escapes_fields_and_unknown_transport_sorts_last(self):
        known = model3()
        known.update({'vin': '5YJ3E1EA1SF123457', 'price': 40000,
                      'transport_fee': 1000, 'transport_fee_status': 'known',
                      'first_seen': '2026-01-01 00:00:00'})
        unknown = model3()
        unknown.update({'price': 30000, 'trim': '<unsafe>',
                        'first_seen': '2026-01-01 00:00:00'})
        with mock.patch.object(notifier, 'INCLUDE_TRANSPORT_IN_SORT', True):
            self.assertEqual(notifier._review_sort_key(known)[0], 0)
            self.assertEqual(notifier._review_sort_key(unknown)[0], 1)
        line = notifier._review_line(unknown, '2026-01-02 00:00:00')
        self.assertIn('Model 3', line)
        self.assertIn('&lt;unsafe&gt;', line)
        self.assertNotIn('<unsafe>', line)

    def test_request_error_does_not_print_secret_url(self):
        secret = 'SECRET_TOKEN'
        with mock.patch.object(notifier, 'TELEGRAM_TOKEN', secret), \
             mock.patch.object(notifier, 'TELEGRAM_CHAT_ID', '123'), \
             mock.patch.object(notifier.requests, 'post', side_effect=notifier.requests.RequestException('https://api.telegram.org/bot' + secret)):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                ok = notifier._send_telegram_html('<b>safe</b>')
        self.assertFalse(ok)
        self.assertNotIn(secret, output.getvalue())

    def test_console_demo_never_calls_network(self):
        with mock.patch.object(notifier, 'TELEGRAM_TOKEN', ''), \
             mock.patch.object(notifier, 'TELEGRAM_CHAT_ID', ''), \
             mock.patch.object(notifier.requests, 'post') as post:
            self.assertTrue(notifier.send_notification(model3()))
            post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
