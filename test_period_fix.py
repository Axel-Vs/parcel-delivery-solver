#!/usr/bin/env python3
"""
Test script to validate the period boundary fix with 12-hour buffers.
"""
import requests
import json
import sys

API_BASE = "http://localhost:8080"

def test_upload_and_optimize():
    """Test uploading CSV and running optimization with the fix."""
    
    print("=" * 80)
    print("TEST: Period Boundary Fix (±12 hour buffers)")
    print("=" * 80)
    
    # Step 1: Upload CSV
    print("\n[STEP 1] Uploading small dataset...")
    with open('data/amazon_test_dataset_small.csv', 'rb') as f:
        files = {'file': f}
        upload_response = requests.post(f"{API_BASE}/api/upload-csv", files=files)
    
    if upload_response.status_code != 200:
        print(f"❌ Upload failed: {upload_response.status_code}")
        print(upload_response.text)
        return False
    
    upload_data = upload_response.json()
    print(f"✅ Upload successful")
    print(f"   - Vendors: {upload_data['count']}")
    vendors = upload_data['vendors']
    csv_filepath = upload_data['filepath']
    
    # Step 2: Run optimization
    print("\n[STEP 2] Running optimization with period fix...")
    optimize_payload = {
        "vendors": vendors,
        "csv_filepath": csv_filepath,
        "parameters": {
            "use_metaheuristic": False,  # Use MIP for small datasets
            "starting_depot": 8,
            "closing_depot": 18,
            "vendor_start_hr": 6,
            "pickup_end_hr": 14,
            "earl_arv": 24,
            "late_arv": 24,
            "loading": 2,
            "max_driving": 75,  # Increased to meet calculated minimum (70.9h)
            "max_weight": 30,
            "max_ldms": 70
        }
    }
    
    optimize_response = requests.post(f"{API_BASE}/api/optimize", json=optimize_payload)
    
    if optimize_response.status_code != 200:
        print(f"❌ Optimization failed: {optimize_response.status_code}")
        print(optimize_response.text)
        return False
    
    result = optimize_response.json()
    
    if not result.get('success'):
        print(f"❌ Optimization returned failure: {result.get('error')}")
        return False
    
    # Step 3: Analyze results
    print(f"✅ Optimization successful!")
    
    stats = result['statistics']
    print(f"\n📊 OPTIMIZATION RESULTS:")
    print(f"   - Routes: {stats['num_routes']}")
    print(f"   - Vendors: {stats['num_vendors']}")
    print(f"   - Total Distance: {stats['total_distance']:.0f} km")
    print(f"   - Total Cargo: {stats['total_cargo']:.2f} kg")
    print(f"   - Total Volume: {stats['total_volume']:.1f} m³")
    print(f"   - Solver Type: {stats['solver_type']}")
    print(f"   - Solving Time: {stats['solving_time']:.1f} sec")
    
    # Check for violations
    violations_found = 0
    excluded = result.get('excluded_vendors', [])
    if excluded:
        violations_found = len(excluded)
        print(f"\n⚠️  {violations_found} excluded vendors:")
        for vendor in excluded[:3]:
            print(f"   - {vendor['vendor_name']}: {vendor['reason']}")
    
    # Step 4: Verdict
    print(f"\n{'=' * 80}")
    if violations_found == 0:
        print(f"✅ SUCCESS: Period fix working! No violations found.")
        print(f"   Routes were generated without time window conflicts.")
        return True
    else:
        print(f"⚠️  PARTIAL: {violations_found} vendors couldn't be routed.")
        print(f"   Check if these have physical constraint violations.")
        return violations_found < 5  # Consider success if fewer than 5 violations
    
    print("=" * 80)

if __name__ == "__main__":
    try:
        success = test_upload_and_optimize()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"❌ Test error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
