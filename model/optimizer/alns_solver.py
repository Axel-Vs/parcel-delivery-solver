"""
Adaptive Large Neighborhood Search (ALNS) solver for VRP.
Uses destroy and repair operators on route-based representations.
"""

import numpy as np
import random
import copy
from .route_solution import RouteSolution


class ALNSSolver:
    """
    Adaptive Large Neighborhood Search for Vehicle Routing Problem.
    
    Much faster than MIP for large instances (50+ vendors).
    Uses route-based representation instead of time-expanded binary tensors.
    """
    
    def __init__(self, vendors_df, distance_matrix, time_matrix, 
                 capacity_matrix, loading_matrix, max_capacity_kg, max_ldms_vc=None,
                 discretization_constant=None, min_date=None, max_driving_hours=None,
                 service_time_matrix=None, config=None, evaluation_period=None,
                 max_volume=None, max_linear_length=None):
        """
        Initialize ALNS solver.
        
        Args:
            vendors_df: DataFrame with vendor information
            distance_matrix: Distance matrix [km]
            time_matrix: Time matrix [seconds]
            capacity_matrix: Cargo weight per vendor [kg]
            loading_matrix: Loading volume per vendor [m³]
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
        
        # ALNS parameters
        self.config = config or {}
        self.max_iterations = self.config.get('max_iterations', 1000)
        self.min_removal_size = self.config.get('min_removal_size', 0.1)  # 10% of vendors
        self.max_removal_size = self.config.get('max_removal_size', 0.4)  # 40% of vendors
        self.initial_temperature = self.config.get('initial_temperature', 1000)
        self.cooling_rate = self.config.get('cooling_rate', 0.995)
        
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
        
        self.num_vendors = len(capacity_matrix) - 1  # Exclude depot
    
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
        
        # Generate initial solution using k-means clustering
        # NOTE: Clustering is only a buffer for initialization. ALNS operators
        # below are free to move vendors across cluster boundaries - no constraints.
        current = self.generate_initial_solution()
        best = current.copy()
        
        # Check if initial solution is feasible
        current_feasible = current.is_feasible(check_all=True)  # Get ALL violations
        
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
            current_cost = current.evaluate()
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
                no_improvement_count = 0
                
                if repaired_cost < best.evaluate():
                    best = repaired.copy()
                    if verbose and iteration % 100 == 0:
                        print(f'   - Iteration {iteration}: New best {best.evaluate():.0f} km ({best.get_num_routes()} routes)')
            else:
                no_improvement_count += 1
            
            # Cool down temperature
            temperature *= self.cooling_rate
            
            # Periodically try to merge routes
            if iteration % 250 == 0 and iteration > 0:
                merged = self.try_merge_routes(best)
                if merged.evaluate() < best.evaluate() and merged.is_feasible(check_all=False):
                    best = merged
                    current = merged.copy()
            
            # Early stopping if no improvement for long time
            if no_improvement_count > 200:
                break
        
        # Final route merging attempt
        if verbose:
            print(f'   - Final solution: {best.get_num_routes()} routes, {best.evaluate():.0f} km')
            print(f'   - Attempting route merging...')
        
        merged = self.try_merge_routes(best)
        if merged.evaluate() <= best.evaluate() and merged.is_feasible(check_all=False):
            best = merged
            if verbose:
                print(f'   - After merging: {best.get_num_routes()} routes, {best.evaluate():.0f} km')
        
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
        
        return best
    
    def generate_initial_solution(self):
        """Generate initial solution using k-means clustering by geographical proximity.
        
        NOTE: Clustering is only used as a buffer for initial solution creation to handle
        extreme cases (far vendors, dense clusters) and ensure feasibility. During ALNS
        optimization, routes are FREE to visit vendors from any cluster - there are no
        cluster boundaries enforced. This prevents artificial constraints and allows
        finding optimal solutions across the entire vendor set.
        
        Groups vendors by their geographical coordinates, then builds routes
        within each cluster. Handles extreme cases (far vendors, dense clusters).
        """
        unrouted = list(range(1, self.num_vendors + 1))
        routes = []
        
        # Capacity limits
        max_weight = max(self.max_capacity_kg) if self.max_capacity_kg else float('inf')
        max_volume = max(self.max_ldms_vc) if self.max_ldms_vc else float('inf')
        
        # Step 1: Cluster vendors by geographical proximity using k-means
        clusters = self._cluster_vendors_kmeans(unrouted)
        
        if len(clusters) > 1:
            print(f'   📦 Created {len(clusters)} geographical clusters:')
            for i, cluster in enumerate(clusters):
                print(f'      Cluster {i+1}: {len(cluster)} vendors')
        
        # Step 2: Build routes for each cluster
        for cluster in clusters:
            cluster_routes = self._build_cluster_routes(cluster, max_weight, max_volume)
            routes.extend(cluster_routes)
        
        # Build RouteSolution
        return RouteSolution(
            routes=routes,
            vendors_df=self.vendors_df,
            distance_matrix=self.distance_matrix,
            time_matrix=self.time_matrix,
            capacity_matrix=self.capacity_matrix,
            loading_matrix=self.loading_matrix,
            service_time_matrix=self.service_time_matrix,
            max_capacity_kg=self.max_capacity_kg,
            max_volume=self.max_volume_vc,
            max_linear_length=self.max_linear_length_vc,
            discretization_constant=self.discretization_constant,
            min_date=self.min_date,
            max_driving_hours=self.max_driving_hours,
            evaluation_period=self.evaluation_period
        )
    
    def _cluster_vendors_kmeans(self, vendors):
        """Cluster vendors using k-medoids (PAM) based on travel time distances.
        
        K-medoids is superior to k-means for this problem because:
        - Works directly with the travel time distance matrix
        - More robust to outliers (uses actual data points as centers)
        - Better for non-Euclidean metrics
        
        Returns list of vendor clusters (lists of vendor IDs).
        """
        if len(vendors) <= 3:
            return [vendors]  # Too few to cluster
        
        # Determine optimal number of clusters (target ~3-4 vendors per cluster)
        # For 58 vendors: 58/3.5 ≈ 16-17 clusters
        num_clusters = max(1, len(vendors) // 3)  # Target 3 vendors per cluster, no max limit
        
        # Identify extreme outliers (very far vendors with dedicated routes)
        depot_distances = [(v, self.distance_matrix[0][v]) for v in vendors]
        distances = [d for _, d in depot_distances]
        mean_dist = sum(distances) / len(distances)
        std_dist = (sum((d - mean_dist) ** 2 for d in distances) / len(distances)) ** 0.5
        threshold_far = mean_dist + 2.0 * std_dist  # Only extreme outliers (>2σ)
        
        outliers = [v for v, d in depot_distances if d > threshold_far]
        normal_vendors = [v for v, d in depot_distances if d <= threshold_far]
        
        clusters = []
        
        # Process extreme outliers individually
        for outlier in outliers:
            clusters.append([outlier])
        
        # K-medoids on normal vendors using travel time matrix
        if len(normal_vendors) > 0:
            if len(normal_vendors) <= num_clusters:
                # Too few vendors for clustering, create 1 cluster per vendor group
                clusters.append(normal_vendors)
            else:
                # Run k-medoids on travel times with appropriate cluster count
                num_normal_clusters = max(1, len(normal_vendors) // 3)  # 3 vendors per cluster
                vendor_clusters = self._kmedoids_cluster(normal_vendors, num_normal_clusters)
                clusters.extend(vendor_clusters)
        
        return clusters
    
    
    def _kmedoids_cluster(self, vendors, k):
        """K-medoids (PAM) clustering on travel time distance matrix.
        
        Uses actual vendors as cluster centers (medoids) rather than synthetic centroids.
        More robust than k-means for distance matrices and non-Euclidean metrics.
        
        Args:
            vendors: List of vendor IDs
            k: Number of clusters
            
        Returns:
            List of clusters (each cluster is a list of vendor IDs)
        """
        n = len(vendors)
        
        if n <= k:
            return [[v] for v in vendors]
        
        # Build distance matrix for these vendors
        dist_matrix = np.zeros((n, n))
        for i in range(n):
            for j in range(n):
                if i != j:
                    dist_matrix[i][j] = self.time_matrix[vendors[i]][vendors[j]]
        
        # Initialize medoids using k-medoids++ strategy
        # Start with vendor closest to the "center" (min sum of distances)
        medoid_indices = []
        
        # First medoid: minimize total distance to all other points
        total_distances = np.sum(dist_matrix, axis=1)
        first_medoid = np.argmin(total_distances)
        medoid_indices.append(first_medoid)
        
        # Remaining medoids: farthest from existing medoids
        for _ in range(k - 1):
            min_distances_to_medoids = np.min(
                [dist_matrix[m] for m in medoid_indices], 
                axis=0
            )
            next_medoid = np.argmax(min_distances_to_medoids)
            medoid_indices.append(next_medoid)
        
        # PAM iterations
        max_iters = 50
        improved = True
        iteration = 0
        
        while improved and iteration < max_iters:
            iteration += 1
            improved = False
            
            # Assign each point to nearest medoid
            assignments = np.zeros(n, dtype=int)
            for i in range(n):
                if i not in medoid_indices:
                    distances_to_medoids = [dist_matrix[i][m] for m in medoid_indices]
                    assignments[i] = np.argmin(distances_to_medoids)
            
            # Try swapping each medoid with each non-medoid
            for m_idx, medoid_pos in enumerate(medoid_indices):
                for candidate in range(n):
                    if candidate in medoid_indices:
                        continue
                    
                    # Calculate cost change of swapping medoid_pos with candidate
                    old_cost = 0
                    new_cost = 0
                    
                    for i in range(n):
                        if i == candidate:
                            continue
                        
                        # Current assignment cost
                        if i == medoid_pos:
                            # This medoid is being replaced
                            old_cost += min(dist_matrix[i][m] for m in medoid_indices)
                        else:
                            assigned_medoid = medoid_indices[assignments[i]]
                            old_cost += dist_matrix[i][assigned_medoid]
                        
                        # New assignment cost (with candidate as new medoid)
                        new_medoids = medoid_indices.copy()
                        new_medoids[m_idx] = candidate
                        new_cost += min(dist_matrix[i][m] for m in new_medoids)
                    
                    # Accept swap if it improves
                    if new_cost < old_cost:
                        medoid_indices[m_idx] = candidate
                        improved = True
                        break
                
                if improved:
                    break
        
        # Build final clusters
        clusters = [[] for _ in range(k)]
        
        for i in range(n):
            distances_to_medoids = [dist_matrix[i][m] for m in medoid_indices]
            cluster_idx = np.argmin(distances_to_medoids)
            clusters[cluster_idx].append(vendors[i])
        
        # Filter out empty clusters
        clusters = [c for c in clusters if len(c) > 0]
        
        return clusters
    
    def _build_cluster_routes(self, cluster, max_weight, max_volume):
        """Build one or more routes for a cluster of vendors."""
        routes = []
        unrouted = cluster.copy()
        
        while unrouted:
            route = [0]  # Start from depot
            current_weight = 0
            current_volume = 0
            
            # For single-vendor clusters (outliers), create direct route
            if len(cluster) == 1 and len(unrouted) == 1:
                vendor = unrouted[0]
                routes.append([0, vendor, 0])
                unrouted.remove(vendor)
                continue
            
            # Greedy insertion within cluster: add nearest feasible vendor
            while unrouted:
                last_node = route[-1]
                
                # Find nearest unrouted vendor that fits capacity
                best_vendor = None
                best_distance = float('inf')
                
                for vendor in unrouted:
                    vendor_weight = float(self.capacity_matrix[vendor])
                    vendor_volume = float(self.loading_matrix[vendor])
                    
                    # Check capacity
                    if (current_weight + vendor_weight <= max_weight and 
                        current_volume + vendor_volume <= max_volume):
                        
                        distance = self.distance_matrix[last_node][vendor]
                        if distance < best_distance:
                            best_distance = distance
                            best_vendor = vendor
                
                if best_vendor is None:
                    break  # No more vendors fit in this route
                
                # Try adding vendor and check max_driving constraint
                test_route = route + [best_vendor, 0]
                
                # Check max_driving if specified
                if self.max_driving_hours is not None:
                    route_travel_seconds = 0
                    
                    # If route has only depot + one vendor, just count vendor→depot
                    if len(route) == 1:  # Only [0]
                        route_travel_seconds = self.time_matrix[best_vendor][0]
                    else:
                        # Count middle segments (vendor→vendor)
                        for i in range(1, len(route)-1):
                            route_travel_seconds += self.time_matrix[route[i]][route[i+1]]
                        # Count last vendor → new vendor
                        route_travel_seconds += self.time_matrix[route[-1]][best_vendor]
                        # Count return to depot
                        route_travel_seconds += self.time_matrix[best_vendor][0]
                    
                    route_travel_hours = route_travel_seconds / 3600.0
                    
                    num_stops = len([v for v in test_route if v != 0])
                    service_time_per_stop = self.service_time_matrix[1] / 60.0 if len(self.service_time_matrix) > 1 else 0
                    route_service_hours = num_stops * service_time_per_stop
                    total_time = route_travel_hours + route_service_hours
                    
                    # If adding this vendor violates max_driving, stop adding to this route
                    if total_time > self.max_driving_hours:
                        break
                
                # Add vendor to route
                route.append(best_vendor)
                current_weight += float(self.capacity_matrix[best_vendor])
                current_volume += float(self.loading_matrix[best_vendor])
                unrouted.remove(best_vendor)
            
            # Close route by returning to depot
            if len(route) > 1:  # Has at least one vendor
                route.append(0)
                routes.append(route)
            else:
                # Edge case: no vendors could be added
                if unrouted:
                    vendor = unrouted.pop(0)
                    routes.append([0, vendor, 0])
        
        return routes
    
    def destroy(self, solution, operator, num_remove):
        """
        Destroy operators: remove vendors from solution.
        
        NOTE: These operators are cluster-agnostic - they can remove any vendor
        from any route, regardless of initial cluster assignments. Clustering is
        only used for initial solution generation.
        
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
            all_vendors.extend([v for v in route if v != 0])
        
        removed = random.sample(all_vendors, min(num_remove, len(all_vendors)))
        
        # Remove from routes
        for route in solution.routes:
            route[:] = [v for v in route if v not in removed or v == 0]
        
        # Remove empty routes
        solution.routes = [r for r in solution.routes if len(r) > 2]
        solution.invalidate_cache()
        
        return removed
    
    def destroy_worst_cost(self, solution, num_remove):
        """Remove vendors with highest cost contribution."""
        vendor_costs = []
        
        for route in solution.routes:
            for i, vendor in enumerate(route):
                if vendor == 0:
                    continue
                
                # Calculate cost of removing this vendor
                prev_node = route[i-1] if i > 0 else 0
                next_node = route[i+1] if i < len(route)-1 else 0
                
                current_cost = self.distance_matrix[prev_node][vendor] + self.distance_matrix[vendor][next_node]
                direct_cost = self.distance_matrix[prev_node][next_node]
                savings = current_cost - direct_cost
                
                vendor_costs.append((vendor, savings))
        
        # Sort by worst savings (highest cost)
        vendor_costs.sort(key=lambda x: x[1], reverse=True)
        removed = [v for v, _ in vendor_costs[:num_remove]]
        
        # Remove from routes
        for route in solution.routes:
            route[:] = [v for v in route if v not in removed or v == 0]
        
        solution.routes = [r for r in solution.routes if len(r) > 2]
        solution.invalidate_cache()
        
        return removed
    
    def destroy_shaw(self, solution, num_remove):
        """Shaw removal: remove similar vendors (by distance)."""
        # Pick random seed vendor
        all_vendors = []
        for route in solution.routes:
            all_vendors.extend([v for v in route if v != 0])
        
        if not all_vendors:
            return []
        
        seed = random.choice(all_vendors)
        
        # Calculate relatedness (inverse distance)
        relatedness = [(v, 1.0 / (self.distance_matrix[seed][v] + 1)) for v in all_vendors if v != seed]
        relatedness.sort(key=lambda x: x[1], reverse=True)
        
        removed = [seed] + [v for v, _ in relatedness[:num_remove-1]]
        
        # Remove from routes
        for route in solution.routes:
            route[:] = [v for v in route if v not in removed or v == 0]
        
        solution.routes = [r for r in solution.routes if len(r) > 2]
        solution.invalidate_cache()
        
        return removed
    
    def repair(self, solution, removed_vendors, operator):
        """
        Repair operators: reinsert removed vendors.
        
        NOTE: These operators are cluster-agnostic - they can insert vendors into
        any route, regardless of initial cluster assignments. Routes are free to
        visit vendors from different clusters during optimization.
        
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
        for vendor in removed_vendors:
            best_cost = float('inf')
            best_route_idx = -1
            best_position = -1
            
            # Try inserting in existing routes
            for route_idx, route in enumerate(solution.routes):
                for pos in range(1, len(route)):
                    # Check capacity
                    route_weight, route_volume = solution.get_route_capacity_usage(route_idx)
                    vehicle_idx = route_idx
                    max_weight = self.max_capacity_kg[vehicle_idx] if vehicle_idx < len(self.max_capacity_kg) else float('inf')
                    max_volume = self.max_ldms_vc[vehicle_idx] if vehicle_idx < len(self.max_ldms_vc) else float('inf')
                    
                    if route_weight + self.capacity_matrix[vendor] > max_weight:
                        continue
                    if route_volume + self.loading_matrix[vendor] > max_volume:
                        continue
                    
                    # Check max_driving constraint
                    if self.max_driving_hours is not None:
                        test_route = route[:pos] + [vendor] + route[pos:]
                        route_travel_seconds = 0
                        
                        # Count all edges in route except depot→first
                        if len(test_route) > 2:  # Has at least one vendor
                            for i in range(1, len(test_route)-1):
                                route_travel_seconds += self.time_matrix[test_route[i]][test_route[i+1]]
                            # Add return to depot
                            route_travel_seconds += self.time_matrix[test_route[-2]][0]
                        else:
                            # Single vendor: just vendor→depot
                            route_travel_seconds = self.time_matrix[vendor][0]
                        
                        route_travel_hours = route_travel_seconds / 3600.0
                        num_stops = len([v for v in test_route if v != 0])
                        service_time_per_stop = self.service_time_matrix[1] / 60.0 if len(self.service_time_matrix) > 1 else 0
                        route_service_hours = num_stops * service_time_per_stop
                        total_time = route_travel_hours + route_service_hours
                        
                        if total_time > self.max_driving_hours:
                            continue  # Skip this insertion - violates time constraint
                    
                    # Calculate insertion cost
                    prev_node = route[pos-1]
                    next_node = route[pos]
                    
                    current_cost = self.distance_matrix[prev_node][next_node]
                    new_cost = self.distance_matrix[prev_node][vendor] + self.distance_matrix[vendor][next_node]
                    insertion_cost = new_cost - current_cost
                    
                    if insertion_cost < best_cost:
                        best_cost = insertion_cost
                        best_route_idx = route_idx
                        best_position = pos
            
            # Insert at best position or create new route
            if best_route_idx >= 0:
                solution.routes[best_route_idx].insert(best_position, vendor)
            else:
                # Create new route
                solution.routes.append([0, vendor, 0])
        
        solution.invalidate_cache()
        return solution
    
    def repair_regret2(self, solution, removed_vendors):
        """Regret-2 insertion: prioritize vendors with large regret."""
        uninserted = set(removed_vendors)
        
        while uninserted:
            best_regret = -float('inf')
            best_vendor = None
            best_route_idx = -1
            best_position = -1
            
            for vendor in uninserted:
                # Find best and second-best insertion positions
                costs = []
                
                for route_idx, route in enumerate(solution.routes):
                    for pos in range(1, len(route)):
                        # Check capacity
                        route_weight, route_volume = solution.get_route_capacity_usage(route_idx)
                        vehicle_idx = route_idx
                        max_weight = self.max_capacity_kg[vehicle_idx] if vehicle_idx < len(self.max_capacity_kg) else float('inf')
                        max_volume = self.max_ldms_vc[vehicle_idx] if vehicle_idx < len(self.max_ldms_vc) else float('inf')
                        
                        if route_weight + self.capacity_matrix[vendor] > max_weight:
                            continue
                        if route_volume + self.loading_matrix[vendor] > max_volume:
                            continue
                        
                        # Check max_driving constraint
                        if self.max_driving_hours is not None:
                            test_route = route[:pos] + [vendor] + route[pos:]
                            route_travel_seconds = 0
                            
                            # Count all edges in route except depot→first
                            if len(test_route) > 2:  # Has at least one vendor
                                for i in range(1, len(test_route)-1):
                                    route_travel_seconds += self.time_matrix[test_route[i]][test_route[i+1]]
                                # Add return to depot
                                route_travel_seconds += self.time_matrix[test_route[-2]][0]
                            else:
                                # Single vendor: just vendor→depot
                                route_travel_seconds = self.time_matrix[vendor][0]
                            
                            route_travel_hours = route_travel_seconds / 3600.0
                            num_stops = len([v for v in test_route if v != 0])
                            service_time_per_stop = self.service_time_matrix[1] / 60.0 if len(self.service_time_matrix) > 1 else 0
                            route_service_hours = num_stops * service_time_per_stop
                            total_time = route_travel_hours + route_service_hours
                            
                            if total_time > self.max_driving_hours:
                                continue  # Skip this insertion - violates time constraint
                        
                        # Calculate insertion cost
                        prev_node = route[pos-1]
                        next_node = route[pos]
                        
                        current_cost = self.distance_matrix[prev_node][next_node]
                        new_cost = self.distance_matrix[prev_node][vendor] + self.distance_matrix[vendor][next_node]
                        insertion_cost = new_cost - current_cost
                        
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
                    solution.routes.append([0, vendor, 0])
                break
            
            solution.routes[best_route_idx].insert(best_position, best_vendor)
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
                    
                    # Skip if either route is empty or just depot
                    if len(route_i) <= 2 or len(route_j) <= 2:
                        continue
                    
                    # Get vendors from both routes (exclude depot)
                    vendors_i = [v for v in route_i if v != 0]
                    vendors_j = [v for v in route_j if v != 0]
                    
                    # Try to merge route j into route i
                    # Test combined route: depot → vendors_i → vendors_j → depot
                    combined_vendors = vendors_i + vendors_j
                    test_route = [0] + combined_vendors + [0]
                    
                    # Check capacity constraints
                    total_weight = sum(self.capacity_matrix[v] for v in combined_vendors)
                    total_volume = sum(self.loading_matrix[v] for v in combined_vendors)
                    max_weight = max(self.max_capacity_kg) if self.max_capacity_kg else float('inf')
                    max_volume = max(self.max_ldms_vc) if self.max_ldms_vc else float('inf')
                    
                    if total_weight > max_weight or total_volume > max_volume:
                        continue
                    
                    # Check max_driving constraint
                    if self.max_driving_hours is not None:
                        route_travel_seconds = 0
                        for k in range(1, len(test_route)-1):
                            route_travel_seconds += self.time_matrix[test_route[k]][test_route[k+1]]
                        # Add return to depot
                        route_travel_seconds += self.time_matrix[test_route[-2]][0]
                        
                        route_travel_hours = route_travel_seconds / 3600.0
                        num_stops = len(combined_vendors)
                        service_time_per_stop = self.service_time_matrix[1] / 60.0 if len(self.service_time_matrix) > 1 else 0
                        route_service_hours = num_stops * service_time_per_stop
                        total_time = route_travel_hours + route_service_hours
                        
                        if total_time > self.max_driving_hours:
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
