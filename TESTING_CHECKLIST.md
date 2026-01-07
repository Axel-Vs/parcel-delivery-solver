# Feature Testing Checklist

## Testing Procedure
Run through this checklist to verify all features are working correctly.

## Prerequisites
- Flask server running on http://localhost:8080
- Sample CSV files available in `data/` directory
- At least one saved run in `results/runs/`

---

## 1. Data Management

### CSV Upload
- [ ] Click "Upload New CSV" box
- [ ] Select `data/amazon_test_dataset_small.csv`
- [ ] Verify green checkmark appears: "✓ File Uploaded"
- [ ] Verify filename shows below
- [ ] Verify data summary shows: 5 vendors, weight, volume
- [ ] Verify cost estimation shows: min vehicles, approx distance
- [ ] Verify "INITIATE OPTIMIZATION" button enabled

### Past Run Loading
- [ ] Click dropdown "Load from past run..."
- [ ] Select a previous run
- [ ] Verify "✓ Loaded from Past Run" appears
- [ ] Verify vendor count shows
- [ ] Verify data summary updates
- [ ] Verify cost estimation updates
- [ ] Verify optimize button enabled

### Data Summary Card
- [ ] Load data (CSV or past run)
- [ ] Verify VENDORS count matches uploaded file
- [ ] Verify TOTAL WEIGHT shows kg value
- [ ] Verify TOTAL VOLUME shows m³ value
- [ ] Verify summary card visible and styled correctly

### Clear Data
- [ ] Load any dataset
- [ ] Click "Clear Data" button
- [ ] Verify data summary resets to 0
- [ ] Verify cost estimation hidden
- [ ] Verify optimize button disabled
- [ ] Verify vendor list shows "No data loaded"

---

## 2. Parameter Validation

### Valid Parameters
- [ ] Set Depot Start: 8
- [ ] Set Depot Close: 18
- [ ] Verify no red borders on inputs
- [ ] Verify optimization can run

### Invalid Parameters
- [ ] Set Depot Start: 18
- [ ] Set Depot Close: 8
- [ ] Verify red borders appear on both inputs
- [ ] Click "INITIATE OPTIMIZATION"
- [ ] Verify alert: "Invalid parameters..."
- [ ] Fix parameters
- [ ] Verify red borders removed

### Tooltips
- [ ] Hover over "Depot Start" parameter
- [ ] Verify tooltip appears: "Hour when depot opens..."
- [ ] Hover over "Max Driving" parameter
- [ ] Verify tooltip appears with driving hours info
- [ ] Hover over "Max Weight" parameter
- [ ] Verify tooltip appears with capacity info

---

## 3. Cost Estimation

### Display
- [ ] Load dataset with 5 vendors
- [ ] Verify "Estimated Requirements" section visible
- [ ] Verify MIN VEHICLES shows number (e.g., 1-2)
- [ ] Verify APPROX. DISTANCE shows km estimate (e.g., ~125 km)

### Dynamic Updates
- [ ] Change Max Weight from 24 to 12 tons
- [ ] Verify MIN VEHICLES increases
- [ ] Change Max Loading Meters from 13.6 to 6.8
- [ ] Verify MIN VEHICLES increases further

### Hidden When No Data
- [ ] Click "Clear Data"
- [ ] Verify cost estimation section hidden

---

## 4. Optimization

### Run Optimization
- [ ] Load `data/amazon_test_dataset_small.csv`
- [ ] Set all parameters to defaults
- [ ] Click "INITIATE OPTIMIZATION"
- [ ] Wait for completion (should take 10-30 seconds)
- [ ] Verify results section appears
- [ ] Verify map loads with routes
- [ ] Verify statistics panel shows routes, distance, time

### Results Display
- [ ] Verify map has multiple layers (route filters)
- [ ] Click route layer checkbox to hide/show route
- [ ] Verify route tooltips show cargo details on hover
- [ ] Verify route operations section visible
- [ ] Verify export section visible (4 buttons)
- [ ] Verify undo/redo controls visible

---

## 5. Results Filtering (NEW)

