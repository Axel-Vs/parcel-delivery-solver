"""
Route-based solution representation for VRP metaheuristics.
Compact representation using routes instead of time-expanded binary tensors.
"""

import numpy as np
import copy
import pandas as pd
from datetime import datetime, timedelta


class RouteSolution:
    """
    Represents a VRP solution as a list of routes.
    Each route is a sequence: [depot, vendor1, vendor2, ..., depot]
    
    This is much more efficient than x[k, i, t1, j, t2] for metaheuristics:
    - Compact: O(n) vs O(k × n² × T²)
    - Always feasible structure
    - Natural for local search operators
    """
    
    def __init__(self, routes, vendors_df, distance_matrix, time_matrix, 
                 capacity_matrix, loading_matrix, max_capacity_kg, max_volume, max_linear_length,
                 discretization_constant, min_date, max_driving_hours=None,
                 service_time_matrix=None, evaluation_period=None,
                 allowed_early_hours=12, allowed_late_hours=12):
        """
        Initialize route solution.
        
        Args:
            routes: List of routes [[0, 2, 5, 0], [0, 1, 4, 0], ...]
            vendors_df: DataFrame with vendor information
            distance_matrix: Distance matrix [km]
            time_matrix: Time matrix [seconds]
            capacity_matrix: Cargo weight per vendor [kg]
            loading_matrix: Loading volume per vendor [m³]
            max_capacity_kg: Max weight capacity per vehicle [kg]
            max_volume: Max volume capacity per vehicle [m³]
            max_linear_length: Max linear length capacity per vehicle [m]
            discretization_constant: Time discretization [hours]
            min_date: Minimum simulation date
            service_time_matrix: Service time per vendor [minutes]
            evaluation_period: Tuple [period_start, period_end] for time window calculation
        """
        self.routes = routes
        self.vendors_df = vendors_df
        self.distance_matrix = distance_matrix
        self.time_matrix = time_matrix
        self.capacity_matrix = capacity_matrix
        self.loading_matrix = loading_matrix
        self.service_time_matrix = service_time_matrix if service_time_matrix is not None else np.zeros(len(distance_matrix))
        self.max_capacity_kg = max_capacity_kg
        self.max_volume = max_volume
        self.max_linear_length = max_linear_length
        self.discretization_constant = discretization_constant
        self.min_date = min_date
        self.max_driving_hours = max_driving_hours
        self.evaluation_period = evaluation_period
        self.allowed_early_hours = allowed_early_hours
        self.allowed_late_hours = allowed_late_hours
        self.debug_time_windows = False
        
        # Cache evaluation results
        self._total_distance = None
        self._total_time = None
        self._is_feasible = None
        self._constraint_violations = []
        
    def evaluate(self):
        """
        Calculate objective function: total distance + penalties for violations.
        
        Returns:
            float: Total cost (distance + penalties)
        """
        if self._total_distance is not None:
            return self._total_distance
        
        total_distance = 0
        total_time = 0
        
        for route in self.routes:
            for i in range(len(route) - 1):
                from_node = route[i]
                to_node = route[i + 1]
                total_distance += self.distance_matrix[from_node][to_node]
                total_time += self.time_matrix[from_node][to_node]
        
        self._total_distance = total_distance
        self._total_time = total_time
        
        return total_distance
    
    def is_feasible(self, check_all=True):
        """
        Check if solution satisfies all constraints.
        
        Args:
            check_all: If True, check all constraints. If False, stop at first violation.
            
        Returns:
            bool: True if feasible, False otherwise
        """
        if self._is_feasible is not None and not check_all:
            return self._is_feasible
        
        self._constraint_violations = []
        
        # Check 1: All vendors visited exactly once
        visited_vendors = set()
        for route in self.routes:
            for node in route:
                if node != 0:  # Not depot
                    if node in visited_vendors:
                        self._constraint_violations.append(f"Vendor {node} visited multiple times")
                        if not check_all:
                            self._is_feasible = False
                            return False
                    visited_vendors.add(node)
        
        num_vendors = len(self.capacity_matrix) - 1  # Exclude depot
        if len(visited_vendors) != num_vendors:
            missing = set(range(1, num_vendors + 1)) - visited_vendors
            self._constraint_violations.append(f"Missing vendors: {missing}")
            if not check_all:
                self._is_feasible = False
                return False
        
        # Check 2: Capacity + driving-time window constraints
        for route_idx, route in enumerate(self.routes):
            route_weight = sum(self.capacity_matrix[node] for node in route if node != 0)
            route_volume = sum(self.loading_matrix[node] for node in route if node != 0)
            
            # Calculate travel time from time_matrix (expecting seconds)
            # ONLY count segments AFTER the first (skip depot → first vendor)
            # This counts: vendor1 → vendor2, vendor2 → vendor3, ..., last_vendor → depot
            route_travel_time_seconds = sum(self.time_matrix[route[i]][route[i + 1]] for i in range(1, len(route) - 1))
            route_travel_time_hours = route_travel_time_seconds / 3600.0
            
            # Debug: Check if time values seem reasonable
            if route_idx == 0 and route_travel_time_hours > 100:
                print(f"⚠️  WARNING: Route 0 travel time suspiciously high: {route_travel_time_hours:.2f}h ({route_travel_time_seconds:.0f}s)")
                print(f"   Sample time values from matrix (excluding depot->first): {[self.time_matrix[route[i]][route[i+1]] for i in range(1, min(3, len(route)-1))]}")
            
            # Add service time: count non-depot stops and multiply by service time per stop
            num_stops = len([node for node in route if node != 0])
            if len(self.service_time_matrix) > 0:
                # Service time in minutes - convert to hours; assume service_time_matrix[i] is in minutes for vendor i
                service_time_per_stop = self.service_time_matrix[1] / 60.0 if len(self.service_time_matrix) > 1 else 0
            else:
                service_time_per_stop = 0
            
            route_service_time_hours = num_stops * service_time_per_stop
            route_time_hours = route_travel_time_hours + route_service_time_hours
            
            if route_idx < len(self.max_capacity_kg):
                if route_weight > self.max_capacity_kg[route_idx]:
                    self._constraint_violations.append(
                        f"Route {route_idx}: Weight {route_weight:.0f} > {self.max_capacity_kg[route_idx]:.0f} kg"
                    )
                    if not check_all:
                        self._is_feasible = False
                        return False
                
                if route_volume > self.max_volume[route_idx]:
                    self._constraint_violations.append(
                        f"Route {route_idx}: Volume {route_volume:.1f} > {self.max_volume[route_idx]:.1f} m³"
                    )
                    if not check_all:
                        self._is_feasible = False
                        return False

            # Driving time constraint (if provided)
            if self.max_driving_hours is not None:
                if route_time_hours > self.max_driving_hours:
                    violation_msg = f"Route {route_idx}: Total time (travel {route_travel_time_hours:.2f}h + service {route_service_time_hours:.2f}h = {route_time_hours:.2f}h) > {self.max_driving_hours:.2f}h max"
                    self._constraint_violations.append(violation_msg)
                    # Log first few violations for debugging
                    if route_idx < 3 and len(self._constraint_violations) < 5:
                        print(f"⚠️  {violation_msg}")
                    if not check_all:
                        self._is_feasible = False
                        return False
        
        # Check 3: Time windows (enforce based on evaluation period ±12 hours)
        if self.evaluation_period is not None:
            period_start = pd.to_datetime(self.evaluation_period[0])
            period_end = pd.to_datetime(self.evaluation_period[1])
            
            # Allowed buffers: 12 hours before/after vendor date
            allowed_early = timedelta(hours=float(self.allowed_early_hours))
            allowed_late = timedelta(hours=float(self.allowed_late_hours))

            def _to_naive(dt_value):
                if dt_value is None:
                    return None
                if isinstance(dt_value, pd.Timestamp):
                    return dt_value.tz_localize(None).to_pydatetime()
                if hasattr(dt_value, 'tzinfo') and dt_value.tzinfo is not None:
                    return dt_value.replace(tzinfo=None)
                return dt_value

            def _fmt_time(val):
                if val is None:
                    return "None"
                if isinstance(val, pd.Timestamp):
                    val = _to_naive(val)
                if isinstance(val, datetime):
                    return val.strftime("%Y-%m-%d")
                if isinstance(val, timedelta):
                    seconds = val.total_seconds()
                elif isinstance(val, (int, float, np.number)):
                    seconds = float(val)
                else:
                    return str(val)
                hours = seconds / 3600.0
                if abs(hours) >= 48:
                    return f"{hours / 24.0:.1f}d"
                return f"{hours:.1f}h"

            # DEBUG: Log time window parameters (log once per solution)
            log_time_config = self.debug_time_windows and not getattr(self, "_logged_time_window_config", False)
            if log_time_config:
                print(f"\n📋 TIME WINDOW CONFIGURATION:")
                print(f"   - allowed_early: {allowed_early} ({allowed_early.total_seconds() / 3600:.0f} hours)")
                print(f"   - allowed_late: {allowed_late} ({allowed_late.total_seconds() / 3600:.0f} hours)")
                print(f"   - Evaluation period min_date: {self.min_date}")

            # Determine depot delivery target (use LATEST vendor's requested delivery to set depot window)
            # This is critical: route only returns to depot AFTER serving all vendors
            # Depot window based on evaluation_period (NOT individual vendor dates!)
            # Routes must depart after evaluation_period start and return before evaluation_period end
            if isinstance(self.evaluation_period[0], str):
                depot_earliest = datetime.strptime(self.evaluation_period[0], '%Y-%m-%d %H:%M:%S')
            else:
                depot_earliest = _to_naive(self.evaluation_period[0])
            
            if isinstance(self.evaluation_period[1], str):
                depot_latest = datetime.strptime(self.evaluation_period[1], '%Y-%m-%d %H:%M:%S')
            else:
                depot_latest = _to_naive(self.evaluation_period[1])

            for route_idx, route in enumerate(self.routes):
                # Also enforce depot arrival relative to this route's latest requested time
                route_latest_requested = None
                depot_target_earliest = None
                depot_target_latest = None
                for node in route:
                    if node == 0:
                        continue
                    vendor_idx = node - 1
                    if vendor_idx >= len(self.vendors_df):
                        continue
                    vendor_row = self.vendors_df.iloc[vendor_idx]
                    raw_candidates = [
                        vendor_row.get('Requested Delivery', None),
                        vendor_row.get('Requested Delivery Date', None),
                    ]
                    for raw in raw_candidates:
                        parsed = pd.to_datetime(raw, errors='coerce')
                        if pd.notna(parsed):
                            parsed = _to_naive(parsed)
                            route_latest_requested = parsed if route_latest_requested is None else max(route_latest_requested, parsed)
                            break
                if route_latest_requested is not None:
                    depot_target_earliest = route_latest_requested - allowed_early
                    depot_target_latest = route_latest_requested + allowed_late
                    if log_time_config:
                        print(
                            f"🧭 Route {route_idx}: depot target window "
                            f"[{depot_target_earliest} .. {depot_target_latest}] "
                            f"(latest requested={route_latest_requested})"
                        )
                if log_time_config:
                    print(
                        f"🧭 Route {route_idx}: depot evaluation window "
                        f"[{depot_earliest} .. {depot_latest}]"
                    )
                # Initialize current_time baseline from min_date
                if isinstance(self.min_date, datetime):
                    current_time = _to_naive(self.min_date)
                elif isinstance(self.min_date, str):
                    try:
                        current_time = _to_naive(datetime.strptime(self.min_date, '%Y-%m-%d %H:%M:%S'))
                    except Exception:
                        # Skip time window checks if date format is unknown
                        continue
                else:
                    # Skip if min_date is not usable
                    continue

                # Anchor start time to the first vendor's requested window (if available)
                first_vendor_requested = None
                for node in route:
                    if node == 0:
                        continue
                    vendor_idx = node - 1
                    if vendor_idx >= len(self.vendors_df):
                        continue
                    vendor_row = self.vendors_df.iloc[vendor_idx]
                    raw_candidates = [
                        vendor_row.get('Requested Loading', None),
                        vendor_row.get('Requested Loading Date', None),
                    ]
                    for raw in raw_candidates:
                        parsed = pd.to_datetime(raw, errors='coerce')
                        if pd.notna(parsed):
                            first_vendor_requested = _to_naive(parsed)
                            break
                    if first_vendor_requested is not None:
                        break

                if first_vendor_requested is not None:
                    # Compute route duration (travel + service) to reach depot
                    route_duration_seconds = 0
                    for i in range(1, len(route)):
                        prev_node = route[i - 1]
                        node = route[i]
                        if prev_node < len(self.time_matrix) and node < len(self.time_matrix[prev_node]):
                            route_duration_seconds += self.time_matrix[prev_node][node]
                        if node != 0 and self.service_time_matrix is not None and node < len(self.service_time_matrix):
                            route_duration_seconds += self.service_time_matrix[node] * 60

                    # Vendor window for the first vendor
                    vendor_window_start = first_vendor_requested - allowed_early
                    vendor_window_end = first_vendor_requested + allowed_late

                    # Depot window (target if available, otherwise evaluation period)
                    if depot_target_earliest is not None:
                        depot_window_start = depot_target_earliest
                        depot_window_end = depot_target_latest
                    else:
                        depot_window_start = depot_earliest
                        depot_window_end = depot_latest

                    # Translate depot window into feasible start window
                    if route_duration_seconds > 0:
                        depot_start_min = depot_window_start - timedelta(seconds=route_duration_seconds)
                        depot_start_max = depot_window_end - timedelta(seconds=route_duration_seconds)
                    else:
                        depot_start_min = depot_window_start
                        depot_start_max = depot_window_end

                    # Combine windows and min_date
                    start_min = max(current_time, vendor_window_start, depot_start_min)
                    start_max = min(vendor_window_end, depot_start_max)

                    # Choose start time closest to requested loading, within feasible window
                    if start_min <= start_max:
                        if first_vendor_requested < start_min:
                            current_time = start_min
                        elif first_vendor_requested > start_max:
                            current_time = start_max
                        else:
                            current_time = first_vendor_requested
                    else:
                        # No feasible start window; fall back to earliest vendor window
                        current_time = max(current_time, vendor_window_start)

                    if log_time_config:
                        print(f"🕒 Route {route_idx}: optimized start time = {current_time}")

                if log_time_config:
                    self._logged_time_window_config = True

                for i, node in enumerate(route):
                    # Add travel time from previous node
                    if i > 0:
                        prev_node = route[i - 1]
                        if prev_node < len(self.time_matrix) and node < len(self.time_matrix[prev_node]):
                            travel_seconds = self.time_matrix[prev_node][node]
                            current_time = current_time + timedelta(seconds=travel_seconds)

                    if node == 0:
                        # Check depot time window only at END of route (return to depot)
                        if i == len(route) - 1:
                            # Arrival at depot must be within delivery window for this route.
                            # If early or late, mark infeasible.
                            if depot_target_earliest is not None:
                                if current_time < depot_target_earliest:
                                    violation_msg = (f"Route {route_idx}: depot arrival too early "
                                                   f"({_fmt_time(current_time)}; window starts {_fmt_time(depot_target_earliest)})")
                                    self._constraint_violations.append(violation_msg)
                                    if not check_all:
                                        return False
                                if current_time > depot_target_latest:
                                    violation_msg = (f"Route {route_idx}: depot arrival too late "
                                                   f"({_fmt_time(current_time)}; window ends {_fmt_time(depot_target_latest)})")
                                    self._constraint_violations.append(violation_msg)
                                    if not check_all:
                                        return False
                            else:
                                # Fallback to evaluation period if no depot delivery window is available
                                if current_time < depot_earliest:
                                    violation_msg = (f"Route {route_idx}: depot arrival too early "
                                                   f"({_fmt_time(current_time)}; window starts {_fmt_time(depot_earliest)})")
                                    self._constraint_violations.append(violation_msg)
                                    if not check_all:
                                        return False
                                if current_time > depot_latest:
                                    violation_msg = (f"Route {route_idx}: depot arrival too late "
                                                   f"({_fmt_time(current_time)}; window ends {_fmt_time(depot_latest)})")
                                    self._constraint_violations.append(violation_msg)
                                    if not check_all:
                                        return False
                        continue

                    vendor_idx = node - 1
                    if vendor_idx >= len(self.vendors_df):
                        continue

                    vendor_row = self.vendors_df.iloc[vendor_idx]
                    raw_candidates = [
                        vendor_row.get('Requested Loading', None),
                        vendor_row.get('Requested Loading Date', None),
                    ]
                    requested_dt = None
                    for raw in raw_candidates:
                        parsed = pd.to_datetime(raw, errors='coerce')
                        if pd.notna(parsed):
                            requested_dt = _to_naive(parsed)
                            break

                    if requested_dt is None:
                        # If no requested time is provided, skip window check for this vendor
                        # (but still add service time if any)
                        if self.service_time_matrix is not None and node < len(self.service_time_matrix):
                            current_time = current_time + timedelta(minutes=self.service_time_matrix[node])
                        continue

                    earliest_allowed = requested_dt - allowed_early
                    latest_allowed = requested_dt + allowed_late

                    if current_time < earliest_allowed:
                        # Wait until the earliest allowed time instead of marking infeasible
                        current_time = earliest_allowed

                    if current_time > latest_allowed:
                        days_late = (current_time - latest_allowed).days
                        hours_late = ((current_time - latest_allowed).total_seconds() / 3600) % 24
                        violation_msg = (f"Route {route_idx}: arrives at vendor {node} at {current_time} "
                                         f"after latest allowed {latest_allowed} ({days_late}d {hours_late:.0f}h late)")
                        self._constraint_violations.append(violation_msg)
                        if not check_all:
                            self._is_feasible = False
                            return False

                    # Add service time at this stop before moving on
                    if self.service_time_matrix is not None and node < len(self.service_time_matrix):
                        current_time = current_time + timedelta(minutes=self.service_time_matrix[node])
        
        self._is_feasible = len(self._constraint_violations) == 0
        return self._is_feasible
    
    def get_num_routes(self):
        """Return number of routes (vehicles used)."""
        return len(self.routes)
    
    def get_route_cost(self, route_idx):
        """Get distance cost of a specific route."""
        if route_idx >= len(self.routes):
            return 0
        
        route = self.routes[route_idx]
        distance = 0
        for i in range(len(route) - 1):
            distance += self.distance_matrix[route[i]][route[i + 1]]
        return distance
    
    def get_route_capacity_usage(self, route_idx):
        """Get capacity usage for a specific route."""
        if route_idx >= len(self.routes):
            return 0, 0
        
        route = self.routes[route_idx]
        weight = sum(self.capacity_matrix[node] for node in route if node != 0)
        volume = sum(self.loading_matrix[node] for node in route if node != 0)
        return weight, volume
    
    def copy(self):
        """Create a deep copy of this solution."""
        return RouteSolution(
            routes=copy.deepcopy(self.routes),
            vendors_df=self.vendors_df,
            distance_matrix=self.distance_matrix,
            time_matrix=self.time_matrix,
            capacity_matrix=self.capacity_matrix,
            loading_matrix=self.loading_matrix,
            service_time_matrix=self.service_time_matrix,
            max_capacity_kg=self.max_capacity_kg,
            max_volume=self.max_volume,
            max_linear_length=self.max_linear_length,
            discretization_constant=self.discretization_constant,
            min_date=self.min_date,
            max_driving_hours=self.max_driving_hours,
            evaluation_period=self.evaluation_period,
            allowed_early_hours=self.allowed_early_hours,
            allowed_late_hours=self.allowed_late_hours
        )
    
    def invalidate_cache(self):
        """Invalidate cached evaluation results after modification."""
        self._total_distance = None
        self._total_time = None
        self._is_feasible = None
        self._constraint_violations = []
    
    def __str__(self):
        """String representation of solution."""
        cost = self.evaluate()
        feasible = "✓" if self.is_feasible(check_all=False) else "✗"
        return f"RouteSolution[{len(self.routes)} routes, {cost:.0f} km, feasible: {feasible}]"
