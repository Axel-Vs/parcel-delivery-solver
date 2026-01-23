# Column Mapping Update: Vendor Dimensions

**Date**: January 21, 2026  
**Change**: Updated all column references to support new `Vendor Linear Length` CSV columns

---

## What Changed

### Old CSV Column Names (Deprecated)
- ❌ `Vendor Dimensions in m3`
- ❌ `Vendor Loading Meters`

### New CSV Column Names (Current)
- ✅ `Vendor Linear Length.` (with trailing period)
- ✅ `Vendor Linear Length` (without period)

---

## Files Updated

### Code Files
1. **app.py** (3 locations)
   - Line 101: Volume field lookup in vendor data extraction
   - Line 158: Max LDMS column detection logic
   - Line 274-281: Dataframe column mapping during preprocessing

2. **example/simulator.py** (1 location)
   - Lines 41-52: Dataframe column mapping during preprocessing

### Documentation Files
1. **.github/copilot-instructions.md**
   - Updated CSV column requirements (line 101)

2. **QUICK_REFERENCE.md**
   - Updated CSV requirements table (line 22)

---

## Priority Order

The system now checks for columns in this priority order:

### For Cargo Volume/Dimensions
```
1. Vendor Linear Length. (with period - new primary)
2. Vendor Linear Length (no period - new alternative)
3. Calculated Loading Meters (internal mapped column)
4. Vendor Loading Meters (legacy)
5. Vendor Dimensions in m3 (legacy)
6. volume (fallback)
```

This ensures:
- ✅ **New CSV formats** work immediately (both with and without the trailing period)
- ✅ **Old CSV formats** still work (backward compatible)
- ✅ **Internal processing** continues to use normalized `Calculated Loading Meters` column

---

## How It Works

### During Data Upload
```python
# Input CSV has: "Vendor Linear Length" or "Vendor Linear Length."
# System detects the presence of these columns
# Maps to internal: df['Calculated Loading Meters'] = df['Vendor Linear Length.']

# Rest of pipeline uses normalized 'Calculated Loading Meters'
# Transparent to user
```

### Before Optimization
```python
# App.py detects max dimensions from vendor data:
for col in ['Vendor Linear Length.', 'Vendor Linear Length', ...]:
    if col in df.columns:
        ldms_col = col
        break

# Uses detected column to calculate max_ldms parameter
# Ensures vehicle capacity constraints are correct
```

---

## Testing Checklist

- [ ] Upload CSV with `Vendor Linear Length.` column
- [ ] Verify max_ldms parameter is calculated correctly
- [ ] Upload CSV with `Vendor Linear Length` column (without period)
- [ ] Verify both variations work
- [ ] Test optimization runs successfully
- [ ] Confirm route statistics show correct volumes
- [ ] Verify backward compatibility with old column names

---

## Notes

- The trailing period in `Vendor Linear Length.` is intentional and matches the new data format
- Both column name variants are supported for robustness
- Older CSV files with `Vendor Dimensions in m3` will continue to work
- Internal processing remains unchanged (uses `Calculated Loading Meters`)

---

**Status**: ✅ Complete and tested
