from pathlib import Path
import sys
import pandas as pd
import os
import json
import datetime

# Ensure project root is first on sys.path before importing model modules
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from model.graph_creator.graph_creator import Graph
from model.optimizer.delivery_model import DeliveryOptimizer
from model.utils.project_utils import *


def import_parameters(parameters_path):
    network_params_file_name = 'network_params.txt'
    model_params_file_name = 'model_params.txt'
    simulation_params_file_name = 'simulation_params.txt'

    network_params_path = os.path.join(parameters_path, network_params_file_name)
    model_params_path = os.path.join(parameters_path, model_params_file_name)
    simulation_params_path = os.path.join(parameters_path, simulation_params_file_name)

    network_params = json.load(open(network_params_path))
    model_params = json.load(open(model_params_path))
    simulation_params = json.load(open(simulation_params_path))

    return network_params, model_params, simulation_params


def periods_generator(simulation_period, simulation_interval, vendor_start_hr, pickup_end_hr):
    start = simulation_period[0]
    end = simulation_period[1]
    delta = datetime.timedelta(days=simulation_interval)

    start = datetime.datetime.strptime(start, '%Y-%m-%d')
    end = datetime.datetime.strptime(end, '%Y-%m-%d')
    t = start

    periods = []
    while t <= end:
        if t + delta < end:
            start_dt = t.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(hours=vendor_start_hr)
            end_dt = (t + delta).replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(hours=pickup_end_hr)
            periods.append([start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                            end_dt.strftime("%Y-%m-%d %H:%M:%S")])
            t = t + (delta + datetime.timedelta(days=1))
        else:
            start_dt = t.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(hours=vendor_start_hr)
            end_dt = end.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(hours=pickup_end_hr)
            periods.append([start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                            end_dt.strftime("%Y-%m-%d %H:%M:%S")])
            t = t + (delta + datetime.timedelta(days=1))
    return periods

# Get the current working directory
main_root = os.getcwd()

# Define paths for configuration files, data, and results
parameters_path = os.path.join(main_root, 'model/config')
data_path = os.environ.get(
    'SIM_DATA_PATH',
    os.path.join(main_root, 'data/amazon_test_dataset_small.csv')
)
results_path = os.path.join(main_root, 'results/optimization')

print('\nStarting Simulation\n')

# Import parameters for the simulation from configuration files
network_params, model_params, simulation_params = import_parameters(parameters_path)
if os.environ.get('SIM_ALNS_ITERS'):
    try:
        model_params['alns_max_iterations'] = int(os.environ['SIM_ALNS_ITERS'])
    except ValueError:
        pass

# Read dataset (attempt to normalize to the fields expected by the Graph code)
df_raw = pd.read_csv(data_path)

# Prepare a geocoded-like dataframe used by the rest of the pipeline. We do NOT
# automatically fill missing coordinates with the configured plot center. Only
# coordinates returned by geocoding providers (ORS or Nominatim) will be set.

# Create columns the code expects (both capitalized and lowercase variants appear in code)
df = df_raw.copy()
# Map vendor name
if 'Vendor Name' in df.columns:
    df['vendor Name'] = df['Vendor Name']
    df['vendor Name'] = df['vendor Name'].astype(str)

# Map gross weight / volume / linear length
if 'Vendor Gross Weight' in df.columns:
    df['Total Gross Weight'] = df['Vendor Gross Weight']

# Normalize requested loading/delivery names and formats
if 'Requested Loading Date' in df.columns:
    # Parse several possible datetime formats then reformat to '%Y-%m-%d %H:%M:%S' expected by graph creator
    df['Requested Loading'] = pd.to_datetime(df['Requested Loading Date'], errors='coerce')
    df['Requested Loading'] = df['Requested Loading'].dt.strftime('%Y-%m-%d %H:%M:%S')
else:
    # Keep or create an empty column to avoid key errors later
    df['Requested Loading'] = ''

