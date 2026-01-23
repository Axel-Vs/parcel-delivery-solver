# Copilot Instructions - Parcel Delivery Solver

**Last Updated**: January 23, 2026  
**Project**: Enterprise VRP solver with K-Medoids clustering, ALNS metaheuristic, and Flask web UI

---

## 🏗️ Architecture Overview

**Three-layer optimization pipeline:**
1. **K-Medoids Clustering** ([alns_solver.py](../model/optimizer/alns_solver.py#L200-L300)) - PAM algorithm groups vendors by travel time (not Euclidean distance)
2. **Greedy Initial Solution** - Builds feasible routes within each cluster
3. **ALNS Metaheuristic** - Adaptive destroy/repair operators optimize across 2500 iterations

**Dual Solver Architecture:**
- **CBC MIP** ([delivery_model.py](../model/optimizer/delivery_model.py)) - Exact solutions for <20 vendors, time-expanded network formulation
- **ALNS** ([alns_solver.py](../model/optimizer/alns_solver.py)) - Metaheuristic for ≥20 vendors, route-based representation
- Auto-switches based on vendor count in [app.py#L420](../app.py#L420)

**Key Distinction:** ALNS uses **route representation** `[[0,3,5,0], [0,2,8,0]]` while MIP uses **time-expanded binary tensors**. Never mix them.

---

## 🚨 Critical Constraints & Gotchas

### Time Window Calculation (FIXED in Jan 2026)
**Problem:** 58-65 time window violations rejecting valid routes  
**Root Cause:** Depot window used `max(vendor_delivery_times)` instead of `latest_vendor_window + travel_time`

**Correct Formula** ([delivery_model.py#L850-L900](../model/optimizer/delivery_model.py#L850-L900)):
```python
# Vendor earliest arrival = loading_date - earl_arv_days
# Vendor latest arrival = loading_date + late_arv_days
# Depot earliest = min(vendor_earliest) - travel_time - service_time
# Depot latest = max(vendor_latest) + travel_time + service_time
```

**Testing:** Run `python test_alns_direct.py` - should have 0 time window violations

### Period Buffers (CRITICAL)
**Always use ±12h buffers** when calculating optimization period ([app.py#L250-L280](../app.py#L250-L280)):
```python
period_start = min_loading_date - timedelta(hours=12)  # Vendor prep time
period_end = max_delivery_date + timedelta(hours=12)   # Depot receiving time
```
Missing buffers → infeasible solutions due to too-tight time windows.

### max_driving Constraint
**Minimum:** 67 hours for US cross-country routes (Seattle to Miami one-way + service)  
**Formula:** `max_driving ≥ travel_time + (num_stops × loading_hours)`  
**Common Error:** Setting max_driving < 67 → trivial solutions (1 vendor per route)

See [QUICK_REFERENCE.md](../QUICK_REFERENCE.md) for parameter table.

---

## 📂 Key Files & Entry Points

### Flask Server
- **[app.py](../app.py)** - Main entry point on port 8080
  - Line 69: `/api/upload-csv` - CSV upload & geocoding
  - Line 211: `/api/optimize` - Core optimization endpoint
  - Line 800: `/api/saved-runs` - Run history management
  
### Core Optimization
- **[alns_solver.py](../model/optimizer/alns_solver.py)** - ALNS metaheuristic (2500 iterations)
  - Line 200: `k_medoids_clustering()` - PAM algorithm using travel time matrix
  - Line 400: `generate_initial_solution()` - Greedy cluster routing
  - Line 95: `solve()` - Main ALNS loop with simulated annealing
  
- **[delivery_model.py](../model/optimizer/delivery_model.py)** - MIP formulation
  - Line 30: `__init__()` - Time-expanded network setup
  - Line 850: Time window constraint definitions (CRITICAL - see gotchas above)
  
- **[route_solution.py](../model/optimizer/route_solution.py)** - Route container class
  - Stores routes as `List[List[int]]` where each inner list is `[depot, vendor1, vendor2, ..., depot]`
  - Line 50: `evaluate()` - Calculates total distance (objective function)
  - Line 150: `is_feasible()` - Validates capacity, time, and driving constraints

### Distance & Time Matrices
- **[graph_creator.py](../model/graph_creator/graph_creator.py)** - OSRM/Google Maps integration
  - Line 80: Routing provider selection (Google preferred, ORS fallback)
  - Line 200: `create_distance_matrix()` - Real road distances via OSRM
  - Uses persistent geocoding cache at [data/geocode_cache.csv](../data/geocode_cache.csv)

---

## 🧪 Testing Workflow

### Direct Optimization Testing (Bypasses Flask)
```bash
python test_alns_direct.py  # Uses most recent processed CSV from uploads/
```
**Use case:** Debug optimization logic without Flask server issues  
**Validates:** Time window violations, constraint satisfaction, K-Medoids clustering

### API Integration Testing
```bash
python test_period_fix_fast.py  # Fast ALNS test
python test_validation.py        # Full validation suite
```

### Test Datasets
- `data/amazon_test_dataset_small.csv` - 8 vendors (~20s, development)
- `data/amazon_test_dataset_medium.csv` - 30 vendors (~45s, demos)
- `data/amazon_test_dataset.csv` - 58 vendors (~90s, production)

**Pattern:** Test files prefixed with `test_*` are standalone scripts, not pytest suites.

---

## 🔧 Development Workflows

### Starting Development Server
```bash
cd /Users/axelvargas/Documents/Axel/parcel_delivery/parcel-delivery-solver
source parcel_env/bin/activate  # Python 3.9+ venv
python app.py                     # Starts Flask on http://localhost:8080
```

### Adding New ALNS Operators
1. Define in [local_search.py](../model/optimizer/local_search.py) (destroy/repair methods)
2. Register in [alns_solver.py](../model/optimizer/alns_solver.py#L70-L85) operator dictionaries
3. Adaptive weights auto-tune based on improvement success rate

### Modifying Constraints
**MIP constraints:** [delivery_model.py](../model/optimizer/delivery_model.py) methods prefixed with `_add_constraint_*`  
**ALNS feasibility checks:** [route_solution.py](../model/optimizer/route_solution.py#L150) `is_feasible()` method  
⚠️ **Must update BOTH** or solvers will diverge in behavior

---

## 📊 CSV Column Mappings

**Required columns** (flexible naming, app.py maps automatically):
```python
# Weight
'Vendor Gross Weight' OR 'Total Gross Weight' → capacity_matrix

# Volume (tries in order)
'Vendor Volume in m3' → loading_matrix
'Vendor Linear Length' → loading_matrix
'Calculated Loading Meters' → loading_matrix  
'Vendor Dimensions in m3' → loading_matrix

# Dates
'Requested Loading Date' → vendor time windows
'Requested Delivery Date' → depot time windows

# Location (geocoded if missing lat/lon)
'Vendor Street', 'Vendor City', 'Vendor Postcode'
'vendor_latitude', 'vendor_longitude' (auto-generated)
'recipient_latitude', 'recipient_longitude' (auto-generated)
```

See [QUICK_REFERENCE.md](../QUICK_REFERENCE.md) for complete spec.

---

## 🗺️ Map Visualization

**Folium-based** interactive maps generated in [app.py#L600-L800](../app.py#L600-L800):
- **Vendor pins:** Blue gradient teardrop markers (precise coordinate anchoring)
- **Depot:** Unfilled concentric red circles (3 rings)
- **Routes:** Color-coded polylines fetched from OSRM with real road geometry
- **Segment details:** Click routes for distance/duration/speed popup

**Common Issue:** Markers not appearing → Check geocoding succeeded (vendor_latitude exists)

---

## 🐞 Debugging Tips

### Flask Server Code 137 Exit
**Symptom:** `python app.py` terminates immediately during initialization  
**Workaround:** Use `python test_alns_direct.py` to test optimization logic directly  
**Status:** Known issue as of Jan 2026, affects web UI only (optimization code works)

### Infeasible Solutions
1. Check `max_driving ≥ 67` for US routes
2. Verify period buffers (±12h) applied
3. Run with `verbose=True` in [alns_solver.py#L95](../model/optimizer/alns_solver.py#L95) to see violation details
4. Inspect depot time window calculation in [delivery_model.py#L850](../model/optimizer/delivery_model.py#L850)

### Distance Matrix Issues
- **OSRM unavailable:** Check OSRM server running or use Google Maps API key
- **Geocoding failures:** Inspect [data/geocode_cache.csv](../data/geocode_cache.csv), uses city fallbacks
- **Zero distances:** Likely using dummy ORS client (see [graph_creator.py#L25](../model/graph_creator/graph_creator.py#L25))

---

## 📦 Dependencies & Environment

**Core:** pandas, numpy, ortools (≥9.7), Flask (≥3.1), folium (≥0.14)  
**Routing:** OSRM (preferred) or Google Maps API  
**Config:** [pyproject.toml](../pyproject.toml) - Python ≥3.8, setuptools build system

**Virtual Environment:**
```bash
python -m venv parcel_env
source parcel_env/bin/activate
pip install -e .  # Installs from pyproject.toml
```

---

## 📚 Documentation Hierarchy

**Start here:**
1. [README.md](../README.md) - Features overview & tech stack
2. [GETTING_STARTED.md](../GETTING_STARTED.md) - 5-minute quick start
3. [QUICK_REFERENCE.md](../QUICK_REFERENCE.md) - One-page cheat sheet

**Deep dives:**
- [ARCHITECTURE.md](../ARCHITECTURE.md) - System design, K-Medoids algorithm, constraint formulas
- [FEATURES.md](../FEATURES.md) - Detailed feature documentation
- [PROJECT_STRUCTURE.md](../PROJECT_STRUCTURE.md) - File organization guide

**Operational:**
- [CHANGELOG.md](../CHANGELOG.md) - Version history & recent fixes
- [DEPLOYMENT.md](../DEPLOYMENT.md) - Production deployment guide

---

## 🎯 Project-Specific Conventions

### Naming Patterns
- **max_ldms_vc** (DEPRECATED) → Use **max_volume** for volume capacity (m³)
- **max_linear_length** → Linear dimension constraint (meters)
- **discretization_constant** → Time step size in hours (typically 4h)
- **Tau_hours** → Total optimization horizon in discrete time steps

### Route Representation
**ALNS:** `[[0,3,5,0], [0,2,8,0]]` - Lists of node indices (0 = depot)  
**MIP:** Binary tensor `x[i,j,t,v]` - Arc activation at time t for vehicle v  
Never mix these in the same function.

### Logging
Uses Python stdlib `logging` with rotating file handlers:
- App logs → `/tmp/flask_app.log` (5MB limit, 3 backups)
- Optimization logs → `stdout` with `log.info()` from [model/utils/project_utils.py](../model/utils/project_utils.py)

### State Management
**APP_STATE** dict in [app.py#L53](../app.py#L53) - In-memory state for route editing:
```python
APP_STATE = {
    'routes': List[List[int]],      # Current route solution
    'distance_matrix': List[List[float]],
    'capacity_matrix': List[float],  # Indexed by vendor ID (depot at 0)
    'frozen_prefix': List[int]       # Optional: stops to preserve per route
}
```

---

## ⚡ Performance Expectations

| Vendors | Solver | Time | Routes | Quality |
|---------|--------|------|--------|---------|
| 8       | MIP    | ~20s | 2-3    | Optimal |
| 30      | ALNS   | ~45s | 8-12   | 30% reduction vs naive |
| 58      | ALNS   | ~90s | 15-20  | 50% reduction vs naive |

**Scaling:** ALNS handles 100+ vendors in ~3 minutes. K-Medoids clustering prevents quadratic explosion.

---

## 🔗 External Integrations

**OSRM (Open Source Routing Machine):**
- Default endpoint: `http://router.project-osrm.org`
- Used for real road distances/times instead of haversine
- Fallback: Google Maps Distance Matrix API (requires API key)

**Geocoding:**
- Nominatim (OpenStreetMap) with 1-second rate limit
- Persistent cache at [data/geocode_cache.csv](../data/geocode_cache.csv)
- City coordinate fallbacks for common US/CA/MX cities

---

## 🚀 Quick Command Reference

```bash
# Start server
python app.py

# Test optimization directly
python test_alns_direct.py

# Install dependencies
pip install -e .

# Run specific test dataset
# (Upload via web UI at localhost:8080)
data/amazon_test_dataset_small.csv
```

**Web UI:** http://localhost:8080 (3 tabs: Optimizer, Saved Runs, Route Visualization)
