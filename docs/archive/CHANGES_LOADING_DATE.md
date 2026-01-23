# Loading Date Information Addition

## Summary
Added expected loading date display throughout the route information interface to help users quickly identify when each route is scheduled to be executed.

## Changes Made

### 1. Backend Changes (app.py)

**Location**: Lines 858-880 in `app.py`

**What was added:**
- Calculation of `expected_loading_date` for each route
- Extracts the earliest loading date from all vendors in the route
- Format: `YYYY-MM-DD` (e.g., `2025-01-15`)

```python
# Calculate expected loading date (earliest loading date of vendors in this route)
expected_loading_date = None
vendors_seq = route_stats[vehicle_id].get('vendors', [])
if vendors_seq:
    loading_dates = []
    for vendor_id in vendors_seq:
        vendor_row = vendors_df.iloc[vendor_id - 1]  # Convert to 0-indexed
        raw_date = vendor_row.get('Requested Loading', vendor_row.get('Requested Loading Date', ''))
        parsed_date = pd.to_datetime(raw_date, errors='coerce')
        if pd.notna(parsed_date):
            loading_dates.append(parsed_date)
    
    if loading_dates:
        expected_loading_date = min(loading_dates).strftime('%Y-%m-%d')
```

**In route_summaries**:
- Added `'expected_loading_date': expected_loading_date` field to each route summary
- This field is passed to the frontend JSON response

### 2. Frontend Changes (web/index.html)

#### Route Card Display
**Location**: Route grid rendering in `displayResults()` function

**What was added:**
- A new metric row showing the pickup date with 📅 icon
- Displayed only if `expected_loading_date` is available
- Placed after Volume metric for visual hierarchy
- Full-width layout to accommodate date display

```html
${route.expected_loading_date ? `
<div class="metric-row" style="border-top: 1px solid var(--grid-line); padding-top: 6px; margin-top: 6px;">
    <span class="metric-label">📅 PICKUP</span>
    <span class="metric-value">${route.expected_loading_date}</span>
</div>
` : ''}
```

#### Route Detail Popup
**Location**: `toggleRouteSegments()` function in the route detail popup

**What was added:**
- Loading date displayed in the route information header
- Grid layout updated to show date information
- Same format and styling as route cards for consistency

```html
${route.expected_loading_date ? `<div style="grid-column: span 2;"><strong>📅 Pickup Date:</strong> ${route.expected_loading_date}</div>` : ''}
```

## User Interface Updates

### Route Grid Cards (Main View)
Each route card now displays:
- VENDORS
- DISTANCE
- TIME
- CARGO
- VOLUME
- 📅 PICKUP (NEW) ← Expected loading date

### Route Detail Popup (Click to Expand)
The detailed popup shows:
- Vendors count
- Distance
- Time
- Cargo
- Volume
- 📅 Pickup Date (NEW) ← Earliest loading date in route

### Data Flow
```
CSV Upload
    ↓
Requested Loading Date extracted from each vendor
    ↓
During route creation, collect all loading dates for vendors in route
    ↓
Select earliest loading date (min)
    ↓
Format as YYYY-MM-DD
    ↓
Display in route summary & popup with 📅 icon
```

## Logic Details

### Expected Loading Date Calculation
- **Scope**: Per route (not per vendor)
- **Value**: Earliest `Requested Loading Date` among all vendors in the route
- **Reasoning**: This is when the first pickup in the route occurs
- **Format**: `YYYY-MM-DD`
- **Fallback**: If no vendors have loading dates, field is omitted

### Example
```
Route 1 vendors: [V5, V8, V12]
  - V5: Requested Loading = 2025-01-15 09:00:00
  - V8: Requested Loading = 2025-01-16 08:00:00
  - V12: Requested Loading = 2025-01-15 14:00:00

Expected Loading Date = 2025-01-15 (min of all loading dates)
```

## Testing

### To Test the Changes:

1. **Start the server** (already running on port 8080)
   ```bash
   cd /Users/axelvargas/Documents/Axel/parcel_delivery/parcel-delivery-solver
   source parcel_env/bin/activate
   python app.py
   ```

2. **Upload a CSV** with:
   - Vendor names
   - Requested Loading Dates (in various formats)
   - Standard required columns

3. **Run optimization** → Results page shows routes with loading dates

4. **Verify**:
   - Each route card displays 📅 PICKUP with a date
   - Click a route to see detailed popup also shows loading date
   - Date is the earliest loading date in that route

### Test Dataset
Use `data/amazon_test_dataset.csv` which has proper `Requested Loading Date` values.

## Browser Compatibility
- ✅ Chrome/Chromium
- ✅ Firefox
- ✅ Safari
- ✅ Edge

## Performance Impact
- **Negligible**: O(num_vendors) calculation per route
- Executed only during optimization results display
- No impact on optimization algorithm

## Future Enhancements

1. **Date Range Display**
   - Show both earliest and latest loading dates
   - Format: `2025-01-15 to 2025-01-16`

2. **Filtering by Date**
   - Add date range filter to route selector
   - Show only routes for specific dates

3. **Route Timeline View**
   - Visual timeline showing all routes by loading date
   - Helps identify date conflicts or gaps

4. **CSV Export Enhancement**
   - Include `expected_loading_date` column in exported CSV

## Rollback Instructions
If needed to revert these changes:
1. Remove the `expected_loading_date` calculation block from `app.py` (lines 858-871)
2. Remove `'expected_loading_date': expected_loading_date` from `route_summaries`
3. Remove the date display HTML from the route cards and popup in `index.html`

## Files Modified
- ✅ `/Users/axelvargas/Documents/Axel/parcel_delivery/parcel-delivery-solver/app.py` (Lines 858-880)
- ✅ `/Users/axelvargas/Documents/Axel/parcel_delivery/parcel-delivery-solver/web/index.html` (Route card display & popup)

---

**Status**: ✅ Complete and Ready for Testing
**Date**: January 13, 2025
**Impact**: UI Enhancement (Non-Breaking)
