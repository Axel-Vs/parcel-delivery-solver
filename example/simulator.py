from pathlib import Path
import sys
import pandas as pd
import os

# Ensure project root is on sys.path before importing model modules
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from model.graph_creator.graph_creator import Graph
from model.optimizer.delivery_model import DeliveryOptimizer
from model.utils.project_utils import *

# Get the current working directory
main_root = os.getcwd()

# Define paths for configuration files, data, and results
parameters_path = os.path.join(main_root, 'model/config')
data_path = os.path.join(main_root, 'data/amazon_test_dataset.csv')  # Using medium test with 5 vendors
results_path = os.path.join(main_root, 'results/optimization')

print('\nStarting Simulation\n')

# Import parameters for the simulation from configuration files
network_params, model_params, simulation_params = import_parameters(parameters_path)

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

# Map gross weight / loading meters
if 'Vendor Gross Weight' in df.columns:
    df['Total Gross Weight'] = df['Vendor Gross Weight']
if 'Vendor Volume in m3' in df.columns:
    df['Calculated Loading Meters'] = df['Vendor Volume in m3']
elif 'Vendor Linear Length' in df.columns:
    df['Calculated Loading Meters'] = df['Vendor Linear Length']
elif 'Vendor Loading Meters' in df.columns:
    df['Calculated Loading Meters'] = df['Vendor Loading Meters']