### Filter Visibility
- [ ] After optimization completes
- [ ] Scroll to route list area
- [ ] Verify three filter dropdowns present: Week, Vendor, Route
- [ ] Verify each dropdown shows "All weeks/vendors/routes" as first option
- [ ] Verify dropdown lists are populated with available options

### Linked Filter: Week Selection
- [ ] Select a specific week from Week dropdown (e.g., "Week 1")
- [ ] Verify Vendor dropdown updates to show only vendors in Week 1
- [ ] Verify Route dropdown updates to show only routes in Week 1
- [ ] Verify route summary cards update to show only Week 1 routes
- [ ] Verify route count reduced on map

### Linked Filter: Vendor Selection
- [ ] Click Week dropdown, select "All weeks" (reset)
- [ ] Select a specific vendor from Vendor dropdown (e.g., "Vendor A")
- [ ] Verify Week dropdown updates to show only weeks with Vendor A
- [ ] Verify Route dropdown updates to show only routes with Vendor A
- [ ] Verify route summary cards update accordingly
- [ ] Verify map shows only routes containing Vendor A

### Linked Filter: Route Selection
- [ ] Click Vendor dropdown, select "All vendors" (reset)
- [ ] Select a specific route from Route dropdown (e.g., "Route 3")
- [ ] Verify Week dropdown updates to show only the week of Route 3
- [ ] Verify Vendor dropdown updates to show only vendors on Route 3
- [ ] Verify only Route 3 summary card displays
- [ ] Verify map highlights only Route 3

### Cascade Reset: All Weeks Button
- [ ] Select Week: "Week 2"
- [ ] Select Vendor: "Vendor B"
- [ ] Select Route: "Route 5"
- [ ] Click Week dropdown and select "All weeks"
- [ ] Verify Vendor dropdown resets to "All vendors"
- [ ] Verify Route dropdown resets to "All routes"
- [ ] Verify all route summary cards visible

### Cascade Reset: All Vendors Button
- [ ] Select Week: "Week 3"
- [ ] Select Vendor: "Vendor C"
- [ ] Select Route: "Route 8"
- [ ] Click Vendor dropdown and select "All vendors"
- [ ] Verify Week dropdown resets to "All weeks"
- [ ] Verify Route dropdown resets to "All routes"
- [ ] Verify all route cards and map routes visible

### Cascade Reset: All Routes Button
- [ ] Select Week: "Week 1"
- [ ] Select Vendor: "Vendor D"
- [ ] Select Route: "Route 1"
- [ ] Click Route dropdown and select "All routes"
- [ ] Verify Week dropdown resets to "All weeks"
- [ ] Verify Vendor dropdown resets to "All vendors"
- [ ] Verify all route cards and map routes visible

### Route Card Visibility
- [ ] Apply filter: Week = "Week 2", Vendor = "All vendors"
- [ ] Verify only route cards containing vendors from Week 2 display
- [ ] Apply filter: Vendor = "Vendor E", Week = "All weeks"
- [ ] Verify only route cards containing Vendor E display
- [ ] Apply filter: Route = "Route 12"
- [ ] Verify only Route 12 summary card displays

### Map Layer Synchronization
- [ ] Select Week: "Week 4"
- [ ] Verify map shows only routes in Week 4 (colored layers visible)
- [ ] Select Vendor: "Vendor F"
- [ ] Verify map shows only routes containing Vendor F
- [ ] Select Route: "Route 15"
- [ ] Verify map highlights only Route 15 with distinct styling

### Filter Count Display
- [ ] Open Week dropdown
- [ ] Verify "All weeks" option shows total count (e.g., "All weeks (4)")
- [ ] Select a vendor
- [ ] Verify Week dropdown "All weeks" updates to show filtered count (e.g., "All weeks (2)")
- [ ] Same for Vendor and Route dropdowns

---

## 6. Download and Export

### Download CSV
- [ ] After optimization completes
- [ ] Click "Download CSV" button in export section
- [ ] Verify CSV downloads with timestamp filename
- [ ] Open CSV and verify route data present

