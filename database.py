import sqlite3
import datetime
import json
from config import DB_PATH, RELIST_NOTIFY_MIN_HOURS
from config import TESLA_SOLD_MISS_COUNT, TESLA_SOLD_MISS_MIN_HOURS

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vehicles (
            vin TEXT PRIMARY KEY,
            year INTEGER,
            model TEXT,
            trim TEXT,
            price REAL,
            mileage INTEGER,
            url TEXT,
            dealer_name TEXT,
            dealer_address TEXT,
            source TEXT,
            first_seen TIMESTAMP,
            last_seen TIMESTAMP,
            is_active INTEGER DEFAULT 1,
            sold_at TIMESTAMP
        )
    ''')

    cursor.execute("PRAGMA table_info(vehicles)")
    columns = [col[1] for col in cursor.fetchall()]

    migrations = {
        'is_active': "ALTER TABLE vehicles ADD COLUMN is_active INTEGER DEFAULT 1",
        'sold_at': "ALTER TABLE vehicles ADD COLUMN sold_at TIMESTAMP",
        'color_ext': "ALTER TABLE vehicles ADD COLUMN color_ext TEXT",
        'color_int': "ALTER TABLE vehicles ADD COLUMN color_int TEXT",
        'accident_history': "ALTER TABLE vehicles ADD COLUMN accident_history TEXT",
        'clean_title': "ALTER TABLE vehicles ADD COLUMN clean_title TEXT",
        'miss_count': "ALTER TABLE vehicles ADD COLUMN miss_count INTEGER DEFAULT 0",
        'first_miss_at': "ALTER TABLE vehicles ADD COLUMN first_miss_at TIMESTAMP",
        'is_repaired': "ALTER TABLE vehicles ADD COLUMN is_repaired INTEGER DEFAULT 0",
        'est_range': "ALTER TABLE vehicles ADD COLUMN est_range INTEGER",
        'transport_fee': "ALTER TABLE vehicles ADD COLUMN transport_fee INTEGER DEFAULT 0",
        'today_drop_amount': "ALTER TABLE vehicles ADD COLUMN today_drop_amount INTEGER DEFAULT 0",
        'today_drop_date': "ALTER TABLE vehicles ADD COLUMN today_drop_date TEXT",
        'last_relist_at': "ALTER TABLE vehicles ADD COLUMN last_relist_at TIMESTAMP",
        'wheel_inches': "ALTER TABLE vehicles ADD COLUMN wheel_inches INTEGER",
        'seat_count': "ALTER TABLE vehicles ADD COLUMN seat_count INTEGER",
        'model_code': "ALTER TABLE vehicles ADD COLUMN model_code TEXT",
        'transport_fee_status': "ALTER TABLE vehicles ADD COLUMN transport_fee_status TEXT",
        'transport_fee_source': "ALTER TABLE vehicles ADD COLUMN transport_fee_source TEXT",
        'distance_miles': "ALTER TABLE vehicles ADD COLUMN distance_miles REAL",
        'distance_source': "ALTER TABLE vehicles ADD COLUMN distance_source TEXT",
        'clean_status': "ALTER TABLE vehicles ADD COLUMN clean_status TEXT",
        'hw4_status': "ALTER TABLE vehicles ADD COLUMN hw4_status TEXT",
        'hw4_source': "ALTER TABLE vehicles ADD COLUMN hw4_source TEXT",
        'hw4_reason': "ALTER TABLE vehicles ADD COLUMN hw4_reason TEXT",
        'matrix_status': "ALTER TABLE vehicles ADD COLUMN matrix_status TEXT",
        'matrix_source': "ALTER TABLE vehicles ADD COLUMN matrix_source TEXT",
        'matrix_reason': "ALTER TABLE vehicles ADD COLUMN matrix_reason TEXT",
        'range_unit': "ALTER TABLE vehicles ADD COLUMN range_unit TEXT",
        'tire_size': "ALTER TABLE vehicles ADD COLUMN tire_size TEXT",
    }
    for col, sql in migrations.items():
        if col not in columns:
            cursor.execute(sql)

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notification_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT NOT NULL UNIQUE,
            vin TEXT NOT NULL,
            status_type TEXT NOT NULL,
            old_price REAL,
            payload_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            delivered_at TIMESTAMP,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TIMESTAMP,
            last_error TEXT
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_notification_outbox_pending
        ON notification_outbox(delivered_at, created_at)
    ''')

    conn.commit()
    conn.close()


