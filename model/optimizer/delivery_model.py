# Import necessary libraries
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

# Determine the project root directory
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

# Import required modules
from utils.project_utils import *
from ortools.linear_solver import pywraplp
from concurrent.futures import ProcessPoolExecutor

# Import metaheuristic solvers
try:
    from .alns_solver import ALNSSolver
    from .route_solution import RouteSolution
    from .local_search import LocalSearchOperators
    METAHEURISTIC_AVAILABLE = True
except ImportError:
    METAHEURISTIC_AVAILABLE = False


def _solve_alns_group(payload):
    group_solver = ALNSSolver(
        vendors_df=payload['vendors_df'],
        distance_matrix=payload['distance_matrix'],
        time_matrix=payload['time_matrix'],
        capacity_matrix=payload['capacity_matrix'],
        loading_matrix=payload['loading_matrix'],
        service_time_matrix=payload['service_time_matrix'],
        max_capacity_kg=payload['max_capacity_kg'],
        max_volume=payload['max_volume'],
        max_linear_length=payload['max_linear_length'],
        discretization_constant=payload['discretization_constant'],
        min_date=payload['min_date'],
        max_driving_hours=payload['max_driving_hours'],
        config=payload['alns_config'],
        evaluation_period=payload['evaluation_period'],
        allowed_early_hours=payload['allowed_early_hours'],
        allowed_late_hours=payload['allowed_late_hours'],
        vendor_ids=payload['vendor_ids'],
        depot_node_ids=payload.get('depot_node_ids'),
        vendor_node_ids=payload.get('vendor_ids'),
        vendor_depot_map=payload.get('vendor_depot_map')
    )
    group_solution = group_solver.solve(verbose=payload['verbose'])
    if not getattr(group_solver, 'initial_feasible', False):
        group_solution = LocalSearchOperators.improve_solution(group_solution, max_iterations=payload['ls_iters'])
    feasible = group_solution.is_feasible(check_all=True)
    return {
        'routes': group_solution.routes,
        'feasible': feasible,
        'violations': list(group_solution._constraint_violations),
    }

