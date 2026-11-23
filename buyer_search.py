"""Buyer-defined desktop search shared by the preview, results and alert worker."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
import re


@dataclass(frozen=True)
class BuyerSearch:
    model: str = 'Both models'
    zip_code: str = '92092'
    region: str = 'CA'
    budget: int = 38000
    mileage: int = 75000
    year: int = 2023
    interior: str = 'Any'
    awd: bool = False
    clean: bool = False
    include_fee: bool = False
    allow_unknown: bool = True
    interval: int = 15
    lat: str = ''
    lng: str = ''

    def __post_init__(self):
        if self.model not in ('Both models', 'Model Y', 'Model 3') or self.interior not in ('Any', 'Black', 'White'):
            raise ValueError('Choose a listed model and interior.')
        if not re.fullmatch(r'[0-9]{5}', self.zip_code) or not re.fullmatch(r'[A-Z]{2}', self.region):
            raise ValueError('Enter a five-digit US ZIP and a two-letter state.')
        for name, low, high in (('budget', 1, 1000000), ('mileage', 1, 1000000),
                                ('year', 2017, 2099), ('interval', 1, 1440)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'{name.title()} must be from {low:,} to {high:,}.')
        if bool(self.lat) != bool(self.lng):
            raise ValueError('Enter both coordinates or leave both blank.')
        if self.lat and not (-90 <= float(self.lat) <= 90 and -180 <= float(self.lng) <= 180):
            raise ValueError('Enter valid latitude and longitude.')

    def match(self, v):
        if self.model != 'Both models' and v.get('model') != self.model:
            return False
        if (v.get('year') or 0) < self.year or v.get('mileage') is None or v['mileage'] > self.mileage:
            return False
        trim = (v.get('trim') or '').lower().replace('-', ' ')
        if self.awd and 'awd' not in trim and 'all wheel drive' not in trim:
            return False
        if self.clean and v.get('clean_status') != 'clean':
            return False
        if self.interior != 'Any' and self.interior.lower() not in (v.get('color_int') or '').lower():
            return False
        price = v.get('price')
        fee = v.get('transport_fee')
        if not price or not math.isfinite(price) or price > self.budget:
            return False
        if not self.include_fee:
            return True
        if fee is None:
            return self.allow_unknown
        return price + fee <= self.budget

    def encoded(self):
        return json.dumps(asdict(self), sort_keys=True)

    def state_key(self):
        criteria = asdict(self)
        del criteria['interval']
        return sha256(json.dumps(criteria, sort_keys=True).encode()).hexdigest()[:24]

    def environment(self):
        codes = {'Both models': 'my,m3', 'Model Y': 'my', 'Model 3': 'm3'}
        return {'YRADAR_BUYER_SEARCH': self.encoded(), 'TESLA_MODELS': codes[self.model],
                'TESLA_ZIP': self.zip_code, 'TESLA_REGION': self.region,
                'TARGET_LAT': self.lat, 'TARGET_LNG': self.lng,
                'TESLA_MIN_YEAR': str(self.year), 'MAX_MILEAGE': str(self.mileage),
                'TESLA_INTERVAL_MINS': str(self.interval),
                'INCLUDE_TRANSPORT_IN_SORT': 'true' if self.include_fee else 'false',
                'PRICE_DISPLAY_MODE': 'total' if self.include_fee else 'net_plus_fee',
                'PUSH_UNKNOWN_TRANSPORT': 'true' if (self.allow_unknown or not self.include_fee) else 'false'}
