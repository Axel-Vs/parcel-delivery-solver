from typing import Dict, Any, List, Tuple
import pandas as pd

_LAT_CANDIDATES = [
    'vendor_latitude', 'Vendor Latitude', 'Latitude', 'vendor_lat', 'lat'
]
_LON_CANDIDATES = [
    'vendor_longitude', 'Vendor Longitude', 'Longitude', 'vendor_lon', 'lon'
]


def _parse_coord(val):
    if val is None:
        return None
    try:
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).strip()
        if s == '' or s.lower() in {'nan', 'none', 'null'}:
            return None
        # Support comma-decimal
        if s.count(',') == 1 and s.count('.') == 0:
            s = s.replace(',', '.')
        s = s.replace(' ', '')
        return float(s)
    except Exception:
        return None


def _first_present(row: pd.Series, candidates: List[str]):
    for c in candidates:
        if c in row and row[c] is not None and str(row[c]).strip() != '':
            return row[c]
    return None


def _looks_swapped(lat: float, lon: float) -> bool:
    if lat is None or lon is None:
        return False
    # Latitude must be within [-90, 90], longitude within [-180, 180]
    # Typical swap symptoms: |lat| > 90 while |lon| <= 90, or lon wildly out of range
    if abs(lat) > 90 and abs(lon) <= 90:
        return True
    if abs(lon) > 180 and abs(lat) <= 180:
        return True
    return False


def validate_coordinates(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Validate vendor coordinates in a DataFrame.

    Returns a tuple of (issues_df, summary_dict)
    - issues_df: rows with problems and diagnostic columns
    - summary_dict: counts per issue type and totals
    """
    records: List[Dict[str, Any]] = []

    for idx, row in df.iterrows():
        lat_raw = _first_present(row, _LAT_CANDIDATES)
        lon_raw = _first_present(row, _LON_CANDIDATES)
        lat = _parse_coord(lat_raw)
        lon = _parse_coord(lon_raw)

        issues: List[str] = []
        flags: Dict[str, Any] = {}

        if lat is None or lon is None:
            issues.append('missing_coordinate')
        else:
            if not (-90 <= lat <= 90):
                issues.append('lat_out_of_range')
            if not (-180 <= lon <= 180):
                issues.append('lon_out_of_range')
            if lat == 0 and lon == 0:
                issues.append('zero_zero_coordinate')
            if _looks_swapped(lat, lon):
                issues.append('likely_swapped')
                # Propose a fix
                flags['suggested_lat'] = lon
                flags['suggested_lon'] = lat

        if issues:
            rec = {
                'index': idx,
                'vendor_name': row.get('vendor Name') or row.get('Vendor Name') or '',
                'city': row.get('Vendor City') or row.get('city') or '',
                'lat_raw': lat_raw,
                'lon_raw': lon_raw,
                'lat_parsed': lat,
                'lon_parsed': lon,
                'issues': ';'.join(issues)
            }
            rec.update(flags)
            records.append(rec)

    issues_df = pd.DataFrame.from_records(records)

    summary = {
        'total_rows': int(len(df)),
        'total_issues': int(len(issues_df)),
        'by_issue': {}
    }
    if not issues_df.empty:
        for issue in ['missing_coordinate', 'lat_out_of_range', 'lon_out_of_range', 'zero_zero_coordinate', 'likely_swapped']:
            summary['by_issue'][issue] = int((issues_df['issues'].str.contains(issue)).sum())

    return issues_df, summary
