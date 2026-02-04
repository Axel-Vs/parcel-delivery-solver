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
from datetime import datetime, timedelta
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

# ---------- Time window grouping ----------
def _compute_time_groups(df, earl_arv_hours, late_arv_hours):
    """Group requests by overlapping time windows (transitive overlap)."""
    # Prefer normalized columns, fall back to raw if needed
    loading_col = 'Requested Loading' if 'Requested Loading' in df.columns else 'Requested Loading Date'
    delivery_col = 'Requested Delivery' if 'Requested Delivery' in df.columns else 'Requested Delivery Date'

    loading_dt = pd.to_datetime(df.get(loading_col), errors='coerce', utc=True)
    delivery_dt = pd.to_datetime(df.get(delivery_col), errors='coerce', utc=True)
    delivery_dt = delivery_dt.fillna(loading_dt)

    start_dt = loading_dt - pd.to_timedelta(earl_arv_hours, unit='h')
    end_dt = delivery_dt + pd.to_timedelta(late_arv_hours, unit='h')

    entries = []
    for idx in df.index:
        s = start_dt.loc[idx]
        e = end_dt.loc[idx]
        if pd.isna(s) or pd.isna(e):
            entries.append((idx, None, None))
        else:
            entries.append((idx, s.to_pydatetime(), e.to_pydatetime()))

    # Sort valid entries by start time
    valid_entries = [(idx, s, e) for idx, s, e in entries if s is not None and e is not None]
    valid_entries.sort(key=lambda x: x[1])

    group_ids = {}
    groups = []
    group_counter = 1
    current_start = None
    current_end = None
    current_members = []

    def _close_group():
        nonlocal group_counter, current_start, current_end, current_members
        if current_start is None:
            return
        group_id = f"G{group_counter}"
        for idx in current_members:
            group_ids[idx] = group_id
        groups.append({
            'group_id': group_id,
            'min_start': current_start.strftime('%Y-%m-%d %H:%M:%S'),
            'max_end': current_end.strftime('%Y-%m-%d %H:%M:%S'),
        })
        group_counter += 1
        current_start = None
        current_end = None
        current_members = []

    for idx, s, e in valid_entries:
        if current_start is None:
            current_start, current_end = s, e
            current_members = [idx]
            continue
        if s <= current_end:
            current_end = max(current_end, e)
            current_members.append(idx)
        else:
            _close_group()
            current_start, current_end = s, e
            current_members = [idx]

    _close_group()

    # Assign missing-date rows to their own group
    for idx, s, e in entries:
        if s is None or e is None:
            group_id = f"UNGROUPED-{idx}"
            group_ids[idx] = group_id
            groups.append({
                'group_id': group_id,
                'min_start': None,
                'max_end': None,
            })

    # Return a series aligned with df
    group_series = pd.Series([group_ids.get(idx, '') for idx in df.index], index=df.index)
    return group_series, groups

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
    'depots_df': None,
    'depot_node_ids': None,
    'vendor_node_ids': None,
    'vendor_depot_map': None,
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
        preprocessing_warnings = []
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
                'volume': float(row.get('Vendor Volume in m3', row.get('Vendor Linear Length', row.get('Calculated Loading Meters', row.get('Vendor Loading Meters', row.get('Vendor Dimensions in m3', 0)))))),
                'delivery_date': str(row.get('Requested Delivery', row.get('Requested Delivery Date', '')))
            })
        
        # Calculate dynamic parameters based on data
        calculated_max_driving = None
        calculated_max_weight = None
        calculated_max_volume = None
        calculated_max_linear_length = None
        
        try:
            # Estimate based on vendor count and geographic spread
            vendor_count = len(df)
            if vendor_count > 0:
                # Trivial solution: one vehicle per vendor
                # Each vehicle: depot → vendor (one-way) + service time
                # Max driving time = max(depot_to_vendor_distance) + service_time + buffer
                # This is the MINIMUM guaranteed max_driving needed for ANY solution
                
                service_time_per_stop = 2.0  # hours
                
                # Geographic estimates based on vendor count/spread (one-way distance)
                # Assumption: more vendors = larger geographic area
                if vendor_count <= 5:
                    # Local area - farthest vendor maybe 30-60 min away (one-way)
                    estimated_max_travel = 1.0  # hours
                elif vendor_count <= 10:
                    # Regional - farthest vendor maybe 2-4 hours away (one-way)
                    estimated_max_travel = 4.0  # hours
                elif vendor_count <= 20:
                    # Multi-city - farthest vendor maybe 8-12 hours away (one-way)
                    estimated_max_travel = 12.0  # hours
                elif vendor_count <= 50:
                    # Multi-state - farthest vendor maybe 20-30 hours away (one-way, e.g. Seattle to Chicago)
                    estimated_max_travel = 30.0  # hours
                else:
                    # Cross-country - farthest vendor ~60-65 hours away (one-way, e.g. Seattle to Miami)
                    estimated_max_travel = 65.0  # hours
                
                # Calculate minimum max_driving needed for trivial solution:
                # = one-way travel to farthest vendor + service_time + buffer
                calculated_max_driving = estimated_max_travel + service_time_per_stop + 2.0
                print(f"✅ Estimated min max_driving: {calculated_max_driving:.1f}h for {vendor_count} vendors (trivial solution: travel {estimated_max_travel:.1f}h + service {service_time_per_stop:.1f}h + 2h buffer)")
                
                # Calculate max_weight based on heaviest vendor (with 20% safety margin)
                weight_col = None
                for col in ['Total Gross Weight', 'Vendor Gross Weight', 'weight']:
                    if col in df.columns:
                        weight_col = col
                        break
                
                if weight_col:
                    max_vendor_weight_kg = df[weight_col].fillna(0).max()
                    calculated_max_weight = max(30, max_vendor_weight_kg * 1.2)  # Min 30kg, plus 20% buffer
                    print(f"✅ Calculated max_weight: {calculated_max_weight:.0f}kg (max vendor: {max_vendor_weight_kg:.0f}kg + 20%)")
                
                # Calculate max_volume based on largest vendor volume (with 20% safety margin)
                ldms_col = None
                for col in ['Vendor Volume in m3', 'Vendor Linear Length', 'Calculated Loading Meters', 'Vendor Loading Meters', 'Vendor Dimensions in m3', 'volume']:
                    if col in df.columns:
                        ldms_col = col
                        break
                
                if ldms_col:
                    max_vendor_ldms = df[ldms_col].fillna(0).max()
                    calculated_max_volume = max(90, max_vendor_ldms * 1.2)  # Min 90m³, plus 20% buffer
                    print(f"✅ Calculated max_volume: {calculated_max_volume:.1f}m³ (max vendor: {max_vendor_ldms:.1f}m³ + 20%)")
                
                # Calculate max_linear_length based on largest vendor linear length (with 20% safety margin)
                linear_col = None
                for col in ['Vendor Linear Length', 'volume']:
                    if col in df.columns:
                        linear_col = col
                        break
                
                if linear_col:
                    max_vendor_linear = df[linear_col].fillna(0).max()
                    calculated_max_linear_length = max(16.1, max_vendor_linear * 1.2)  # Min 16.1m, plus 20% buffer
                    print(f"✅ Calculated max_linear_length: {calculated_max_linear_length:.1f}m (max vendor: {max_vendor_linear:.1f}m + 20%)")
        
        except Exception as e:
            print(f"⚠️ Could not estimate parameters: {e}")
            # Continue without calculation - optimizer will use defaults
        
        response_data = {
            'success': True,
            'vendors': vendors,
            'count': len(vendors),
            'filepath': filepath
        }
        
        # Include calculated parameters if available
        if calculated_max_driving is not None:
            response_data['calculated_max_driving'] = round(calculated_max_driving, 1)
        if calculated_max_weight is not None:
            response_data['calculated_max_weight'] = round(calculated_max_weight, 0)
        if calculated_max_volume is not None:
            response_data['calculated_max_volume'] = round(calculated_max_volume, 1)
        if calculated_max_linear_length is not None:
            response_data['calculated_max_linear_length'] = round(calculated_max_linear_length, 1)
        
        response_data['preprocessing_warnings'] = preprocessing_warnings
        # Optional grouping preview with default tolerances
        try:
            preview_groups = []
            df_preview = pd.read_csv(filepath)
            if 'Requested Loading Date' in df_preview.columns or 'Requested Delivery Date' in df_preview.columns:
                group_series, preview_groups = _compute_time_groups(df_preview, 24, 24)
                response_data['time_groups'] = preview_groups
                logging.info(
                    "🧩 Upload time-window groups (%s): %s",
                    len(preview_groups),
                    ", ".join([f"{g['group_id']}[{g['min_start']}..{g['max_end']}]" for g in preview_groups if g.get('min_start')])
                )
        except Exception:
            pass
        return jsonify(response_data)
    
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
        # Allow auto-selection of solver based on vendor count if not explicitly provided
        use_metaheuristic = params.get('use_metaheuristic', None)
        max_vehicles = len(vendors_data)  # Always use all vendors as max vehicles
        period = None  # Initialize period at top of function
        
        # Read the original CSV file to preserve all columns for geocoding
        if csv_filepath and os.path.exists(csv_filepath):
            print(f"Using CSV file: {csv_filepath}")
            df_raw = pd.read_csv(csv_filepath)
            
            # Calculate period from RAW dates using input buffers
            # Period Start = MIN(Requested Loading Date) - earl_arv hours
            # Period End = MAX(Requested Delivery Date) + late_arv hours
            period = None
            loading_min = None
            delivery_max = None
            
            # Get earliest loading date (when vendors have packages ready)
            if 'Requested Loading Date' in df_raw.columns:
                loading_dates = pd.to_datetime(df_raw['Requested Loading Date'], errors='coerce').dropna()
                if len(loading_dates) > 0:
                    loading_min = loading_dates.min()
            
            # Get latest delivery date (when packages must reach depot)
            if 'Requested Delivery Date' in df_raw.columns:
                delivery_dates = pd.to_datetime(df_raw['Requested Delivery Date'], errors='coerce').dropna()
                if len(delivery_dates) > 0:
                    delivery_max = delivery_dates.max()
            
            if loading_min is not None and delivery_max is not None:
                # Apply input buffers
                buffer_early_hours = int(params.get('earl_arv', 12))
                buffer_late_hours = int(params.get('late_arv', 12))
                period_start = loading_min - timedelta(hours=buffer_early_hours)
                period_end = delivery_max + timedelta(hours=buffer_late_hours)
                period = [period_start, period_end]
                print(f"📅 Correct Tour Period (with ±{buffer_early_hours}h/{buffer_late_hours}h buffers):")
                print(f"   MIN(Requested Loading Date) - {buffer_early_hours}h = {period_start}")
                print(f"   MAX(Requested Delivery Date) + {buffer_late_hours}h = {period_end}")
                print(f"   Period: {period_start} to {period_end}")
            
                # Analyze the date distribution for debugging time window issues
                print(f"\n📊 DATE ANALYSIS FOR TIME WINDOW DEBUGGING:")
                print(f"   - Loading period: {loading_min} to {pd.to_datetime(df_raw['Requested Loading Date'], errors='coerce').dropna().max()}")
                print(f"   - Delivery period: {pd.to_datetime(df_raw['Requested Delivery Date'], errors='coerce').dropna().min()} to {delivery_max}")
                print(f"   - Total routing span: {(delivery_max - loading_min).days} days")
                print(f"   - With buffers: {(period_end - period_start).days} days")
            
            
            # Prepare dataframe similar to simulator.py preprocessing
            df = df_raw.copy()
            
            # Map vendor name
            if 'Vendor Name' in df.columns:
                df['vendor Name'] = df['Vendor Name'].astype(str)
            
            # Map weights and loading
            if 'Vendor Gross Weight' in df.columns:
                df['Total Gross Weight'] = df['Vendor Gross Weight']
            if 'Vendor Volume in m3' in df.columns:
                df['Calculated Loading Meters'] = df['Vendor Volume in m3']
            elif 'Vendor Linear Length' in df.columns:
                df['Calculated Loading Meters'] = df['Vendor Linear Length']
            elif 'Vendor Loading Meters' in df.columns:
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

            # Add time bucket based on overlapping time windows (transitive overlap)
            buffer_early_hours = int(params.get('earl_arv', 24))
            buffer_late_hours = int(params.get('late_arv', 24))
            time_group_series, time_groups = _compute_time_groups(df, buffer_early_hours, buffer_late_hours)
            df['time_bucket'] = time_group_series.fillna('')
            logging.info(
                "🧩 Time-window groups (%s): %s",
                len(time_groups),
                ", ".join([f"{g['group_id']}[{g['min_start']}..{g['max_end']}]" for g in time_groups if g.get('min_start')])
            )
            
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
                
                def _needs_geocoding(df_in, lat_col, lon_col):
                    if lat_col not in df_in.columns or lon_col not in df_in.columns:
                        return True
                    lat = pd.to_numeric(df_in[lat_col], errors='coerce')
                    lon = pd.to_numeric(df_in[lon_col], errors='coerce')
                    has_valid = ~(lat.isna() | lon.isna() | ((lat.abs() < 1e-6) & (lon.abs() < 1e-6)))
                    return not has_valid.any()

                # Determine which columns need geocoding (treat 0/NaN as missing)
                need_vendor_geocoding = _needs_geocoding(df, 'vendor_latitude', 'vendor_longitude')
                need_recipient_geocoding = _needs_geocoding(df, 'recipient_latitude', 'recipient_longitude')
                
                # Geocode all addresses
                for idx, row in df.iterrows():
                    if need_vendor_geocoding or pd.isna(row.get('vendor_latitude', None)) or pd.isna(row.get('vendor_longitude', None)) or (
                        float(row.get('vendor_latitude', 0.0) or 0.0) == 0.0 and float(row.get('vendor_longitude', 0.0) or 0.0) == 0.0
                    ):
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
                    
                    if need_recipient_geocoding or pd.isna(row.get('recipient_latitude', None)) or pd.isna(row.get('recipient_longitude', None)) or (
                        float(row.get('recipient_latitude', 0.0) or 0.0) == 0.0 and float(row.get('recipient_longitude', 0.0) or 0.0) == 0.0
                    ):
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
        
        # Create network parameters (max_driving will be calculated dynamically later)
        network_params = {
            'discretization_constant': 4,
            'starting_depot': params.get('starting_depot', 0),
            'closing_depot': params.get('closing_depot', 24),
            'vendor_start_hr': params.get('vendor_start_hr', 0),
            'pickup_end_hr': params.get('pickup_end_hr', 24),
            'loading': params.get('loading', 2),
            'earl_arv': params.get('earl_arv', 24),
            'late_arv': params.get('late_arv', 24),
            'max_driving': 50,  # Placeholder - will be recalculated dynamically
            'max_weight': params.get('max_weight', 30),
            'max_volume': params.get('max_volume', 90),
            'max_linear_length': params.get('max_linear_length', 16.1),
            'plot_centered_coordinates': [depot_lat, depot_lon],
            'max_feasible_distance': 3000,
            'time_window_sampling_threshold': 20,
            'time_window_sample_size': 20
        }
        print(f"🧭 Time window params: earl_arv={network_params['earl_arv']}h, late_arv={network_params['late_arv']}h")
        
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
            complete_coordinates, vendors_df, depots_df = net.read_data(period_str, df)
        except Exception as e:
            return jsonify({'error': f'Failed to read vendor data: {str(e)}'}), 500

        # Preprocessing warnings (rows filtered out before optimization)
        preprocessing_warnings = []
        try:
            period_start_dt = pd.to_datetime(period_str[0], errors='coerce')
            period_end_dt = pd.to_datetime(period_str[1], errors='coerce')
            if 'Requested Loading' in df.columns:
                loading_dt = pd.to_datetime(df['Requested Loading'], errors='coerce')
                in_period = (loading_dt >= period_start_dt) & (loading_dt <= period_end_dt)
                for idx, row in df[~in_period].iterrows():
                    vendor_name = str(row.get('vendor Name', row.get('Vendor Name', f'Vendor {idx + 1}'))).strip()
                    vendor_city = str(row.get('Vendor City', 'Unknown')).strip()
                    preprocessing_warnings.append({
                        'type': 'filtered_outside_period',
                        'vendor_name': vendor_name,
                        'city': vendor_city,
                        'detail': f"Requested Loading Date {row.get('Requested Loading')} is outside period [{period_str[0]} .. {period_str[1]}]"
                    })
            # Detect inconsistent loading/delivery ordering
            if 'Requested Loading' in df.columns and 'Requested Delivery' in df.columns:
                loading_dt = pd.to_datetime(df['Requested Loading'], errors='coerce')
                delivery_dt = pd.to_datetime(df['Requested Delivery'], errors='coerce')
                invalid_order = delivery_dt.notna() & loading_dt.notna() & (delivery_dt < loading_dt)
                for idx, row in df[invalid_order].iterrows():
                    vendor_name = str(row.get('vendor Name', row.get('Vendor Name', f'Vendor {idx + 1}'))).strip()
                    vendor_city = str(row.get('Vendor City', 'Unknown')).strip()
                    preprocessing_warnings.append({
                        'type': 'delivery_before_loading',
                        'vendor_name': vendor_name,
                        'city': vendor_city,
                        'detail': f"Requested Delivery Date {row.get('Requested Delivery')} is earlier than Requested Loading Date {row.get('Requested Loading')}"
                    })
        except Exception:
            pass
        
        vendor_count = len(vendors_df)
        print(f"Successfully loaded {vendor_count} vendors")

        depot_count = len(depots_df) if depots_df is not None else 0
        depot_node_ids = depots_df['node_id'].tolist() if depots_df is not None and 'node_id' in depots_df.columns else []
        vendor_node_ids = vendors_df['node_id'].tolist() if 'node_id' in vendors_df.columns else list(range(1, vendor_count + 1))
        vendor_depot_map = {}
        if 'depot_node_id' in vendors_df.columns:
            vendor_depot_map = {
                int(v_node): int(d_node)
                for v_node, d_node in zip(vendor_node_ids, vendors_df['depot_node_id'])
                if pd.notna(d_node)
            }


        # Load model-level parameters (for gap/time/thresholds)
        model_params_path = os.path.join('model', 'config', 'model_params.txt')
        model_params = {}
        try:
            if os.path.exists(model_params_path):
                import json
                with open(model_params_path, 'r') as f:
                    model_params = json.loads(f.read())
        except Exception as e:
            print(f"⚠️ Could not read model_params.txt: {e}")

        # Allow request to override model parameters (live configuration)
        # Priority: request params > model_params.txt > hardcoded defaults
        mip_time_limit = int(params.get('mip_time', model_params.get('max_time', 20)))
        alns_time_limit = int(params.get('alns_time', model_params.get('alns_time', 10)))
        mip_gap_value = float(params.get('gap_value', model_params.get('gap_value', 0.05)))
        vendor_threshold = int(params.get('vendor_threshold', model_params.get('vendor_threshold', 20)))
        max_iterations_alns = int(params.get('alns_iterations', model_params.get('alns_max_iterations', 2500)))
        
        print(f"📋 Configuration (request overrides model_params.txt):")
        print(f"   MIP time: {mip_time_limit} min (from: {'request' if 'mip_time' in params else 'model_params.txt' if 'max_time' in model_params else 'default'})")
        print(f"   ALNS time: {alns_time_limit} min (from: {'request' if 'alns_time' in params else 'model_params.txt' if 'alns_time' in model_params else 'default'})")
        print(f"   MIP gap: {mip_gap_value} (from: {'request' if 'gap_value' in params else 'model_params.txt' if 'gap_value' in model_params else 'default'})")
        print(f"   Solver threshold: {vendor_threshold} vendors (from: {'request' if 'vendor_threshold' in params else 'model_params.txt' if 'vendor_threshold' in model_params else 'default'})")

        # Auto-select solver if not specified in request
        if use_metaheuristic is None:
            use_metaheuristic = vendor_count >= vendor_threshold
            print(f"Solver auto-selected: {'ALNS' if use_metaheuristic else 'MIP'} (threshold={vendor_threshold})")
        
        # Create network
        net.create_network(complete_coordinates, vendors_df)
        
        # Discretize the network (creates disc_time_distance_matrix)
        net.discretize()
        
        # Calculate dynamic max_driving based on actual travel times
        # Always calculate, then decide whether to use it
        calculated_max_driving = None
        if net.time_distance_matrix is not None and len(net.time_distance_matrix) > 1:
            # Get max time from any vendor to its delivery depot (node indices in matrix)
            vendor_to_depot_times = []
            for v_node in vendor_node_ids:
                depot_node = vendor_depot_map.get(int(v_node))
                if depot_node is None:
                    continue
                if v_node < len(net.time_distance_matrix) and depot_node < len(net.time_distance_matrix):
                    vendor_to_depot_times.append(net.time_distance_matrix[v_node][depot_node])
            if vendor_to_depot_times:
                max_vendor_depot_time_hours = max(vendor_to_depot_times) / 3600.0
                # Add service time per stop + safety buffer
                service_time_hours = (params.get('service_time_minutes', 120)) / 60.0

                # Estimate multi-stop route requirement to avoid over-splitting
                vendor_count = len(vendor_node_ids)
                vendor_to_vendor_times = []
                for i in vendor_node_ids:
                    for j in vendor_node_ids:
                        if i != j:
                            vendor_to_vendor_times.append(net.time_distance_matrix[i][j])
                max_vendor_to_vendor_hours = (max(vendor_to_vendor_times) / 3600.0) if vendor_to_vendor_times else 0.0

                # Target at least 2 routes for small datasets; estimate stops per route
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
                    estimated_multi_stop
                )
                
                # Use calculated value (user can still override by changing the form from default 67)
                user_max_driving = params.get('max_driving', None)
                
                # If user-provided max_driving is too low, auto-raise to minimum
                if user_max_driving is not None and user_max_driving < calculated_max_driving:
                    print(
                        f"⚠️  max_driving ({user_max_driving:.1f}h) below minimum "
                        f"({calculated_max_driving:.1f}h). Auto-raising to minimum."
                    )
                    user_max_driving = calculated_max_driving
                
                if user_max_driving is None or user_max_driving == 67:  # 67 is the new UI default
                    network_params['max_driving'] = calculated_max_driving
                    print(f"✅ Max driving time set: {calculated_max_driving:.1f}h (farthest vendor: {max_vendor_depot_time_hours:.1f}h travel + {service_time_hours:.1f}h service + 2h buffer)")
                else:
                    network_params['max_driving'] = user_max_driving
                    print(f"📋 Using user-specified max driving: {user_max_driving:.1f}h (calculated minimum: {calculated_max_driving:.1f}h)")
        
        # Extract matrices and parameters
        # Node layout: 0 = dummy start, 1..D = depots, D+1..D+N = vendors
        matrix_size = 1 + depot_count + vendor_count
        capacity_matrix = np.zeros(matrix_size, dtype=float)
        loading_matrix = np.zeros(matrix_size, dtype=float)
        for _, row in vendors_df.iterrows():
            v_node = int(row.get('node_id', 0))
            if v_node <= 0 or v_node >= matrix_size:
                continue
            capacity_matrix[v_node] = float(row.get('Total Gross Weight', 0) or 0)
            loading_matrix[v_node] = float(row.get('Calculated Loading Meters', 0) or 0)
        
        # Service time: configurable per request, default 2 hours per stop (120 minutes)
        # This is added to travel time; total_time = travel_time + (num_stops × service_time)
        # max_driving enforces: total_time ≤ max_driving_hours
        DEFAULT_SERVICE_TIME_MINUTES = 2 * 60  # 2 hours × 60 = 120 minutes per stop
        service_time_minutes = params.get('service_time_minutes', DEFAULT_SERVICE_TIME_MINUTES)
        service_time_matrix = np.zeros(len(capacity_matrix))
        for v_node in vendor_node_ids:
            if v_node < len(service_time_matrix):
                service_time_matrix[v_node] = service_time_minutes
        print(f"⏱️  Service time per stop: {service_time_minutes:.0f} minutes ({service_time_minutes/60:.1f} hours)")
        print(f"📊 Max driving (total): {network_params['max_driving']:.1f} hours (travel + service combined)")
        
        # Debug: Check sample time values from matrix (after it's created)
        if net.time_distance_matrix is not None and len(vendor_node_ids) > 1:
            a = vendor_node_ids[0]
            b = vendor_node_ids[1]
            sample_time_sec = net.time_distance_matrix[a][b]
            sample_time_hr = sample_time_sec / 3600.0
            print(f"🔍 Debug: Sample time matrix value [{a}][{b}] = {sample_time_sec:.1f}s ({sample_time_hr:.2f}h)")
        
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
            print(f"✅ Time-expanded network: {len(time_expanded_network)} arcs")
            
            if len(time_expanded_network) == 0:
                return jsonify({'error': 'No feasible arcs in time-expanded network. This usually means time windows are too restrictive or there are no valid routes between vendors.'}), 500
        
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
            service_time_matrix=service_time_matrix,
            max_capacity=network_params['max_weight'],
            max_volume=network_params['max_volume'],
            max_linear_length=network_params['max_linear_length'],
            max_driving=network_params['max_driving'],
            allowed_early_hours=network_params.get('earl_arv', 12),
            allowed_late_hours=network_params.get('late_arv', 12),
            is_gap=not use_metaheuristic,
            # MIP gap: use live parameter (request > model_params > default)
            mip_gap=(
                0.01 if vendor_count <= 8 else
                0.03 if vendor_count <= 15 else
                mip_gap_value  # ← NOW LIVE FROM REQUEST
            ),
            # Time limit: use live parameters (request > model_params > default)
            maximum_minutes=mip_time_limit if not use_metaheuristic else alns_time_limit,
            vendors_df=vendors_df,
            depots_df=depots_df,
            depot_node_ids=depot_node_ids,
            vendor_node_ids=vendor_node_ids,
            vendor_depot_map=vendor_depot_map
        )
        optimizer.min_date = net.min_date
        print(f"📅 DEBUG: optimizer.min_date = {optimizer.min_date} (type: {type(optimizer.min_date).__name__})")
        
        # Run optimization with selected solver
        print(f"Starting optimization...")
        start_time = datetime.now()
        
        if use_metaheuristic:
            # Determine iteration budget based on problem size if not provided
            default_iters = (
                1200 if vendor_count < 20 else
                2000 if vendor_count < 30 else
                2500 if vendor_count < 60 else
                4000
            )
            # Allow override from request params or use model_params, else use default_iters
            alns_iterations = int(params.get('alns_iterations', 
                                   model_params.get('alns_max_iterations', default_iters)))
            print(f"🔄 ALNS iterations: {alns_iterations} (from: {'request' if 'alns_iterations' in params else 'model_params.txt' if 'alns_max_iterations' in model_params else 'default'})")
            
            status, x, y = optimizer.solve_with_metaheuristic(
                w=0.5,
                max_iterations=alns_iterations,
                verbose=True
            )
            solver_type = "ALNS Metaheuristic"
        else:
            optimizer.create_model(w=0.5)
            status, x, y = optimizer.solve_model()
            solver_type = "CBC MIP"

            # Auto-fallback: if the exact MIP is infeasible, switch to ALNS to try finding a feasible solution
            if status == 2:
                print("MIP solver reported infeasible (status=2). Falling back to ALNS metaheuristic...")
                default_iters = (
                    1200 if vendor_count < 20 else
                    2000 if vendor_count < 30 else
                    2500 if vendor_count < 60 else
                    4000
                )
                alns_iterations = int(params.get('alns_iterations', 
                                       model_params.get('alns_max_iterations', default_iters)))
                status, x, y = optimizer.solve_with_metaheuristic(
                    w=0.5,
                    max_iterations=alns_iterations,
                    verbose=True
                )
                solver_type = "ALNS Metaheuristic (fallback after MIP infeasible)"
        
        solving_time = (datetime.now() - start_time).total_seconds()
        
        # Check if solution was found
        if status not in (0, 1):
            violations = []
            violation_count = 0
            if hasattr(optimizer, 'last_constraint_violations'):
                violations = optimizer.last_constraint_violations or []
                violation_count = len(violations)
            osrm_failures = getattr(optimizer, 'osrm_failures', None)
            warning_text = f'Infeasible solution (status {status})'
            if violations:
                warning_text = f'{warning_text}: {violations[0]}'
            # Try to generate a map even for infeasible solutions
            map_path = None
            try:
                map_filename = f'routes_{datetime.now().strftime("%Y%m%d_%H%M%S")}.html'
                map_path = os.path.join('results/optimization', map_filename)
                os.makedirs('results/optimization', exist_ok=True)
                logging.info(f'🗺️  Starting map generation (infeasible) at {map_path}...')
                optimizer.plot_routes(x, y, show_plot=False, save_path=map_path)
                APP_STATE['map_path'] = map_path
                logging.info(f'✅ Map generated (infeasible) at {map_path}')
            except Exception as e:
                logging.error(f'❌ Error during plot_routes (infeasible): {e}', exc_info=True)
            return jsonify({
                'success': False,
                'status': status,
                'warning': warning_text,
                'constraint_violation_count': violation_count,
                'constraint_violations': violations[:50],
                'preprocessing_warnings': preprocessing_warnings,
                'time_groups': time_groups if 'time_groups' in locals() else [],
                'osrm_failures': osrm_failures[:50] if osrm_failures else [],
                'map_path': map_path
            }), 200
        if status == 1:
            logging.info('⚠️ Optimization returned feasible (status=1); proceeding with best found solution')
        
        # Generate map
        map_filename = f'routes_{datetime.now().strftime("%Y%m%d_%H%M%S")}.html'
        map_path = os.path.join('results/optimization', map_filename)
        os.makedirs('results/optimization', exist_ok=True)
        
        # Log before suppressing stdout
        logging.info(f'🗺️  Starting map generation via plot_routes at {map_path}...')
        
        # Plot routes using the optimizer's built-in method
        # Suppress stdout to prevent BrokenPipeError from excessive printing
        import sys
        from io import StringIO
        old_stdout = sys.stdout
        sys.stdout = StringIO()  # Redirect stdout
        try:
            _, route_stats = optimizer.plot_routes(x, y, show_plot=False, save_path=map_path)
        except Exception as e:
            logging.error(f'❌ Error during plot_routes: {e}', exc_info=True)
            raise
        finally:
            sys.stdout = old_stdout  # Restore stdout
            logging.info(f'✅ Map generated successfully at {map_path}')
        
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
        
        # DEBUG: Log segment data from first route
        if route_stats and len(route_stats) > 0:
            first_route = route_stats[0]
            if 'segments' in first_route and len(first_route['segments']) > 0:
                first_seg = first_route['segments'][0]
                print(f"🔍 DEBUG: First segment from route 0:")
                print(f"   from_id: {first_seg.get('from_id')}, to_id: {first_seg.get('to_id')}")
                print(f"   arrival_hours: {first_seg.get('arrival_hours')}")
                print(f"   arrival_time: {first_seg.get('arrival_time')}")
        
        # Total volume (m³) - only for INCLUDED vendors
        volume_series = None
        for vol_col in ['Vendor Dimensions in m3', 'Total Volume (cbm)', 'volume', 'Vendor Loading Meters', 'Calculated Loading Meters']:
            if vol_col in vendors_df.columns:
                volume_series = vendors_df[vol_col]
                break
        
        # Calculate total volume only for included vendors (1-indexed vendor IDs)
        if volume_series is not None:
            if 'node_id' in vendors_df.columns:
                included_rows = vendors_df[vendors_df['node_id'].isin(included_vendor_ids)]
                total_volume = float(included_rows[volume_series.name].fillna(0).sum()) if not included_rows.empty else 0.0
            else:
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
        
        # Build vendor name, city and country maps (needed for route summaries)
        vendor_name_map = {}
        vendor_city_map = {}
        vendor_country_map = {}
        vendor_date_map = {}
        depot_name_map = {}
        depot_city_map = {}
        depot_country_map = {}

        if depots_df is not None and len(depots_df) > 0:
            for _, row in depots_df.iterrows():
                node_id = int(row.get('node_id', 0))
                city = str(row.get('Recipient City', 'Depot')).strip()
                country = str(row.get('Recipient Country Name', 'USA')).strip()
                depot_city_map[node_id] = city
                depot_country_map[node_id] = country
                depot_name_map[node_id] = f"Depot, {city}, {country}"
        
        for idx, row in vendors_df.iterrows():
            vendor_id = int(row.get('node_id', idx))
            vendor_name_map[vendor_id] = str(row.get('vendor Name', row.get('Vendor Name', f'Vendor {vendor_id}'))).strip()
            
            # Extract city information
            city = str(row.get('Vendor City', 'Unknown')).strip() if pd.notna(row.get('Vendor City')) else 'Unknown'
            vendor_city_map[vendor_id] = city
            
            # Extract country information
            country = str(row.get('Vendor Country Name', 'USA')).strip() if pd.notna(row.get('Vendor Country Name')) else 'USA'
            vendor_country_map[vendor_id] = country
            
            raw_date = row.get('Requested Delivery', row.get('Requested Delivery Date', ''))
            parsed_date = pd.to_datetime(raw_date, errors='coerce')
            if pd.notna(parsed_date):
                vendor_date_map[vendor_id] = parsed_date.strftime('%Y-%m-%d')
        
        # Create detailed route summaries from statistics
        route_summaries = []
        print(f"\n📊 DEBUG: Building route summaries from {len(route_stats)} routes")
        print(f"📊 DEBUG: route_stats keys (vehicle_ids): {sorted(route_stats.keys())}")
        
        # Validate route_stats for duplicates
        vehicle_ids_sorted = sorted(route_stats.keys())
        if len(vehicle_ids_sorted) != len(set(vehicle_ids_sorted)):
            print(f"⚠️ WARNING: Duplicate vehicle IDs detected in route_stats!")
        
        for route_seq_num, vehicle_id in enumerate(vehicle_ids_sorted, start=1):
            stats = route_stats[vehicle_id]
            num_vendors = len(stats.get('vendors', []))
            total_dist = stats.get('total_distance', 0)
            num_segments = len(stats.get('segments', []))
            
            print(f"📊 DEBUG: Route {route_seq_num} (vehicle_id {vehicle_id}): {num_vendors} vendors, {total_dist:.0f} km, {num_segments} segments")
            if num_segments > 0:
                first_seg = stats.get('segments', [])[0]
                print(f"         First segment: {first_seg.get('from_id')} → {first_seg.get('to_id')}, distance={first_seg.get('distance', 0):.0f}km")
            
            # Add vendor names, cities and countries to segments
            segments_with_names = []
            raw_segments = stats.get('segments', [])
            print(f"📊 DEBUG: Processing {len(raw_segments)} raw segments for route {route_seq_num}")
            
            # Track cumulative cargo - what's currently on the truck
            cumulative_weight = 0.0
            cumulative_volume = 0.0
            prev_arrival_time = None

            # Route-level optimized start time (within vendor + depot windows)
            route_base_dt = None
            start_vendor_display = None
            start_vendor_requested = None
            start_cargo_kg = None
            start_volume_m3 = None
            try:
                first_vendor_id = stats.get('vendors', [None])[0]
                if first_vendor_id:
                    if 'node_id' in vendors_df.columns:
                        match = vendors_df[vendors_df['node_id'] == int(first_vendor_id)]
                        vendor_row = match.iloc[0] if not match.empty else None
                    else:
                        vendor_row = vendors_df.iloc[int(first_vendor_id) - 1]
                    start_vendor_name = vendor_name_map.get(int(first_vendor_id), f'Vendor {first_vendor_id}')
                    start_vendor_city = vendor_city_map.get(int(first_vendor_id), 'Unknown')
                    start_vendor_country = vendor_country_map.get(int(first_vendor_id), 'USA')
                    start_vendor_display = f"{start_vendor_name}, {start_vendor_city}, {start_vendor_country}"
                    raw_candidates = [
                        vendor_row.get('Requested Loading', '') if vendor_row is not None else '',
                        vendor_row.get('Requested Loading Date', '') if vendor_row is not None else '',
                        vendor_row.get('Requested Delivery', '') if vendor_row is not None else '',
                        vendor_row.get('Requested Delivery Date', '') if vendor_row is not None else '',
                    ]
                    first_vendor_requested = None
                    for raw_dt in raw_candidates:
                        parsed = pd.to_datetime(raw_dt, errors='coerce')
                        if pd.notna(parsed):
                            first_vendor_requested = parsed.to_pydatetime()
                            start_vendor_requested = parsed.to_pydatetime()
                            break

                    if int(first_vendor_id) < len(capacity_matrix):
                        start_cargo_kg = float(capacity_matrix[int(first_vendor_id)])
                    if int(first_vendor_id) < len(loading_matrix):
                        start_volume_m3 = float(loading_matrix[int(first_vendor_id)])

                    # Compute route duration (travel + service) to reach depot
                    route_duration_hours = 0.0
                    for seg in raw_segments:
                        route_duration_hours += float(seg.get('duration', 0) or 0)
                        if seg.get('to_id') not in depot_node_ids:
                            route_duration_hours += (service_time_minutes / 60.0)

                    # Depot target window from latest requested delivery in route
                    route_latest_delivery = None
                    for v_id in stats.get('vendors', []):
                        try:
                            if 'node_id' in vendors_df.columns:
                                match = vendors_df[vendors_df['node_id'] == int(v_id)]
                                v_row = match.iloc[0] if not match.empty else None
                            else:
                                v_row = vendors_df.iloc[int(v_id) - 1]
                            if v_row is None:
                                continue
                            raw_delivery_candidates = [
                                v_row.get('Requested Delivery', ''),
                                v_row.get('Requested Delivery Date', ''),
                            ]
                            for raw_delivery in raw_delivery_candidates:
                                parsed_delivery = pd.to_datetime(raw_delivery, errors='coerce')
                                if pd.notna(parsed_delivery):
                                    route_latest_delivery = parsed_delivery.to_pydatetime() if route_latest_delivery is None else max(route_latest_delivery, parsed_delivery.to_pydatetime())
                                    break
                        except Exception:
                            continue

                    if first_vendor_requested:
                        allowed_early_hours = float(params.get('earl_arv', 12))
                        allowed_late_hours = float(params.get('late_arv', 12))
                        vendor_window_start = first_vendor_requested - timedelta(hours=allowed_early_hours)
                        vendor_window_end = first_vendor_requested + timedelta(hours=allowed_late_hours)

                        if route_latest_delivery:
                            depot_window_start = route_latest_delivery - timedelta(hours=allowed_early_hours)
                            depot_window_end = route_latest_delivery + timedelta(hours=allowed_late_hours)
                        else:
                            depot_window_start = period[0] if period else None
                            depot_window_end = period[1] if period else None

                        if depot_window_start and depot_window_end:
                            depot_start_min = depot_window_start - timedelta(hours=route_duration_hours)
                            depot_start_max = depot_window_end - timedelta(hours=route_duration_hours)
                            start_min = max(vendor_window_start, depot_start_min)
                            start_max = min(vendor_window_end, depot_start_max)
                        else:
                            start_min = vendor_window_start
                            start_max = vendor_window_end

                        if start_min <= start_max:
                            if first_vendor_requested < start_min:
                                route_base_dt = start_min
                            elif first_vendor_requested > start_max:
                                route_base_dt = start_max
                            else:
                                route_base_dt = first_vendor_requested
                        else:
                            route_base_dt = vendor_window_start
            except Exception:
                route_base_dt = None
            
            for seg_idx, seg in enumerate(raw_segments):
                from_id = seg['from_id']
                to_id = seg['to_id']
                
                # Get vendor names, cities and countries (0 = Depot)
                from_is_depot = from_id in depot_node_ids
                from_name = depot_name_map.get(from_id, 'Depot') if from_is_depot else vendor_name_map.get(from_id, f'Vendor {from_id}')
                from_city = depot_city_map.get(from_id, 'Depot City') if from_is_depot else vendor_city_map.get(from_id, 'Unknown')
                from_country = depot_country_map.get(from_id, 'USA') if from_is_depot else vendor_country_map.get(from_id, 'USA')
                
                to_is_depot = to_id in depot_node_ids
                to_name = depot_name_map.get(to_id, 'Depot') if to_is_depot else vendor_name_map.get(to_id, f'Vendor {to_id}')
                to_city = depot_city_map.get(to_id, 'Depot City') if to_is_depot else vendor_city_map.get(to_id, 'Unknown')
                to_country = depot_country_map.get(to_id, 'USA') if to_is_depot else vendor_country_map.get(to_id, 'USA')
                
                # Format with city and country: "Vendor Name, City, Country → Destination, City, Country"
                from_display = f"{from_name}, {from_city}, {from_country}"
                to_display = f"{to_name}, {to_city}, {to_country}"
                
                # Check for duplicate vendor coordinates (0 distance between different vendors)
                segment_warning = None
                if seg['distance'] == 0 and seg['from_id'] != seg['to_id']:
                    segment_warning = "ℹ️ Multiple cargo pickups at same address"
                
                # Add per-segment service time at the destination vendor (skip depot)
                per_stop_service_hours = (service_time_minutes / 60.0) if not to_is_depot else 0.0
                
                # Cargo on this segment = what's currently on the truck BEFORE arriving at destination
                # Since segments shown always start from vendors (not depot), we track cumulative cargo
                if not from_is_depot:
                    # Leaving a vendor - pick up their cargo first if not already added
                    if cumulative_weight == 0.0 or (seg_idx > 0 and raw_segments[seg_idx-1]['to_id'] != from_id):
                        # First pickup OR this is a new vendor we're leaving from
                        cumulative_weight += float(capacity_matrix[from_id])
                        cumulative_volume += float(loading_matrix[from_id])
                
                # Show cumulative cargo being carried on this segment
                vendor_weight_kg = cumulative_weight
                vendor_volume_m3 = cumulative_volume
                
                # If arriving at a vendor (not depot), we'll pick up their cargo after this segment
                if not to_is_depot:
                    # Arriving at next vendor - will pick up their cargo for subsequent segments
                    cumulative_weight += float(capacity_matrix[to_id])
                    cumulative_volume += float(loading_matrix[to_id])

                # Distinguish between vendor pickup and depot delivery
                # For Vendor → Vendor: show vendor requested pickup time
                # For Vendor → Depot: show depot requested delivery time
                pickup_date = None
                requested_vendor_pickup = None
                requested_depot_delivery = None
                requested_vendor_pickup_from = None
                expected_arrival_vendor_from = None
                
                if not to_is_depot:
                    # Going TO a vendor - show vendor's requested pickup/loading time
                    try:
                        vendor_row = vendors_df[vendors_df['node_id'] == to_id].iloc[0] if 'node_id' in vendors_df.columns else vendors_df.iloc[to_id - 1]
                        raw_pickup_candidates = [
                            vendor_row.get('Requested Loading', ''),
                            vendor_row.get('Requested Loading Date', ''),
                            vendor_row.get('Requested Delivery', ''),
                            vendor_row.get('Requested Delivery Date', ''),
                        ]
                        for raw_pickup in raw_pickup_candidates:
                            parsed_pickup = pd.to_datetime(raw_pickup, errors='coerce')
                            if pd.notna(parsed_pickup):
                                pickup_date = parsed_pickup.strftime('%Y-%m-%d')
                                requested_vendor_pickup = parsed_pickup.strftime('%Y-%m-%d %H:%M:%S')
                                break
                    except Exception:
                        pass
                else:
                    # Going TO depot - show depot's requested delivery time
                    try:
                        vendor_row = vendors_df[vendors_df['node_id'] == from_id].iloc[0] if 'node_id' in vendors_df.columns else vendors_df.iloc[from_id - 1]
                        raw_delivery_candidates = [
                            vendor_row.get('Depot Requested Delivery', ''),
                            vendor_row.get('Depot Requested Delivery Date', ''),
                            vendor_row.get('Requested Delivery', ''),
                            vendor_row.get('Requested Delivery Date', ''),
                        ]
                        for raw_delivery in raw_delivery_candidates:
                            parsed_delivery = pd.to_datetime(raw_delivery, errors='coerce')
                            if pd.notna(parsed_delivery):
                                pickup_date = parsed_delivery.strftime('%Y-%m-%d')
                                requested_depot_delivery = parsed_delivery.strftime('%Y-%m-%d %H:%M:%S')
                                break
                    except Exception:
                        pass
                    # Also show vendor's requested loading and expected arrival (from previous segment)
                    try:
                        vendor_row = vendors_df[vendors_df['node_id'] == from_id].iloc[0] if 'node_id' in vendors_df.columns else vendors_df.iloc[from_id - 1]
                        raw_pickup_candidates = [
                            vendor_row.get('Requested Loading', ''),
                            vendor_row.get('Requested Loading Date', ''),
                            vendor_row.get('Requested Delivery', ''),
                            vendor_row.get('Requested Delivery Date', ''),
                        ]
                        for raw_pickup in raw_pickup_candidates:
                            parsed_pickup = pd.to_datetime(raw_pickup, errors='coerce')
                            if pd.notna(parsed_pickup):
                                requested_vendor_pickup_from = parsed_pickup.strftime('%Y-%m-%d %H:%M:%S')
                                break
                    except Exception:
                        pass
                    if prev_arrival_time:
                        expected_arrival_vendor_from = prev_arrival_time
                    elif route_base_dt is not None:
                        # For first-vendor start, use optimized route start time
                        expected_arrival_vendor_from = route_base_dt.strftime('%Y-%m-%d %H:%M:%S')

                seg_data = {
                    'from': from_display,
                    'to': to_display,
                    'distance': float(round(seg['distance'], 1)),
                    'duration': float(round(seg['duration'], 2)),
                    'duration_including_service': float(round(seg['duration'] + per_stop_service_hours, 2)),
                    'service_time_hours': float(round(per_stop_service_hours, 2)),
                    'cargo_kg': float(round(vendor_weight_kg, 2)),
                    'volume_m3': float(round(vendor_volume_m3, 2)),
                    'is_depot_destination': to_id in depot_node_ids,  # Flag to indicate if going to depot
                }
                if segment_warning:
                    seg_data['warning'] = segment_warning
                if pickup_date:
                    seg_data['pickup_date'] = pickup_date
                if requested_vendor_pickup:
                    seg_data['requested_vendor_pickup'] = requested_vendor_pickup
                if requested_depot_delivery:
                    seg_data['requested_depot_delivery'] = requested_depot_delivery
                if requested_vendor_pickup_from:
                    seg_data['requested_vendor_pickup_from'] = requested_vendor_pickup_from
                if expected_arrival_vendor_from:
                    seg_data['expected_arrival_vendor_from'] = expected_arrival_vendor_from
                if 'arrival_time' in seg and seg.get('arrival_time'):
                    seg_data['expected_arrival'] = seg.get('arrival_time')
                if 'arrival_hours' in seg and seg.get('arrival_hours') is not None:
                    seg_data['expected_arrival_hours'] = float(round(seg.get('arrival_hours'), 2))
                
                segments_with_names.append(seg_data)
                if seg.get('arrival_time'):
                    prev_arrival_time = seg.get('arrival_time')

            # Keep all segments even if they share the same location
            # (show each cargo request explicitly)

            travel_time_hours = sum(seg['duration'] for seg in stats.get('segments', []))
            service_time_hours = stats['num_vendors'] * (service_time_minutes / 60.0)
            total_time_hours = travel_time_hours + service_time_hours

            print(f'   Route {route_seq_num}: {stats["num_vendors"]} vendors, {stats["total_distance"]:.0f} km, {total_time_hours:.1f} hours (travel: {travel_time_hours:.1f}h + service: {service_time_hours:.1f}h)')
            print(f'   Route {route_seq_num}: segments_with_names has {len(segments_with_names)} items (after grouping same-location pickups)')

            # Calculate capacity utilization percentages
            max_weight_kg = float(network_params.get('max_weight', 5000)) * 1000.0  # Convert tons to kg
            max_volume_m3 = float(network_params.get('max_volume', 90))
            max_linear_length_m = float(network_params.get('max_linear_length', 16.1))
            max_driving_hours = float(network_params.get('max_driving', 69))
            
            weight_utilization = (stats['total_cargo'] / max_weight_kg * 100.0) if max_weight_kg > 0 else 0.0
            volume_utilization = (stats['total_loading'] / max_volume_m3 * 100.0) if max_volume_m3 > 0 else 0.0
            # Linear length - estimate based on volume (rough approximation: 1 m³ ≈ 0.2m length)
            estimated_linear_length = stats['total_loading'] * 0.18  # More conservative estimate
            linear_length_utilization = (estimated_linear_length / max_linear_length_m * 100.0) if max_linear_length_m > 0 else 0.0
            time_utilization = (total_time_hours / max_driving_hours * 100.0) if max_driving_hours > 0 else 0.0

            # Create new dict with fresh data (never reuse references)
            route_summary_dict = {
                'route_id': int(route_seq_num),
                'num_vendors': int(stats['num_vendors']),
                'distance': float(round(stats['total_distance'], 1)),
                'cargo': float(round(stats['total_cargo'] / 1000.0, 3)),  # tons
                'loading': float(round(stats['total_loading'], 2)),
                'total_time_hours': float(round(total_time_hours, 2)),
                'segments': list(segments_with_names),  # Make explicit copy
                'start_vendor': start_vendor_display,
                'start_requested_loading': start_vendor_requested.strftime('%Y-%m-%d %H:%M:%S') if start_vendor_requested else None,
                'start_expected_arrival': route_base_dt.strftime('%Y-%m-%d %H:%M:%S') if route_base_dt else None,
                'start_cargo_kg': float(round(start_cargo_kg, 2)) if start_cargo_kg is not None else None,
                'start_volume_m3': float(round(start_volume_m3, 2)) if start_volume_m3 is not None else None,
                # Utilization metrics
                'weight_utilization': float(round(weight_utilization, 1)),
                'volume_utilization': float(round(volume_utilization, 1)),
                'linear_length_utilization': float(round(linear_length_utilization, 1)),
                'time_utilization': float(round(time_utilization, 1)),
                'max_weight_kg': float(round(max_weight_kg, 1)),
                'max_volume_m3': float(round(max_volume_m3, 1)),
                'max_linear_length_m': float(round(max_linear_length_m, 1)),
                'max_driving_hours': float(round(max_driving_hours, 1)),
            }

            print(f'   Route {route_seq_num}: route_summary has {len(route_summary_dict["segments"])} segments')

            route_summaries.append(route_summary_dict)

        # Build filter metadata (vendor names, delivery dates, route-vendor mappings, week options)
        vendor_routes_map = {}
        route_vendors_map = {}
        week_options = []

        try:
            # Prepare vendor name and delivery date lookup (1-indexed)
            index_min = int(vendors_df.index.min()) if len(vendors_df.index) > 0 else 0
            index_max = int(vendors_df.index.max()) if len(vendors_df.index) > 0 else 0
            use_df_index = index_min == 1 and index_max == len(vendors_df)
            for idx, row in vendors_df.iterrows():
                vendor_id = int(idx) if use_df_index else int(idx) + 1
                vendor_name_map[vendor_id] = str(row.get('vendor Name', row.get('Vendor Name', f'Vendor {vendor_id}')))

                raw_date = row.get('Requested Delivery', row.get('Requested Delivery Date', ''))
                parsed_date = pd.to_datetime(raw_date, errors='coerce')
                if pd.notna(parsed_date):
                    vendor_date_map[vendor_id] = parsed_date.strftime('%Y-%m-%d')

            # Map routes to vendors (and vendors to routes for fast lookup)
            for route_seq_num, vehicle_id in enumerate(sorted(route_stats.keys()), start=1):
                vendors_seq = route_stats[vehicle_id].get('vendors', [])
                route_number = int(route_seq_num)
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
        
        all_vendor_ids = set(vendor_node_ids)
        excluded_vendor_ids = sorted(list(all_vendor_ids - included_vendor_ids))
        
        excluded_vendors = []
        for vendor_id in excluded_vendor_ids:
            if 'node_id' in vendors_df.columns:
                match = vendors_df[vendors_df['node_id'] == int(vendor_id)]
                vendor_row = match.iloc[0] if not match.empty else None
            else:
                vendor_row = vendors_df.iloc[vendor_id - 1]
            if vendor_row is None:
                continue
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
            
            max_volume = network_params['max_volume']
            if loading_meters > max_volume:
                reasons.append(f'Volume exceeds capacity ({loading_meters:.1f} m³ > {max_volume:.1f} m³)')
            
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
                route_path = route_stats[vehicle_id].get('route_path', [])
                if route_path:
                    cached_routes.append(list(route_path))
                else:
                    vendors_seq = route_stats[vehicle_id].get('vendors', [])
                    cached_routes.append([0] + vendors_seq)

            APP_STATE['routes'] = cached_routes
            # Convert numpy arrays to plain lists and ensure depot padding
            APP_STATE['capacity_matrix'] = list(np.array(capacity_matrix, dtype=float))
            APP_STATE['loading_matrix'] = list(np.array(loading_matrix, dtype=float))
            # Distance matrix from graph (convert to list of lists if numpy)
            dm = net.distance_matrix
            APP_STATE['distance_matrix'] = dm.tolist() if hasattr(dm, 'tolist') else dm
            # Store time matrix (seconds)
            APP_STATE['time_matrix'] = net.time_distance_matrix.tolist() if hasattr(net.time_distance_matrix, 'tolist') else net.time_distance_matrix
            # Store capacities
            APP_STATE['max_capacity_kg'] = float(network_params['max_weight'] * 1000.0)
            APP_STATE['max_volume'] = float(network_params['max_volume'])
            APP_STATE['max_linear_length'] = float(network_params['max_linear_length'])
            # Store simple time windows based on requested delivery ±allowed hours
            APP_STATE['min_date'] = str(net.min_date) if hasattr(net, 'min_date') else str(period[0])
            # Build earliest/latest arrays aligned to node index (0=dummy start)
            earliest = [None] * len(capacity_matrix)
            latest = [None] * len(capacity_matrix)
            base = pd.to_datetime(net.min_date) if hasattr(net, 'min_date') else pd.to_datetime(period[0])
            allowed_early_hours = float(network_params.get('earl_arv', 12))
            allowed_late_hours = float(network_params.get('late_arv', 12))
            for _, row in vendors_df.iterrows():
                node_id = int(row.get('node_id', 0))
                ts = pd.to_datetime(row.get('Requested Delivery', None), errors='coerce')
                if pd.isna(ts):
                    continue
                offset = (ts - base).total_seconds()
                # window ±allowed hours
                if 0 <= node_id < len(earliest):
                    earliest[node_id] = max(0.0, offset - allowed_early_hours * 3600)
                    latest[node_id] = offset + allowed_late_hours * 3600
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
            APP_STATE['depots_df'] = depots_df.to_dict(orient='records') if depots_df is not None else []
            APP_STATE['depot_node_ids'] = depot_node_ids
            APP_STATE['vendor_node_ids'] = vendor_node_ids
            APP_STATE['vendor_depot_map'] = vendor_depot_map
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
            'preprocessing_warnings': preprocessing_warnings,
            'time_groups': time_groups if 'time_groups' in locals() else [],
            'osrm_failures': getattr(optimizer, 'osrm_failures', [])[:50],
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
    - max_volume: vehicle volume capacity (m³)
    - max_linear_length: vehicle linear length capacity (m)
    - frozen_prefix (optional): list of ints per route indicating immutable prefix length
    - allow_new_route (optional): bool
    """
    try:
        payload = request.get_json(force=True, silent=False) or {}
        required = [
            'routes', 'new_stop', 'distance_matrix', 'capacity_matrix',
            'loading_matrix', 'max_capacity_kg', 'max_volume', 'max_linear_length'
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
        max_volume = float(payload['max_volume'])
        max_linear_length = float(payload['max_linear_length'])
        frozen_prefix = payload.get('frozen_prefix')
        allow_new_route = bool(payload.get('allow_new_route', True))

        result = insert_stop_best_position(
            routes=routes,
            new_stop=new_stop,
            distance_matrix=distance_matrix,
            capacity_matrix=capacity_matrix,
            loading_matrix=loading_matrix,
            max_capacity_kg=max_capacity_kg,
            max_volume=max_volume,
            max_linear_length=max_linear_length,
            frozen_prefix=frozen_prefix,
            allow_new_route=allow_new_route,
            depot_node_ids=APP_STATE.get('depot_node_ids')
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
        result = remove_stop(routes=routes, stop=stop, depot_node_ids=APP_STATE.get('depot_node_ids'))
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
            max_volume=APP_STATE.get('max_volume', 90.0),
            max_linear_length=APP_STATE.get('max_linear_length', 16.1),
            frozen_prefix=frozen_prefix,
            allow_new_route=allow_new_route,
            depot_node_ids=APP_STATE.get('depot_node_ids'),
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

        result = remove_stop(routes=APP_STATE['routes'], stop=stop, depot_node_ids=APP_STATE.get('depot_node_ids'))
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
    # Bind to localhost to support both IPv4 (127.0.0.1) and IPv6 (::1)
    app.run(debug=False, use_reloader=False, host='localhost', port=8080, threaded=True)
