"""YRadar main loop: Tesla official used inventory only."""
import time
import argparse
import sys
import datetime
import os

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

from config import (
    NEARBY_RADIUS, STATE_DIR, BUYER_SEARCH, REVIEW_SLOTS, LOOP_SLEEP_CHUNK_SECS,
    TESLA_FAIL_BACKOFF_MINS, next_tesla_scan_at,
    TESLA_NEAR_MAX_FEE, TESLA_INTERVAL_MINS, TESLA_MODELS,
    INCLUDE_TRANSPORT_IN_SORT, PUSH_UNKNOWN_TRANSPORT,
)
from database import (
    init_db, is_new_or_price_dropped, update_active_status,
    get_daily_new_listings, get_currently_active_listings, get_sold_today_listings,
    get_pending_alerts, mark_alert_delivered, mark_alert_failed,
)
from geo import calculate_distance
from fetchers.tesla_fetcher import TeslaFetcher
from notifier import (
    send_notification, send_daily_review, send_system_alert,
    format_price_display, format_duration,
)
from vehicle_tiers import push_tier, classify_tesla_tier

REVIEW_STATE_FILE = os.path.join(STATE_DIR, 'last_review_state.json')


def _review_slot_key(hour, minute):
    return f"{hour:02d}:{minute:02d}"


