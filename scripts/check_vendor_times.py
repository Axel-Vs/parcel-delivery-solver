#!/usr/bin/env python3
import json
from pathlib import Path

# Find the most recent run
runs_dir = Path("results/runs")
if runs_dir.exists():
    runs = sorted(runs_dir.glob("run_*"), key=lambda p: p.name, reverse=True)
    if runs:
        latest_run = runs[0]
        state_file = latest_run / "state.json"
        if state_file.exists():
            with open(state_file) as f:
                state = json.load(f)
                if 'time_distance_matrix' in state:
                    matrix = state['time_distance_matrix']
                    # Column 0 is depot, so check times from each vendor (row) to depot (col 0)
                    vendor_to_depot = [(i, matrix[i][0]) for i in range(1, len(matrix))]
                    vendor_to_depot.sort(key=lambda x: x[1], reverse=True)
                    
                    print("🔍 Vendor-to-Depot Travel Times (Top 10):")
                    print("=" * 50)
                    for rank, (vendor_id, time_seconds) in enumerate(vendor_to_depot[:10], 1):
                        hours = time_seconds / 3600
                        print(f"{rank:2d}. Vendor {vendor_id:2d}: {hours:6.2f}h ({time_seconds:8.0f}s)")
                    print("=" * 50)
                    print(f"\n✅ Longest travel time: Vendor {vendor_to_depot[0][0]} → Depot = {vendor_to_depot[0][1]/3600:.2f}h")
                else:
                    print("No time_distance_matrix in state.json")
        else:
            print(f"No state.json in {latest_run}")
    else:
        print("No runs found")
else:
    print("No results/runs directory")