### Save with Custom Name
- [ ] Click "Save with Custom Name" button
- [ ] Verify modal opens with input field
- [ ] Enter name: "Test Run 123"
- [ ] Click "Save" button
- [ ] Verify modal closes
- [ ] Go to Saved Runs tab
- [ ] Verify "Test Run 123" appears in table

### Excel Export (Placeholder)
- [ ] Click "Export to Excel" button
- [ ] Verify alert: "Excel export coming soon..."

### Map Image Export (Placeholder)
- [ ] Click "Download Map as Image" button
- [ ] Verify alert: "Map image export coming soon..."

---

## 7. Route Operations

### Add Delivery
- [ ] After optimization
- [ ] Enter vendor name in "Add Delivery" input
- [ ] Click "Add to Route" button
- [ ] Verify success alert with route number
- [ ] Verify page refreshes with updated routes
- [ ] Verify undo button enabled

### Remove Delivery
- [ ] Enter vendor name in "Remove Delivery" input
- [ ] Click "Remove from Route" button
- [ ] Verify success alert with route number
- [ ] Verify page refreshes
- [ ] Verify undo button enabled

### Truck Breakdown
- [ ] Select a route from "Select truck..." dropdown
- [ ] Click "Mark Breakdown" button
- [ ] Verify confirmation dialog appears
- [ ] Click OK
- [ ] Verify alert: "Truck breakdown feature..."
- [ ] (Note: Full redistribution requires backend implementation)

---

## 8. Undo/Redo

### Undo Operation
- [ ] After optimization, perform an add/remove operation
- [ ] Verify undo button (◀) enabled
- [ ] Click undo button
- [ ] Verify results revert to previous state
- [ ] Verify redo button (▶) enabled

### Redo Operation
- [ ] After undo
- [ ] Click redo button (▶)
- [ ] Verify changes re-applied
- [ ] Verify undo button enabled again

### History Limits
- [ ] At start of history (no operations yet)
- [ ] Verify undo button disabled (gray)
- [ ] At end of history (after redo)
- [ ] Verify redo button disabled (gray)

---

## 9. Route Details Modal

### Open Modal
- [ ] After optimization
- [ ] Click "Route Details" button (if visible)
- [ ] Verify modal opens with route information
- [ ] Verify scrollable content
- [ ] Verify stop-by-stop breakdown visible

### Close Modal
- [ ] Click "Close" button in modal
- [ ] Verify modal closes
- [ ] Re-open modal
- [ ] Click backdrop (outside modal content)
- [ ] Verify modal closes
- [ ] Re-open modal
- [ ] Press ESC key
- [ ] Verify modal closes

---

## 10. Saved Runs Management

### View Runs Table
- [ ] Click "Saved Runs" tab
- [ ] Verify sidebar automatically hidden
- [ ] Verify runs table displays
- [ ] Verify columns: checkbox, name, run ID, created, solver, actions
- [ ] Verify at least one run visible

### View Map
- [ ] Click "View Map" button for any run
- [ ] Verify new tab opens with interactive map
- [ ] Verify routes displayed correctly

### Download Input
- [ ] Click "Download Input" button for any run
- [ ] Verify CSV downloads
- [ ] Open CSV and verify vendor data

### Re-run
- [ ] Click "Re-run" button for any run
- [ ] Verify switches to Optimizer tab
- [ ] Verify data loaded
- [ ] Verify parameters populated
- [ ] Verify optimize button enabled

---

## 11. Comparison Mode

### Select Runs
- [ ] In Saved Runs tab
- [ ] Click checkbox for first run
- [ ] Verify counter shows "1 selected"
- [ ] Click checkbox for second run
- [ ] Verify counter shows "2 selected"
- [ ] Verify "Compare Selected Runs" button appears

### Select All
- [ ] Click "Select All" checkbox in header
- [ ] Verify all run checkboxes checked
- [ ] Verify counter shows total count
- [ ] Click "Select All" again
- [ ] Verify all unchecked
- [ ] Verify compare button hidden

