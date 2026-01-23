# Expected Loading Date - Visual Guide

## Where It Appears in the UI

### 1. Route Grid Cards (Main Route Breakdown View)

```
┌─────────────────────────────────┐
│        ROUTE 1                  │
│     5 VENDORS                   │
├─────────────────────────────────┤
│ VENDORS        5                │
│ DISTANCE       4,850 km         │
│ TIME           55h 54m          │
│ CARGO          12,500 kg        │
│ VOLUME         28 m³            │
│ ─────────────────────────────   │  ← NEW: Separator line
│ 📅 PICKUP      2025-01-15      │  ← NEW: Expected Loading Date
└─────────────────────────────────┘
```

**Location**: Bottom of each route card in the grid
**Icon**: 📅 Calendar emoji
**Format**: `YYYY-MM-DD`
**Visibility**: Click any route card to see full details

---

### 2. Route Detail Popup (Expanded View)

```
╔════════════════════════════════════╗
║  🚚 Route 1              [✕]      ║
╠════════════════════════════════════╣
║ ┌──────────────────────────────┐  ║
║ │ Vendors:     5               │  ║
║ │ Distance:    4,850 km        │  ║
║ │ Time:        55h 54m         │  ║
║ │ Cargo:       12,500 kg       │  ║
║ │ Volume:      28 m³           │  ║
║ │ 📅 Pickup Date: 2025-01-15  │  ║  ← NEW: Date display
║ └──────────────────────────────┘  ║
║                                    ║
║ ROUTE SEGMENTS                     ║
║                                    ║
║ [Segments listed below...]         ║
║                                    ║
╚════════════════════════════════════╝
```

**Location**: Top section of popup, after Volume
**Spans full width** for emphasis
**Format**: `📅 Pickup Date: YYYY-MM-DD`

---

## Data Flow Diagram

```
                    CSV Upload
                        │
                        ▼
            Vendor Records (n rows)
                        │
                        ├─ Vendor 1: Loading Date = 2025-01-15
                        ├─ Vendor 2: Loading Date = 2025-01-16
                        └─ Vendor 3: Loading Date = 2025-01-15
                        │
                        ▼
              Route Creation (Optimization)
              ┌────────────────────────┐
              │ Route 1:               │
              │  - Vendor 1            │
              │  - Vendor 3            │
              │  - Vendor 2            │
              └────────────────────────┘
                        │
                        ▼
         Extract Loading Dates for Route 1:
         [2025-01-15, 2025-01-15, 2025-01-16]
                        │
                        ▼
          Calculate MIN (Earliest):
          2025-01-15 ← Expected Loading Date
                        │
                        ▼
        Display in Route Summary:
        📅 PICKUP: 2025-01-15
```

---

## Example Scenarios

### Scenario 1: Mixed Loading Dates
```
Route 2 contains:
  ├─ Vendor A: Requested Loading = Jan 15, 9am
  ├─ Vendor B: Requested Loading = Jan 16, 8am
  └─ Vendor C: Requested Loading = Jan 15, 2pm

Expected Loading Date = 2025-01-15  ← Earliest date
```

### Scenario 2: Same Day Multiple Pickups
```
Route 3 contains:
  ├─ Vendor X: Requested Loading = Jan 20, 10am
  ├─ Vendor Y: Requested Loading = Jan 20, 11am
  └─ Vendor Z: Requested Loading = Jan 20, 9am

Expected Loading Date = 2025-01-20  ← Same day
```

### Scenario 3: No Loading Dates Available
```
Route 4 contains vendors with no loading dates

Expected Loading Date = [Not Displayed]  ← Falls back gracefully
```

---

## Backend Logic (app.py)

```python
# For each route in the optimization results:
expected_loading_date = None

# Get all vendors in this route
vendors_seq = route.get('vendors', [])

if vendors_seq:
    loading_dates = []
    
    # Collect all loading dates
    for vendor_id in vendors_seq:
        vendor_row = vendors_df.iloc[vendor_id - 1]
        raw_date = vendor_row.get('Requested Loading', '')
        parsed_date = pd.to_datetime(raw_date, errors='coerce')
        
        if pd.notna(parsed_date):
            loading_dates.append(parsed_date)
    
    # Select the earliest date
    if loading_dates:
        expected_loading_date = min(loading_dates).strftime('%Y-%m-%d')

# Add to route summary
route_summary = {
    'route_id': route_id,
    'expected_loading_date': expected_loading_date,  # ← NEW
    # ... other fields
}
```

---

## Frontend Logic (web/index.html)

### Route Card Display
```javascript
// In route grid rendering:
${route.expected_loading_date ? `
<div class="metric-row" style="border-top: 1px solid var(--grid-line); padding-top: 6px; margin-top: 6px;">
    <span class="metric-label">📅 PICKUP</span>
    <span class="metric-value">${route.expected_loading_date}</span>
</div>
` : ''}
```

### Route Detail Popup Display
```javascript
// In route detail popup:
${route.expected_loading_date ? 
    `<div style="grid-column: span 2;">
        <strong>📅 Pickup Date:</strong> ${route.expected_loading_date}
    </div>` 
: ''}
```

---

## CSS Styling

The date display uses existing route metric styles:

```css
.metric-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 10px;
}

.metric-label {
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-size: 8px;
}

.metric-value {
    color: var(--text-primary);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
}
```

---

## Testing Instructions

### To verify the implementation:

1. **Upload a CSV** with `Requested Loading Date` column
   - Example: `2025-01-15 09:00:00`

2. **Run optimization**
   - Routes will be created

3. **Check Route Grid**
   - Scroll down to see each route card
   - Look for `📅 PICKUP` with date

4. **Click a Route Card**
   - Popup appears
   - Should show `📅 Pickup Date: YYYY-MM-DD`

5. **Verify Date Accuracy**
   - Check that date = earliest loading date in route
   - Not all vendors' loading dates, just the minimum

---

## Browser DevTools Check

To verify data is being passed correctly:

1. Open Browser DevTools (F12)
2. Go to Network tab
3. Run optimization
4. Find `/api/optimize` response
5. Search for `expected_loading_date` in JSON
6. Should see entries like: `"expected_loading_date": "2025-01-15"`

---

## FAQ

**Q: Why show the earliest loading date?**
A: This indicates when the route execution should begin. The driver needs to start the route on or after this date.

**Q: What if vendors have different loading times (not just dates)?**
A: Currently shows only the date portion. Future version could include time if needed.

**Q: What if a vendor has no loading date?**
A: That vendor is skipped in the date calculation. If all vendors have no date, the field isn't displayed.

**Q: Can the date be used for filtering?**
A: Not yet, but it's a planned future enhancement. Date range filtering could help operators see routes for specific days.

---

## Performance Notes

- ✅ Negligible performance impact
- ✅ Executed only during results display
- ✅ O(num_vendors_in_route) complexity
- ✅ No impact on optimization algorithm
- ✅ Works with large datasets (58+ vendors)

---

**Last Updated**: January 13, 2025
**Status**: ✅ Implemented and Ready
