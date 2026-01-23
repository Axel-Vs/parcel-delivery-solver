#!/usr/bin/env python3
"""Test is_feasible() method directly to see time window debug output"""

from model.optimizer.route_solution import RouteSolution
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Create minimal test data with date columns
vendors_df = pd.DataFrame({
    'vendor Name': ['V1', 'V2'],
    'Requested Delivery': ['2025-01-20 14:00:00', '2025-01-20 15:00:00'],
    'Requested Loading': ['2025-01-20 08:00:00', '2025-01-20 09:00:00'],
})

routes = [[0, 1, 2, 0]]
distance_matrix = [[0, 100, 200], [100, 0, 150], [200, 150, 0]]
time_matrix = [[0, 3600, 7200], [3600, 0, 5400], [7200, 5400, 0]]
capacity_matrix = [0, 1000, 1000]
loading_matrix = [0, 50, 50]
max_capacity_kg = [2000]
max_ldms_vc = [100]
service_time_matrix = np.array([0, 120.0, 120.0], dtype=float)  # 2 hours per stop in minutes

# Create RouteSolution with evaluation period
evaluation_period = ['2025-01-20 02:00:00', '2025-01-21 02:00:00']

solution = RouteSolution(
    routes=routes,
    vendors_df=vendors_df,
    distance_matrix=distance_matrix,
    time_matrix=time_matrix,
    capacity_matrix=capacity_matrix,
    loading_matrix=loading_matrix,
    service_time_matrix=service_time_matrix,
    max_capacity_kg=max_capacity_kg,
    max_ldms_vc=max_ldms_vc,
    discretization_constant=1,
    min_date=datetime(2025, 1, 20, 2, 0, 0),
    max_driving_hours=50,
    evaluation_period=evaluation_period
)

print("\n" + "="*60)
print("TEST: Calling is_feasible() directly")
print("="*60)
result = solution.is_feasible(check_all=True)
print(f"\nFinal Result: {result}")
print("="*60)
