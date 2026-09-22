import unittest
from unittest.mock import Mock, patch
import geo
from fetchers import cdp_chrome


class GeoTests(unittest.TestCase):
    def test_same_point_and_antipode(self):
        self.assertEqual(geo.haversine_miles(0, 0, 0, 0), 0)
        self.assertAlmostEqual(geo.haversine_miles(0, 0, 0, 180), 12436.94, places=1)

    def test_unknown_is_not_nearby(self):
        self.assertEqual(geo.calculate_distance('Not a real city, CA'), (None, False))
        self.assertEqual(geo.calculate_distance('Ontario, OR'), (None, False))
        with patch.object(geo, 'TARGET_COORDS', None):
            self.assertEqual(geo.calculate_distance('Irvine, CA'), (None, False))

    def test_city_address_and_exact_matching(self):
        self.assertEqual(geo.extract_city_from_address('1 Example Road, Irvine, CA 92618'), 'Irvine')
        self.assertIsNone(geo.city_coords('north irvine elsewhere'))
        self.assertIsNotNone(geo.calculate_distance('Irvine, CA')[0])


class BrowserOwnershipTests(unittest.TestCase):
    def test_unknown_listener_is_never_killed(self):
        with patch.object(cdp_chrome, '_OWNED_PROCESSES', {}), patch.object(cdp_chrome, 'terminate_chrome_proc') as terminate:
            cdp_chrome.force_kill_cdp_port(9225)
            terminate.assert_not_called()

    def test_only_owned_process_stopped(self):
        proc = Mock()
        proc.poll.return_value = None
        with patch.object(cdp_chrome, '_OWNED_PROCESSES', {9225: proc}), patch.object(cdp_chrome, 'terminate_chrome_proc') as terminate:
            cdp_chrome.force_kill_cdp_port(9225)
            terminate.assert_called_once_with(proc)

    def test_occupied_port_refuses_launch(self):
        with patch.object(cdp_chrome, 'is_cdp_ready', return_value=True), patch.object(cdp_chrome, 'clear_chrome_window_placement'), patch.object(cdp_chrome.subprocess, 'Popen') as launch:
            with self.assertRaises(RuntimeError):
                cdp_chrome.launch_chrome_debug('chrome', 9225, 'test-profile')
            launch.assert_not_called()