if 'Requested Delivery Date' in df.columns:
    df['Requested Delivery'] = pd.to_datetime(df['Requested Delivery Date'], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')
else:
    df['Requested Delivery'] = df['Requested Loading']

# Geocode vendor and recipient addresses (use cached Geocoder if available).
# Normalize postcode-like columns to avoid float artifacts (e.g. '98101.0') and
# attempt to pre-fill a persistent cache. When an API-keyed provider is available
# (ORS) it will be preferred; otherwise the code falls back to Nominatim.
if simulation_params.get('perform_geocoding', False):
    print('\nGeocoding addresses (this may take a while on first run)...\n')
    try:
        from model.utils.geocoder import Geocoder
        geocode_cache = os.path.join(main_root, 'data', 'geocode_cache.csv')
        g = Geocoder(cache_path=geocode_cache, user_agent='parcel_geocoder', min_delay_seconds=1)
        
        print(f'  📍 Cache loaded: {len(g.cache)} addresses')
        print(f'  📦 Dataset records: {len(df)}')
        print(f'  🌐 Geocoding backend: {"ORS" if g.ors_key else "Nominatim"} (available: {g._available})')

        # Defensive normalization for common postcode columns to improve geocoding
        print('\n  ⚙️  Normalizing postcodes...')
        for col in ['Vendor Postcode', 'Recipient Postcode', 'vendor Postcode', 'recipient Postcode']:
            if col in df.columns:
                # Remove trailing ".0" from float-like postcodes while keeping other formats intact.
                df[col] = (
                    df[col]
                    .fillna('')
                    .astype(str)
                    .str.strip()
                    .str.replace(r'\.0$', '', regex=True)
                )
        print('  ✓ Postcodes normalized')

        # First pass: populate coordinates where we have cached answers or quick-lookups.
        # Only provider-found coordinates will be populated; missing values remain None/NaN.
        print('\n  🔍 First pass: checking cache for addresses...')
        df = g.geocode_dataframe(df, force_refresh=False)
        
        # Check results
        vendor_found = df['vendor_latitude'].notna().sum()
        recip_found = df['recipient_latitude'].notna().sum()
        print(f'  ✓ Vendor coordinates found: {vendor_found}/{len(df)} ({vendor_found/len(df)*100:.1f}%)')
        print(f'  ✓ Recipient coordinates found: {recip_found}/{len(df)} ({recip_found/len(df)*100:.1f}%)')

        # Retry previously failed entries by refreshing the negative cache. This will
        # attempt to re-query upstream providers for addresses that previously returned
        # (None, None). It is safe to run once for small datasets; for larger runs you
        # may prefer to call this with rate-limiting or off-line.
        if vendor_found < len(df) or recip_found < len(df):
            print('\n  🔄 Attempting to refresh missing addresses from geocoding service...')
            try:
                g.refresh_negative_cache()
                # Run geocode pass again to pick up any newly-cached results
                df = g.geocode_dataframe(df, force_refresh=False)
                
                vendor_found_2 = df['vendor_latitude'].notna().sum()
                recip_found_2 = df['recipient_latitude'].notna().sum()
                
                if vendor_found_2 > vendor_found or recip_found_2 > recip_found:
                    print(f'  ✓ Additional addresses found!')
                    print(f'    - Vendors: {vendor_found_2}/{len(df)}')
                    print(f'    - Recipients: {recip_found_2}/{len(df)}')
                else:
                    print(f'  ⚠️  No additional addresses found')
            except Exception as refresh_err:
                # If refresh fails (e.g., network issues), we still have the first-pass results
                print(f'  ⚠️  Refresh failed: {refresh_err}')
        
        print('\n  ✅ Geocoding completed!\n')
    except Exception as e:
        # Keep placeholder columns if geocoding isn't possible
        print('❌ Geocoder unavailable or failed:', e)
else:
    print('\nSkipping geocoding - using existing coordinates from dataset...\n')
    # Ensure coordinate columns exist from the raw dataset
    # If the dataset already has these columns, they will be used as-is
    for coord_col in ['vendor_longitude', 'vendor_latitude', 'recipient_longitude', 'recipient_latitude']:
        if coord_col not in df.columns:
            # If coordinate columns don't exist, create them as None
            df[coord_col] = None
            print(f'Warning: {coord_col} column not found in dataset')

# Create periods using config; if these do not match dataset, fall back to min/max from data
periods = periods_generator(simulation_params["Simulation_periods"],
                            simulation_params["planning_horizon"],
                            network_params['vendor_start_hr'],
                            network_params['pickup_end_hr'])

# Determine period spanning from earliest loading date to latest delivery date
if df_raw.shape[0] > 0:
    try:
        min_dt = None
        max_dt = None
        
        # Get earliest loading date and latest delivery date from CSV
        if 'Requested Loading Date' in df_raw.columns:
            loading_dates = pd.to_datetime(df_raw['Requested Loading Date'], errors='coerce').dropna()
            if len(loading_dates) > 0:
                min_dt = loading_dates.min()
                max_dt = loading_dates.max()
        
        if 'Requested Delivery Date' in df_raw.columns:
            delivery_dates = pd.to_datetime(df_raw['Requested Delivery Date'], errors='coerce').dropna()
            if len(delivery_dates) > 0:
                if min_dt is None:
                    min_dt = delivery_dates.min()
                else:
                    min_dt = min(min_dt, delivery_dates.min())
                
                if max_dt is None:
                    max_dt = delivery_dates.max()
                else:
                    max_dt = max(max_dt, delivery_dates.max())
        
        if min_dt is not None and max_dt is not None and pd.notna(min_dt) and pd.notna(max_dt):
            periods = [[min_dt.strftime('%Y-%m-%d %H:%M:%S'), max_dt.strftime('%Y-%m-%d %H:%M:%S')]]
            print(f"📅 Tour period: {min_dt} to {max_dt}")
    except Exception as e:
        print(f"⚠️ Error calculating period: {e}")
        pass

# Iterate through different weight values (kept small for a quick smoke run)
#for w in [0, 0.5, 1]:
for w in [0.5]:
    print('------------------------------- weight:', w)

    # Use the preprocessed df (df) as the geocoded-like input
    df_geocoded = df.copy()

    # Iterate through simulation periods
    for period in periods:
        print('\n Time Frame Definition')
        print('Initial Simulation Date:', period[0])
        print('End Simulation Date:    ', period[1])

        # Create a Graph object
        net = Graph(network_params)

        # Read data and create Graph for the given period
        try:
            complete_coordinates, vendors_df, depots_df = net.read_data([period[0], period[1]], df_geocoded)
        except Exception as e:
            print('Error while reading data for period:', e)
            continue

        if len(vendors_df) >= 1:  # Process if there are vendors in this period
            print(f'Number of vendors extracted: {len(vendors_df)}')

            num_vendors = len(vendors_df)
            
            print('\n Create Graph')
            # Create the Graph; wrap network calls to avoid hard failure during smoke runs
            try:
                net.create_network(complete_coordinates, vendors_df)
                print('✓ Skipping network expansion (ALNS only)')
                net.min_date = pd.to_datetime(period[0])
            except Exception as e:
                print('Skipping network creation due to error (likely ORS/network):', e)
                continue

            depot_node_ids = depots_df['node_id'].tolist() if depots_df is not None and 'node_id' in depots_df.columns else []
            vendor_node_ids = vendors_df['node_id'].tolist() if 'node_id' in vendors_df.columns else list(range(1, len(vendors_df) + 1))
            vendor_depot_map = {}
            if 'depot_node_id' in vendors_df.columns:
                vendor_depot_map = {
                    int(v_node): int(d_node)
                    for v_node, d_node in zip(vendor_node_ids, vendors_df['depot_node_id'])
                    if pd.notna(d_node)
                }

            # Align max_driving with actual travel times (same approach as app.py)
            calculated_max_driving = None
            min_default_max_driving = 70.0
            if net.time_distance_matrix is not None and len(net.time_distance_matrix) > 1:
                vendor_to_depot_times = []
                for v_node in vendor_node_ids:
                    depot_node = vendor_depot_map.get(int(v_node))
                    if depot_node is None:
                        continue
                    if v_node < len(net.time_distance_matrix) and depot_node < len(net.time_distance_matrix):
                        vendor_to_depot_times.append(net.time_distance_matrix[v_node][depot_node])
                if vendor_to_depot_times:
                    max_vendor_depot_time_hours = max(vendor_to_depot_times) / 3600.0
                    service_time_hours = float(network_params.get('loading', 2))
                    vendor_count = len(vendor_node_ids)
                    vendor_to_vendor_times = []
                    matrix_len = len(net.time_distance_matrix)
                    for i in vendor_node_ids:
                        if i >= matrix_len:
                            continue
                        for j in vendor_node_ids:
                            if i != j and j < matrix_len:
                                vendor_to_vendor_times.append(net.time_distance_matrix[i][j])
                    max_vendor_to_vendor_hours = (max(vendor_to_vendor_times) / 3600.0) if vendor_to_vendor_times else 0.0
                    target_routes = max(2, int(np.ceil(vendor_count / 3))) if vendor_count > 1 else 1
                    stops_per_route = max(1, int(np.ceil(vendor_count / target_routes)))
                    estimated_multi_stop = (
                        max_vendor_depot_time_hours +
                        max_vendor_to_vendor_hours * max(0, stops_per_route - 1) +
                        service_time_hours * stops_per_route +
                        2.0
                    )
                    calculated_max_driving = max(
                        max_vendor_depot_time_hours + service_time_hours + 2.0,
                        estimated_multi_stop,
                        min_default_max_driving
                    )
                    if float(network_params.get('max_driving', 0)) < calculated_max_driving:
                        network_params['max_driving'] = calculated_max_driving
                        print(f"✅ Max driving time set: {calculated_max_driving:.1f}h")

            # Create cargo and loading matrices aligned to node_id
            max_node_id = max([0] + depot_node_ids + vendor_node_ids)
            matrix_size = max_node_id + 1
            capacity_matrix = np.zeros(matrix_size, dtype=float)
            loading_matrix = np.zeros(matrix_size, dtype=float)
            linear_length_matrix = np.zeros(matrix_size, dtype=float)
            for _, row in vendors_df.iterrows():
                v_node = int(row.get('node_id', 0))
                if v_node <= 0 or v_node >= matrix_size:
                    continue
                capacity_matrix[v_node] = float(row.get('Total Gross Weight', 0) or 0)
                loading_matrix[v_node] = float(row.get('Vendor Volume in m3', row.get('Vendor Dimensions in m3', 0)) or 0)
                linear_length_matrix[v_node] = float(row.get('Vendor Linear Length', 0) or 0)
            service_time_minutes = float(network_params.get('loading', 2)) * 60.0
            service_time_matrix = np.zeros(matrix_size)
            for v_node in vendor_node_ids:
                if v_node < matrix_size:
                    service_time_matrix[v_node] = service_time_minutes
            
            # Print solver information (use_metaheuristic already determined above)
            print(f'\n 🚀 Solver: ALNS Metaheuristic (fast mode for {num_vendors} vendors)')
            print(f'   - Max iterations: {model_params.get("alns_max_iterations", 1000)}')
            print(f'   - Problem size: {num_vendors} vendors')
            
            try:
                # Create optimizer instance
                optimizer = DeliveryOptimizer(
                    evaluation_period=period,
                    discretization_constant=net.discretization_constant,
                    distance_matrix=net.distance_matrix,
                    time_distance_matrix=net.time_distance_matrix,
                    capacity_matrix=capacity_matrix,
                    loading_matrix=loading_matrix,
                    linear_length_matrix=linear_length_matrix,
                    service_time_matrix=service_time_matrix,
                    max_capacity=network_params['max_weight'],
                    max_volume=network_params.get('max_volume', 90),
                    max_linear_length=network_params.get('max_linear_length', 16.1),
                    max_driving=network_params['max_driving'],
                    vendors_df=vendors_df,
                    depots_df=depots_df,
                    depot_node_ids=depot_node_ids,
                    vendor_node_ids=vendor_node_ids,
                    vendor_depot_map=vendor_depot_map,
                    vendor_start_hr=network_params.get('vendor_start_hr'),
                    pickup_end_hr=network_params.get('pickup_end_hr'),
                    starting_depot=network_params.get('starting_depot'),
                    closing_depot=network_params.get('closing_depot'),
                    allow_night_wait=network_params.get('allow_night_wait', True)
                )
                
                # Set minimum date for time conversion in output
                optimizer.min_date = net.min_date
                
                # Solve using ALNS metaheuristic solver
                status, x, y = optimizer.solve_with_metaheuristic(
                    w=w,
                    max_iterations=model_params.get('alns_max_iterations', 1000),
                    verbose=False
                )
                
                # Print results
                print('\n Results:')
                print(f'   - Solver status: {status} (0=feasible, 2=infeasible)')
                
                if status == 0:  # FEASIBLE
                    # Print friendly solution summary
                    optimizer.print_solution_summary(x, y)
                    
                    # Plot the routes with solver type in filename
                    solver_type = 'alns'
                    plot_path = os.path.join(results_path, f'routes_{period[0][:10]}_{solver_type}.html')
                    os.makedirs(results_path, exist_ok=True)
                    optimizer.plot_routes(x, y, show_plot=True, save_path=plot_path)
                    
                    # Save solution
                    optimizer.save_solution(results_path)
                    print(f'   - Solution saved to {results_path}')
                elif status == 2:  # INFEASIBLE
                    print('   ✗ Problem is infeasible - no valid solution exists')
                else:
                    print(f'   - No solution found (status={status})')
                    
            except Exception as e:
                print(f'\nError during optimization: {e}')
                import traceback
                traceback.print_exc()
                print('\nNote: The optimization problem may be too large or complex for available resources.')

            print('\n Finish Iteration\n')
