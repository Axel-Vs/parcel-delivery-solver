# 🏗️ Project Structure Guide

**For next session clarity: This document explains the project organization.**

---

## 📚 Documentation (7 files total)

### **🟢 START HERE**
- **[GETTING_STARTED.md](GETTING_STARTED.md)** - 5-minute quick start guide
  - How to start server
  - How to upload data
  - Common tasks
  - Troubleshooting

- **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** - One-page cheat sheet
  - Quick commands
  - Parameter table
  - Key formulas
  - Performance expectations

### **🟡 PRIMARY DOCS**
- **[README.md](README.md)** - Project overview & features
  - What the system does
  - Installation instructions
  - Technology stack

- **[ARCHITECTURE.md](ARCHITECTURE.md)** - System design deep-dive
  - K-Medoids clustering algorithm
  - ALNS optimization process
  - Constraint model
  - Performance analysis
  - Troubleshooting

- **[FEATURES.md](FEATURES.md)** - Detailed feature documentation
  - Architecture section
  - Each feature explained
  - Configuration details

### **🟠 REFERENCE DOCS**
- **[CHANGELOG.md](CHANGELOG.md)** - Version history
  - What changed recently
  - Bug fixes
  - New features

- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Deployment guide
  - Streamlit deployment
  - Production setup

---

## 📁 Code Structure

```
parcel-delivery-solver/
│
├── 🎯 app.py                          ← MAIN ENTRY POINT
│   └── Flask server on port 8080
│   └── Handles CSV upload, optimization, map generation
│
├── 📦 model/
│   ├── optimizer/
│   │   ├── alns_solver.py            ← CORE ALGORITHM
│   │   │   ├── K-Medoids clustering (PAM)
│   │   │   ├── Greedy cluster routing
│   │   │   ├── ALNS optimization (2500 iterations)
│   │   │   └── Route merging
│   │   ├── delivery_model.py         ← Route constraints & validation
│   │   ├── local_search.py           ← ALNS operators (destroy/repair)
│   │   └── route_solution.py         ← Route container & evaluation
│   │
│   ├── graph_creator/
│   │   └── graph_builder.py          ← Distance & time matrices
│   │
│   └── utils/
│       ├── coordinate_validator.py   ← Geocoding
│       └── cache management
│
├── 🎨 web/
│   └── index.html                    ← WEB UI
│       ├── 3-tab layout (Optimizer, Saved Runs, Routes)
│       ├── Parameter sidebar
│       ├── Route cards with TIME metrics
│       └── Folium map visualization
│
├── 📊 data/
│   ├── amazon_test_dataset_small.csv      (8 vendors, 20s)
│   ├── amazon_test_dataset_medium.csv     (30 vendors, 45s)
│   └── amazon_test_dataset.csv            (58 vendors, 90s)
│
├── 📝 results/
│   ├── optimization/                 ← Generated Folium maps
│   ├── runs/                          ← Saved run metadata
│   └── validation/                    ← Geocoding cache
│
└── 📄 docs/
    ├── DOCUMENTATION.md              (legacy, see ARCHITECTURE.md)
    └── ROUTING_PROVIDERS.md

```

---

## 🔄 Data Flow

```
CSV Upload
    ↓
Validation (coordinate_validator.py)
    ↓
Geocoding (if needed) + caching
    ↓
Distance Matrix (graph_builder.py via OSRM)
    ↓
Time Matrix (from distance)
    ↓
ALNS Solver (alns_solver.py)
    ├─ K-Medoids clusters vendors
    ├─ Greedy insertion per cluster
    ├─ ALNS optimization (2500 iterations)
    ├─ Route merging
    └─ Selective route fixing
    ↓
Output (app.py)
    ├─ Route cards (Vendors, Distance, Cargo, Volume, TIME)
    ├─ Folium map (polylines + markers)
    └─ CSV export
```

---

## 🎯 Key Files & Their Jobs

| File | Purpose | Key Function |
|------|---------|--------------|
| `app.py` | Flask server | POST /api/optimize, handles UI |
| `alns_solver.py` | Optimization engine | `solve()` method, K-medoids + ALNS |
| `delivery_model.py` | Route modeling | Constraint checking, feasibility |
| `local_search.py` | ALNS operators | Destroy/repair methods |
| `graph_builder.py` | Distance matrices | OSRM routing |
| `coordinate_validator.py` | Geocoding | Address → coordinates |
| `index.html` | Web UI | 3-tab interface, map display |

---

## ⚙️ Optimization Pipeline (What happens when you click "Optimize")

