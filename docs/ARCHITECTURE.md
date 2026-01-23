# Architecture Documentation

## System Overview

The Parcel Delivery Solver is a VRP (Vehicle Routing Problem) optimization platform using intelligent clustering, metaheuristic optimization, and interactive visualization.

```
┌─────────────────────────────────────────────────────────────┐
│                    Web UI (Flask + Folium)                   │
│  - 12 parameter controls                                     │
│  - Interactive map with vendor pins & routes                │
│  - Route statistics: Distance, Cargo, Volume, TIME (NEW)    │
│  - Real-time optimization progress                          │
└──────────────────────┬──────────────────────────────────────┘
                       │
         ┌─────────────┴─────────────┐
         │                           │
    ┌────▼─────────┐        ┌─────────▼─────┐
    │ CSV Upload   │        │ Optimization  │
    │ Geocoding    │        │ Request       │
    └────┬─────────┘        └────┬──────────┘
         │                        │
         └────────────┬───────────┘
                      │
         ┌────────────▼────────────────────────┐
         │  OPTIMIZATION PIPELINE              │
         │  ===================================│
         │  1. K-Medoids Clustering (PAM)      │
         │  2. Greedy Initial Solution         │
         │  3. ALNS Metaheuristic              │
         │  4. Route Merging                   │
         │  5. Selective Route Fixing          │
         └────────────┬────────────────────────┘
                      │
         ┌────────────▼────────────────┐
         │ Map Generation (Folium)     │
         │ + Route Statistics Export   │
         └────────────┬────────────────┘
                      │
         ┌────────────▼────────────────┐
         │ Web Display & CSV Export    │
         └────────────────────────────┘
```

## K-Medoids Clustering (NEW)

### Algorithm: PAM (Partitioning Around Medoids)

**Why K-Medoids instead of K-Means?**
- ✅ Works directly with travel time distance matrix (not coordinates)
- ✅ More robust to outliers (medoids are real data points)
- ✅ Better for non-Euclidean metrics
- ✅ Gives representative centers (actual vendors)

### Initialization (K-Medoids++)
1. **First medoid**: Vendor with minimum total travel time to all others
2. **Additional medoids**: Greedily select vendors farthest from existing medoids

### Optimization Loop
- **Assign**: Each vendor → nearest medoid by travel time
- **Update**: Try swapping each medoid with each non-medoid
- **Accept**: If swap reduces total travel time
- **Converge**: When no beneficial swaps exist (max 50 iterations)

### Configuration
```python
# For 58 vendors:
num_clusters = 58 // 3 = ~19 clusters target
outlier_threshold = mean_distance + 2.0 * std_dev
  → Extreme outliers get individual routes
  → Normal vendors grouped into clusters
```

### Output Example
```
📦 Created 12 geographical clusters:
   Cluster 1: 5 vendors (West Coast)
   Cluster 2: 4 vendors (Southwest)
   Cluster 3: 4 vendors (Midwest)
   ...
   Cluster 12: 4 vendors (Northeast)
```

## Greedy Cluster Routing

**Goal**: Build initial feasible routes within each cluster

**For each cluster:**
1. Initialize route with depot `[0]`
2. Greedily insert nearest feasible vendor:
   - Check capacity constraints (weight, volume)
   - Check max_driving time constraint
   - Calculate insertion cost (distance increase)
   - Select vendor with minimum cost
3. Close route by returning to depot

**Result**: Feasible initial solution with geographically compact routes

## ALNS Metaheuristic

### Destroy Operators
- **Random**: Randomly remove 5-15% of vendors
- **Worst-cost**: Remove vendors with highest removal cost
- **Shaw**: Remove related vendors (by proximity)

### Repair Operators
- **Greedy**: Insert each removed vendor at lowest-cost position
- **Regret-2**: Insert vendor that minimizes regret between best and second-best positions

### Optimization Loop (2,500 iterations)
```
for iteration in range(2500):
    1. Select random destroy & repair operator pair
    2. Destroy: Remove vendors from solution
    3. Repair: Reinsert vendors at best positions
    4. Accept/Reject: Simulated annealing criterion
    5. Update temperature: Cool down gradually
    6. Every 250 iterations: Try route merging
```

### Temperature Control
- **Initial**: `T0 = 1200` (accept worse solutions)
- **Cooling**: `T *= 0.996` (gradually become greedy)
- **Final**: Accept only improvements

