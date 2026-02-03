import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from calendar import monthrange
import datetime
import copy
import os
import pandas as pd
import math
import warnings

# Try to import Google Maps client
try:
    import googlemaps
    GOOGLEMAPS_AVAILABLE = True
except ImportError:
    GOOGLEMAPS_AVAILABLE = False
    googlemaps = None

try:
    import openrouteservice as ors
except Exception:
    # Provide a lightweight dummy ORS client for smoke tests when openrouteservice
    # is not installed or network access is not available. This dummy returns
    # zero distance/duration matrices so the rest of the pipeline can be exercised
    # without external dependencies.
    class _DummyORSClient:
        def __init__(self, key=None):
            self.key = key

        def distance_matrix(self, locations, profile='driving-hgv', metrics=None, units='km', resolve_locations=False, validate=False):
            n = len(locations)
            distances = [[0 for _ in range(n)] for _ in range(n)]
            durations = [[0 for _ in range(n)] for _ in range(n)]
            return {'distances': distances, 'durations': durations}

    ors = type('ors', (), {'Client': _DummyORSClient})

from utils.project_utils import *


warnings.simplefilter(action='ignore', category=FutureWarning)

class Graph:
    """Create Graph & Time-Expanded Graph according to the period of time given.
    Input: Dictionary with all the business parameters / period
    """
    def __init__(self, params): 
        self.discretization_constant = params['discretization_constant']
        self.starting_depot = params['starting_depot']
        self.closing_depot = params['closing_depot']
        self.vendor_start_hr = params['vendor_start_hr']
        self.pickup_end_hr = params['pickup_end_hr']
        self.loading = params['loading']        
        self.earl_arv = params['earl_arv']   # Earliest arrival to vendor (days) 
        self.late_arv = params['late_arv']   # Latest arrival to vendor (days)
        self.max_driving = params['max_driving']
        self.max_weight = params['max_weight']
        self.max_volume = params.get('max_volume', 90)
        self.max_linear_length = params.get('max_linear_length', 16.1)
        self.plot_centered_coordinates = params['plot_centered_coordinates']
        
        # Arc pruning parameters for optimization
        self.max_feasible_distance = params.get('max_feasible_distance', 3000)  # km
        self.time_window_sampling_threshold = params.get('time_window_sampling_threshold', 20)  # periods
        self.time_window_sample_size = params.get('time_window_sample_size', 20)  # max samples
        
        # Initialize routing client (Google Maps preferred, ORS as fallback)
        self.google_api_key = params.get('google_maps_api_key', None)
        self.ors_api_key = params.get('ors_api_key', '5b3ce3597851110001cf6248448674063e0d4ec38216e52a54d951b5')
        
        if self.google_api_key and GOOGLEMAPS_AVAILABLE:
            self.client = googlemaps.Client(key=self.google_api_key)
            self.routing_provider = 'google'
            log.info('Using Google Maps for distance/time calculations')
        else:
            self.client = ors.Client(key=self.ors_api_key)
            self.routing_provider = 'ors'
            if not self.google_api_key:
                log.info('No Google Maps API key provided, using ORS/Haversine fallback')
        
        self.distance_matrix = None
        self.time_distance_matrix = None
        self.disc_time_distance_matrix = None
        # self.capacity_matrix = None
        self.solution = None

    def export_network_to_csv(self, path = "./python_files/Graphs/"):
        """Export Graph as 4 files that fully define it.
        """    
        folder_name = datetime.datetime.now()+"/"
        path = path+folder_name
        os.mkdir(path)
        params_export_dict = {
            'discretization_constant':self.discretization_constant,
            'starting_depot':self.starting_depot,
            'closing_depot':self.closing_depot,
            'vendor_start_hr':self.vendor_start_hr,
            'pickup_end_hr':self.pickup_end_hr,
            'loading':self.loading,
            'max_driving':self.max_driving,
            'max_weight':self.max_weight,
            'max_volume':self.max_volume,
            'max_linear_length':self.max_linear_length,
            'germany_coordinates':self.germany_coordinates
        }
        with open(path+'params.csv', 'w') as f:
            for key in params_export_dict.keys():
                f.write("%s,%s\n"%(key,params_export_dict[key]))
        self.distance_matrix.write_csv(path+"distance_matrix")
        self.time_distance_matrix.write_csv(path+"time_distance_matrix")
        self.capacity_matrix.write_csv(path+"capacity_matrix")
    
    def get_subset_of_network(self, first_day: int, last_day: int):
        """
        Get a subset of a Graph for the rolling horizon scheduling
        """
        return self.distance_matrix, self.time_distance_matrix, self.capacity_matrix, self.loading_matrix

    def update_wrt_solution(self, x: bool ,y: bool ,first_day: int, last_day: int, update_interval: int):
        """
        Updates the (partial) Graph w.r.t. the (partial) solution from the model
        input:
            x = Solution variable of model. Boolean vector with: k,i,t,j,t'
            y = tbd
            first_day = First day to consider
            last_day = Last day to consider, just as info, probably not needed?!
            update_interval = Consider first_day up to here.
        Output:
            None
        """
        # For all i, t, j, t' remove all connections but the one we took up until first_day+update_interval
        
        # Filter x for t'< first_day+update_interval
        # Only keep i, t, j, t' in time_expnanded_network that was actually taken (i.e. set to 1 in x)
        # -> For >= first_day+update_interval: Remove all connections to and from i's that have been visited
        # Same for Capacity_matrix, distance_matrix, loading_matrix

        # distance_matrix: i,j
        # capacity_matrix: i
        # disc_time_distance_matrix: i,j
        # time_expanded_network: i, t, j, t'

    def read_data(self,  period, initial_dataset):
        """Read pre-processing data, filter the period given and extracts coordinates and vendor dataset.
        Input: Period and dataset path.
        Output: List with coordinates and vendors information dataframe
        """
        # initial_dataset = pd.read_csv(path)
        #---!
        # initial_dataset = initial_dataset[3:6]
        #---!


        # recipient_extract
        recipient_values = initial_dataset[['recipient_longitude', 'recipient_latitude']].apply(list, axis=1)

        # vendor_extrac
        if not isinstance(period, list):
            in_p = period.replace(hour=0, minute=0)
            en_p = period.replace(hour=23, minute=59)

            in_p = in_p.strftime('%Y-%m-%d %H:%M:%S')
            en_p = en_p.strftime('%Y-%m-%d %H:%M:%S')
            period = [in_p, en_p]

        filter_geocoded = filter_dates(initial_dataset, period)       
 
        filter_geocoded.reset_index(drop=True, inplace=True)
        df_coord = filter_geocoded[['vendor_longitude', 'vendor_latitude']]
        vendor_coordinates = df_coord.apply(list, axis=1)       # just coordinates 

        base_cols = [
            'vendor Name',
            'vendor_longitude',
            'vendor_latitude',
            'Calculated Loading Meters',
            'Total Gross Weight',
            'Requested Loading',
            'Requested Delivery',
            'Vendor City',
            'Vendor Postcode',
            'Vendor Country Name',
            'Recipient City',
            'Recipient Country Name'
        ]
        if 'time_bucket' in filter_geocoded.columns:
            base_cols.append('time_bucket')
        vendors_df = filter_geocoded[base_cols]
        vendors_df.index = vendors_df.index + 1
        vendors_df = vendors_df
        if len(vendors_df) !=0:
            print('Number of vendors extracted:', len(vendors_df))   # complete dataset
        
        # consolidation
        complete_coordinates = pd.concat([recipient_values.head(1), vendor_coordinates], ignore_index=False)
        complete_coordinates = list(complete_coordinates)
        
        return complete_coordinates, vendors_df
        

    def create_network(self, complete_coordinates, vendors_df):
        """Creates Fix connection network.
        Input: Nodes coordinates, source index, routing client, setting to zero parameter.
        Output: Distance and Time Travel Matrix
        """
        self.distance_matrix, self.time_distance_matrix = info_matrix_definition(
            complete_coordinates, 0, 'horizontal', self.client, self.routing_provider
        )
        self.Nodes = cr_nodes(vendors_df)       
        self.length = len(self.distance_matrix)
        print('Distance & Time Distance Matrix created')

    def discretize(self):
        """Discretize Loading Time, Maximum Driving Hours, Time Distance Matrix.      
        """
        self.disc_time_distance_matrix, self.discretization_constant = discret_time_matrix(self.time_distance_matrix, self.discretization_constant)
        self.max_driving = np.max(  [self.max_driving] + [math.ceil(self.time_distance_matrix[i][0]/3600) for i in range(len(self.distance_matrix))] )
        
        print('Maximum hours allow to drive:', self.max_driving)
        print('New discretization constant allow to drive:', self.discretization_constant)
        self.disc_max_driving = self.max_driving/self.discretization_constant
        self.disc_loading = math.floor(self.loading/self.discretization_constant)

        print('Discretized objects: [Loading Time, Maximum Driving Hours, Time Distance Matrix]')


    def time_definition(discretization_constant, upper_time, lower_time):
            ext_days = ((upper_time-lower_time).days+1)
            ext_hours = ext_days*24
            # print('Number of days considered: ', ext_days)

            min_overall_time = int(lower_time.strftime("%H") )  
            max_dep_arrival_time = int(upper_time.strftime("%H") )   

            Tau_hours = np.arange(min_overall_time, 
                                    max_dep_arrival_time + ext_hours + discretization_constant, 
                                    discretization_constant)

            while len(Tau_hours[Tau_hours>=25]) != 0:
                    next_day_length = len(Tau_hours[Tau_hours>=25])
                    big_than = Tau_hours[Tau_hours>=25]
                    exceed = big_than[0] -24
                    Tau_hours[Tau_hours>=25] = np.arange(exceed, exceed + next_day_length*discretization_constant, discretization_constant)
            Tau_hours = list(Tau_hours)
            Tau = list(np.arange(0, len(Tau_hours), 1))

            if len(Tau) != len(Tau_hours):
                print('Length Tau', len(Tau))
                print('Lenght Tau_hours', len(Tau_hours))
                raise ValueError ('Indexes do not match!')
            return ext_days, Tau_hours, Tau


    def Time_set_wider(discretization_constant, earl_arv, late_arv, vendors_df, local, init_simulation_date, end_simulation_date): 
        """Creates Time Index and Hour range vector of the given period. The interval [d1, d2] is the time space range that will be considered for arriving to the vendors.
        Input: 
        discretization_constant: Discretization constant.
        earl_arv: Earliest arrival time to the vendor.
        late_arv: Latest arrival time to the vendor.
        vendors_df: vendors DataFrame.
        Output: 
        d1: min_date for arriving during the period excluding the recipient.
        d2: max_date for arriving during the period excluding the recipient.
        ext_days: Complete range of arrivals including the recipient [d1, d2 + 1].
        Tau_hours: Vector of arrival hours.
        Tau: Index of the Tau_hours vector.
        """
        min_date = min(vendors_df['Requested Loading']) 
        max_date = max(vendors_df['Requested Loading']) 
        maximum_recipient = vendors_df['Requested Delivery']

        min_dt = datetime.datetime.strptime(min_date, '%Y-%m-%d %H:%M:%S')
        max_dt = datetime.datetime.strptime(max_date, '%Y-%m-%d %H:%M:%S')
        # should be 
        d0 = init_simulation_date
        d1 = min_dt - datetime.timedelta(days=earl_arv)
        d2 = max_dt + datetime.timedelta(days=late_arv + 1) # Max arrival time to the depot (48 hours after maximum arrival time on vendor)
        dn = end_simulation_date

        # Local Time Array
        if local == True:
            minimum_dt = d1
            ext_days, Tau_hours, Tau = Graph.time_definition(discretization_constant, d2, d1)        
        else:
        # Extend Time Array
            minimum_dt = d0
            ext_days, Tau_hours, Tau = Graph.time_definition(discretization_constant, dn, d0)

        return maximum_recipient, minimum_dt, ext_days, Tau_hours, Tau

    def create_time_network(self, vendors_df, init_simulation_date, end_simulation_date):
        """Creates Time-Expanded Graph
        Input: 
        vendors_df: vendors DataFrame.
        Output: 
        T_ex: Time-Expanded Graph.
        time_network_index: Index of the Tau_hours vector (expanded).
        """
        # 1) Establish the index boundaries of the Time-Network.
        maximum_recipient, self.min_date, self.tour_days, self.Tau_hours, Tau_index = Graph.Time_set_wider(self.discretization_constant, self.earl_arv, self.late_arv, vendors_df, True, init_simulation_date, end_simulation_date)
        self.min_day = self.min_date.day
        
        non_open_depot_index=np.where((np.array(self.Tau_hours)<self.starting_depot) | (np.array(self.Tau_hours)>self.closing_depot))
        non_open_supp_index=np.where((np.array(self.Tau_hours)<self.vendor_start_hr) | (np.array(self.Tau_hours)>self.pickup_end_hr))

        non_open_depot_index = list(non_open_depot_index[0])
        non_open_supp_index = list(non_open_supp_index[0])

        days_req = pd.to_datetime(vendors_df['Requested Loading'], format= '%Y-%m-%d %H:%M:%S').dt.day
        if self.late_arv <= 6:
            e = {}
            l = {}
            for i in range(1,len(days_req)+1):
                current_time = vendors_df['Requested Loading'][i]

                current_time = datetime.datetime.strptime(current_time, '%Y-%m-%d %H:%M:%S')
                upper_time_window = current_time + datetime.timedelta(hours=self.earl_arv)
                lower_time_window = current_time - datetime.timedelta(hours=self.late_arv)

                if upper_time_window.hour >= self.pickup_end_hr:
                    diff_hours = upper_time_window.hour - self.pickup_end_hr
                    if monthrange(current_time.year, current_time.month)[1] >= current_time.day + 1:
                        upper_time_window = datetime.datetime(current_time.year, current_time.month, current_time.day + 1, self.vendor_start_hr + diff_hours, 0, 0)
                    else:
                        upper_time_window = datetime.datetime(current_time.year, current_time.month + 1, current_time.day + 1 - monthrange(current_time.year, current_time.month)[1], self.vendor_start_hr + diff_hours, 0, 0)

                if lower_time_window.hour < self.vendor_start_hr:
                    diff_hours = self.vendor_start_hr - lower_time_window.hour
                    prev_day = current_time - datetime.timedelta(days=1)
                    lower_time_window = datetime.datetime(prev_day.year, prev_day.month, prev_day.day, self.pickup_end_hr - diff_hours, 0, 0)

                e[i] = [lower_time_window.day, int(lower_time_window.strftime("%H") )]
                l[i] = [upper_time_window.day, int(upper_time_window.strftime("%H") )]
        elif self.late_arv == 24:
            # 2) Defines the time Windows for each vendor.
            e = {}
            l = {}
            print(vendors_df)
            for i in days_req.index:
                current_time = vendors_df['Requested Loading'][i]
                current_time = datetime.datetime.strptime(current_time, '%Y-%m-%d %H:%M:%S')
            
                lower_datetime = current_time - datetime.timedelta(hours=self.earl_arv)
                upper_datetime = current_time + datetime.timedelta(hours=self.late_arv)
                
                # Pass full datetime instead of just day number to avoid ambiguity in multi-month tours
                e[i] = [lower_datetime, int(current_time.strftime("%H"))]  # one day before allow
                l[i] = [upper_datetime, int(current_time.strftime("%H"))] # one day after allow
        else:
            raise ValueError('Arrival time not supported')

        e_inx = {}
        l_inx = {}
        e_inx[0] = 0
        l_inx[0] = max(Tau_index)
        for i in e.keys():
            e_inx[i] = date_index(self.discretization_constant, e[i][0], e[i][1], self.min_date, self.tour_days, self.Tau_hours, 'early_values', maximum_recipient[i])
            l_inx[i] = date_index(self.discretization_constant, l[i][0], l[i][1], self.min_date, self.tour_days, self.Tau_hours, 'latest_values', maximum_recipient[i])
            
        time_windows={}
        for i in self.Nodes:
            time_windows[i] = list(np.arange( e_inx[i], l_inx[i]+1, 1))
        
        # 3) Creates the Expanded-Time Graph with pruning optimizations.
        log.info('Creating Time-Expanded Graph with arc pruning...')
        T_ex = []
        maximum_values=[]
        
        # OPTIMIZATION 1: Pre-compute sets for faster lookup
        non_open_supp_set = set(non_open_supp_index)
        non_open_depot_set = set(non_open_depot_index)
        max_tau = max(Tau_index)
        
        # OPTIMIZATION 2: Use configurable distance threshold
        arcs_pruned = 0
        total_potential_arcs = 0
        
        for i in range(self.length):
            for j in range(self.length):        
                if i != j:
                    total_potential_arcs += 1
                    # OPTIMIZATION 3: Skip arcs with excessive travel distance
                    if self.distance_matrix[i][j] > self.max_feasible_distance:
                        arcs_pruned += 1
                        continue
                    
                    if i == 0:
                        # Depot to vendor arcs
                        for u in time_windows[j]:
                            if (u not in non_open_supp_set) & (u <= max_tau):                        
                                T_ex.append([ [i, u ], [j, u ] ])
                                
                    elif j == 0:
                        # Vendor to depot arcs
                        for m in time_windows[i]:                                     
                            t_e = m + self.disc_loading + self.disc_time_distance_matrix[i][j]

                            if (t_e not in non_open_depot_set) & (t_e <= max_tau):                                    
                                if m not in non_open_supp_set:
                                    T_ex.append([ [i, m ], [j, t_e] ] )              
                            elif t_e >= max_tau:
                                if m not in non_open_supp_set:
                                    T_ex.append( [ [i, m], [j, max_tau] ] )
                                    maximum_values.append(t_e)                        
                    else:
                        # Vendor to vendor arcs - most pruning happens here
                        # OPTIMIZATION 4: Check driving time constraint first (cheapest check)
                        if self.disc_time_distance_matrix[i][j] > self.disc_max_driving:
                            arcs_pruned += 1
                            continue
                        
                        # OPTIMIZATION 5: Limit time window iterations - sample every N periods for large windows
                        tw_i = time_windows[i]
                        if len(tw_i) > self.time_window_sampling_threshold:
                            step = max(1, len(tw_i) // self.time_window_sample_size)
                            tw_i = tw_i[::step]
                        
                        for m in tw_i:
                            if m in non_open_supp_set:
                                continue
                                
                            if self.disc_time_distance_matrix[i][j] == 0:                                
                                t_e = m + (self.disc_loading - math.floor( self.disc_loading/2 )) + self.disc_time_distance_matrix[i][j]        
                            else:
                                t_e = m + self.disc_loading + self.disc_time_distance_matrix[i][j]
                            
                            # OPTIMIZATION 6: Early exit if time constraints violated
                            if t_e > max_tau:
                                continue
                                
                            if (t_e not in non_open_supp_set) and (t_e <= max(time_windows[j])):
                                T_ex.append( [ [i, m ], [j,  int(max( time_windows[i][0], t_e) )] ])
        
        # Report pruning statistics
        pruning_rate = (arcs_pruned / total_potential_arcs * 100) if total_potential_arcs > 0 else 0
        print(f'Arc pruning statistics:')
        print(f'  - Potential node pairs: {total_potential_arcs}')
        print(f'  - Arcs pruned by distance/time: {arcs_pruned} ({pruning_rate:.1f}%)')
        print(f'  - Arcs generated: {len(T_ex)}')
        
        # Sanity Checks:
        for i in range(len(T_ex)):
            if T_ex[i][1][1] - T_ex[i][0][1] < 0:
                print(i)
                print('Your going backwards')
                if T_ex[i][0][0] != 0:
                    print('Teletransport!')
                    print(i)

        # 4) Add time points above the +1 day given in [d1, d2 + 1] if one day is not enough (given the travel time from vendor i to the recipient).
        time_index_values = []
        for i in range(len(T_ex)):
            time_index_values.append(T_ex[i][1][1] )
        maximum_values=max(time_index_values)
        minimum_values=min(time_index_values)
        # range()
        time_network_index = np.arange(0, maximum_values + 1, 1)
        
        date_time_overall, date_time_network_min = inv_date_index(self.discretization_constant, minimum_values, self.min_date, self.Tau_hours)
        date_time_overall, date_time_network_max = inv_date_index(self.discretization_constant, maximum_values, self.min_date, self.Tau_hours)
        
        print('length time-expanded:                                 ', len(T_ex))
        print('Minimum arrival time in the time-expanded matrix is on', date_time_network_min )
        print('Maximum arrival time in the time-expanded matrix is on', date_time_network_max )

        # Convert NumPy types to native Python types for cleaner output
        T_ex_clean = [[[int(arc[0][0]), int(arc[0][1])], [int(arc[1][0]), int(arc[1][1])]] for arc in T_ex]
        Tau_index_clean = [int(x) for x in Tau_index]
        time_network_index_clean = [int(x) for x in time_network_index]

        return T_ex_clean, Tau_index_clean, time_network_index_clean

    def shift_time(self, index_to_shift, vendors_df, init_simulation_date, end_simulation_date):
        # shift index to the overall time index matrix
        maximum_recipient, min_date_overall, tour_days, Tau_hours_overall, Tau_index_overall = Graph.Time_set_wider(self.discretization_constant, self.earl_arv, self.late_arv, vendors_df, False, init_simulation_date, end_simulation_date)
    
        current_time = self.min_date
        arrv_day = current_time.day
        arrv_time = int(current_time.strftime("%H") )
        return index_to_shift + date_index(self.discretization_constant, arrv_day, arrv_time, min_date_overall, tour_days, Tau_hours_overall, 'early_values')

    def update_time_expanded_network(self, time_expanded_network_local, vendors_df, init_simulation_date, end_simulation_date):
        time_expanded_network_general = copy.deepcopy(time_expanded_network_local)
        shift_value = Graph.shift_time(self, 0, vendors_df, init_simulation_date, end_simulation_date) 
        for i in range(len(time_expanded_network_local)):
            time_expanded_network_general[i][0][1] = time_expanded_network_local[i][0][1] + shift_value
            time_expanded_network_general[i][1][1] = time_expanded_network_local[i][1][1] + shift_value

        return time_expanded_network_general