"""Telegram and console notifications (English)."""
import datetime
import html
import re
import sys
import requests
from config import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    WATCH_MAX_PRICE, FOCUS_MAX_PRICE, HW4_MAX_PRICE, TESLA_NEAR_MAX_FEE,
    PRICE_DISPLAY_MODE, NEARBY_RADIUS, MAX_MILEAGE,
    INCLUDE_TRANSPORT_IN_SORT, PUSH_UNKNOWN_TRANSPORT,
)
from vin_hw import (
    vin_review_suffix, assess_hw4, assess_matrix_headlights,
)
from vehicle_tiers import (
    push_tier, classify_tesla_tier, bold_keywords_html, tier_label,
    is_long_range, is_white_interior,
)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

TELEGRAM_CHUNK_SIZE = 3800
MATRIX_MISSING_MARK = 'matrix headlights ❌'


def format_duration(start_time_str, end_time_str):
    try:
        fmt = '%Y-%m-%d %H:%M:%S'
        start = datetime.datetime.strptime(start_time_str, fmt)
        end = datetime.datetime.strptime(end_time_str, fmt)
        delta = end - start
        days = delta.days
        hours = delta.seconds // 3600
        mins = (delta.seconds % 3600) // 60
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if not parts:
            parts.append(f"{mins}m")
        return "".join(parts)
    except Exception:
        return "unknown"


def _fee(transport_fee):
    try:
        return max(0, int(transport_fee or 0))
    except (TypeError, ValueError):
        return 0


def _fee_known(transport_fee, status=None):
    return transport_fee is not None and status not in ('unknown', 'unavailable')


def format_price_display(price, transport_fee, with_cents=True, transport_fee_status=None):
    """Sticker vs delivered total, controlled by PRICE_DISPLAY_MODE."""
    net = float(price or 0)
    fee = _fee(transport_fee)
    known = _fee_known(transport_fee, transport_fee_status)
    total = net + fee
    if with_cents:
        net_s, total_s = f"${net:,.2f}", f"${total:,.2f}"
    else:
        net_s, total_s = f"${net:,.0f}", f"${total:,.0f}"
    if not known:
        return f"{net_s} (transport unknown)"
    if PRICE_DISPLAY_MODE == 'total':
        if fee > 0:
            return f"{total_s} (includes ${fee:,} transport)"
        return total_s
    if fee > 0:
        return f"{net_s} (+{fee} transport)"
    return net_s


def format_price_html(price, transport_fee, transport_fee_status=None):
    net = float(price or 0)
    fee = _fee(transport_fee)
    known = _fee_known(transport_fee, transport_fee_status)
    total = net + fee
    if not known:
        return f"<b>${net:,.2f}</b> (transport unknown)"
    if PRICE_DISPLAY_MODE == 'total':
        if fee > 0:
            return f"<b>${total:,.2f}</b> (includes ${fee:,} transport)"
        return f"<b>${total:,.2f}</b>"
    suffix = f" (+{fee} transport)" if fee > 0 else ""
    return f"<b>${net:,.2f}</b>{suffix}"


def _format_vehicle_title(vehicle):
    year = _e(vehicle.get('year'))
    model = _e(vehicle.get('model') or 'Tesla')
    trim = _e(vehicle.get('trim') or '')
    title = f"{year} {model} {trim}".strip()
    if vehicle.get('is_repaired'):
        title += " ⚠️ Repaired"
    return title


def _e(value):
    return html.escape(str(value if value is not None else 'n/a'), quote=True)


def _price_html(vehicle, price=None):
    return format_price_html(
        vehicle.get('price') if price is None else price,
        vehicle.get('transport_fee'), vehicle.get('transport_fee_status'),
    )


def _hardware_assessment(vehicle, kind):
    status = vehicle.get(f'{kind}_status')
    source = vehicle.get(f'{kind}_source')
    reason = vehicle.get(f'{kind}_reason')
    if not status:
        model = vehicle.get('model_code') or vehicle.get('model') or ''
        result = (assess_hw4 if kind == 'hw4' else assess_matrix_headlights)(
            vehicle.get('vin'), model=model,
        )
        status, source, reason = result['status'], result['source'], result['reason']
    return status, source, reason