# Define the DeliveryOptimizer class
class DeliveryOptimizer:
    def __init__(self, evaluation_period, discretization_constant, time_expanded_network, time_expanded_network_index,
                 Tau_hours, distance_matrix, time_distance_matrix, disc_time_distance_matrix, capacity_matrix, loading_matrix,
                 max_capacity, max_volume, max_linear_length, max_driving, is_gap, mip_gap, maximum_minutes,
                 service_time_matrix=None, vendors_df=None, allowed_early_hours=12, allowed_late_hours=12,
                 depots_df=None, depot_node_ids=None, vendor_node_ids=None, vendor_depot_map=None):
        # Log information about the MIP model setup
        log.info('Defining MIP model... ')

        # Set problem-specific attributes
        self.evaluation_period = evaluation_period
        self.discretization_constant = discretization_constant
        self.time_expanded_network = time_expanded_network
        self.time_expanded_network_index = time_expanded_network_index
        self.Tau_hours = Tau_hours
        self.distance_matrix = distance_matrix
        self.time_distance_matrix = time_distance_matrix
        self.disc_time_distance_matrix = disc_time_distance_matrix
        self.capacity_matrix = capacity_matrix
        self.loading_matrix = loading_matrix
        self.service_time_matrix = service_time_matrix if service_time_matrix is not None else np.zeros(len(distance_matrix))
        self.vendors_df = vendors_df
        self.allowed_early_hours = allowed_early_hours
        self.allowed_late_hours = allowed_late_hours
        self.depots_df = depots_df
        self.depot_node_ids = depot_node_ids or []
        self.vendor_node_ids = vendor_node_ids or []
        self.vendor_depot_map = vendor_depot_map or {}

        # Calculate derived attributes
        self.max_driving_hours = max_driving
        self.des_max_driving = max_driving / discretization_constant
        self.length = len(self.distance_matrix)
        self.max_num_vehicles = self.length - 1

        # Log information about the problem size
        log.info('Number of nodes %i' % self.length)
        log.info('Solving for maximum number of %i vehicles...' % self.max_num_vehicles)

        # Set maximum capacity and load limits for vehicles
        self.max_capacity = max_capacity
        self.max_capacity_kg = [max_capacity * 1000] * self.max_num_vehicles
        self.max_volume = max_volume
        self.max_volume_vc = [max_volume] * self.max_num_vehicles
        self.max_linear_length = max_linear_length
        self.max_linear_length_vc = [max_linear_length] * self.max_num_vehicles
        
        # Service time per stop in hours (for time calculations)
        # If service_time_matrix is provided, extract the service time per stop in minutes (excluding depot at index 0)
        if self.service_time_matrix is not None and len(self.service_time_matrix) > 1:
            self.service_time_hours_per_stop = self.service_time_matrix[1] / 60.0  # Convert minutes to hours
        else:
            self.service_time_hours_per_stop = 0

        # Initialize solution containers
        self.connections_solution = None
        self.vehicles_solution = None
        self.used_metaheuristic = False
        self.last_constraint_violations = []

        # Create a solver instance with specified time limit
        self.model = pywraplp.Solver('DeliveryOptimizer', pywraplp.Solver.CBC_MIXED_INTEGER_PROGRAMMING)
        self.model.set_time_limit(maximum_minutes * 60 * 1000)  # milliseconds

        # Configure solver based on MIP gap
        self.is_gap = is_gap
        if is_gap == True:
            log.info('MIP GAP %s and maximum solving minutes %s...' % (mip_gap, maximum_minutes))
            self.solverParams = pywraplp.MPSolverParameters()
            self.solverParams.SetDoubleParam(self.solverParams.RELATIVE_MIP_GAP, mip_gap)


    def create_model(self, w):
        A_i, A_j, self.nodes = DeliveryOptimizer.nodes_range(self.time_expanded_network)
        all_duples, index_out, index_ins, index_zero_ins = DeliveryOptimizer.nodes_expanded_points(self.time_expanded_network)

        self._defining_variables()
        self._add_constraint_nodes()
        self._add_constraint_vehicle_routing(A_i, A_j, all_duples, index_out, index_ins, index_zero_ins)
        self._add_obj_function(w)

    def solve_model(self):        
        status = 1
        while status != 0:
            if  self.max_num_vehicles < self.length + 2 :
                if self.is_gap:
                    status = self.model.Solve(self.solverParams)
                else:
                    status = self.model.Solve()
                                
                self.max_num_vehicles += 2

                self.max_capacity_kg = [self.max_capacity*1000] * self.max_num_vehicles
                self.max_volume_vc = [self.max_volume] * self.max_num_vehicles
                self.max_linear_length_vc = [self.max_linear_length] * self.max_num_vehicles
            else:    
                print('No solution found, last num. vehicles considered:', self.max_num_vehicles)
                break

            # self.max_capacity_kg = [self.max_capacity_kg] * self.max_num_vehicles 
            # self.max_ldms = [self.max_ldms] * self.max_num_vehicles

        return status, self.x, self.y
    
    def solve_with_metaheuristic(self, w=0.5, max_iterations=1000, verbose=True):
        """
        Solve using ALNS metaheuristic instead of MIP.
        Much faster for large instances (50+ vendors).
        
        Args:
            w: Weight for objective (0.5 = balanced distance/vehicles)
            max_iterations: Maximum ALNS iterations
            verbose: Print progress information
            
        Returns:
            tuple: (status, x, y) where:
                - status: 0 if solution found, 2 if infeasible
                - x: Connection matrix (converted from routes)
                - y: Vehicle usage vector
        """
        if not METAHEURISTIC_AVAILABLE:
            print("⚠️  Metaheuristic solver not available. Using MIP instead.")
            return self.solve_model()
        
        if verbose:
            print('\n🚀 Using ALNS metaheuristic solver (fast mode)')
            print(f'   - Network size: {len(self.time_expanded_network)} arcs, {self.length} nodes')

        # Normalize requested loading columns to avoid missing vendor windows
        if self.vendors_df is not None:
            self.vendors_df.columns = [str(c).strip() for c in self.vendors_df.columns]
            if 'Requested Loading' not in self.vendors_df.columns and 'Requested Loading Date' in self.vendors_df.columns:
                self.vendors_df['Requested Loading'] = self.vendors_df['Requested Loading Date']
            if 'Requested Loading Date' not in self.vendors_df.columns and 'Requested Loading' in self.vendors_df.columns:
                self.vendors_df['Requested Loading Date'] = self.vendors_df['Requested Loading']
            if 'Requested Loading' in self.vendors_df.columns and 'Requested Loading Date' in self.vendors_df.columns:
                self.vendors_df['Requested Loading'] = self.vendors_df['Requested Loading'].fillna(
                    self.vendors_df['Requested Loading Date']
                )
            for col in ['Requested Loading', 'Requested Loading Date']:
                if col in self.vendors_df.columns:
                    self.vendors_df[col] = (
                        self.vendors_df[col]
                        .apply(lambda v: v.strip() if isinstance(v, str) else v)
                    )
        
        # Create ALNS solver with dynamically tuned parameters based on problem size
        vendors = max(0, self.length - 1)
        if vendors >= 60:
            min_removal, max_removal = 0.25, 0.55
            initial_T, cooling = 2500, 0.998
            ls_iters = 500
        elif vendors >= 30:
            min_removal, max_removal = 0.20, 0.50
            initial_T, cooling = 2000, 0.9975
            ls_iters = 350
        elif vendors >= 20:
            min_removal, max_removal = 0.15, 0.45
            initial_T, cooling = 1500, 0.997
            ls_iters = 250
        else:
            min_removal, max_removal = 0.12, 0.40
            initial_T, cooling = 1200, 0.996
            ls_iters = 200

        alns_config = {
            'max_iterations': max_iterations,
            'min_removal_size': min_removal,
            'max_removal_size': max_removal,
            'initial_temperature': initial_T,
            'cooling_rate': cooling,
            'merge_debug': bool(verbose),
            'depot_debug': bool(verbose),
            'enable_merge': False,
            'enable_split': False
        }
        
        # Parallel solve by time-window groups when available
        group_map = {}
        if self.vendors_df is not None and 'time_bucket' in self.vendors_df.columns:
            for i in range(len(self.vendors_df)):
                raw_bucket = self.vendors_df.iloc[i].get('time_bucket', '')
                bucket = str(raw_bucket).strip()
                if bucket:
                    node_id = self.vendors_df.iloc[i].get('node_id', i + 1)
                    group_map.setdefault(bucket, []).append(int(node_id))

        if len(group_map) > 1:
            if verbose:
                print(f'🔀 Parallel ALNS groups: {len(group_map)}')
            max_workers = min(len(group_map), max(1, (os.cpu_count() or 2) // 2))

            payloads = []
            for vendor_ids in group_map.values():
                group_size = len(vendor_ids)
                if group_size <= 3:
                    group_max_iterations = min(max_iterations, 400)
                    group_ls_iters = 30
                elif group_size <= 6:
                    group_max_iterations = min(max_iterations, 700)
                    group_ls_iters = 60
                elif group_size <= 10:
                    group_max_iterations = min(max_iterations, 1200)
                    group_ls_iters = 120
                else:
                    group_max_iterations = max_iterations
                    group_ls_iters = ls_iters
                payloads.append({
                    'vendors_df': self.vendors_df,
                    'distance_matrix': self.distance_matrix,
                    'time_matrix': self.time_distance_matrix,
                    'capacity_matrix': self.capacity_matrix,
                    'loading_matrix': self.loading_matrix,
                    'service_time_matrix': self.service_time_matrix,
                    'max_capacity_kg': self.max_capacity_kg,
                    'max_volume': self.max_volume_vc,
                    'max_linear_length': self.max_linear_length_vc,
                    'discretization_constant': self.discretization_constant,
                    'min_date': self.evaluation_period[0] if isinstance(self.evaluation_period, list) else self.evaluation_period,
                    'max_driving_hours': self.max_driving_hours,
                    'alns_config': {**alns_config, 'max_iterations': group_max_iterations},
                    'evaluation_period': self.evaluation_period,
                    'allowed_early_hours': self.allowed_early_hours,
                    'allowed_late_hours': self.allowed_late_hours,
                    'vendor_ids': vendor_ids,
                    'verbose': verbose,
                    'ls_iters': group_ls_iters,
                    'depot_node_ids': self.depot_node_ids,
                    'vendor_node_ids': self.vendor_node_ids,
                    'vendor_depot_map': self.vendor_depot_map
                })

            try:
                with ProcessPoolExecutor(max_workers=max_workers) as executor:
                    group_results = list(executor.map(_solve_alns_group, payloads))
            except Exception as e:
                if verbose:
                    print(f"⚠️ Parallel ALNS failed ({type(e).__name__}: {e}); retrying sequentially")
                group_results = [_solve_alns_group(p) for p in payloads]

            combined_routes = []
            combined_violations = []
            feasible = True
            for result in group_results:
                combined_routes.extend(result['routes'])
                combined_violations.extend(result['violations'])
                feasible = feasible and result['feasible']

            solution = RouteSolution(
                routes=combined_routes,
                vendors_df=self.vendors_df,
                distance_matrix=self.distance_matrix,
                time_matrix=self.time_distance_matrix,
                capacity_matrix=self.capacity_matrix,
                loading_matrix=self.loading_matrix,
                service_time_matrix=self.service_time_matrix,
                max_capacity_kg=self.max_capacity_kg,
                max_volume=self.max_volume_vc,
                max_linear_length=self.max_linear_length_vc,
                discretization_constant=self.discretization_constant,
                min_date=self.evaluation_period[0] if isinstance(self.evaluation_period, list) else self.evaluation_period,
                max_driving_hours=self.max_driving_hours,
                evaluation_period=self.evaluation_period,
                allowed_early_hours=self.allowed_early_hours,
                allowed_late_hours=self.allowed_late_hours,
                depot_node_ids=self.depot_node_ids,
                vendor_node_ids=self.vendor_node_ids,
                vendor_depot_map=self.vendor_depot_map
            )
            is_feasible = solution.is_feasible(check_all=True) if feasible else False
            solution._constraint_violations = combined_violations if combined_violations else list(solution._constraint_violations)
            status = 0 if is_feasible else 2
            self.last_constraint_violations = list(solution._constraint_violations)
        else:
            alns = ALNSSolver(
                vendors_df=self.vendors_df,
                distance_matrix=self.distance_matrix,
                time_matrix=self.time_distance_matrix,
                capacity_matrix=self.capacity_matrix,
                loading_matrix=self.loading_matrix,
                service_time_matrix=self.service_time_matrix,
                max_capacity_kg=self.max_capacity_kg,
                max_volume=self.max_volume_vc,
                max_linear_length=self.max_linear_length_vc,
                discretization_constant=self.discretization_constant,
                min_date=self.evaluation_period[0] if isinstance(self.evaluation_period, list) else self.evaluation_period,
                max_driving_hours=self.max_driving_hours,
                config=alns_config,
                evaluation_period=self.evaluation_period,
                allowed_early_hours=self.allowed_early_hours,
                allowed_late_hours=self.allowed_late_hours,
                depot_node_ids=self.depot_node_ids,
                vendor_node_ids=self.vendor_node_ids,
                vendor_depot_map=self.vendor_depot_map
            )
            
            # Solve with ALNS
            solution = alns.solve(verbose=verbose)
            
            # Apply local search improvement only if initial solution was not feasible
            if not getattr(alns, 'initial_feasible', False):
                if verbose:
                    print('   - Applying local search improvement...')
                solution = LocalSearchOperators.improve_solution(solution, max_iterations=ls_iters)
            
            # Check feasibility
            is_feasible = solution.is_feasible(check_all=True)
            status = 0 if is_feasible else 2
            self.last_constraint_violations = list(solution._constraint_violations)
        
        if verbose:
            if is_feasible:
                print(f'   ✓ Solution found: {solution.get_num_routes()} routes, {solution.evaluate():.0f} km')
            else:
                print(f'   ✗ Solution infeasible: {len(solution._constraint_violations)} violations')
                for violation in solution._constraint_violations[:5]:
                    print(f'     - {violation}')
                if len(solution._constraint_violations) > 5:
                    print(f'     ... and {len(solution._constraint_violations) - 5} more')
                
                if alns_config.get('enable_split', False):
                    # Smart fallback: Split only violated routes, keep feasible ones
                    print(f'   🔧 Fixing {len(solution._constraint_violations)} violated routes...')
                    violated_route_ids = set()
                    for violation in solution._constraint_violations:
                        # Extract route number from violation message
                        if 'Route' in violation:
                            try:
                                route_id = int(violation.split('Route')[1].split(':')[0].strip())
                                violated_route_ids.add(route_id)
                            except:
                                pass

                    def _vendor_row(node_id):
                        if self.vendors_df is None:
                            return None
                        if 'node_id' in self.vendors_df.columns:
                            match = self.vendors_df[self.vendors_df['node_id'] == int(node_id)]
                            return match.iloc[0] if not match.empty else None
                        idx = int(node_id) - 1
                        if 0 <= idx < len(self.vendors_df):
                            return self.vendors_df.iloc[idx]
                        return None

                    def _vendor_window(node_id):
                        row = _vendor_row(node_id)
                        if row is None:
                            return None, None
                        for raw in [row.get('Requested Loading', None), row.get('Requested Loading Date', None)]:
                            parsed = pd.to_datetime(raw, errors='coerce')
                            if pd.notna(parsed):
                                requested = parsed.to_pydatetime()
                                start = requested - timedelta(hours=float(self.allowed_early_hours))
                                end = requested + timedelta(hours=float(self.allowed_late_hours))
                                return start, end
                        return None, None

                    def _split_by_overlap(vendor_nodes):
                        entries = []
                        for v in vendor_nodes:
                            s, e = _vendor_window(v)
                            if s is None or e is None:
                                continue
                            entries.append((v, s, e))
                        if not entries:
                            return []
                        entries.sort(key=lambda x: x[1])
                        groups = []
                        current_end = None
                        current_group = []
                        for v, s, e in entries:
                            if current_end is None or s <= current_end:
                                current_end = e if current_end is None else max(current_end, e)
                                current_group.append(v)
                            else:
                                groups.append(list(current_group))
                                current_group = [v]
                                current_end = e
                        if current_group:
                            groups.append(list(current_group))
                        # Preserve original route order inside each group
                        ordered_groups = []
                        for group in groups:
                            ordered = [v for v in vendor_nodes if v in group]
                            if ordered:
                                ordered_groups.append(ordered)

                        # Verify disjoint group intervals by time; merge if overlapping
                        def _group_interval(group):
                            starts = []
                            ends = []
                            for v in group:
                                s, e = _vendor_window(v)
                                if s is None or e is None:
                                    continue
                                starts.append(s)
                                ends.append(e)
                            if not starts or not ends:
                                return None, None
                            return min(starts), max(ends)

                        merged_groups = []
                        prev_start = None
                        prev_end = None
                        for group in ordered_groups:
                            g_start, g_end = _group_interval(group)
                            if g_start is None or g_end is None:
                                merged_groups.append(group)
                                prev_start, prev_end = g_start, g_end
                                continue
                            if prev_end is None or g_start > prev_end:
                                merged_groups.append(group)
                                prev_start, prev_end = g_start, g_end
                            else:
                                if verbose:
                                    print(
                                        f"      ⚠️ Overlap detected between groups "
                                        f"[{prev_start}..{prev_end}] and [{g_start}..{g_end}]; merging"
                                    )
                                merged_groups[-1].extend(group)
                                prev_start, prev_end = _group_interval(merged_groups[-1])

                        return merged_groups

                    def _route_depots(vendor_nodes):
                        depot_deadlines = {}
                        for v in vendor_nodes:
                            depot_node = self.vendor_depot_map.get(v)
                            if depot_node is None:
                                continue
                            row = _vendor_row(v)
                            if row is None:
                                continue
                            for raw in [row.get('Requested Delivery', None), row.get('Requested Delivery Date', None)]:
                                parsed = pd.to_datetime(raw, errors='coerce')
                                if pd.notna(parsed):
                                    delivery_time = parsed.to_pydatetime()
                                    if depot_node not in depot_deadlines:
                                        depot_deadlines[depot_node] = delivery_time
                                    else:
                                        depot_deadlines[depot_node] = min(depot_deadlines[depot_node], delivery_time)
                                    break
                        depots = list(depot_deadlines.keys())
                        depots.sort(key=lambda d: depot_deadlines.get(d))
                        return depots

                    # Rebuild solution: keep good routes, split violated ones
                    new_routes = []
                    for route_idx, route in enumerate(solution.routes):
                        if route_idx in violated_route_ids:
                            vendors_in_route = [v for v in route if v in self.vendor_node_ids]
                            grouped = _split_by_overlap(vendors_in_route)
                            if not grouped and vendors_in_route:
                                grouped = [[v] for v in vendors_in_route]
                            for group in grouped:
                                # Re-run ALNS on each overlap group
                                group_solver = ALNSSolver(
                                    vendors_df=self.vendors_df,
                                    distance_matrix=self.distance_matrix,
                                    time_matrix=self.time_distance_matrix,
                                    capacity_matrix=self.capacity_matrix,
                                    loading_matrix=self.loading_matrix,
                                    service_time_matrix=self.service_time_matrix,
                                    max_capacity_kg=self.max_capacity_kg,
                                    max_volume=self.max_volume_vc,
                                    max_linear_length=self.max_linear_length_vc,
                                    discretization_constant=self.discretization_constant,
                                    min_date=self.evaluation_period[0] if isinstance(self.evaluation_period, list) else self.evaluation_period,
                                    max_driving_hours=self.max_driving_hours,
                                    config=alns_config,
                                    evaluation_period=self.evaluation_period,
                                    allowed_early_hours=self.allowed_early_hours,
                                    allowed_late_hours=self.allowed_late_hours,
                                    vendor_ids=group,
                                    depot_node_ids=self.depot_node_ids,
                                    vendor_node_ids=group,
                                    vendor_depot_map=self.vendor_depot_map
                                )
                                group_solution = group_solver.solve(verbose=verbose)
                                if not getattr(group_solver, 'initial_feasible', False):
                                    group_solution = LocalSearchOperators.improve_solution(group_solution, max_iterations=ls_iters)
                                if group_solution.is_feasible(check_all=True):
                                    for r in group_solution.routes:
                                        new_routes.append(list(r))
                                else:
                                    if verbose:
                                        print(f'      ⚠️ Group infeasible, splitting into single-vendor routes')
                                    for v in group:
                                        single_solver = ALNSSolver(
                                            vendors_df=self.vendors_df,
                                            distance_matrix=self.distance_matrix,
                                            time_matrix=self.time_distance_matrix,
                                            capacity_matrix=self.capacity_matrix,
                                            loading_matrix=self.loading_matrix,
                                            service_time_matrix=self.service_time_matrix,
                                            max_capacity_kg=self.max_capacity_kg,
                                            max_volume=self.max_volume_vc,
                                            max_linear_length=self.max_linear_length_vc,
                                            discretization_constant=self.discretization_constant,
                                            min_date=self.evaluation_period[0] if isinstance(self.evaluation_period, list) else self.evaluation_period,
                                            max_driving_hours=self.max_driving_hours,
                                            config=alns_config,
                                            evaluation_period=self.evaluation_period,
                                            allowed_early_hours=self.allowed_early_hours,
                                            allowed_late_hours=self.allowed_late_hours,
                                            vendor_ids=[v],
                                            depot_node_ids=self.depot_node_ids,
                                            vendor_node_ids=[v],
                                            vendor_depot_map=self.vendor_depot_map
                                        )
                                        single_solution = single_solver.solve(verbose=verbose)
                                        if not getattr(single_solver, 'initial_feasible', False):
                                            single_solution = LocalSearchOperators.improve_solution(single_solution, max_iterations=ls_iters)
                                        for r in single_solution.routes:
                                            new_routes.append(list(r))
                            if verbose:
                                print(f'      → Split Route {route_idx}: {len(vendors_in_route)} vendors → {len(grouped)} routes')
                        else:
                            # Keep feasible route as is
                            new_routes.append(route)

                    solution = RouteSolution(
                        routes=new_routes,
                        vendors_df=self.vendors_df,
                        distance_matrix=self.distance_matrix,
                        time_matrix=self.time_distance_matrix,
                        capacity_matrix=self.capacity_matrix,
                        loading_matrix=self.loading_matrix,
                        service_time_matrix=self.service_time_matrix,
                        max_capacity_kg=self.max_capacity_kg,
                        max_volume=self.max_volume_vc,
                        max_linear_length=self.max_linear_length_vc,
                        discretization_constant=self.discretization_constant,
                        min_date=self.evaluation_period[0] if isinstance(self.evaluation_period, list) else self.evaluation_period,
                        max_driving_hours=self.max_driving_hours,
                        evaluation_period=self.evaluation_period,
                        allowed_early_hours=self.allowed_early_hours,
                        allowed_late_hours=self.allowed_late_hours,
                        depot_node_ids=self.depot_node_ids,
                        vendor_node_ids=self.vendor_node_ids,
                        vendor_depot_map=self.vendor_depot_map
                    )
                else:
                    if verbose:
                        print('   ⚠️ Splitting disabled; returning infeasible solution as-is')

                is_feasible = solution.is_feasible(check_all=True)
                if alns_config.get('enable_split', False):
                    if is_feasible:
                        status = 0
                        print(f'   ✓ Fixed solution: {solution.get_num_routes()} routes')
                    else:
                        status = 2
                        print(f'   ✗ Still infeasible after fix!')
                else:
                    status = 0 if is_feasible else 2
                self.last_constraint_violations = list(solution._constraint_violations)
        
        # Convert route solution to MIP-style x and y matrices for compatibility
        x, y = self._convert_routes_to_mip_format(solution)
        
        # Store solution and mark as metaheuristic
        self.connections_solution = x
        self.vehicles_solution = y
        self.used_metaheuristic = True
        self.metaheuristic_solution = solution
        self.metaheuristic_objective = solution.evaluate()
        self.metaheuristic_routes = solution.get_num_routes()
        self.secs_taken = 0  # Metaheuristic doesn't track time separately
        
        return status, x, y
    
    def _convert_routes_to_mip_format(self, route_solution):
        """
        Convert route-based solution to MIP format for compatibility.
        
        Args:
            route_solution: RouteSolution object
            
        Returns:
            tuple: (x, y) matrices compatible with MIP output format
        """
        # Initialize connection matrix x[k][i][ti][j][tj]
        # For metaheuristic, use simplified structure with only time index 0
        max_k = max(len(route_solution.routes), self.max_num_vehicles)
        x = {}
        
        for k in range(max_k):
            x[k] = {}
            for i in range(self.length):
                x[k][i] = {}
                x[k][i][0] = {}  # Only time index 0 for metaheuristic
                for j in range(self.length):
                    x[k][i][0][j] = {}
                    x[k][i][0][j][0] = 0  # Only time index 0 for metaheuristic
        
        # Fill in connections from routes
        for k, route in enumerate(route_solution.routes):
            for idx in range(len(route) - 1):
                i = route[idx]
                j = route[idx + 1]
                # Use time index 0 for simplicity (metaheuristic doesn't use time expansion)
                x[k][i][0][j][0] = 1
        
        # Create vehicle usage vector y[k]
        y = {}
        for k in range(max_k):
            y[k] = 1 if k < len(route_solution.routes) else 0
        
        return x, y

    def nodes_range(time_expanded_network):
        """Static function: Gives out the feasible space of the nodes given the Time-Expanded Network.
        Input: 
        time_expanded_network: Time-Expanded Network.
        Output: 
        A_i: Leaving Nodes to consider.
        A_j: Arriving Nodes to consider.
        all_duples: List of of the duples (Node-i,Time-t) on the Time-Expanded Network. Includes arrival and leaving points.
        index_zero_ins: Extracts the arrival time-network points to the recipient.
        """
        A_i=[]
        A_j=[]
        for j in range(len(time_expanded_network)):
            A_i.append(time_expanded_network[j][0][0])
            A_j.append(time_expanded_network[j][1][0])
        all_nodes = list(set(A_i+A_j))

        return A_i, A_j, all_nodes

    def nodes_expanded_points(time_expanded_network):
        duples_1 = []
        duples_2 = []
        for j in range(len(time_expanded_network)):
                duples_1.append( time_expanded_network[j][0] )
                duples_2.append( time_expanded_network[j][1] )
        all_duples = duples_1 + duples_2
        all_duples = list(set(map(tuple, all_duples)))

        index_ins = {}
        index_out = {}
        for dups in all_duples:
            index_ins[dups] = []
            index_out[dups] = []
            k = 0
            for elem in time_expanded_network:
                if tuple(elem[1]) == dups:
                    if len(elem[0]) != 0:
                        index_ins[ dups ].append(k )
                elif tuple(elem[0]) == dups:
                    if len(elem[1]) != 0:
                        index_out[ dups ].append(k )            
                k += 1        
        index_zero_ins = []
        for vals in all_duples:
            if vals[0] == 0:        
                for j in index_ins[vals]:
                    index_zero_ins.append( [ [time_expanded_network[j][0][0], time_expanded_network[j][0][1]], [time_expanded_network[j][1][0], time_expanded_network[j][1][1]]] )
        
        return all_duples, index_out, index_ins, index_zero_ins

    def _defining_variables(self):
        log.info('Defining variables...')
        self.x = [[[[[self.model.IntVar(0,1,'') for t in self.time_expanded_network_index] for j in range(0, self.length)] for t in self.time_expanded_network_index] for i in range(0, self.length)] for k in range(self.max_num_vehicles)]         
        self.y = [self.model.IntVar(0,1,'') for k in range(self.max_num_vehicles)] 
    
    def _add_constraint_nodes(self):
        log.info('Adding Nodes Constraints...')        
        for i in self.nodes:
            if i != 0:                
                options_i = A_index(self.time_expanded_network, i, 'delta_out') 
                self.model.Add( sum( self.x[k][ options_i[j][0][0] ][ options_i[j][0][1] ][ options_i[j][1][0] ][ options_i[j][1][1] ] for j in range(len(options_i)) for k in range(self.max_num_vehicles) ) == 1 )

    def _add_constraint_vehicle_routing(self, A_i, A_j, all_duples, index_out, index_ins, index_zero_ins):       
        log.info('Adding Vehicle Routing Constraints...')    
        for k in range(self.max_num_vehicles):
            self.model.Add( sum( self.x[k][ j[0][0] ][ j[0][1] ][ j[1][0] ][ j[1][1] ] for j in index_zero_ins ) == self.y[k] )  # every vehicle has to be used and return to 0
            self.model.Add( sum( self.capacity_matrix[self.time_expanded_network[j][0][0]] * self.x[k][ self.time_expanded_network[j][0][0] ][ self.time_expanded_network[j][0][1] ][ self.time_expanded_network[j][1][0] ][ self.time_expanded_network[j][1][1] ] for j in range(0, len(self.time_expanded_network))) <= self.y[k] * self.max_capacity_kg[k] ) 
            
            # Driving time cap (hours) per vehicle
            # Exclude depot→vendor edges (source node = 0) to only count return travel + inter-vendor
            # Add service time: count non-depot nodes visited
            travel_time = sum( 
                ( self.time_distance_matrix[self.time_expanded_network[j][0][0]][self.time_expanded_network[j][1][0]] / 3600.0 ) * 
                self.x[k][ self.time_expanded_network[j][0][0] ][ self.time_expanded_network[j][0][1] ][ self.time_expanded_network[j][1][0] ][ self.time_expanded_network[j][1][1] ] 
                for j in range(0, len(self.time_expanded_network)) 
                if self.time_expanded_network[j][0][0] != 0  # Skip depot→vendor edges
            )
            service_time = sum(
                self.service_time_hours_per_stop * self.x[k][ self.time_expanded_network[j][0][0] ][ self.time_expanded_network[j][0][1] ][ self.time_expanded_network[j][1][0] ][ self.time_expanded_network[j][1][1] ]
                for j in range(0, len(self.time_expanded_network))
                if self.time_expanded_network[j][0][0] != 0  # Service time only for non-depot sources
            )
            self.model.Add( travel_time + service_time <= self.y[k] * self.max_driving_hours )
            for vals in all_duples:
                if vals[0] != 0:
                    self.model.Add( sum( self.x[k][ self.time_expanded_network[j][0][0] ][ self.time_expanded_network[j][0][1] ][ self.time_expanded_network[j][1][0] ][ self.time_expanded_network[j][1][1] ] for j in index_out[vals]) - sum( self.x[k][ self.time_expanded_network[j][0][0] ][self.time_expanded_network[j][0][1]][self.time_expanded_network[j][1][0]][self.time_expanded_network[j][1][1]] for j in index_ins[vals]) == 0 )                

    def _add_obj_function(self, w):
        log.info('Number of constraints = ' + str( self.model.NumConstraints() ) ) 
        log.info('Solving time-extended network MIP model...')

        number_nodes = len(self.distance_matrix)
        P = 0
        for i in range(number_nodes):
            P += self.distance_matrix[i][0]
        P = P/number_nodes
        # print('P', P)
        # print('w',w)

        self.model.Minimize( w * self.model.Sum( self.x[k][ self.time_expanded_network[i][0][0] ][ self.time_expanded_network[i][0][1] ][ self.time_expanded_network[i][1][0] ][ self.time_expanded_network[i][1][1] ]*self.distance_matrix[ self.time_expanded_network[i][0][0] ][ self.time_expanded_network[i][1][0] ] for i in range(len(self.time_expanded_network)) for k in range(self.max_num_vehicles)  ) +
                    (1 - w) * P *self.model.Sum( self.y[k] for k in range(self.max_num_vehicles))) 

        # self.model.Minimize( self.model.Sum( self.x[k][ self.time_expanded_network[i][0][0] ][ self.time_expanded_network[i][0][1] ][ self.time_expanded_network[i][1][0] ][ self.time_expanded_network[i][1][1] ]*self.distance_matrix[ self.time_expanded_network[i][0][0] ][ self.time_expanded_network[i][1][0] ] for i in range(len(self.time_expanded_network)) for k in range(self.max_num_vehicles)  ) ) 

        # self.model.Minimize( self.model.Sum( self.y[k] for k in range(self.max_num_vehicles)) ) 


    def read_solution(self, solution_path):
        current_solution = np.load(solution_path, allow_pickle=True)
        re_dict = current_solution.tolist()
        return re_dict

    def print_solution(self, connections_matrix, index_solution, discretization_constant, min_date, Tau_hours, distance_matrix, 
                   time_distance_matrix, disc_time_distance_matrix, capacity_matrix, loading_matrix, vendors_df=None):        
        r = {}
        index={}
        dist={}
        driv={}
        cargo={}
        load={}

        total_dist=0
        total_driv=0
        total_cargo=0
        total_load=0
        vehicle_id=1
        route_number = 1  # Sequential route counter
        for k in index_solution:
            vehicle_id = k
            
            # Extract active arcs for this vehicle from time-expanded network
            active_arcs = []
            for arc in self.time_expanded_network:
                i, ti, j, tj = arc[0][0], arc[0][1], arc[1][0], arc[1][1]
                if connections_matrix[k][i][ti][j][tj] > 0.5:
                    active_arcs.append([i, ti, j, tj])
            
            r[vehicle_id] = np.array(active_arcs)

            # Skip vehicles with no routes
            if len(r[vehicle_id]) == 0:
                continue
            
            print('Route %i:' % route_number)
            
            index[vehicle_id] = []
            dist[vehicle_id] = []
            driv[vehicle_id] = []
            cargo[vehicle_id] = []
            load[vehicle_id] = []
            
            # For pickup problem: find starting vendors (never count depot as starting point)
            # Check if there are arcs leaving the depot
            depot_destinations = set()
            for arc in r[vehicle_id]:
                if arc[0] == 0:  # Arc leaving depot
                    depot_destinations.add(arc[2])
            
            if depot_destinations:
                # If depot has outgoing arcs, start from those vendors (not depot)
                starting_nodes = depot_destinations
            else:
                # No depot arcs - find nodes with outgoing but no incoming arcs (excluding depot)
                all_origins = set([arc[0] for arc in r[vehicle_id] if arc[0] != 0])
                all_destinations = set([arc[2] for arc in r[vehicle_id] if arc[2] != 0])
                starting_nodes = all_origins - all_destinations
                
                # If still no clear starting nodes, use all non-depot origins
                if len(starting_nodes) == 0:
                    starting_nodes = set([arc[0] for arc in r[vehicle_id] if arc[0] != 0])
            
            # print(f'  Found {len(starting_nodes)} starting point(s): {sorted([int(n) for n in starting_nodes])}')
            
            # Process each starting node as a separate route segment
            route_segments = []
            route_segments_with_times = []  # Store (node, departure_time, arrival_time, travel_hours) tuples
            for start_node in sorted(starting_nodes):
                segment = []
                segment_with_times = []
                prev_index = start_node
                prev_time = None
                
                # Find the time index for the starting node
                for arc in r[vehicle_id]:
                    if arc[2] == start_node:
                        prev_time = arc[3]
                        break
                
                # Get actual departure datetime from the starting node
                from datetime import datetime, timedelta
                if prev_time is not None:
                    current_datetime, time_str = inv_date_index(discretization_constant, prev_time, min_date, Tau_hours)
                    # For the first node, this is departure time (no arrival since we start here)
                    segment_with_times.append((prev_index, time_str, None, 0))
                    # Ensure current_datetime is a proper datetime object
                    if not isinstance(current_datetime, datetime):
                        # Parse the time string if needed
                        current_datetime = datetime.strptime(time_str, '%Y-%m-%d at %H:%M')
                
                segment.append(prev_index)
                
                # Check if there's an arc from depot to this starting node
                # If so, add its distance/time to the stats
                for arc in r[vehicle_id]:
                    if arc[0] == 0 and arc[2] == start_node:
                        # Found depot -> starting_node arc, include it in statistics
                        dist[vehicle_id].append(distance_matrix[0][start_node])
                        driv[vehicle_id].append(time_distance_matrix[0][start_node] / 3600)  # Convert seconds to hours
                        break
                
                # Follow the route until we reach depot (node 0) or a cycle
                visited = set()
                current_time = current_datetime  # Track actual clock time as we travel
                
                while prev_index != 0 and prev_index not in visited:
                    visited.add(prev_index)
                    
                    # Add cargo/loading from the current node (where we're picking up)
                    # This should only be added once per vendor node visited
                    if prev_index != 0:  # Not depot
                        cargo[vehicle_id].append(capacity_matrix[prev_index])
                        load[vehicle_id].append(loading_matrix[prev_index])
                    
                    # Find next arc from this node
                    found_next = False
                    for arc in r[vehicle_id]:
                        if arc[0] == prev_index:
                            forw_index = arc[2]
                            forw_time = arc[3]
                            
                            # Get actual travel time in hours from time_distance_matrix (stored in seconds)
                            travel_time_hours = time_distance_matrix[prev_index][forw_index] / 3600  # Convert seconds to hours
                            
                            # Calculate actual arrival time = departure + travel time
                            arrival_time = current_time + timedelta(hours=float(travel_time_hours))
                            arrival_str = arrival_time.strftime('%Y-%m-%d at %H:%M')
                            
                            segment.append(forw_index)
                            segment_with_times.append((forw_index, None, arrival_str, travel_time_hours))
                            index[vehicle_id].append(forw_index)
                            dist[vehicle_id].append(distance_matrix[prev_index][forw_index])
                            driv[vehicle_id].append(travel_time_hours)  # Already converted to hours above
                            
                            prev_index = forw_index
                            current_time = arrival_time  # Update current time for next leg
                            found_next = True
                            break
                    
                    if not found_next:
                        break
                
                route_segments.append(segment)
                route_segments_with_times.append(segment_with_times)
            
            # Display all route segments and identify valid pickup routes
            valid_routes = []
            invalid_routes = []
            
            for seg_idx, (segment, segment_times) in enumerate(zip(route_segments, route_segments_with_times), 1):
                if len(segment) > 1:
                    if segment[-1] == 0:
                        valid_routes.append(segment)
                        print(f'\n  ┌─ 📦 Route Timeline')
                        # Display detailed route with departure/arrival times and travel durations
                        for idx, (node, depart_str, arrival_str, travel_hours) in enumerate(segment_times):
                            if node == 0:
                                node_name = 'Start'
                                location_info = ''
                            else:
                                node_name = f'Node {int(node)}'
                                location_info = ''
                                if vendors_df is not None:
                                    try:
                                        vendor_row = None
                                        if 'node_id' in vendors_df.columns:
                                            match = vendors_df[vendors_df['node_id'] == int(node)]
                                            if not match.empty:
                                                vendor_row = match.iloc[0]
                                        elif int(node) <= len(vendors_df):
                                            vendor_row = vendors_df.iloc[int(node) - 1]
                                        if vendor_row is not None:
                                            node_name = str(vendor_row.get('vendor Name', node_name)).strip()
                                            city = str(vendor_row.get('Vendor City', '')).strip()
                                            postcode = str(vendor_row.get('Vendor Postcode', '')).strip()
                                            if city and postcode:
                                                location_info = f' ({city}, PLZ {postcode})'
                                    except Exception:
                                        pass
                            
                            if idx == 0:
                                # First node - departure point
                                print(f'  │  🚚 Pickup: {node_name}{location_info}')
                                print(f'  │     Departs: {depart_str}')
                            else:
                                # Subsequent nodes - show arrival after travel
                                travel_str = f' ({travel_hours:.1f} hrs travel)' if travel_hours > 0 else ''
                                if idx < len(segment_times) - 1:
                                    print(f'  │  ⬇️  Stop at: {node_name}{location_info}')
                                    print(f'  │     Arrives: {arrival_str}{travel_str}')
                                else:
                                    print(f'  │  🏁 Final Destination: {node_name}{location_info}')
                                    print(f'  └─    Arrives: {arrival_str}{travel_str}')
                    else:
                        invalid_routes.append(segment)
                        print(f'\n  ⚠️  INVALID ROUTE (disconnected):')
                        for idx, (node, time_str) in enumerate(segment_times):
                            node_name = 'Depot' if node == 0 else f'Vendor {int(node)}'
                            if idx == 0:
                                print(f'     • Start: {node_name} at {time_str}')
                            else:
                                print(f'     • Stop: {node_name} at {time_str}')
                        print(f'     ⚠️  Route does NOT end at depot')
                else:
                    print(f'  ⚠️  Isolated node: {segment[0]}')
            
            # Summary
            if valid_routes:
                vendors_in_valid_routes = set()
                for route in valid_routes:
                    vendors_in_valid_routes.update([int(n) for n in route if n != 0])
                print(f'  Summary: {len(valid_routes)} valid route(s) serving vendors {sorted(vendors_in_valid_routes)}')
            
            if invalid_routes:
                vendors_in_invalid_routes = set()
                for route in invalid_routes:
                    vendors_in_invalid_routes.update([int(n) for n in route if n != 0])
                print(f'  ⚠️  WARNING: {len(invalid_routes)} disconnected cycle(s) involving vendors {sorted(vendors_in_invalid_routes)}')
            
            if len(index[vehicle_id]) == 0:
                index[vehicle_id].append(list(starting_nodes)[0])

            total_dist += sum(dist[vehicle_id])
            total_driv += sum(driv[vehicle_id])
            total_cargo += sum(cargo[vehicle_id]) 
            total_load += sum(load[vehicle_id]) 

            print(f' - Distance Route {route_number}:           {int(sum(dist[vehicle_id]))} km')
            print(f' - Total Driving Time Route {route_number}: {round(sum(driv[vehicle_id]), 1)} hrs')
            print(f' - Cargo Route {route_number}:              {int(sum(cargo[vehicle_id]))} kg')
            print(f' - L. Meters Route {route_number}:          {round(sum(load[vehicle_id]),2)} m3')
            route_number += 1  # Increment for next route
            print('')

        print('')
        if total_dist != 0:
            total_dist = int(round(total_dist,0))
            print('Total Distance %i km'%total_dist)
            print('Total Cargo %i kg'%total_cargo)
            print('Total Loading Meters %i m3'%round(total_load,2))

        # Compute distance saved compared to trivial solution
        # Trivial solution: each vendor sends one vehicle directly to depot
        # All vendor nodes are nodes 1, 2, 3, ... (depot is node 0)
        num_nodes = len(distance_matrix)
        vendor_nodes = range(1, num_nodes)  # Exclude depot (node 0)
        
        before_dist = 0
        for vendor_node in vendor_nodes:
            before_dist += distance_matrix[vendor_node][0]
        before_dist = int(round(before_dist,0))

        if total_dist == 0:
            print('No more routes to optimize.')
        else:
            print('Trivial distance:', before_dist, 'km')
            if before_dist > 0:
                print('Distance reduction achieved:', round(( (before_dist - total_dist) /before_dist) *100,2), '% \n \n \n')
            else:
                print('Distance reduction: N/A (trivial distance is 0)\n \n \n')

    def print_status(self, status, x, y):
        # Handle both CBC solver objects and plain dictionaries
        if isinstance(x, dict):
            self.connections_solution = x
            self.vehicles_solution = y
        else:
            self.connections_solution = SolVal(x)
            self.vehicles_solution = SolVal(y)

        # Check if metaheuristic was used
        is_metaheuristic = getattr(self, 'used_metaheuristic', False)

        if status != pywraplp.Solver.INFEASIBLE:
            if not is_metaheuristic and status != pywraplp.Solver.OPTIMAL:
                logger.warning("Due to time constraint, the closer solution for optimality is given...")

            op_num_vehicles = int(sum(self.vehicles_solution.values()) if isinstance(self.vehicles_solution, dict) else sum(self.vehicles_solution))
            
            if is_metaheuristic:
                obj_value = self.metaheuristic_objective
                log.info('Metaheuristic solution found.')
            else:
                obj_value = round(self.model.Objective().Value(), 2)
                log.info('Optimal solution found.')
                
            log.info('Objective value = ' + str(obj_value))
            log.info('Number of nodes = ' + str(len(self.distance_matrix)))
            log.info('Number of vehicles selected = ' + str(op_num_vehicles))
            log.info('Total Distance = ' + str(obj_value))
            log.info('Total Cargo = ' + str(int(sum(self.capacity_matrix))))
            log.info('Total Loading Meters = ' + str(int(sum(self.loading_matrix))))
            
            if not is_metaheuristic:
                self.secs_taken = round(int(self.model.wall_time())/1000, 2)
                log.info('Problem solved in %s seconds' % self.secs_taken)
                log.info('Problem solved in %s minutes' % str((self.secs_taken)/60))
        else:
            logger.warning("The problem is infeasible.")
            if not is_metaheuristic:
                print(self.time_expanded_network)
                obj_value = round(self.model.Objective().Value(), 2)
                print(obj_value)           


        if status != pywraplp.Solver.INFEASIBLE:
            # For metaheuristic, extract vehicle indices from the solution
            if is_metaheuristic:
                index_solution = [k for k, v in self.vehicles_solution.items() if v > 0.5]
            else:
                index_solution = information_index(self.y)
                
            DeliveryOptimizer.print_solution(self, self.connections_solution, index_solution, self.discretization_constant, 
                                    self.min_date, self.Tau_hours, self.distance_matrix,
                                    self.time_distance_matrix, self.disc_time_distance_matrix, self.capacity_matrix, 
                                    self.loading_matrix, self.vendors_df)

    def save_solution(self, path):
        # Store solution ----------------------------------------------------------------------------------------
        solution_dict = {}
        # Model
        solution_dict['period'] = self.evaluation_period 
        solution_dict['discretization_constant'] = self.discretization_constant
        solution_dict['distance_matrix'] = self.distance_matrix
        solution_dict['disc_time_distance_matrix'] = self.disc_time_distance_matrix        
        # Solution
        solution_dict['time_needed'] = self.secs_taken
        
        # Handle both MIP and metaheuristic solutions
        if getattr(self, 'used_metaheuristic', False):
            solution_dict['index_solution'] = [k for k, v in self.vehicles_solution.items() if v > 0.5]
        else:
            solution_dict['index_solution'] = information_index(self.y)
        solution_dict['connections_matrix'] = self.connections_solution
        # Truck
        solution_dict['capacity_matrix'] = self.capacity_matrix
        solution_dict['loading_matrix'] = self.loading_matrix
        # Time 
        solution_dict['min_date'] = self.min_date 
        solution_dict['Tau_hours'] = self.Tau_hours 
        solution_dict['time_expand_network'] = self.time_expanded_network
        solution_dict['time_expand_network_index'] = self.time_expanded_network_index

        #moment = datetime.datetime.now().strftime("%Y_%m_%d-%I_%M_%S_%p")        
        if not isinstance(self.evaluation_period, list):
            self.evaluation_period = self.evaluation_period.strftime('%Y-%m-%d')
            self.evaluation_period = [self.evaluation_period, self.evaluation_period]
            t_0 = datetime.datetime.strptime(self.evaluation_period[0], '%Y-%m-%d')
            t_0 = t_0.strftime('%m%d%Y')

            t_1 = datetime.datetime.strptime(self.evaluation_period[1], '%Y-%m-%d')
            t_1 = t_1.strftime('%m%d%Y')
        else:
            t_0 = datetime.datetime.strptime(self.evaluation_period[0], '%Y-%m-%d %H:%M:%S')
            t_0 = t_0.strftime('%m%d%Y')

            t_1 = datetime.datetime.strptime(self.evaluation_period[1], '%Y-%m-%d %H:%M:%S')
            t_1 = t_1.strftime('%m%d%Y')

        # Save
        save_name = os.path.join(path, 'solution' + str(self.discretization_constant) + '_' + str(t_0) + '-' + str(t_1) + '.npy' )        
        print('file saved:,', save_name)
        np.save(save_name, solution_dict)



     # Quantum Annealing:
    def _defining_hamiltonian_variables(self, A_i, A_j):
        log.info('Defining Hamiltonian Variables...')
            
        self.x_array = Array.create('connection', shape=(self.max_num_vehicles, len(np.unique(A_i)), len(self.time_expanded_network_index), len(np.unique(A_j)), \
                                        len(self.time_expanded_network_index)), vartype="BINARY")
        self.y_array = Array.create('vehicle', shape=(self.max_num_vehicles,), vartype="BINARY")

        self.capacity_matrix = np.array(self.capacity_matrix, dtype=np.int)          
        self.q_range = range(0, math.ceil(1 + math.log(self.max_capacity*1000 -
                                                       np.min(self.capacity_matrix[np.nonzero(self.capacity_matrix)]) , 2)))       
                     
        self.loading_matrix = np.array(self.loading_matrix, dtype=np.float32)
        self.m_range = range(0, math.ceil(1 + math.log(self.max_ldms -
                                                       np.min(self.loading_matrix[np.nonzero(self.loading_matrix)]) , 2))) 
        
        self.lambda_q = Array.create('slack_q', shape=(max(self.q_range) + 1, self.max_num_vehicles), vartype="BINARY")
        self.lambda_m = Array.create('slack_m', shape=(max(self.m_range) + 1, self.max_num_vehicles), vartype="BINARY")


    def _add_hamiltonian_min_function(self):
        log.info('Adding Hamiltonian Minimization Function (H0)...')    
        w=0.9
        
        H_0 = w*sum(self.x_array[k][ self.time_expanded_network[i][0][0] ][ int(self.time_expanded_network[i][0][1]) ][ int(self.time_expanded_network[i][1][0]) ][ int(self.time_expanded_network[i][1][1]) ] * \
                    self.distance_matrix[ self.time_expanded_network[i][0][0] ][ self.time_expanded_network[i][1][0] ] for i in range(len(self.time_expanded_network)) for k in range(self.max_num_vehicles)  ) + \
        (1-w)*self.P*sum(self.y_array[k] for k in range(self.max_num_vehicles))
        return H_0


    def _add_hamiltonian_constraints_nodes(self):
        log.info('Adding Hamiltonian Constraints Nodes (H1)...') 
        Nodes_0 = list(set(self.nodes) - {0})
        H_1 = {}
        for i in Nodes_0:
            options_i = A_index(self.time_expanded_network, i, 'delta_out') 
            H_1[i] = Constraint( (sum( self.x_array[k][ int(options_i[j][0][0]) ][ int(options_i[j][0][1]) ][ int(options_i[j][1][0]) ][ int(options_i[j][1][1]) ] \
                                for j in range(len(options_i)) for k in range(self.max_num_vehicles)) - 1 )**2, 'Route_Depo_End_Constraints')
        # Merging the constraint into one sum
        H_1 = sum(H_1[i] for i in Nodes_0)
        return H_1


    def _add_hamiltonian_constraint_vehicle_routing(self, all_duples, index_out, index_ins, index_zero_ins):       
        log.info('Adding Hamiltonian Vehicle Routing Constraints (H2, H3, H4, H5)...')    

        H_2 = {}
        H_3 = {}
        H_4 = {}
        H_5 = {}
        for k in range(self.max_num_vehicles):
            H_2[k] = {}
            for vals in all_duples:
                if vals[0] != 0:
                    H_2[k][vals] = Constraint( (sum( self.x_array[k][ int(self.time_expanded_network[j][0][0]) ][ int(self.time_expanded_network[j][0][1]) ][ int(self.time_expanded_network[j][1][0]) ][ int(self.time_expanded_network[j][1][1]) ] \
                                                for j in index_out[vals]) - \
                                                sum( self.x_array[k][ int(self.time_expanded_network[j][0][0]) ][ int(self.time_expanded_network[j][0][1]) ][ int(self.time_expanded_network[j][1][0]) ][ int(self.time_expanded_network[j][1][1]) ] \
                                                    for j in index_ins[vals]) )**2, 'Equilibrium_Constraints')
                    
            H_3[k] = Constraint( (sum( self.x_array[k][ int(j[0][0]) ][ int(j[0][1]) ][ int(j[1][0]) ][ int(j[1][1]) ] \
                                    for j in index_zero_ins) - self.y_array[k])**2, 'Vehicle_Return_Constraints')  # every vehicle has to be used and return to 0    

            H_4[k] = Constraint( (sum(self.capacity_matrix[ int(self.time_expanded_network[j][0][0]) ] * self.x_array[k][ int(self.time_expanded_network[j][0][0]) ][ int(self.time_expanded_network[j][0][1]) ][ int(self.time_expanded_network[j][1][0]) ][ int(self.time_expanded_network[j][1][1]) ] \
                                           for j in range(0,len(self.time_expanded_network)) ) + \
                         sum(self.lambda_q[l][k]*(2**((l))) for l in self.q_range) - \
                                  self.y_array[k]*self.max_capacity*1000 )**2, 'Capacity_Countraints')
                            
            H_5[k] = Constraint( (sum( self.loading_matrix[ int(self.time_expanded_network[j][0][0]) ] * self.x_array[k][ int(self.time_expanded_network[j][0][0]) ][ int(self.time_expanded_network[j][0][1]) ][ int(self.time_expanded_network[j][1][0]) ][ int(self.time_expanded_network[j][1][1]) ] \
                                           for j in range(0,len(self.time_expanded_network)) ) + \
                         sum(self.lambda_m[l][k]*(2**((l))) for l in self.m_range) - self.y_array[k]*self.max_ldms )**2, 'Loading_Countraints')
                      
        
        # Merging the constraint into sums
        H_2 = sum(H_2[k][vals] for k in range(self.max_num_vehicles) for vals in all_duples if vals[0] != 0 )

        H_3 = sum(H_3[k] for k in range(self.max_num_vehicles))
        H_4 = sum(H_4[k] for k in range(self.max_num_vehicles))
        H_5 = sum(H_5[k] for k in range(self.max_num_vehicles)) 
        return H_2, H_3, H_4, H_5
            

    def create_hamiltonian_model(self):
        log.info('Creating Hamiltonian Model (HF)...')   
        A_i, A_j, self.nodes = DeliveryOptimizer.nodes_range(self.time_expanded_network)
        all_duples, index_out, index_ins, index_zero_ins = DeliveryOptimizer.nodes_expanded_points(self.time_expanded_network)

        self._defining_hamiltonian_variables(A_i, A_j)
        H_0 = self._add_hamiltonian_min_function()
        H_1 = self._add_hamiltonian_constraints_nodes()
        H_2, H_3, H_4, H_5 = self._add_hamiltonian_constraint_vehicle_routing(all_duples, index_out, index_ins, index_zero_ins)

        # Penalization Terms
        B = Placeholder('B')        
        HF = H_0 + B*(H_1 + H_2 + H_3 + H_4 + H_5)

        log.info('Compiling HF...') 
        HF_model = HF.compile()
        
        log.info('HF Finished...') 
        return HF_model

    def print_solution_summary(self, x, y):
        """Print decision variables x and y in a friendly, readable format."""
        
        print('\n' + '='*80)
        print(' '*25 + '📊 OPTIMIZATION SOLUTION SUMMARY')
        print('='*80)
        
        # Get solution values - handle both CBC solver objects and plain dictionaries
        if isinstance(x, dict):
            connections = x
            vehicles = y
        else:
            connections = SolVal(x)
            vehicles = SolVal(y)
        
        # Print y variables (vehicle usage)
        print('\n🚛 VEHICLE USAGE (y variables):')
        print('-'*80)
        vehicles_used = []
        for k in range(len(vehicles)):
            if vehicles[k] > 0.5:
                vehicles_used.append(k)
                print(f'   ✓ y[{k}] = {int(vehicles[k])}  → Vehicle {k} is USED')
            else:
                print(f'   ✗ y[{k}] = {int(vehicles[k])}  → Vehicle {k} is NOT USED')
        
        print(f'\n   📦 Total vehicles in solution: {len(vehicles_used)}')
        
        # Print x variables (arc assignments) for each used vehicle
        print('\n\n🗺️  ROUTE ASSIGNMENTS (x variables):')
        print('='*80)
        
        for k in vehicles_used:
            print(f'\n🚚 Vehicle {k}:')
            print('-'*80)
            
            active_arcs = []
            
            # Check if metaheuristic was used (stores arcs at time 0)
            is_metaheuristic = getattr(self, 'used_metaheuristic', False)
            
            if is_metaheuristic:
                # For metaheuristic: iterate through all node pairs at time 0
                for i in range(self.length):
                    for j in range(self.length):
                        if i != j and connections[k][i][0][j][0] > 0.5:
                            active_arcs.append((i, 0, j, 0))
            else:
                # For MIP: iterate through time-expanded network
                for arc in self.time_expanded_network:
                    i, ti, j, tj = arc[0][0], arc[0][1], arc[1][0], arc[1][1]
                    if connections[k][i][ti][j][tj] > 0.5:
                        active_arcs.append((i, ti, j, tj))
            
            if not active_arcs:
                print('   (No active arcs)')
                continue
            
            # Display arcs with time conversion
            for idx, (i, ti, j, tj) in enumerate(active_arcs, 1):
                # Convert time indices to readable dates
                _, time_origin = inv_date_index(self.discretization_constant, ti, self.min_date, self.Tau_hours)
                _, time_dest = inv_date_index(self.discretization_constant, tj, self.min_date, self.Tau_hours)
                
                node_origin = "Depot" if i == 0 else f"Vendor {i}"
                node_dest = "Depot" if j == 0 else f"Vendor {j}"
                
                print(f'\n   Arc {idx}: x[{k}][{i}][{ti}][{j}][{tj}] = 1')
                print(f'   ├─ Origin: {node_origin} at {time_origin}')
                print(f'   └─ Destination: {node_dest} at {time_dest}')
            
            # Analyze route structure by building the actual path
            print(f'\n   Route Analysis:')
            
            # Build adjacency list
            arc_dict = {}
            for i, ti, j, tj in active_arcs:
                arc_dict[(i, ti)] = (j, tj)
            
            # Find all nodes visited
            all_nodes = set([arc[0] for arc in active_arcs] + [arc[2] for arc in active_arcs])
            all_vendors = sorted([n for n in all_nodes if n != 0])
            print(f'   ├─ Nodes visited: Depot + Vendors {all_vendors}')
            
            # Try to trace the complete route
            route_path = []
            # Look for arc starting from depot
            depot_start_arc = None
            for (i, ti), (j, tj) in arc_dict.items():
                if i == 0:
                    depot_start_arc = (i, ti)
                    break
            
            if depot_start_arc:
                # Trace route starting from depot
                current = depot_start_arc
                visited = set()
                route_path.append(current[0])  # Start node
                
                while current in arc_dict and current not in visited:
                    visited.add(current)
                    next_node, next_time = arc_dict[current]
                    route_path.append(next_node)
                    current = (next_node, next_time)
                
                # Display the route
                route_str = ' → '.join(['Depot' if n == 0 else f'V{n}' for n in route_path])
                print(f'   ├─ Complete path: {route_str}')
                
                # Classify route type
                if route_path[0] == 0 and route_path[-1] == 0:
                    print(f'   └─ ⚠️  DELIVERY PROBLEM: Round-trip starting from Depot')
                    print(f'      Note: For pickup problem, vehicles should START at vendors, not depot')
                elif route_path[0] != 0 and route_path[-1] == 0:
                    print(f'   └─ ✓ PICKUP ROUTE: Starts at Vendor {route_path[0]}, ends at Depot')
                else:
                    print(f'   └─ ⚠️  Incomplete route or cycle')
            else:
                # No depot start - look for vendor starts
                origins = set([arc[0] for arc in active_arcs])
                destinations = set([arc[2] for arc in active_arcs])
                starting_nodes = origins - destinations
                ending_nodes = destinations - origins
                
                if starting_nodes and 0 in ending_nodes:
                    vendor_starts = [n for n in starting_nodes if n != 0]
                    if vendor_starts:
                        print(f'   ├─ Starting vendors: {sorted(vendor_starts)}')
                        print(f'   ├─ Ending at: Depot')
                        print(f'   └─ ✓ PICKUP ROUTE(S): Vendor(s) → Depot')
                else:
                    print(f'   └─ ⚠️  Disconnected arcs or cycle')
        
        print('\n' + '='*80)
        print(' '*30 + 'END OF SOLUTION')
        print('='*80 + '\n')

    def plot_routes(self, x, y, show_plot=True, save_path=None):
        """Plot the optimized routes on an interactive map using Folium and OSMnx.
        
        Args:
            x: Decision variable for arc assignments
            y: Decision variable for vehicle usage
            show_plot: Whether to open the map in browser (default: True)
            save_path: Path to save the HTML map (optional, defaults to routes_map.html)
        """
        try:
            import folium
            from folium import plugins
        except ImportError:
            print('⚠️  Folium not installed. Install with: pip install folium')
            return
        
        try:
            import osmnx as ox
        except ImportError:
            print('⚠️  OSMnx not installed. Install with: pip install osmnx')
            return
        
        # Get solution values - handle both CBC solver objects and plain dictionaries
        if isinstance(x, dict):
            connections = x
            vehicles = y
        else:
            connections = SolVal(x)
            vehicles = SolVal(y)
        
        # Find used vehicles
        vehicles_used = [k for k in range(len(vehicles)) if vehicles[k] > 0.5]
        log.info(f"📊 DEBUG: vehicles_used from y = {vehicles_used}")
        log.info(f"📊 DEBUG: y length={len(vehicles)} sample={list(vehicles)[:5]}")
        
        # Extract coordinates from vendors_df
        if self.vendors_df is None:
            print('⚠️  No vendor data available for plotting')
            return
        
        # Build coordinate mapping: node_id -> (lat, lon) and node info
        coords = {}
        node_info = {}

        # Dummy start node (0)
        dummy_lat = None
        dummy_lon = None
        if self.depots_df is not None and len(self.depots_df) > 0:
            dummy_lat = self.depots_df.iloc[0].get('recipient_latitude', None)
            dummy_lon = self.depots_df.iloc[0].get('recipient_longitude', None)
        if dummy_lat is None or dummy_lon is None:
            dummy_lat = 47.6062  # Seattle
            dummy_lon = -122.3321
        coords[0] = (dummy_lat, dummy_lon)
        node_info[0] = {'name': 'Route Start', 'city': '', 'country': '', 'type': 'start'}

        # Depots (multiple)
        if self.depots_df is not None:
            for _, row in self.depots_df.iterrows():
                node_id = int(row.get('node_id', 0))
                depot_lat = row.get('recipient_latitude', None)
                depot_lon = row.get('recipient_longitude', None)
                if depot_lat is None or depot_lon is None:
                    continue
                depot_city = row.get('Recipient City', 'Depot')
                depot_country = row.get('Recipient Country Name', '')
                coords[node_id] = (depot_lat, depot_lon)
                node_info[node_id] = {
                    'name': f"Depot ({depot_city})",
                    'city': depot_city,
                    'country': depot_country,
                    'type': 'depot'
                }

        # Vendors
        for _, row in self.vendors_df.iterrows():
            node_id = int(row.get('node_id', 0))
            vendor_lat = row.get('vendor_latitude', None)
            vendor_lon = row.get('vendor_longitude', None)
            vendor_name = row.get('vendor Name', f'Vendor {node_id}')
            vendor_city = row.get('Vendor City', 'Unknown')
            vendor_plz = row.get('Vendor Postcode', 'N/A')
            
            if vendor_lat is not None and vendor_lon is not None:
                coords[node_id] = (vendor_lat, vendor_lon)
                node_info[node_id] = {
                    'name': vendor_name,
                    'city': vendor_city,
                    'plz': vendor_plz,
                    'type': 'vendor'
                }

        depot_count = len(self.depots_df) if self.depots_df is not None else 0
        print(f'📊 Total nodes with coordinates: {len(coords)} (Depots: {depot_count}, Vendors: {len(self.vendors_df)})')
        
        vendor_nodes = set(self.vendor_node_ids or [])
        depot_nodes = set(self.depot_node_ids or [])
        vendor_row_by_node = {}
        for _, row in self.vendors_df.iterrows():
            node_id = row.get('node_id', None)
            if pd.notna(node_id):
                vendor_row_by_node[int(node_id)] = row
        depot_row_by_node = {}
        if self.depots_df is not None:
            for _, row in self.depots_df.iterrows():
                node_id = row.get('node_id', None)
                if pd.notna(node_id):
                    depot_row_by_node[int(node_id)] = row
        
        if len(coords) < 2:
            print('⚠️  Insufficient coordinate data for plotting')
            return
        
        # Calculate center and bounds of map automatically from data
        all_lats = [coord[0] for coord in coords.values()]
        all_lons = [coord[1] for coord in coords.values()]
        center_lat = sum(all_lats) / len(all_lats)
        center_lon = sum(all_lons) / len(all_lons)
        
        # Calculate bounds for auto-zoom
        min_lat, max_lat = min(all_lats), max(all_lats)
        min_lon, max_lon = min(all_lons), max(all_lons)
        
        # Create folium map with automatic centering
        m = folium.Map(
            location=[center_lat, center_lon],
            tiles='OpenStreetMap',
            control_scale=True,
            zoom_control=True,
            scrollWheelZoom=True,
            dragging=True,
            prefer_canvas=True
        )
        
        # Fit map to show all points with extra padding for better overview
        m.fit_bounds([[min_lat, min_lon], [max_lat, max_lon]], padding=[100, 100])
        
        # Define elegant color palette for routes (high saturation, distinct hues)
        colors = [
            '#E74C3C',  # Vibrant Red
            '#3498DB',  # Bright Blue
            '#2ECC71',  # Emerald Green
            '#F39C12',  # Golden Orange
            '#9B59B6',  # Purple
            '#1ABC9C',  # Turquoise
            '#E67E22',  # Dark Orange
            '#34495E',  # Slate Blue
            '#C0392B',  # Dark Red
            '#16A085',  # Sea Green
            '#D35400',  # Burnt Orange
            '#8E44AD',  # Vivid Purple
            '#2980B9',  # Ocean Blue
            '#27AE60',  # Green
            '#D68910',  # Brown Orange
            '#CA6F1E',  # Tan Orange
            '#C70039',  # Crimson
            '#0099CC',  # Cyber Blue
            '#33CC33',  # Lime Green
            '#FF9900',  # Web Orange
        ]
        
        # Extract routes for each vehicle (excluding depot→vendor arcs) and calculate stats
        routes = {}
        route_stats = {}  # Store vehicle statistics
        
        # Check if metaheuristic was used (stores arcs at time 0)
        is_metaheuristic = getattr(self, 'used_metaheuristic', False)
        use_direct_routes = is_metaheuristic and getattr(self, 'metaheuristic_solution', None) is not None
        log.info(f"📊 DEBUG: used_metaheuristic={is_metaheuristic}, has_meta_solution={getattr(self, 'metaheuristic_solution', None) is not None}")
        if use_direct_routes:
            raw_routes = {k: list(route) for k, route in enumerate(self.metaheuristic_solution.routes)}
            routes = {
                k: route for k, route in raw_routes.items()
                if route and any(node in vendor_nodes for node in route)
            }
            vehicles_used = list(routes.keys())
            log.info(f"📊 DEBUG: meta routes count={len(routes)}, vehicles_used set to {vehicles_used}")
            log.info(f"📊 DEBUG: meta routes preview={list(routes.values())[:3]}")

        if not vehicles_used and use_direct_routes and routes:
            vehicles_used = list(routes.keys())
            log.info(f"📊 DEBUG: vehicles_used repaired from routes={vehicles_used}")
            print(f"📊 DEBUG: vehicles_used repaired from routes={vehicles_used}")

        if not vehicles_used:
            log.warning('⚠️  No vehicles used in solution - nothing to plot')
            log.info(f"📊 DEBUG: routes keys={list(routes.keys())}")
            return
        
        for k in vehicles_used:
            route_arcs = []
            
            if use_direct_routes:
                route_path = routes.get(k, [])
                if route_path and route_path[0] != 0:
                    route_path = [0] + route_path
                # Remove dummy start node if it appears mid-route
                if route_path:
                    route_path = [
                        n for idx, n in enumerate(route_path)
                        if not (n == 0 and idx > 0)
                    ]
                # Display routes should start at first vendor (no depot start)
                display_path = route_path[1:] if route_path and route_path[0] == 0 else route_path
                routes[k] = display_path
                log.info(f"📊 DEBUG: route_path for vehicle {k} = {route_path}")
                log.info(f"📊 DEBUG: display_path for vehicle {k} = {display_path}")
                # Enhanced depot visit logging (detect repeats and mid-route depot/start usage)
                try:
                    depot_nodes_set = set(self.depot_node_ids or [])
                    depot_visits = [n for n in route_path if n in depot_nodes_set]
                    depot_names = [node_info.get(n, {}).get('name', f'Depot {n}') for n in depot_visits]
                    if depot_visits:
                        counts = {}
                        for n in depot_visits:
                            counts[n] = counts.get(n, 0) + 1
                        repeated = {n: c for n, c in counts.items() if c > 1}
                        if repeated:
                            repeated_names = ", ".join(
                                f"{node_info.get(n, {}).get('name', f'Depot {n}')} x{c}"
                                for n, c in repeated.items()
                            )
                            log.warning(f"⚠️  Route {k}: depot visited multiple times: {repeated_names}")
                        log.info(f"📦 Route {k}: depot visits in order: {depot_names}")
                    # Dummy start node should only be at route start
                    if len(route_path) > 1 and 0 in route_path[1:]:
                        log.warning(f"⚠️  Route {k}: dummy start node appears mid-route: {route_path}")
                except Exception as _:
                    pass
                route_path = display_path
            elif is_metaheuristic:
                # For metaheuristic: iterate through all node pairs at time 0
                for i in range(self.length):
                    for j in range(self.length):
                        if i != j and connections[k][i][0][j][0] > 0.5:
                            # Skip depot→vendor arcs (vehicles start at vendors, not depot)
                            if not (i == 0 and j != 0):
                                route_arcs.append((i, j))
            else:
                # For MIP: iterate through time-expanded network
                for arc in self.time_expanded_network:
                    i, ti, j, tj = arc[0][0], arc[0][1], arc[1][0], arc[1][1]
                    if connections[k][i][ti][j][tj] > 0.5:
                        # Skip depot→vendor arcs (vehicles start at vendors, not depot)
                        if not (i == 0 and j != 0):
                            route_arcs.append((i, j))
            
            # Build route path by following arcs (starting from vendors)
            if use_direct_routes:
                pass
            elif route_arcs:
                # Create adjacency dict
                arc_dict = {arc[0]: arc[1] for arc in route_arcs}
                
                # Find starting nodes (vendors that have no incoming arcs from other vendors)
                destinations = set(arc[1] for arc in route_arcs)
                origins = set(arc[0] for arc in route_arcs)
                starting = list(origins - destinations) if origins - destinations else [arc[0] for arc in route_arcs if arc[0] != 0]
                
                # Trace route from each starting vendor
                route_path = []
                for start in starting:
                    if start == 0:  # Skip depot as starting point
                        continue
                    current = start
                    visited = set()
                    path = [current]
                    while current in arc_dict and current not in visited:
                        visited.add(current)
                        current = arc_dict[current]
                        path.append(current)
                    route_path.extend(path)
                
                # Remove duplicates while preserving order
                seen = set()
                route_path = [x for x in route_path if not (x in seen or seen.add(x))]
                
                # Additional pass: remove consecutive duplicates (shouldn't happen but failsafe)
                if route_path:
                    cleaned_route = [route_path[0]]
                    for i in range(1, len(route_path)):
                        if route_path[i] != route_path[i-1]:
                            cleaned_route.append(route_path[i])
                    route_path = cleaned_route
                
                routes[k] = route_path
            elif not use_direct_routes:
                continue
                
            # Calculate vehicle statistics
            if not route_path or not any(n in vendor_nodes for n in route_path):
                continue

            vendors_visited = [n for n in route_path if n in vendor_nodes] if vendor_nodes else [n for n in route_path if n != 0]
            unique_vendors = []
            seen_vendors = set()
            for v in vendors_visited:
                if v not in seen_vendors:
                    seen_vendors.add(v)
                    unique_vendors.append(v)

            total_cargo = sum(self.capacity_matrix[v] for v in unique_vendors if v < len(self.capacity_matrix))
            total_loading = sum(self.loading_matrix[v] for v in unique_vendors if v < len(self.loading_matrix))
            total_distance = 0
            segments = []  # Store segment details for route card display

            def _to_naive(dt_value):
                if dt_value is None:
                    return None
                if isinstance(dt_value, pd.Timestamp):
                    ts = dt_value.tz_convert(None) if dt_value.tzinfo is not None else dt_value
                    return ts.to_pydatetime()
                if hasattr(dt_value, 'tzinfo') and dt_value.tzinfo is not None:
                    return dt_value.replace(tzinfo=None)
                return dt_value

            # Determine route baseline time: optimize within vendor + depot windows
            base_dt = None
            first_vendor_id = next((n for n in route_path if n in vendor_nodes), None)
            if first_vendor_id is not None and self.vendors_df is not None:
                try:
                    vendor_row = vendor_row_by_node.get(int(first_vendor_id))
                    if vendor_row is None:
                        raise ValueError("Vendor row not found for node")
                    raw_candidates = [
                        vendor_row.get('Requested Loading', None),
                        vendor_row.get('Requested Loading Date', None),
                        vendor_row.get('Requested Delivery', None),
                        vendor_row.get('Requested Delivery Date', None),
                    ]
                    first_vendor_requested = None
                    for raw_dt in raw_candidates:
                        parsed = pd.to_datetime(raw_dt, errors='coerce', utc=True)
                        if pd.notna(parsed):
                            first_vendor_requested = _to_naive(parsed)
                            break

                    # Compute route duration (travel + service) to reach depot
                    route_duration_seconds = 0
                    for i in range(1, len(route_path)):
                        prev_node = route_path[i - 1]
                        node = route_path[i]
                        if prev_node < len(self.time_distance_matrix) and node < len(self.time_distance_matrix[prev_node]):
                            route_duration_seconds += self.time_distance_matrix[prev_node][node]
                        if node in vendor_nodes and self.service_time_matrix is not None and node < len(self.service_time_matrix):
                            route_duration_seconds += self.service_time_matrix[node] * 60

                    # Latest requested delivery in this route (for depot window)
                    route_latest_delivery = None
                    for node in route_path:
                        if node not in vendor_nodes:
                            continue
                        vendor_row = vendor_row_by_node.get(int(node))
                        if vendor_row is None:
                            continue
                        raw_delivery_candidates = [
                            vendor_row.get('Requested Delivery', None),
                            vendor_row.get('Requested Delivery Date', None),
                        ]
                        for raw in raw_delivery_candidates:
                            parsed = pd.to_datetime(raw, errors='coerce', utc=True)
                            if pd.notna(parsed):
                                parsed = _to_naive(parsed)
                                route_latest_delivery = parsed if route_latest_delivery is None else max(route_latest_delivery, parsed)
                                break

                    if first_vendor_requested is not None:
                        allowed_early_hours = float(getattr(self, 'allowed_early_hours', 12))
                        allowed_late_hours = float(getattr(self, 'allowed_late_hours', 12))

                        vendor_window_start = first_vendor_requested - timedelta(hours=allowed_early_hours)
                        vendor_window_end = first_vendor_requested + timedelta(hours=allowed_late_hours)

                        if route_latest_delivery is not None:
                            depot_window_start = route_latest_delivery - timedelta(hours=allowed_early_hours)
                            depot_window_end = route_latest_delivery + timedelta(hours=allowed_late_hours)
                        else:
                            depot_window_start = getattr(self, 'min_date', None)
                            depot_window_end = getattr(self, 'max_date', None)

                        if depot_window_start is not None and depot_window_end is not None:
                            depot_start_min = depot_window_start - timedelta(seconds=route_duration_seconds)
                            depot_start_max = depot_window_end - timedelta(seconds=route_duration_seconds)
                            start_min = max(vendor_window_start, depot_start_min)
                            start_max = min(vendor_window_end, depot_start_max)
                        else:
                            start_min = vendor_window_start
                            start_max = vendor_window_end

                        # Choose start time closest to requested loading, within feasible window
                        if start_min <= start_max:
                            if first_vendor_requested < start_min:
                                base_dt = start_min
                            elif first_vendor_requested > start_max:
                                base_dt = start_max
                            else:
                                base_dt = first_vendor_requested
                        else:
                            base_dt = vendor_window_start

                        log.info(
                            "🕒 Route %s optimized start: first_vendor_id=%s requested=%s start=%s",
                            k,
                            first_vendor_id,
                            first_vendor_requested,
                            base_dt
                        )
                except Exception:
                    base_dt = None

            if base_dt is None:
                min_dt = getattr(self, 'min_date', None)
                if min_dt is not None:
                    if hasattr(min_dt, 'to_pydatetime'):
                        try:
                            base_dt = min_dt.to_pydatetime()
                        except Exception as e:
                            print(f"⚠️ Warning: to_pydatetime failed: {e}")
                            base_dt = None
                    if base_dt is None and isinstance(min_dt, str):
                        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S'):
                            try:
                                base_dt = datetime.strptime(min_dt, fmt)
                                break
                            except ValueError:
                                continue
                        if base_dt is None:
                            try:
                                base_dt = datetime.fromisoformat(min_dt)
                            except (ValueError, AttributeError):
                                pass

            cumulative_time_hours = 0.0
            for i in range(len(route_path) - 1):
                node_from = route_path[i]
                node_to = route_path[i + 1]

                # Skip invalid segments (same node to itself)
                if node_from == node_to:
                    continue
                # Add distance and duration for this segment
                if node_from in range(len(self.distance_matrix)) and node_to in range(len(self.distance_matrix)):
                    seg_distance = self.distance_matrix[node_from][node_to]
                    total_distance += seg_distance

                    # Get duration from time matrix (convert seconds to hours)
                    seg_duration = 0
                    if hasattr(self, 'time_distance_matrix') and self.time_distance_matrix is not None:
                        if node_from < len(self.time_distance_matrix) and node_to < len(self.time_distance_matrix[node_from]):
                            seg_duration = self.time_distance_matrix[node_from][node_to] / 3600.0  # seconds to hours

                    # Calculate average speed
                    avg_speed = seg_distance / seg_duration if seg_duration > 0 else 0

                    # Expected arrival time at destination (hours since route start)
                    arrival_hours = cumulative_time_hours + seg_duration
                    arrival_iso = None
                    vendor_dt = None
                    if node_to in vendor_nodes and self.vendors_df is not None:
                        try:
                            vendor_row = vendor_row_by_node.get(int(node_to))
                            if vendor_row is None:
                                raise ValueError("Vendor row not found")
                            raw_candidates = [
                                vendor_row.get('Requested Loading', None),
                                vendor_row.get('Requested Loading Date', None),
                                vendor_row.get('Requested Delivery', None),
                                vendor_row.get('Requested Delivery Date', None),
                            ]
                            for raw_dt in raw_candidates:
                                parsed = pd.to_datetime(raw_dt, errors='coerce', utc=True)
                                if pd.notna(parsed):
                                    vendor_dt = _to_naive(parsed)
                                    break
                        except Exception:
                            vendor_dt = None

                    # Compute candidate arrival time
                    candidate_dt = None
                    if base_dt is not None:
                        candidate_dt = _to_naive(base_dt) + timedelta(hours=arrival_hours)
                    elif vendor_dt is not None:
                        # Fall back to vendor requested time if base is unavailable
                        candidate_dt = vendor_dt

                    # Enforce a not-earlier-than window of allowed_early hours (if available)
                    if vendor_dt is not None and candidate_dt is not None:
                        vendor_dt = _to_naive(vendor_dt)
                        candidate_dt = _to_naive(candidate_dt)
                        allowed_early_hours = float(getattr(self, 'allowed_early_hours', 12))
                        earliest_allowed = vendor_dt - timedelta(hours=allowed_early_hours)
                        candidate_dt = max(candidate_dt, earliest_allowed)

                    if candidate_dt is not None:
                        arrival_iso = candidate_dt.strftime('%Y-%m-%d %H:%M:%S')
                    elif getattr(self, 'min_date', None) is not None:
                        # Debug: min_dt exists but base_dt is None - conversion failed
                        min_dt = getattr(self, 'min_date', None)
                        print(f"⚠️ Warning: Could not convert min_date to datetime. min_date={min_dt} (type: {type(min_dt).__name__})")

                    # Service time at destination (hours)
                    service_time_hours_node = 0.0
                    if hasattr(self, 'service_time_matrix') and self.service_time_matrix is not None:
                        if node_to < len(self.service_time_matrix):
                            service_time_hours_node = self.service_time_matrix[node_to] / 60.0

                    # Store segment details (only valid segments with different nodes)
                    segments.append({
                        'from_id': node_from,
                        'to_id': node_to,
                        'distance': seg_distance,
                        'duration': seg_duration,
                        'avg_speed': avg_speed,
                        'arrival_hours': arrival_hours,
                        'arrival_time': arrival_iso
                    })

                    # Advance cumulative time (travel + service at destination)
                    cumulative_time_hours = arrival_hours + service_time_hours_node

            route_stats[k] = {
                'total_cargo': total_cargo,
                'total_loading': total_loading,
                'total_distance': total_distance,
                'num_vendors': len(unique_vendors),
                'vendors': unique_vendors,
                'segments': segments,  # Add segment details
                'route_path': route_path
            }
        
        print('🗺️  Generating route visualization with actual road routing...')
        
        # Create sequential route numbering (1, 2, 3, ...) instead of using vehicle indices
        route_mapping = {}  # Maps original vehicle_id to sequential route number
        for idx, vehicle_id in enumerate(sorted(routes.keys()), start=1):
            route_mapping[vehicle_id] = idx
        
        # Create vendor visit mapping (vendor_id -> {vehicle, step})
        vendor_visits = {}
        for vehicle_id, route in routes.items():
            step_num = 0
            for i, node in enumerate(route):
                if node in vendor_nodes:  # Vendor node
                    step_num += 1
                    if node not in vendor_visits:
                        vendor_visits[node] = {
                            'vehicle': vehicle_id,
                            'route_number': route_mapping[vehicle_id],
                            'step': step_num,
                            'total_steps': len([n for n in route if n in vendor_nodes])
                        }
        
        # Plot routes using OSRM for actual street routing
        osrm_failures = []
        route_feature_groups = {}  # Store feature groups for each route
        
        for vehicle_id, route in routes.items():
            route_number = route_mapping[vehicle_id]
            color = colors[(route_number - 1) % len(colors)]
            
            # Create individual feature group for this route
            vehicle_group = folium.FeatureGroup(name=f'🚚 Route {route_number}', show=True)
            route_feature_groups[route_number] = vehicle_group
            
            # Plot route segments
            for i in range(len(route) - 1):
                node_from = route[i]
                node_to = route[i + 1]

                # Calculate step number (1-indexed)
                step_number = i + 1
                total_steps = len(route) - 1
                
                # Get cargo and loading for this specific segment (from node_from)
                segment_cargo = 0
                segment_loading = 0
                if node_from in vendor_nodes:  # Vendor
                    segment_cargo = self.capacity_matrix[node_from] if node_from < len(self.capacity_matrix) else 0
                    segment_loading = self.loading_matrix[node_from] if node_from < len(self.loading_matrix) else 0
                
                if node_from in coords and node_to in coords:
                    lat_from, lon_from = coords[node_from]
                    lat_to, lon_to = coords[node_to]
                    
                    # Get actual street route using OSRM route API
                    try:
                        import requests
                        
                        # OSRM route API (provides actual route polyline)
                        url = f"http://router.project-osrm.org/route/v1/driving/{lon_from},{lat_from};{lon_to},{lat_to}"
                        params = {
                            'overview': 'full',
                            'geometries': 'geojson'
                        }
                        
                        response = requests.get(url, params=params, timeout=10)
                        
                        if response.status_code == 200:
                            data = response.json()
                            
                            if data['code'] == 'Ok' and len(data['routes']) > 0:
                                # Extract route geometry
                                route_geometry = data['routes'][0]['geometry']['coordinates']
                                
                                # Convert to lat,lon format (OSRM returns lon,lat)
                                route_coords = [(coord[1], coord[0]) for coord in route_geometry]
                                
                                # Get distance and duration
                                distance_km = data['routes'][0]['distance'] / 1000
                                duration_sec = data['routes'][0]['duration']
                                duration_hrs = duration_sec / 3600
                                
                                # Handle same-location pickups (multiple vendors at same address)
                                is_same_location = (distance_km < 0.1 and duration_hrs < 0.01)
                                
                                if is_same_location:
                                    # Multiple cargo pickups at same location - show special marker
                                    avg_speed = 0
                                    location_note = "📍 <b>Same Location</b> - Multiple pickups at this address"
                                else:
                                    # Calculate avg speed safely for normal routes
                                    avg_speed = distance_km / duration_hrs if duration_hrs > 0 else 0
                                    location_note = ""
                                
                                # Draw shadow/outline for depth effect (skip for same location)
                                if not is_same_location:
                                    folium.PolyLine(
                                        route_coords,
                                        color='#000000',
                                        weight=6,
                                        opacity=0.2
                                    ).add_to(vehicle_group)
                                
                                # Draw the actual route on the map with modern styling
                                speed_display = f'💨 Avg Speed: {avg_speed:.0f} km/h' if not is_same_location else location_note
                                
                                popup_html = f"""
                                <div style="font-family: 'Segoe UI', Arial, sans-serif; min-width: 200px;">
                                    <div style="background: linear-gradient(135deg, {color} 0%, {color}DD 100%); 
                                                color: white; padding: 12px; border-radius: 8px 8px 0 0; margin: -10px -10px 10px -10px;">
                                        <h4 style="margin: 0; font-weight: 600;">🚚 Route {route_number}</h4>
                                    </div>
                                    <div style="padding: 5px 0;">
                                        <p style="margin: 8px 0; font-size: 13px;"><b>From:</b> {node_info[node_from]["name"]}</p>
                                        <p style="margin: 8px 0; font-size: 13px;"><b>To:</b> {node_info[node_to]["name"]}</p>
                                        <hr style="border: none; border-top: 1px solid #eee; margin: 10px 0;">
                                        {'<p style="margin: 8px 0; font-size: 12px; background: #fff3cd; padding: 6px; border-radius: 4px;">' + location_note + '</p>' if is_same_location else ''}
                                        <p style="margin: 8px 0; font-size: 13px;">📏 <b>Distance:</b> {distance_km:.1f} km</p>
                                        <p style="margin: 8px 0; font-size: 13px;">⏱️ <b>Duration:</b> {duration_hrs:.2f} hrs</p>
                                        <p style="margin: 8px 0; font-size: 12px; color: #666;">{speed_display}</p>
                                    </div>
                                </div>
                                """
                                # Enhanced tooltip with comprehensive route solution info
                                
                                # Get vehicle statistics
                                v_stats = route_stats.get(vehicle_id, {})
                                total_cargo = v_stats.get('total_cargo', 0)
                                total_loading = v_stats.get('total_loading', 0)
                                total_distance = v_stats.get('total_distance', 0)
                                num_vendors = v_stats.get('num_vendors', 0)
                                vendors_in_route = v_stats.get('vendors', [])
                                
                                # Build vendor list HTML (remove duplicates while preserving order)
                                vendor_list_html = ""
                                if vendors_in_route:
                                    vendor_list_items = []
                                    seen_vendors = set()
                                    for v_id in vendors_in_route:
                                        if v_id not in seen_vendors:
                                            seen_vendors.add(v_id)
                                            v_name = node_info.get(v_id, {}).get('name', f'Vendor {v_id}')
                                            v_city = node_info.get(v_id, {}).get('city', 'N/A')
                                            vendor_list_html = v_name
                                
                                # Draw route line (or marker for same location)
                                if is_same_location:
                                    # Same-location pickups: skip drawing vendor circles to avoid clutter
                                    pass
                                else:
                                    # Normal route with polyline
                                    folium.PolyLine(
                                        route_coords,
                                        color=color,
                                        weight=5,
                                        opacity=0.9,
                                        smooth_factor=2.0,
                                        class_name=f'route-{route_number}'
                                    ).add_to(vehicle_group)
                            else:
                                raise Exception("No route found")
                        else:
                            raise Exception(f"OSRM API error: {response.status_code}")
                            
                    except Exception as e:
                        failure_msg = f"OSRM route failed for segment {node_from}→{node_to}: {e}"
                        print(f'  ⚠️  {failure_msg}')
                        osrm_failures.append(failure_msg)
                        # Fallback to straight line with clear indication
                        folium.PolyLine(
                            [(lat_from, lon_from), (lat_to, lon_to)],
                            color=color,
                            weight=3,
                            opacity=0.4,
                            dash_array='10, 10',
                            tooltip=f'V{vehicle_id}: Direct line',
                            class_name=f'route-{route_number}'
                        ).add_to(vehicle_group)
            
            # Add route group to map
            vehicle_group.add_to(m)
        
        # Add markers for all nodes with clear differentiation
        log.info(f'📍 Starting to add node markers... Total nodes to process: {len(coords)}')
        vendor_markers_added = 0
        for node_id, (lat, lon) in coords.items():
            info = node_info[node_id]
            
            if info['type'] == 'depot':
                # Depot marker - modern design with gradient
                popup_html = f"""
                <div style="font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif; width: 280px;">
                    <div style="background: #6B6560; 
                                color: white; padding: 24px; border-radius: 12px 12px 0 0; 
                                margin: -15px -15px 15px -15px; text-align: center;">
                        <div style="font-size: 32px; margin-bottom: 10px;">🏭</div>
                        <h3 style="margin: 0; font-weight: 600; letter-spacing: 0.5px; font-size: 15px;">DISTRIBUTION CENTER</h3>
                    </div>
                    <div style="padding: 12px 8px;">
                        <p style="margin: 12px 0; font-size: 14px; color: #2C2B28; font-weight: 500;">
                            <b style="color: #5C5B56;">📍 Location:</b> Seattle, WA
                        </p>
                        <p style="margin: 12px 0; font-size: 13px; color: #5C5B56; line-height: 1.6;">
                            Central hub for all delivery operations
                        </p>
                        <div style="background: #F8F7F4; padding: 12px; border-radius: 8px; margin-top: 14px; border-left: 3px solid #6B6560;">
                            <p style="margin: 0; font-size: 12px; color: #5C5B56;">
                                ✅ All vehicles end routes here
                            </p>
                        </div>
                    </div>
                </div>
                """
                # Concentric red circles for depot (no pin marker)
                # Outer circle (unfilled)
                folium.Circle(
                    location=[lat, lon],
                    radius=15000,
                    color='#E74C3C',
                    fill=False,
                    weight=3,
                    opacity=0.8,
                    tooltip=folium.Tooltip('<b style="font-size: 14px;">🏭 Distribution Center</b>', direction='auto')
                ).add_to(m)
                
                # Middle circle (unfilled)
                folium.Circle(
                    location=[lat, lon],
                    radius=8000,
                    color='#C0392B',
                    fill=False,
                    weight=3,
                    opacity=0.9,
                ).add_to(m)
                
                # Inner circle (unfilled)
                folium.CircleMarker(
                    location=[lat, lon],
                    radius=15,
                    color='#922B21',
                    fill=False,
                    weight=4,
                    opacity=1.0,
                ).add_to(m)
                
                # Inner circle (center point)
                folium.CircleMarker(
                    location=[lat, lon],
                    radius=15,
                    color='#922B21',
                    fill=False,
                    weight=3,
                    opacity=1.0,
                ).add_to(m)
                
            else:
                # Vendor marker - modern card design
                vendor_colors_hex = ['#2980B9', '#27AE60', '#8E44AD', '#D68910', '#16A085', '#CA6F1E']
                vendor_color_hex = vendor_colors_hex[(node_id - 1) % len(vendor_colors_hex)]
                
                log.info(f'🏭 Processing vendor marker for node_id={node_id}, lat={lat}, lon={lon}, name={info["name"]}')
                
                # Get cargo and loading for this vendor
                vendor_cargo = self.capacity_matrix[node_id] if node_id < len(self.capacity_matrix) else 0
                vendor_loading = self.loading_matrix[node_id] if node_id < len(self.loading_matrix) else 0
                
                # Get solution stage information
                visit_info = vendor_visits.get(node_id, {})
                assigned_vehicle = visit_info.get('vehicle', 'N/A')
                assigned_route = visit_info.get('route_number', 'N/A')
                # Create vendor marker with blue teardrop pin
                pin_html = '''
                <div style="position: relative; width: 20px; height: 28px;">
                    <svg width="20" height="28" viewBox="0 0 30 40" xmlns="http://www.w3.org/2000/svg">
                        <path d="M15,0 C8.373,0 3,5.373 3,12 C3,20.25 15,40 15,40 C15,40 27,20.25 27,12 C27,5.373 21.627,0 15,0 Z" 
                              fill="#1976D2" stroke="#1565C0" stroke-width="1"/>
                        <circle cx="15" cy="12" r="6" fill="white"/>
                    </svg>
                </div>
                '''
                tooltip_city = info.get("city", "")
                tooltip_name = info.get("name", f"Vendor {node_id}")
                folium.Marker(
                    location=[lat, lon],
                    tooltip=folium.Tooltip(f'<b>{tooltip_name}</b><br>{tooltip_city}', direction='top'),
                    icon=folium.DivIcon(html=pin_html, icon_size=(20, 28), icon_anchor=(10, 28))
                ).add_to(m)
                
                log.info(f'✅ Vendor marker added to map: {info["name"]} at ({lat}, {lon})')
                vendor_markers_added += 1
        
        depot_marker_count = len(self.depots_df) if self.depots_df is not None else 1
        log.info(f'✅ COMPLETE: All node markers processed ({depot_marker_count} depots + {vendor_markers_added} vendors added)')

        # Expose OSRM failures (if any) for API/UI warnings
        self.osrm_failures = osrm_failures
        
        # Add fullscreen button
        plugins.Fullscreen(position='topleft', title='Fullscreen', titleCancel='Exit Fullscreen').add_to(m)
        
        # Add mouse position plugin
        plugins.MousePosition(
            position='bottomleft',
            separator=' | ',
            prefix='Coordinates: ',
            num_digits=4
        ).add_to(m)
        
        # Add measure control
        plugins.MeasureControl(
            position='topleft',
            primary_length_unit='kilometers',
            secondary_length_unit='miles',
            primary_area_unit='sqkilometers'
        ).add_to(m)
        
        # Mini map removed per user request
        
        depot_marker_count = len(self.depots_df) if self.depots_df is not None else 1
        log.info(f'✅ COMPLETE: All node markers added to map ({depot_marker_count} depots + {vendor_markers_added} vendors)')
        
        # Calculate total distance across all routes
        total_distance = sum(stats['total_distance'] for stats in route_stats.values())
        distance_formatted = f'{total_distance:,.1f}'
        
        # Get solver info
        solver_type = 'Metaheuristic (ALNS)' if is_metaheuristic else 'Exact (MIP)'
        solving_time = getattr(self, 'secs_taken', 0)
        num_depots = len(self.depots_df) if self.depots_df is not None else 1
        num_routes = len(vehicles_used)
        num_vendors = len(coords) - 1
        
        # Add Excel-like collapsible route filter
        num_routes = len(route_feature_groups)
        excel_filter_html = f'''
        <style>
            /* Layer control background - transparent by default, white when routes visible */
            .leaflet-control-layers-expanded {{
                background: transparent !important;
                backdrop-filter: none !important;
                box-shadow: none !important;
                border: none !important;
                transition: all 0.3s ease;
            }}
            
            .leaflet-control-layers-expanded.has-visible-routes {{
                background: rgba(255, 255, 255, 0.98) !important;
                backdrop-filter: blur(12px) !important;
                box-shadow: 0 2px 16px rgba(0,0,0,0.08) !important;
                border: 1px solid rgba(232, 230, 224, 0.6) !important;
                border-radius: 12px !important;
            }}
            
            .route-filter-header {{
                padding: 10px 12px;
                border-bottom: 1px solid rgba(0, 0, 0, 0.1);
                background: transparent;
                margin: 0;
                cursor: pointer;
                user-select: none;
                font-weight: 600;
                font-size: 13px;
                color: #333;
            }}
            .route-filter-header:hover {{
                background: rgba(0, 0, 0, 0.03);
            }}
            .route-filter-arrow {{
                display: inline-block;
                margin-right: 8px;
                transition: transform 0.2s;
                font-size: 12px;
                font-weight: bold;
            }}
            .route-filter-arrow.expanded {{
                transform: rotate(90deg);
            }}
            .route-filter-content {{
                max-height: 0;
                overflow: hidden;
                transition: max-height 0.3s ease-out;
                background: transparent;
            }}
            .route-filter-content.show {{
                max-height: 500px;
                overflow-y: auto;
            }}
            .route-filter-content label {{
                display: block;
                padding: 6px 12px;
                margin: 0;
                cursor: pointer;
                font-size: 12px;
                user-select: none;
                background: transparent;
                border-bottom: none;
            }}
            .route-filter-content label:hover {{
                background: rgba(0, 0, 0, 0.05);
            }}
            .route-filter-content input[type="checkbox"] {{
                margin-right: 8px;
                cursor: pointer;
            }}
            .select-all-option {{
                font-weight: 600;
                color: #2196F3;
                background: rgba(33, 150, 243, 0.1) !important;
                border-bottom: 1px solid rgba(33, 150, 243, 0.3) !important;
            }}
        </style>
        
        <script>
            function initializeRouteFilter() {{
                var layerControl = document.querySelector('.leaflet-control-layers-overlays');
                if (!layerControl) {{
                    setTimeout(initializeRouteFilter, 100);
                    return;
                }}
                
                // Check if already initialized
                if (document.querySelector('.route-filter-header')) return;
                
                // Create filter header
                var filterHeader = document.createElement('div');
                filterHeader.className = 'route-filter-header';
                filterHeader.innerHTML = '<span class="route-filter-arrow">▶</span>🚚 Routes ({num_routes})';
                
                // Create filter content container
                var filterContent = document.createElement('div');
                filterContent.className = 'route-filter-content';
                
                // Add Select All option
                var selectAllLabel = document.createElement('label');
                selectAllLabel.className = 'select-all-option';
                selectAllLabel.innerHTML = '<input type="checkbox" id="route-select-all" checked> (Select All)';
                filterContent.appendChild(selectAllLabel);
                
                // Store original labels for syncing
                var originalLabels = [];
                var allLabels = Array.from(layerControl.querySelectorAll('label'));
                allLabels.forEach(function(label) {{
                    var labelText = label.textContent || label.innerText;
                    if (labelText.includes('🚚 Route')) {{
                        originalLabels.push(label);
                        
                        // Create visual clone for our filter
                        var clonedLabel = document.createElement('label');
                        clonedLabel.innerHTML = label.innerHTML;
                        
                        // Get the original checkbox
                        var originalCheckbox = label.querySelector('input[type="checkbox"]');
                        var clonedCheckbox = clonedLabel.querySelector('input[type="checkbox"]');
                        
                        // Sync cloned checkbox with original
                        if (clonedCheckbox && originalCheckbox) {{
                            clonedCheckbox.checked = originalCheckbox.checked;
                            
                            // When cloned checkbox is clicked, trigger original
                            clonedCheckbox.addEventListener('change', function() {{
                                originalCheckbox.click();
                            }});
                            
                            // Listen to original checkbox changes to update clone
                            originalCheckbox.addEventListener('change', function() {{
                                clonedCheckbox.checked = originalCheckbox.checked;
                            }});
                        }}
                        
                        filterContent.appendChild(clonedLabel);
                        label.style.display = 'none'; // Hide original
                    }}
                }});
                
                // Insert at the top of layer control
                layerControl.insertBefore(filterContent, layerControl.firstChild);
                layerControl.insertBefore(filterHeader, layerControl.firstChild);
                
                // Toggle expand/collapse
                filterHeader.addEventListener('click', function() {{
                    var arrow = this.querySelector('.route-filter-arrow');
                    arrow.classList.toggle('expanded');
                    filterContent.classList.toggle('show');
                }});
                
                // Handle Select All checkbox
                var selectAllCheckbox = document.getElementById('route-select-all');
                if (selectAllCheckbox) {{
                    selectAllCheckbox.addEventListener('change', function(e) {{
                        e.stopPropagation();
                        var isChecked = selectAllCheckbox.checked;
                        
                        // Trigger all original checkboxes
                        originalLabels.forEach(function(label) {{
                            var checkbox = label.querySelector('input[type="checkbox"]');
                            if (checkbox && checkbox.checked !== isChecked) {{
                                checkbox.click();
                            }}
                        }});
                    }});
                }}
                
                // Function to update panel transparency based on visible routes
                var userHasInteracted = false;
                function updatePanelTransparency() {{
                    if (!userHasInteracted) return; // Don't apply transparency until user interacts
                    
                    var layerControlContainer = document.querySelector('.leaflet-control-layers-expanded');
                    if (!layerControlContainer) return;
                    
                    var hasVisibleRoutes = false;
                    originalLabels.forEach(function(label) {{
                        var checkbox = label.querySelector('input[type="checkbox"]');
                        if (checkbox && checkbox.checked) {{
                            hasVisibleRoutes = true;
                        }}
                    }});
                    
                    if (hasVisibleRoutes) {{
                        layerControlContainer.classList.add('has-visible-routes');
                    }} else {{
                        layerControlContainer.classList.remove('has-visible-routes');
                    }}
                }}
                
                // Listen to all checkbox changes
                originalLabels.forEach(function(label) {{
                    var checkbox = label.querySelector('input[type="checkbox"]');
                    if (checkbox) {{
                        checkbox.addEventListener('change', function() {{
                            userHasInteracted = true;
                            updatePanelTransparency();
                        }});
                    }}
                }});
                
                console.log('Route filter initialized with', {num_routes}, 'routes');
            }}
            
            // Initialize when page loads
            if (document.readyState === 'loading') {{
                document.addEventListener('DOMContentLoaded', initializeRouteFilter);
            }} else {{
                setTimeout(initializeRouteFilter, 500);
            }}
        </script>
        '''
        m.get_root().html.add_child(folium.Element(excel_filter_html))
        
        # Expose map and route layer groups to window for external filter control
        # Build a JavaScript mapping of route numbers to layer groups
        route_groups_json = {}
        for route_num, fg in route_feature_groups.items():
            # Store the feature group with the route number as key
            route_groups_json[str(route_num)] = f"window.routeLayerGroups[{route_num}]"
        
        map_var = m.get_name()
        layer_map_lines = []
        for route_num, fg in route_feature_groups.items():
            layer_map_lines.append(f"            window.routeLayerGroups[{route_num}] = {fg.get_name()};")

        layer_map_js = "\n".join(layer_map_lines)

        expose_layers_js = f"""
        <script>
            // Directly expose Folium map and route layers - wait for map to be defined
            function initRouteLayerExposure() {{
                if (typeof {map_var} === 'undefined') {{
                    setTimeout(initRouteLayerExposure, 100);
                    return;
                }}
                
                window.routeMap = {map_var};
                window.routeLayerGroups = {{}};
{layer_map_js}
                
                console.groupCollapsed('[ROUTE HIGHLIGHT] Layer Exposure');
                console.log('✓ Exposed', Object.keys(window.routeLayerGroups).length, 'route layers to window');
                console.log('Map variable:', '{map_var}');
                try {{
                    Object.keys(window.routeLayerGroups).forEach(function(key) {{
                        var fg = window.routeLayerGroups[key];
                        var layerCount = (fg && typeof fg.getLayers === 'function') ? fg.getLayers().length : 'N/A';
                        console.log('  Route ' + key + ' FeatureGroup: ' + layerCount + ' layers');
                    }});
                }} catch(e) {{
                    console.warn('  Error enumerating FeatureGroups:', e);
                }}
                console.groupEnd();
            }}
            
            if (document.readyState === 'loading') {{
                document.addEventListener('DOMContentLoaded', initRouteLayerExposure);
            }} else {{
                initRouteLayerExposure();
            }}
        </script>
        """
        m.get_root().html.add_child(folium.Element(expose_layers_js))
        
        # Add comprehensive route highlighting JavaScript handler
        route_highlight_js = rf"""
        <script>
        (function() {{
            console.log('%c[ROUTE HIGHLIGHT] INITIALIZING', 'color: #FFD700; font-weight: bold; font-size: 14px;');
            console.log('Waiting for window.routeMap...');
            
            var maxWait = 0;
            var currentlyHighlighted = null;  // Track globally so map click handler can access it
            
            function initHighlight() {{
                maxWait++;
                console.log('[ROUTE HIGHLIGHT] Init attempt ' + maxWait);
                
                var map = window.routeMap;
                if (!map) {{
                    console.log('[ROUTE HIGHLIGHT] ⏳ window.routeMap not ready yet, retrying...');
                    if (maxWait < 50) {{
                        setTimeout(initHighlight, 100);
                    }} else {{
                        console.error('[ROUTE HIGHLIGHT] ERROR: window.routeMap never became available after ' + (maxWait * 100) + 'ms');
                    }}
                    return;
                }}
                
                console.log('%c[ROUTE HIGHLIGHT] ✓ window.routeMap available', 'color: #4CAF50; font-weight: bold;');
                
                var attached = false;
                var attempts = 0;
                
                function attachHandlers() {{
                    if (attached) {{
                        console.log('[ROUTE HIGHLIGHT] Handlers already attached');
                        return;
                    }}
                    attempts++;
                    console.group('[ROUTE HIGHLIGHT] Attachment Attempt #' + attempts);
                    
                    var polylines = [];
                    var layerTypes = {{}};
                    
                    // Scan map._layers for polylines
                    try {{
                        for (var id in map._layers) {{
                            var layer = map._layers[id];
                            if (!layer) continue;
                            var typeName = layer.constructor ? layer.constructor.name : 'unknown';
                            layerTypes[typeName] = (layerTypes[typeName] || 0) + 1;
                            
                            if (layer instanceof L.Polyline && !(layer instanceof L.Marker)) {{
                                polylines.push(layer);
                            }}
                        }}
                    }} catch(e) {{
                        console.error('Error scanning map._layers:', e);
                    }}
                    
                    console.log('Layer types found:', layerTypes);
                    console.log('Polylines in map._layers:', polylines.length);
                    
                    // Also scan FeatureGroups
                    try {{
                        var idsSeen = {{}};
                        polylines.forEach(function(l) {{ idsSeen[l._leaflet_id] = true; }});
                        
                        (window.routeLayerGroups || {{}});
                        Object.keys(window.routeLayerGroups || {{}}).forEach(function(key) {{
                            var fg = window.routeLayerGroups[key];
                            if (fg && typeof fg.getLayers === 'function') {{
                                var fgLayers = fg.getLayers();
                                var polyCount = 0;
                                fgLayers.forEach(function(l) {{
                                    if (l instanceof L.Polyline && !(l instanceof L.Marker)) {{
                                        polyCount++;
                                        if (!idsSeen[l._leaflet_id]) {{
                                            idsSeen[l._leaflet_id] = true;
                                            polylines.push(l);
                                        }}
                                    }}
                                }});
                                console.log('Route ' + key + ' FeatureGroup: ' + fgLayers.length + ' total layers, ' + polyCount + ' polylines (added ' + (polyCount > 0 && !idsSeen[fgLayers[0]._leaflet_id] ? polyCount : 0) + ' new)');
                            }}
                        }});
                    }} catch(e) {{
                        console.warn('Error scanning FeatureGroups:', e);
                    }}
                    
                    console.log('Total polylines found after scanning:', polylines.length);
                    
                    if (polylines.length === 0 && attempts < 30) {{
                        console.warn('⏳ No polylines yet, retrying in 200ms...');
                        console.groupEnd();
                        setTimeout(attachHandlers, 200);
                        return;
                    }}
                    
                    if (polylines.length === 0) {{
                        console.error('❌ No polylines found after ' + attempts + ' attempts. Check HTML structure.');
                        console.groupEnd();
                        return;
                    }}
                    
                    console.log('%c🖱️ ATTACHING CLICK HANDLERS TO ' + polylines.length + ' POLYLINES', 'color: #2196F3; font-weight: bold; font-size: 13px;');
                    
                    var handlerCount = 0;
                    polylines.forEach(function(layer, idx) {{
                        if (layer._routeHighlightAttached) {{
                            console.log('  Polyline [' + idx + '] (id ' + (layer._leaflet_id || 'n/a') + '): handler already attached, skipping');
                            return;
                        }}
                        
                        layer._originalColor = (layer.options && layer.options.color) || '#000000';
                        layer._originalWeight = (layer.options && layer.options.weight) || 5;
                        layer._originalOpacity = (layer.options && layer.options.opacity) || 0.9;
                        // Extract route ID from className
                        layer._routeId = null;
                        if (layer.options && layer.options.className) {{
                            var match = layer.options.className.match(/route-(\d+)/);
                            if (match) {{
                                layer._routeId = match[1];
                                console.log('  Extracted routeId: ' + layer._routeId + ' from className: ' + layer.options.className);
                            }}
                        }}
                        
                        if (layer._path) {{
                            layer._path.style.cursor = 'pointer';
                            layer._path.style.transition = 'stroke 0.2s ease, filter 0.2s ease';
                        }}
                        
                        (function(poly, index) {{
                            poly.on('click', function(e) {{
                                L.DomEvent.stop(e);
                                var startTime = performance.now();
                                
                                console.log('%c╔═══════════════════════════════════════════════════════╗', 'color: #666; font-weight: bold;');
                                console.log('%c║  🖱️  ROUTE POLYLINE CLICK EVENT', 'color: #FF9800; font-weight: bold; font-size: 15px; background: #FFF3E0; padding: 4px;');
                                console.log('%c╠═══════════════════════════════════════════════════════╣', 'color: #666;');
                                console.groupCollapsed('%c📍 Click Details', 'color: #2196F3; font-weight: bold;');
                                console.log('Polyline Index:', index);
                                console.log('Route ID:', poly._routeId);
                                console.log('Click Coordinates:', {{
                                    lat: e.latlng.lat.toFixed(6),
                                    lng: e.latlng.lng.toFixed(6),
                                    formatted: e.latlng.lat.toFixed(6) + '°N, ' + e.latlng.lng.toFixed(6) + '°W'
                                }});
                                console.log('Polyline State:', {{
                                    originalColor: poly._originalColor,
                                    originalWeight: poly._originalWeight,
                                    currentlyHighlighted: !!poly._highlighted,
                                    leafletId: poly._leaflet_id
                                }});
                                console.groupEnd();
                                
                                // Safety check: only proceed if we have a valid route ID
                                if (!poly._routeId) {{
                                    console.error('%c⚠️ CRITICAL ERROR: No route ID found for polyline', 'color: #F44336; font-weight: bold; font-size: 14px; background: #FFEBEE; padding: 4px;');
                                    console.log('%c╚═══════════════════════════════════════════════════════╝', 'color: #666;');
                                    return;
                                }}
                                
                                // Find all polylines with the same route ID
                                var segmentSearchStart = performance.now();
                                var routePolylines = [];
                                map.eachLayer(function(lyr) {{
                                    if (lyr instanceof L.Polyline && lyr._routeId && lyr._routeId === poly._routeId) {{
                                        routePolylines.push(lyr);
                                    }}
                                }});
                                var segmentSearchTime = (performance.now() - segmentSearchStart).toFixed(2);
                                console.log('%c📊 Route Segment Discovery', 'color: #00BCD4; font-weight: bold;');
                                console.log('  ├─ Segments Found: %c' + routePolylines.length, 'color: #4CAF50; font-weight: bold;');
                                console.log('  └─ Search Time: %c' + segmentSearchTime + 'ms', 'color: #9E9E9E;');
                                
                                // Clear previously highlighted route if different
                                if (currentlyHighlighted && currentlyHighlighted._routeId && currentlyHighlighted._routeId !== poly._routeId) {{
                                    var clearStart = performance.now();
                                    console.log('%c🧹 Clearing Previous Route Highlight', 'color: #FF5722; font-weight: bold; font-size: 13px;');
                                    console.log('  ├─ Previous Route ID: %c' + currentlyHighlighted._routeId, 'color: #FF7043;');
                                    var clearedCount = 0;
                                    // Clear all segments of the previous route
                                    map.eachLayer(function(lyr) {{
                                        if (lyr instanceof L.Polyline && lyr._routeId && lyr._routeId === currentlyHighlighted._routeId && lyr._highlighted) {{
                                            lyr.setStyle({{ 
                                                color: lyr._originalColor, 
                                                weight: lyr._originalWeight, 
                                                opacity: lyr._originalOpacity 
                                            }});
                                            if (lyr._path) {{
                                                lyr._path.style.filter = 'none';
                                                var currentEl = lyr._path;
                                                while (currentEl) {{
                                                    if (currentEl.style) {{
                                                        currentEl.style.zIndex = '';
                                                    }}
                                                    if (currentEl.tagName === 'svg') break;
                                                    currentEl = currentEl.parentNode;
                                                }}
                                            }}
                                            lyr._highlighted = false;
                                            clearedCount++;
                                        }}
                                    }});
                                    var clearTime = (performance.now() - clearStart).toFixed(2);
                                    console.log('  ├─ Segments Cleared: %c' + clearedCount, 'color: #8BC34A; font-weight: bold;');
                                    console.log('  └─ Clear Time: %c' + clearTime + 'ms', 'color: #9E9E9E;');
                                }}
                                
                                if (!poly._highlighted) {{
                                    var highlightStart = performance.now();
                                    console.log('%c🟡 APPLYING ROUTE HIGHLIGHT', 'color: #FFD700; font-weight: bold; font-size: 13px; background: #000; padding: 4px 8px; border-radius: 3px;');
                                    console.log('  ├─ Target Route: %c#' + poly._routeId, 'color: #FFF59D; font-weight: bold;');
                                    console.log('  ├─ Segments to Highlight: %c' + routePolylines.length, 'color: #FFEB3B;');
                                    var highlightedCount = 0;
                                    // Highlight all segments of this route
                                    routePolylines.forEach(function(segment) {{
                                        segment.setStyle({{ color: '#FFD700', weight: segment._originalWeight + 3, opacity: 1 }});
                                        segment.bringToFront();
                                        if (segment._path) {{ 
                                            segment._path.style.filter = 'drop-shadow(0 0 8px rgba(255, 215, 0, 0.8))';
                                            segment._path.setAttribute('pointer-events', 'visibleStroke');
                                            // Force to highest z-index
                                            var currentEl = segment._path;
                                            while (currentEl) {{
                                                if (currentEl.style) {{
                                                    currentEl.style.zIndex = '9999';
                                                }}
                                                if (currentEl.tagName === 'svg') break;
                                                currentEl = currentEl.parentNode;
                                            }}
                                            var svgParent = segment._path.parentNode;
                                            if (svgParent) {{
                                                svgParent.appendChild(segment._path);
                                            }}
                                        }}
                                        segment._highlighted = true;
                                        highlightedCount++;
                                    }});
                                    var highlightTime = (performance.now() - highlightStart).toFixed(2);
                                    console.log('  ├─ Highlighted: %c' + highlightedCount + ' segments', 'color: #8BC34A; font-weight: bold;');
                                    console.log('  └─ Highlight Time: %c' + highlightTime + 'ms', 'color: #9E9E9E;');
                                    currentlyHighlighted = poly;
                                    
                                    // Show route summary
                                    var popupStart = performance.now();
                                    var summaryDiv = document.getElementById('routeSummary');
                                    if (summaryDiv && routeStatsData && routeMapping) {{
                                        console.log('%c📋 ROUTE SUMMARY POPUP', 'color: #3F51B5; font-weight: bold; font-size: 13px;');
                                        // Find the vehicle_id for this route number
                                        var vehicleId = null;
                                        for (var vid in routeMapping) {{
                                            if (routeMapping[vid] == poly._routeId) {{
                                                vehicleId = vid;
                                                break;
                                            }}
                                        }}
                                        console.groupCollapsed('%c  ├─ Vehicle ID: ' + vehicleId, 'color: #7986CB; font-weight: bold;');
                                        console.log('Mapping Entry:', vehicleId, '→', 'Route', poly._routeId);
                                        console.groupEnd();
                                        
                                        if (vehicleId && routeStatsData[vehicleId]) {{
                                            var stats = routeStatsData[vehicleId];
                                            console.groupCollapsed('%c  ├─ Route Statistics', 'color: #7986CB; font-weight: bold;');
                                            console.table({{
                                                'Vendors': stats.num_vendors || 0,
                                                'Distance (km)': stats.total_distance ? stats.total_distance.toFixed(2) : 0,
                                                'Time (hours)': stats.total_time_hours ? stats.total_time_hours.toFixed(2) : 0,
                                                'Cargo (kg)': stats.total_cargo ? stats.total_cargo.toFixed(0) : 0,
                                                'Volume (m³)': stats.total_loading ? stats.total_loading.toFixed(2) : 0,
                                                'Vendor IDs': stats.vendors ? stats.vendors.join(', ') : 'N/A'
                                            }});
                                            console.groupEnd();
                                            
                                            document.getElementById('routeNumber').textContent = poly._routeId;
                                            document.getElementById('routeVendors').textContent = stats.num_vendors || '-';
                                            document.getElementById('routeDistance').textContent = (stats.total_distance ? stats.total_distance.toFixed(1) + ' km' : '-');
                                            document.getElementById('routeCargo').textContent = (stats.total_cargo ? stats.total_cargo.toFixed(0) + ' kg' : '-');
                                            document.getElementById('routeTime').textContent = (stats.total_time_hours ? formatHours(stats.total_time_hours) : '-');
                                            document.getElementById('routeVolume').textContent = (stats.total_loading ? stats.total_loading.toFixed(2) + ' m³' : '-');
                                            summaryDiv.style.display = 'block';
                                            
                                            var popupTime = (performance.now() - popupStart).toFixed(2);
                                            console.log('  └─ Popup Render Time: %c' + popupTime + 'ms', 'color: #9E9E9E;');
                                            console.log('  %c✓ Popup displayed successfully', 'color: #8BC34A; font-weight: bold; font-size: 12px;');
                                        }} else {{
                                            console.warn('%c  ⚠️ No stats data found for vehicle ' + vehicleId, 'color: #FF9800; font-weight: bold;');
                                            console.log('  Available vehicle IDs:', Object.keys(routeStatsData));
                                        }}
                                    }} else {{
                                        console.warn('%c  ⚠️ Popup Requirements Missing', 'color: #FF9800; font-weight: bold;');
                                        console.log('  Summary Div:', !!summaryDiv, '| Stats Data:', !!routeStatsData, '| Mapping:', !!routeMapping);
                                    }}
                                }} else {{
                                    var restoreStart = performance.now();
                                    console.log('%c⚪ RESTORING ROUTE TO ORIGINAL STATE', 'color: #2196F3; font-weight: bold; font-size: 13px; background: #E3F2FD; padding: 4px 8px; border-radius: 3px;');
                                    console.log('  ├─ Route ID: %c#' + poly._routeId, 'color: #64B5F6;');
                                    var restoredCount = 0;
                                    // Restore all segments of this route
                                    routePolylines.forEach(function(segment) {{
                                        segment.setStyle({{ color: segment._originalColor, weight: segment._originalWeight, opacity: segment._originalOpacity }});
                                        if (segment._path) {{ 
                                            segment._path.style.filter = 'none';
                                            var currentEl = segment._path;
                                            while (currentEl) {{
                                                if (currentEl.style) {{
                                                    currentEl.style.zIndex = '';
                                                }}
                                                if (currentEl.tagName === 'svg') break;
                                                currentEl = currentEl.parentNode;
                                            }}
                                        }}
                                        segment._highlighted = false;
                                        restoredCount++;
                                    }});
                                    var restoreTime = (performance.now() - restoreStart).toFixed(2);
                                    console.log('  ├─ Segments Restored: %c' + restoredCount, 'color: #8BC34A; font-weight: bold;');
                                    console.log('  └─ Restore Time: %c' + restoreTime + 'ms', 'color: #9E9E9E;');
                                    currentlyHighlighted = null;
                                    
                                    // Hide route summary
                                    var summaryDiv = document.getElementById('routeSummary');
                                    if (summaryDiv) {{
                                        summaryDiv.style.display = 'none';
                                        console.log('  %c✓ Popup hidden', 'color: #8BC34A; font-weight: bold;');
                                    }}
                                }}
                                
                                var totalTime = (performance.now() - startTime).toFixed(2);
                                console.log('%c╠═══════════════════════════════════════════════════════╣', 'color: #666;');
                                console.log('%c⏱️  TOTAL OPERATION TIME: ' + totalTime + 'ms', 'color: #9C27B0; font-weight: bold; font-size: 12px;');
                                console.log('%c╚═══════════════════════════════════════════════════════╝', 'color: #666; font-weight: bold;');
                                console.log(' ');
                            }});
                        }})(layer, idx);
                        
                        layer._routeHighlightAttached = true;
                        handlerCount++;
                        console.log('  ✓ Polyline [' + idx + '] (id ' + (layer._leaflet_id || 'n/a') + ') handler attached');
                    }});
                    
                    console.log('%c✅ SUCCESS - ' + handlerCount + ' handlers attached', 'color: #4CAF50; font-weight: bold; font-size: 12px;');
                    attached = true;
                    console.groupEnd();
                }}
                
                console.log('[ROUTE HIGHLIGHT] Scheduling first attachment in 100ms');
                setTimeout(attachHandlers, 100);
                
                // Add map click handler to deselect routes when clicking on map background
                map.on('click', function(e) {{
                    // Check if click was on the map background (not on a marker, polyline, or other UI element)
                    if (e.originalEvent && (e.originalEvent.target.tagName === 'CANVAS' || e.originalEvent.target.classList.contains('leaflet-container'))) {{
                        console.log('%c🗺️ MAP BACKGROUND CLICK - Clearing route highlight', 'color: #2196F3; font-weight: bold;');
                        
                        // Clear highlighted routes
                        if (currentlyHighlighted) {{
                            map.eachLayer(function(lyr) {{
                                if (lyr instanceof L.Polyline && lyr._routeId && lyr._routeId === currentlyHighlighted._routeId && lyr._highlighted) {{
                                    lyr.setStyle({{ 
                                        color: lyr._originalColor, 
                                        weight: lyr._originalWeight, 
                                        opacity: lyr._originalOpacity 
                                    }});
                                    if (lyr._path) {{
                                        lyr._path.style.filter = 'none';
                                        var currentEl = lyr._path;
                                        while (currentEl) {{
                                            if (currentEl.style) {{
                                                currentEl.style.zIndex = '';
                                            }}
                                            if (currentEl.tagName === 'svg') break;
                                            currentEl = currentEl.parentNode;
                                        }}
                                    }}
                                    lyr._highlighted = false;
                                }}
                            }});
                            currentlyHighlighted = null;
                        }}
                        
                        // Hide route summary
                        var summaryDiv = document.getElementById('routeSummary');
                        if (summaryDiv) {{
                            summaryDiv.style.display = 'none';
                        }}
                    }}
                }});
                
                // Retry after page load
                if (document.readyState === 'loading') {{
                    window.addEventListener('load', function() {{ 
                        console.log('[ROUTE HIGHLIGHT] Page load detected, retrying attachment in 500ms');
                        setTimeout(attachHandlers, 500); 
                    }});
                }} else {{
                    console.log('[ROUTE HIGHLIGHT] Page already loaded, scheduling retry in 500ms');
                    setTimeout(attachHandlers, 500);
                }}
            }}
            
            initHighlight();
        }})();
        </script>
        """
        m.get_root().html.add_child(folium.Element(route_highlight_js))
        
        # Route summary window (top-right, above legend) - initially hidden
        route_summary_html = """
        <div id="routeSummary" style="position: fixed; bottom: 160px; right: 20px; z-index: 999999;
                    background: rgba(255, 255, 255, 0.95); padding: 12px 14px;
                    border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.2);
                    font-family: 'Segoe UI', Arial, sans-serif; font-size: 12px; line-height: 1.5;
                    display: none; min-width: 160px;">
            <div style="font-weight: 700; font-size: 13px; margin-bottom: 8px; color: #2C3E50;">
                Route <span id="routeNumber">-</span>
            </div>
            <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                <span style="color: #7F8C8D;">Vendors:</span>
                <span style="font-weight: 600;" id="routeVendors">-</span>
            </div>
            <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                <span style="color: #7F8C8D;">Distance:</span>
                <span style="font-weight: 600;" id="routeDistance">-</span>
            </div>
            <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                <span style="color: #7F8C8D;">Cargo:</span>
                <span style="font-weight: 600;" id="routeCargo">-</span>
            </div>
            <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                <span style="color: #7F8C8D;">Time:</span>
                <span style="font-weight: 600;" id="routeTime">-</span>
            </div>
            <div style="display: flex; justify-content: space-between;">
                <span style="color: #7F8C8D;">Volume:</span>
                <span style="font-weight: 600;" id="routeVolume">-</span>
            </div>
        </div>
        """
        m.get_root().html.add_child(folium.Element(route_summary_html))
        
        # Embed route statistics into JavaScript
        # Convert numpy types to native Python types for JSON serialization
        route_stats_clean = {}
        for k, v in route_stats.items():
            # Calculate total time from segments if available
            total_time_hours = 0
            if 'segments' in v:
                total_time_hours = sum(seg.get('duration', 0) for seg in v.get('segments', []))
            
            route_stats_clean[str(k)] = {
                'total_cargo': float(v.get('total_cargo', 0)),
                'total_loading': float(v.get('total_loading', 0)),
                'total_distance': float(v.get('total_distance', 0)),
                'total_time_hours': float(total_time_hours),
                'num_vendors': int(v.get('num_vendors', 0)),
                'vendors': [int(vid) for vid in v.get('vendors', [])],
            }
        
        route_mapping_clean = {str(k): int(v) for k, v in route_mapping.items()}
        
        route_stats_js = f"""
        <script>
        // Helper function to format hours as "Xh Ym"
        function formatHours(hours) {{
            if (hours === null || hours === undefined || isNaN(hours)) return '0h 0m';
            const totalMinutes = Math.round(Number(hours) * 60);
            const h = Math.floor(totalMinutes / 60);
            const m = totalMinutes % 60;
            return `${{h}}h ${{m}}m`;
        }}
        
        var routeStatsData = {json.dumps(route_stats_clean)};
        var routeMapping = {json.dumps(route_mapping_clean)};
        </script>
        """
        m.get_root().html.add_child(folium.Element(route_stats_js))

        # Legend (bottom-right): vendor pin, depot marker, route + highlight
        legend_html = """
        <div style="position: fixed; bottom: 20px; right: 20px; z-index: 999999;
                    background: rgba(255, 255, 255, 0.9); padding: 10px 12px;
                    border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.2);
                    font-family: 'Segoe UI', Arial, sans-serif; font-size: 12px; line-height: 1.4;">
            <div style="display: flex; align-items: center; margin-bottom: 4px;">
                <div style="width: 18px; height: 24px; margin-right: 8px;">
                    <svg width="18" height="24" viewBox="0 0 30 40" xmlns="http://www.w3.org/2000/svg">
                        <path d="M15,0 C8.373,0 3,5.373 3,12 C3,20.25 15,40 15,40 C15,40 27,20.25 27,12 C27,5.373 21.627,0 15,0 Z"
                              fill="#1976D2" stroke="#1565C0" stroke-width="1"/>
                        <circle cx="15" cy="12" r="6" fill="white"/>
                    </svg>
                </div>
                <span>Vendor</span>
            </div>
            <div style="display: flex; align-items: center; margin-bottom: 4px;">
                <div style="position: relative; width: 18px; height: 18px; margin-right: 8px;">
                    <svg width="18" height="18" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
                        <circle cx="20" cy="20" r="10" fill="rgba(231, 76, 60, 0.15)" stroke="#E74C3C" stroke-width="2"/>
                        <circle cx="20" cy="20" r="4" fill="rgba(231, 76, 60, 0.4)" stroke="#C0392B" stroke-width="2"/>
                    </svg>
                </div>
                <span>Depot</span>
            </div>
            <div style="display: flex; align-items: center; margin-bottom: 4px;">
                <div style="width: 28px; height: 6px; margin-right: 8px; background: linear-gradient(90deg, #2980B9, #16A085);
                            border-radius: 4px; border: 1px solid rgba(0,0,0,0.15);"></div>
                <span>Route (per vehicle)</span>
            </div>
        </div>
        """
        m.get_root().html.add_child(folium.Element(legend_html))
        
        # Fit bounds to show all markers
        m.fit_bounds([[min(all_lats), min(all_lons)], [max(all_lats), max(all_lons)]])
        
        # Save map
        if save_path is None:
            save_path = 'routes_map.html'
        
        m.save(save_path)
        print(f'🗺️  Interactive map saved to: {save_path}')
        
        # Open in browser if requested
        if show_plot:
            import webbrowser
            import os
            webbrowser.open('file://' + os.path.abspath(save_path))
        
        # Return map and statistics
        return m, route_stats