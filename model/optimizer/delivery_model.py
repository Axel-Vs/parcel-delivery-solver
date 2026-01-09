# Import necessary libraries
import os
import sys
from pathlib import Path

# Determine the project root directory
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

# Import required modules
from utils.project_utils import *
from ortools.linear_solver import pywraplp
from pyqubo import Array, Constraint, Placeholder

# Import metaheuristic solvers
try:
    from .alns_solver import ALNSSolver
    from .route_solution import RouteSolution
    from .local_search import LocalSearchOperators
    METAHEURISTIC_AVAILABLE = True
except ImportError:
    METAHEURISTIC_AVAILABLE = False

# Define the DeliveryOptimizer class
class DeliveryOptimizer:
    def __init__(self, evaluation_period, discretization_constant, time_expanded_network, time_expanded_network_index,
                 Tau_hours, distance_matrix, time_distance_matrix, disc_time_distance_matrix, capacity_matrix, loading_matrix,
                 max_capacity, max_ldms, max_driving, is_gap, mip_gap, maximum_minutes, vendors_df=None):
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
        self.vendors_df = vendors_df

        # Calculate derived attributes
        self.des_max_driving = max_driving / discretization_constant
        self.length = len(self.distance_matrix)
        self.max_num_vehicles = self.length - 1

        # Log information about the problem size
        log.info('Number of nodes %i' % self.length)
        log.info('Solving for maximum number of %i vehicles...' % self.max_num_vehicles)

        # Set maximum capacity and load limits for vehicles
        self.max_capacity = max_capacity
        self.max_capacity_kg = [max_capacity * 1000] * self.max_num_vehicles
        self.max_ldms = max_ldms
        self.max_ldms_vc = [max_ldms] * self.max_num_vehicles

        # Initialize solution containers
        self.connections_solution = None
        self.vehicles_solution = None
        self.used_metaheuristic = False

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
                self.max_ldms_vc = [self.max_ldms] * self.max_num_vehicles
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
        
        # Create ALNS solver with balanced parameters
        alns_config = {
            'max_iterations': max_iterations,
            'min_removal_size': 0.15,
            'max_removal_size': 0.45,
            'initial_temperature': 1500,
            'cooling_rate': 0.997
        }
        
        alns = ALNSSolver(
            vendors_df=self.vendors_df,
            distance_matrix=self.distance_matrix,
            time_matrix=self.time_distance_matrix,
            capacity_matrix=self.capacity_matrix,
            loading_matrix=self.loading_matrix,
            max_capacity_kg=self.max_capacity_kg,
            max_ldms_vc=self.max_ldms_vc,
            discretization_constant=self.discretization_constant,
            min_date=self.evaluation_period[0] if isinstance(self.evaluation_period, list) else self.evaluation_period,
            config=alns_config
        )
        
        # Solve with ALNS
        solution = alns.solve(verbose=verbose)
        
        # Apply local search improvement
        if verbose:
            print('   - Applying local search improvement...')
        solution = LocalSearchOperators.improve_solution(solution, max_iterations=100)
        
        # Check feasibility
        is_feasible = solution.is_feasible(check_all=True)
        status = 0 if is_feasible else 2
        
        if verbose:
            if is_feasible:
                print(f'   ✓ Solution found: {solution.get_num_routes()} routes, {solution.evaluate():.0f} km')
            else:
                print(f'   ✗ Solution infeasible: {len(solution._constraint_violations)} violations')
                for violation in solution._constraint_violations[:5]:
                    print(f'     - {violation}')
        
        # Convert route solution to MIP-style x and y matrices for compatibility
        x, y = self._convert_routes_to_mip_format(solution)
        
        # Store solution and mark as metaheuristic
        self.connections_solution = x
        self.vehicles_solution = y
        self.used_metaheuristic = True
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
                                node_name = 'Depot'
                                location_info = ''
                            else:
                                node_name = f'Vendor {int(node)}'
                                # Get city and postcode from vendors_df
                                location_info = ''
                                if vendors_df is not None and int(node) <= len(vendors_df):
                                    try:
                                        vendor_row = vendors_df.iloc[int(node) - 1]
                                        # Try multiple ways to access the columns
                                        city = ''
                                        postcode = ''
                                        if 'Vendor City' in vendor_row.index:
                                            city = str(vendor_row['Vendor City']).strip()
                                        if 'Vendor Postcode' in vendor_row.index:
                                            postcode = str(vendor_row['Vendor Postcode']).strip()
                                        if city and postcode:
                                            location_info = f' ({city}, PLZ {postcode})'
                                    except Exception as e:
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
        
        if not vehicles_used:
            print('⚠️  No vehicles used in solution - nothing to plot')
            return
        
        # Extract coordinates from vendors_df
        if self.vendors_df is None:
            print('⚠️  No vendor data available for plotting')
            return
        
        # Build coordinate mapping: node_id -> (lat, lon) and node info
        coords = {}
        node_info = {}
        
        # Depot is node 0 - get from first vendor's recipient coordinates
        depot_lat = None
        depot_lon = None
        
        if len(self.vendors_df) > 0:
            depot_lat = self.vendors_df.iloc[0].get('recipient_latitude', None)
            depot_lon = self.vendors_df.iloc[0].get('recipient_longitude', None)
            
            # If recipient coordinates not available, use Seattle as default depot
            if depot_lat is None or depot_lon is None:
                print('⚠️  Depot coordinates not found, using Seattle as default')
                depot_lat = 47.6062  # Seattle
                depot_lon = -122.3321
            
            coords[0] = (depot_lat, depot_lon)
            node_info[0] = {'name': 'Depot (Seattle)', 'type': 'depot'}
            print(f'📍 Depot coordinates: {depot_lat}, {depot_lon}')
        
        # Vendors are nodes 1, 2, 3, ... (node_id = dataframe_index)
        for node_id, row in self.vendors_df.iterrows():
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
                print(f'📍 Vendor {node_id} ({vendor_city}): {vendor_lat}, {vendor_lon}')
        
        print(f'📊 Total nodes with coordinates: {len(coords)} (Depot + {len(coords)-1} vendors)')
        
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
            tiles=None,
            control_scale=True,
            zoom_control=True,
            scrollWheelZoom=True,
            dragging=True,
            prefer_canvas=True
        )
        
        # Fit map to show all points with extra padding for better overview
        m.fit_bounds([[min_lat, min_lon], [max_lat, max_lon]], padding=[100, 100])
        
        # Define aesthetic colors for each vehicle (modern, slightly darker palette)
        colors = ['#C0392B', '#2980B9', '#27AE60', '#8E44AD', '#D68910', '#16A085', '#CA6F1E', '#2C3E50']
        
        # Extract routes for each vehicle (excluding depot→vendor arcs) and calculate stats
        routes = {}
        route_stats = {}  # Store vehicle statistics
        
        # Check if metaheuristic was used (stores arcs at time 0)
        is_metaheuristic = getattr(self, 'used_metaheuristic', False)
        
        for k in vehicles_used:
            route_arcs = []
            
            if is_metaheuristic:
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
            if route_arcs:
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
                
                routes[k] = route_path
                
                # Calculate vehicle statistics
                total_cargo = 0
                total_loading = 0
                total_distance = 0
                vendors_visited = []
                segments = []  # Store segment details for route card display
                
                for i in range(len(route_path) - 1):
                    node_from = route_path[i]
                    node_to = route_path[i + 1]
                    
                    # Add cargo and loading from vendor nodes
                    if node_from != 0:
                        total_cargo += self.capacity_matrix[node_from]
                        total_loading += self.loading_matrix[node_from]
                        vendors_visited.append(node_from)
                    
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
                        
                        # Store segment details
                        segments.append({
                            'from_id': node_from,
                            'to_id': node_to,
                            'distance': seg_distance,
                            'duration': seg_duration,
                            'avg_speed': avg_speed
                        })
                
                route_stats[k] = {
                    'total_cargo': total_cargo,
                    'total_loading': total_loading,
                    'total_distance': total_distance,
                    'num_vendors': len(set(vendors_visited)),
                    'vendors': vendors_visited,
                    'segments': segments  # Add segment details
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
                if node != 0:  # Not depot
                    step_num += 1
                    if node not in vendor_visits:
                        vendor_visits[node] = {'vehicle': vehicle_id, 'route_number': route_mapping[vehicle_id], 'step': step_num, 'total_steps': len([n for n in route if n != 0])}
        
        # Plot routes using OSRM for actual street routing
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
                if node_from != 0:  # Not depot
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
                                    # For same location pickups, draw a small circular arrow indicator
                                    folium.CircleMarker(
                                        location=(lat_from, lon_from),
                                        radius=8,
                                        color=color,
                                        fill=True,
                                        fillColor=color,
                                        fillOpacity=0.6,
                                        popup=folium.Popup(popup_html, max_width=280)
                                    ).add_to(vehicle_group)
                                else:
                                    # Normal route with polyline
                                    folium.PolyLine(
                                        route_coords,
                                        color=color,
                                        weight=5,
                                        opacity=0.9,
                                        popup=folium.Popup(popup_html, max_width=280),
                                        smooth_factor=2.0
                                    ).add_to(vehicle_group)
                            else:
                                raise Exception("No route found")
                        else:
                            raise Exception(f"OSRM API error: {response.status_code}")
                            
                    except Exception as e:
                        print(f'  ⚠️  Could not get route from OSRM for segment {node_from}→{node_to}: {e}')
                        # Fallback to straight line with clear indication
                        folium.PolyLine(
                            [(lat_from, lon_from), (lat_to, lon_to)],
                            color=color,
                            weight=3,
                            opacity=0.4,
                            dash_array='10, 10',
                            popup=f'Route {route_number}: {node_info[node_from]["name"]} → {node_info[node_to]["name"]} (Direct line - routing unavailable)',
                            tooltip=f'V{vehicle_id}: Direct line'
                        ).add_to(vehicle_group)
            
            # Add route group to map
            vehicle_group.add_to(m)
        
        # Add markers for all nodes with clear differentiation
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
                    popup=folium.Popup(popup_html, max_width=300),
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
                    fill=True,
                    fillColor='#E74C3C',
                    fillOpacity=0.9,
                    weight=3,
                    opacity=1.0,
                ).add_to(m)
                
            else:
                # Vendor marker - modern card design
                vendor_colors_hex = ['#2980B9', '#27AE60', '#8E44AD', '#D68910', '#16A085', '#CA6F1E']
                vendor_color_hex = vendor_colors_hex[(node_id - 1) % len(vendor_colors_hex)]
                
                # Get cargo and loading for this vendor
                vendor_cargo = self.capacity_matrix[node_id] if node_id < len(self.capacity_matrix) else 0
                vendor_loading = self.loading_matrix[node_id] if node_id < len(self.loading_matrix) else 0
                
                # Get solution stage information
                visit_info = vendor_visits.get(node_id, {})
                assigned_vehicle = visit_info.get('vehicle', 'N/A')
                assigned_route = visit_info.get('route_number', 'N/A')
                visit_step = visit_info.get('step', 'N/A')
                total_vendor_stops = visit_info.get('total_steps', 'N/A')
                
                popup_html = f"""
                <div style="font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif; width: 300px;">
                    <div style="background: {vendor_color_hex}; 
                                color: white; padding: 20px; border-radius: 12px 12px 0 0; 
                                margin: -15px -15px 15px -15px;">
                        <div style="display: flex; align-items: center; gap: 14px;">
                            <div style="background: rgba(255,255,255,0.15); padding: 11px; border-radius: 10px; 
                                        font-size: 22px; width: 48px; height: 48px; display: flex; 
                                        align-items: center; justify-content: center; backdrop-filter: blur(4px);">
                                🏭
                            </div>
                            <div>
                                <div style="font-size: 11px; opacity: 0.85; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase;">VENDOR {node_id}</div>
                                <h4 style="margin: 5px 0 0 0; font-size: 15px; font-weight: 600; letter-spacing: -0.01em;">{info['name']}</h4>
                            </div>
                        </div>
                    </div>
                    <div style="padding: 16px;">
                        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 12px; 
                                    padding: 10px; background: #f8f9fa; border-radius: 8px;">
                            <span style="font-size: 20px;">📍</span>
                            <div>
                                <div style="font-size: 14px; font-weight: 600; color: #2c3e50;">{info['city']}</div>
                                <div style="font-size: 12px; color: #7f8c8d;">ZIP: {info['plz']}</div>
                            </div>
                        </div>
                        <div style="background: #F8F7F4; padding: 12px 14px; border-radius: 10px; margin-bottom: 14px; border: 1px solid #E8E6E0;">
                            <div style="font-size: 11px; color: #5A7A65; font-weight: 600; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px;">
                                📦 CARGO TO PICKUP
                            </div>
                            <div style="display: flex; justify-content: space-between; align-items: center;">
                                <div style="flex: 1;">
                                    <div style="font-size: 12px; color: #9A9892; margin-bottom: 4px; font-weight: 500;">Weight:</div>
                                    <div style="font-size: 17px; font-weight: 600; color: #2C2B28;">{vendor_cargo:,.0f} kg</div>
                                </div>
                                <div style="width: 1px; height: 32px; background: #E8E6E0; margin: 0 12px;"></div>
                                <div style="flex: 1;">
                                    <div style="font-size: 12px; color: #9A9892; margin-bottom: 4px; font-weight: 500;">Volume:</div>
                                    <div style="font-size: 17px; font-weight: 600; color: #2C2B28;">{vendor_loading:,.1f} m³</div>
                                </div>
                            </div>
                        </div>
                        <div style="background: linear-gradient(90deg, {vendor_color_hex}20 0%, {vendor_color_hex}05 100%); 
                                    padding: 10px 12px; border-radius: 8px; margin-bottom: 12px; border: 1px solid {vendor_color_hex}40;">
                            <div style="font-size: 11px; color: {vendor_color_hex}; font-weight: 600; margin-bottom: 6px;">
                                🚚 SOLUTION STAGE
                            </div>
                            <div style="display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: #2c3e50;">
                                <div>
                                    <span style="color: #777;">Assigned to:</span>
                                    <b style="margin-left: 4px;">Route {assigned_route}</b>
                                </div>
                                <div style="background: {vendor_color_hex}; color: white; padding: 4px 10px; 
                                            border-radius: 12px; font-weight: 600; font-size: 11px;">
                                    Stop {visit_step}/{total_vendor_stops}
                                </div>
                            </div>
                        </div>
                        <div style="background: linear-gradient(90deg, {vendor_color_hex}15 0%, transparent 100%); 
                                    padding: 8px 12px; border-left: 3px solid {vendor_color_hex}; border-radius: 4px;">
                            <p style="margin: 0; font-size: 12px; color: #555;">
                                <b>Pickup Location</b> • Active Route
                            </p>
                        </div>
                    </div>
                </div>
                """
                
                # Use different colors for vendors to distinguish them
                vendor_colors = ['blue', 'green', 'purple', 'orange', 'cadetblue', 'darkgreen']
                vendor_color = vendor_colors[(node_id - 1) % len(vendor_colors)]
                
                folium.Marker(
                    location=[lat, lon],
                    popup=folium.Popup(popup_html, max_width=320),
                    tooltip=folium.Tooltip(f"<b style='font-size: 13px;'>📦 {info['name']}</b><br><span style='font-size: 11px;'>{info['city']}</span>", direction='auto'),
                    icon=folium.Icon(
                        color=vendor_color,
                        icon='cube',
                        prefix='fa',
                        icon_color='white'
                    )
                ).add_to(m)
                
                # Add small circle around vendor (unfilled)
                folium.CircleMarker(
                    location=[lat, lon],
                    radius=12,
                    color=vendor_color_hex,
                    fill=False,
                    weight=2,
                    opacity=0.6
                ).add_to(m)
        
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
        
        # Calculate total distance across all routes
        total_distance = sum(stats['total_distance'] for stats in route_stats.values())
        distance_formatted = f'{total_distance:,.1f}'
        
        # Get solver info
        solver_type = 'Metaheuristic (ALNS)' if is_metaheuristic else 'Exact (MIP)'
        solving_time = getattr(self, 'secs_taken', 0)
        num_depots = 1
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
                
                console.log('✓ Exposed', Object.keys(window.routeLayerGroups).length, 'route layers to window');
            }}
            
            if (document.readyState === 'loading') {{
                document.addEventListener('DOMContentLoaded', initRouteLayerExposure);
            }} else {{
                initRouteLayerExposure();
            }}
        </script>
        """
        m.get_root().html.add_child(folium.Element(expose_layers_js))
        
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