def _vehicle_extra_fields(vehicle):
    """Optional extra columns for INSERT/UPDATE."""
    is_repaired = 1 if vehicle.get('is_repaired') else 0
    est_range = vehicle.get('est_range')
    transport_fee = vehicle.get('transport_fee')
    if transport_fee is not None:
        transport_fee = int(transport_fee)
    wheel_inches = vehicle.get('wheel_inches')
    if wheel_inches is not None:
        try:
            wheel_inches = int(wheel_inches)
        except (TypeError, ValueError):
            wheel_inches = None
    seat_count = vehicle.get('seat_count')
    if seat_count is not None:
        try:
            seat_count = int(seat_count)
        except (TypeError, ValueError):
            seat_count = None
    return is_repaired, est_range, transport_fee, wheel_inches, seat_count


_DETAIL_FIELDS = (
    'year', 'model', 'model_code', 'trim', 'mileage', 'url',
    'dealer_name', 'dealer_address', 'source',
    'transport_fee_status', 'transport_fee_source',
    'distance_miles', 'distance_source', 'clean_status',
    'hw4_status', 'hw4_source', 'hw4_reason',
    'matrix_status', 'matrix_source', 'matrix_reason',
    'range_unit', 'tire_size',
)


def _update_vehicle_details(cursor, vin, vehicle):
    """Persist normalized analysis fields without changing the legacy API."""
    cursor.execute(
        "UPDATE vehicles SET " + ", ".join(f"{name} = ?" for name in _DETAIL_FIELDS) + " WHERE vin = ?",
        [vehicle.get(name) for name in _DETAIL_FIELDS] + [vin],
    )


def _accumulate_today_drop(cursor, vin, old_price, new_price):
    """Accumulate today's sticker-price drop (kept across morning/evening digest until tomorrow)."""
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    drop_add = max(0, float(old_price) - float(new_price))
    if drop_add <= 0:
        return

    cursor.execute(
        "SELECT today_drop_amount, today_drop_date FROM vehicles WHERE vin = ?",
        (vin,),
    )
    row = cursor.fetchone()
    if row and row[1] == today:
        total_drop = int(row[0] or 0) + int(round(drop_add))
    else:
        total_drop = int(round(drop_add))

    cursor.execute(
        "UPDATE vehicles SET today_drop_amount = ?, today_drop_date = ? WHERE vin = ?",
        (total_drop, today, vin),
    )


