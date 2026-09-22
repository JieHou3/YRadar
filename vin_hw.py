"""Conservative VIN and listing-evidence hardware assessments.

VIN production cutovers are useful clues, not build-sheet confirmation.  A
structured Tesla listing value or explicit page text is preferred whenever
available.  Model Y serial heuristics are never applied to Model 3.
"""
import re

VIN_YEAR = {'N': 2022, 'P': 2023, 'R': 2024, 'S': 2025, 'T': 2026, 'V': 2027}
MATRIX_LIGHT_PA_SERIAL_MIN = 160000
VIN_RE = re.compile(r'^[A-HJ-NPR-Z0-9]{17}$')


def is_valid_vin(vin):
    return bool(VIN_RE.fullmatch(str(vin or '').strip().upper()))


def vin_last8(vin):
    vin = str(vin or '').strip().upper()
    return vin[9:17] if len(vin) >= 17 else (vin[-8:] if len(vin) >= 8 else vin)


def vin_year_code(vin):
    vin = str(vin or '').strip().upper()
    return vin[9] if is_valid_vin(vin) else ''


def model_year_from_vin(vin):
    return VIN_YEAR.get(vin_year_code(vin))


def _result(status, source, reason):
    return {'status': status, 'source': source, 'reason': reason}


def _evidence_text(record):
    if not record:
        return ''
    pieces = []
    for key in ('Hardware', 'AutopilotHardware', 'InfotainmentHardware',
                'Headlights', 'Lighting', 'MatrixHeadlights'):
        value = record.get(key)
        if value is not None:
            pieces.append(f'{key}: {value}')
    for option in record.get('OptionCodeData') or []:
        if not isinstance(option, dict):
            continue
        pieces.extend(str(option.get(k) or '') for k in
                      ('name', 'description', 'long_name'))
    return ' '.join(pieces).lower()


def _explicit_bool(record, keys):
    if not record:
        return None
    seen = []
    for key in keys:
        if key not in record:
            continue
        value = record.get(key)
        if isinstance(value, bool):
            seen.append(value)
        elif str(value).strip().lower() in ('true', 'yes', '1', 'present', 'equipped'):
            seen.append(True)
        elif str(value).strip().lower() in ('false', 'no', '0', 'absent', 'unavailable'):
            seen.append(False)
    return seen[0] if seen and all(v == seen[0] for v in seen) else None


def assess_hw4(vin, model='my', record=None):
    """Return status/source/reason; only explicit listing evidence confirms HW4."""
    text = _evidence_text(record)
    has_hw4 = bool(re.search(r'\b(hw ?4|hardware ?4|ai4)\b', text))
    has_hw3 = bool(re.search(r'\b(hw ?3|hardware ?3|ai3)\b', text))
    if has_hw4 and has_hw3:
        return _result('unknown', 'tesla_listing', 'Listing contains conflicting HW3 and HW4 evidence.')
    if has_hw4:
        return _result('confirmed', 'tesla_listing', 'Tesla listing explicitly identifies HW4/AI4.')
    if has_hw3:
        return _result('unlikely', 'tesla_listing', 'Tesla listing explicitly identifies HW3/AI3.')

    if not is_valid_vin(vin):
        return _result('unknown', 'vin', 'A complete, valid 17-character VIN is required.')
    year = model_year_from_vin(vin)
    model_code = str(model or '').lower().replace('model ', 'm').replace(' ', '')
    if year is None:
        return _result('unknown', 'vin', 'VIN model year could not be decoded.')
    if year >= 2025:
        return _result('likely', 'vin_model_year',
                       f'{year} vehicles are expected to use HW4; verify the build in person.')
    if model_code not in ('my', 'y', 'modely'):
        return _result('unknown', 'vin',
                       'No Model 3 production cutoff is inferred from Model Y VIN rules.')
    if year >= 2024:
        return _result('likely', 'vin_model_year',
                       f'{year} Model Y is likely HW4; VIN alone is not confirmation.')
    if year != 2023:
        return _result('unlikely', 'vin_model_year',
                       f'{year} Model Y predates the common HW4 transition.')

    last8 = vin_last8(vin)
    serial_text = last8[-6:]
    if len(last8) == 8 and serial_text.isdigit():
        serial = int(serial_text)
        if last8.startswith('PF'):
            cutoff = 789500
        elif last8.startswith('PA'):
            cutoff = 131200
        else:
            cutoff = None
        if cutoff is not None:
            status = 'likely' if serial > cutoff else 'unlikely'
            relation = 'after' if serial > cutoff else 'before'
            return _result(status, 'vin_production_heuristic',
                           f'{last8} is {relation} an observed Model Y transition; verify physically.')
    return _result('unknown', 'vin', 'VIN does not support a known Model Y production heuristic.')