### 1️⃣ **K-Medoids Clustering** (30 seconds for 58 vendors)
```
Algorithm: PAM (Partitioning Around Medoids)
Input: Travel time distance matrix
Output: ~19 clusters (target: 3 vendors per cluster)
Purpose: Geographical grouping for better initial solution
```

### 2️⃣ **Greedy Cluster Routing** (automatic after clustering)
```
For each cluster:
  - Insert vendors one by one
  - Respect capacity + time constraints
  - Create feasible initial routes
Result: ~25 routes (vs 57 trivial solution)
```

### 3️⃣ **ALNS Optimization** (2500 iterations)
```
Repeat 2500 times:
  - Destroy: Remove 12-40% of customers
  - Repair: Reinsert using greedy
  - Evaluate: Calculate cost
  - Accept: If better or probability accepts
Result: ~24 routes, improved distance
```

### 4️⃣ **Route Merging** (every 250 iterations + final)
```
For each pair of routes:
  - Try combining them
  - Check constraints: capacity + max_driving
  - Accept if feasible
Result: ~24 routes (vehicles consolidated)
```

### 5️⃣ **Selective Route Fixing** (if violations found)
```
Find routes that violate max_driving constraint
Split ONLY those routes (keep good routes intact)
Result: All routes feasible, fewer vehicles
```

---

## 📊 Example Run Output

```
✅ Optimization Successful

📦 Created 12 geographical clusters
🚚 Initial Solution: 27 routes, 100,396 km

ALNS Optimization (2500 iterations):
  ├─ Iteration 1250: 25 routes, 100,100 km
  ├─ Iteration 2000: 24 routes, 99,750 km
  └─ Final: 24 routes, 99,622 km

Route Merging: 24 routes → 24 routes (3 merged)

Violations Found: 2 routes exceed max_driving
Selective Fixing: Split 2 routes

Final Solution: 30 routes, 101,200 km
Vehicle Reduction: 47% (vs 57 trivial)
Total Time: 89 seconds

Route Examples:
  Route 1: 6 vendors, 4850 km, 55.9 hours (2 service + 53.9 driving)
  Route 2: 5 vendors, 3200 km, 41.0 hours (1.5 service + 39.5 driving)
  ...
```

---

## 🔧 Configuration

### Default Parameters (in app.py)
```python
max_driving = 67           # hours (one-way: vendor→vendor→depot)
loading = 2                # hours per stop (service time)
driving_starts = 6         # earliest departure (hours)
driving_stop = 21          # latest stop time (hours)
vehicle_capacity_kg = 5000 # max weight
vehicle_capacity_ldms = 50 # max volume (m³)
```

### Clustering Parameters (in alns_solver.py)
```python
target_cluster_size = len(vendors) // 3  # ~3 vendors per cluster
outlier_threshold = 2.0                  # sigma from mean
max_kmedoids_iterations = 50             # PAM algorithm iterations
```

### ALNS Parameters (auto-scaled by vendor count)
```python
For 58 vendors:
  iterations = 2500        # optimization loops
  T0 = 2000               # initial temperature
  cooling_rate = 0.9975   # temperature decrease
  removal_rate = 20-50%   # destroy percentage
```

---

## 🎓 Understanding the Constraint

**CRITICAL: One-way constraint only!**

```
max_driving ≥ (travel_time + service_time)

Where:
  travel_time = sum(vendor→vendor + vendor→depot)
                (does NOT include depot→first_vendor outbound)
  
  service_time = num_stops × loading_hours
                 (default: num_stops × 2)
  
  max_driving = parameter (default: 67 hours for US)

Example:
  Route: [Depot, V1, V2, V3, Depot]
  
  Travel Time:
    Depot→V1 = 1h
    V1→V2 = 2h
    V2→V3 = 3h
    V3→Depot = 1h
    Total = 7h
  
  Service Time:
    3 stops × 2h = 6h
  
  Total Constraint Time = 7 + 6 = 13 hours
  
  Must satisfy: 13 ≤ max_driving
```

---

## ✅ Next Session Checklist

- [ ] Read GETTING_STARTED.md first
- [ ] Check QUICK_REFERENCE.md when you need quick answers
- [ ] Know the one-way constraint (see above)
- [ ] Understand that clusters are initialization only
- [ ] TIME metric = driving + service hours
- [ ] Default max_driving = 67 for US (non-negotiable minimum)
- [ ] ALNS starts at 10 vendors (MIP for <10)

---

**Questions?** Each `.md` file is linked and referenced. Start with GETTING_STARTED.md!
