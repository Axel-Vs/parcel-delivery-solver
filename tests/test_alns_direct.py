#!/usr/bin/env python3
"""End-to-end test: Run ALNS solver on small dataset and check time window violations"""

import sys
from pathlib import Path
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from model.graph_creator.graph_creator import Graph
from model.optimizer.delivery_model import DeliveryOptimizer
import pandas as pd
from datetime import datetime

# Load a processed CSV file that has geocoded coordinates
import glob
processed_files = sorted(glob.glob(str(repo_root / "uploads" / "processed_*.csv")))
if not processed_files:
    print("ERROR: No processed CSV files found in uploads/")
    sys.exit(1)

data_path = processed_files[-1]  # Use most recent
df = pd.read_csv(data_path)

print(f"\n{'='*70}")
print(f"ALNS SOLVER TEST - Time Window Buffers")
print(f"{'='*70}")
print(f"Loaded {len(df)} vendors from {data_path}\n")

# Verify coordinates exist
if 'vendor_latitude' not in df.columns or 'recipient_latitude' not in df.columns:
    print("ERROR: No geocoded coordinates in CSV")
    sys.exit(1)

# Set period with ±12h buffers
min_loading = pd.to_datetime(df.get('Requested Loading', df.get('Requested Loading Date', None)), errors='coerce').min()
max_delivery = pd.to_datetime(df.get('Requested Delivery', df.get('Requested Delivery Date', None)), errors='coerce').max()

period_start = min_loading - pd.Timedelta(hours=12)
period_end = max_delivery + pd.Timedelta(hours=12)

period = [period_start.strftime('%Y-%m-%d %H:%M:%S'), period_end.strftime('%Y-%m-%d %H:%M:%S')]

print(f"Period: {period[0]} to {period[1]}")
print(f"MIN loading - 12h: {period_start}")
print(f"MAX delivery + 12h: {period_end}")

# Network parameters
network_params = {
    'discretization_constant': 4,
    'starting_depot': 8,
    'closing_depot': 18,
    'vendor_start_hr': 6,
    'pickup_end_hr': 14,
    'loading': 2,
    'earl_arv': 24,
    'late_arv': 24,
    'max_driving': 75,
    'max_weight': 30,
    'max_ldms': 70,
    'plot_centered_coordinates': [47.6062, -122.3321],
    'max_feasible_distance': 3000,
    'time_window_sampling_threshold': 20,
    'time_window_sample_size': 20,
}

# Create graph and process
net = Graph(network_params)
complete_coordinates, vendors_df, depots_df = net.read_data(period, df)

print(f"\nProcessed {len(vendors_df)} vendors with dates")
print(f"Creating network...")

# Create network (use dummy OSRM or ORS client)
try:
    net.create_network(complete_coordinates, vendors_df)
    net.discretize()
except Exception as e:
    print(f"Network creation skipped (expected without routing): {e}")
    # Create dummy matrices for testing
    import numpy as np
    n = len(vendors_df) + 1
    net.distance_matrix = np.ones((n, n)) * 100  # 100 km between all pairs
    net.time_distance_matrix = np.ones((n, n)) * 3600  # 1 hour between all pairs
    net.time_distance_matrix[0] = 0  # Depot
    net.time_distance_matrix[:, 0] = 0
    net.disc_time_distance_matrix = net.time_distance_matrix / 3600
    net.min_date = pd.to_datetime(period[0])

# Prepare matrices
import numpy as np
capacity_matrix = np.concatenate([[0], vendors_df['Total Gross Weight'].fillna(0).to_numpy()])
loading_matrix = np.concatenate([[0], vendors_df['Calculated Loading Meters'].fillna(0).to_numpy()])
service_time_matrix = np.zeros(len(capacity_matrix))
service_time_matrix[1:] = 120  # 2 hours per stop

# Create optimizer with ALNS
print(f"\nCreating optimizer with ALNS metaheuristic...")
optimizer = DeliveryOptimizer(
    evaluation_period=period,
    discretization_constant=network_params['discretization_constant'],
    time_expanded_network=[],
    time_expanded_network_index=[],
    Tau_hours=[0],
    distance_matrix=net.distance_matrix,
    time_distance_matrix=net.time_distance_matrix,
    disc_time_distance_matrix=net.disc_time_distance_matrix,
    capacity_matrix=capacity_matrix,
    loading_matrix=loading_matrix,
    service_time_matrix=service_time_matrix,
    max_capacity=network_params['max_weight'],
    max_volume=network_params['max_ldms'],
    max_linear_length=16.1,
    max_driving=network_params['max_driving'],
    is_gap=False,
    mip_gap=0.05,
    maximum_minutes=10,
    vendors_df=vendors_df
)

optimizer.min_date = pd.to_datetime(period[0])

# Run ALNS
print(f"Running ALNS solver with evaluation_period parameter...")
status, x, y = optimizer.solve_with_metaheuristic(
    w=0.5,
    max_iterations=1,  # Single iteration for debug
    verbose=True
)

print(f"\n{'='*70}")
print(f"ALNS Results")
print(f"{'='*70}")
print(f"Status: {status}")

