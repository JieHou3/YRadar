"""YRadar configuration. Secrets come from environment / .env — never commit tokens."""
import os

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(CURRENT_DIR, '.env'))
except ImportError:
    pass


def _env(name, default=''):
    val = os.environ.get(name)
    return default if val is None or val == '' else val


def _env_int(name, default):
    try:
        return int(_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name, default):
    try:
        return float(_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name, default=False):
    raw = _env(name, '1' if default else '0').strip().lower()
    return raw in ('1', 'true', 'yes', 'on')


# --- Search center (example: La Jolla, CA 92092) ---
def _parse_models(raw):
    aliases = {'my': 'my', 'y': 'my', 'modely': 'my',
               'm3': 'm3', '3': 'm3', 'model3': 'm3'}
    models = []
    for item in str(raw).split(','):
        key = item.strip().lower().replace(' ', '').replace('-', '')
        model = aliases.get(key)
        if model and model not in models:
            models.append(model)
    return tuple(models or ('my', 'm3'))


TESLA_MODELS = _parse_models(_env('TESLA_MODELS', 'my,m3'))
TESLA_ZIP = _env('TESLA_ZIP', '92092')
TARGET_ZIP = TESLA_ZIP
_DEFAULT_COORDS = (32.8458529, -117.2575702) if TESLA_ZIP == '92092' else (None, None)
_target_lat = _env_float('TARGET_LAT', _DEFAULT_COORDS[0])
_target_lng = _env_float('TARGET_LNG', _DEFAULT_COORDS[1])
TARGET_COORDS = ((_target_lat, _target_lng)
                 if _target_lat is not None and _target_lng is not None else None)
NEARBY_RADIUS = _env_int('NEARBY_RADIUS_MILES', 50)

# --- Inventory filters (defaults match a practical used Model Y hunt) ---
TESLA_MIN_YEAR = _env_int('TESLA_MIN_YEAR', 2023)  # HW4 is rare before mid-2023
MAX_MODEL_YEAR = None
MAX_MILEAGE = _env_int('MAX_MILEAGE', 75000)

# Watch list = White Interior / Long Range AWD / Clean history
WATCH_MAX_PRICE = _env_int('WATCH_MAX_PRICE', 38000)
FOCUS_MAX_PRICE = _env_int('FOCUS_MAX_PRICE', 33000)  # Long Range + HW4
HW4_MAX_PRICE = _env_int('HW4_MAX_PRICE', 32000)
BASE_MAX_PRICE = _env_int('BASE_MAX_PRICE', 32000)
TESLA_FETCH_MAX_PRICE = max(WATCH_MAX_PRICE, FOCUS_MAX_PRICE, HW4_MAX_PRICE, BASE_MAX_PRICE)

# Backward-compatible names used by the fetcher
TESLA_FAVORITE_MAX_PRICE = WATCH_MAX_PRICE
TESLA_FOCUS_MAX_PRICE = FOCUS_MAX_PRICE
TESLA_MAX_PRICE = HW4_MAX_PRICE

# Transport fee: Tesla inventory exposes mileage bands, not a filled TransportationFee
NEAR_MAX_FEE = _env_int('NEAR_MAX_FEE', 500)
TESLA_NEAR_MAX_FEE = NEAR_MAX_FEE
# 'net_plus_fee' -> $35,000 (+500 transport)
# 'total'        -> $35,500 (includes $500 transport)
PRICE_DISPLAY_MODE = _env('PRICE_DISPLAY_MODE', 'net_plus_fee')
INCLUDE_TRANSPORT_IN_SORT = _env_bool('INCLUDE_TRANSPORT_IN_SORT', False)
PUSH_UNKNOWN_TRANSPORT = _env_bool('PUSH_UNKNOWN_TRANSPORT', True)

# --- Tesla crawl ---
TESLA_SEARCH_RANGE = _env_int('TESLA_SEARCH_RANGE', 0)
TESLA_REGION = _env('TESLA_REGION', 'CA').strip().upper()
TESLA_ALL_DELIVERABLE = _env_bool('TESLA_ALL_DELIVERABLE', True)
TESLA_SKIP_LOCAL_POOL = _env_bool('TESLA_SKIP_LOCAL_POOL', True)
TESLA_ZERO_NEW_STOP_PAGES = max(1, _env_int('TESLA_ZERO_NEW_STOP_PAGES', 2))
TESLA_DETAIL_VERIFY_PAUSE_SECS = max(0, _env_int('TESLA_DETAIL_VERIFY_PAUSE_SECS', 30))
TESLA_DETAIL_VERIFY_DELAY_SECS = max(0, _env_int('TESLA_DETAIL_VERIFY_DELAY_SECS', 10))
CDP_VISIBLE = _env_bool('CDP_VISIBLE', False)

_known_alert_tiers = ('favorite', 'focus', 'hw4', 'other')
ALERT_TIERS = tuple(
    tier for tier in (
        item.strip().lower() for item in _env(
            'ALERT_TIERS', 'favorite,focus,hw4,other').split(',')
    ) if tier in _known_alert_tiers
) or _known_alert_tiers

# --- Notifications (leave empty to print to console only) ---
TELEGRAM_TOKEN = _env('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = _env('TELEGRAM_CHAT_ID', '')

# --- Schedule ---
TESLA_INTERVAL_MINS = max(1, _env_int('TESLA_INTERVAL_MINS', 15))
TESLA_FAIL_BACKOFF_MINS = max(1, _env_int('TESLA_FAIL_BACKOFF_MINS', 60))
LOOP_SLEEP_CHUNK_SECS = max(1, min(300, _env_int('LOOP_SLEEP_CHUNK_SECS', 60)))
REVIEW_SLOTS = (
    (11, 30, 'morning'),
    (23, 0, 'evening'),
)

TESLA_SOLD_MISS_COUNT = max(2, _env_int('TESLA_SOLD_MISS_COUNT', 2))
TESLA_SOLD_MISS_MIN_HOURS = 0
RELIST_NOTIFY_MIN_HOURS = 0

DB_PATH = os.path.join(CURRENT_DIR, 'vehicles.db')
POLL_INTERVAL = TESLA_INTERVAL_MINS * 60


def get_tesla_interval_mins(now=None):
    return TESLA_INTERVAL_MINS


def next_tesla_scan_at(now=None, last_scan_at=None):
    """Return the next regular scan time."""
    import datetime
    now = now or datetime.datetime.now()
    if last_scan_at is None:
        return now
    scheduled = last_scan_at + datetime.timedelta(minutes=TESLA_INTERVAL_MINS)
    return max(now, scheduled)