elif 'Vendor Dimensions in m3' in df.columns:
    # fallback: use dimensions as a proxy
    df['Calculated Loading Meters'] = df['Vendor Dimensions in m3']

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
                # Convert floats that represent postcodes to integer-like strings
                # Handle float postcodes like 98101.0 -> '98101', but preserve trailing zeros in actual postcodes
                def normalize_postcode(v):
                    if pd.isna(v):
                        return ''
                    s = str(v).strip()
                    # Only remove .0 if it's actually a float representation (contains decimal point)
                    if '.' in s and s.endswith('.0'):
                        try:
                            # Convert to float then int to remove .0 safely
                            return str(int(float(s)))
                        except:
                            return s
                    return s
                df[col] = df[col].apply(normalize_postcode)
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

            # Determine solver mode BEFORE creating time-expanded network
            solver_mode = model_params.get('solver_mode', 'auto')
            vendor_threshold = model_params.get('vendor_threshold', 15)
            num_vendors = len(vendors_df)
            
            # Auto mode: choose solver based on problem size
            if solver_mode == 'auto':
                use_metaheuristic = num_vendors > vendor_threshold
            elif solver_mode == 'metaheuristic' or solver_mode == 'alns':
                use_metaheuristic = True
            else:  # 'exact' or 'mip'
                use_metaheuristic = False

            print('\n Create Graph')
            # Create and discretize the Graph; wrap network calls to avoid hard failure during smoke runs
            try:
                net.create_network(complete_coordinates, vendors_df)
                net.discretize()
                
                # Only create time-expanded network for exact MIP solver
                # Metaheuristic doesn't need it and it can fail with large date ranges
                if not use_metaheuristic:
                    time_expanded_network, complete_time_index, time_expanded_network_index = net.create_time_network(vendors_df, period[0], period[1])
                    
                    # Print network statistics for MIP solver
                    print(f'Time-expanded network created: {len(time_expanded_network)} arcs')
                    print(f'Unique nodes in network: {len(set([arc[0][0] for arc in time_expanded_network] + [arc[1][0] for arc in time_expanded_network]))}')
                    print(f'Time index range: {min(time_expanded_network_index)} to {max(time_expanded_network_index)}')
                    
                    # Check arc distribution per node
                    from collections import Counter
                    node_arcs = Counter([arc[0][0] for arc in time_expanded_network] + [arc[1][0] for arc in time_expanded_network])
                    print(f'\nArcs per node: {dict(sorted(node_arcs.items()))}')
                    
                    print(f'\nSample arcs (first 10):')
                    for i, arc in enumerate(time_expanded_network[:10]):
                        print(f'  Arc {i}: {arc} -> Node {arc[0][0]} at time {arc[0][1]} → Node {arc[1][0]} at time {arc[1][1]}')
                else:
                    # For metaheuristic, use dummy time-expanded network and set required attributes
                    print('✓ Skipping time-expanded network (not needed for metaheuristic)')
                    time_expanded_network = []  # Empty list, won't be used
                    complete_time_index = []
                    time_expanded_network_index = []
                    # Set dummy Tau_hours attribute (metaheuristic doesn't use it)
                    net.Tau_hours = [0]
                    net.min_date = pd.to_datetime(period[0])
            except Exception as e:
                print('Skipping network creation due to error (likely ORS/network):', e)
                continue

            # Create cargo and loading matrices
            capacity_matrix = cargo_vector(vendors_df)
            loading_matrix = loading_vector(vendors_df)
            
            # Print solver information (use_metaheuristic already determined above)
            if use_metaheuristic:
                print(f'\n 🚀 Solver: ALNS Metaheuristic (fast mode for {num_vendors} vendors)')
                print(f'   - Max iterations: {model_params.get("alns_max_iterations", 1000)}')
                print(f'   - Problem size: {num_vendors} vendors')
            else:
                print(f'\n 🎯 Solver: OR-Tools MIP (exact mode for {num_vendors} vendors)')
                print(f'   - Max solving time: {model_params.get("max_time", 360)} minutes')
                print(f'   - MIP gap tolerance: {model_params.get("gap_value", 0.05)}')
                print(f'   - Network size: {len(time_expanded_network)} arcs, {num_vendors+1} nodes')
            
            try:
                # Create optimizer instance
                optimizer = DeliveryOptimizer(
                    evaluation_period=period,
                    discretization_constant=net.discretization_constant,
                    time_expanded_network=time_expanded_network,
                    time_expanded_network_index=time_expanded_network_index,
                    Tau_hours=net.Tau_hours,
                    distance_matrix=net.distance_matrix,
                    time_distance_matrix=net.time_distance_matrix,
                    disc_time_distance_matrix=net.disc_time_distance_matrix,
                    capacity_matrix=capacity_matrix,
                    loading_matrix=loading_matrix,
                    max_capacity=network_params['max_weight'],
                    max_volume=network_params.get('max_volume', 90),
                    max_linear_length=network_params.get('max_linear_length', 16.1),
                    max_driving=network_params['max_driving'],
                    is_gap=model_params.get('gap', False),
                    mip_gap=model_params.get('gap_value', 0.05),
                    maximum_minutes=model_params.get('max_time', 360),
                    vendors_df=vendors_df
                )
                
                # Set minimum date for time conversion in output
                optimizer.min_date = net.min_date
                
                # Solve using selected method
                if use_metaheuristic:
                    # Use ALNS metaheuristic solver
                    status, x, y = optimizer.solve_with_metaheuristic(
                        w=w,
                        max_iterations=model_params.get('alns_max_iterations', 1000),
                        verbose=True
                    )
                else:
                    # Use exact MIP solver
                    print('   - Building optimization model...')
                    print(f'   - Optimizing with weight w={w} (distance) vs {1-w} (vehicles)')
                    optimizer.create_model(w=w)
                    
                    print(f'   - Nodes to visit: {optimizer.nodes}')
                    print(f'   - Constraint: each node visited exactly once')
                    print('   - Solving (this may take several minutes)...')
                    status, x, y = optimizer.solve_model()
                
                # Print results
                print('\n Results:')
                print(f'   - Solver status: {status} (0=optimal, 1=feasible, 2=infeasible)')
                
                if status == 0:  # OPTIMAL
                    # Print friendly solution summary
                    optimizer.print_solution_summary(x, y)
                    
                    optimizer.print_status(status, x, y)
                    
                    # Plot the routes with solver type in filename
                    solver_type = 'metaheuristic' if use_metaheuristic else 'exact'
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
