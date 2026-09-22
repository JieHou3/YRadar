"""Watch-list tiers for Tesla official used inventory."""
from config import (
    HW4_MAX_PRICE,
    FOCUS_MAX_PRICE,
    WATCH_MAX_PRICE,
    BASE_MAX_PRICE,
    ALERT_TIERS,
)
from vin_hw import is_hw4_vehicle


def _transport_fee_amount(transport_fee):
    if transport_fee is None:
        return None
    try:
        return max(0, int(transport_fee))
    except (TypeError, ValueError):
        return None


def net_price(vehicle):
    return float(vehicle.get('price') or 0)


def total_price(vehicle):
    fee = _transport_fee_amount(vehicle.get('transport_fee'))
    return None if fee is None else net_price(vehicle) + fee


def sort_price(vehicle, include_transport=False):
    if not include_transport:
        return net_price(vehicle)
    total = total_price(vehicle)
    return float('inf') if total is None else total


def is_long_range(trim):
    if not trim:
        return False
    t = str(trim).lower().replace('-', ' ').replace('_', ' ')
    return 'long range' in t or t.strip() in ('lr', 'longrange')


def is_awd(trim):
    if not trim:
        return False
    value = str(trim).lower().replace('-', ' ').replace('_', ' ')
    return 'awd' in value or 'all wheel drive' in value


def is_confirmed_clean(vehicle):
    return str(vehicle.get('clean_status') or '').lower() == 'clean'


def is_white_interior(color_int):
    if not color_int:
        return False
    return 'white' in str(color_int).lower()


def classify_tesla_tier(vehicle):
    """
    'favorite'  White Interior / Long Range AWD / Clean within WATCH_MAX_PRICE
    'focus'     Long Range + HW4 within FOCUS_MAX_PRICE
    'hw4'    HW4 within HW4_MAX_PRICE
    'other'  non-HW4 within HW4_MAX_PRICE (stored, not pushed)
    None     drop
    """
    vin = vehicle.get('vin') or ''
    net = net_price(vehicle)
    model = vehicle.get('model_code') or vehicle.get('model') or 'my'
    known_hw4_status = str(vehicle.get('hw4_status') or '').lower()
    hw4 = (known_hw4_status in ('confirmed', 'likely') if known_hw4_status
           else is_hw4_vehicle(vin, model, vehicle.get('raw_record')))
    lr = is_long_range(vehicle.get('trim'))
    awd = is_awd(vehicle.get('trim'))
    white = is_white_interior(vehicle.get('color_int'))
    clean = is_confirmed_clean(vehicle)

    # Internal key remains 'favorite' (White Interior / Long Range AWD / Clean).
    if lr and awd and white and clean and net <= WATCH_MAX_PRICE:
        return 'favorite'
    if hw4 and lr and awd and net <= FOCUS_MAX_PRICE:
        return 'focus'
    if hw4 and net <= HW4_MAX_PRICE:
        return 'hw4'
    if (not hw4) and net <= BASE_MAX_PRICE:
        return 'other'
    return None


def should_store_tesla(vehicle):
    return classify_tesla_tier(vehicle) is not None


def push_tier(vehicle):
    tier = classify_tesla_tier(vehicle)
    if tier in ALERT_TIERS:
        return tier
    return None


def tier_label(tier):
    return {
        'favorite': 'White Interior / Long Range AWD / Clean',
        'focus': 'Long Range AWD / HW4',
        'hw4': 'HW4',
        'other': 'Other',
    }.get(tier or '', '')


tier_label_zh = tier_label  # notifier import compatibility


def bold_keywords_html(text, tier):
    if not text:
        return text
    out = text
    replacements = []
    if tier in ('watch', 'focus', 'hw4', 'favorite'):
        replacements.append(('HW4', '<b>HW4</b>'))
    if tier in ('watch', 'focus', 'favorite'):
        replacements.extend([
            ('Long Range', '<b>Long Range</b>'),
            ('long-range', '<b>long-range</b>'),
            ('long range', '<b>long range</b>'),
        ])
    if tier in ('watch', 'favorite'):
        replacements.extend([
            ('White Interior', '<b>White Interior</b>'),
            ('Clean', '<b>Clean</b>'),
        ])
    for raw, bolded in replacements:
        if raw in out and bolded not in out:
            out = out.replace(raw, bolded)
    return out
