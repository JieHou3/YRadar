import json
import math
import os
import re
import asyncio
import urllib.parse
import datetime
from fetchers.base_fetcher import BaseFetcher
from fetchers.cdp_chrome import (
    cdp_base_url, is_cdp_ready, wait_for_cdp_ready, launch_chrome_debug,
    force_kill_cdp_port, find_chrome,
)
from config import (
    MAX_MILEAGE, TESLA_FETCH_MAX_PRICE, TESLA_ZIP, TESLA_SEARCH_RANGE,
    TESLA_ALL_DELIVERABLE, TESLA_SKIP_LOCAL_POOL, TESLA_ZERO_NEW_STOP_PAGES,
    TESLA_MIN_YEAR, MAX_MODEL_YEAR, TARGET_COORDS, CURRENT_DIR, CDP_VISIBLE,
    TESLA_DETAIL_VERIFY_DELAY_SECS, TESLA_DETAIL_VERIFY_PAUSE_SECS,
    TESLA_FAVORITE_MAX_PRICE, TESLA_FOCUS_MAX_PRICE, TESLA_MODELS,
    BASE_MAX_PRICE, TESLA_REGION, BUYER_SEARCH,
)
from vehicle_tiers import should_store_tesla, classify_tesla_tier, net_price
from vin_hw import model_year_from_vin, assess_hw4, assess_matrix_headlights


class TeslaFetchBlocked(Exception):
    """Blocked; abort remaining crawl tiers this round.

    kind: '403' = inventory API returned 403; 'akamai' = Access Denied page.
    """

    def __init__(self, status, where='', kind='403'):
        self.status = status
        self.where = where
        self.kind = kind  # '403' | 'akamai'
        super().__init__(f"blocked kind={kind} status={status} where={where}")


# Two inventory pages in one CDP session; VIN-deduped. Caps are net (sticker) price.
TESLA_CRAWL_TIERS = (
    {
        'label': 'White Interior / Long Range AWD / Clean',
        'query_extra': 'INTERIOR=PREMIUM_WHITE&VehicleHistory=CLEAN',
        'price_cap': TESLA_FAVORITE_MAX_PRICE,
    },
    {
        'label': 'Base inventory',
        'query_extra': '',
        'price_cap': BASE_MAX_PRICE,
    },
)

if BUYER_SEARCH:
    TESLA_CRAWL_TIERS = ({'label': 'Your search', 'query_extra': '', 'price_cap': BUYER_SEARCH.budget},)