def _load_review_state(now=None):
    import json
    now = now or datetime.datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    state = {'date': today_str, 'sent': []}
    if os.path.exists(REVIEW_STATE_FILE):
        try:
            with open(REVIEW_STATE_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            if loaded.get('date') == today_str:
                state['sent'] = list(loaded.get('sent') or [])
        except Exception:
            pass
    return state


def _save_review_state(state):
    import json
    try:
        with open(REVIEW_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception as e:
        print(f"[error] Could not write review state: {e}")


def _attach_distance(listings):
    for v in listings:
        dist, nearby = calculate_distance(v.get('dealer_address') or '')
        v['distance'] = dist
        v['is_nearby'] = bool(nearby)


def _send_one_review(review_time_label):
    now = datetime.datetime.now()
    print(f"\n[sys] [{now.strftime('%Y-%m-%d %H:%M:%S')}] Building digest ({review_time_label})...")
    start_of_today = now.strftime('%Y-%m-%d 00:00:00')
    new_list = get_daily_new_listings(start_of_today)
    active_list = get_currently_active_listings()
    sold_list = get_sold_today_listings(start_of_today)
    for lst in (new_list, active_list, sold_list):
        _attach_distance(lst)
    return send_daily_review(new_list, active_list, sold_list, review_time_label=review_time_label)


def check_and_trigger_daily_review(force=False):
    now = datetime.datetime.now()
    state = _load_review_state(now)
    if force:
        ok = _send_one_review('manual')
        print("[sys] Manual digest sent.\n" if ok else "[warn] Digest send incomplete.\n")
        return ok
    sent_any = False
    for hour, minute, label in REVIEW_SLOTS:
        slot_key = _review_slot_key(hour, minute)
        if slot_key in state['sent']:
            continue
        slot_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < slot_dt:
            continue
        time_label = f"{hour:02d}:{minute:02d} {label}"
        ok = _send_one_review(time_label)
        if ok:
            state['sent'].append(slot_key)
            _save_review_state(state)
            sent_any = True
            print(f"[sys] Digest ({time_label}) sent.\n")
        else:
            print(f"[warn] Digest ({time_label}) failed; will retry.\n")
    return sent_any


def should_run_tesla_scan(last_scan_at, now=None, in_backoff=False):
    now = now or datetime.datetime.now()
    if in_backoff:
        if last_scan_at is None:
            return True
        return (now - last_scan_at).total_seconds() >= TESLA_FAIL_BACKOFF_MINS * 60
    return now >= next_tesla_scan_at(now, last_scan_at)


def compute_tesla_sleep_seconds(last_scan_at, now=None, in_backoff=False):
    now = now or datetime.datetime.now()
    if in_backoff:
        if last_scan_at is None:
            return 0
        due = last_scan_at + datetime.timedelta(minutes=TESLA_FAIL_BACKOFF_MINS)
        return max(0, (due - now).total_seconds())
    due = next_tesla_scan_at(now, last_scan_at)
    return max(0, (due - now).total_seconds())


def _transport_fee_int(vehicle):
    try:
        value = vehicle.get('transport_fee')
        if value is None:
            return None
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _should_realtime_push(vehicle):
    if BUYER_SEARCH:
        return BUYER_SEARCH.match(vehicle)
    if push_tier(vehicle) is None:
        return False
    fee = _transport_fee_int(vehicle)
    if fee is None:
        return PUSH_UNKNOWN_TRANSPORT
    return fee <= TESLA_NEAR_MAX_FEE


def _price_sort_key(vehicle):
    price = float(vehicle.get('price') or 0)
    if not INCLUDE_TRANSPORT_IN_SORT:
        return (False, price)
    fee = _transport_fee_int(vehicle)
    return (fee is None, price + (fee or 0))


def flush_pending_alerts():
    """Retry durable events. Failed sends remain queued for later loop ticks."""
    pending = get_pending_alerts()
    pending.sort(key=lambda row: _price_sort_key(row['vehicle']))
    sent = 0
    for row in pending:
        try:
            ok = send_notification(row['vehicle'], row['status_type'], row.get('old_price'))
        except Exception as error:
            # Request exceptions can contain credential-bearing URLs. Record only type.
            mark_alert_failed(row['id'], type(error).__name__)
            print(f"[warn] Alert delivery failed ({type(error).__name__}); queued for retry.")
            continue
        if ok:
            mark_alert_delivered(row['id'])
            sent += 1
        else:
            mark_alert_failed(row['id'], 'Delivery unsuccessful')
    return sent


_STATUS = {'new': 'new', 'relist': 'relisted', 'price_drop': 'price drop', 'sold': 'no longer listed'}


def _print_skip_console(prefix, vehicle, status, old_price=None):
    label = _STATUS.get(status, status)
    year = vehicle.get('year')
    trim = (vehicle.get('trim') or '')[:24]
    vin = vehicle.get('vin') or ''
    vin_tail = vin[-8:] if len(vin) >= 8 else vin
    fee = vehicle.get('transport_fee')
    price_str = format_price_display(vehicle.get('price'), fee, with_cents=False)
    mileage = vehicle.get('mileage')
    try:
        mileage_str = f"{int(mileage):,} mi" if mileage is not None else "mileage —"
    except (TypeError, ValueError):
        mileage_str = "mileage —"
    print(
        f"[{prefix}: {label}]\n"
        f"  {year} {trim} | {price_str} | {mileage_str} | VIN ...{vin_tail}"
    )


def run_scan(fetcher, on_fetch_done=None):
    source_name = 'Tesla Official'
    label = 'Tesla official'
    print("\n" + "-" * 50)
    print(f"[{label}] {time.strftime('%Y-%m-%d %H:%M:%S')} scan start...")
    t0 = time.monotonic()
    counters = {
        'matched': 0, 'new': 0, 'price_drop': 0, 'relist': 0, 'sold': 0,
        'pending_notifies': [],
    }
    active_vins = set()
    fetch_ok = False
    vehicles = []

    try:
        vehicles, fetch_ok = fetcher.fetch()
    except Exception as e:
        print(f"[{label}] fetch error: {e}")
        if on_fetch_done:
            on_fetch_done(False)
        print("-" * 50)
        return False

    print(f"[{label}] fetch done in {time.monotonic() - t0:.0f}s, {len(vehicles)} candidates.")
    if on_fetch_done:
        on_fetch_done(fetch_ok)

    try:
        for v in vehicles:
            dist, nearby = calculate_distance(v.get('dealer_address') or '')
            v['distance'] = dist
            if BUYER_SEARCH and v.get('distance_miles') is None and dist is not None:
                v['distance_miles'] = dist
                v['distance_source'] = 'city_coordinates'
            v['is_nearby'] = bool(nearby and dist is not None and dist <= NEARBY_RADIUS)
            v['matched_dealer_name'] = 'Tesla Inc. (official)'
            v['match_score'] = 1.0
            v['chain_warning'] = False
            active_vins.add(v['vin'])
            eligible = _should_realtime_push(v)
            if not BUYER_SEARCH or eligible:
                counters['matched'] += 1
            should_notify, status, old_price = is_new_or_price_dropped(
                v, queue_alert=eligible,
                match_rule=BUYER_SEARCH.match if BUYER_SEARCH else None,
            )
            if not should_notify:
                continue
            if BUYER_SEARCH and not eligible:
                continue
            if status == 'new':
                counters['new'] += 1
            elif status == 'price_drop':
                counters['price_drop'] += 1
            elif status == 'relist':
                counters['relist'] += 1
            elif status != 'match':
                continue
            if eligible:
                counters['pending_notifies'].append((v, status, old_price))
            elif classify_tesla_tier(v) == 'other':
                _print_skip_console('outside alert criteria', v, status, old_price)
            elif push_tier(v) is not None and (_transport_fee_int(v) is None or _transport_fee_int(v) > TESLA_NEAR_MAX_FEE):
                _print_skip_console(f'transport >${TESLA_NEAR_MAX_FEE} not pushed', v, status, old_price)

        skip_miss_vins = set()
        inv_vins = {
            str(v).strip().upper()
            for v in (getattr(fetcher, 'last_inventory_vins', set()) or set())
            if v
        }
        active_vins |= inv_vins
        present = {str(v).strip().upper() for v in (getattr(fetcher, 'detail_confirmed_present', set()) or set()) if v}
        ambiguous = {str(v).strip().upper() for v in (getattr(fetcher, 'detail_verify_ambiguous', set()) or set()) if v}
        skipped = {str(v).strip().upper() for v in (getattr(fetcher, 'detail_verify_skipped', set()) or set()) if v}
        confirmed_sold = {str(v).strip().upper() for v in (getattr(fetcher, 'detail_confirmed_sold', set()) or set()) if v}
        active_vins |= present
        skip_miss_vins |= ambiguous | skipped
        if present or confirmed_sold or ambiguous or skipped:
            print(
                f"[{label}] detail verify: keep {len(present)} | "
                f"redirected/unavailable {len(confirmed_sold)} | skip-miss {len(skip_miss_vins)}"
            )

        # Disabled models must never be marked unavailable by this scan.
        for row in get_currently_active_listings():
            code = row.get('model_code') or {'Model Y': 'my', 'Model 3': 'm3'}.get(row.get('model'))
            if code not in TESLA_MODELS:
                skip_miss_vins.add(row['vin'])

        if fetch_ok and not (BUYER_SEARCH and BUYER_SEARCH.watches_new):
            newly_sold = update_active_status(
                source_name, active_vins, skip_miss_vins=skip_miss_vins or None,
                notification_predicate=_should_realtime_push,
            )
            pending_sold = []
            for v in newly_sold:
                if not _should_realtime_push(v):
                    if classify_tesla_tier(v) == 'other':
                        _print_skip_console('outside alert criteria', v, 'sold')
                    elif push_tier(v) is not None and (_transport_fee_int(v) is None or _transport_fee_int(v) > TESLA_NEAR_MAX_FEE):
                        _print_skip_console(f'transport >${TESLA_NEAR_MAX_FEE} not pushed', v, 'sold')
                    continue
                dist, nearby = calculate_distance(v.get('dealer_address') or '')
                v['distance'] = dist
                v['is_nearby'] = bool(nearby)
                v['matched_dealer_name'] = 'Tesla Inc. (official)'
                counters['sold'] += 1
                pending_sold.append((v, 'sold', None))
            counters['pending_notifies'].extend(pending_sold)
        elif not fetch_ok:
            print(
                f"[{label}] Incomplete fetch — stored {len(vehicles)} rows; "
                f"skip sold/miss logic this round."
            )

        sent = flush_pending_alerts()
        if sent:
            print(f"[{label}] Delivered {sent} queued alerts.")

    except Exception as e:
        print(f"[{label}] process error: {e}")
        fetch_ok = False

    print(
        f"[{label}] done in {time.monotonic() - t0:.0f}s | "
        f"matched {counters['matched']} | new {counters['new']} | "
        f"drops {counters['price_drop']} | relist {counters['relist']} | "
        f"sold alerts {counters['sold']}"
    )
    print("-" * 50)
    return fetch_ok


def run_loop(stop_event=None):
    init_db()
    watches_new = BUYER_SEARCH is not None and BUYER_SEARCH.watches_new
    if watches_new:
        from fetchers.new_inventory import NewInventoryFetcher
        fetcher = NewInventoryFetcher()
    else:
        fetcher = TeslaFetcher()
    last_scan_at = None
    in_backoff = False
    scope = 'New & Demo (experimental)' if watches_new else 'used inventory'
    print(f"[sys] YRadar — Tesla official {scope} ({', '.join(TESLA_MODELS)})")
    if BUYER_SEARCH:
        print(f"[sys] Scan every {TESLA_INTERVAL_MINS} minutes.")
    else:
        print(
            f"[sys] Scan every {TESLA_INTERVAL_MINS} min | "
            f"digest {REVIEW_SLOTS[0][0]:02d}:{REVIEW_SLOTS[0][1]:02d} / "
            f"{REVIEW_SLOTS[1][0]:02d}:{REVIEW_SLOTS[1][1]:02d}"
        )
    print(f"[sys] 403 / Access Denied → pause {TESLA_FAIL_BACKOFF_MINS} min, then resume.")
    print("[sys] Ctrl+C to exit.")

    while stop_event is None or not stop_event.is_set():
        flush_pending_alerts()
        if not BUYER_SEARCH:
            check_and_trigger_daily_review(force=False)
        if should_run_tesla_scan(last_scan_at, in_backoff=in_backoff):
            was_backoff = in_backoff

            def on_fetch_done(ok):
                nonlocal in_backoff
                if ok:
                    if was_backoff:
                        send_system_alert(
                            f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                            "Inventory scanning recovered."
                        )
                    in_backoff = False
                else:
                    in_backoff = True
                    send_system_alert(
                        f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                        f"Scan incomplete ({fetcher.last_fail_reason or 'fetch error'}). "
                        f"Retrying in {TESLA_FAIL_BACKOFF_MINS} minutes."
                        + ('' if watches_new else ' If a browser challenge persists, run warmup_tesla.py.')
                    )

            ok = run_scan(fetcher)
            on_fetch_done(ok)
            last_scan_at = datetime.datetime.now()
        sleep_s = compute_tesla_sleep_seconds(last_scan_at, in_backoff=in_backoff)
        chunk = min(LOOP_SLEEP_CHUNK_SECS, max(1, int(sleep_s) or LOOP_SLEEP_CHUNK_SECS))
        if stop_event is None:
            time.sleep(chunk)
        else:
            stop_event.wait(chunk)


def main():
    parser = argparse.ArgumentParser(description="YRadar — Tesla official Model Y and Model 3 inventory alerts")
    parser.add_argument('--loop', action='store_true', help='Run continuously')
    parser.add_argument('--review', action='store_true', help='Send a digest now and exit')
    parser.add_argument('--demo', action='store_true', help='Show synthetic alerts offline; no browser, network, or user database')
    args = parser.parse_args()
    if args.demo:
        from demo import run_demo
        run_demo()
        return 0
    init_db()
    if args.review:
        check_and_trigger_daily_review(force=True)
        return
    if args.loop:
        try:
            run_loop()
        except KeyboardInterrupt:
            print("\n[sys] Stopped.")
        return
    fetcher = TeslaFetcher()
    return 0 if run_scan(fetcher) else 1


if __name__ == '__main__':
    raise SystemExit(main())
