# 🧠 Intelligent Router

![Platform Screenshot 1](docs/platform_screenshot_1.png)
*Route visualization with interactive map and multi-route display.*

![Platform Screenshot 2](docs/platform_screenshot_2.png)
*Enterprise web interface: parameter configuration and optimization results.*

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![OR-Tools](https://img.shields.io/badge/OR--Tools-9.7%2B-orange.svg)](https://developers.google.com/optimization)
[![Flask](https://img.shields.io/badge/Flask-3.1%2B-black.svg)](https://flask.palletsprojects.com/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An elegant, enterprise-grade AI-powered route optimization platform for vehicle routing and parcel delivery. Featuring a sophisticated web interface with real-time optimization, interactive map visualization, and comprehensive network parameter configuration. Built with OR-Tools, ALNS metaheuristics, and OSRM routing integration.

## ✨ Features

### 🎨 Enterprise Web Interface
- **Elegant 3-Tab Layout** - Modern black tab navigation with white active highlights
  - **Optimizer Tab** - 320px sidebar with 12 parameters + interactive map visualization
  - **Saved Runs Tab** - Complete optimization history with management tools
  - **Route Visualization** - Full-screen maps for detailed route inspection
- **Clickable Branding** - Click "Intelligent Router" title to quickly navigate to Optimizer tab
- **Refined Design System** - Light beige/bone color palette (#FAFAF8) with black accents and deep navy branding
- **Real-Time Optimization** - Web-based interface with live progress tracking and results
- **Responsive Parameters** - 12 configurable network parameters in clean 2-column grid
- **Compact Results Display** - Optimized stat cards showing routes, distance, cargo, volume, and solving time

### 🚚 Advanced Optimization
- **Multi-Vehicle Routing** - Efficiently assigns parcels to vehicles and optimizes delivery routes
- **Dual Solver Architecture** - CBC MIP for exact solutions + ALNS metaheuristic for large-scale problems
- **K-Medoids Clustering** - Intelligent geographical grouping using PAM algorithm on travel time matrix
  - Creates 12+ clusters for 58 vendors (target: 3 vendors per cluster)
  - Uses real travel distances, not Euclidean coordinates
  - Identifies extreme outliers for dedicated routes
- **12 Network Parameters** - Comprehensive configuration for depot hours, driving limits, vehicle capacity
- **Smart Auto-Scaling** - Automatically switches to metaheuristic for datasets with 20+ vendors
- **Selective Route Fixing** - Only fixes violated routes instead of full fallback to trivial solution
- **Route Merging Optimization** - Intelligently combines routes when feasible, maintaining constraints
- **Scalable to 50+ Vendors** - Handles large datasets with advanced ALNS optimization

### 🗺️ Interactive Visualization
- **Beautiful Map Interface** - Folium-based maps with real road routing via OSRM
- **Clean Single Layer** - OpenStreetMap base (no tile/layer control for minimalist design)
- **Professional Vendor Pins** - Elegant blue gradient teardrop markers, perfectly anchored at exact coordinates
- **Depot Highlights** - Unfilled concentric red circles marking distribution center
- **Route Visualization** - Color-coded polylines with real road geometry
- **Route Visibility Control** - Excel-style filter: Select All/Deselect All from results panel
- **Segment Details** - Click routes for right-side popup with distance, duration, speed metrics

### 📊 Comprehensive Analytics
- **Route Statistics** - Total routes, vendors, distance, cargo weight, loading volume
- **Performance Metrics** - Solving time, vehicle utilization, capacity percentages
- **Real-World Routing** - Uses OSRM for accurate distance and travel time calculations
- **Geocoding Support** - Automatic address-to-coordinate conversion with persistent caching

### 💾 Saved Runs Management
- **Run History** - Browse all past optimization runs with comparison table
- **Three Action Buttons** per run:
  - **View Map** - Open interactive map visualization in new tab
  - **Input Data CSV** - Download original input dataset
  - **Route Solution CSV** - Export detailed route solution (14 columns)
- **Re-optimization** - Load any past run to adjust parameters and re-run optimization
- **Delete Functionality** - Remove unwanted runs from history
- **Selection Controls** - Checkboxes for future batch operations

### 📄 Solution CSV Export
Comprehensive route details with 14 columns per stop:
- Route Number, Stop Sequence, Stop Type (pickup/delivery)
- Vendor ID, Vendor Name, Vendor City, Vendor Address
- Recipient Name, Recipient City, Recipient Address  
- Cargo Weight (kg), Cargo Volume (m³)
- Requested Delivery Date, Requested Loading Date

## 🎯 Key Capabilities

### Route Optimization
- Multi-vehicle fleet management
- Capacity constraints (weight and volume)
- Time windows and delivery scheduling
- Distance minimization
- Vehicle count optimization

### Visualization
- **Interactive Maps** - Single OpenStreetMap base layer for clean, focused display
- **Excel-Style Route Filter**:
  - Collapsible filter panel with transparent styling
  - Select All/Deselect All for quick toggling
  - Individual route visibility controls
  - Real-time map layer synchronization
- **Professional Markers**:
  - **Depot**: Unfilled concentric red circles (three rings) marking distribution hub
  - **Vendors**: Elegant blue gradient pins with precise coordinate anchoring
- **Segment Details**: Click any route to display metrics (distance, duration, speed) in right-side popup
### Data Processing
- CSV-based vendor and parcel data
- Geocoding with persistent cache
- Distance/time matrix calculation via OSRM
- Time discretization for optimization

## � Documentation Guide

**New users?** Start here in this order:
1. **[GETTING_STARTED.md](GETTING_STARTED.md)** ← Begin here! (5-minute quick start)
2. **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** ← Handy cheat sheet
3. **[README.md](README.md)** (this file) ← Features and installation
4. **[ARCHITECTURE.md](ARCHITECTURE.md)** ← How the algorithm works (deep dive)
5. **[FEATURES.md](FEATURES.md)** ← Detailed feature descriptions
6. **[DEPLOYMENT.md](DEPLOYMENT.md)** ← Production deployment

---

## �📋 Prerequisites

- Python 3.8 or higher
- pip package manager
- Internet connection (for OSRM routing and geocoding)

## 🚀 Getting Started

### Installation

1. **Clone the repository**
```bash
git clone https://github.com/Axel-Vs/parcel-delivery-solver.git
cd parcel-delivery-solver
```

2. **Create and activate virtual environment**
```bash
python -m venv parcel_env
source parcel_env/bin/activate  # On Windows: parcel_env\Scripts\activate
```

3. **Install the package and dependencies**
```bash
pip install -e .
```
> This installs the package in editable mode using `pyproject.toml`

### Quick Start - Web Application

1. **Start the web server**
```bash
python app.py
```

2. **Open your browser**
   - Navigate to `http://localhost:8080`
   - You'll see the elegant Intelligent Router interface with 3 tabs: **Optimizer**, **Saved Runs**, **Route Visualization**

3. **Upload your dataset** (Optimizer tab)
   - Click the upload area or drag-and-drop your CSV file
   - Supported format: vendor coordinates, cargo weight, loading volume

4. **Configure optimization parameters**
   - **Depot Hours**: Start (8) and Close (18) times
   - **Vendor Hours**: Start (6) and Pickup End (14) times
   - **Time Windows**: Early Arrival (24h) and Late Arrival (24h) penalties
  - **Loading & Driving**: Loading Time (2h), Max Driving (70h), Driving Start/Stop (6-21)
   - **Vehicle Capacity**: Max Weight (30 kg), Max Loading Meters (70 m³)

5. **Run optimization**
   - Check "ALNS METAHEURISTIC" for large datasets (20+ vendors)
   - Click "INITIATE OPTIMIZATION"
   - Watch the progress message: "Finding optimal routes with minimal distance and fewest vehicles required"

6. **View results**
   - **Left Panel**: Optimization results with routes, vendors, distance, cargo, volume, solving time
   - **Right Panel**: Interactive map with all routes visualized
   - **Route Control**: Toggle individual routes or use Select All/Deselect All

7. **Manage saved runs** (Saved Runs tab)
   - Browse all past optimization runs in comparison table
   - **View Map**: Open interactive visualization in new tab
   - **Input Data CSV**: Download original input dataset
   - **Route Solution CSV**: Export detailed 14-column route solution
   - **Re-optimize**: Load past run back to Optimizer to adjust parameters and re-run
   - **Delete**: Remove unwanted runs from history

### Command Line Usage (Alternative)

For batch processing or programmatic use:

```bash
python example/simulator.py
```

**Results:**
- Solution summary printed to console
- Interactive map saved to `results/optimization/routes_[date].html`
- Solution arrays saved as `.npy` files

## 📊 Data Format

### Input CSV Structure
Your data file should include:

| Column | Description | Example |
|--------|-------------|---------|
| `vendor_latitude` | Vendor location latitude | 37.7749 |
| `vendor_longitude` | Vendor location longitude | -122.4194 |
| `recipient_latitude` | Delivery destination latitude | 47.6062 |
| `recipient_longitude` | Delivery destination longitude | -122.3321 |
| `vendor Name` | Vendor business name | Tech Company Inc. |
| `Vendor City` | Vendor city | San Francisco |
| `Vendor Postcode` | Vendor postal code | 94102 |
| `Total Shipment Weight (kg)` | Parcel weight in kilograms | 12438 |
| `Total Volume (cbm)` | Parcel volume in cubic meters | 31.5 |
| `Requested Delivery` | Delivery date/time | 2023-08-16 15:00:00 |

Example CSV:
```csv
vendor_latitude,vendor_longitude,recipient_latitude,recipient_longitude,vendor Name,Vendor City,Vendor Postcode,Total Shipment Weight (kg),Total Volume (cbm),Requested Delivery
37.7749,-122.4194,47.6062,-122.3321,Tech Company Inc.,San Francisco,94102,12438,31.5,2023-08-16 15:00:00
29.7604,-95.3698,47.6062,-122.3321,Manufacturing Co.,Houston,77001,13005,22.1,2023-08-17 14:45:00
```

### Solution CSV Export

After optimization completes, download a comprehensive **Route Solution CSV** from the Saved Runs tab with 14 detailed columns per stop:

| Column | Description | Example |
|--------|-------------|---------|
| `Route Number` | Vehicle/route identifier | 1 |
| `Stop Sequence` | Order of stop in route | 1 |
| `Stop Type` | Type of stop | pickup |
| `Vendor ID` | Unique vendor identifier | vendor_123 |
| `Vendor Name` | Vendor business name | Tech Company Inc. |
| `Vendor City` | Vendor city | San Francisco |
| `Vendor Address` | Full vendor address | 123 Main St, San Francisco, CA 94102 |
| `Recipient Name` | Delivery destination name | Amazon Fulfillment Center |
| `Recipient City` | Recipient city | Seattle |
| `Recipient Address` | Full recipient address | 456 Warehouse Rd, Seattle, WA 98101 |
| `Cargo Weight (kg)` | Weight of cargo at this stop | 12438 |
| `Cargo Volume (m³)` | Volume of cargo at this stop | 31.5 |
| `Requested Delivery Date` | Requested delivery time | 2023-08-16 15:00:00 |
| `Requested Loading Date` | Requested pickup time | 2023-08-15 08:00:00 |

**Use Cases:**
- Detailed route planning and dispatch
- Driver manifest generation
- Loading dock scheduling
- Delivery confirmation tracking
- Performance analysis and reporting

## ⚙️ Configuration

### Web Application Parameters

The web interface provides 12 configurable network parameters in an elegant 2-column grid:

| Parameter | Description | Default | Unit |
|-----------|-------------|---------|------|
| **Depot Start** | Depot opening hour | 8 | Hour |
| **Depot Close** | Depot closing hour | 18 | Hour |
| **Vendor Start** | Vendor availability start | 6 | Hour |
| **Pickup End** | Latest pickup time | 14 | Hour |
| **Early Arrival** | Early arrival penalty window | 24 | Hours |
| **Late Arrival** | Late arrival penalty window | 24 | Hours |
| **Loading Time** | Time required for loading | 2 | Hours |
| **Max Driving** | Maximum driving hours per vehicle | 70 | Hours |
| **Driving Start** | Earliest driving start time | 6 | Hour |
| **Driving Stop** | Latest driving end time | 21 | Hour |
| **Max Weight** | Maximum vehicle cargo weight | 30 | kg |
| **Max Loading Meters** | Maximum vehicle volume capacity | 70 | m³ |

### Model Parameters (`model/config/model_params.txt`)
```ini
max_nodes = 100                   # Maximum number of nodes to process
solver_time_limit = 900          # Solver timeout in seconds (15 minutes)
mip_gap_tolerance = 0.1          # MIP optimality gap (10%)
optimization_weight = 0.5        # Balance: distance (0.5) vs vehicle count (0.5)
use_metaheuristic = True         # Auto-switch to ALNS for large problems
alns_iterations = 2000           # ALNS: number of iterations
alns_temperature = 1500          # ALNS: initial temperature
alns_cooling = 0.997             # ALNS: cooling rate
```

### Network Parameters (`model/config/network_params.txt`)
```ini
discretization_constant = 4       # Time discretization in hours
starting_depot = 8               # Depot opening hour
closing_depot = 18               # Depot closing hour
vendor_start_hr = 6              # Vendor availability start
pickup_end_hr = 14               # Latest pickup time
earl_arv = 24                    # Early arrival penalty window
late_arv = 24                    # Late arrival penalty window
loading = 2                      # Loading time in hours
max_driving = 70                 # Maximum driving hours
driving_starts = 6               # Earliest driving start
driving_stop = 21                # Latest driving end
max_weight = 30                  # Maximum cargo weight (kg)
max_ldms = 70                    # Maximum loading meters (m³)
```

### Simulation Parameters (`model/config/simulation_params.txt`)
```ini
simulation_start_date = 2023-08-15 08:00:00
simulation_end_date = 2023-08-15 10:00:00
dataset_file = data/amazon_test_dataset.csv
```

## 🗺️ OSRM Integration

The system uses the free OSRM routing service for:
- **Distance Calculations** - Real road network distances
- **Travel Times** - Actual driving durations
- **Route Geometry** - Precise routing paths for visualization

✅ No API key required! Uses public OSRM server at `router.project-osrm.org`.

### Routing Features
- Real-world road network routing
- Automatic distance matrix generation
- Travel time estimation
- Route polyline extraction for maps

## 📈 Output & Visualization

### Web Interface Results

The elegant 2-column layout displays results in a compact sidebar:

**Optimization Results Panel (Left):**
- **ROUTES**: Number of vehicles used (e.g., 28)
- **VENDORS**: Total vendor pickups (e.g., 58)
- **TOTAL DISTANCE**: Aggregate route distance in km (e.g., 97,129.7 km)
- **TOTAL CARGO**: Sum of all cargo weight in kg (e.g., 561,002 kg)
- **TOTAL LOADING**: Aggregate volume in m³ (e.g., 1,314.65 m³)
- **SOLVING TIME**: Optimization duration in seconds (e.g., 10.62 sec)
- **WARNINGS**: Infeasible solutions and preprocessing issues appear inline (no popups)

**Route Detail Popup (Left Sidebar):**
- **Route start**: Starting vendor, requested loading, expected arrival, cargo, volume
- **Segments**: Vendor → vendor → depot with distance, duration, loading time
- **Time windows**:
  - Vendor: expected arrival vs requested loading window
  - Depot: expected arrival vs requested delivery window

**Interactive Map (Right):**
- Full-screen route visualization with multiple tile layers
- Colored routes for each vehicle with OSRM-based road routing
- Vendor markers showing cargo details and pickup locations
- Depot markers with black headers indicating start/end points
- Layer control panel for toggling individual routes
- Select All/Deselect All for route visibility

### Console Output (Command Line)
```
================================================================================
                         📊 OPTIMIZATION SOLUTION SUMMARY
================================================================================

🚛 VEHICLE USAGE (y variables):
   ✓ y[1] = 1  → Vehicle 1 is USED
   ✓ y[2] = 1  → Vehicle 2 is USED
   📦 Total vehicles in solution: 2

🗺️  ROUTE ASSIGNMENTS (x variables):
🚚 Vehicle 1:
   Route: Vendor 1 (San Francisco) → Depot (Seattle)
   Distance: 1299 km
   Driving Time: 15.6 hrs
   Cargo: 12,438 kg | Loading: 31.5 m³

🚚 Vehicle 2:
   Route: Vendor 2 (Houston) → Vendor 3 (Chicago) → Depot (Seattle)
   Distance: 5027 km
   Driving Time: 55.7 hrs
   Cargo: 15,280 kg | Loading: 24.85 m³

Total Distance: 6327 km
Distance reduction achieved: 24.03%
```

### Interactive Map Features (Web & Saved Files)

**Hover over routes** to see:
- 🚚 Vehicle ID and route step (e.g., "Step 1/2")
- 📦 Cargo pickup: weight (kg) and volume (m³)
- 📏 Segment distance and duration
- 💨 Average speed
- 📊 Complete route summary with capacity utilization

**Vendor markers** display:
- Blue teardrop pins showing pickup locations
- Tooltip on hover with vendor name and city

**Map Controls:**
- 🗺️ Multiple tile layers (Street Map, Light, Dark, Terrain)
- � Excel-style collapsible route filter with Select All
- 🔍 Mini-map for context
- 📏 Distance measurement tool
- 🖥️ Fullscreen mode
- 📍 Mouse position coordinates
- 👁️ Individual route visibility toggles

#### Route Highlighting and Developer Logs

- Click any route polyline to highlight it in yellow with a soft glow.
- Clicking a different route will automatically clear the previous highlight (exclusive highlighting).
- Clicking the same route again toggles the highlight off.
- Open your browser’s DevTools Console to see detailed logs under the tag "[ROUTE HIGHLIGHT]" (initialization, layer scanning, handler attachment, and click events).
- The script is resilient to load timing and re-attaches handlers when layers change.

### Saved Files
- `results/optimization/routes_[date]_metaheuristic.html` - Interactive map (ALNS solver)
- `results/optimization/routes_[date].html` - Interactive map (CBC solver)
- `results/optimization/solution[N]_[date].npy` - Numpy arrays with decision variables
- `data/geocode_cache.csv` - Cached geocoding results

## 🧹 Maintenance & Cleanup

To reduce confusion and keep the workspace clean, you can safely remove ephemeral artifacts:

- Clear distance matrix caches: `cache/*.json` (will be regenerated on next run)
- Remove old optimization maps: keep only the most recent file under `results/optimization/`
- Clear temporary uploads: `uploads/*.csv` (inputs can be re-uploaded as needed)
- Remove Python bytecode caches: `**/__pycache__/`

Important: Do not delete `data/geocode_cache.csv`. This file persists geocoding results and avoids rate limits and repeated geocoding.

After cleanup, the app will regenerate the necessary matrices and outputs on demand.

## 🔧 Project Architecture

```
parcel-delivery-solver/
├── app.py                        # Flask web application server (Port 8080)
├── web/
│   └── index.html                # Elegant web interface with 2-column layout
├── data/                          # Input datasets and cache
│   ├── amazon_test_dataset.csv   # Main dataset
│   ├── geocode_cache.csv         # Geocoding cache
│   └── *.csv                     # Additional test datasets
├── model/
│   ├── config/                   # Configuration files
│   │   ├── model_params.txt      # Solver parameters
│   │   ├── network_params.txt    # 12 network parameters (depot, driving, capacity)
│   │   └── simulation_params.txt # Simulation settings
│   ├── graph_creator/            # Time-expanded network generation
│   │   └── graph_creator.py
│   ├── optimizer/                # Optimization model
│   │   └── delivery_model.py    # ALNS + CBC optimization with Folium maps
│   └── utils/                    # Utility functions
│       ├── geocoder.py           # Address geocoding
│       ├── pre_processing.py     # Data preprocessing
│       └── project_utils.py      # OSRM distance/routing utilities
├── example/
│   └── simulator.py              # Command-line simulation script
├── results/                      # Output directory
│   └── optimization/             # Solution files and interactive maps
├── docs/                         # Documentation
├── requirements.txt              # Python dependencies
├── pyproject.toml                # Package configuration
└── README.md                     # This file
```

### Web Application Stack
- **Backend**: Flask 3.1+ (Python web framework)
- **Frontend**: Vanilla HTML/CSS/JavaScript with elegant design system
- **Optimization**: OR-Tools 9.7+ with ALNS metaheuristics
- **Mapping**: Folium with OSRM routing integration
- **Colors**: Light beige backgrounds (#FAFAF8, #F7F7F5), black text/buttons, deep navy branding (#1f2d3d)

## 🧮 Algorithms & Methods

### Dual Solver Architecture

The system intelligently selects between two optimization approaches:

#### 1. OR-Tools CBC Solver (Small-Medium Problems)
The **Coin-or Branch and Cut (CBC)** solver provides exact solutions for smaller datasets (typically <20 vendors).

**Key Features:**
- Linear programming with integer constraints
- Branch and cut algorithm for optimal solutions
- Configurable time limits and gap tolerance
- Requires time-expanded network for temporal modeling

**Optimization Objectives:**
- Minimize total distance traveled
- Minimize number of vehicles used
- Balance trade-off via `optimization_weight` parameter

#### 2. ALNS Metaheuristic (Large-Scale Problems)
The **Adaptive Large Neighborhood Search (ALNS)** provides high-quality solutions for large datasets (20+ vendors).

**Key Features:**
- Route-based optimization (no time-expansion needed)
- Adaptive destroy/repair operators
- Simulated annealing acceptance criterion
- Scales to 50+ vendors efficiently

**ALNS Parameters:**
```python
iterations = 2000              # Total iterations
start_temperature = 1500      # Initial acceptance temperature
cooling_rate = 0.997          # Temperature decay
removal_fraction = 0.15-0.45  # Vendors removed per iteration
local_search_iterations = 100 # Local optimization steps
```

**When to Use Each:**
- **CBC MIP**: Exact solutions, <20 vendors, time windows critical
- **ALNS**: Fast solutions, 20-100+ vendors, large date ranges

### Time-Expanded Network
Discretizes time into fixed periods (e.g., 4 hours) to:
- Model temporal constraints efficiently
- Handle delivery time windows
- Optimize arrival schedules
- Reduce problem complexity

**Time Discretization Formula:**
```python
discrete_time = ceil(travel_time_seconds / 3600 / discretization_constant)
```

### Distance Matrix Calculation
Uses OSRM for real-world routing:
1. Query OSRM table API with vendor/depot coordinates
2. Extract distance (meters) and duration (seconds)
3. Cache results for efficiency
4. Convert to discrete time periods

## 📊 Example Use Case

### Scenario
Optimize deliveries for 3 vendors across the US with 2 vehicles available.

### Input Data
| Vendor | Location | Weight | Volume | Destination |
|--------|----------|--------|--------|-------------|
| Vendor 1 | San Francisco, CA | 12,438 kg | 31.5 m³ | Seattle, WA |
| Vendor 2 | Houston, TX | 13,005 kg | 22.1 m³ | Seattle, WA |
| Vendor 3 | Chicago, IL | 2,275 kg | 2.8 m³ | Seattle, WA |

### Optimization Results
**Vehicle 1:**
- Route: San Francisco → Seattle
- Distance: 1,299 km
- Time: 15.6 hours
- Utilization: 41.5% weight, 45.0% volume

**Vehicle 2:**
- Route: Houston → Chicago → Seattle
- Distance: 5,027 km
- Time: 55.7 hours
- Utilization: 50.9% weight, 35.5% volume

**Performance:**
- Total distance: 6,327 km
- Distance reduction: 24.03% vs direct routing
- Vehicles used: 2 out of 3 available

## 🎨 Design System

The Intelligent Router features an elegant, enterprise-grade design system:

### Color Palette
- **Primary Background**: #FAFAF8 (Light bone/beige)
- **Secondary Background**: #F7F7F5 (Darker bone)
- **Surface**: #FFFFFF (Pure white for cards)
- **Border**: #E8E6E0 (Subtle beige border)
- **Text Primary**: #000000 (Black for main content)
- **Text Secondary**: #000000 (Black for labels)
- **Text Muted**: #9A9892 (Light gray for units and hints)
- **Accent**: #000000 (Black for buttons and emphasis)
- **Branding**: #1f2d3d (Deep navy for "Intelligent Router" title)
- **Tab Navigation**: #000000 (Black background with white active highlights)
- **Tab Active**: #FFFFFF (White text and bottom border for active tab)

### Typography
- **Font Family**: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", system fonts
- **Title**: 28px, Bold (700), Deep Navy (#1f2d3d)
- **Section Headers**: 10px, Uppercase, Bold (600), Letter Spacing
- **Parameter Labels**: 9px, Uppercase, Light Gray for units
- **Stats**: 16px values, 9px labels

### Layout
- **2-Column Grid**: 320px sidebar + flexible map area
- **Compact Spacing**: 8-10px padding, 6-8px gaps
- **Border Radius**: 6-12px for modern, refined corners
- **Shadows**: Soft (0 2px 8px rgba(0,0,0,0.04))

### Components
- **Tab Navigation**: Black background (#000000), white text for active tab, smooth transitions
- **Buttons**: Black background, white text, 32px height, rounded 6px
- **Action Buttons**: 320px width for longer labels (e.g., "Input Data CSV", "Route Solution CSV")
- **Input Fields**: 32px height, white background, aligned rows
- **Stat Cards**: 10px padding, 16px stat values, minimal design
- **Parameters Grid**: 2 columns, aligned inputs, light gray units
- **Saved Runs Table**: Comparison table with View Map, Input Data CSV, Route Solution CSV buttons per run

## 🐛 Troubleshooting

### Common Issues

**Issue: "Web app not loading at localhost:8080"**
- Solution: Ensure Flask server is running (`python app.py`). Check if port 8080 is already in use.
- Check console for errors: `lsof -ti:8080` to see if port is occupied

**Issue: "CSV upload fails"**
- Solution: Ensure CSV has required columns: vendor coordinates, cargo weight, loading volume
- Check file encoding (UTF-8 recommended)
- Verify numeric values are properly formatted

**Issue: "Depot coordinates not found"**
- Solution: The system uses Seattle (47.6062, -122.3321) as default. Configure depot coordinates in your dataset or modify the code.

**Issue: "OSRM connection timeout"**
- Solution: Check internet connection. OSRM requires online access to router.project-osrm.org
- Try again after a moment - OSRM may be temporarily unavailable

**Issue: "No feasible solution found"**
- Solutions:
  - Switch to metaheuristic solver (check "ALNS METAHEURISTIC" in web interface)
  - Increase `solver_time_limit` in model_params.txt
  - Adjust network parameters: increase Max Driving, adjust time windows
  - Reduce dataset size or increase vehicle capacities (Max Weight, Max Loading Meters)

**Issue: "Optimization takes too long"**
- Solution: Enable ALNS metaheuristic for datasets with 20+ vendors
- Reduce `alns_iterations` in model_params.txt for faster (but potentially less optimal) solutions
- Consider reducing date range in simulation parameters

**Issue: "Geocoding failed"**
- Solution: Ensure coordinates are provided in the CSV or check Nominatim service availability
- Use geocode cache (`data/geocode_cache.csv`) to avoid repeated API calls

**Issue: "Division by zero warnings"**
- This is normal for vendors at the same location - the system handles it with circular markers

**Issue: "max() iterable argument is empty"**
- Solution: Use metaheuristic solver for large date ranges (enable ALNS checkbox)

**Issue: "Routes not visible on map"**
- Solution: Check the Routes dropdown in top-right - ensure routes are toggled on
- Use "Select All" to quickly enable all routes
- Refresh the page if routes don't appear after optimization

**Issue: "Map not displaying correctly"**
- Solution: Ensure JavaScript is enabled in browser
- Check browser console for errors (F12)
- Try different tile layer (Street, Light, Dark, Terrain)

## 🤝 Contributing

We welcome contributions! Here's how you can help:

### Ways to Contribute
- 🐛 Report bugs and issues
- 💡 Suggest new features
- 📝 Improve documentation
- 🔧 Submit pull requests

### Development Setup
1. Fork the repository
2. Create a feature branch
   ```bash
   git checkout -b feature/AmazingFeature
   ```
3. Make your changes and test
4. Commit with descriptive messages
   ```bash
   git commit -m 'feat: Add some AmazingFeature'
   ```
5. Push to your branch
   ```bash
   git push origin feature/AmazingFeature
   ```
6. Open a Pull Request

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **[Google OR-Tools](https://developers.google.com/optimization)** - Powerful optimization solver
- **[OSRM](http://project-osrm.org/)** - Open Source Routing Machine
- **[Folium](https://python-visualization.github.io/folium/)** - Interactive map visualization
- **[Nominatim](https://nominatim.org/)** - OpenStreetMap geocoding service
- **[GeoPy](https://geopy.readthedocs.io/)** - Geocoding library

## 📧 Contact & Support

**Author:** Axel Vargas  
**GitHub:** [@Axel-Vs](https://github.com/Axel-Vs)  
**Project:** [Intelligent Router](https://github.com/Axel-Vs/parcel-delivery-solver)

For questions, issues, or suggestions:
- 📫 Open an issue on GitHub
- 💬 Start a discussion
- 🐛 Report bugs with detailed information
- 🌐 Access the web interface at http://localhost:8080

## 📚 Additional Resources

- [OR-Tools Documentation](https://developers.google.com/optimization/routing)
- [Vehicle Routing Problem](https://en.wikipedia.org/wiki/Vehicle_routing_problem)
- [OSRM API Documentation](http://project-osrm.org/docs/v5.5.1/api/)
- [Time-Expanded Networks](https://en.wikipedia.org/wiki/Time-expanded_network)
- [Flask Documentation](https://flask.palletsprojects.com/)
- [Folium Documentation](https://python-visualization.github.io/folium/)

## 🎯 Use Cases

**Intelligent Router** is ideal for:
- 📦 **E-commerce & Logistics** - Optimize delivery routes for online orders
- 🚚 **Fleet Management** - Minimize fuel costs and vehicle usage
- 📍 **Last-Mile Delivery** - Efficient urban delivery planning
- 🏭 **Manufacturing** - Pickup route optimization from suppliers
- 🌍 **Multi-City Routing** - Long-distance transportation planning
- ⏱️ **Time-Sensitive Deliveries** - Routes with strict time windows

---

<p align="center">
  <b>🧠 Intelligent Router - Enterprise AI-Powered Logistics Optimization</b><br>
  Made with ❤️ for efficient routing and sustainable transportation<br>
  ⭐ Star this repo if you find it useful!
</p>