class TeslaFetcher(BaseFetcher):
    PAGE_SIZE = 24  # Tesla inventory API returns at most 24 cars per page
    MAX_PAGES = 30
    CDP_PORT = 9225

    def __init__(self):
        super().__init__()
        self.zip_code = TESLA_ZIP
        self.search_range = TESLA_SEARCH_RANGE
        self.detail_confirmed_present = set()
        self.detail_confirmed_sold = set()
        self.detail_verify_ambiguous = set()
        self.detail_verify_skipped = set()
        self.last_inventory_vins = set()
        self.last_inventory_vins_by_model = {model: set() for model in TESLA_MODELS}
        self.model_fetch_ok = {model: False for model in TESLA_MODELS}
        self.last_fail_reason = None

    def _reset_detail_verify(self):
        self.detail_confirmed_present = set()
        self.detail_confirmed_sold = set()
        self.detail_verify_ambiguous = set()
        self.detail_verify_skipped = set()
        self.last_inventory_vins = set()
        self.last_inventory_vins_by_model = {model: set() for model in TESLA_MODELS}
        self.model_fetch_ok = {model: False for model in TESLA_MODELS}
        self.last_fail_reason = None

    @staticmethod
    def _tesla_year_list():
        max_year = MAX_MODEL_YEAR or (datetime.datetime.now().year + 1)
        return list(range(TESLA_MIN_YEAR, max_year + 1))

    def _apply_year_filter(self, inner):
        """Write Tesla API year filter (options.Year only; a top-level Year field returns 412)."""
        years = self._tesla_year_list()
        inner.setdefault('options', {})['Year'] = years
        inner.pop('Year', None)
        return years

    @staticmethod
    def _haversine_miles(lat1, lon1, lat2, lon2):
        """Great-circle distance in miles."""
        earth_radius_mi = 3958.8
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
        )
        return earth_radius_mi * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @classmethod
    def transport_assessment(cls, record, target_coords=None):
        """
        Estimate delivery fee to TESLA_ZIP.
        TransportationFee is often 0; real bands live in TransportFees.
        """
        target_coords = TARGET_COORDS if target_coords is None else target_coords

        fee_raw = record.get('TransportationFee')
        if fee_raw is not None:
            try:
                direct_fee = int(fee_raw)
                if direct_fee > 0:
                    return {'fee': direct_fee, 'status': 'known',
                            'source': 'tesla_quote', 'distance_miles': None}
            except (TypeError, ValueError):
                pass

        transport_fees = record.get('TransportFees') or {}
        vrl = record.get('Vrl') or record.get('Trt')
        exempt = transport_fees.get('exemptVRL') or []
        if vrl and vrl in exempt:
            return {'fee': 0, 'status': 'known', 'source': 'exempt',
                    'distance_miles': None}

        vrl_list = record.get('vrlList') or []
        if not vrl_list or target_coords is None:
            return {'fee': None, 'status': 'unknown', 'source': 'unknown',
                    'distance_miles': None}
        vrl_point = vrl_list[0]
        lat = vrl_point.get('lat')
        lon = vrl_point.get('lon')
        if lat is None or lon is None:
            return {'fee': None, 'status': 'unknown', 'source': 'unknown',
                    'distance_miles': None}

        try:
            distance_mi = cls._haversine_miles(
                float(lat), float(lon), float(target_coords[0]), float(target_coords[1]))
        except (TypeError, ValueError):
            return {'fee': None, 'status': 'unknown', 'source': 'unknown',
                    'distance_miles': None}

        for tier in transport_fees.get('fees') or []:
            if tier.get('type') != 'transportation' or tier.get('units') != 'miles':
                continue
            bounds = tier.get('query') or {}
            min_mi = float(bounds.get('min', 0))
            max_mi = float(bounds.get('max', float('inf')))
            if min_mi <= distance_mi <= max_mi:
                try:
                    return {'fee': int(tier.get('amount', 0)), 'status': 'estimated',
                            'source': 'distance_band', 'distance_miles': distance_mi}
                except (TypeError, ValueError):
                    break
        return {'fee': None, 'status': 'unknown', 'source': 'unknown',
                'distance_miles': distance_mi}

    @classmethod
    def estimate_transport_fee(cls, record, target_coords=None):
        """Compatibility wrapper. Unknown stays None rather than silently becoming $0."""
        return cls.transport_assessment(record, target_coords)['fee']

    def fetch(self):
        self._reset_detail_verify()
        try:
            return asyncio.run(self.async_fetch())
        except Exception as e:
            print(f" [error] Tesla CDP fetch failed: {e}")
            force_kill_cdp_port(self.CDP_PORT)
            return [], False

    def _build_api_url(self, envelope_obj):
        """Encode query envelope as API URL"""
        encoded = urllib.parse.quote(json.dumps(envelope_obj, separators=(',', ':')))
        return f"https://www.tesla.com/inventory/api/v4/inventory-results?query={encoded}"

    def _prepare_envelope(self, base_envelope, offset, count, outside_search, outside_offset,
                          model_code=None):
        """Build one paginated request envelope"""
        q = json.loads(json.dumps(base_envelope))
        inner = q.setdefault('query', {})
        if BUYER_SEARCH:
            inner.pop('paymentRange', None)
            inner.pop('PaymentType', None)
        self._apply_year_filter(inner)
        inner['Odometer'] = f"0,{MAX_MILEAGE}"
        if model_code:
            inner['model'] = model_code
        if TARGET_COORDS is not None:
            lat, lng = TARGET_COORDS
            inner['lng'] = lng
            inner['lat'] = lat
        else:
            inner.pop('lng', None)
            inner.pop('lat', None)
        inner['zip'] = str(self.zip_code)
        inner['range'] = self.search_range
        inner['region'] = TESLA_REGION
        inner['arrangeby'] = inner.get('arrangeby') or 'Price'
        inner['order'] = inner.get('order') or 'asc'
        inner.pop('price', None)  # Tesla API rejects a price field (HTTP 412)
        q['offset'] = offset
        q['outsideOffset'] = outside_offset
        q['outsideSearch'] = outside_search
        q['count'] = count
        return q

    @staticmethod
    def _parse_query_from_url(api_url):
        parsed = urllib.parse.urlparse(api_url)
        qs = urllib.parse.parse_qs(parsed.query)
        if 'query' not in qs:
            return None
        return json.loads(qs['query'][0])

    def _inventory_page_url(self, model_code='my', query_extra=''):
        """Inventory list URL (price asc, zip, range, optional filters)."""
        base = (
            f"https://www.tesla.com/inventory/used/{model_code}"
            f"?arrangeby=plh&zip={self.zip_code}&range={self.search_range}"
        )
        extra = (query_extra or '').strip().lstrip('&')
        return f"{base}&{extra}" if extra else base

    def _fallback_query_obj(self, model_code='my'):
        query = {
                "model": model_code,
                "condition": "used",
                "options": {},
                "arrangeby": "Price",
                "order": "asc",
                "market": "US",
                "language": "en",
                "super_region": "north america",
                "PaymentType": "finance",
                "paymentRange": "350,900",
                "Odometer": f"0,{MAX_MILEAGE}",
                "zip": str(self.zip_code),
                "range": self.search_range,
                "region": TESLA_REGION,
            }
        if TARGET_COORDS is not None:
            query['lat'], query['lng'] = TARGET_COORDS
        return {
            "query": {
                **query,
            },
            "offset": 0,
            "count": self.PAGE_SIZE,
            "outsideOffset": 0,
            "outsideSearch": False,
            "isFalconDeliverySelectionEnabled": False,
            "version": None,
        }

    async def _load_inventory_session(self, page, load_url, captured_urls, model_code='my'):
        """Load inventory page and capture API template."""
        captured_urls.clear()
        await page.goto(load_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(10000)

        content = await page.content()
        if "Access Denied" in content:
            print(" [warn] CDP load failed: Access Denied.")
            raise TeslaFetchBlocked(403, 'Access Denied', kind='akamai')

        if captured_urls:
            print(f" [ok] Session ready, captured {len(captured_urls)} API template(s).")
            captured = self._parse_query_from_url(captured_urls[0])
            if captured:
                return captured

        print(" [warn] No API template intercepted; using built-in query.")
        return self._fallback_query_obj(model_code)

    async def _paginate_inventory(
        self, page, base_query_obj, price_cap, tier_label, all_results, seen_vins,
        model_code='my'
    ):
        """Paginate until price_cap; VIN-dedupe into all_results."""
        added = 0
        if TESLA_ALL_DELIVERABLE and TESLA_SKIP_LOCAL_POOL:
            pools = [True]
        else:
            pools = [False]
            if TESLA_ALL_DELIVERABLE:
                pools.append(True)

        for outside_search in pools:
            pool_complete = True
            pool_label = f"{tier_label}/{'outside' if outside_search else 'local'}"
            total_reported = None
            for page_idx in range(self.MAX_PAGES):
                page_offset = page_idx * self.PAGE_SIZE
                offset = 0 if outside_search else page_offset
                outside_offset = page_offset if outside_search else 0
                envelope = self._prepare_envelope(
                    base_query_obj, offset, self.PAGE_SIZE, outside_search, outside_offset,
                    model_code,
                )
                api_url = self._build_api_url(envelope)
                js = f"""async () => {{
                    const r = await fetch({json.dumps(api_url)}, {{
                        method: 'GET',
                        credentials: 'include',
                        headers: {{ 'Accept': 'application/json' }}
                    }});
                    if (!r.ok) {{
                        return {{ status: r.status, schema_valid: false, results: [], total: 0 }};
                    }}
                    const j = await r.json();
                    const schemaValid = Array.isArray(j.results)
                        && j.results.every(item => item && typeof item === 'object'
                            && !Array.isArray(item));
                    return {{
                        status: r.status,
                        schema_valid: schemaValid,
                        results: schemaValid ? j.results : [],
                        total: j.total_matches_found || 0
                    }};
                }}"""
                resp = await page.evaluate(js)
                status = resp.get('status')
                if status != 200:
                    if status == 403:
                        print(
                            f" [warn] {pool_label} page {page_idx + 1} returned 403 — "
                            f"aborting this entire scan round."
                        )
                        raise TeslaFetchBlocked(403, pool_label, kind='403')
                    print(
                        f" [warn] {pool_label} page {page_idx + 1} returned {status}; stopping this pool."
                    )
                    pool_complete = False
                    break
                if resp.get('schema_valid', True) is False:
                    print(f" [warn] {pool_label} returned an unexpected response schema.")
                    pool_complete = False
                    break

                results = resp.get('results', [])
                if not isinstance(results, list) or any(not isinstance(item, dict) for item in results):
                    print(f" [warn] {pool_label} returned malformed inventory rows.")
                    pool_complete = False
                    break
                if total_reported is None:
                    total_reported = resp.get('total', 0)

                new_in_page = 0
                for item in results:
                    vin = item.get('VIN')
                    if vin and vin not in seen_vins:
                        item['_yradar_model_code'] = model_code
                        seen_vins.add(vin)
                        all_results.append(item)
                        new_in_page += 1
                        added += 1

                total_str = total_reported if total_reported is not None else '?'
                print(
                    f" -> [{pool_label}] page {page_idx + 1} (off={page_offset}): "
                    f"{len(results)} cars (new {new_in_page}, total {len(all_results)}/{total_str})"
                )

                if results and price_cap:
                    first_price_raw = (
                        results[0].get('InventoryPrice') or results[0].get('Price')
                        or results[0].get('price')
                    )
                    if first_price_raw and float(first_price_raw) > price_cap:
                        print(
                            f" [hint] [{pool_label}] page min price ${float(first_price_raw):,.0f} "
                            f"> ${price_cap:,} (price asc); stop paging."
                        )
                        break

                if len(results) == 0:
                    break
                if total_reported and page_offset + len(results) >= total_reported:
                    break

                await asyncio.sleep(1.5)
            else:
                return added, False
            if not pool_complete:
                return added, False
        return added, True

    async def async_fetch(self):
        from playwright.async_api import async_playwright

        print(
            f"[Tesla] Starting two-pass scan of Tesla official used inventory "
            f"(models={','.join(TESLA_MODELS)}, Year>={TESLA_MIN_YEAR}, "
            f"zip={self.zip_code}, range={self.search_range})"
        )

        chrome_path = find_chrome()
        if not chrome_path:
            print(" [error] Google Chrome not found. Install Chrome, then retry.")
            return [], False

        profile_dir = os.path.join(CURRENT_DIR, "chrome_debug_profile_stealth")
        os.makedirs(profile_dir, exist_ok=True)

        if is_cdp_ready(self.CDP_PORT):
            print(f" [CDP] Closing leftover Chrome from last round (port {self.CDP_PORT})...")
            force_kill_cdp_port(self.CDP_PORT)

        chrome_proc = launch_chrome_debug(
            chrome_path, self.CDP_PORT, profile_dir,
            visible=CDP_VISIBLE, window_size='800,600',
        )
        if not await wait_for_cdp_ready(self.CDP_PORT):
            force_kill_cdp_port(self.CDP_PORT, chrome_proc)
            print(
                " [error] Chrome debug port did not come up. "
                "Close any Chrome windows using chrome_debug_profile_stealth, then retry."
            )
            return [], False

        all_results = []
        seen_vins = set()
        fetch_ok = False

        async with async_playwright() as p:
            try:
                browser = await p.chromium.connect_over_cdp(
                    cdp_base_url(self.CDP_PORT), timeout=15000
                )
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else await context.new_page()

                captured_urls = []

                async def on_response(response):
                    if "inventory-results" in response.url and response.status == 200:
                        captured_urls.append(response.url)

                page.on("response", on_response)

                for model_code in TESLA_MODELS:
                    model_seen = set()
                    model_complete = True
                    for tier in TESLA_CRAWL_TIERS:
                        label = tier['label']
                        price_cap = tier['price_cap']
                        load_url = self._inventory_page_url(
                            model_code, tier.get('query_extra') or '')
                        print(f" [tier] {model_code}/{label} | cap=${price_cap:,} | {load_url}")
                        base_query_obj = await self._load_inventory_session(
                            page, load_url, captured_urls, model_code)
                        before = set(seen_vins)
                        added, complete = await self._paginate_inventory(
                            page, base_query_obj, price_cap, label, all_results, seen_vins,
                            model_code)
                        model_seen.update(seen_vins - before)
                        model_complete = model_complete and complete
                        print(
                            f" [tier] {model_code}/{label} done, +{added}, "
                            f"complete={complete}, merged={len(all_results)}."
                        )
                    self.last_inventory_vins_by_model[model_code] = model_seen
                    self.model_fetch_ok[model_code] = model_complete

                print(f" [ok] Multi-model merge: {len(all_results)} Tesla official cars.")

                self.last_inventory_vins = {str(v).strip().upper() for v in seen_vins if v}
                fetch_ok = all(self.model_fetch_ok.values())

                # After inventory pages, wait, then open missing VINs on the order page
                complete_models = {m for m, ok in self.model_fetch_ok.items() if ok}
                await self._verify_missing_via_detail_pages(page, seen_vins, complete_models)

            except TeslaFetchBlocked as e:
                self.last_fail_reason = e.kind  # '403' | 'akamai'
                self.model_fetch_ok = {model: False for model in TESLA_MODELS}
                self.last_inventory_vins = {
                    str(v).strip().upper() for v in seen_vins if v
                }
                print(
                    f" [warn] Scan aborted ({e.kind} at {e.where}); "
                    f"remaining tiers / detail verify skipped. "
                    f"{len(all_results)} cars already fetched will still be parsed (skip sold/miss)."
                )
                fetch_ok = False
            except Exception as e:
                print(f" [warn] CDP/API fetch error: {e}")
                self.last_fail_reason = self.last_fail_reason or 'error'
                self.model_fetch_ok = {model: False for model in TESLA_MODELS}
                self.last_inventory_vins = {
                    str(v).strip().upper() for v in seen_vins if v
                }
                fetch_ok = False
            finally:
                force_kill_cdp_port(self.CDP_PORT, chrome_proc)

        self._save_live_data(all_results)
        # Parse whatever we got; monitor skips sold/miss when fetch_ok is False
        parsed = self.parse_results(all_results) if all_results else []
        return parsed, fetch_ok

    @staticmethod
    def _tier_verify_priority(vehicle):
        """Detail-verify priority: watch list > Long Range HW4 > HW4 > other."""
        rank = {'favorite': 0, 'focus': 1, 'hw4': 2, 'other': 3}
        tier = classify_tesla_tier(vehicle)
        return (rank.get(tier, 4), net_price(vehicle))

    @staticmethod
    def _normalize_model_code(value, vin=''):
        model_code = str(value or '').strip().lower().replace('-', ' ')
        if model_code in ('my', 'model y', 'y'):
            return 'my'
        if model_code in ('m3', 'model 3', '3'):
            return 'm3'
        return 'my' if str(vin or '').upper().startswith(('7SA', '5YJY')) else 'm3'

    def _order_url(self, vin, model_code):
        model_code = self._normalize_model_code(model_code, vin)
        return (
            f"https://www.tesla.com/{model_code}/order/{vin}"
            f"?titleStatus=used&postal={self.zip_code}"
        )

    @staticmethod
    def _classify_order_page(final_url, page_text, vin):
        """
        Returns: 'present' | 'sold' | 'ambiguous'

        Observed:
        - Still listed: stays on /my/order/{VIN} or /m3/order/{VIN}
        - Gone: redirects to /inventory/new/my?zip=... or another /inventory/... list
        """
        vin_u = (vin or '').strip().upper()
        url = (final_url or '').lower()
        text = page_text or ''
        text_l = text.lower()
        text_u = text.upper()
        vin_l = vin_u.lower()

        blocked = any(
            marker in text_l for marker in ('access denied', 'press & hold', 'captcha')
        )
        if blocked:
            return 'ambiguous'

        order_paths = (f'/my/order/{vin_l}', f'/m3/order/{vin_l}')
        on_order_path = bool(vin_l) and any(path in url for path in order_paths)
        # Delist redirect: left the order page, landed on inventory (new or used)
        on_inventory_list = ('/inventory/' in url or url.rstrip('/').endswith('/inventory')) and (
            '/my/order/' not in url and '/m3/order/' not in url
        )

        if on_inventory_list:
            return 'sold'

        if on_order_path:
            has_vin_body = bool(vin_u) and vin_u in text_u
            has_order_signals = any(
                k in text or k.lower() in text_l
                for k in (
                    'InventoryPrice', 'PurchasePrice', 'Place Order', 'Order Card',
                    'Cash Price', '#overview',
                )
            )
            if has_vin_body or has_order_signals:
                return 'present'
            # Still on the order path: treat as present (body may not have finished loading)
            return 'present'

        # Unknown landing page: be conservative
        return 'ambiguous'

    async def _verify_missing_via_detail_pages(self, page, seen_vins, complete_models=None):
        """Open the model-specific order page for VINs missing from this snapshot."""
        from database import get_active_vehicles_by_source

        seen = {str(v).strip().upper() for v in (seen_vins or set()) if v}
        active_rows = get_active_vehicles_by_source('Tesla Official')
        complete_models = set(complete_models or ())
        model_names = {'my': 'Model Y', 'm3': 'Model 3'}
        allowed_names = {model_names[m] for m in complete_models if m in model_names}
        missing = []
        for row in active_rows:
            if row.get('model') not in allowed_names:
                continue
            vin = (row.get('vin') or '').strip().upper()
            if not vin or vin in seen:
                continue
            missing.append(row)

        if not missing:
            print(" [detail] No missing in-stock VINs; skip.")
            return

        missing.sort(key=self._tier_verify_priority)
        print(
            f" [detail] {len(missing)} in-stock VINs missing from this snapshot; "
            f"wait {TESLA_DETAIL_VERIFY_PAUSE_SECS}s, then check "
            f"({TESLA_DETAIL_VERIFY_DELAY_SECS}s between cars)..."
        )
        await asyncio.sleep(TESLA_DETAIL_VERIFY_PAUSE_SECS)

        for row in missing:
            vin = (row.get('vin') or '').strip().upper()
            model_code = self._normalize_model_code(
                row.get('model_code') or row.get('model'), vin)
            order_url = self._order_url(vin, model_code)
            try:
                await page.goto(order_url, wait_until='domcontentloaded', timeout=45000)
                await page.wait_for_timeout(2000)
                final_url = page.url or ''
                content = await page.content()
                verdict = self._classify_order_page(final_url, content, vin)
                if verdict == 'present':
                    self.detail_confirmed_present.add(vin)
                    print(f" [detail] {vin[-8:]} → still listed (false miss; keep)")
                elif verdict == 'sold':
                    self.detail_confirmed_sold.add(vin)
                    print(f" [detail] {vin[-8:]} → confirmed gone (redirected / not for sale)")
                else:
                    self.detail_verify_ambiguous.add(vin)
                    print(f" [detail] {vin[-8:]} → unclear (do not delist) url={final_url[:80]}")
            except Exception as e:
                self.detail_verify_ambiguous.add(vin)
                print(f" [detail] {vin[-8:]} → unclear (error: {e})")
            await asyncio.sleep(TESLA_DETAIL_VERIFY_DELAY_SECS)

        print(
            f" [detail] done: still listed {len(self.detail_confirmed_present)} | "
            f"gone {len(self.detail_confirmed_sold)} | "
            f"unclear {len(self.detail_verify_ambiguous)} | "
            f"skipped {len(self.detail_verify_skipped)}"
        )

    def _save_live_data(self, results):
        try:
            scratch_dir = os.path.join(CURRENT_DIR, 'scratch')
            os.makedirs(scratch_dir, exist_ok=True)
            live_path = os.path.join(scratch_dir, 'tesla_live_data.json')
            with open(live_path, 'w', encoding='utf-8') as f:
                json.dump({'results': results}, f, indent=2)
        except Exception as e:
            print(f" [warn] Could not backup Tesla live JSON: {e}")

    def parse_results(self, raw_results):
        parsed_vehicles = []
        stats = {
            'no_vin': 0, 'no_price': 0, 'no_year': 0,
            'year_low': 0, 'mileage_high': 0, 'price_high': 0, 'ok': 0,
        }

        fee_stats = {'with_fee': 0, 'zero_fee': 0, 'unknown': 0}
        for r in raw_results:
            try:
                vin = r.get('VIN') or r.get('vin')
                if not vin:
                    stats['no_vin'] += 1
                    continue
                vin = vin.strip().upper()

                price_raw = (
                    r.get('InventoryPrice') or r.get('Price') or r.get('price')
                    or r.get('ActualPrice') or r.get('actualPrice') or r.get('TotalPrice')
                )
                if not price_raw:
                    stats['no_price'] += 1
                    continue
                price = float(price_raw)
                if not math.isfinite(price) or price <= 0:
                    stats['no_price'] += 1
                    continue

                mileage_raw = r.get('Odometer')
                if mileage_raw is None:
                    mileage_raw = r.get('odometer')
                if mileage_raw is None:
                    stats['mileage_high'] += 1
                    continue
                mileage = int(mileage_raw)
                if mileage < 0:
                    stats['mileage_high'] += 1
                    continue

                year = r.get('Year') or r.get('year')
                if not year:
                    year = model_year_from_vin(vin)

                if not year:
                    stats['no_year'] += 1
                    continue
                year = int(year)

                if year < TESLA_MIN_YEAR:
                    stats['year_low'] += 1
                    continue
                if not BUYER_SEARCH and mileage > MAX_MILEAGE:
                    stats['mileage_high'] += 1
                    continue
                if not BUYER_SEARCH and TESLA_FETCH_MAX_PRICE is not None and price > TESLA_FETCH_MAX_PRICE:
                    stats['price_high'] += 1
                    continue

                transport = self.transport_assessment(r)
                transport_fee = transport['fee']

                stats['ok'] += 1
                if transport_fee is None:
                    fee_stats['unknown'] += 1
                elif transport_fee > 0:
                    fee_stats['with_fee'] += 1
                else:
                    fee_stats['zero_fee'] += 1

                model_code = self._normalize_model_code(
                    r.get('_yradar_model_code') or r.get('Model') or r.get('model'), vin)
                model_name = 'Model Y' if model_code == 'my' else 'Model 3'

                trim_raw = r.get('TrimName') or r.get('Trim') or 'Unknown trim'
                trim_raw = trim_raw.strip()
                prefix = model_name.lower() + ' '
                if trim_raw.lower().startswith(prefix):
                    trim = trim_raw[len(prefix):].strip()
                elif trim_raw.lower() == model_name.lower():
                    trim = 'Unknown trim'
                else:
                    trim = trim_raw

                is_repaired = self._is_repaired_vehicle(r)
                vehicle_history = self._history_text(r.get('VehicleHistory'))
                if is_repaired:
                    clean_status = 'repaired'
                    condition_label = 'Repaired Pre-Owned'
                    accident_history = f"Repaired ({vehicle_history})" if vehicle_history else "Repaired Pre-Owned"
                    clean_title = "Title status not independently verified"
                elif vehicle_history.upper() in ('CLEAN', 'CLEAN_HISTORY', 'NO_ACCIDENTS'):
                    clean_status = 'clean'
                    condition_label = 'Pre-Owned'
                    accident_history = "Clean history reported by Tesla"
                    clean_title = "Title status not independently verified"
                else:
                    clean_status = 'unknown'
                    condition_label = 'Pre-Owned'
                    accident_history = "History not stated"
                    clean_title = "Title status unknown"

                city = r.get('City') or ''
                state = r.get('StateProvince') or r.get('state') or ''
                metro = r.get('MetroName') or ''
                if metro and ',' in metro:
                    dealer_address = metro.strip()
                elif city:
                    dealer_address = f"{city.strip()}, {state}"
                else:
                    dealer_address = state or 'Location unavailable'

                est_range = r.get('ActualRange')
                range_unit = r.get('ActualRangeUnit') or 'mi'

                url = self._order_url(vin, model_code)

                paint_codes = {
                    'PPSW': 'White', 'WHITE': 'White', 'White': 'White',
                    'PBSB': 'Black', 'BLACK': 'Black', 'Black': 'Black',
                    'PPSB': 'Blue', 'BLUE': 'Blue', 'Blue': 'Blue',
                    'PMNG': 'Midnight Silver Metallic (Gray)', 'GRAY': 'Midnight Silver Metallic (Gray)',
                    'PR01': 'Red', 'RED': 'Red', 'Red': 'Red',
                    'PN00': 'Ultra Red',
                    'PSTE': 'Stealth Grey', 'STEALTH_GREY': 'Stealth Grey',
                    'QS00': 'Quicksilver',
                }
                interior_codes = {
                    'PBTB': 'Black', 'PREMIUM_BLACK': 'Black', 'BLACK': 'Black',
                    'PWUS': 'White', 'PREMIUM_WHITE': 'White', 'WHITE': 'White',
                }

                paint_raw = r.get('PAINT') or r.get('paint') or []
                paint_code = paint_raw[0] if isinstance(paint_raw, list) and paint_raw else str(paint_raw)
                color_ext = paint_codes.get(paint_code, paint_code or 'n/a')

                interior_raw = r.get('INTERIOR') or r.get('interior') or []
                interior_code = (
                    interior_raw[0] if isinstance(interior_raw, list) and interior_raw else str(interior_raw)
                )
                color_int = interior_codes.get(interior_code, interior_code or 'n/a')

                vehicle = {
                    'vin': vin,
                    'year': year,
                    'model': model_name,
                    'model_code': model_code,
                    'trim': trim,
                    'price': price,
                    'mileage': mileage,
                    'url': url,
                    'dealer_name': 'Tesla Inc.',
                    'dealer_address': dealer_address,
                    'source': 'Tesla Official',
                    'color_ext': color_ext,
                    'color_int': color_int,
                    'accident_history': accident_history,
                    'clean_title': clean_title,
                    'clean_status': clean_status,
                    'is_repaired': is_repaired,
                    'condition_label': condition_label,
                    'est_range': int(est_range) if est_range else None,
                    'range_unit': range_unit,
                    'transport_fee': transport_fee,
                    'transport_fee_status': transport['status'],
                    'transport_fee_source': transport['source'],
                    'distance_miles': transport['distance_miles'],
                    'distance_source': (
                        'inventory_coordinates' if transport['distance_miles'] is not None
                        else 'unknown'
                    ),
                    'wheel_inches': self._parse_wheel_inches(r),
                    'seat_count': self._parse_seat_count(r),
                    'raw_record': r,
                }
                hw4 = assess_hw4(vin, model_code, r)
                matrix = assess_matrix_headlights(vin, model_code, r)
                vehicle.update({
                    'hw4_status': hw4['status'],
                    'hw4_source': hw4['source'],
                    'hw4_reason': hw4['reason'],
                    'matrix_status': matrix['status'],
                    'matrix_source': matrix['source'],
                    'matrix_reason': matrix['reason'],
                })
                if not should_store_tesla(vehicle):
                    stats['tier_filtered'] = stats.get('tier_filtered', 0) + 1
                    continue
                parsed_vehicles.append(vehicle)
            except Exception:
                pass

        tier_filtered = stats.get('tier_filtered', 0)
        print(
            f" [Tesla] filter stats: raw {len(raw_results)} -> kept {len(parsed_vehicles)} cars "
            f"(hard cap sticker ${TESLA_FETCH_MAX_PRICE:,}; tier-filtered {tier_filtered}) | "
            f"skip: no VIN={stats['no_vin']}, no price={stats['no_price']}, no year={stats['no_year']}, "
            f"year low={stats['year_low']}, mileage high={stats['mileage_high']}, sticker high={stats['price_high']} | "
            f"transport: with fee={fee_stats['with_fee']}, zero fee={fee_stats['zero_fee']}, "
            f"unknown={fee_stats['unknown']}"
        )
        return parsed_vehicles

    @staticmethod
    def _parse_wheel_inches(record):
        """Parse wheel diameter (inches) from WHEELS / option codes."""
        mapping = {
            'NINETEEN': 19,
            'NINETEEN_INCH': 19,
            'TWENTY': 20,
            'TWENTY_INCH': 20,
            'TWENTY_ONE': 21,
            'TWENTYONE': 21,
            'TWENTY_ONE_INCH': 21,
        }
        wheels = record.get('WHEELS') or record.get('wheels') or []
        if isinstance(wheels, str):
            wheels = [wheels]
        for w in wheels:
            key = str(w or '').upper().replace(' ', '_').replace('-', '_')
            if key in mapping:
                return mapping[key]
        # OptionCodeData / description fallback
        for o in record.get('OptionCodeData') or []:
            if not isinstance(o, dict):
                continue
            if str(o.get('group') or '').upper() != 'WHEELS':
                continue
            text = ' '.join([
                str(o.get('name') or ''),
                str(o.get('description') or ''),
                str(o.get('long_name') or ''),
                str(o.get('code') or ''),
            ])
            m = re.search(r'(\d{2})\s*[\'’″"”]', text)
            if m:
                return int(m.group(1))
            m = re.search(r'WY(\d{2})', text.upper())
            if m:
                return int(m.group(1))
        # OptionCodeList e.g. $WY20P
        code_list = record.get('OptionCodeList') or ''
        m = re.search(r'\$?WY(\d{2})', str(code_list).upper())
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _parse_seat_count(record):
        """Parse seat count from CABIN_CONFIG / STY5S / STY7S."""
        cabin = record.get('CABIN_CONFIG') or record.get('CabinConfig') or []
        if isinstance(cabin, str):
            cabin = [cabin]
        for c in cabin:
            u = str(c or '').upper()
            if 'SEVEN' in u or u in ('7', '7SEAT', '7_SEAT'):
                return 7
            if 'FIVE' in u or u in ('5', '5SEAT', '5_SEAT'):
                return 5
        code_list = str(record.get('OptionCodeList') or '').upper()
        if 'STY7S' in code_list or 'STY7' in code_list:
            return 7
        if 'STY5S' in code_list or 'STY5' in code_list:
            return 5
        for o in record.get('OptionCodeData') or []:
            if not isinstance(o, dict):
                continue
            code = str(o.get('code') or '').upper().lstrip('$')
            text = ' '.join([
                str(o.get('name') or ''),
                str(o.get('description') or ''),
                str(o.get('long_name') or ''),
            ])
            if code.startswith('STY7') or 'Seven Seat' in text or '7 Seat' in text:
                return 7
            if code.startswith('STY5') or 'Five Seat' in text or '5 Seat' in text:
                return 5
        return None

    @staticmethod
    def _is_repaired_vehicle(r):
        """Detect Repaired Pre-Owned (API has no literal Repaired field)"""
        history = TeslaFetcher._history_text(r.get('VehicleHistory')).upper()
        if 'ACCIDENT' in history or 'REPAIR' in history:
            return True
        if r.get('DamageDisclosure') is True:
            return True
        if r.get('HasDamagePhotos') is True:
            return True
        return False

    @staticmethod
    def _history_text(value):
        """Normalize Tesla history fields that may be scalar, list, or object."""
        if value is None:
            return ''
        if isinstance(value, dict):
            return ' '.join(
                str(item).strip() for item in value.values() if item is not None
            ).strip()
        if isinstance(value, (list, tuple, set)):
            return ' '.join(
                TeslaFetcher._history_text(item) for item in value if item is not None
            ).strip()
        return str(value).strip()