def is_new_or_price_dropped(vehicle, queue_alert=False):
    """
    Detect a new listing or a sticker-price drop.
    Returns: (should_notify, status_type, old_price)
    """
    vin = vehicle['vin']
    price = vehicle['price']
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    is_repaired, est_range, transport_fee, wheel_inches, seat_count = _vehicle_extra_fields(vehicle)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT price, is_active, sold_at FROM vehicles WHERE vin = ?", (vin,))
    row = cursor.fetchone()

    if row is None:
        cursor.execute('''
            INSERT INTO vehicles (
                vin, year, model, trim, price, mileage, url, dealer_name, dealer_address, source,
                first_seen, last_seen, is_active, sold_at, miss_count, first_miss_at,
                color_ext, color_int, accident_history, clean_title, is_repaired, est_range,
                transport_fee, wheel_inches, seat_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, 0, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            vin, vehicle['year'], vehicle['model'], vehicle['trim'], price, vehicle['mileage'],
            vehicle['url'], vehicle['dealer_name'], vehicle['dealer_address'], vehicle['source'],
            now, now,
            vehicle.get('color_ext'), vehicle.get('color_int'),
            vehicle.get('accident_history'), vehicle.get('clean_title'),
            is_repaired, est_range, transport_fee, wheel_inches, seat_count,
        ))
        _update_vehicle_details(cursor, vin, vehicle)
        if queue_alert:
            _enqueue_alert_cursor(cursor, vehicle, 'new', None, now)
        conn.commit()
        conn.close()
        return True, "new", None

    old_price, was_active, sold_at = row
    if not was_active:
        # Was marked gone, now back: restore in-stock first (keep first_seen)
        cursor.execute("""
            UPDATE vehicles
            SET price = ?, is_active = 1, sold_at = NULL, last_seen = ?,
                miss_count = 0, first_miss_at = NULL,
                color_ext = ?, color_int = ?, accident_history = ?, clean_title = ?,
                is_repaired = ?, est_range = ?, transport_fee = ?, wheel_inches = ?,
                seat_count = ?
            WHERE vin = ?
        """, (
            price, now,
            vehicle.get('color_ext'), vehicle.get('color_int'),
            vehicle.get('accident_history'), vehicle.get('clean_title'),
            is_repaired, est_range, transport_fee, wheel_inches, seat_count, vin,
        ))
        _update_vehicle_details(cursor, vin, vehicle)

        # Offline duration: past the threshold → real relist + notify; else silent restore (anti-jitter)
        if not sold_at:
            conn.commit()
            conn.close()
            # Missing sold_at (legacy row): cannot measure offline time → silent restore
            return False, None, None
        try:
            sold_dt = datetime.datetime.strptime(sold_at, '%Y-%m-%d %H:%M:%S')
            off_hours = (datetime.datetime.now() - sold_dt).total_seconds() / 3600
        except Exception:
            conn.commit()
            conn.close()
            return False, None, None

        if off_hours >= RELIST_NOTIFY_MIN_HOURS:
            cursor.execute(
                "UPDATE vehicles SET last_relist_at = ? WHERE vin = ?",
                (now, vin),
            )
            # For notifier / console: when it left, and the last sticker price
            vehicle['relist_sold_at'] = sold_at
            vehicle['relist_old_price'] = old_price
            if queue_alert:
                _enqueue_alert_cursor(cursor, vehicle, 'relist', old_price, now)
            conn.commit()
            conn.close()
            return True, "relist", old_price

        conn.commit()
        conn.close()
        return False, None, None

    cursor.execute("""
        UPDATE vehicles
        SET last_seen = ?, price = ?, color_ext = ?, color_int = ?,
            accident_history = ?, clean_title = ?, is_repaired = ?, est_range = ?,
            transport_fee = ?, wheel_inches = ?, seat_count = ?
        WHERE vin = ?
    """, (
        now, price,
        vehicle.get('color_ext'), vehicle.get('color_int'),
        vehicle.get('accident_history'), vehicle.get('clean_title'),
        is_repaired, est_range, transport_fee, wheel_inches, seat_count, vin,
    ))
    _update_vehicle_details(cursor, vin, vehicle)

    if price < old_price:
        _accumulate_today_drop(cursor, vin, old_price, price)
        if queue_alert:
            _enqueue_alert_cursor(cursor, vehicle, 'price_drop', old_price, now)
        conn.commit()
        conn.close()
        return True, "price_drop", old_price

    conn.commit()
    conn.close()
    return False, None, None


def get_active_vehicles_by_source(source):
    """Current in-stock rows for one source (full dicts)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM vehicles WHERE is_active = 1 AND source = ?",
        (source,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def update_active_status(source, active_vins, skip_miss_vins=None, force_sold_vins=None,
                         notification_predicate=None):
    """
    After a complete successful scan, update in-stock flags.
    Returns vehicles newly marked gone this round.

    skip_miss_vins: do not increment miss / do not delist (detail verify unclear or skipped).
    force_sold_vins: order page confirmed gone — mark sold immediately (skip miss count).
    """
    miss_need = TESLA_SOLD_MISS_COUNT
    miss_hours_need = TESLA_SOLD_MISS_MIN_HOURS

    now_dt = datetime.datetime.now()
    now = now_dt.strftime('%Y-%m-%d %H:%M:%S')
    active_set = set(active_vins or [])
    skip_set = set(skip_miss_vins or [])
    force_set = {str(v).strip().upper() for v in (force_sold_vins or []) if v}
    newly_sold = []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if active_set:
        placeholders = ",".join("?" for _ in active_set)
        cursor.execute(f"""
            UPDATE vehicles
            SET miss_count = 0, first_miss_at = NULL, last_seen = ?
            WHERE source = ? AND is_active = 1 AND vin IN ({placeholders})
        """, [now, source] + list(active_set))

    cursor.execute("""
        SELECT vin, miss_count, first_miss_at FROM vehicles
        WHERE is_active = 1 AND source = ?
    """, (source,))
    rows = cursor.fetchall()

    def _mark_sold(vin):
        cursor.execute("SELECT * FROM vehicles WHERE vin = ?", (vin,))
        sold_row = cursor.fetchone()
        cursor.execute("""
            UPDATE vehicles
            SET is_active = 0, sold_at = ?, miss_count = 0, first_miss_at = NULL
            WHERE vin = ?
        """, (now, vin))
        if sold_row:
            sold = dict(sold_row)
            sold['is_active'] = 0
            sold['sold_at'] = now
            sold['miss_count'] = 0
            sold['first_miss_at'] = None
            newly_sold.append(sold)
            if notification_predicate and notification_predicate(sold):
                _enqueue_alert_cursor(cursor, sold, 'sold', None, now)

    for row in rows:
        vin = row['vin']
        vin_u = (vin or '').strip().upper()
        if vin_u in force_set:
            _mark_sold(vin)
            continue
        if vin in active_set or vin_u in active_set:
            continue
        if vin in skip_set or vin_u in skip_set:
            continue

        miss_count = row['miss_count']
        first_miss_at = row['first_miss_at']
        miss_count = (miss_count or 0) + 1
        if not first_miss_at:
            first_miss_at = now

        cursor.execute(
            "UPDATE vehicles SET miss_count = ?, first_miss_at = ? WHERE vin = ?",
            (miss_count, first_miss_at, vin),
        )

        if miss_count >= miss_need:
            try:
                first_dt = datetime.datetime.strptime(first_miss_at, '%Y-%m-%d %H:%M:%S')
                hours_elapsed = (now_dt - first_dt).total_seconds() / 3600
            except Exception:
                hours_elapsed = 0

            if hours_elapsed >= miss_hours_need:
                _mark_sold(vin)

    conn.commit()
    conn.close()
    return newly_sold

def get_daily_new_listings(start_of_day):
    """
    Today's "new" column: first seen today, or relisted today.
    Includes cars that arrived (or relisted) today and already left.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM vehicles
        WHERE (
            first_seen >= ?
            OR (last_relist_at IS NOT NULL AND last_relist_at >= ?)
        )
          AND (is_active = 1 OR (sold_at IS NOT NULL AND sold_at >= ?))
    """, (start_of_day, start_of_day, start_of_day))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    for r in rows:
        first_seen = r.get('first_seen') or ''
        last_relist = r.get('last_relist_at') or ''
        # Relisted today: first seen earlier, last_relist_at is today
        r['review_is_relist'] = bool(
            last_relist >= start_of_day and first_seen and first_seen < start_of_day
        )
    return rows


def get_sold_today_listings(start_of_day):
    """
    Cars marked gone today (sold_at >= 00:00).
    Shown on both morning and evening digests; not shown the next day.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM vehicles
        WHERE is_active = 0 AND sold_at >= ?
    """, (start_of_day,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_currently_active_listings():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM vehicles WHERE is_active = 1")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def _json_default(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat(sep=' ')
    return str(value)


def _alert_event_key(vehicle, status_type, old_price, transition_at):
    """Identify one inventory transition while allowing the same price after a later rise."""
    vin = str(vehicle.get('vin') or '').strip().upper()
    new_price = vehicle.get('price')
    marker = (
        vehicle.get('sold_at') or vehicle.get('relist_sold_at')
        or vehicle.get('first_seen') or transition_at
    )
    return f"{vin}|{status_type}|{old_price}|{new_price}|{marker}"


def _enqueue_alert_cursor(cursor, vehicle, status_type, old_price, transition_at=None):
    transition_at = transition_at or datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
    event_key = vehicle.get('_alert_event_key')
    if not event_key:
        cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM notification_outbox")
        event_serial = cursor.fetchone()[0]
        event_key = f"{_alert_event_key(vehicle, status_type, old_price, transition_at)}|{event_serial}"
    payload = {key: value for key, value in vehicle.items() if key != 'raw_record'}
    payload['_alert_event_key'] = event_key
    cursor.execute('''
        INSERT OR IGNORE INTO notification_outbox (
            event_key, vin, status_type, old_price, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        event_key, str(vehicle.get('vin') or '').strip().upper(), status_type,
        old_price, json.dumps(payload, ensure_ascii=False, default=_json_default), transition_at,
    ))
    if cursor.rowcount:
        return cursor.lastrowid
    cursor.execute("SELECT id FROM notification_outbox WHERE event_key = ?", (event_key,))
    row = cursor.fetchone()
    return row[0] if row else None


