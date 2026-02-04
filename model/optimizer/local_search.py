"""
Local search operators for route improvement.
2-opt, swap, relocate, and cross-exchange operators.
"""

import copy
import pandas as pd


class LocalSearchOperators:
    """
    Collection of local search operators for VRP route improvement.
    """
    
    @staticmethod
    def _is_depot(node_id, depot_node_ids=None):
        depot_nodes = set(depot_node_ids or [0])
        return node_id in depot_nodes

    @staticmethod
    @staticmethod
    def _get_route_depots(vendor_nodes, delivery_map, vendor_depot_map):
        depot_deadlines = {}
        for v in vendor_nodes:
            depot_node = vendor_depot_map.get(v)
            if depot_node is None:
                continue
            delivery_time = delivery_map.get(v)
            if delivery_time is None:
                continue
            if depot_node not in depot_deadlines:
                depot_deadlines[depot_node] = delivery_time
            else:
                depot_deadlines[depot_node] = min(depot_deadlines[depot_node], delivery_time)
        depots = list(depot_deadlines.keys())
        depots.sort(key=lambda d: depot_deadlines.get(d))
        return depots

    @staticmethod
    def _build_route_with_depots(route, delivery_map, vendor_depot_map, depot_node_ids=None):
        vendor_nodes = [n for n in route if not LocalSearchOperators._is_depot(n, depot_node_ids)]
        if not vendor_nodes:
            return route
        depots = LocalSearchOperators._get_route_depots(vendor_nodes, delivery_map, vendor_depot_map)
        return [0] + vendor_nodes + depots
    
    @staticmethod
    def _route_distance(route, distance_matrix):
        """
        Calculate total distance of a route.
        
        Args:
            route: List of nodes (including depot at start/end)
            distance_matrix: Distance matrix
            
        Returns:
            float: Total route distance
        """
        total_distance = 0.0
        for i in range(len(route) - 1):
            total_distance += distance_matrix[route[i], route[i+1]]
        return total_distance
    
    @staticmethod
    def two_opt_route(route, distance_matrix):
        """
        2-opt improvement within a single route.
        Reverses a segment of the route to reduce distance.
        
        Args:
            route: List of nodes [0, v1, v2, ..., vn, 0]
            distance_matrix: Distance matrix
            
        Returns:
            tuple: (improved_route, improvement_found)
        """
        improved = True
        best_route = route[:]
        
        while improved:
            improved = False
            best_distance = LocalSearchOperators._route_distance(best_route, distance_matrix)
            
            for i in range(1, len(best_route) - 2):
                for j in range(i + 1, len(best_route) - 1):
                    # Reverse segment between i and j
                    new_route = best_route[:i] + best_route[i:j+1][::-1] + best_route[j+1:]
                    new_distance = LocalSearchOperators._route_distance(new_route, distance_matrix)
                    
                    if new_distance < best_distance:
                        best_route = new_route
                        best_distance = new_distance
                        improved = True
                        break
                
                if improved:
                    break
        
        return best_route, best_route != route
    
    @staticmethod
    def relocate_intra(route, distance_matrix, capacity_matrix=None, 
                       loading_matrix=None, max_weight=None, max_volume=None,
                       depot_node_ids=None):
        """
        Relocate a vendor to a different position within the same route.
        
        Args:
            route: List of nodes
            distance_matrix: Distance matrix
            capacity_matrix: Cargo weights (optional)
            loading_matrix: Loading volumes (optional)
            max_weight: Max weight capacity (optional)
            max_volume: Max volume capacity (optional)
            
        Returns:
            tuple: (improved_route, improvement_found)
        """
        best_route = route[:]
        best_distance = LocalSearchOperators._route_distance(best_route, distance_matrix)
        improved = False
        
        for i in range(1, len(route) - 1):  # Skip depots
            vendor = route[i]
            if LocalSearchOperators._is_depot(vendor, depot_node_ids):
                continue
            
            # Try moving vendor to each other position
            for j in range(1, len(route) - 1):
                if i == j:
                    continue
                
                # Create new route with vendor relocated
                new_route = route[:]
                new_route.pop(i)
                new_route.insert(j, vendor)
                
                new_distance = LocalSearchOperators._route_distance(new_route, distance_matrix)
                
                if new_distance < best_distance:
                    best_route = new_route
                    best_distance = new_distance
                    improved = True
        
        return best_route, improved
    
    @staticmethod
    def swap_inter(route1, route2, distance_matrix, capacity_matrix=None,
                   loading_matrix=None, max_weights=None, max_volumes=None,
                   depot_node_ids=None):
        """
        Swap vendors between two different routes.
        
        Args:
            route1, route2: Routes to swap between
            distance_matrix: Distance matrix
            capacity_matrix: Cargo weights (optional)
            loading_matrix: Loading volumes (optional)
            max_weights: [max_weight_route1, max_weight_route2] (optional)
            max_volumes: [max_volume_route1, max_volume_route2] (optional)
            
        Returns:
            tuple: (new_route1, new_route2, improvement_found)
        """
        best_distance = (LocalSearchOperators._route_distance(route1, distance_matrix) +
                        LocalSearchOperators._route_distance(route2, distance_matrix))
        best_route1 = route1[:]
        best_route2 = route2[:]
        improved = False
        
        for i in range(1, len(route1) - 1):
            for j in range(1, len(route2) - 1):
                vendor1 = route1[i]
                vendor2 = route2[j]
                if (LocalSearchOperators._is_depot(vendor1, depot_node_ids) or
                    LocalSearchOperators._is_depot(vendor2, depot_node_ids)):
                    continue
                
                # Check capacity constraints if provided
                if capacity_matrix is not None and max_weights is not None:
                    weight1_before = sum(LocalSearchOperators._get_vendor_capacity(capacity_matrix, v, depot_node_ids) for v in route1 if not LocalSearchOperators._is_depot(v, depot_node_ids))
                    weight2_before = sum(LocalSearchOperators._get_vendor_capacity(capacity_matrix, v, depot_node_ids) for v in route2 if not LocalSearchOperators._is_depot(v, depot_node_ids))
                    
                    weight1_after = weight1_before - LocalSearchOperators._get_vendor_capacity(capacity_matrix, vendor1, depot_node_ids) + LocalSearchOperators._get_vendor_capacity(capacity_matrix, vendor2, depot_node_ids)
                    weight2_after = weight2_before - LocalSearchOperators._get_vendor_capacity(capacity_matrix, vendor2, depot_node_ids) + LocalSearchOperators._get_vendor_capacity(capacity_matrix, vendor1, depot_node_ids)
                    
                    if weight1_after > max_weights[0] or weight2_after > max_weights[1]:
                        continue
                
                if loading_matrix is not None and max_volumes is not None:
                    volume1_before = sum(LocalSearchOperators._get_vendor_volume(loading_matrix, v, depot_node_ids) for v in route1 if not LocalSearchOperators._is_depot(v, depot_node_ids))
                    volume2_before = sum(LocalSearchOperators._get_vendor_volume(loading_matrix, v, depot_node_ids) for v in route2 if not LocalSearchOperators._is_depot(v, depot_node_ids))
                    
                    volume1_after = volume1_before - LocalSearchOperators._get_vendor_volume(loading_matrix, vendor1, depot_node_ids) + LocalSearchOperators._get_vendor_volume(loading_matrix, vendor2, depot_node_ids)
                    volume2_after = volume2_before - LocalSearchOperators._get_vendor_volume(loading_matrix, vendor2, depot_node_ids) + LocalSearchOperators._get_vendor_volume(loading_matrix, vendor1, depot_node_ids)
                    
                    if volume1_after > max_volumes[0] or volume2_after > max_volumes[1]:
                        continue
                
                # Swap vendors
                new_route1 = route1[:]
                new_route2 = route2[:]
                new_route1[i] = vendor2
                new_route2[j] = vendor1
                
                new_distance = (LocalSearchOperators._route_distance(new_route1, distance_matrix) +
                               LocalSearchOperators._route_distance(new_route2, distance_matrix))
                
                if new_distance < best_distance:
                    best_route1 = new_route1
                    best_route2 = new_route2
                    best_distance = new_distance
                    improved = True
        
        return best_route1, best_route2, improved
    
    @staticmethod
    def relocate_inter(route1, route2, distance_matrix, capacity_matrix=None,
                       loading_matrix=None, max_weights=None, max_volumes=None,
                       depot_node_ids=None):
        """
        Relocate a vendor from one route to another.
        
        Args:
            route1, route2: Routes
            distance_matrix: Distance matrix
            capacity_matrix: Cargo weights (optional)
            loading_matrix: Loading volumes (optional)
            max_weights: [max_weight_route1, max_weight_route2] (optional)
            max_volumes: [max_volume_route1, max_volume_route2] (optional)
            
        Returns:
            tuple: (new_route1, new_route2, improvement_found)
        """
        best_distance = (LocalSearchOperators._route_distance(route1, distance_matrix) +
                        LocalSearchOperators._route_distance(route2, distance_matrix))
        best_route1 = route1[:]
        best_route2 = route2[:]
        improved = False
        
        # Try moving from route1 to route2
        for i in range(1, len(route1) - 1):
            vendor = route1[i]
            if LocalSearchOperators._is_depot(vendor, depot_node_ids):
                continue
            
            # Check capacity if moving to route2
            if capacity_matrix is not None and max_weights is not None:
                weight2 = sum(LocalSearchOperators._get_vendor_capacity(capacity_matrix, v, depot_node_ids) for v in route2 if not LocalSearchOperators._is_depot(v, depot_node_ids))
                if weight2 + LocalSearchOperators._get_vendor_capacity(capacity_matrix, vendor, depot_node_ids) > max_weights[1]:
                    continue
            
            if loading_matrix is not None and max_volumes is not None:
                volume2 = sum(LocalSearchOperators._get_vendor_volume(loading_matrix, v, depot_node_ids) for v in route2 if not LocalSearchOperators._is_depot(v, depot_node_ids))
                if volume2 + LocalSearchOperators._get_vendor_volume(loading_matrix, vendor, depot_node_ids) > max_volumes[1]:
                    continue
            
            # Try inserting at each position in route2
            for j in range(1, len(route2)):
                new_route1 = route1[:i] + route1[i+1:]
                new_route2 = route2[:j] + [vendor] + route2[j:]
                
                new_distance = (LocalSearchOperators._route_distance(new_route1, distance_matrix) +
                               LocalSearchOperators._route_distance(new_route2, distance_matrix))
                
                if new_distance < best_distance:
                    best_route1 = new_route1
                    best_route2 = new_route2
                    best_distance = new_distance
                    improved = True
        
        # Try moving from route2 to route1
        for i in range(1, len(route2) - 1):
            vendor = route2[i]
            if LocalSearchOperators._is_depot(vendor, depot_node_ids):
                continue
            
            # Check capacity if moving to route1
            if capacity_matrix is not None and max_weights is not None:
                weight1 = sum(LocalSearchOperators._get_vendor_capacity(capacity_matrix, v, depot_node_ids) for v in route1 if not LocalSearchOperators._is_depot(v, depot_node_ids))
                if weight1 + LocalSearchOperators._get_vendor_capacity(capacity_matrix, vendor, depot_node_ids) > max_weights[0]:
                    continue
            
            if loading_matrix is not None and max_volumes is not None:
                volume1 = sum(LocalSearchOperators._get_vendor_volume(loading_matrix, v, depot_node_ids) for v in route1 if not LocalSearchOperators._is_depot(v, depot_node_ids))
                if volume1 + LocalSearchOperators._get_vendor_volume(loading_matrix, vendor, depot_node_ids) > max_volumes[0]:
                    continue
            
            # Try inserting at each position in route1
            for j in range(1, len(route1)):
                new_route2 = route2[:i] + route2[i+1:]
                new_route1 = route1[:j] + [vendor] + route1[j:]
                
                new_distance = (LocalSearchOperators._route_distance(new_route1, distance_matrix) +
                               LocalSearchOperators._route_distance(new_route2, distance_matrix))
                
                if new_distance < best_distance:
                    best_route1 = new_route1
                    best_route2 = new_route2
                    best_distance = new_distance
                    improved = True
        
        return best_route1, best_route2, improved
    
    @staticmethod
    def improve_solution(solution, max_iterations=100):
        """
        Apply multiple local search operators to improve a solution.
        
        Args:
            solution: RouteSolution object
            max_iterations: Maximum number of improvement iterations
            
        Returns:
            RouteSolution: Improved solution
        """
        improved_solution = solution.copy()
        depot_node_ids = set(getattr(improved_solution, 'depot_node_ids', [0]))
        delivery_map = {}
        if improved_solution.vendors_df is not None:
            for _, row in improved_solution.vendors_df.iterrows():
                node_id = row.get('node_id', None)
                if pd.isna(node_id):
                    continue
                for raw in [
                    row.get('Requested Delivery', None),
                    row.get('Requested Delivery Date', None),
                ]:
                    parsed = pd.to_datetime(raw, errors='coerce')
                    if pd.notna(parsed):
                        delivery_map[int(node_id)] = parsed.to_pydatetime()
                        break
        route_buckets = [
            LocalSearchOperators._get_route_time_bucket(route, improved_solution.vendors_df)
            for route in improved_solution.routes
        ]
        
        for iteration in range(max_iterations):
            improved = False
            
            # Intra-route 2-opt for each route
            for route_idx in range(len(improved_solution.routes)):
                route = improved_solution.routes[route_idx]
                if any(LocalSearchOperators._is_depot(n, depot_node_ids) for n in route[1:]):
                    continue
                new_route, changed = LocalSearchOperators.two_opt_route(
                    route,
                    improved_solution.distance_matrix
                )
                if changed:
                    improved_solution.routes[route_idx] = new_route
                    improved = True
            
            # Intra-route relocate (fine-tuning order)
            for route_idx in range(len(improved_solution.routes)):
                route = improved_solution.routes[route_idx]
                max_w = improved_solution.max_capacity_kg[route_idx] if route_idx < len(improved_solution.max_capacity_kg) else float('inf')
                max_v = improved_solution.max_volume[route_idx] if route_idx < len(improved_solution.max_volume) else float('inf')
                new_route, changed = LocalSearchOperators.relocate_intra(
                    route,
                    improved_solution.distance_matrix,
                    improved_solution.capacity_matrix,
                    improved_solution.loading_matrix,
                    max_w,
                    max_v,
                    depot_node_ids=depot_node_ids
                )
                if changed:
                    if getattr(improved_solution, 'vendor_depot_map', None):
                        new_route = LocalSearchOperators._build_route_with_depots(
                            new_route,
                            delivery_map,
                            improved_solution.vendor_depot_map,
                            depot_node_ids
                        )
                    improved_solution.routes[route_idx] = new_route
                    improved = True

            # Inter-route swap
            for i in range(len(improved_solution.routes)):
                for j in range(i + 1, len(improved_solution.routes)):
                    if (route_buckets[i] is not None and route_buckets[j] is not None and
                        route_buckets[i] != route_buckets[j]):
                        continue
                    max_weights = [
                        improved_solution.max_capacity_kg[i] if i < len(improved_solution.max_capacity_kg) else float('inf'),
                        improved_solution.max_capacity_kg[j] if j < len(improved_solution.max_capacity_kg) else float('inf')
                    ]
                    max_volumes = [
                        improved_solution.max_volume[i] if i < len(improved_solution.max_volume) else float('inf'),
                        improved_solution.max_volume[j] if j < len(improved_solution.max_volume) else float('inf')
                    ]
                    
                    route1, route2, changed = LocalSearchOperators.swap_inter(
                        improved_solution.routes[i],
                        improved_solution.routes[j],
                        improved_solution.distance_matrix,
                        improved_solution.capacity_matrix,
                        improved_solution.loading_matrix,
                        max_weights,
                        max_volumes,
                        depot_node_ids=depot_node_ids
                    )
                    
                    if changed:
                        if getattr(improved_solution, 'vendor_depot_map', None):
                            route1 = LocalSearchOperators._build_route_with_depots(
                                route1,
                                delivery_map,
                                improved_solution.vendor_depot_map,
                                depot_node_ids
                            )
                            route2 = LocalSearchOperators._build_route_with_depots(
                                route2,
                                delivery_map,
                                improved_solution.vendor_depot_map,
                                depot_node_ids
                            )
                        improved_solution.routes[i] = route1
                        improved_solution.routes[j] = route2
                        route_buckets[i] = LocalSearchOperators._get_route_time_bucket(route1, improved_solution.vendors_df)
                        route_buckets[j] = LocalSearchOperators._get_route_time_bucket(route2, improved_solution.vendors_df)
                        improved = True

            # Inter-route relocate (move a vendor to better route)
            for i in range(len(improved_solution.routes)):
                for j in range(i + 1, len(improved_solution.routes)):
                    if (route_buckets[i] is not None and route_buckets[j] is not None and
                        route_buckets[i] != route_buckets[j]):
                        continue
                    # Extract scalar values from arrays
                    max_weight_i = float(improved_solution.max_capacity_kg[i]) if hasattr(improved_solution.max_capacity_kg, '__len__') else float(improved_solution.max_capacity_kg)
                    max_weight_j = float(improved_solution.max_capacity_kg[j]) if hasattr(improved_solution.max_capacity_kg, '__len__') else float(improved_solution.max_capacity_kg)
                    max_volume_i = float(improved_solution.max_volume[i]) if hasattr(improved_solution.max_volume, '__len__') else float(improved_solution.max_volume)
                    max_volume_j = float(improved_solution.max_volume[j]) if hasattr(improved_solution.max_volume, '__len__') else float(improved_solution.max_volume)
                    
                    max_weights = [max_weight_i, max_weight_j]
                    max_volumes = [max_volume_i, max_volume_j]
                    
                    route1, route2, changed = LocalSearchOperators.relocate_inter(
                        improved_solution.routes[i],
                        improved_solution.routes[j],
                        improved_solution.distance_matrix,
                        improved_solution.capacity_matrix,
                        improved_solution.loading_matrix,
                        max_weights,
                        max_volumes,
                        depot_node_ids=depot_node_ids
                    )
                    if changed:
                        if getattr(improved_solution, 'vendor_depot_map', None):
                            route1 = LocalSearchOperators._build_route_with_depots(
                                route1,
                                delivery_map,
                                improved_solution.vendor_depot_map,
                                depot_node_ids
                            )
                            route2 = LocalSearchOperators._build_route_with_depots(
                                route2,
                                delivery_map,
                                improved_solution.vendor_depot_map,
                                depot_node_ids
                            )
                        improved_solution.routes[i] = route1
                        improved_solution.routes[j] = route2
                        route_buckets[i] = LocalSearchOperators._get_route_time_bucket(route1, improved_solution.vendors_df)
                        route_buckets[j] = LocalSearchOperators._get_route_time_bucket(route2, improved_solution.vendors_df)
                        improved = True
            
            improved_solution.invalidate_cache()
            
            if not improved:
                break
        
        return improved_solution

    @staticmethod
    def _get_route_time_bucket(route, vendors_df):
        if vendors_df is None or len(route) <= 2:
            return None
        if 'time_bucket' not in vendors_df.columns:
            return None
        for node in route:
            if node == 0:
                continue
            if 'node_id' in vendors_df.columns:
                match = vendors_df[vendors_df['node_id'] == int(node)]
                if not match.empty:
                    raw_bucket = match.iloc[0].get('time_bucket', None)
                    if isinstance(raw_bucket, str) and raw_bucket.strip():
                        return raw_bucket.strip()
            else:
                vendor_idx = int(node) - 1
                if 0 <= vendor_idx < len(vendors_df):
                    raw_bucket = vendors_df.iloc[vendor_idx].get('time_bucket', None)
                    if isinstance(raw_bucket, str) and raw_bucket.strip():
                        return raw_bucket.strip()
        return None
    
    @staticmethod
    def _get_vendor_capacity(capacity_matrix, vendor_id, depot_node_ids=None):
        """Safely extract vendor capacity as scalar value."""
        if capacity_matrix is None:
            return 0
        if LocalSearchOperators._is_depot(vendor_id, depot_node_ids):
            return 0
        val = capacity_matrix[vendor_id]
        # If it's an array/row, extract the first element
        if hasattr(val, '__len__') and not isinstance(val, (str, bytes)):
            return float(val[0]) if len(val) > 0 else float(val)
        return float(val)
    
    @staticmethod
    def _get_vendor_volume(loading_matrix, vendor_id, depot_node_ids=None):
        """Safely extract vendor volume as scalar value."""
        if loading_matrix is None:
            return 0
        if LocalSearchOperators._is_depot(vendor_id, depot_node_ids):
            return 0
        val = loading_matrix[vendor_id]
        # If it's an array/row, extract the first element
        if hasattr(val, '__len__') and not isinstance(val, (str, bytes)):
            return float(val[0]) if len(val) > 0 else float(val)
        return float(val)
        """Calculate total distance of a route."""
        distance = 0
        for i in range(len(route) - 1):
            distance += distance_matrix[route[i]][route[i+1]]
        return distance
