"""
Flask API Backend for Parcel Delivery Optimizer
Provides REST endpoints for route optimization
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import pandas as pd
import numpy as np
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
import requests

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from model.graph_creator.graph_creator import Graph
from model.optimizer.delivery_model import DeliveryOptimizer
from model.utils.coordinate_validator import validate_coordinates
from model.optimizer.alns_solver import ALNSSolver
from model.optimizer.route_edit import insert_stop_best_position, remove_stop
from model.utils.run_storage import save_run, list_runs, load_run, generate_run_id
import json

app = Flask(__name__, static_folder='web', static_url_path='')
CORS(app)

# Configure file logging to /tmp/flask_app.log
log_path = '/tmp/flask_app.log'
try:
    file_handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=3)
    file_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    if not any(getattr(h, 'baseFilename', None) == file_handler.baseFilename for h in root_logger.handlers):
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(file_handler)
except Exception:
    # Non-fatal: if handler fails, continue with stdout logging
    pass

# Create necessary directories
os.makedirs('uploads', exist_ok=True)
os.makedirs('results/optimization', exist_ok=True)

# Simple in-memory state for last optimized plan
APP_STATE = {
    'routes': None,               # List[List[int]]
    'distance_matrix': None,      # 2D list
    'capacity_matrix': None,      # List[float], depot at index 0
    'loading_matrix': None,       # List[float], depot at index 0
    'frozen_prefix': None,        # List[int] per route (optional)
}


@app.route('/')
def index():
    """Serve the main HTML interface"""
    return send_from_directory('web', 'index.html')


@app.route('/api/upload-csv', methods=['POST'])
def upload_csv():
    """Handle CSV file upload and return vendor data"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        # Save uploaded file
        filepath = os.path.join('uploads', f'vendors_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv')
        file.save(filepath)
        
        # Store in APP_STATE for later saves
        APP_STATE['csv_filepath'] = filepath
        
        # Read and validate CSV
        df = pd.read_csv(filepath)
        
        # Return vendor data as JSON
        # The CSV will be processed and geocoded during the optimization phase
        vendors = []
        for idx, row in df.iterrows():
            vendors.append({
                'id': idx + 1,
                'name': str(row.get('vendor Name', row.get('Vendor Name', f'Vendor {idx + 1}'))),
                'city': str(row.get('Vendor City', 'N/A')),
                'latitude': float(row.get('vendor_latitude', 0)),
                'longitude': float(row.get('vendor_longitude', 0)),
                'recipient_latitude': float(row.get('recipient_latitude', 0)),
                'recipient_longitude': float(row.get('recipient_longitude', 0)),
                'weight': float(row.get('Total Gross Weight', row.get('Vendor Gross Weight', 0))),
                'volume': float(row.get('Calculated Loading Meters', row.get('Vendor Loading Meters', row.get('Vendor Dimensions in m3', 0)))),
                'delivery_date': str(row.get('Requested Delivery', row.get('Requested Delivery Date', '')))
            })
        
        return jsonify({
            'success': True,
            'vendors': vendors,
            'count': len(vendors),
            'filepath': filepath
        })
    
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in upload-csv: {error_details}")
        return jsonify({'error': str(e), 'details': error_details}), 500


