"""
Adaptive Large Neighborhood Search (ALNS) solver for VRP.
Uses destroy and repair operators on route-based representations.
"""

import numpy as np
import pandas as pd
import random
import copy
import itertools
from datetime import datetime, timedelta
from .route_solution import RouteSolution


class ALNSSolver:
    """
    Adaptive Large Neighborhood Search for Vehicle Routing Problem.
    
    Designed for large instances (50+ vendors).
    Uses route-based representation.
    """
    
    def __init__(self, vendors_df, distance_matrix, time_matrix, 
                 capacity_matrix, loading_matrix, linear_length_matrix, max_capacity_kg, max_ldms_vc=None,
                 discretization_constant=None, min_date=None, max_driving_hours=None,
                 service_time_matrix=None, config=None, evaluation_period=None,
                 max_volume=None, max_linear_length=None, allowed_early_hours=12, allowed_late_hours=12,
                 vendor_ids=None, depot_node_ids=None, vendor_node_ids=None, vendor_depot_map=None,
                 vendor_start_hr=None, pickup_end_hr=None, starting_depot=None, closing_depot=None,
                 allow_night_wait=True):
        """
        Initialize ALNS solver.
        
        Args:
            vendors_df: DataFrame with vendor information
            distance_matrix: Distance matrix [km]
            time_matrix: Time matrix [seconds]
            capacity_matrix: Cargo weight per vendor [kg]
            loading_matrix: Loading volume per vendor [m³]
            linear_length_matrix: Linear length per vendor [m]
            max_capacity_kg: Max weight capacity per vehicle [kg]
            max_ldms_vc: Max volume capacity per vehicle [m³] (DEPRECATED, use max_volume)
            max_volume: Max volume capacity per vehicle [m³]
            max_linear_length: Max linear dimension per vehicle [m]
            discretization_constant: Time discretization [hours]
            min_date: Minimum simulation date
            service_time_matrix: Service time per vendor [minutes]
            config: Dictionary with ALNS parameters
            evaluation_period: Tuple [period_start, period_end] for time window calculation
        """
        self.vendors_df = vendors_df
        self.distance_matrix = distance_matrix
        self.time_matrix = time_matrix
        self.capacity_matrix = capacity_matrix
        self.loading_matrix = loading_matrix
        self.linear_length_matrix = linear_length_matrix if linear_length_matrix is not None else np.zeros(len(distance_matrix))
        self.service_time_matrix = service_time_matrix if service_time_matrix is not None else np.zeros(len(distance_matrix))
        self.max_capacity_kg = max_capacity_kg
        
        # Handle both old (max_ldms_vc) and new (max_volume, max_linear_length) parameter names
        self.max_volume_vc = max_volume if max_volume is not None else max_ldms_vc
        self.max_linear_length_vc = max_linear_length if max_linear_length is not None else 16.1  # Default value
        # Keep max_ldms_vc for backward compatibility
        self.max_ldms_vc = self.max_volume_vc
        
        self.discretization_constant = discretization_constant
        self.min_date = min_date
        self.max_driving_hours = max_driving_hours
        self.evaluation_period = evaluation_period
        self.allowed_early_hours = allowed_early_hours
        self.allowed_late_hours = allowed_late_hours
        self.depot_node_ids = set(depot_node_ids or [])
        self.vendor_node_ids = set(vendor_node_ids or [])
        self.vendor_depot_map = vendor_depot_map or {}
        self.vendor_start_hr = vendor_start_hr
        self.pickup_end_hr = pickup_end_hr
        self.starting_depot = starting_depot
        self.closing_depot = closing_depot
        self.allow_night_wait = bool(allow_night_wait)
        self._vendor_row_by_node = {}
        if self.vendors_df is not None and 'node_id' in self.vendors_df.columns:
            for _, row in self.vendors_df.iterrows():
                node_id = row.get('node_id', None)
                if pd.notna(node_id):
                    self._vendor_row_by_node[int(node_id)] = row
        self._vendor_requested_cache = {}
        self._vendor_delivery_cache = {}
        self._feasible_cache = {}
        self._time_span_cache = {}
        
        # ALNS parameters
        self.config = config or {}
        self.merge_debug = bool(self.config.get('merge_debug', False))
        self.depot_debug = bool(self.config.get('depot_debug', False) or self.merge_debug)
        self.depot_debug_verbose = bool(self.config.get('depot_debug_verbose', False))
        self._depot_debug_seen = set()
        self.enable_merge = bool(self.config.get('enable_merge', False))
        self.max_iterations = self.config.get('max_iterations', 1000)
        self.min_removal_size = self.config.get('min_removal_size', 0.1)  # 10% of vendors
        self.max_removal_size = self.config.get('max_removal_size', 0.4)  # 40% of vendors
        self.initial_temperature = self.config.get('initial_temperature', 1000)
        self.cooling_rate = self.config.get('cooling_rate', 0.995)
        self.no_improvement_limit = self.config.get('no_improvement_limit', 200)
        self.insertion_top_k = self.config.get('insertion_top_k', None)
        self.initial_feasible = False
        
        # Operator weights (adaptive)
        self.destroy_operators = {
            'random': {'weight': 1.0, 'calls': 0, 'improvements': 0},
            'worst_cost': {'weight': 1.0, 'calls': 0, 'improvements': 0},
            'shaw': {'weight': 1.0, 'calls': 0, 'improvements': 0}
        }
        
        self.repair_operators = {
            'greedy': {'weight': 1.0, 'calls': 0, 'improvements': 0},
            'regret2': {'weight': 1.0, 'calls': 0, 'improvements': 0}
        }
        
        self.vendor_ids = list(vendor_ids) if vendor_ids is not None else None
        if self.vendor_ids is None:
            self.vendor_ids = list(self.vendor_node_ids) if self.vendor_node_ids else list(range(1, len(capacity_matrix)))
        self.num_vendors = len(self.vendor_ids)
    
    def solve(self, verbose=True):
        """
        Run ALNS algorithm.
        
        Args:
            verbose: Print progress information
            
        Returns:
            RouteSolution: Best solution found
        """
        if verbose:
            print(f'\n🔍 Running ALNS metaheuristic solver')
            print(f'   - Max iterations: {self.max_iterations}')
            print(f'   - Vendors: {self.num_vendors}')
            print(f'   - Max driving: {self.max_driving_hours}h')
        
        # Generate initial solution using time-window-aware routing
        current = self.generate_initial_solution()
        best = current.copy()
        current_cost = current.evaluate()
        best_cost = current_cost
        
        # Check if initial solution is feasible
        current_feasible = current.is_feasible(check_all=True)  # Get ALL violations
        self.initial_feasible = current_feasible
        
        if verbose:
            print(f'   - Initial solution: {current.get_num_routes()} routes, {current.evaluate():.0f} km, feasible={current_feasible}')
            if not current_feasible and current._constraint_violations:
                print(f'     ⚠️  {len(current._constraint_violations)} violations found:')
                # Group by violation type
                early_violations = [v for v in current._constraint_violations if 'before earliest' in v or 'early' in v]
                late_violations = [v for v in current._constraint_violations if 'after latest' in v or 'late' in v]
                
                if early_violations:
                    print(f'       Early arrivals ({len(early_violations)}):')
                    for v in early_violations[:3]:
                        print(f'         - {v}')
                    if len(early_violations) > 3:
                        print(f'         ... and {len(early_violations) - 3} more')
                
                if late_violations:
                    print(f'       Late arrivals ({len(late_violations)}):')
                    for v in late_violations[:3]:
                        print(f'         - {v}')
                    if len(late_violations) > 3:
                        print(f'         ... and {len(late_violations) - 3} more')
        
        temperature = self.initial_temperature
        no_improvement_count = 0
        
        for iteration in range(self.max_iterations):
            # Select operators adaptively
            destroy_op = self.select_operator(self.destroy_operators)
            repair_op = self.select_operator(self.repair_operators)
            
            # Destroy
            removal_size = random.uniform(self.min_removal_size, self.max_removal_size)
            num_remove = max(1, int(self.num_vendors * removal_size))
            destroyed, removed_vendors = self.destroy(current, destroy_op, num_remove)
            
            # Repair
            repaired = self.repair(destroyed, removed_vendors, repair_op)
            
            # Acceptance criterion (simulated annealing)
            repaired_cost = repaired.evaluate()
            delta = repaired_cost - current_cost
            
            accept = False
            if delta < 0:
                # Improvement
                accept = True
                self.update_operator_weights(destroy_op, repair_op, reward=3)
            elif random.random() < np.exp(-delta / temperature):
                # Accept worse solution probabilistically
                accept = True
                self.update_operator_weights(destroy_op, repair_op, reward=1)
            
            if accept:
                current = repaired
                current_cost = repaired_cost
                no_improvement_count = 0
                
                if repaired_cost < best_cost:
                    best = repaired.copy()
                    best_cost = repaired_cost
                    if verbose and iteration % 100 == 0:
                        print(f'   - Iteration {iteration}: New best {best_cost:.0f} km ({best.get_num_routes()} routes)')
            else:
                no_improvement_count += 1
            
            # Cool down temperature
            temperature *= self.cooling_rate
            
            # Periodically try to merge routes (optional)
            if self.enable_merge and iteration % 250 == 0 and iteration > 0:
                merged = self.try_merge_routes(best)
                merged_cost = merged.evaluate()
                if merged_cost < best_cost and merged.is_feasible(check_all=False):
                    best = merged
                    current = merged.copy()
                    best_cost = merged_cost
                    current_cost = merged_cost
            
            # Early stopping if no improvement for long time
            if no_improvement_count > self.no_improvement_limit:
                break
        
        # Final route merging attempt (optional)
        if verbose:
            print(f'   - Final solution: {best.get_num_routes()} routes, {best.evaluate():.0f} km')
        
        if self.enable_merge:
            if verbose:
                print(f'   - Attempting route merging...')
            merged = self.try_merge_routes(best)
            merged_cost = merged.evaluate()
            if merged_cost <= best_cost and merged.is_feasible(check_all=False):
                best = merged
                best_cost = merged_cost
                if verbose:
                    print(f'   - After merging: {best.get_num_routes()} routes, {best_cost:.0f} km')
        
        # Final feasibility check
        best_feasible = best.is_feasible(check_all=True)  # Get ALL violations
        if verbose:
            print(f'   - Feasible: {best_feasible}')
            if not best_feasible and best._constraint_violations:
                print(f'   ✗ Solution infeasible: {len(best._constraint_violations)} violations')
                early_violations = [v for v in best._constraint_violations if 'before earliest' in v or 'early' in v]
                late_violations = [v for v in best._constraint_violations if 'after latest' in v or 'late' in v]
                
                if early_violations:
                    print(f'     Early arrivals ({len(early_violations)}):')
                    for v in early_violations[:5]:
                        print(f'       - {v}')
                    if len(early_violations) > 5:
                        print(f'       ... and {len(early_violations) - 5} more')
                
                if late_violations:
                    print(f'     Late arrivals ({len(late_violations)}):')
                    for v in late_violations[:5]:
                        print(f'       - {v}')
                    if len(late_violations) > 5:
                        print(f'       ... and {len(late_violations) - 5} more')

        # Prune empty depot-only routes
        best.routes = [r for r in best.routes if len(r) > 1 and any(self._is_vendor(node) for node in r)]
        best.invalidate_cache()

        return best
    
    def generate_initial_solution(self):
        """Generate initial solution without spatial/temporal grouping.

        Builds routes by considering vendor time windows (earliest to latest) and
        capacities, starting from the earliest requested arrivals.
        """
        unrouted = list(self.vendor_ids) if self.vendor_ids is not None else list(range(1, self.num_vendors + 1))
        routes = []
        
        # Capacity limits
        max_weight = max(self.max_capacity_kg) if self.max_capacity_kg else float('inf')
        max_volume = max(self.max_ldms_vc) if self.max_ldms_vc else float('inf')
        max_linear = max(self.max_linear_length_vc) if self.max_linear_length_vc else float('inf')
        
        # Sort vendors by requested time (earliest to latest)
        unrouted_sorted = sorted(
            unrouted,
            key=lambda v: (
                self._get_vendor_requested_time(v).timestamp()
                if self._get_vendor_requested_time(v)
                else float('inf')
            )
        )

        # Build routes greedily with time-window feasibility
        routes = self._build_time_feasible_routes(unrouted_sorted, max_weight, max_volume, max_linear)
        
        # Build RouteSolution
        return RouteSolution(
            routes=routes,
            vendors_df=self.vendors_df,
            distance_matrix=self.distance_matrix,
            time_matrix=self.time_matrix,
            capacity_matrix=self.capacity_matrix,
            loading_matrix=self.loading_matrix,
            linear_length_matrix=self.linear_length_matrix,
            service_time_matrix=self.service_time_matrix,
            max_capacity_kg=self.max_capacity_kg,
            max_volume=self.max_volume_vc,
            max_linear_length=self.max_linear_length_vc,
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
    
    def _get_vendor_requested_time(self, vendor_id):
        if vendor_id in self._vendor_requested_cache:
            return self._vendor_requested_cache[vendor_id]
        if self.vendors_df is None:
            return None
        vendor_row = self._vendor_row_by_node.get(vendor_id)
        if vendor_row is None:
            vendor_idx = vendor_id - 1
            if vendor_idx < 0 or vendor_idx >= len(self.vendors_df):
                return None
            vendor_row = self.vendors_df.iloc[vendor_idx]
        for raw in [
            vendor_row.get('Requested Loading', None),
            vendor_row.get('Requested Loading Date', None),
        ]:
            parsed = pd.to_datetime(raw, errors='coerce', utc=True)
            if pd.notna(parsed):
                result = self._to_naive(parsed)
                self._vendor_requested_cache[vendor_id] = result
                return result
        self._vendor_requested_cache[vendor_id] = None
        return None

    def _get_vendor_time_window(self, vendor_id):
        requested = self._get_vendor_requested_time(vendor_id)
        if requested is None:
            return None, None
        requested_dt = self._to_naive(requested)
        allowed_early = float(self.allowed_early_hours) * 3600
        allowed_late = float(self.allowed_late_hours) * 3600
        earliest = requested_dt - timedelta(seconds=allowed_early)
        latest = requested_dt + timedelta(seconds=allowed_late)
        return earliest, latest

    def _get_vendor_delivery_time(self, vendor_id):
        if vendor_id in self._vendor_delivery_cache:
            return self._vendor_delivery_cache[vendor_id]
        if self.vendors_df is None:
            return None
        vendor_row = self._vendor_row_by_node.get(vendor_id)
        if vendor_row is None:
            vendor_idx = vendor_id - 1
            if vendor_idx < 0 or vendor_idx >= len(self.vendors_df):
                return None
            vendor_row = self.vendors_df.iloc[vendor_idx]
        for raw in [
            vendor_row.get('Requested Delivery', None),
            vendor_row.get('Requested Delivery Date', None),
        ]:
            parsed = pd.to_datetime(raw, errors='coerce', utc=True)
            if pd.notna(parsed):
                result = self._to_naive(parsed)
                self._vendor_delivery_cache[vendor_id] = result
                return result
        self._vendor_delivery_cache[vendor_id] = None
        return None

    def _get_vendor_time_bucket(self, vendor_id):
        if self.vendors_df is None:
            return None
        vendor_row = self._vendor_row_by_node.get(vendor_id)
        if vendor_row is None:
            vendor_idx = vendor_id - 1
            if vendor_idx < 0 or vendor_idx >= len(self.vendors_df):
                return None
            vendor_row = self.vendors_df.iloc[vendor_idx]
        raw_bucket = vendor_row.get('time_bucket', None)
        if isinstance(raw_bucket, str) and raw_bucket.strip():
            return raw_bucket.strip()
        requested = self._get_vendor_requested_time(vendor_id)
        if requested is None:
            return None
        requested_dt = self._to_naive(requested)
        if requested_dt is None:
            return None
        return requested_dt.strftime('%Y-%m')

    def _get_route_time_bucket(self, route):
        for node in route:
            if node == 0 or node in self.depot_node_ids:
                continue
            return self._get_vendor_time_bucket(node)
        return None

    def _get_route_time_span_hours(self, route):
        key = tuple(route)
        if key in self._time_span_cache:
            return self._time_span_cache[key]
        earliest = None
        latest = None
        for node in route:
            if node == 0 or node in self.depot_node_ids:
                continue
            node_earliest, node_latest = self._get_vendor_time_window(node)
            if node_earliest is None or node_latest is None:
                continue
            earliest = node_earliest if earliest is None else min(earliest, node_earliest)
            latest = node_latest if latest is None else max(latest, node_latest)
        if earliest is None or latest is None:
            self._time_span_cache[key] = 0.0
            return 0.0
        span = (latest - earliest).total_seconds() / 3600.0
        self._time_span_cache[key] = span
        return span

    def _get_route_depots(self, vendor_nodes):
        depot_deadlines = {}
        debug_key = None
        if self.depot_debug and self.depot_debug_verbose:
            debug_key = tuple(sorted(int(v) for v in vendor_nodes))
            if debug_key not in self._depot_debug_seen:
                self._depot_debug_seen.add(debug_key)
                print(f"🧭 Depot order debug: vendors={list(vendor_nodes)}")
        for v in vendor_nodes:
            depot_node = self.vendor_depot_map.get(v)
            if depot_node is None:
                continue
            delivery_time = self._get_vendor_delivery_time(v)
            if delivery_time is None:
                continue
            if self.depot_debug and self.depot_debug_verbose and debug_key in self._depot_debug_seen:
                vendor_row = self._vendor_row_by_node.get(v)
                vendor_name = None
                if vendor_row is not None:
                    vendor_name = str(vendor_row.get('vendor Name', vendor_row.get('Vendor Name', f'Vendor {v}'))).strip()
                vendor_label = vendor_name or f"Vendor {v}"
                print(f"   - {vendor_label} ({v}) -> depot {depot_node}, delivery={delivery_time}")
            if depot_node not in depot_deadlines:
                depot_deadlines[depot_node] = delivery_time
            else:
                depot_deadlines[depot_node] = min(depot_deadlines[depot_node], delivery_time)
        depots = list(depot_deadlines.keys())
        depots.sort(key=lambda d: depot_deadlines.get(d))
        if self.depot_debug and self.depot_debug_verbose and debug_key in self._depot_debug_seen:
            deadline_summary = ", ".join(
                f"{d}:{depot_deadlines.get(d)}" for d in depots
            )
            print(f"   -> depot deadlines (min): {deadline_summary}")

        if len(depots) <= 1:
            return depots

        # Heuristic: start with deadline order, then try limited swaps
        def _order_feasibility(order):
            test_route = [0] + list(vendor_nodes) + list(order)
            temp_solution = RouteSolution(
                routes=[test_route],
                vendors_df=self.vendors_df,
                distance_matrix=self.distance_matrix,
                time_matrix=self.time_matrix,
                capacity_matrix=self.capacity_matrix,
                loading_matrix=self.loading_matrix,
                linear_length_matrix=self.linear_length_matrix,
                service_time_matrix=self.service_time_matrix,
                max_capacity_kg=self.max_capacity_kg,
                max_volume=self.max_volume_vc,
                max_linear_length=self.max_linear_length_vc,
                discretization_constant=self.discretization_constant,
                min_date=self.min_date,
                max_driving_hours=self.max_driving_hours,
                evaluation_period=self.evaluation_period,
                allowed_early_hours=self.allowed_early_hours,
                allowed_late_hours=self.allowed_late_hours,
                depot_node_ids=list(self.depot_node_ids),
                vendor_node_ids=list(vendor_nodes),
                vendor_depot_map=self.vendor_depot_map,
                vendor_start_hr=self.vendor_start_hr,
                pickup_end_hr=self.pickup_end_hr,
                starting_depot=self.starting_depot,
                closing_depot=self.closing_depot,
                allow_night_wait=self.allow_night_wait
            )
            feasible = temp_solution.is_feasible(check_all=True)
            return feasible, list(temp_solution._constraint_violations)

        base_order = depots
        candidates = []
        # Deadline order and its reverse
        candidates.append(list(base_order))
        candidates.append(list(reversed(base_order)))

        # Try a small set of adjacent swaps
        max_swaps = min(6, len(base_order) - 1)
        for i in range(max_swaps):
            swapped = list(base_order)
            swapped[i], swapped[i + 1] = swapped[i + 1], swapped[i]
            candidates.append(swapped)

        # Deduplicate candidates while preserving order
        seen = set()
        unique_candidates = []
        for cand in candidates:
            key = tuple(cand)
            if key in seen:
                continue
            seen.add(key)
            unique_candidates.append(cand)

        best_order = None
        best_distance = None
        rejection_counts = {}
        for cand in unique_candidates:
            feasible, violations = _order_feasibility(cand)
            if not feasible:
                if self.depot_debug:
                    reason = violations[0] if violations else "infeasible (no details)"
                    rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
                continue
            test_route = [0] + list(vendor_nodes) + list(cand)
            route_distance = self._route_distance(test_route)
            if best_distance is None or route_distance < best_distance:
                best_distance = route_distance
                best_order = list(cand)

        if best_order is not None:
            if self.depot_debug and self.depot_debug_verbose and debug_key in self._depot_debug_seen:
                if rejection_counts:
                    top_reasons = sorted(rejection_counts.items(), key=lambda x: -x[1])[:3]
                    reason_summary = "; ".join(
                        f"{count}x {reason}" for reason, count in top_reasons
                    )
                    print(f"   -> depot order rejections: {reason_summary}")
                print(f"   -> depot order chosen (distance): {best_order} (km={best_distance:.1f})")
            return best_order

        # If still infeasible, keep deadline order (fast fallback)
        if self.depot_debug and self.depot_debug_verbose and debug_key in self._depot_debug_seen:
            if rejection_counts:
                top_reasons = sorted(rejection_counts.items(), key=lambda x: -x[1])[:3]
                reason_summary = "; ".join(
                    f"{count}x {reason}" for reason, count in top_reasons
                )
                print(f"   -> depot order rejections: {reason_summary}")
            print(f"   -> depot order fallback (deadline): {base_order}")
        return base_order

    def _route_vendors(self, route):
        return [n for n in route if self._is_vendor(n)]

    def _build_route_with_depots(self, vendor_nodes):
        if not vendor_nodes:
            return []
        return [0] + list(vendor_nodes) + self._get_route_depots(list(vendor_nodes))

    def _route_distance(self, route):
        if not route or len(route) < 2:
            return 0.0
        return sum(self.distance_matrix[route[i]][route[i + 1]] for i in range(len(route) - 1))

    def _route_travel_seconds(self, route, exclude_dummy_start=True):
        if not route or len(route) < 2:
            return 0.0
        total = 0.0
        for i in range(0, len(route) - 1):
            if exclude_dummy_start and route[i] == 0:
                continue
            total += self.time_matrix[route[i]][route[i + 1]]
        return total

    def _is_route_feasible(self, vendor_nodes):
        """Check feasibility for a single route (vendor + depot windows)."""
        if not vendor_nodes:
            return False
        key = (tuple(vendor_nodes), tuple(self._get_route_depots(list(vendor_nodes))))
        cached = self._feasible_cache.get(key)
        if cached is not None:
            return cached
        route = self._build_route_with_depots(list(vendor_nodes))
        if not route:
            return False
        temp_solution = RouteSolution(
            routes=[route],
            vendors_df=self.vendors_df,
            distance_matrix=self.distance_matrix,
            time_matrix=self.time_matrix,
            capacity_matrix=self.capacity_matrix,
            loading_matrix=self.loading_matrix,
            linear_length_matrix=self.linear_length_matrix,
            service_time_matrix=self.service_time_matrix,
            max_capacity_kg=self.max_capacity_kg,
            max_volume=self.max_volume_vc,
            max_linear_length=self.max_linear_length_vc,
            discretization_constant=self.discretization_constant,
            min_date=self.min_date,
            max_driving_hours=self.max_driving_hours,
            evaluation_period=self.evaluation_period,
            allowed_early_hours=self.allowed_early_hours,
            allowed_late_hours=self.allowed_late_hours,
            depot_node_ids=list(self.depot_node_ids),
            vendor_node_ids=list(vendor_nodes),
            vendor_depot_map=self.vendor_depot_map,
            vendor_start_hr=self.vendor_start_hr,
            pickup_end_hr=self.pickup_end_hr,
            starting_depot=self.starting_depot,
            closing_depot=self.closing_depot,
            allow_night_wait=self.allow_night_wait
        )
        feasible = temp_solution.is_feasible(check_all=False)
        if len(self._feasible_cache) > 5000:
            self._feasible_cache.clear()
        self._feasible_cache[key] = feasible
        return feasible

    def _get_route_violations(self, vendor_nodes):
        if not vendor_nodes:
            return ['empty vendor set']
        route = self._build_route_with_depots(list(vendor_nodes))
        if not route:
            return ['empty route']
        temp_solution = RouteSolution(
            routes=[route],
            vendors_df=self.vendors_df,
            distance_matrix=self.distance_matrix,
            time_matrix=self.time_matrix,
            capacity_matrix=self.capacity_matrix,
            loading_matrix=self.loading_matrix,
            linear_length_matrix=self.linear_length_matrix,
            service_time_matrix=self.service_time_matrix,
            max_capacity_kg=self.max_capacity_kg,
            max_volume=self.max_volume_vc,
            max_linear_length=self.max_linear_length_vc,
            discretization_constant=self.discretization_constant,
            min_date=self.min_date,
            max_driving_hours=self.max_driving_hours,
            evaluation_period=self.evaluation_period,
            allowed_early_hours=self.allowed_early_hours,
            allowed_late_hours=self.allowed_late_hours,
            depot_node_ids=list(self.depot_node_ids),
            vendor_node_ids=list(vendor_nodes),
            vendor_depot_map=self.vendor_depot_map,
            vendor_start_hr=self.vendor_start_hr,
            pickup_end_hr=self.pickup_end_hr,
            starting_depot=self.starting_depot,
            closing_depot=self.closing_depot,
            allow_night_wait=self.allow_night_wait
        )
        temp_solution.is_feasible(check_all=False)
        return list(temp_solution._constraint_violations)

    def _to_naive(self, dt_value):
        if dt_value is None:
            return None
        if isinstance(dt_value, str):
            parsed = pd.to_datetime(dt_value, errors='coerce', utc=True)
            if pd.notna(parsed):
                ts = parsed.tz_convert(None)
                return ts.to_pydatetime()
            return None
        if isinstance(dt_value, pd.Timestamp):
            ts = dt_value.tz_convert(None) if dt_value.tzinfo is not None else dt_value
            return ts.to_pydatetime()
        if isinstance(dt_value, np.datetime64):
            return pd.to_datetime(dt_value).to_pydatetime()
        if isinstance(dt_value, datetime):
            return dt_value.replace(tzinfo=None)
        if hasattr(dt_value, 'tzinfo') and dt_value.tzinfo is not None:
            return dt_value.replace(tzinfo=None)
        return dt_value

    def _is_vendor(self, node_id):
        if self.vendor_node_ids:
            return node_id in self.vendor_node_ids
        return node_id != 0 and node_id not in self.depot_node_ids

    def _is_depot(self, node_id):
        return node_id == 0 or node_id in self.depot_node_ids

    def _build_time_feasible_routes(self, vendors, max_weight, max_volume, max_linear_length):
        """Build routes considering vendor earliest/latest arrival windows."""
        routes = []
        unrouted = vendors.copy()
        safety_counter = 0
        
        while unrouted:
            safety_counter += 1
            if safety_counter > max(10, len(vendors) * 5):
                print("⚠️  Safety break: initial route construction not converging.")
                break
            route = [0]  # Start from dummy node
            current_weight = 0
            current_volume = 0
            current_linear = 0
            current_time = self._to_naive(self.min_date) if self.min_date is not None else None
            route_bucket = None
            if current_time is None and self.evaluation_period:
                current_time = self._to_naive(self.evaluation_period[0])
            
            # For single-vendor routes, create direct route
            if len(unrouted) == 1:
                vendor = unrouted.pop(0)
                depots = self._get_route_depots([vendor])
                routes.append([0, vendor] + depots)
                continue

            # Greedy insertion by earliest feasible arrival
            initial_unrouted_count = len(unrouted)
            while unrouted:
                last_node = route[-1]
                
                # Find nearest unrouted vendor that fits capacity/time window
                best_vendor = None
                best_distance = float('inf')
                
                for vendor in unrouted:
                    vendor_bucket = self._get_vendor_time_bucket(vendor)
                    if route_bucket is not None and vendor_bucket is not None and vendor_bucket != route_bucket:
                        continue
                    vendor_weight = float(self.capacity_matrix[vendor])
                    vendor_volume = float(self.loading_matrix[vendor])
                    
                    # Check capacity
                    vendor_linear = float(self.linear_length_matrix[vendor])
                    if (current_weight + vendor_weight <= max_weight and 
                        current_volume + vendor_volume <= max_volume and
                        current_linear + vendor_linear <= max_linear_length):
                        # Check time window feasibility
                        earliest, latest = self._get_vendor_time_window(vendor)
                        if current_time is not None and earliest is not None:
                            travel_seconds = self.time_matrix[last_node][vendor]
                            arrival_time = self._to_naive(current_time + timedelta(seconds=travel_seconds))
                            if arrival_time < earliest:
                                arrival_time = earliest
                            if latest is not None and arrival_time > latest:
                                continue

                        distance = self.distance_matrix[last_node][vendor]
                        if distance < best_distance:
                            best_distance = distance
                            best_vendor = vendor
                
                if best_vendor is None:
                    break  # No more vendors fit in this route
                
                # Try adding vendor and check max_driving constraint
                test_vendors = [n for n in (route + [best_vendor]) if self._is_vendor(n)]
                test_route = route + [best_vendor] + self._get_route_depots(test_vendors)
                
                # Ensure vendor + depot window feasibility for this candidate route
                if not self._is_route_feasible(test_vendors):
                    best_vendor = None
                    break

                # Check max_driving if specified
                if self.max_driving_hours is not None:
                    # Reject if vendor time windows span too large for a single route
                    span_hours = self._get_route_time_span_hours(test_route)
                    if span_hours > self.max_driving_hours:
                        best_vendor = None
                        break

                    route_travel_seconds = self._route_travel_seconds(test_route, exclude_dummy_start=True)
                    route_travel_hours = route_travel_seconds / 3600.0
                    
                    num_stops = len([v for v in test_route if self._is_vendor(v)])
                    service_time_per_stop = self.service_time_matrix[1] / 60.0 if len(self.service_time_matrix) > 1 else 0
                    route_service_hours = num_stops * service_time_per_stop
                    total_time = route_travel_hours + route_service_hours
                    
                    # If adding this vendor violates max_driving, stop adding to this route
                    if total_time > self.max_driving_hours:
                        break
                
                # Add vendor to route and advance time
                route.append(best_vendor)
                if route_bucket is None:
                    route_bucket = self._get_vendor_time_bucket(best_vendor)
                current_weight += float(self.capacity_matrix[best_vendor])
                current_volume += float(self.loading_matrix[best_vendor])
                current_linear += float(self.linear_length_matrix[best_vendor])
                if current_time is not None:
                    travel_seconds = self.time_matrix[last_node][best_vendor]
                    current_time = self._to_naive(current_time + timedelta(seconds=travel_seconds))
                    earliest, _ = self._get_vendor_time_window(best_vendor)
                    if earliest is not None and current_time < earliest:
                        current_time = earliest
                    service_time_per_stop = self.service_time_matrix[1] / 60.0 if len(self.service_time_matrix) > 1 else 0
                    current_time = current_time + timedelta(hours=service_time_per_stop)
                unrouted.remove(best_vendor)
            
            # Close route by visiting required depots
            if len(route) > 1:  # Has at least one vendor
                route_vendors = [n for n in route if self._is_vendor(n)]
                route += self._get_route_depots(route_vendors)
                # Only keep route if feasible with depot windows
                if self._is_route_feasible(route_vendors):
                    routes.append(route)
                else:
                    # Split into single-vendor routes when group is infeasible
                    for v in route_vendors:
                        routes.append(self._build_route_with_depots([v]))
            else:
                # Edge case: no vendors could be added
                if unrouted:
                    vendor = unrouted.pop(0)
                    depots = self._get_route_depots([vendor])
                    routes.append([0, vendor] + depots)
            
            # Ensure progress if no vendors were removed in this pass
            if len(unrouted) == initial_unrouted_count and unrouted:
                vendor = unrouted.pop(0)
                routes.append(self._build_route_with_depots([vendor]))
        
        return routes
    
    def destroy(self, solution, operator, num_remove):
        """
        Destroy operators: remove vendors from solution.
        
        NOTE: These operators can remove any vendor from any route.
        
        Returns:
            RouteSolution: Partial solution
            list: Removed vendors
        """
        destroyed = solution.copy()
        removed = []
        
        if operator == 'random':
            removed = self.destroy_random(destroyed, num_remove)
        elif operator == 'worst_cost':
            removed = self.destroy_worst_cost(destroyed, num_remove)
        elif operator == 'shaw':
            removed = self.destroy_shaw(destroyed, num_remove)
        
        return destroyed, removed
    
    def destroy_random(self, solution, num_remove):
        """Random removal destroy operator."""
        all_vendors = []
        for route in solution.routes:
            all_vendors.extend([v for v in route if self._is_vendor(v)])
        
        removed = random.sample(all_vendors, min(num_remove, len(all_vendors)))
        
        # Remove from routes
        for idx, route in enumerate(solution.routes):
            vendors_only = [v for v in route if self._is_vendor(v) and v not in removed]
            solution.routes[idx] = self._build_route_with_depots(vendors_only)
        
        # Remove empty routes
        solution.routes = [r for r in solution.routes if len(r) > 1]
        solution.invalidate_cache()
        
        return removed
    
    def destroy_worst_cost(self, solution, num_remove):
        """Remove vendors with highest cost contribution."""
        vendor_costs = []
        
        for route in solution.routes:
            for i, vendor in enumerate(route):
                if not self._is_vendor(vendor):
                    continue
                
                # Calculate cost of removing this vendor
                prev_node = route[i - 1] if i > 0 else 0
                next_node = route[i + 1] if i < len(route) - 1 else self.vendor_depot_map.get(vendor, 0)
                
                current_cost = self.distance_matrix[prev_node][vendor] + self.distance_matrix[vendor][next_node]
                direct_cost = self.distance_matrix[prev_node][next_node]
                savings = current_cost - direct_cost
                
                vendor_costs.append((vendor, savings))
        
        # Sort by worst savings (highest cost)
        vendor_costs.sort(key=lambda x: x[1], reverse=True)
        removed = [v for v, _ in vendor_costs[:num_remove]]
        
        # Remove from routes
        for idx, route in enumerate(solution.routes):
            vendors_only = [v for v in route if self._is_vendor(v) and v not in removed]
            solution.routes[idx] = self._build_route_with_depots(vendors_only)
        
        solution.routes = [r for r in solution.routes if len(r) > 1]
        solution.invalidate_cache()
        
        return removed
    
    def destroy_shaw(self, solution, num_remove):
        """Shaw removal: remove similar vendors (by distance)."""
        # Pick random seed vendor
        all_vendors = []
        for route in solution.routes:
            all_vendors.extend([v for v in route if self._is_vendor(v)])
        
        if not all_vendors:
            return []
        
        seed = random.choice(all_vendors)
        
        # Calculate relatedness (inverse distance)
        relatedness = [(v, 1.0 / (self.distance_matrix[seed][v] + 1)) for v in all_vendors if v != seed]
        relatedness.sort(key=lambda x: x[1], reverse=True)
        
        removed = [seed] + [v for v, _ in relatedness[:num_remove-1]]
        
        # Remove from routes
        for idx, route in enumerate(solution.routes):
            vendors_only = [v for v in route if self._is_vendor(v) and v not in removed]
            solution.routes[idx] = self._build_route_with_depots(vendors_only)
        
        solution.routes = [r for r in solution.routes if len(r) > 1]
        solution.invalidate_cache()
        
        return removed
    
    def repair(self, solution, removed_vendors, operator):
        """
        Repair operators: reinsert removed vendors.
        
        NOTE: These operators can insert vendors into any route.
        
        Returns:
            RouteSolution: Repaired solution
        """
        if operator == 'greedy':
            return self.repair_greedy(solution, removed_vendors)
        elif operator == 'regret2':
            return self.repair_regret2(solution, removed_vendors)
        
        return solution
    
    def repair_greedy(self, solution, removed_vendors):
        """Greedy insertion: insert each vendor at best position."""
        service_time_per_stop = self.service_time_matrix[1] / 60.0 if len(self.service_time_matrix) > 1 else 0
        route_cache = []
        def _refresh_route_cache(route_idx):
            route = solution.routes[route_idx]
            route_vendors = self._route_vendors(route)
            depots = self._get_route_depots(route_vendors) if route_vendors else []
            route_full = route if len(route) > 1 else self._build_route_with_depots(route_vendors)
            route_travel_seconds = 0.0
            route_distance = 0.0
            for i in range(0, len(route_full) - 1):
                route_travel_seconds += self.time_matrix[route_full[i]][route_full[i + 1]]
                route_distance += self.distance_matrix[route_full[i]][route_full[i + 1]]
            route_weight, route_volume, route_linear = solution.get_route_capacity_usage(route_idx)
            route_cache[route_idx] = {
                'vendors': route_vendors,
                'depots': depots,
                'travel_seconds': route_travel_seconds,
                'distance': route_distance,
                'weight': route_weight,
                'volume': route_volume,
                'linear': route_linear,
            }
        for route_idx in range(len(solution.routes)):
            route_cache.append({})
            _refresh_route_cache(route_idx)
        for vendor in removed_vendors:
            best_cost = float('inf')
            best_route_idx = -1
            best_position = -1
            vendor_bucket = self._get_vendor_time_bucket(vendor)
            
            # Try inserting in existing routes
            for route_idx, route in enumerate(solution.routes):
                cache = route_cache[route_idx]
                route_bucket = self._get_route_time_bucket(route)
                if route_bucket is not None and vendor_bucket is not None and vendor_bucket != route_bucket:
                    continue

                route_vendors = cache['vendors']
                depots = cache['depots']
                current_cost = cache['distance']
                # Limit insertion positions to nearest K (optional)
                positions = list(range(0, len(route_vendors) + 1))
                if self.insertion_top_k is not None and len(route_vendors) + 1 > self.insertion_top_k:
                    scored = []
                    for pos in positions:
                        prev_node = 0 if pos == 0 else route_vendors[pos - 1]
                        if pos < len(route_vendors):
                            next_node = route_vendors[pos]
                        else:
                            next_node = depots[0] if depots else 0
                        delta_distance = (
                            self.distance_matrix[prev_node][vendor]
                            + self.distance_matrix[vendor][next_node]
                            - self.distance_matrix[prev_node][next_node]
                        )
                        scored.append((delta_distance, pos))
                    scored.sort(key=lambda x: x[0])
                    positions = [p for _, p in scored[:self.insertion_top_k]]
                for pos in positions:
                    candidate_vendors = route_vendors[:pos] + [vendor] + route_vendors[pos:]
                    prev_node = 0 if pos == 0 else route_vendors[pos - 1]
                    if pos < len(route_vendors):
                        next_node = route_vendors[pos]
                    else:
                        next_node = depots[0] if depots else 0
                    delta_distance = (
                        self.distance_matrix[prev_node][vendor]
                        + self.distance_matrix[vendor][next_node]
                        - self.distance_matrix[prev_node][next_node]
                    )

                    # Check capacity
                    route_weight, route_volume, route_linear = cache['weight'], cache['volume'], cache.get('linear', 0.0)
                    vehicle_idx = route_idx
                    max_weight = self.max_capacity_kg[vehicle_idx] if vehicle_idx < len(self.max_capacity_kg) else float('inf')
                    max_volume = self.max_ldms_vc[vehicle_idx] if vehicle_idx < len(self.max_ldms_vc) else float('inf')
                    max_linear = self.max_linear_length_vc[vehicle_idx] if vehicle_idx < len(self.max_linear_length_vc) else float('inf')
                    if route_weight + self.capacity_matrix[vendor] > max_weight:
                        continue
                    if route_volume + self.loading_matrix[vendor] > max_volume:
                        continue
                    if route_linear + self.linear_length_matrix[vendor] > max_linear:
                        continue
                    
                    # Enforce vendor + depot window feasibility
                    if not self._is_route_feasible(candidate_vendors):
                        continue

                    # Check max_driving constraint
                    if self.max_driving_hours is not None:
                        candidate_route = self._build_route_with_depots(candidate_vendors)
                        span_hours = self._get_route_time_span_hours(candidate_route)
                        if span_hours > self.max_driving_hours:
                            continue

                        route_travel_seconds = self._route_travel_seconds(candidate_route, exclude_dummy_start=True)
                        route_travel_hours = route_travel_seconds / 3600.0
                        num_stops = len([v for v in candidate_route if self._is_vendor(v)])
                        route_service_hours = num_stops * service_time_per_stop
                        total_time = route_travel_hours + route_service_hours
                        if total_time > self.max_driving_hours:
                            continue

                    insertion_cost = delta_distance
                    if insertion_cost < best_cost:
                        best_cost = insertion_cost
                        best_route_idx = route_idx
                        best_position = pos
            
            # Insert at best position or create new route
            if best_route_idx >= 0:
                current_vendors = self._route_vendors(solution.routes[best_route_idx])
                updated_vendors = current_vendors[:best_position] + [vendor] + current_vendors[best_position:]
                solution.routes[best_route_idx] = self._build_route_with_depots(updated_vendors)
                _refresh_route_cache(best_route_idx)
            else:
                # Create new route
                solution.routes.append(self._build_route_with_depots([vendor]))
                route_cache.append({})
                _refresh_route_cache(len(solution.routes) - 1)
        
        solution.invalidate_cache()
        return solution
    
    def repair_regret2(self, solution, removed_vendors):
        """Regret-2 insertion: prioritize vendors with large regret."""
        uninserted = set(removed_vendors)
        service_time_per_stop = self.service_time_matrix[1] / 60.0 if len(self.service_time_matrix) > 1 else 0
        route_cache = []
        def _refresh_route_cache(route_idx):
            route = solution.routes[route_idx]
            route_vendors = self._route_vendors(route)
            depots = self._get_route_depots(route_vendors) if route_vendors else []
            route_full = route if len(route) > 1 else self._build_route_with_depots(route_vendors)
            route_travel_seconds = 0.0
            route_distance = 0.0
            for i in range(0, len(route_full) - 1):
                route_travel_seconds += self.time_matrix[route_full[i]][route_full[i + 1]]
                route_distance += self.distance_matrix[route_full[i]][route_full[i + 1]]
            route_weight, route_volume, route_linear = solution.get_route_capacity_usage(route_idx)
            route_cache[route_idx] = {
                'vendors': route_vendors,
                'depots': depots,
                'travel_seconds': route_travel_seconds,
                'distance': route_distance,
                'weight': route_weight,
                'volume': route_volume,
                'linear': route_linear,
            }
        for route_idx in range(len(solution.routes)):
            route_cache.append({})
            _refresh_route_cache(route_idx)
        
        while uninserted:
            best_regret = -float('inf')
            best_vendor = None
            best_route_idx = -1
            best_position = -1
            
            for vendor in uninserted:
                # Find best and second-best insertion positions
                costs = []
                vendor_bucket = self._get_vendor_time_bucket(vendor)
                
                for route_idx, route in enumerate(solution.routes):
                    cache = route_cache[route_idx]
                    route_bucket = self._get_route_time_bucket(route)
                    if route_bucket is not None and vendor_bucket is not None and vendor_bucket != route_bucket:
                        continue

                    route_vendors = cache['vendors']
                    depots = cache['depots']
                    current_cost = cache['distance']
                    # Limit insertion positions to nearest K (optional)
                    positions = list(range(0, len(route_vendors) + 1))
                    if self.insertion_top_k is not None and len(route_vendors) + 1 > self.insertion_top_k:
                        scored = []
                        for pos in positions:
                            prev_node = 0 if pos == 0 else route_vendors[pos - 1]
                            if pos < len(route_vendors):
                                next_node = route_vendors[pos]
                            else:
                                next_node = depots[0] if depots else 0
                            delta_distance = (
                                self.distance_matrix[prev_node][vendor]
                                + self.distance_matrix[vendor][next_node]
                                - self.distance_matrix[prev_node][next_node]
                            )
                            scored.append((delta_distance, pos))
                        scored.sort(key=lambda x: x[0])
                        positions = [p for _, p in scored[:self.insertion_top_k]]
                    for pos in positions:
                        candidate_vendors = route_vendors[:pos] + [vendor] + route_vendors[pos:]
                        prev_node = 0 if pos == 0 else route_vendors[pos - 1]
                        if pos < len(route_vendors):
                            next_node = route_vendors[pos]
                        else:
                            next_node = depots[0] if depots else 0
                        delta_distance = (
                            self.distance_matrix[prev_node][vendor]
                            + self.distance_matrix[vendor][next_node]
                            - self.distance_matrix[prev_node][next_node]
                        )

                        # Check capacity
                        route_weight, route_volume, route_linear = cache['weight'], cache['volume'], cache.get('linear', 0.0)
                        vehicle_idx = route_idx
                        max_weight = self.max_capacity_kg[vehicle_idx] if vehicle_idx < len(self.max_capacity_kg) else float('inf')
                        max_volume = self.max_ldms_vc[vehicle_idx] if vehicle_idx < len(self.max_ldms_vc) else float('inf')
                        max_linear = self.max_linear_length_vc[vehicle_idx] if vehicle_idx < len(self.max_linear_length_vc) else float('inf')
                        if route_weight + self.capacity_matrix[vendor] > max_weight:
                            continue
                        if route_volume + self.loading_matrix[vendor] > max_volume:
                            continue
                        if route_linear + self.linear_length_matrix[vendor] > max_linear:
                            continue
                        
                        # Enforce vendor + depot window feasibility
                        if not self._is_route_feasible(candidate_vendors):
                            continue

                        # Check max_driving constraint
                        if self.max_driving_hours is not None:
                            candidate_route = self._build_route_with_depots(candidate_vendors)
                            span_hours = self._get_route_time_span_hours(candidate_route)
                            if span_hours > self.max_driving_hours:
                                continue

                            route_travel_seconds = self._route_travel_seconds(candidate_route, exclude_dummy_start=True)
                            route_travel_hours = route_travel_seconds / 3600.0
                            num_stops = len([v for v in candidate_route if self._is_vendor(v)])
                            route_service_hours = num_stops * service_time_per_stop
                            total_time = route_travel_hours + route_service_hours
                            if total_time > self.max_driving_hours:
                                continue

                        insertion_cost = delta_distance
                        costs.append((insertion_cost, route_idx, pos))
                
                if len(costs) >= 2:
                    costs.sort(key=lambda x: x[0])
                    regret = costs[1][0] - costs[0][0]  # Second best - best
                    
                    if regret > best_regret:
                        best_regret = regret
                        best_vendor = vendor
                        best_route_idx = costs[0][1]
                        best_position = costs[0][2]
                elif len(costs) == 1:
                    if best_vendor is None:
                        best_vendor = vendor
                        best_route_idx = costs[0][1]
                        best_position = costs[0][2]
            
            if best_vendor is None:
                # Create new route for remaining vendors
                for vendor in uninserted:
                    solution.routes.append(self._build_route_with_depots([vendor]))
                break
            
            current_vendors = self._route_vendors(solution.routes[best_route_idx])
            updated_vendors = current_vendors[:best_position] + [best_vendor] + current_vendors[best_position:]
            solution.routes[best_route_idx] = self._build_route_with_depots(updated_vendors)
            _refresh_route_cache(best_route_idx)
            uninserted.remove(best_vendor)
        
        solution.invalidate_cache()
        return solution
    
    def select_operator(self, operators):
        """Select operator based on adaptive weights."""
        total_weight = sum(op['weight'] for op in operators.values())
        rand = random.uniform(0, total_weight)
        
        cumulative = 0
        for name, op in operators.items():
            cumulative += op['weight']
            if rand <= cumulative:
                op['calls'] += 1
                return name
        
        return list(operators.keys())[0]
    
    def try_merge_routes(self, solution):
        """Try to merge compatible routes to reduce total number of vehicles."""
        merged_solution = solution.copy()
        improved = True
        
        while improved:
            improved = False
            routes = merged_solution.routes
            
            # Try all pairs of routes
            for i in range(len(routes)):
                if improved:
                    break
                for j in range(i + 1, len(routes)):
                    if improved:
                        break
                    
                    route_i = routes[i]
                    route_j = routes[j]
                    
                    # Skip if either route has no vendors
                    if not self._route_vendors(route_i) or not self._route_vendors(route_j):
                        continue

                    route_i_bucket = self._get_route_time_bucket(route_i)
                    route_j_bucket = self._get_route_time_bucket(route_j)
                    if route_i_bucket is not None and route_j_bucket is not None and route_i_bucket != route_j_bucket:
                        if self.merge_debug:
                            print(f'   - Merge rejected (time bucket mismatch): {route_i_bucket} vs {route_j_bucket}')
                        continue
                    
                    # Get vendors from both routes (exclude depot)
                    vendors_i = self._route_vendors(route_i)
                    vendors_j = self._route_vendors(route_j)
                    
                    # Try to merge route j into route i
                    # Test combined route: start → vendors_i → vendors_j → depots
                    combined_vendors = vendors_i + vendors_j
                    test_route = self._build_route_with_depots(combined_vendors)

                    # Reject if vendor time windows span too large for a single route
                    if self.max_driving_hours is not None:
                        span_hours = self._get_route_time_span_hours(test_route)
                        if span_hours > self.max_driving_hours:
                            if self.merge_debug:
                                print(f'   - Merge rejected (time span {span_hours:.1f}h > max {self.max_driving_hours}h)')
                            continue

                    # Check capacity constraints
                    total_weight = sum(self.capacity_matrix[v] for v in combined_vendors)
                    total_volume = sum(self.loading_matrix[v] for v in combined_vendors)
                    total_linear = sum(self.linear_length_matrix[v] for v in combined_vendors)
                    max_weight = max(self.max_capacity_kg) if self.max_capacity_kg else float('inf')
                    max_volume = max(self.max_ldms_vc) if self.max_ldms_vc else float('inf')
                    max_linear = max(self.max_linear_length_vc) if self.max_linear_length_vc else float('inf')

                    if total_weight > max_weight or total_volume > max_volume or total_linear > max_linear:
                        if self.merge_debug:
                            print(
                                f'   - Merge rejected (capacity): '
                                f'{total_weight:.0f}kg/{max_weight:.0f}kg, '
                                f'{total_volume:.2f}m³/{max_volume:.2f}m³, '
                                f'{total_linear:.2f}m/{max_linear:.2f}m'
                            )
                        continue

                    # Check max_driving constraint
                    if self.max_driving_hours is not None:
                        route_travel_seconds = self._route_travel_seconds(test_route, exclude_dummy_start=True)
                        route_travel_hours = route_travel_seconds / 3600.0
                        num_stops = len(combined_vendors)
                        service_time_per_stop = self.service_time_matrix[1] / 60.0 if len(self.service_time_matrix) > 1 else 0
                        route_service_hours = num_stops * service_time_per_stop
                        total_time = route_travel_hours + route_service_hours

                        if total_time > self.max_driving_hours:
                            if self.merge_debug:
                                print(f'   - Merge rejected (max driving): {total_time:.1f}h > {self.max_driving_hours}h')
                            continue

                    # Check route feasibility with time windows/depot rules
                    if not self._is_route_feasible(combined_vendors):
                        if self.merge_debug:
                            violations = self._get_route_violations(combined_vendors)
                            preview = '; '.join(violations[:3]) if violations else 'no details'
                            print(f'   - Merge rejected (route infeasible): {preview}')
                        continue
                    
                    # Merge is feasible! Replace routes
                    new_routes = []
                    for idx, route in enumerate(routes):
                        if idx == i:
                            new_routes.append(test_route)
                        elif idx == j:
                            continue  # Skip route j (merged into i)
                        else:
                            new_routes.append(route)
                    
                    merged_solution.routes = new_routes
                    merged_solution.invalidate_cache()
                    improved = True
                    break
        
        return merged_solution
    
    def update_operator_weights(self, destroy_op, repair_op, reward):
        """Update operator weights based on performance."""
        self.destroy_operators[destroy_op]['improvements'] += reward
        self.repair_operators[repair_op]['improvements'] += reward
        
        # Adaptive weight update every 100 iterations
        for operators in [self.destroy_operators, self.repair_operators]:
            for op in operators.values():
                if op['calls'] > 0:
                    op['weight'] = 0.8 * op['weight'] + 0.2 * (op['improvements'] / op['calls'])
