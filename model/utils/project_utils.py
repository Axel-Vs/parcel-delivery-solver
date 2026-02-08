from pathlib import Path
import sys
# Get the parent directory of the current script (project root)
project_root = Path(__file__).resolve().parent.parent
# Add the paths of the relevant directories to sys.path
sys.path.append(str(project_root))

import datetime
import pandas as pd
import numpy as np
import logging as log
try:
    import coloredlogs
except Exception:
    coloredlogs = None
import math
################################################################################################################################################
# Logout configuration ----------------------------------------------------------------------------------------------------
################################################################################################################################################
# Create a logger object.
logger = log.getLogger(__name__)
# Create a filehandler object for debugging logs
fh = log.FileHandler('spam.log')
fh.setLevel(log.DEBUG)
# Use coloredlogs if available, otherwise fall back to standard logging.Formatter
if coloredlogs is not None:
    formatter = coloredlogs.ColoredFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    try:
        fh.setFormatter(formatter)
    except Exception:
        fh.setFormatter(log.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(fh)
    try:
        coloredlogs.install(level='DEBUG')
    except Exception:
        pass
else:
    fh.setFormatter(log.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(fh)


################################################################################################################################################
# Pre-processing helpers removed (unused in app flow).
################################################################################################################################################


################################################################################################################################################
# Optimizer ------------------------------------
################################################################################################################################################
def SolVal(x):
    if type(x) is not list:
        return 0 if x is None else x.SolutionValue()
    elif type(x) is list:
        return [SolVal(e) for e in x ]


def haversine_distance(lon1, lat1, lon2, lat2):
    """Calculate the great circle distance between two points on Earth in kilometers."""
    # Convert decimal degrees to radians
    lon1, lat1, lon2, lat2 = map(math.radians, [lon1, lat1, lon2, lat2])
    
    # Haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    
    # Radius of earth in kilometers
    r = 6371
    return c * r

def calculate_osrm_matrix(coordinates):
    """Calculate distance/time matrix using free OSRM service (no API key needed).
    
    Returns:
        distance_matrix: distances in km
        time_matrix: durations in SECONDS (not hours - will be converted later)
    """
    import requests
    
    # OSRM expects lon,lat format
    coords_str = ';'.join([f"{coord[0]},{coord[1]}" for coord in coordinates])
    
    # Use OSRM public demo server - completely free!
    url = f"http://router.project-osrm.org/table/v1/driving/{coords_str}"
    params = {
        'annotations': 'distance,duration'
    }
    
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    
    data = response.json()
    
    if data['code'] != 'Ok':
        raise Exception(f"OSRM error: {data.get('message', 'Unknown error')}")
    
    # Convert meters to km, but keep durations in SECONDS (converted later to hours)
    distance_matrix = [[dist / 1000.0 for dist in row] for row in data['distances']]
    time_matrix = [[dur for dur in row] for row in data['durations']]  # Keep in seconds!
    
    return distance_matrix, time_matrix

def info_matrix_definition(coordinates, source_node, shape, client, provider='ors'):
    """Calculate distance and time matrices using OSRM (free), Google Maps, ORS, or Haversine fallback.
    
    Args:
        coordinates: List of [longitude, latitude] pairs
        source_node: Index of source node (depot)
        shape: 'horizontal' or 'vertical' for zeroing depot row/column
        client: Routing API client (Google Maps or ORS)
        provider: 'google', 'ors', or 'osrm'
    """
    # Try OSRM first (completely free, no API key needed)
    try:
        print('🌐 Calculating distances using OSRM (free, real road distances)...')
        log.info('Calculating distances using OSRM (free, real road distances)...')
        distance_mtrx, time_mtrx = calculate_osrm_matrix(coordinates)
        print('✅ OSRM calculation completed successfully')
        log.info(f'OSRM calculation completed successfully')
    except Exception as e:
        log.info(f'OSRM failed ({str(e)}), trying alternative providers...')
        
        # Fallback to Google Maps or ORS
        try:
            if provider == 'google':
                # Use Google Maps Distance Matrix API
                log.info('Calculating distances using Google Maps API...')
                n = len(coordinates)
                distance_mtrx = [[0.0 for _ in range(n)] for _ in range(n)]
                time_mtrx = [[0.0 for _ in range(n)] for _ in range(n)]
                
                # Convert coordinates to lat,lng format for Google Maps
                origins = [f"{coord[1]},{coord[0]}" for coord in coordinates]  # lat,lng format
                
                # Google Maps API has limits, so batch requests if needed
                # Maximum 25 origins × 25 destinations per request
                batch_size = 25
                
                for i in range(0, n, batch_size):
                    for j in range(0, n, batch_size):
                        batch_origins = origins[i:min(i+batch_size, n)]
                        batch_destinations = origins[j:min(j+batch_size, n)]
                        
                        result = client.distance_matrix(
                            origins=batch_origins,
                            destinations=batch_destinations,
                            mode='driving',
                            units='metric'
                        )
                        
                        # Parse results
                        for row_idx, row in enumerate(result['rows']):
                            for col_idx, element in enumerate(row['elements']):
                                global_row = i + row_idx
                                global_col = j + col_idx
                                
                                if element['status'] == 'OK':
                                    # Distance in meters, convert to km
                                    distance_mtrx[global_row][global_col] = element['distance']['value'] / 1000.0
                                    # Duration in seconds (keep consistent with OSRM)
                                    time_mtrx[global_row][global_col] = element['duration']['value']
                
                log.info('Google Maps distance calculation completed')
                
            else:
                # Try using ORS client for real routing distances
                info_mtrx = client.distance_matrix(
                                                        locations=coordinates,
                                                        profile='driving-hgv',
                                                        metrics= ['distance', 'duration'],
                                                        units = 'km',
                                                        resolve_locations=True,
                                                        validate=False
                                                        )
                distance_mtrx = info_mtrx['distances']
                time_mtrx = info_mtrx['durations']
            
                # Check if we got real data or dummy zeros
                total_distance = sum(sum(row) for row in distance_mtrx)
                if total_distance == 0 and len(coordinates) > 1:
                    # Dummy client returned zeros, fall back to Haversine
                    raise ValueError("ORS returned zero distances, using Haversine fallback")
        except Exception as e:
            # Fall back to Haversine distance calculation
            log.info(f'Using Haversine distance calculation (straight-line distances): {str(e)}')
            n = len(coordinates)
            distance_mtrx = [[0.0 for _ in range(n)] for _ in range(n)]
            
            for i in range(n):
                for j in range(n):
                    if i != j:
                        # coordinates format: [longitude, latitude]
                        dist = haversine_distance(
                            coordinates[i][0], coordinates[i][1],
                            coordinates[j][0], coordinates[j][1]
                        )
                        distance_mtrx[i][j] = dist
            
            # Estimate time based on distance (assume average speed of 60 km/h for trucks)
            time_mtrx = [[dist / 60.0 for dist in row] for row in distance_mtrx]
    
    if shape == 'horizontal':
        # Setting first row to zero
        zero_row = np.zeros(len(distance_mtrx))               
        
        distance_mtrx[source_node] = list(zero_row)
        time_mtrx[source_node] = list(zero_row)
        
        distance_mtrx = np.asarray(distance_mtrx)
        time_mtrx = np.asarray(time_mtrx)
    elif shape == 'vertical':
        for i in range(len(distance_mtrx)):
            distance_mtrx[i][source_node] = 0
            time_mtrx[i][source_node] = 0
        distance_mtrx = np.asarray(distance_mtrx)
        time_mtrx = np.asarray(time_mtrx)
    else:
        distance_mtrx = np.asarray(distance_mtrx)
        time_mtrx = np.asarray(time_mtrx)
    return distance_mtrx, time_mtrx


def filter_dates(df, period):
    df_f = df.copy(deep=True)
    
    # init_p = datetime.datetime.strptime(period[0], '%Y-%m-%d %H:%M:%S')
    init_p = datetime.datetime.strptime(period[0], '%Y-%m-%d %H:%M:%S')
    endg_p = datetime.datetime.strptime(period[1], '%Y-%m-%d %H:%M:%S')
    
    # Be permissive when parsing dates: accept day-first formats and coerce errors
    df_f['temp_date'] = pd.to_datetime(df_f['Requested Loading'], errors='coerce')
    df_f = df_f[(df_f['temp_date'] >= init_p) & (df_f['temp_date'] <= endg_p)]


    # if len(df_f) == 0:       
    #     raise ValueError('---------------------------- No service needed on this period. Please try later.')
            
    # Normalize 'Requested Loading' to '%Y-%m-%d %H:%M:%S' format expected by time network
    df_f['Requested Loading'] = df_f['temp_date'].dt.strftime('%Y-%m-%d %H:%M:%S')
    df_f.drop('temp_date', axis=1, inplace=True)
    df_f.sort_values(by=['Requested Loading'],inplace=True)   
    df_f.reset_index(drop=True, inplace=True)
    df_time = pd.to_datetime(df_f['Requested Loading'], format='%Y-%m-%d %H:%M:%S', errors='coerce')
    
    # Week days -----------------------------------------------------------------
    nur_dates = df_time.dt.date.drop_duplicates()
    nur_dates.reset_index(drop=True, inplace=True)
    week_days = [date_obj.strftime('%A') for date_obj in nur_dates]
    week_days = pd.DataFrame(week_days, columns = ['Week Days'])
    # display(pd.concat([nur_dates, week_days], axis=1))
    # Times ---------------------------------------------------------------------
    # times_values = [dates_values.strftime("%H") for dates_values in df_time ]
    return df_f # complete period window



################################################################################################################################################
# Time Matrix ----------------------------------------------------------------------------
################################################################################################################################################

## ----------------------------------------------------------------------------------------------------------------------------------------
    # Removed time-expanded network helpers (unused).