@app.route('/api/optimize', methods=['POST'])
def optimize_routes():
    """Run route optimization with provided parameters"""
    try:
        data = request.json
        vendors_data = data.get('vendors', [])
        params = data.get('parameters', {})
        csv_filepath = data.get('csv_filepath', None)
        
        print(f"\n=== OPTIMIZE REQUEST DEBUG ===")
        print(f"Number of vendors: {len(vendors_data)}")
        print(f"CSV filepath: {csv_filepath}")
        if vendors_data and len(vendors_data) > 0:
            print(f"First vendor keys: {list(vendors_data[0].keys()) if isinstance(vendors_data[0], dict) else 'Not a dict'}")
            print(f"First vendor sample: {vendors_data[0] if len(str(vendors_data[0])) < 200 else str(vendors_data[0])[:200]}")
        print(f"=== END OPTIMIZE DEBUG ===\n")
        
        # Allow either vendors_data or a csv_filepath
        if not vendors_data and not csv_filepath:
            return jsonify({'error': 'No vendor data or csv_filepath provided'}), 400
        
        # Get other parameters
        use_metaheuristic = params.get('use_metaheuristic', True)
        max_vehicles = len(vendors_data)  # Always use all vendors as max vehicles
        period = None  # Initialize period at top of function
        
        # Read the original CSV file to preserve all columns for geocoding
        if csv_filepath and os.path.exists(csv_filepath):
            print(f"Using CSV file: {csv_filepath}")
            df_raw = pd.read_csv(csv_filepath)
            
            # Calculate period from RAW dates spanning earliest loading to latest delivery
            period = None
            min_date = None
            max_date = None
            
            # Get earliest loading date
            if 'Requested Loading Date' in df_raw.columns:
                loading_dates = pd.to_datetime(df_raw['Requested Loading Date'], errors='coerce').dropna()
                if len(loading_dates) > 0:
                    min_date = loading_dates.min()
                    max_date = loading_dates.max()
            
            # Get latest delivery date and expand range
            if 'Requested Delivery Date' in df_raw.columns:
                delivery_dates = pd.to_datetime(df_raw['Requested Delivery Date'], errors='coerce').dropna()
                if len(delivery_dates) > 0:
                    if min_date is None:
                        min_date = delivery_dates.min()
                    else:
                        min_date = min(min_date, delivery_dates.min())
                    
                    if max_date is None:
                        max_date = delivery_dates.max()
                    else:
                        max_date = max(max_date, delivery_dates.max())
            
            if min_date is not None and max_date is not None:
                period = [min_date, max_date]
                print(f"📅 Tour period: {min_date} to {max_date}")
            
            # Prepare dataframe similar to simulator.py preprocessing
            df = df_raw.copy()
            
            # Map vendor name
            if 'Vendor Name' in df.columns:
                df['vendor Name'] = df['Vendor Name'].astype(str)
            
            # Map weights and loading
            if 'Vendor Gross Weight' in df.columns:
                df['Total Gross Weight'] = df['Vendor Gross Weight']
            if 'Vendor Loading Meters' in df.columns:
                df['Calculated Loading Meters'] = df['Vendor Loading Meters']
            elif 'Vendor Dimensions in m3' in df.columns:
                df['Calculated Loading Meters'] = df['Vendor Dimensions in m3']
            
            # Map dates
            if 'Requested Loading Date' in df.columns:
                df['Requested Loading'] = pd.to_datetime(df['Requested Loading Date'], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')
            else:
                df['Requested Loading'] = ''
            
            if 'Requested Delivery Date' in df.columns:
                df['Requested Delivery'] = pd.to_datetime(df['Requested Delivery Date'], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')
            else:
                df['Requested Delivery'] = df['Requested Loading']
            
            # Track failed geocoding attempts (will be returned to frontend)
            failed_geocodes = []
            
            # Geocode addresses if coordinates not present
            if 'vendor_latitude' not in df.columns or 'recipient_latitude' not in df.columns:
                print("📍 Geocoding addresses...")
                
                # City coordinate fallbacks for common cities (used when geocoding fails)
                city_coords = {
                    'San Francisco': (37.7749, -122.4194),
                    'Chicago': (41.8781, -87.6298),
                    'Houston': (29.7604, -95.3698),
                    'Los Angeles': (34.0522, -118.2437),
                    'New York': (40.7128, -74.0060),
                    'Miami': (25.7617, -80.1918),
                    'Mexico City': (19.4326, -99.1332),
                    'Guadalajara': (20.6597, -103.3496),
                    'Monterrey': (25.6866, -100.3161),
                    'Vancouver': (49.2827, -123.1207),
                    'Toronto': (43.6532, -79.3832),
                    'Montreal': (45.5017, -73.5673),
                    'Seattle': (47.6062, -122.3321)
                }
                
                from model.utils.geocoder import Geocoder
                geocode_cache = os.path.join('data', 'geocode_cache.csv')
                g = Geocoder(cache_path=geocode_cache, user_agent='parcel_web_geocoder', min_delay_seconds=1)
                
                # Determine which columns need geocoding
                need_vendor_geocoding = 'vendor_latitude' not in df.columns
                need_recipient_geocoding = 'recipient_latitude' not in df.columns
                
                # Geocode all addresses
                for idx, row in df.iterrows():
                    if need_vendor_geocoding:
                        # Try city-based fallback first for speed
                        vendor_city = str(row.get('Vendor City', '')).strip()
                        if vendor_city in city_coords:
                            df.at[idx, 'vendor_latitude'] = city_coords[vendor_city][0]
                            df.at[idx, 'vendor_longitude'] = city_coords[vendor_city][1]
                        else:
                            vendor_addr = f"{row.get('Vendor Street', '')}, {row.get('Vendor City', '')}, {row.get('Vendor Postcode', '')}, {row.get('Vendor Country Name', '')}"
                            vendor_coords = g.geocode_address(vendor_addr.strip())
                            if vendor_coords and vendor_coords[0] is not None and vendor_coords[1] is not None:
                                df.at[idx, 'vendor_latitude'] = float(vendor_coords[0])
                                df.at[idx, 'vendor_longitude'] = float(vendor_coords[1])
                            else:
                                df.at[idx, 'vendor_latitude'] = 0.0
                                df.at[idx, 'vendor_longitude'] = 0.0
                                failed_geocodes.append({
                                    'type': 'vendor',
                                    'address': vendor_addr.strip(),
                                    'row': int(idx),
                                    'vendor_name': str(row.get('Vendor Name', 'Unknown'))
                                })
                    
                    if need_recipient_geocoding:
                        # Try city-based fallback first
                        recipient_city = str(row.get('Recipient City', '')).strip()
                        if recipient_city in city_coords:
                            df.at[idx, 'recipient_latitude'] = city_coords[recipient_city][0]
                            df.at[idx, 'recipient_longitude'] = city_coords[recipient_city][1]
                        else:
                            # Geocode recipient address
                            recipient_addr = f"{row.get('Recipient Street', '')}, {row.get('Recipient City', '')}, {row.get('Recipient Postcode', '')}, {row.get('Recipient Country Name', '')}"
                            recipient_coords = g.geocode_address(recipient_addr.strip())
                            if recipient_coords and recipient_coords[0] is not None and recipient_coords[1] is not None:
                                df.at[idx, 'recipient_latitude'] = float(recipient_coords[0])
                                df.at[idx, 'recipient_longitude'] = float(recipient_coords[1])
                            else:
                                # Default to Seattle if geocoding fails
                                df.at[idx, 'recipient_latitude'] = 47.6062
                                df.at[idx, 'recipient_longitude'] = -122.3321
                                failed_geocodes.append({
                                    'type': 'recipient',
                                    'address': recipient_addr.strip(),
                                    'row': int(idx),
                                    'vendor_name': str(row.get('Vendor Name', 'Unknown'))
                                })
                
                print(f"✅ Geocoded {len(df)} addresses")
                
                # Log failed geocoding attempts
                if failed_geocodes:
                    print(f"\n⚠️  Failed to geocode {len(failed_geocodes)} address(es):")
                    for fail in failed_geocodes:
                        print(f"   • {fail['type'].capitalize()}: {fail['address']}")
                        print(f"     Vendor: {fail['vendor_name']} (Row {fail['row']})")
                    
                    # Save to log file
                    failed_log_path = os.path.join('data', 'failed_geocodes.csv')
                    pd.DataFrame(failed_geocodes).to_csv(failed_log_path, index=False)
                    print(f"   📄 Details saved to: {failed_log_path}")
                else:
                    print("✅ All addresses geocoded successfully!")
            
            # Add node_id column to preserve DataFrame index for route matching
            # The optimizer uses df.iterrows() which returns the actual index, not sequential position
            df['node_id'] = df.index
            
            # Save processed CSV for read_data
            temp_csv = os.path.join('uploads', f'processed_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv')
            df.to_csv(temp_csv, index=False)
            
            # Coordinate validation report
            try:
                issues_df, summary = validate_coordinates(df)
                print("\n" + "="*80)
                print("COORDINATE VALIDATION REPORT")
                print("="*80)
                print(f"Total rows: {summary['total_rows']}")
                print(f"Rows with issues: {summary['total_issues']}")
                if summary['total_issues'] > 0:
                    print("Issues breakdown:")
                    for k, v in summary.get('by_issue', {}).items():
                        print(f"  - {k}: {v}")
                    # Save issues to results for inspection
                    os.makedirs('results/validation', exist_ok=True)
                    issues_path = os.path.join('results/validation', f"coordinate_issues_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
                    issues_df.to_csv(issues_path, index=False)
                    print(f"Saved detailed issues to: {issues_path}")
                print("="*80 + "\n")
            except Exception as e:
                print(f"⚠️  Coordinate validation failed: {e}")
        else:
            # Fallback: create DataFrame from JSON vendors_data (no geocoding needed)
            failed_geocodes = []
            df = pd.DataFrame(vendors_data)
            
            # Map fields only if they don't already exist (fresh upload vs CSV reload)
            if 'name' in df.columns and 'vendor Name' not in df.columns and 'Vendor Name' not in df.columns:
                df['vendor Name'] = df['name']
            if 'city' in df.columns and 'Vendor City' not in df.columns:
                df['Vendor City'] = df['city']
            if 'latitude' in df.columns and 'vendor_latitude' not in df.columns:
                df['vendor_latitude'] = df['latitude']
            if 'longitude' in df.columns and 'vendor_longitude' not in df.columns:
                df['vendor_longitude'] = df['longitude']
            if 'weight' in df.columns and 'Total Gross Weight' not in df.columns:
                df['Total Gross Weight'] = df['weight']
            if 'volume' in df.columns and 'Calculated Loading Meters' not in df.columns:
                df['Calculated Loading Meters'] = df['volume']
            if 'delivery_date' in df.columns and 'Requested Delivery' not in df.columns:
                df['Requested Delivery'] = df['delivery_date']
                df['Requested Loading'] = df['delivery_date']
            
            # Add node_id column to preserve DataFrame index for route matching
            df['node_id'] = df.index
            
            temp_csv = os.path.join('uploads', f'temp_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv')
            df.to_csv(temp_csv, index=False)
            
            # Coordinate validation report
            try:
                issues_df, summary = validate_coordinates(df)
                print("\n" + "="*80)
                print("COORDINATE VALIDATION REPORT")
                print("="*80)
                print(f"Total rows: {summary['total_rows']}")
                print(f"Rows with issues: {summary['total_issues']}")
                if summary['total_issues'] > 0:
                    print("Issues breakdown:")
                    for k, v in summary.get('by_issue', {}).items():
                        print(f"  - {k}: {v}")
                    os.makedirs('results/validation', exist_ok=True)
                    issues_path = os.path.join('results/validation', f"coordinate_issues_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
                    issues_df.to_csv(issues_path, index=False)
                    print(f"Saved detailed issues to: {issues_path}")
                print("="*80 + "\n")
            except Exception as e:
                print(f"⚠️  Coordinate validation failed: {e}")
        
        # Get depot coordinates from the first recipient row (they should all be the same depot)
        depot_lat = df['recipient_latitude'].iloc[0] if 'recipient_latitude' in df.columns else 47.6062
        depot_lon = df['recipient_longitude'].iloc[0] if 'recipient_longitude' in df.columns else -122.3321
        
        # Create network parameters
        network_params = {
            'discretization_constant': 4,
            'starting_depot': params.get('starting_depot', 8),
            'closing_depot': params.get('closing_depot', 18),
            'vendor_start_hr': params.get('vendor_start_hr', 6),
            'pickup_end_hr': params.get('pickup_end_hr', 14),
            'loading': params.get('loading', 2),
            'earl_arv': params.get('earl_arv', 24),
            'late_arv': params.get('late_arv', 24),
            'max_driving': params.get('max_driving', 15),
            'driving_starts': params.get('driving_starts', 6),
            'driving_stop': params.get('driving_stop', 21),
            'max_weight': params.get('max_weight', 30),
            'max_ldms': params.get('max_ldms', 70),
            'plot_centered_coordinates': [depot_lat, depot_lon],
            'max_feasible_distance': 3000,
            'time_window_sampling_threshold': 20,
            'time_window_sample_size': 20
        }
        
        # Create graph
        print(f"Creating graph with {len(vendors_data)} vendors...")
        net = Graph(network_params)
        
        # Use period calculated earlier from raw CSV, or fallback to current time
        if period is None:
            print("⚠️ No period calculated from CSV, using current time as fallback")
            period = [pd.Timestamp.now(), pd.Timestamp.now()]
        
        # Convert period to strings for processing
        period_str = [period[0].strftime('%Y-%m-%d %H:%M:%S'), period[1].strftime('%Y-%m-%d %H:%M:%S')]
        
        # Force all date columns to be strings before passing to read_data
        for col in ['Requested Delivery', 'Requested Loading']:
            if col in df.columns:
                df[col] = df[col].apply(lambda x: 
                    x.strftime('%Y-%m-%d %H:%M:%S') if hasattr(x, 'strftime') 
                    else str(x) if x and not pd.isna(x) 
                    else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                )
        
        # Read data into graph
        print(f"Reading vendor data for period {period_str[0]} to {period_str[1]}...")
        try:
            complete_coordinates, vendors_df = net.read_data(period_str, df)
        except Exception as e:
            return jsonify({'error': f'Failed to read vendor data: {str(e)}'}), 500
        
        print(f"Successfully loaded {len(vendors_df)} vendors")
        
        # Create network
        net.create_network(complete_coordinates, vendors_df)
        
        # Discretize the network (creates disc_time_distance_matrix)
        net.discretize()
        
        # Extract matrices and parameters
        capacity_matrix = vendors_df['Total Gross Weight'].to_numpy()
        loading_matrix = vendors_df['Calculated Loading Meters'].to_numpy()
        
        # Prepare network data based on solver type (following simulator.py pattern)
        if use_metaheuristic:
            # Metaheuristic doesn't need time-expanded network
            print("🚀 Using ALNS metaheuristic solver (fast mode)")
            time_expanded_network = []
            time_expanded_network_index = []
            net.Tau_hours = [0]
            net.min_date = period[0]
        else:
            # MIP solver requires time-expanded network
            print("🎯 Using exact MIP solver")
            time_expanded_network, complete_time_index, time_expanded_network_index = net.create_time_network(
                vendors_df, period_str[0], period_str[1]
            )
        
        # Create optimizer (same for both solver types)
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
            max_ldms=network_params['max_ldms'],
            max_driving=network_params['max_driving'],
            is_gap=not use_metaheuristic,
            mip_gap=0.05,
            maximum_minutes=360 if not use_metaheuristic else 60,
            vendors_df=vendors_df
        )
        optimizer.min_date = net.min_date
        
        # Run optimization with selected solver
        print(f"Starting optimization...")
        start_time = datetime.now()
        
        if use_metaheuristic:
            status, x, y = optimizer.solve_with_metaheuristic(
                w=0.5,
                max_iterations=params.get('iterations', 2000),
                verbose=True
            )
            solver_type = "ALNS Metaheuristic"
        else:
            optimizer.create_model(w=0.5)
            status, x, y = optimizer.solve_model()
            solver_type = "CBC MIP"
        
        solving_time = (datetime.now() - start_time).total_seconds()
        
        # Check if solution was found
        if status != 0:
            return jsonify({'error': f'Optimization failed with status {status} (0=optimal, 1=feasible, 2=infeasible)'}), 500
        
        # Generate map
        map_filename = f'routes_{datetime.now().strftime("%Y%m%d_%H%M%S")}.html'
        map_path = os.path.join('results/optimization', map_filename)
        os.makedirs('results/optimization', exist_ok=True)
        
        # Plot routes using the optimizer's built-in method
        # Suppress stdout to prevent BrokenPipeError from excessive printing
        import sys
        from io import StringIO
        old_stdout = sys.stdout
        sys.stdout = StringIO()  # Redirect stdout
        try:
            _, route_stats = optimizer.plot_routes(x, y, show_plot=False, save_path=map_path)
        finally:
            sys.stdout = old_stdout  # Restore stdout
        
        # Store map_path in APP_STATE for manual saves
        APP_STATE['map_path'] = map_path
        
        # Extract statistics from route_stats returned by plot_routes
        num_vehicles_used = len(route_stats)
        total_distance = sum(stats['total_distance'] for stats in route_stats.values())
        total_cargo_kg = sum(stats['total_cargo'] for stats in route_stats.values())
        total_cargo_tons = total_cargo_kg / 1000.0
        total_loading = sum(stats['total_loading'] for stats in route_stats.values())

        # Identify included vendors first
        included_vendor_ids = set()
        for vehicle_id in sorted(route_stats.keys()):
            vendors_seq = route_stats[vehicle_id].get('vendors', [])
            included_vendor_ids.update([int(v) for v in vendors_seq])
        
        # Total volume (m³) - only for INCLUDED vendors
        volume_series = None
        for vol_col in ['Vendor Dimensions in m3', 'Total Volume (cbm)', 'volume', 'Vendor Loading Meters', 'Calculated Loading Meters']:
            if vol_col in vendors_df.columns:
                volume_series = vendors_df[vol_col]
                break
        
        # Calculate total volume only for included vendors (1-indexed vendor IDs)
        if volume_series is not None:
            included_indices = [vid - 1 for vid in included_vendor_ids]  # Convert to 0-indexed
            total_volume = float(volume_series.iloc[included_indices].fillna(0).sum())
        else:
            total_volume = 0.0
        
        print(f"Solution found: {num_vehicles_used} vehicles used")
        print(f"Total distance: {total_distance:.0f} km")
        print(f"Total weight: {total_cargo_tons:.1f} tons (raw kg: {total_cargo_kg:.0f})")
        print(f"Total loading: {total_loading:.1f} m")
        print(f"Total volume (included vendors only): {total_volume:.1f} m³")
        print(f"[DEBUG] Stats being sent - total_cargo value: {float(round(total_cargo_tons, 3))}")
        
        # Build vendor name map first (needed for route summaries)
        vendor_name_map = {}
        vendor_date_map = {}
        for idx, row in vendors_df.iterrows():
            vendor_id = int(idx + 1)
            vendor_name_map[vendor_id] = str(row.get('vendor Name', row.get('Vendor Name', f'Vendor {vendor_id}')))
            
            raw_date = row.get('Requested Delivery', row.get('Requested Delivery Date', ''))
            parsed_date = pd.to_datetime(raw_date, errors='coerce')
            if pd.notna(parsed_date):
                vendor_date_map[vendor_id] = parsed_date.strftime('%Y-%m-%d')
        
        # Create detailed route summaries from statistics
        route_summaries = []
        for vehicle_id in sorted(route_stats.keys()):
            stats = route_stats[vehicle_id]
            
            # Add vendor names to segments
            segments_with_names = []
            for seg in stats.get('segments', []):
                from_id = seg['from_id']
                to_id = seg['to_id']
                
                # Get vendor names (0 = Depot)
                from_name = 'Depot' if from_id == 0 else vendor_name_map.get(from_id, f'Vendor {from_id}')
                to_name = 'Depot' if to_id == 0 else vendor_name_map.get(to_id, f'Vendor {to_id}')
                
                segments_with_names.append({
                    'from': from_name,
                    'to': to_name,
                    'distance': float(round(seg['distance'], 1)),
                    'duration': float(round(seg['duration'], 2)),
                    'avg_speed': float(round(seg['avg_speed'], 0))
                })
            
            route_summaries.append({
                'route_id': int(vehicle_id + 1),
                'num_vendors': int(stats['num_vendors']),
                'distance': float(round(stats['total_distance'], 1)),
                'cargo': float(round(stats['total_cargo'] / 1000.0, 3)),  # tons
                'loading': float(round(stats['total_loading'], 2)),
                'segments': segments_with_names  # Add segment details with names
            })

        # Build filter metadata (vendor names, delivery dates, route-vendor mappings, week options)
        vendor_routes_map = {}
        route_vendors_map = {}
        week_options = []

        try:
            # Prepare vendor name and delivery date lookup (1-indexed)
            for idx, row in vendors_df.iterrows():
                vendor_id = int(idx + 1)
                vendor_name_map[vendor_id] = str(row.get('vendor Name', row.get('Vendor Name', f'Vendor {vendor_id}')))

                raw_date = row.get('Requested Delivery', row.get('Requested Delivery Date', ''))
                parsed_date = pd.to_datetime(raw_date, errors='coerce')
                if pd.notna(parsed_date):
                    vendor_date_map[vendor_id] = parsed_date.strftime('%Y-%m-%d')

            # Map routes to vendors (and vendors to routes for fast lookup)
            for vehicle_id in sorted(route_stats.keys()):
                vendors_seq = route_stats[vehicle_id].get('vendors', [])
                route_number = int(vehicle_id + 1)
                route_vendors_map[route_number] = [int(v) for v in vendors_seq]

                for v in vendors_seq:
                    v_int = int(v)
                    v_name = vendor_name_map.get(v_int)
                    if v_name:
                        vendor_routes_map.setdefault(v_name, []).append(route_number)

            # Compute contiguous weekly options based on available delivery dates (Mon-Sun)
            if len(vendor_date_map) > 0:
                all_dates = pd.to_datetime(list(vendor_date_map.values()), errors='coerce').dropna()
                if len(all_dates) > 0:
                    min_date = all_dates.min()
                    max_date = all_dates.max()

                    week_start = min_date - pd.to_timedelta(min_date.weekday(), unit='d')
                    week_end = max_date + pd.to_timedelta(6 - max_date.weekday(), unit='d')

                    current_start = week_start
                    week_idx = 1
                    while current_start <= week_end:
                        current_end = current_start + pd.Timedelta(days=6)
                        week_options.append({
                            'value': f"{current_start.strftime('%Y-%m-%d')}|{current_end.strftime('%Y-%m-%d')}",
                            'label': f"Week {week_idx}: {current_start.strftime('%Y-%m-%d')} to {current_end.strftime('%Y-%m-%d')}"
                        })
                        week_idx += 1
                        current_start += pd.Timedelta(days=7)
        except Exception as _:
            # Non-fatal; filters will simply not be pre-populated
            pass

        # Identify excluded vendors
        
        all_vendor_ids = set(range(1, len(vendors_df) + 1))  # 1-indexed
        excluded_vendor_ids = sorted(list(all_vendor_ids - included_vendor_ids))
        
        excluded_vendors = []
        for vendor_id in excluded_vendor_ids:
            vendor_row = vendors_df.iloc[vendor_id - 1]  # Convert back to 0-indexed
            vendor_name = vendor_row.get('Vendor Name', vendor_row.get('vendor Name', f'Vendor {vendor_id}'))
            vendor_city = vendor_row.get('Vendor City', 'N/A')
            weight = vendor_row.get('Vendor Gross Weight', vendor_row.get('Total Gross Weight', 0))
            
            # Determine specific constraint violation
            reasons = []
            
            # Check weight capacity (in kg)
            max_weight_kg = network_params['max_weight'] * 1000  # Convert tons to kg
            if weight > max_weight_kg:
                reasons.append(f'Weight exceeds capacity ({weight:.0f} kg > {max_weight_kg:.0f} kg)')
            
            # Check loading meters capacity
            loading_meters = 0
            for ldm_col in ['Vendor Loading Meters', 'Calculated Loading Meters', 'loading_meters']:
                if ldm_col in vendor_row and pd.notna(vendor_row[ldm_col]):
                    loading_meters = float(vendor_row[ldm_col])
                    break
            
            max_ldms = network_params['max_ldms']
            if loading_meters > max_ldms:
                reasons.append(f'Loading exceeds capacity ({loading_meters:.1f} m > {max_ldms:.1f} m)')
            
            # Check geocoding failure
            vendor_lat = vendor_row.get('vendor_latitude', 0)
            vendor_lon = vendor_row.get('vendor_longitude', 0)
            if vendor_lat == 0 or vendor_lon == 0:
                reasons.append('Geocoding failed (invalid coordinates)')
            
            # Check time window feasibility
            requested_delivery = vendor_row.get('Requested Delivery', None)
            if pd.notna(requested_delivery):
                try:
                    delivery_time = pd.to_datetime(requested_delivery)
                    # Check if delivery time is outside the optimization period
                    if period and (delivery_time < period[0] or delivery_time > period[1]):
                        reasons.append(f'Delivery time outside period ({delivery_time.strftime("%Y-%m-%d")} not in [{period[0].strftime("%Y-%m-%d")}, {period[1].strftime("%Y-%m-%d")}])')
                except:
                    reasons.append('Invalid delivery date format')
            else:
                reasons.append('Missing delivery time window')
            
            # Default reason if no specific constraint found
            reason = '; '.join(reasons) if reasons else 'Infeasible constraint (solver rejected)'
            
            excluded_vendors.append({
                'vendor_id': int(vendor_id),
                'vendor_name': str(vendor_name),
                'city': str(vendor_city),
                'weight': float(weight),
                'reason': reason
            })
        
        print(f"Included {len(included_vendor_ids)} vendors, excluded {len(excluded_vendors)} vendors")
        try:
            # Reconstruct simple routes as [0] + vendors + [0]
            cached_routes = []
            for vehicle_id in sorted(route_stats.keys()):
                vendors_seq = route_stats[vehicle_id].get('vendors', [])
                cached_routes.append([0] + vendors_seq + [0])

            APP_STATE['routes'] = cached_routes
            # Convert numpy arrays to plain lists and ensure depot padding
            APP_STATE['capacity_matrix'] = [0.0] + list(np.array(capacity_matrix, dtype=float))
            APP_STATE['loading_matrix'] = [0.0] + list(np.array(loading_matrix, dtype=float))
            # Distance matrix from graph (convert to list of lists if numpy)
            dm = net.distance_matrix
            APP_STATE['distance_matrix'] = dm.tolist() if hasattr(dm, 'tolist') else dm
            # Store time matrix (seconds)
            APP_STATE['time_matrix'] = net.time_distance_matrix.tolist() if hasattr(net.time_distance_matrix, 'tolist') else net.time_distance_matrix
            # Store capacities
            APP_STATE['max_capacity_kg'] = float(network_params['max_weight'] * 1000.0)
            APP_STATE['max_ldms_vc'] = float(network_params['max_ldms'])
            # Store simple time windows based on requested delivery ±12h
            APP_STATE['min_date'] = str(net.min_date) if hasattr(net, 'min_date') else str(period[0])
            # Build earliest/latest arrays aligned to node index (0=depot placeholder)
            earliest = [None] * (len(vendors_df) + 1)
            latest = [None] * (len(vendors_df) + 1)
            base = pd.to_datetime(net.min_date) if hasattr(net, 'min_date') else pd.to_datetime(period[0])
            for node_id, row in vendors_df.iterrows():
                ts = pd.to_datetime(row.get('Requested Delivery', None), errors='coerce')
                if pd.isna(ts):
                    continue
                offset = (ts - base).total_seconds()
                # window ±12h
                earliest[node_id] = max(0.0, offset - 12 * 3600)
                latest[node_id] = offset + 12 * 3600
            APP_STATE['earliest'] = earliest
            APP_STATE['latest'] = latest
            # Record original used vendors for status tracking
            orig_used = set()
            for vehicle_id in sorted(route_stats.keys()):
                vendors_seq = route_stats[vehicle_id].get('vendors', [])
                for v in vendors_seq:
                    orig_used.add(int(v))
            APP_STATE['original_used_vendors'] = sorted(list(orig_used))
            # Optional: reset frozen prefixes
            APP_STATE['frozen_prefix'] = [0] * len(cached_routes)
        except Exception as _:
            pass

        # Persist this run to disk for survival across restarts
        run_id = None
        try:
            run_id = generate_run_id('run')
            run_name = f"Run {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            metadata = {
                'solver_type': solver_type,
                'period': [str(period[0]), str(period[1])],
                'num_vendors': int(len(vendors_data)),
                'csv_filepath': csv_filepath,
                'map_path': map_path,
                'original_used_vendors': APP_STATE.get('original_used_vendors'),
            }
            save_run(
                run_id=run_id,
                name=run_name,
                state={
                    'routes': APP_STATE['routes'],
                    'distance_matrix': APP_STATE['distance_matrix'],
                    'capacity_matrix': APP_STATE['capacity_matrix'],
                    'loading_matrix': APP_STATE['loading_matrix'],
                    'frozen_prefix': APP_STATE['frozen_prefix'],
                },
                metadata=metadata,
            )
        except Exception as _:
            pass
        
        # Ensure total_cargo is in tons (divide by 1000 if needed)
        final_total_cargo = total_cargo_tons
        if final_total_cargo > 10000:  # If still looks like kg, divide again
            final_total_cargo = final_total_cargo / 1000.0
        
        return jsonify({
            'success': True,
            'map_url': f'/results/optimization/{map_filename}',
            'statistics': {
                'total_distance': float(round(total_distance, 1)),
                'total_cargo': float(round(final_total_cargo, 3)),
                'total_loading': float(round(total_loading, 2)),
                'total_volume': float(round(total_volume, 2)),
                'num_routes': int(num_vehicles_used),
                'num_vendors': int(len(included_vendor_ids)),  # Only included vendors
                'solving_time': float(round(solving_time, 2)),
                'solver_type': str(solver_type)
            },
            'routes': route_summaries,
            'filter_metadata': {
                'vendor_routes': vendor_routes_map,
                'vendor_delivery_dates': vendor_date_map,
                'route_vendors': route_vendors_map,
                'week_options': week_options
            },
            'excluded_vendors': excluded_vendors,
            'failed_geocodes': failed_geocodes,
            'run_id': run_id
        })
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/results/optimization/<path:filename>')
def serve_map(filename):
    """Serve generated map files"""
    return send_from_directory('results/optimization', filename)

@app.route('/results/runs/<run_id>/<path:filename>')
def serve_run_file(run_id, filename):
    """Serve artifacts saved per run (e.g., map.html, input.csv copies)."""
    base = os.path.join('results', 'runs', run_id)
    return send_from_directory(base, filename)


@app.route('/api/route/add-stop', methods=['POST'])
def add_stop_to_plan():
    """Insert a new stop into existing routes without full re-optimization.

    Expects JSON payload with:
    - routes: list of routes (each route is list of ints, depot=0)
    - new_stop: int index to insert
    - distance_matrix: 2D list (km)
    - capacity_matrix: list of weights per node
    - loading_matrix: list of volumes per node
    - max_capacity_kg: vehicle capacity (kg)
    - max_ldms_vc: vehicle volume capacity
    - frozen_prefix (optional): list of ints per route indicating immutable prefix length
    - allow_new_route (optional): bool
    """
    try:
        payload = request.get_json(force=True, silent=False) or {}
        required = [
            'routes', 'new_stop', 'distance_matrix', 'capacity_matrix',
            'loading_matrix', 'max_capacity_kg', 'max_ldms_vc'
        ]
        missing = [k for k in required if k not in payload]
        if missing:
            return jsonify({'success': False, 'error': f'Missing fields: {missing}'}), 400

        routes = payload['routes']
        new_stop = int(payload['new_stop'])
        distance_matrix = payload['distance_matrix']
        capacity_matrix = payload['capacity_matrix']
        loading_matrix = payload['loading_matrix']
        max_capacity_kg = float(payload['max_capacity_kg'])
        max_ldms_vc = float(payload['max_ldms_vc'])
        frozen_prefix = payload.get('frozen_prefix')
        allow_new_route = bool(payload.get('allow_new_route', True))

        result = insert_stop_best_position(
            routes=routes,
            new_stop=new_stop,
            distance_matrix=distance_matrix,
            capacity_matrix=capacity_matrix,
            loading_matrix=loading_matrix,
            max_capacity_kg=max_capacity_kg,
            max_ldms_vc=max_ldms_vc,
            frozen_prefix=frozen_prefix,
            allow_new_route=allow_new_route,
        )

        status = 200 if result.get('success') else 400
        return jsonify(result), status
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/route/remove-stop', methods=['POST'])
def remove_stop_from_plan():
    """Remove a stop from routes without full re-optimization.

    Expects JSON payload with:
    - routes: list of routes
    - stop: int index to remove
    """
    try:
        payload = request.get_json(force=True, silent=False) or {}
        if 'routes' not in payload or 'stop' not in payload:
            return jsonify({'success': False, 'error': 'Missing routes or stop'}), 400

        routes = payload['routes']
        stop = int(payload['stop'])
        result = remove_stop(routes=routes, stop=stop)
        status = 200 if result.get('success') else 404
        return jsonify(result), status
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/route/add-stop-state', methods=['POST'])
def add_stop_using_state():
    """Insert a stop using cached plan state (no matrices required).

    JSON payload:
    - new_stop: int node index (vendor node id)
    - frozen_prefix (optional): list[int]
    - allow_new_route (optional): bool
    """
    try:
        payload = request.get_json(force=True, silent=False) or {}
        if APP_STATE['routes'] is None or APP_STATE['distance_matrix'] is None:
            return jsonify({'success': False, 'error': 'No cached plan. Run /api/optimize first.'}), 400

        new_stop = int(payload.get('new_stop', -1))
        if new_stop < 1:
            return jsonify({'success': False, 'error': 'Invalid new_stop'}), 400

        frozen_prefix = payload.get('frozen_prefix', APP_STATE.get('frozen_prefix'))
        allow_new_route = bool(payload.get('allow_new_route', True))

        result = insert_stop_best_position(
            routes=APP_STATE['routes'],
            new_stop=new_stop,
            distance_matrix=APP_STATE['distance_matrix'],
            capacity_matrix=APP_STATE['capacity_matrix'],
            loading_matrix=APP_STATE['loading_matrix'],
            max_capacity_kg=APP_STATE.get('max_capacity_kg', 0.0),
            max_ldms_vc=APP_STATE.get('max_ldms_vc', 0.0),
            frozen_prefix=frozen_prefix,
            allow_new_route=allow_new_route,
            time_matrix=APP_STATE.get('time_matrix'),
            earliest=APP_STATE.get('earliest'),
            latest=APP_STATE.get('latest'),
            start_time_seconds=0.0,
        )

        if result.get('success'):
            APP_STATE['routes'] = result['routes']
            # Update statuses compared to original
            orig_set = set(APP_STATE.get('original_used_vendors') or [])
            cur_set = set()
            for r in APP_STATE['routes']:
                for n in r:
                    if n != 0:
                        cur_set.add(int(n))
            APP_STATE['statuses'] = {int(n): ('original' if n in orig_set else 'added') for n in cur_set}
            return jsonify(result), 200
        return jsonify(result), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/route/remove-stop-state', methods=['POST'])
def remove_stop_using_state():
    """Remove a stop using cached plan state (no matrices required).

    JSON payload:
    - stop: int node index (vendor node id)
    """
    try:
        payload = request.get_json(force=True, silent=False) or {}
        if APP_STATE['routes'] is None:
            return jsonify({'success': False, 'error': 'No cached plan. Run /api/optimize first.'}), 400

        stop = int(payload.get('stop', -1))
        if stop < 1:
            return jsonify({'success': False, 'error': 'Invalid stop'}), 400

        result = remove_stop(routes=APP_STATE['routes'], stop=stop)
        if result.get('success'):
            APP_STATE['routes'] = result['routes']
            # Update statuses compared to original
            orig_set = set(APP_STATE.get('original_used_vendors') or [])
            cur_set = set()
            for r in APP_STATE['routes']:
                for n in r:
                    if n != 0:
                        cur_set.add(int(n))
            # nodes in original but not current are removed
            statuses = {int(n): ('original' if n in cur_set else 'removed') for n in orig_set}
            for n in cur_set:
                if n not in statuses:
                    statuses[int(n)] = 'added'
            APP_STATE['statuses'] = statuses
            return jsonify(result), 200
        return jsonify(result), 404
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/runs', methods=['GET'])
def list_all_runs():
    """List all saved runs (metadata only)."""
    try:
        runs = list_runs()
        return jsonify({'success': True, 'runs': runs}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/runs/load', methods=['POST'])
def load_run_into_state():
    """Load a run by id and set APP_STATE from disk."""
    try:
        payload = request.get_json(force=True, silent=False) or {}
        run_id = payload.get('run_id')
        if not run_id:
            return jsonify({'success': False, 'error': 'run_id required'}), 400
        data = load_run(run_id)
        if not data.get('success'):
            return jsonify(data), 404
        state = data['state']
        APP_STATE['routes'] = state.get('routes')
        APP_STATE['distance_matrix'] = state.get('distance_matrix')
        APP_STATE['capacity_matrix'] = state.get('capacity_matrix')
        APP_STATE['loading_matrix'] = state.get('loading_matrix')
        APP_STATE['frozen_prefix'] = state.get('frozen_prefix')
        return jsonify({'success': True, 'run': data['metadata'], 'routes': APP_STATE['routes']}), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/runs/<run_id>', methods=['DELETE'])
def delete_run(run_id):
    """Delete a saved run."""
    try:
        import shutil
        run_dir = os.path.join('results', 'runs', run_id)
        
        if not os.path.exists(run_dir):
            return jsonify({'success': False, 'error': 'Run not found'}), 404
        
        # Delete the entire run directory
        shutil.rmtree(run_dir)
        print(f"Deleted run: {run_id}")
        
        return jsonify({'success': True, 'run_id': run_id}), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/runs/save', methods=['POST'])
def save_current_state_as_run():
    """Save current APP_STATE as a new run with a provided name."""
    try:
        payload = request.get_json(force=True, silent=False) or {}
        name = payload.get('name') or f"Run {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        base_run_id = payload.get('base_run_id')
        
        print(f"\n=== SAVE RUN DEBUG ===")
        print(f"Saving run with name: {name}")
        print(f"Payload csv_filepath: {payload.get('csv_filepath')}")
        print(f"APP_STATE csv_filepath: {APP_STATE.get('csv_filepath')}")
        print(f"APP_STATE map_path: {APP_STATE.get('map_path')}")
        print(f"APP_STATE routes: {len(APP_STATE.get('routes', [])) if APP_STATE.get('routes') else 0} routes")
        
        # Include map_path and csv_filepath from APP_STATE if available
        meta = {
            'base_run_id': base_run_id,
            'created_at': datetime.now().isoformat(),
            'original_used_vendors': APP_STATE.get('original_used_vendors'),
            'map_path': APP_STATE.get('map_path'),  # Include map for copying
            'csv_filepath': payload.get('csv_filepath') or APP_STATE.get('csv_filepath'),  # From frontend or APP_STATE
        }
        
        print(f"Metadata map_path: {meta['map_path']}")
        print(f"Metadata csv_filepath: {meta['csv_filepath']}")
        
        run_id = generate_run_id('run')
        res = save_run(run_id, name, APP_STATE, meta)
        
        print(f"Save result: {res}")
        print(f"=== END SAVE RUN DEBUG ===\n")
        
        return jsonify({'success': True, 'run_id': run_id, 'name': name}), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/runs/download-input/<run_id>', methods=['GET'])
def download_run_input(run_id):
    """Return input CSV for a run, with an added running_status column."""
    try:
        data = load_run(run_id)
        if not data.get('success'):
            return jsonify(data), 404
        meta = data['metadata']
        state = data['state']
        input_csv = meta.get('input_csv_path')
        if not input_csv or not os.path.exists(input_csv):
            return jsonify({'success': False, 'error': 'No input CSV found for run'}), 404
        df = pd.read_csv(input_csv)
        
        print(f"\n=== DOWNLOAD CSV DEBUG ({run_id}) ===")
        print(f"Total rows in CSV: {len(df)}")
        
        # Build statuses: use THIS run's routes, not APP_STATE
        orig_set = set(meta.get('original_used_vendors') or [])
        print(f"Original used vendors: {sorted(orig_set)}")
        
        # Get vendors from THIS run's routes
        run_routes = state.get('routes', [])
        cur_set = set()
        if run_routes:
            for r in run_routes:
                for n in r:
                    if n != 0:
                        cur_set.add(int(n))
        
        print(f"Vendors in current routes: {sorted(cur_set)}")
        
        # Mark all vendors based on their status in THIS run
        # For a fresh optimization, original_used_vendors should match cur_set
        # Only differences indicate manual edits
        
        if not orig_set:
            # No original tracking - mark all as original
            print("No original_used_vendors found - marking all as original")
            df['running_status'] = 'original'
        elif not cur_set:
            # No routes - mark all as original
            print("No routes found - marking all as original")
            df['running_status'] = 'original'
        else:
            # Compare original vs current
            # Node IDs start from 1 for vendors (0 is depot)
            # But they correspond to row indices in the dataframe
            df['running_status'] = df.index.map(lambda idx: 
                'removed' if (idx + 1) in orig_set and (idx + 1) not in cur_set else
                'added' if (idx + 1) in cur_set and (idx + 1) not in orig_set else
                'original' if (idx + 1) in orig_set and (idx + 1) in cur_set else
                'original'  # Default for rows not in routes
            )
        
        print(f"Status distribution: {df['running_status'].value_counts().to_dict()}")
        print(f"=== END DOWNLOAD CSV DEBUG ===\n")
        
        # Serve as CSV with run_id in filename
        out_path = os.path.join('results', 'runs', run_id, 'input_with_status.csv')
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        df.to_csv(out_path, index=False)
        
        # Custom filename for download
        download_filename = f"{run_id}_input.csv"
        return send_from_directory(
            os.path.dirname(out_path), 
            os.path.basename(out_path), 
            as_attachment=True,
            download_name=download_filename
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/runs/rerun', methods=['POST'])
def rerun_from_dataset():
    """Re-run optimization using a saved run's dataset and (optionally) new parameters."""
    try:
        payload = request.get_json(force=True, silent=False) or {}
        run_id = payload.get('run_id')
        if not run_id:
            return jsonify({'success': False, 'error': 'run_id required'}), 400
        data = load_run(run_id)
        if not data.get('success'):
            return jsonify(data), 404
        meta = data['metadata']
        csv_path = meta.get('input_csv_path') or meta.get('csv_filepath')
        if not csv_path or not os.path.exists(csv_path):
            return jsonify({'success': False, 'error': 'dataset not found for run'}), 404
        # Forward to optimize endpoint so the same logic runs and a new run is saved
        params = payload.get('parameters', {})
        optimize_url = request.host_url.rstrip('/') + '/api/optimize'
        resp = requests.post(optimize_url, json={
            'vendors': [],
            'parameters': params,
            'csv_filepath': csv_path,
        })
        if resp.status_code != 200:
            try:
                return jsonify(resp.json()), resp.status_code
            except Exception:
                return jsonify({'success': False, 'error': 'Optimize call failed', 'status_code': resp.status_code}), 500
        result = resp.json()
        return jsonify({'success': True, 'result': result, 'new_run_id': result.get('run_id')}), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """API health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat()
    })


if __name__ == '__main__':
    # Truncate flask_app.log on each restart to keep logs fresh
    try:
        log_path = '/tmp/flask_app.log'
        # Ensure file exists, then truncate
        open(log_path, 'w').close()
    except Exception as _:
        # Non-fatal: if we can't truncate, continue startup
        pass

    print("🚀 Starting Parcel Delivery Optimizer Server...")
    print("📍 Open http://localhost:8080 in your browser")
    # Disable debug reloader for stable single-process listening
    app.run(debug=False, use_reloader=False, host='0.0.0.0', port=8080, threaded=True)
