# 🚀 Getting Started - Quick Start Guide

**Welcome to the Parcel Delivery Solver!** This guide helps you get up and running in 5 minutes.

## 📋 Before You Start

- **Python**: 3.9+
- **Environment**: Virtual environment activated (`parcel_env/`)
- **Data**: CSV file with vendor information

---

## ⚡ Quick Start (5 minutes)

### 1. **Start the Server**
```bash
cd /Users/axelvargas/Documents/Axel/parcel_delivery/parcel-delivery-solver
source parcel_env/bin/activate
python app.py
```
Open browser: **http://localhost:8080**

### 2. **Upload CSV**
Prepare CSV with these columns:
- `Vendor Name`
- `Vendor Gross Weight` [kg]
- `Vendor Volume in m3` [m³]
- `Vendor Linear Length` [m]
- `Requested Loading Date` [YYYY-MM-DD HH:MM:SS]
- `Requested Delivery Date` [YYYY-MM-DD HH:MM:SS]
- `Vendor Street`, `Vendor City`, `Vendor Postcode`

**Upload via Web UI → Optimizer Tab**

### 3. **Configure Parameters**
In the Optimizer tab sidebar:
- **max_driving**: 67-150 (hours, min 67 for US multi-state)
- **loading**: 2 (service time per stop, hours)
- Others: Use defaults for first run

### 4. **Click "Optimize"**
- Automatic solver selection (MIP <20 vendors, ALNS ≥20)
- K-Medoids clustering creates geographical clusters
- Returns 30-50% fewer vehicles vs naive solution

### 5. **View Results**
- **Route Cards**: Vendors, Distance, Cargo, Volume, **TIME (hrs)**
- **Map**: Click routes to highlight, click map background to deselect
- **Save**: Results auto-saved to `results/runs/`

---

## 📚 Documentation Map

| File | Purpose | When to Read |
|------|---------|--------------|
| **README.md** | Features, tech stack, deployment | First time, overview |
| **ARCHITECTURE.md** | System design, algorithms, constraints | Understand how it works |
| **FEATURES.md** | Detailed feature descriptions | Deep dive into features |
| **CHANGELOG.md** | Recent changes, version history | What's new? |
| **DEPLOYMENT.md** | Streamlit deployment guide | Deploy to production |
| **This file (GETTING_STARTED.md)** | Quick start, common tasks | First time, troubleshooting |

---

## 🛠️ Common Tasks

### Task: Upload test data
```bash
# Test datasets available:
data/amazon_test_dataset_small.csv      # 8 vendors (fast, ~20s)
data/amazon_test_dataset_medium.csv     # ~30 vendors (45s)
data/amazon_test_dataset.csv            # 58 vendors (90s)
```
Use **small** for testing, **medium** for demos, **full** for production.

### Task: Increase optimization quality
**Current defaults are good, but if you want better:**
1. ✅ Increase `max_driving` (gives more room for merging)
2. ✅ Use ALNS (automatic for ≥10 vendors)
3. ✅ Wait longer - algorithm improves over time

### Task: Make routes more geographically compact
**Clusters are initialization only (not hard constraints).** Routes can cross cluster boundaries during optimization.
- **If you want cluster boundaries**: Edit `model/optimizer/alns_solver.py` line ~150, add cluster-aware constraints
- **Current design**: Maximum optimization quality + route merging

### Task: Understand route time calculation
- **TIME = Travel Time + Service Time**
- Travel: actual driving between vendors (from distance matrix)
- Service: 2 hours per stop (vendor loading/unloading)
- Example: Route with 6 vendors → 2.5h driving + 4h service = 6.5h total

### Task: Troubleshoot failed optimization
1. Check `max_driving` ≥ 67 (if US, multi-state)
2. Verify CSV has all required columns
3. Check vendor coordinates are valid (not swapped)
4. Look for service time issues (default 2h per stop)
5. See **ARCHITECTURE.md** → Troubleshooting section

---

## 🏗️ Project Structure

