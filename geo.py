"""Approximate straight-line city distance; never a driving-distance claim."""
import math
import re
from config import TARGET_COORDS, NEARBY_RADIUS
from city_coordinates import CITY_COORDINATES

EARTH_RADIUS_MI = 3958.8


def haversine_miles(lat1, lon1, lat2, lon2):
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    a = min(1.0, max(0.0, a))
    return EARTH_RADIUS_MI * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def extract_city_from_address(address):
    if not address:
        return ''
    text = str(address).strip()
    # Only Southern California city centroids ship with the package.
    state = re.search(r",\s*([A-Z]{2})(?:\s+\d{5})?\s*$", text)
    if state and state.group(1) != 'CA':
        return ''
    parts = [part.strip() for part in text.split(',')]
    if len(parts) >= 3:
        return parts[-2]
    if len(parts) == 2:
        return parts[0]
    return re.sub(r'\s+CA\s*\d*$', '', text, flags=re.I).strip()


def city_coords(city_name):
    if not city_name:
        return None
    # Exact matching avoids assigning Ontario, OR or unrelated substrings to CA.
    key = ' '.join(str(city_name).lower().split())
    return CITY_COORDINATES.get(key)


def calculate_distance(address, target_coords=None):
    """Return (straight-line miles, is_nearby), or (None, False)."""
    origin = TARGET_COORDS if target_coords is None else target_coords
    if origin is None:
        return None, False
    coords = city_coords(extract_city_from_address(address))
    if coords is None:
        return None, False
    dist = haversine_miles(coords[0], coords[1], origin[0], origin[1])
    return dist, dist <= NEARBY_RADIUS
