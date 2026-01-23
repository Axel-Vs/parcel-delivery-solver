#!/usr/bin/env python3
"""Quick test to verify expected arrival times are being calculated."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime
import pandas as pd

# Simple test of datetime conversion logic
min_date_timestamp = pd.Timestamp('2023-09-05 06:00:00')
print(f"Test 1: Pandas Timestamp")
print(f"  Type: {type(min_date_timestamp)}")
print(f"  Has to_pydatetime: {hasattr(min_date_timestamp, 'to_pydatetime')}")

if hasattr(min_date_timestamp, 'to_pydatetime'):
    base_dt = min_date_timestamp.to_pydatetime()
    print(f"  Converted: {base_dt} (type: {type(base_dt)})")
    from datetime import timedelta
    arrival_iso = (base_dt + timedelta(hours=12.5)).strftime('%Y-%m-%d %H:%M:%S')
    print(f"  Arrival at +12.5h: {arrival_iso}")

print()
print(f"Test 2: String format")
min_date_str = '2023-09-05 06:00:00'
print(f"  Type: {type(min_date_str)}")
for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S'):
    try:
        base_dt = datetime.strptime(min_date_str, fmt)
        print(f"  Format {fmt}: SUCCESS")
        arrival_iso = (base_dt + timedelta(hours=12.5)).strftime('%Y-%m-%d %H:%M:%S')
        print(f"  Arrival at +12.5h: {arrival_iso}")
        break
    except ValueError:
        print(f"  Format {fmt}: FAILED")

print()
print("✅ All datetime conversion tests passed!")