## Route Merging (NEW)

**Goal**: Combine routes when feasible to reduce vehicle count

**For each pair of routes:**
1. Check if merged route satisfies:
   - Capacity constraints (weight + volume)
   - Max driving time constraint
2. If feasible: Merge and evaluate distance improvement
3. Keep merge if distance doesn't increase significantly

**Execution:**
- Every 250 ALNS iterations
- After ALNS completes (final aggressive pass)
- Iteratively until no more beneficial merges

## Selective Route Fixing (NEW)

**Old behavior (problematic):**
- If ANY route violates constraints → Generate 57 trivial routes
- Discards all optimization gains

**New behavior (smart):**
1. Identify which specific routes violate constraints
2. Split ONLY violated routes into single-vendor routes
3. Keep all feasible routes intact

**Example:**
- Initial solution: 25 routes (3 violations)
- After fixing: 22 routes kept + 3 routes split into 18 → ~40 total
- Result: 30% vehicle reduction vs trivial fallback

## Constraint System

### Max Driving Time
```
total_time = travel_time + service_time
where:
  - travel_time: Sum of all vendor→vendor + vendor→depot segments
  - service_time: 2 hours × number of stops
constraint: total_time ≤ max_driving_hours
```

### Capacity Constraints
```
route_weight ≤ max_capacity_kg
route_volume ≤ max_ldms_m3
```

### Validation
- **Greedy insertion**: Check before adding each vendor
- **Repair operators**: Check before finalizing solution
- **Route merging**: Check before combining routes
- **Final solution**: Full feasibility check

## Data Flow

### Input CSV
```
Vendor Name, Vendor Gross Weight [kg], Vendor Dimensions in m3 [m³],
Vendor Street, Vendor City, Vendor Postcode,
Requested Loading Date, Requested Delivery Date [YYYY-MM-DD HH:MM:SS]
```

### Processing
1. **Geocoding**: Address → (latitude, longitude)
2. **Distance matrix**: OSRM free routing API
3. **Time matrix**: Time from distance (seconds)
4. **Capacity matrix**: Weight per vendor
5. **Loading matrix**: Volume per vendor

### Output
- **Routes**: List of vendor sequences per vehicle
- **Map HTML**: Folium visualization with polylines
- **Route CSV**: 14 columns per stop (details, times, cargo)
- **Statistics**: Total distance, cargo, volume, routes, time

## Performance

### Solver Selection
```
if num_vendors < 20:
    Use MIP (exact) - guaranteed optimal within time limit
else:
    Use ALNS (heuristic) with K-Medoids clustering
```

### Time Complexity
- **K-Medoids**: O(I × n² × k) where I ≤ 50 iterations, n = vendors, k = clusters
- **Greedy routing**: O(n²) per cluster
- **ALNS**: O(iterations × n) = O(2500 × n)
- **Total**: ~30 seconds for 58 vendors on standard hardware

### Memory Usage
- **Distance matrix**: O(n²) = 58² × 8 bytes = ~27 KB
- **Route structures**: O(n × vehicles) = 58 × 30 × pointers
- **Total**: ~5 MB for 58 vendors

## Configuration

### File: `model_params.txt`
```
MIP_GAP: 0.05
MIP_TIME: 20  (minutes)
ALNS_ITERATIONS: 2500
ALNS_TIME: 10  (minutes)
SOLVER_THRESHOLD: 10  (vendors)
```

### File: `.github/copilot-instructions.md`
System architecture, node ID system, constraint definitions, and common issues

## Troubleshooting

| Issue | Root Cause | Solution |
|-------|-----------|----------|
| Routes span entire country | ALNS broke clusters | Enable cluster constraints (WIP) |
| TIME column not visible | Browser cache | Hard refresh: Cmd+Shift+R (Mac) |
| Only 3 clusters created | Old parameter | Updated to: `num_clusters = len(vendors) // 3` |
| Infeasible solution | Violated constraints | Selective fixing keeps good routes |
| Slow optimization | Too many iterations | Reduce ALNS_ITERATIONS in model_params.txt |

## Future Improvements

1. **Cluster enforcement**: Make clusters hard constraints (no cross-cluster moves)
2. **Dynamic cluster sizing**: Adjust target vendors/cluster based on geography
3. **Time window constraints**: Add pickup/delivery time windows
4. **Multi-depot routing**: Support multiple distribution centers
5. **Real-time tracking**: Track vehicles during delivery