def _hardware_line(vehicle, kind):
    status, source, reason = _hardware_assessment(vehicle, kind)
    label = {'confirmed': 'confirmed', 'likely': 'likely', 'unlikely': 'unlikely', 'unknown': 'unknown'}.get(status, status)
    details = '; '.join(_e(x) for x in (source, reason) if x)
    return f"<b>{_e(label)}</b>" + (f" ({details})" if details else '')


def _source_title_tail(vehicle, source):
    bits = []
    if _seat_count_of(vehicle) == 7:
        bits.append('7-seat')
    extra = (' / ' + ' / '.join(bits)) if bits else ''
    return f"{extra} ({_e(source)})"


def _transport_detail(vehicle):
    fee = vehicle.get('transport_fee')
    status = vehicle.get('transport_fee_status')
    source = vehicle.get('transport_fee_source') or 'unknown'
    if not _fee_known(fee, status):
        policy = 'eligible for alerts' if PUSH_UNKNOWN_TRANSPORT else 'not eligible for realtime alerts'
        return f"• Transport: <b>unknown</b> (source: {_e(source)}; {policy})"
    estimate = 'estimated ' if source == 'distance_band' else ''
    return f"• Transport: {estimate}<b>${_fee(fee):,}</b> (source: {_e(source)})"


def _wheel_inches_of(vehicle):
    try:
        w = vehicle.get('wheel_inches')
        return int(w) if w not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _seat_count_of(vehicle):
    try:
        s = vehicle.get('seat_count')
        return int(s) if s not in (None, '') else None
    except (TypeError, ValueError):
        return None