def assess_matrix_headlights(vin, model='my', record=None):
    """Assess matrix headlights without presenting VIN heuristics as facts."""
    explicit = _explicit_bool(record, ('MatrixHeadlights', 'HasMatrixHeadlights'))
    if explicit is True:
        return _result('confirmed', 'tesla_listing',
                       'Tesla listing explicitly identifies matrix headlights.')
    if explicit is False:
        return _result('unlikely', 'tesla_listing',
                       'Tesla listing explicitly says matrix headlights are absent.')
    text = _evidence_text(record)
    positive = bool(re.search(r'\b(matrix headlights?|matrix led)\b', text))
    negative = bool(re.search(r'\b(no|without|not equipped with) matrix headlights?\b', text))
    if negative:
        return _result('unlikely', 'tesla_listing',
                       'Tesla listing text says matrix headlights are absent.')
    if positive:
        return _result('confirmed', 'tesla_listing',
                       'Tesla listing explicitly describes matrix headlights.')
    if not is_valid_vin(vin):
        return _result('unknown', 'vin', 'A complete, valid 17-character VIN is required.')
    year = model_year_from_vin(vin)
    model_code = str(model or '').lower().replace('model ', 'm').replace(' ', '')
    if year is None:
        return _result('unknown', 'vin', 'VIN model year could not be decoded.')
    if year >= 2025:
        return _result('likely', 'vin_model_year',
                       f'{year} vehicles are expected to have matrix-capable headlights; verify.')
    if model_code not in ('my', 'y', 'modely'):
        return _result('unknown', 'vin',
                       'Model Y serial heuristics are not applied to Model 3.')
    last8 = vin_last8(vin)
    serial_text = last8[-6:]
    if year == 2023 and len(last8) == 8 and serial_text.isdigit():
        serial = int(serial_text)
        if last8.startswith('PF'):
            return _result('likely', 'vin_production_heuristic',
                           '2023 Fremont Model Y is likely matrix-equipped; verify.')
        if last8.startswith('PA'):
            if serial < MATRIX_LIGHT_PA_SERIAL_MIN:
                return _result(
                    'unknown', 'vin_mixed_production',
                    'Early Austin Model Y production was mixed. The original author observed roughly '
                    '20% with matrix lights; this unvalidated reference is not a statistical probability.')
            return _result('likely', 'vin_production_heuristic',
                           'Later Austin Model Y is likely matrix-equipped; verify.')
    if year >= 2024:
        return _result('likely', 'vin_model_year',
                       f'{year} Model Y is likely matrix-equipped; VIN alone is not confirmation.')
    return _result('unknown', 'vin', 'Matrix equipment cannot be determined from this VIN.')


def is_hw4_vehicle(vin, model='my', record=None):
    return assess_hw4(vin, model, record)['status'] in ('confirmed', 'likely')


def hw4_judgment(vin, model='my', record=None):
    result = assess_hw4(vin, model, record)
    return result['status'] in ('confirmed', 'likely'), result['reason']


def hw4_push_reason(vin, model='my', record=None):
    result = assess_hw4(vin, model, record)
    return f"{result['status'].title()}: {result['reason']}"


def vin_review_suffix(vin):
    return vin_last8(vin)


def matrix_headlight_status(vin, model='my', record=None):
    return assess_matrix_headlights(vin, model, record)['status']


def lacks_matrix_headlights(vin, model='my', record=None):
    return assess_matrix_headlights(vin, model, record)['status'] == 'unlikely'
