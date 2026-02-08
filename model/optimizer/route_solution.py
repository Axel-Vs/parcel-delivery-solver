"""
Route-based solution representation for VRP metaheuristics.
Compact representation using routes.
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
                 capacity_matrix, loading_matrix, linear_length_matrix, max_capacity_kg, max_volume, max_linear_length,
                 discretization_constant, min_date, max_driving_hours=None,
                 service_time_matrix=None, evaluation_period=None,
                 allowed_early_hours=12, allowed_late_hours=12,
                 depot_node_ids=None, vendor_node_ids=None, vendor_depot_map=None,
                 vendor_start_hr=None, pickup_end_hr=None,
                 starting_depot=None, closing_depot=None,
                 allow_night_wait=True,
                 verbose=False):
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
        self.linear_length_matrix = linear_length_matrix if linear_length_matrix is not None else np.zeros(len(distance_matrix))
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
        self.depot_node_ids = set(depot_node_ids or [])
        self.vendor_node_ids = set(vendor_node_ids or [])
        self.vendor_depot_map = vendor_depot_map or {}
        self.vendor_start_hr = vendor_start_hr
        self.pickup_end_hr = pickup_end_hr
        self.starting_depot = starting_depot
        self.closing_depot = closing_depot
        self.allow_night_wait = bool(allow_night_wait)
        self.verbose = bool(verbose)
        self._vendor_row_by_node = {}
        if self.vendors_df is not None and 'node_id' in self.vendors_df.columns:
            for _, row in self.vendors_df.iterrows():
                node_id = row.get('node_id', None)
                if pd.notna(node_id):
                    self._vendor_row_by_node[int(node_id)] = row

        # Cache evaluation results
        self._total_distance = None
        self._total_time = None
        self._is_feasible = None
        self._constraint_violations = []
        self._warned_long_travel = False

    def _is_depot(self, node_id):
        return node_id == 0 or node_id in self.depot_node_ids

    def _is_vendor(self, node_id):
        if self.vendor_node_ids:
            return node_id in self.vendor_node_ids
        return node_id != 0 and node_id not in self.depot_node_ids

    def _vendor_name(self, node_id):
        row = self._vendor_row_by_node.get(int(node_id))
        if row is None:
            return f"Vendor {node_id}"
        base_name = str(row.get('vendor Name', row.get('Vendor Name', f'Vendor {node_id}'))).strip()
        requested_loading = row.get('Requested Loading', row.get('Requested Loading Date', None))
        loading_label = ""
        if requested_loading is not None and str(requested_loading).strip():
            parsed = pd.to_datetime(requested_loading, errors='coerce')
            if pd.notna(parsed):
                loading_label = parsed.to_pydatetime().strftime("%Y-%m-%d %H:%M")
        if loading_label:
            return f"{base_name} (load {loading_label})"
        return base_name
        
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
            if not route or route[0] != 0:
                self._constraint_violations.append("Route does not start at depot")
                if not check_all:
                    self._is_feasible = False
                    return False
            for node in route:
                if self._is_vendor(node):
                    if node in visited_vendors:
                        self._constraint_violations.append(f"{self._vendor_name(node)} visited multiple times")
                        if not check_all:
                            self._is_feasible = False
                            return False
                    visited_vendors.add(node)

        expected_vendors = self.vendor_node_ids if self.vendor_node_ids else set(
            n for n in range(1, len(self.capacity_matrix)) if not self._is_depot(n)
        )
        if visited_vendors != expected_vendors:
            missing = expected_vendors - visited_vendors
            if missing:
                missing_names = [self._vendor_name(v) for v in sorted(missing)]
                self._constraint_violations.append(f"Missing vendors: {', '.join(missing_names)}")
                if not check_all:
                    self._is_feasible = False
                    return False
        
        # Check 2: Capacity + driving-time window constraints
        for route_idx, route in enumerate(self.routes):
            def _service_minutes_for_vendor(node_id):
                if self.service_time_matrix is None:
                    return 0.0
                if node_id < 0 or node_id >= len(self.service_time_matrix):
                    return 0.0
                return float(self.service_time_matrix[node_id])

            # Track dynamic load to enforce capacity with mid-route depots
            current_weight = 0.0
            current_volume = 0.0
            current_linear_length = 0.0
            picked_vendors = []
            depot_deliveries = {}
            for node in route:
                if self._is_vendor(node):
                    picked_vendors.append(node)
                    current_weight += float(self.capacity_matrix[node])
                    current_volume += float(self.loading_matrix[node])
                    current_linear_length += float(self.linear_length_matrix[node])
                    if route_idx < len(self.max_capacity_kg):
                        if current_weight > self.max_capacity_kg[route_idx]:
                            self._constraint_violations.append(
                                f"Route {route_idx}: Weight {current_weight:.0f} > {self.max_capacity_kg[route_idx]:.0f} kg"
                            )
                            if not check_all:
                                self._is_feasible = False
                                return False
                        if current_volume > self.max_volume[route_idx]:
                            self._constraint_violations.append(
                                f"Route {route_idx}: Volume {current_volume:.1f} > {self.max_volume[route_idx]:.1f} m³"
                            )
                            if not check_all:
                                self._is_feasible = False
                                return False
                        if current_linear_length > self.max_linear_length[route_idx]:
                            self._constraint_violations.append(
                                f"Route {route_idx}: Linear {current_linear_length:.1f} > {self.max_linear_length[route_idx]:.1f} m"
                            )
                            if not check_all:
                                self._is_feasible = False
                                return False
                elif node in self.depot_node_ids:
                    delivered_here = [v for v in picked_vendors if self.vendor_depot_map.get(v) == node]
                    if delivered_here:
                        depot_deliveries[node] = delivered_here
                        for v in delivered_here:
                            current_weight -= float(self.capacity_matrix[v])
                            current_volume -= float(self.loading_matrix[v])
                            current_linear_length -= float(self.linear_length_matrix[v])
                        picked_vendors = [v for v in picked_vendors if v not in delivered_here]

            route_weight = sum(self.capacity_matrix[node] for node in route if self._is_vendor(node))
            route_volume = sum(self.loading_matrix[node] for node in route if self._is_vendor(node))
            route_linear = sum(self.linear_length_matrix[node] for node in route if self._is_vendor(node))
            
            # Calculate travel time from time_matrix (expecting seconds).
                    # Exclude dummy-start (node 0) → first vendor leg.
            route_travel_time_seconds = 0.0
            for i in range(0, len(route) - 1):
                if route[i] == 0:
                    continue
                route_travel_time_seconds += self.time_matrix[route[i]][route[i + 1]]
            route_travel_time_hours = route_travel_time_seconds / 3600.0
            
            # Debug: Check if time values seem reasonable
            if route_idx == 0 and route_travel_time_hours > 100:
                self._warned_long_travel = True
            
            # Add service time: loading at vendors + unloading at depots
            vendor_nodes = [node for node in route if self._is_vendor(node)]
            load_service_minutes = sum(_service_minutes_for_vendor(v) for v in vendor_nodes)
            unload_service_minutes = sum(_service_minutes_for_vendor(v) for v in vendor_nodes)
            route_service_time_hours = (load_service_minutes + unload_service_minutes) / 60.0
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
                    if self.verbose and route_idx < 3 and len(self._constraint_violations) < 5:
                        print(f"⚠️  {violation_msg}")
                    if not check_all:
                        self._is_feasible = False
                        return False
        
        # Check 3: Time windows + multi-depot delivery constraints
        if self.evaluation_period is not None:
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

            def _vendor_row(node_id):
                if node_id in self._vendor_row_by_node:
                    return self._vendor_row_by_node.get(node_id)
                vendor_idx = node_id - 1
                if self.vendors_df is None or vendor_idx < 0 or vendor_idx >= len(self.vendors_df):
                    return None
                return self.vendors_df.iloc[vendor_idx]

            def _vendor_requested_loading(node_id):
                vendor_row = _vendor_row(node_id)
                if vendor_row is None:
                    return None

                def _get_row_value(row, key_name):
                    if key_name in row:
                        return row.get(key_name, None)
                    # Fallback: case/space-insensitive match on column names
                    target = str(key_name).strip().lower()
                    for col in row.index:
                        if str(col).strip().lower() == target:
                            return row.get(col, None)
                    return None

                for raw in [
                    _get_row_value(vendor_row, 'Requested Loading'),
                    _get_row_value(vendor_row, 'Requested Loading Date'),
                ]:
                    parsed = pd.to_datetime(raw, errors='coerce')
                    if pd.notna(parsed):
                        return _to_naive(parsed)
                return None


            def _vendor_requested_delivery(node_id):
                vendor_row = _vendor_row(node_id)
                if vendor_row is None:
                    return None
                for raw in [
                    vendor_row.get('Requested Delivery', None),
                    vendor_row.get('Requested Delivery Date', None),
                ]:
                    parsed = pd.to_datetime(raw, errors='coerce')
                    if pd.notna(parsed):
                        return _to_naive(parsed)
                return None

            def _daily_window_for(dt_value, start_hr, end_hr):
                if dt_value is None or start_hr is None or end_hr is None:
                    return None, None
                base = _to_naive(dt_value)
                if base is None:
                    return None, None
                try:
                    start_hr = int(start_hr)
                    end_hr = int(end_hr)
                except (TypeError, ValueError):
                    return None, None
                base_day = base.replace(hour=0, minute=0, second=0, microsecond=0)
                start_dt = base_day + timedelta(hours=start_hr)
                end_dt = base_day + timedelta(hours=end_hr)
                if end_dt < start_dt:
                    end_dt = end_dt + timedelta(days=1)
                return start_dt, end_dt

            def _depot_window_for_deliveries(delivery_vendor_nodes):
                deliveries = []
                for v in delivery_vendor_nodes:
                    delivery_time = _vendor_requested_delivery(v)
                    if delivery_time is not None:
                        deliveries.append(delivery_time)
                if not deliveries:
                    return None, None
                window_start = None
                window_end = None
                for d in deliveries:
                    candidate_start = d - allowed_early
                    candidate_end = d + allowed_late
                    if candidate_start > candidate_end:
                        return None, None
                    window_start = candidate_start if window_start is None else max(window_start, candidate_start)
                    window_end = candidate_end if window_end is None else min(window_end, candidate_end)
                return window_start, window_end

            for route_idx, route in enumerate(self.routes):
                if not route:
                    continue

                # Must end at a depot node
                last_node = route[-1]
                if not self._is_depot(last_node):
                    self._constraint_violations.append(
                        f"Route {route_idx}: does not end at a depot"
                    )
                    if not check_all:
                        return False

                # Prevent multiple visits to same depot within a route
                depot_visits = [n for n in route if n in self.depot_node_ids]
                if len(depot_visits) != len(set(depot_visits)):
                    self._constraint_violations.append(
                        f"Route {route_idx}: visits the same depot more than once"
                    )
                    if not check_all:
                        return False

                route_vendor_nodes = [n for n in route if self._is_vendor(n)]
                if not route_vendor_nodes:
                    # No vendors in this route; skip vendor window checks
                    continue
                picked_vendors = []
                depot_deliveries = {}
                for node in route:
                    if self._is_vendor(node):
                        picked_vendors.append(node)
                    elif node in self.depot_node_ids:
                        delivered_here = [v for v in picked_vendors if self.vendor_depot_map.get(v) == node]
                        if delivered_here:
                            depot_deliveries[node] = delivered_here
                            picked_vendors = [v for v in picked_vendors if v not in delivered_here]

                # Do not allow depot visits without cargo to deliver
                for node in route:
                    if node in self.depot_node_ids and node != 0 and node not in depot_deliveries:
                        self._constraint_violations.append(
                            f"Route {route_idx}: depot {node} visited with no cargo to unload"
                        )
                        if not check_all:
                            return False

                # Each vendor must have its delivery depot after pickup
                for v in route_vendor_nodes:
                    depot_node = self.vendor_depot_map.get(v)
                    if depot_node is None:
                        self._constraint_violations.append(
                            f"Route {route_idx}: {self._vendor_name(v)} missing depot mapping"
                        )
                        if not check_all:
                            return False
                        continue
                    if depot_node not in route:
                        self._constraint_violations.append(
                            f"Route {route_idx}: {self._vendor_name(v)} depot {depot_node} not visited"
                        )
                        if not check_all:
                            return False
                        continue
                    if route.index(depot_node) <= route.index(v):
                        self._constraint_violations.append(
                            f"Route {route_idx}: {self._vendor_name(v)} delivered before pickup"
                        )
                        if not check_all:
                            return False

                # Build arrival offsets from route start (arrival at first node)
                offsets = {}
                current_offset = 0.0
                for i in range(1, len(route)):
                    prev = route[i - 1]
                    node = route[i]
                    if self._is_vendor(prev) and self.service_time_matrix is not None and prev < len(self.service_time_matrix):
                        current_offset += float(self.service_time_matrix[prev]) * 60.0
                    if prev < len(self.time_matrix) and node < len(self.time_matrix[prev]):
                        current_offset += float(self.time_matrix[prev][node])
                    offsets[node] = current_offset

                # Compute feasible start window intersection using vendor windows,
                # then intersect with the FINAL depot window (if available).
                start_min = None
                start_max = None
                found_vendor_window = False
                vendor_windows = []
                for node in route[1:]:
                    if not self._is_vendor(node):
                        continue
                    requested = _vendor_requested_loading(node)
                    if requested is None:
                        continue
                    found_vendor_window = True
                    window_start = requested - allowed_early
                    window_end = requested + allowed_late
                    vendor_windows.append((self._vendor_name(node), window_start, window_end))

                    offset = timedelta(seconds=float(offsets.get(node, 0.0)))
                    node_start = window_start - offset
                    node_end = window_end - offset
                    start_min = node_start if start_min is None else max(start_min, node_start)
                    start_max = node_end if start_max is None else min(start_max, node_end)

                # Also enforce that the final depot arrival window is feasible from the start
                final_depot = route[-1] if route and route[-1] in self.depot_node_ids else None
                final_depot_window = (None, None)
                if final_depot is not None:
                    final_deliveries = depot_deliveries.get(final_depot, [])
                    depot_window_start, depot_window_end = _depot_window_for_deliveries(final_deliveries)
                    final_depot_window = (depot_window_start, depot_window_end)
                    if depot_window_start is not None and depot_window_end is not None:
                        offset = timedelta(seconds=float(offsets.get(final_depot, 0.0)))
                        node_start = depot_window_start - offset
                        node_end = depot_window_end - offset
                        start_min = node_start if start_min is None else max(start_min, node_start)
                        start_max = node_end if start_max is not None else min(start_max, node_end)

                first_vendor_requested = None
                for v in route_vendor_nodes:
                    first_vendor_requested = _vendor_requested_loading(v)
                    if first_vendor_requested is not None:
                        break

                if start_min is not None and start_max is not None and start_min > start_max:
                    vendor_window_summary = ""
                    if vendor_windows:
                        v_starts = [w[1] for w in vendor_windows]
                        v_ends = [w[2] for w in vendor_windows]
                        vendor_window_summary = (
                            f" vendor_window_range=[{_fmt_time(min(v_starts))}..{_fmt_time(max(v_ends))}]"
                        )
                    depot_window_summary = ""
                    if final_depot_window[0] is not None and final_depot_window[1] is not None:
                        depot_window_summary = (
                            f" final_depot_window=[{_fmt_time(final_depot_window[0])}..{_fmt_time(final_depot_window[1])}]"
                        )
                    vendor_list_summary = ""
                    if route_vendor_nodes:
                        vendor_names = [self._vendor_name(v) for v in route_vendor_nodes]
                        vendor_list_summary = f" vendors=[{', '.join(vendor_names)}]"
                    offset_summary = []
                    for v_name, w_start, w_end in vendor_windows:
                        v_node = None
                        for v_id in route_vendor_nodes:
                            if self._vendor_name(v_id) == v_name:
                                v_node = v_id
                                break
                        v_offset = offsets.get(v_node, 0.0) if v_node is not None else 0.0
                        offset_summary.append(
                            f"{v_name}: window=[{_fmt_time(w_start)}..{_fmt_time(w_end)}], "
                            f"offset={_fmt_time(timedelta(seconds=float(v_offset)))}"
                        )
                    if final_depot is not None and final_depot in offsets and final_depot_window[0] is not None:
                        depot_offset = timedelta(seconds=float(offsets.get(final_depot, 0.0)))
                        offset_summary.append(
                            f"Depot {final_depot}: window=[{_fmt_time(final_depot_window[0])}..{_fmt_time(final_depot_window[1])}], "
                            f"offset={_fmt_time(depot_offset)}"
                        )
                    offset_details = f" offsets=({'; '.join(offset_summary)})" if offset_summary else ""
                    route_names = []
                    for n in route:
                        if n == 0:
                            route_names.append("Start")
                        elif n in self.depot_node_ids:
                            route_names.append(f"Depot {n}")
                        else:
                            route_names.append(self._vendor_name(n))
                    route_summary = f" route={' -> '.join(route_names)}" if route_names else ""
                    self._constraint_violations.append(
                        f"Route {route_idx}: no feasible start time "
                        f"(start_min={_fmt_time(start_min)}, start_max={_fmt_time(start_max)})"
                        f"{vendor_window_summary}{depot_window_summary}{vendor_list_summary}{offset_details}{route_summary}"
                    )
                    if not check_all:
                        return False
                    continue

                if start_min is not None and start_max is not None:
                    # Choose time closest to first vendor requested within feasible range
                    if first_vendor_requested is None:
                        current_time = start_min
                    else:
                        if first_vendor_requested < start_min:
                            current_time = start_min
                        elif first_vendor_requested > start_max:
                            current_time = start_max
                        else:
                            current_time = first_vendor_requested
                else:
                    current_time = start_min if start_min is not None else _vendor_requested_loading(route_vendor_nodes[0]) if route_vendor_nodes else None
                if current_time is None or not found_vendor_window:
                    if not found_vendor_window and route_vendor_nodes:
                        debug_details = []
                        for v in route_vendor_nodes:
                            row = _vendor_row(v)
                            if row is None:
                                debug_details.append(f"{self._vendor_name(v)}: row=None")
                                continue
                            cols_preview = [str(c) for c in list(row.index)[:8]]
                            raw_loading = row.get('Requested Loading', None)
                            raw_loading_date = row.get('Requested Loading Date', None)
                            if raw_loading is None:
                                # Case/space-insensitive fallback
                                for col in row.index:
                                    if str(col).strip().lower() == 'requested loading':
                                        raw_loading = row.get(col, None)
                                    if str(col).strip().lower() == 'requested loading date':
                                        raw_loading_date = row.get(col, None)
                            debug_details.append(
                                f"{self._vendor_name(v)}: Requested Loading={raw_loading} "
                                f"Requested Loading Date={raw_loading_date} cols={cols_preview}"
                            )
                        detail_text = " | ".join(debug_details)
                    else:
                        route_nodes = [str(n) for n in route]
                        sample_vendors = sorted(list(self.vendor_node_ids))[:10]
                        detail_text = (
                            "no vendor windows found; "
                            f"route_nodes={route_nodes} vendor_nodes_sample={sample_vendors}"
                        )
                    self._constraint_violations.append(
                        f"Route {route_idx}: missing vendor window for start time ({detail_text})"
                    )
                    if not check_all:
                        return False
                    continue

                # Walk through route and validate arrivals
                for i in range(1, len(route)):
                    node = route[i]
                    if i > 1:
                        prev = route[i - 1]
                        if self._is_vendor(prev) and self.service_time_matrix is not None and prev < len(self.service_time_matrix):
                            current_time = current_time + timedelta(minutes=self.service_time_matrix[prev])
                        elif prev in self.depot_node_ids:
                            delivered_here = depot_deliveries.get(prev, [])
                            if delivered_here:
                                unload_minutes = sum(
                                    float(self.service_time_matrix[v]) for v in delivered_here
                                    if self.service_time_matrix is not None and v < len(self.service_time_matrix)
                                )
                                current_time = current_time + timedelta(minutes=unload_minutes)
                        if prev < len(self.time_matrix) and node < len(self.time_matrix[prev]):
                            current_time = current_time + timedelta(seconds=self.time_matrix[prev][node])

                    if self._is_vendor(node):
                        requested_loading = _vendor_requested_loading(node)
                        if requested_loading is None:
                            continue
                        earliest_allowed = requested_loading - allowed_early
                        latest_allowed = requested_loading + allowed_late
                        if current_time < earliest_allowed:
                            current_time = earliest_allowed
                        # If vendor has daily open hours, allow waiting until next open window
                        if self.vendor_start_hr is not None and self.pickup_end_hr is not None:
                            open_start, open_end = _daily_window_for(
                                current_time,
                                self.vendor_start_hr,
                                self.pickup_end_hr
                            )
                            if open_start is not None and open_end is not None:
                                if current_time < open_start:
                                    current_time = open_start
                                elif current_time > open_end and self.allow_night_wait:
                                    # Wait until next day's opening (if allowed)
                                    next_open = open_start + timedelta(days=1)
                                    if next_open <= latest_allowed:
                                        current_time = next_open
                        if current_time > latest_allowed:
                            violation_msg = (
                                f"Route {route_idx}: {self._vendor_name(node)} arrival too late "
                                f"({_fmt_time(current_time)}; window ends {_fmt_time(latest_allowed)})"
                            )
                            self._constraint_violations.append(violation_msg)
                            if not check_all:
                                return False
                    elif node in self.depot_node_ids:
                        delivered_here = depot_deliveries.get(node, [])
                        window_start, window_end = _depot_window_for_deliveries(delivered_here)
                        if delivered_here and (window_start is None or window_end is None):
                            violation_msg = (
                                f"Route {route_idx}: depot arrival window infeasible "
                                f"(no overlap with depot hours)"
                            )
                            self._constraint_violations.append(violation_msg)
                            if not check_all:
                                return False
                            continue
                        if window_start is None or window_end is None:
                            continue
                        if current_time < window_start:
                            current_time = window_start
                        if self.starting_depot is not None and self.closing_depot is not None:
                            open_start, open_end = _daily_window_for(
                                current_time,
                                self.starting_depot,
                                self.closing_depot
                            )
                            if open_start is not None and open_end is not None:
                                if current_time < open_start:
                                    current_time = open_start
                                elif current_time > open_end and self.allow_night_wait:
                                    next_open = open_start + timedelta(days=1)
                                    if next_open <= window_end:
                                        current_time = next_open
                        if current_time > window_end:
                            violation_msg = (
                                f"Route {route_idx}: depot arrival too late "
                                f"({_fmt_time(current_time)}; window ends {_fmt_time(window_end)})"
                            )
                            self._constraint_violations.append(violation_msg)
                            if not check_all:
                                return False
        
        self._is_feasible = len(self._constraint_violations) == 0
        return self._is_feasible
    
    def get_num_routes(self):
        """Return number of routes (vehicles used)."""
        return len(self.routes)
    
    def get_route_capacity_usage(self, route_idx):
        """Get capacity usage for a specific route."""
        if route_idx >= len(self.routes):
            return 0, 0, 0
        
        route = self.routes[route_idx]
        weight = sum(self.capacity_matrix[node] for node in route if self._is_vendor(node))
        volume = sum(self.loading_matrix[node] for node in route if self._is_vendor(node))
        linear_length = sum(self.linear_length_matrix[node] for node in route if self._is_vendor(node))
        return weight, volume, linear_length
    
    def copy(self):
        """Create a deep copy of this solution."""
        return RouteSolution(
            routes=copy.deepcopy(self.routes),
            vendors_df=self.vendors_df,
            distance_matrix=self.distance_matrix,
            time_matrix=self.time_matrix,
            capacity_matrix=self.capacity_matrix,
            loading_matrix=self.loading_matrix,
            linear_length_matrix=self.linear_length_matrix,
            service_time_matrix=self.service_time_matrix,
            max_capacity_kg=self.max_capacity_kg,
            max_volume=self.max_volume,
            max_linear_length=self.max_linear_length,
            discretization_constant=self.discretization_constant,
            min_date=self.min_date,
            max_driving_hours=self.max_driving_hours,
            evaluation_period=self.evaluation_period,
            allowed_early_hours=self.allowed_early_hours,
            allowed_late_hours=self.allowed_late_hours,
            depot_node_ids=list(self.depot_node_ids),
            vendor_node_ids=list(self.vendor_node_ids),
            vendor_depot_map=self.vendor_depot_map,
            vendor_start_hr=self.vendor_start_hr,
            pickup_end_hr=self.pickup_end_hr,
            starting_depot=self.starting_depot,
            closing_depot=self.closing_depot,
            allow_night_wait=self.allow_night_wait
        )
    
    def invalidate_cache(self):
        """Invalidate cached evaluation results after modification."""
        self._total_distance = None
        self._total_time = None
        self._is_feasible = None
        self._constraint_violations = []
        self._warned_long_travel = False
    
    def __str__(self):
        """String representation of solution."""
        cost = self.evaluate()
        feasible = "✓" if self.is_feasible(check_all=False) else "✗"
        return f"RouteSolution[{len(self.routes)} routes, {cost:.0f} km, feasible: {feasible}]"