def build_notification_text(vehicle, status_type='new', old_price=None, matcher=None):
    source = vehicle.get('source') or 'Tesla Official'
    vin = vehicle['vin']
    trim = vehicle.get('trim') or ''
    price = vehicle['price']
    mileage = vehicle['mileage']
    url = vehicle['url']
    color_ext = vehicle.get('color_ext') or 'n/a'
    color_int = vehicle.get('color_int') or 'n/a'
    accident_history = vehicle.get('accident_history') or 'n/a'
    clean_title = vehicle.get('clean_title') or 'n/a'
    distance = vehicle.get('distance_miles', vehicle.get('distance'))
    is_nearby = vehicle.get('is_nearby', False)
    city = (vehicle.get('dealer_address') or '').split(',')[0].strip() or 'unknown'

    if distance is not None:
        distance_note = " (approx. straight-line)"
        if is_nearby:
            distance_text = f"• Distance: <b>📍 {distance:.1f} mi{distance_note} (within {NEARBY_RADIUS} mi)</b>"
        else:
            distance_text = f"• Distance: {distance:.1f} mi{distance_note}"
    else:
        distance_text = "• Distance: unknown (city not in SoCal table)"

    vehicle_title = _format_vehicle_title(vehicle)
    tier = push_tier(vehicle) or vehicle.get('push_tier')
    tail = _source_title_tail(vehicle, source)

    if status_type == 'new':
        if tier == 'favorite':
            title = f"🚗 <b>Watch list — new</b>: White Interior / Long Range AWD / Clean{tail}"
        elif tier == 'focus':
            title = f"🚗 <b>Long Range AWD / HW4 — new</b>{tail}"
        elif tier == 'hw4' or (vehicle.get('hw4_status') in ('confirmed', 'likely')):
            title = f"🚗 <b>HW4 listing — new</b>{tail}"
        else:
            title = f"🚗 <b>New listing</b>{tail}"
        price_text = f"• Price: {_price_html(vehicle)}"
    elif status_type == 'relist':
        title = f"🔄 <b>Relisted</b>: {tier_label(tier) or 'listing'}{tail}"
        price_text = f"• Price: {_price_html(vehicle)}"
        if vehicle.get('relist_sold_at'):
            now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            off_dur = format_duration(vehicle['relist_sold_at'], now_str)
            price_text += f"\n• Was offline: <b>{off_dur}</b> (last absent at {_e(vehicle['relist_sold_at'])})"
        if vehicle.get('relist_old_price', old_price) is not None:
            old_disp = format_price_display(
                vehicle.get('relist_old_price', old_price),
                vehicle.get('transport_fee'), with_cents=True,
                transport_fee_status=vehicle.get('transport_fee_status'),
            )
            price_text += f"\n• Price before delist: <s>{old_disp}</s>"
    elif status_type == 'price_drop':
        old_net = float(old_price or 0)
        new_net = float(price or 0)
        drop_amount = old_net - new_net
        drop_pct = (drop_amount / old_net * 100) if old_net else 0
        title = f"📉 <b>Price drop</b>: {tier_label(tier) or 'listing'}{tail}"
        price_text = (
            f"• Was: <s>{format_price_display(old_price, vehicle.get('transport_fee'), transport_fee_status=vehicle.get('transport_fee_status'))}</s>\n"
            f"• Now: {_price_html(vehicle)}\n"
            f"• Drop: <b>${drop_amount:,.2f}</b> (-{drop_pct:.1f}%)"
        )
    elif status_type == 'sold':
        title = f"💸 <b>No longer listed</b>: {_e(tier_label(tier) or 'listing')}{tail}"
        price_text = f"• Last price: {_price_html(vehicle)}"
        if vehicle.get('first_seen') and vehicle.get('sold_at'):
            price_text += f"\n• On lot: <b>{format_duration(vehicle['first_seen'], vehicle['sold_at'])}</b>"
    else:
        title = f"🔔 <b>Inventory update</b>{tail}"
        price_text = f"• Price: {_price_html(vehicle)}"

    title = bold_keywords_html(title, tier)
    if tier == 'favorite' and is_white_interior(color_int) and 'White Interior' not in vehicle_title:
        vehicle_title = bold_keywords_html(f"{vehicle_title} / White Interior", tier)
    else:
        vehicle_title = bold_keywords_html(vehicle_title, tier)

    tesla_extra = ""
    est_range = vehicle.get('est_range')
    if est_range:
        tesla_extra += f"\n• Est. range: <b>{_e(est_range)} {_e(vehicle.get('range_unit') or 'mi')}</b>"
    if vehicle.get('is_repaired'):
        tesla_extra += "\n• ⚠️ <b>Repaired Pre-Owned</b> — inspect carefully"

    wheel = _wheel_inches_of(vehicle)
    wheel_line = ""
    if wheel:
        wheel_line = f"\n• Wheels: {'🛞 ' if wheel >= 20 else ''}{wheel}-inch"
    tire = vehicle.get('tire_size') or vehicle.get('tires')
    if tire:
        wheel_line += f" ({_e(tire)})"
    seats = _seat_count_of(vehicle)
    seats_line = f"\n• Seats: <b>{seats}-seat</b>" if seats else "\n• Seats: unknown"

    telegram_text = f"""{title}

📍 <b>Vehicle</b>
• Spec: {vehicle_title}
{price_text}
{_transport_detail(vehicle)}
• Mileage: {f'{int(mileage):,}' if mileage is not None else 'unknown'} mi
• HW4: {_hardware_line(vehicle, 'hw4')}
• Matrix headlights: {_hardware_line(vehicle, 'matrix')}{tesla_extra}
• Color: exterior {_e(color_ext)} / interior {_e(color_int)}{wheel_line}{seats_line}
• History: {_e(accident_history)} | {_e(clean_title)}

🏢 <b>Location</b>
• {_e(city)}
{distance_text}

🔗 <a href="{_e(url)}">Open Tesla order page</a>
VIN: <code>{_e(vin)}</code>
"""

    return telegram_text


def send_notification(vehicle, status_type='new', old_price=None, matcher=None):
    telegram_text = build_notification_text(vehicle, status_type, old_price, matcher)
    vin = vehicle.get('vin') or 'unknown'
    tier = push_tier(vehicle) or vehicle.get('push_tier')

    print(f"\n[notify] {status_type} VIN {vin} ({tier or '—'})")
    return _deliver_message(telegram_text, preview=True)


def _short_color(color_text):
    if not color_text:
        return '?'
    lower = color_text.lower()
    if 'gray' in lower or 'grey' in lower or 'stealth' in lower:
        return 'gray'
    if 'white' in lower:
        return 'white'
    if 'black' in lower:
        return 'black'
    if 'blue' in lower:
        return 'blue'
    if 'red' in lower:
        return 'red'
    if 'silver' in lower:
        return 'silver'
    return color_text[:6]


