"""Synthetic, offline examples. No request, browser session, or database access."""
import copy
import notifier


DEMO_VEHICLES = [
    {
        'vin': 'DEMO-MODEL-Y-001', 'year': 2025, 'model': 'Model Y', 'model_code': 'my',
        'trim': 'Long Range All-Wheel Drive', 'price': 32900, 'mileage': 42000,
        'color_int': 'White', 'color_ext': 'Blue', 'clean_status': 'clean',
        'accident_history': 'No reported accidents or damage (synthetic example)',
        'clean_title': 'Unknown; verify history report', 'is_repaired': False,
        'transport_fee': 500, 'transport_fee_status': 'estimated',
        'transport_fee_source': 'distance_band',
        'wheel_inches': 20, 'seat_count': 5, 'est_range': 300,
        'hw4_status': 'likely', 'hw4_source': 'vin_heuristic',
        'hw4_reason': 'Synthetic example: model-year expectation; verify on the vehicle',
        'matrix_status': 'likely', 'matrix_source': 'vin_heuristic',
        'matrix_reason': 'Synthetic example: verify photos and actual headlights',
        'dealer_name': 'Tesla', 'dealer_address': 'Irvine, CA',
        'distance': 65.0, 'is_nearby': False, 'source': 'Tesla Official',
        'url': 'https://www.tesla.com/inventory/used/my',
    },
    {
        'vin': 'DEMO-MODEL-3-002', 'year': 2023, 'model': 'Model 3', 'model_code': 'm3',
        'trim': 'Long Range All-Wheel Drive', 'price': 27900, 'mileage': 35000,
        'color_int': 'Black', 'color_ext': 'White', 'clean_status': 'unknown',
        'accident_history': 'Unknown', 'clean_title': 'Unknown', 'is_repaired': None,
        'transport_fee': None, 'transport_fee_status': 'unknown',
        'transport_fee_source': 'Not supplied in this synthetic example',
        'wheel_inches': 18, 'seat_count': 5, 'est_range': 310,
        'hw4_status': 'unknown', 'hw4_source': 'unknown',
        'hw4_reason': 'Model Y VIN thresholds do not apply to Model 3',
        'matrix_status': 'unknown', 'matrix_source': 'unknown',
        'matrix_reason': 'No direct hardware evidence',
        'dealer_name': 'Tesla', 'dealer_address': 'San Diego, CA',
        'distance': 12.0, 'is_nearby': True, 'source': 'Tesla Official',
        'url': 'https://www.tesla.com/inventory/used/m3',
    },
]


DEMO_NEW_VEHICLES = [
    dict(DEMO_VEHICLES[0], vin='DEMO-NEW-Y', year=2026, price=37990, mileage=1200,
         transport_fee=None, inventory_kind='Demo vehicle', hw4_status='unknown'),
    dict(DEMO_VEHICLES[1], vin='DEMO-NEW-3', year=2026, price=36990, mileage=None,
         transport_fee=None, inventory_kind='New vehicle', hw4_status='unknown'),
]


def run_demo():
    print('YRadar | OFFLINE DEMO | All vehicles, prices, and configurations are fictional.\n')
    for vehicle in copy.deepcopy(DEMO_VEHICLES):
        message = notifier.build_notification_text(vehicle, 'new')
        print(notifier.html.unescape(notifier.re.sub(r'<[^>]+>', '', message)))
    print('\nNo website requests, messages, or database writes were made.')