def enqueue_alert(vehicle, status_type, old_price=None, event_key=None):
    """Queue an alert snapshot. Prefer queue_alert=True on transition detection for atomicity."""
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
    snapshot = {key: value for key, value in vehicle.items() if key != 'raw_record'}
    if event_key:
        snapshot['_alert_event_key'] = event_key
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        if event_key:
            payload = json.dumps(snapshot, ensure_ascii=False, default=_json_default)
            cursor.execute('''
                INSERT OR IGNORE INTO notification_outbox
                (event_key, vin, status_type, old_price, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (event_key, str(vehicle.get('vin') or '').strip().upper(), status_type,
                  old_price, payload, now))
            alert_id = cursor.lastrowid if cursor.rowcount else None
        else:
            alert_id = _enqueue_alert_cursor(cursor, snapshot, status_type, old_price, now)
        conn.commit()
        return alert_id
    finally:
        conn.close()


def get_pending_alerts(limit=100):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute('''
            SELECT * FROM notification_outbox
            WHERE delivered_at IS NULL
            ORDER BY created_at, id LIMIT ?
        ''', (max(1, int(limit)),)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item['vehicle'] = json.loads(item.pop('payload_json'))
            except (TypeError, ValueError):
                item['vehicle'] = {}
            result.append(item)
        return result
    finally:
        conn.close()


def mark_alert_delivered(alert_id):
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute('''
            UPDATE notification_outbox
            SET delivered_at = ?, attempt_count = attempt_count + 1,
                last_attempt_at = ?, last_error = NULL
            WHERE id = ? AND delivered_at IS NULL
        ''', (now, now, alert_id))
        conn.commit()
    finally:
        conn.close()


def mark_alert_failed(alert_id, error=None):
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    safe_error = str(error or 'delivery failed')[:500]
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute('''
            UPDATE notification_outbox
            SET attempt_count = attempt_count + 1, last_attempt_at = ?, last_error = ?
            WHERE id = ? AND delivered_at IS NULL
        ''', (now, safe_error, alert_id))
        conn.commit()
    finally:
        conn.close()