def _review_line(v, now_str, for_sold=False, item_no=None):
    num = f"{item_no}. " if item_no is not None else ""
    model = _e(v.get('model') or 'Tesla')
    trim = _e((v.get('trim') or '')[:18])
    dist = v.get('distance_miles')
    if dist is None:
        dist = v.get('distance')
    dist_str = f" {dist:.0f}mi" if dist is not None else ""
    vin_short = _e(vin_review_suffix(v.get('vin')))
    relist = ' 🔄relisted' if v.get('review_is_relist') else ''
    treat_sold = for_sold or (not v.get('is_active', 1) and bool(v.get('sold_at')))
    price_s = format_price_display(
        v.get('price'), v.get('transport_fee'), with_cents=False,
        transport_fee_status=v.get('transport_fee_status'),
    )
    hw4_status = _hardware_assessment(v, 'hw4')[0]
    matrix_status = _hardware_assessment(v, 'matrix')[0]
    hw4 = f' | HW4 {_e(hw4_status)}'
    mx = f' | matrix {_e(matrix_status)}'
    rng = f" | {_e(v['est_range'])}{_e(v.get('range_unit') or 'mi')}" if v.get('est_range') else ''
    colors = f"{_e(_short_color(v.get('color_ext')))}/{_e(_short_color(v.get('color_int')))}"
    wheel = _wheel_inches_of(v)
    wheel_s = f' | {wheel}-inch' if wheel else ''
    seat_count = _seat_count_of(v)
    seats = f' | <b>{seat_count}-seat</b>' if seat_count else ''
    repaired = ' | ⚠️Repaired' if v.get('is_repaired') else ''
    year = _e(v.get('year'))
    mileage = f"{int(v.get('mileage')):,}" if v.get('mileage') is not None else 'unknown'
    if treat_sold:
        dur = format_duration(v.get('first_seen'), v.get('sold_at') or now_str)
        return (
            f"{num}• <s>{year} {model} {trim} {_e(price_s)}</s>{relist} "
            f"| {mileage}mi{dist_str}{hw4}{mx}{rng} | {colors}{wheel_s}{seats}{repaired}\n"
            f"  <code>{vin_short}</code> on lot {dur}"
        )
    in_stock = format_duration(v.get('first_seen'), now_str) if now_str else "?"
    return (
        f"{num}{year} {model} {trim} {_e(price_s)}{relist} "
        f"| {mileage}mi{dist_str}{hw4}{mx}{rng} | {colors}{wheel_s}{seats} "
        f"| listed {in_stock}{repaired} | {vin_short}"
    )


def _filter_tier(listings, name):
    return [v for v in listings if v.get('source') == 'Tesla Official' and classify_tesla_tier(v) == name]


def _review_sort_key(vehicle):
    price = float(vehicle.get('price') or 0)
    if not INCLUDE_TRANSPORT_IN_SORT:
        return (0, price)
    if not _fee_known(vehicle.get('transport_fee'), vehicle.get('transport_fee_status')):
        return (1, price)
    return (0, price + _fee(vehicle.get('transport_fee')))


