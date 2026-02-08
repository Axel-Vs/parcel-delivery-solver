import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

import datetime
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
        # self.capacity_matrix = None
        self.solution = None

    # Removed legacy network export/update helpers (unused in ALNS flow).

    def read_data(self,  period, initial_dataset):
        """Read pre-processing data, filter the period given and extracts coordinates and vendor dataset.
        Input: Period and dataset path.
        Output: List with coordinates and vendors information dataframe
        """
        # initial_dataset = pd.read_csv(path)
        #---!
        # initial_dataset = initial_dataset[3:6]
        #---!


        # recipient_extract (for multi-depot support)
        def _first_col(df, options):
            for col in options:
                if col in df.columns:
                    return col
            return None

        recipient_lon_col = _first_col(initial_dataset, ['recipient_longitude', 'Recipient Longitude'])
        recipient_lat_col = _first_col(initial_dataset, ['recipient_latitude', 'Recipient Latitude'])
        recipient_city_col = _first_col(initial_dataset, ['Recipient City'])
        recipient_country_col = _first_col(initial_dataset, ['Recipient Country Name'])
        recipient_postcode_col = _first_col(initial_dataset, ['Recipient Postcode'])

        recipient_values = initial_dataset[[recipient_lon_col, recipient_lat_col]].apply(list, axis=1)

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
            'Total Gross Weight',
            'Vendor Volume in m3',
            'Vendor Linear Length',
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
        vendors_df = filter_geocoded[base_cols].copy()
        vendors_df.index = vendors_df.index + 1
        vendors_df = vendors_df
        if len(vendors_df) !=0:
            print('Number of vendors extracted:', len(vendors_df))   # complete dataset

        # Build unique depots from recipient coordinates + location info
        depots_df = pd.DataFrame(columns=[
            'depot_id',
            'node_id',
            'recipient_longitude',
            'recipient_latitude',
            'Recipient City',
            'Recipient Country Name',
            'Recipient Postcode'
        ])
        if recipient_lon_col and recipient_lat_col:
            depots_raw = filter_geocoded[[recipient_lon_col, recipient_lat_col]].copy()
            depots_raw['Recipient City'] = (
                filter_geocoded[recipient_city_col].astype(str).str.strip()
                if recipient_city_col in filter_geocoded.columns else ''
            )
            depots_raw['Recipient Country Name'] = (
                filter_geocoded[recipient_country_col].astype(str).str.strip()
                if recipient_country_col in filter_geocoded.columns else ''
            )
            depots_raw['Recipient Postcode'] = (
                filter_geocoded[recipient_postcode_col].astype(str).str.strip()
                if recipient_postcode_col in filter_geocoded.columns else ''
            )
            depots_raw['recipient_longitude'] = depots_raw[recipient_lon_col].astype(float)
            depots_raw['recipient_latitude'] = depots_raw[recipient_lat_col].astype(float)
            depots_raw['depot_key'] = depots_raw.apply(
                lambda r: (
                    round(float(r['recipient_longitude']), 6),
                    round(float(r['recipient_latitude']), 6),
                    str(r['Recipient City']).strip(),
                    str(r['Recipient Country Name']).strip(),
                    str(r['Recipient Postcode']).strip()
                ),
                axis=1
            )
            depots_df = depots_raw.drop_duplicates('depot_key').reset_index(drop=True)
            depots_df['depot_id'] = range(1, len(depots_df) + 1)
            depots_df['node_id'] = depots_df['depot_id']

            depot_map = {
                row['depot_key']: int(row['depot_id'])
                for _, row in depots_df.iterrows()
            }

            depots_df = depots_df[[
                'depot_id',
                'node_id',
                'recipient_longitude',
                'recipient_latitude',
                'Recipient City',
                'Recipient Country Name',
                'Recipient Postcode'
            ]]
            vendor_keys = depots_raw['depot_key'].tolist()
            vendor_depot_ids = [depot_map.get(key) for key in vendor_keys]
            vendors_df.loc[:, 'depot_id'] = vendor_depot_ids
            vendors_df.loc[:, 'depot_node_id'] = vendors_df['depot_id']

        # Assign node_id for vendors after dummy + depot nodes
        # Reset index to keep node IDs within matrix bounds
        vendors_df = vendors_df.reset_index(drop=True)
        depot_count = len(depots_df)
        vendors_df.loc[:, 'node_id'] = vendors_df.index + depot_count + 1

        # Consolidation: [dummy_start] + depots + vendors
        depots_coords = []
        if len(depots_df) > 0:
            depots_coords = depots_df[['recipient_longitude', 'recipient_latitude']].apply(list, axis=1).tolist()
            dummy_coord = depots_coords[0]
        elif len(vendor_coordinates) > 0:
            dummy_coord = vendor_coordinates.iloc[0]
        else:
            dummy_coord = [0.0, 0.0]
        complete_coordinates = [dummy_coord] + depots_coords + list(vendor_coordinates)

        return complete_coordinates, vendors_df, depots_df
        

    def create_network(self, complete_coordinates, vendors_df):
        """Creates Fix connection network.
        Input: Nodes coordinates, source index, routing client, setting to zero parameter.
        Output: Distance and Time Travel Matrix
        """
        self.distance_matrix, self.time_distance_matrix = info_matrix_definition(
            complete_coordinates, 0, 'horizontal', self.client, self.routing_provider
        )
        self.length = len(self.distance_matrix)
        print('Distance & Time Distance Matrix created')

    # Removed time-expanded network functions (unused in ALNS flow).