```
parcel-delivery-solver/
├── app.py                          ← MAIN: Flask server (port 8080)
├── model/
│   ├── optimizer/
│   │   ├── alns_solver.py         ← CORE: K-Medoids + ALNS optimization
│   │   ├── delivery_model.py      ← Route & constraint modeling
│   │   └── local_search.py        ← ALNS operators (destroy/repair)
│   ├── graph_creator/
│   │   └── graph_builder.py       ← Distance matrix & routing
│   └── utils/
│       ├── coordinate_validator.py ← Geocoding & validation
│       └── ...
├── web/
│   └── index.html                 ← Frontend UI (3-tab layout)
├── data/
│   ├── amazon_test_dataset_small.csv
│   ├── amazon_test_dataset_medium.csv
│   └── amazon_test_dataset.csv
├── results/
│   ├── optimization/              ← Generated Folium maps
│   ├── runs/                       ← Saved optimization metadata
│   └── validation/                 ← Geocoding cache
├── docs/
│   └── DOCUMENTATION.md           ← Legacy docs (see README.md instead)
├── README.md                       ← START HERE
├── ARCHITECTURE.md                ← System design deep-dive
├── FEATURES.md                    ← Detailed feature documentation
├── CHANGELOG.md                   ← Version history
├── DEPLOYMENT.md                  ← Streamlit deployment
└── GETTING_STARTED.md            ← This file
```

---

## 🔧 Key Parameters Explained

| Parameter | Default | Range | Purpose |
|-----------|---------|-------|---------|
| `max_driving` | 67 | 67-200 | Max hours per vehicle route (one-way: vendor→vendor→depot) |
| `loading` | 2 | 0.5-4 | Service time per stop (hours) |
| `driving_starts` | 6 | 0-10 | Earliest departure time (hours) |
| `driving_stop` | 21 | 18-24 | Latest stop time (hours) |
| `vehicle_capacity_kg` | 5000 | 100-10000 | Max weight per vehicle (kg) |
| `vehicle_capacity_ldms` | 90 | 10-100 | Max volume per vehicle (m³) |
| `max_linear_length` | 16.1 | 5-25 | Max linear length per vehicle (m) |

---

## ⚠️ Important Notes

### Constraint Model (Critical!)
- **ONE-WAY ONLY**: Travel time = vendor→vendor + vendor→depot (does NOT include depot→first_vendor)
- **Minimum US**: 67 hours (Miami route ~59h + 2h service + 2h buffer)
- **Service Time**: 2 hours per stop by default (not included in max_driving if set to 0)

### Solver Selection (Automatic)
- **<10 vendors**: MIP (CBC) - Guaranteed optimal, exact solution
- **≥10 vendors**: ALNS - Fast heuristic, intelligent clusters

### K-Medoids Clustering (Smart Initialization)
- **Algorithm**: PAM (Partitioning Around Medoids) on travel time matrix
- **Why**: Better than k-means for distance matrices, uses real vendors as centers
- **Clusters**: ~3 vendors per cluster (for 58 vendors → 19 clusters)
- **Outliers**: Vendors >2σ from mean get dedicated single-vendor routes
- **Not Hard Constraint**: Routes can cross clusters during ALNS optimization

---

## 🐛 Debugging

### Server won't start (Exit 137, 1, etc.)
```bash
# Kill any existing process
lsof -ti:8080 | xargs kill -9

# Try starting again
source parcel_env/bin/activate
python app.py
```

### "Connection refused" error
Wait 5 seconds (Flask initialization), then try again.

### Routes look weird / very long
Check `max_driving` parameter - may be too low, forcing impossible constraints.

### TIME column not showing in UI
Hard refresh: **Cmd+Shift+R** (clear browser cache)

---

## 📞 Next Steps

1. **First Run**: Use `data/amazon_test_dataset_small.csv` (8 vendors, 20 seconds)
2. **Understand Results**: Read the route cards and map visualization
3. **Deep Dive**: See **ARCHITECTURE.md** for algorithm details
4. **Customize**: Modify parameters and see how results change
5. **Deploy**: See **DEPLOYMENT.md** for production setup

---

**Questions?** Check the relevant `.md` file in the table above. Code is well-documented with inline comments!