### Compare Runs
- [ ] Select 2-3 runs
- [ ] Click "Compare Selected Runs" button
- [ ] Verify comparison results appear below table
- [ ] Verify side-by-side table with metrics
- [ ] Verify metrics include: routes, distance, time, vendors, solver
- [ ] Click "Close" button
- [ ] Verify comparison results hidden

### Error Cases
- [ ] Select only 1 run
- [ ] Try to click compare (button should be hidden)
- [ ] Verify no error occurs

---

## 12. Sidebar Toggle

### Show/Hide Sidebar
- [ ] In Optimizer tab, verify sidebar visible
- [ ] Click "Hide Sidebar ◀" button (left side of screen)
- [ ] Verify sidebar slides out
- [ ] Verify content area expands
- [ ] Verify button text changes to "Show Sidebar ▶"
- [ ] Click "Show Sidebar ▶"
- [ ] Verify sidebar slides back in

### Auto-Hide on Saved Runs
- [ ] In Optimizer tab with sidebar visible
- [ ] Click "Saved Runs" tab
- [ ] Verify sidebar automatically hides
- [ ] Click "Optimizer" tab
- [ ] Verify sidebar reappears

---

## 13. Modal Interactions

### Save Dialog
- [ ] Trigger save dialog
- [ ] Press ESC key
- [ ] Verify modal closes
- [ ] Trigger again
- [ ] Click backdrop
- [ ] Verify modal closes

### Route Details
- [ ] Trigger route details
- [ ] Press ESC key
- [ ] Verify modal closes
- [ ] Trigger again
- [ ] Click backdrop
- [ ] Verify modal closes

### Comparison Results
- [ ] Trigger comparison
- [ ] Press ESC key
- [ ] Verify comparison hidden
- [ ] (Note: Uses inline div, not full modal)

---

## 14. UI/UX Polish

### Color Scheme
- [ ] Verify all buttons black (except disabled = gray)
- [ ] Verify enterprise badge "INTELLIGENT ROUTER" is black
- [ ] Verify tabs: active = black, inactive = gray
- [ ] Verify no blue colors anywhere (except map)

### Responsive Layout
- [ ] Resize browser window
- [ ] Verify sidebar and content area adjust
- [ ] Verify tables scroll horizontally if needed
- [ ] Verify modals remain centered

### Visual Feedback
- [ ] Upload file → green checkmark
- [ ] Invalid parameters → red borders
- [ ] Disabled buttons → gray, not clickable
- [ ] Tooltips → appear on hover
- [ ] Loading states → show during optimization

---

## 15. Error Handling

### Network Errors
- [ ] Stop Flask server
- [ ] Try to upload CSV
- [ ] Verify error message appears
- [ ] Try to optimize
- [ ] Verify error message appears
- [ ] Restart server
- [ ] Verify app recovers

### Invalid Data
- [ ] Create CSV with missing columns
- [ ] Try to upload
- [ ] Verify error message
- [ ] Create CSV with invalid dates
- [ ] Try to upload
- [ ] Verify error message or parsing warnings

### Empty States
- [ ] Load app fresh (no data)
- [ ] Verify "No data loaded" message
- [ ] Go to Saved Runs tab
- [ ] Verify "No runs saved yet" message (if no runs)

---

## 16. Performance

### Load Times
- [ ] Upload small dataset (5 vendors)
- [ ] Measure time to load and show summary (should be <1 sec)
- [ ] Upload medium dataset (20 vendors)
- [ ] Measure time to load (should be <2 sec)

### Optimization Times
- [ ] Optimize 5 vendors
- [ ] Measure completion time (should be 10-30 sec)
- [ ] Optimize 20 vendors with ALNS
- [ ] Measure completion time (should be 30-60 sec)

### UI Responsiveness
- [ ] During optimization, try to interact with UI
- [ ] Verify sidebar still responds
- [ ] Verify tabs still switch
- [ ] Verify no freezing or lag

---

## Results Summary

**Total Tests**: 150+  
**Passed**: ___  
**Failed**: ___  
**Skipped**: ___  

### Issues Found
1. 
2. 
3. 

### Notes
- 
- 
- 

### Tested By: _______________
### Date: _______________
### Browser: _______________
### OS: _______________
