# Model Phases (Sequential Overview)

This guide explains the end‑to‑end flow in order, focusing on what happens and why, without low‑level details.

## Overall Summary
We solve a **multi‑depot pickup‑and‑delivery problem with time windows (MD‑PDPTW)**. Given vendor pickups, depot deliveries, time windows, and vehicle capacity limits, the system builds feasible routes that respect pickup‑before‑delivery, service times, and max‑driving constraints. The output includes optimized routes, a map visualization, and route summaries (distance, time, cargo, volume, and utilization) for fast operational use.

## 1) Input & Upload
- User uploads a CSV in the web UI.
- Server stores the file and returns basic vendor metadata for the UI list.
- The upload response includes **suggested parameters** (e.g., max driving), but these are **minimums**, not final settings.

## 2) Preprocessing & Normalization
- Column names are normalized (spaces, casing).
- Requested Loading/Delivery fields are cleaned and unified.
- Vendor names are disambiguated when duplicates exist.
- Time window grouping (time buckets) is computed for parallel optimization.

## 3) Geocoding & Coordinates
- Vendor and recipient coordinates are validated.
- Missing or zero coordinates are geocoded.
- Invalid rows are flagged with preprocessing warnings.

## 4) Graph & Matrices
- Nodes are created: `0 = dummy start`, `1..D = depots`, `D+1..N = vendors`.
- Distance/time matrices are built (OSRM for real travel time).
- Service time per vendor is applied.
- Capacity, volume, and linear length vectors are built.

## 5) Constraints & Time Windows
- Vendor windows come from **requested loading** dates.
- Depot windows come from **requested delivery** dates.
- Precedence: pickup must occur before its delivery depot.
- Depot visits are only valid if there is cargo to unload.
- Routes must end at a depot and respect all windows.

## 6) Max Driving & Service Time
- Total route time = travel time + service time.
- Default max driving is **70 hours** (minimum floor).
- If user input is lower than minimum feasible, it is auto‑raised.

## 7) Solver Selection
- Solve with ALNS metaheuristic.
- Otherwise → ALNS metaheuristic.
- ALNS is the primary path for larger or time‑windowed batches.

## 8) Time‑Window Batching
- Requests are grouped by overlapping time windows.
- Each group is optimized independently.
- Groups can run in parallel; non‑solver steps remain serialized.

## 9) ALNS Optimization
- Builds time‑feasible routes using greedy insertion.
- Uses destroy/repair operators with adaptive weights.
- Checks route feasibility during construction (vendor + depot windows).
- Depot order is selected by **shortest feasible distance** among candidates.

## 10) Feasibility Validation
- RouteSolution validates time windows, capacity, and depot rules.
- Violations are logged with vendor/depot names for clarity.
- If infeasible and splitting is disabled, the solution is returned with warnings.

## 11) Post‑Processing & Summaries
- Dummy node is removed from display paths.
- Routes are summarized: distance, time, cargo, volume, utilization.
- All segments are kept (no same‑location grouping).

## 12) Map Generation
- Map is always generated (even if infeasible).
- Routes, vendors, and depots are plotted with labels.
- Results are saved under `results/optimization/`.

## 13) API Response & UI
- API returns routes, summaries, warnings, and map URL.
- UI renders the map, route breakdown, and utilization stats.
- Saved runs store the map and input CSV for later review.

---

If you need a deeper dive on any phase, check:
- `docs/ARCHITECTURE.md`
- `docs/QUICK_REFERENCE.md`
