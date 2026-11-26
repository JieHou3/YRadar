"""Experimental monitor for listings returned by Tesla's public new inventory page."""

import asyncio
from hashlib import sha256
import json
import math
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from config import TESLA_MODELS, TESLA_ZIP
from fetchers.base_fetcher import BaseFetcher
from vin_hw import VIN_RE, model_year_from_vin


TESLA_ORIGIN = 'https://www.tesla.com'
MODELS = {'my': 'Model Y', 'm3': 'Model 3'}


def inventory_url(model, zip_code):
    return f'{TESLA_ORIGIN}/inventory/new/{model}?' + urlencode({'zip': zip_code})


def matches_inventory_response(url, model, zip_code):
    target = urlsplit(url)
    if target.scheme != 'https' or target.netloc != 'www.tesla.com':
        return False
    if not re.fullmatch(r'/inventory/api/v[0-9]+/inventory-results', target.path):
        return False
    try:
        envelope = json.loads(parse_qs(target.query)['query'][0])
        query = envelope['query']
        return (query['condition'] == 'new' and query['model'] == model
                and str(query['zip']) == zip_code and envelope['offset'] == 0
                and envelope.get('outsideOffset', 0) == 0)
    except (KeyError, ValueError, TypeError):
        return False


def normalize_listing(record, model, zip_code):
    identifier = record.get('VIN')
    price = record.get('InventoryPrice')
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', identifier):
        return None
    if type(price) not in (int, float) or not math.isfinite(price) or price <= 0:
        return None
    reported_model = record.get('Model')
    if reported_model and str(reported_model).casefold() not in (model, MODELS[model].casefold()):
        return None

    reported_year = record.get('Year')
    actual_vin = identifier.upper()
    year = reported_year if type(reported_year) is int and 1990 <= reported_year <= 2100 else None
    if year is None and VIN_RE.fullmatch(actual_vin):
        year = model_year_from_vin(actual_vin)
    mileage = record.get('Odometer')
    if type(mileage) is not int or mileage < 0:
        mileage = None
    declared_demo = record.get('IsDemo')
    label = {True: 'Demo vehicle', False: 'New vehicle'}.get(declared_demo) if isinstance(declared_demo, bool) else None
    link = inventory_url(model, zip_code)
    supplied_link = record.get('URL') or record.get('url')
    if isinstance(supplied_link, str):
        proposed = urljoin(TESLA_ORIGIN, supplied_link)
        target = urlsplit(proposed)
        if (target.scheme == 'https' and target.netloc == 'www.tesla.com'
                and re.fullmatch(f'/{model}/order/[a-zA-Z0-9_-]+', target.path)
                and parse_qs(target.query).get('titleStatus') == ['new']):
            link = proposed

    identity = sha256(f'{model}:{identifier}'.encode()).hexdigest().upper()
    interior = record.get('INTERIOR')
    if isinstance(interior, list):
        interior = interior[0] if interior else None
    return {
        'vin': identity,  # Storage key for this mode, independent of the VIN decoder.
        'model': MODELS[model], 'model_code': model, 'year': year,
        'trim': str(record.get('TrimName') or 'Unknown trim'),
        'price': price, 'mileage': mileage, 'url': link,
        'dealer_name': 'Tesla Inc.',
        'dealer_address': ', '.join(str(record[key]) for key in ('City', 'StateProvince') if record.get(key))
                          or 'Location unavailable',
        'source': 'Tesla Official', 'color_int': interior if isinstance(interior, str) else None,
        'inventory_kind': label or 'New inventory (type unverified)',
    }


def parse_snapshot(payload, model, zip_code):
    if not isinstance(payload, dict):
        raise ValueError('Unexpected inventory response')
    results, total = payload.get('results'), payload.get('total_matches_found')
    if not isinstance(results, list) or type(total) is not int or total < len(results):
        raise ValueError('Inventory total or result list is missing or inconsistent')
    if not results and total != 0:
        raise ValueError('Empty page conflicts with the reported inventory total')
    vehicles = {}
    for record in results:
        car = normalize_listing(record, model, zip_code) if isinstance(record, dict) else None
        if car is None:
            raise ValueError('Unrecognized inventory identifier, cash price or model')
        vehicles[car['vin']] = car
    return list(vehicles.values()), total


class NewInventoryFetcher(BaseFetcher):
    def __init__(self):
        self.last_fail_reason = None

    def fetch(self):
        self.last_fail_reason = None
        return asyncio.run(self._fetch())

    async def _fetch(self):
        from playwright.async_api import async_playwright

        vehicles = []
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(channel='chrome')
            except Exception as error:
                self.last_fail_reason = 'Chrome could not start'
                print(f'[new inventory] {self.last_fail_reason} ({type(error).__name__}).')
                return [], False
            async with browser:
                page = await browser.new_page()
                for model in TESLA_MODELS:
                    try:
                        async with page.expect_response(
                            lambda response: matches_inventory_response(response.url, model, TESLA_ZIP),
                            timeout=30000,
                        ) as pending:
                            navigation = await page.goto(inventory_url(model, TESLA_ZIP),
                                                         wait_until='domcontentloaded', timeout=25000)
                            if navigation is None or not navigation.ok:
                                status = navigation.status if navigation else 'no response'
                                raise ValueError(f'Inventory page HTTP {status}')
                        response = await pending.value
                        if not response.ok:
                            raise ValueError(f'Inventory data HTTP {response.status}')
                        rows, total = parse_snapshot(await response.json(), model, TESLA_ZIP)
                    except Exception as error:
                        self.last_fail_reason = f'{MODELS[model]}: {error if type(error) is ValueError else type(error).__name__}'
                        print(f'[new inventory] {self.last_fail_reason}.')
                        return vehicles, False
                    vehicles.extend(rows)
                    if total == 0:
                        print(f'[new inventory] {MODELS[model]}: Tesla reported zero listings for ZIP {TESLA_ZIP}.')
                    else:
                        print(f'[new inventory] {MODELS[model]}: observed {len(rows)} of {total} reported listings.')
        return vehicles, True