def send_daily_review(new_listings, active_listings, sold_today, review_time_label=None):
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    date_short = datetime.datetime.now().strftime('%m/%d')
    if not review_time_label:
        review_time_label = datetime.datetime.now().strftime('%H:%M')

    tesla_new = [v for v in new_listings if v.get('source') == 'Tesla Official']
    tesla_active = [v for v in active_listings if v.get('source') == 'Tesla Official']
    tesla_sold = [v for v in sold_today if v.get('source') == 'Tesla Official']
    watch_new = _filter_tier(tesla_new, 'favorite')
    watch_act = _filter_tier(tesla_active, 'favorite')
    watch_sold = _filter_tier(tesla_sold, 'favorite')

    def dump(title, items, sold=False):
        lines.append(title)
        if not items:
            lines.append("none")
            return
        items = sorted(items, key=_review_sort_key)
        for i, v in enumerate(items, 1):
            lines.append(_review_line(v, now_str, for_sold=sold, item_no=i))

    lines = [
        f"📋 <b>YRadar daily digest</b> {date_short} {review_time_label}",
        "",
        "📌 <b>Filters</b>",
        f"• Watch: White Interior / Long Range AWD / Clean, sticker ≤ ${WATCH_MAX_PRICE:,}, ≤{MAX_MILEAGE:,} mi",
        f"• Long Range HW4: sticker ≤ ${FOCUS_MAX_PRICE:,}",
        f"• Other HW4: sticker ≤ ${HW4_MAX_PRICE:,}",
        f"• Realtime alerts: transport ≤ ${TESLA_NEAR_MAX_FEE}; unknown transport "
        f"{'included' if PUSH_UNKNOWN_TRANSPORT else 'excluded'}",
        f"• Price display: <code>{PRICE_DISPLAY_MODE}</code>",
        "",
        f"📊 Tesla official  new {len(tesla_new)} | in stock {len(tesla_active)} | gone {len(tesla_sold)}",
        f"⭐ Watch list  new {len(watch_new)} | in stock {len(watch_act)} | gone {len(watch_sold)}",
        "",
    ]
    dump("🆕 New today (sticker low→high)", tesla_new)
    lines.append("")
    dump("🚘 In stock (sticker low→high)", tesla_active)
    lines.append("")
    dump("💸 No longer listed today", tesla_sold, sold=True)
    lines.append("")
    lines.append("────────")
    lines.append("⭐ <b>Watch list</b> — White Interior / Long Range AWD / Clean")
    lines.append("")
    dump("🆕 Watch · new", watch_new)
    lines.append("")
    dump("🚘 Watch · in stock", watch_act)
    lines.append("")
    dump("💸 Watch · no longer listed", watch_sold, sold=True)

    return _deliver_message('\n'.join(lines), preview=False)


def _split_telegram_html(text, limit=TELEGRAM_CHUNK_SIZE):
    """Split only between balanced lines; oversized lines become safe plain text."""
    def escaped_pieces(value, width):
        result, current = [], ''
        for character in value:
            escaped = html.escape(character)
            if current and len(current) + len(escaped) > width:
                result.append(current)
                current = ''
            current += escaped
        if current:
            result.append(current)
        return result

    chunks = []
    buffer = ''
    for line in text.split('\n'):
        if len(line) > limit:
            plain = html.unescape(re.sub(r'<[^>]+>', '', line))
            width = max(1, limit - 20)
            pieces = escaped_pieces(plain, width)
        else:
            pieces = [line]
        for piece in pieces:
            candidate = f"{buffer}\n{piece}" if buffer else piece
            if len(candidate) > limit and buffer:
                chunks.append(buffer)
                buffer = piece
            else:
                buffer = candidate
    if buffer:
        chunks.append(buffer)
    return chunks or ['']


def _send_telegram_html(text, preview=False):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print("[hint] No Telegram credentials — console only:")
        print(re.sub(r'<[^>]+>', '', text)[:2000])
        return True
    tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    chunks = _split_telegram_html(text)
    all_ok = True
    for i, chunk in enumerate(chunks):
        if len(chunks) > 1:
            chunk = f"<i>({i + 1}/{len(chunks)})</i>\n" + chunk
        try:
            res = requests.post(
                tg_url,
                json={
                    'chat_id': TELEGRAM_CHAT_ID,
                    'text': chunk,
                    'parse_mode': 'HTML',
                    'disable_web_page_preview': not preview,
                },
                timeout=15,
            )
            if res.status_code == 200:
                print(f" -> Telegram ok ({i + 1}/{len(chunks)})")
            else:
                print(f" -> Telegram fail {res.status_code}")
                all_ok = False
        except requests.RequestException as e:
            print(f" -> Telegram request error ({type(e).__name__})")
            all_ok = False
    return all_ok


def _deliver_message(text, preview=False):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print("[demo] No notification credentials — console output:")
        print(html.unescape(re.sub(r'<[^>]+>', '', text))[:4000])
        return True
    return _send_telegram_html(text, preview=preview)


def send_system_alert(text):
    msg = (text or '').strip()
    if not msg:
        return False
    print(f"[alert] {msg}")
    return _deliver_message(_e(msg), preview=False)


# monitor.py imports this name
_format_total_price_display = format_price_display
