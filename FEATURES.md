# Parcel Delivery Solver - Feature Documentation

## Overview
This document describes all features implemented in the web application, including the 12 new enhancements added in the latest update.

## Core Features

### 1. Data Management

#### CSV Upload
- **Location**: Sidebar > Data Input
- **Functionality**: Upload vendor CSV files with automatic validation
- **Visual Feedback**: Green checkmark and filename display on successful upload
- **Triggers**: Enables optimization button, updates data summary, calculates cost estimates

#### Past Run Loading
- **Location**: Sidebar > Data Input (dropdown below upload)
- **Functionality**: Load data from previously saved optimization runs
- **Use Case**: Modify parameters and re-optimize with same dataset
- **Visual Feedback**: Shows "✓ Loaded from Past Run" with vendor count

#### Data Summary Card
- **Location**: Sidebar > Below Data Input
- **Displays**:
  - Total number of vendors
  - Total weight (kg)
  - Total volume (m³)
- **Auto-updates**: Refreshes whenever data is loaded or modified

#### Clear Data Button
- **Location**: Sidebar > Below Data Summary
- **Functionality**: Resets all loaded data, clears visualization, disables optimization
- **Confirmation**: No confirmation required (can be undone by reloading)

### 2. Optimization Parameters

#### Network Parameters
- **Depot Hours**: Starting depot (default: 8:00), Closing depot (default: 18:00)
- **Vehicle Capacity**: Max weight (tons), Max loading meters (m³)
- **Routing**: Max driving hours per day
- **Validation**: Real-time validation prevents depot start ≥ close time (red border on errors)
- **Tooltips**: Hover hints on Depot Start, Max Driving, Max Weight parameters

#### Solver Selection
- **ALNS Metaheuristic**: Checkbox to enable/disable (checked by default)
- **Auto-switching**: System automatically uses ALNS for ≥20 vendors

#### Cost Estimation (NEW)
- **Location**: Sidebar > Below solver checkbox, above optimization button
- **Displays**:
  - Minimum vehicles required (based on capacity constraints)
  - Approximate total distance (rough estimate)
- **Updates**: Automatically recalculates when data or parameters change
- **Visibility**: Only shown when data is loaded

### 3. Optimization Execution

#### Run Optimization Button
- **Location**: Sidebar > Bottom of parameters section
- **States**:
  - Disabled (gray) - No data loaded or parameters invalid
  - Enabled (black) - Ready to optimize
- **Pre-validation**: Checks depot hours before running
- **Feedback**: Shows loading indicator during optimization

### 4. Results Visualization

#### Route Display
- **Interactive Map**: Folium-based map with multiple tile layers
- **Route Layers**: Each route as separate selectable layer
- **Tooltips**: Hover over routes to see cargo, distance, duration, capacity
- **Statistics Panel**: Summary of routes, total distance, time, vehicles

#### Export Options (NEW)
- **Location**: Results area > Top of results (only visible after optimization)
- **Options**:
  1. **Download CSV**: Current solution as CSV file
  2. **Save with Custom Name**: Opens modal to name the run
  3. **Export to Excel**: Placeholder for future implementation
  4. **Download Map as Image**: Placeholder for future implementation

### 5. Route Operations

#### Add Delivery to Route
- **Location**: Results area > Route Operations (only visible after optimization)
- **Input**: Vendor name or ID
- **Functionality**: Adds new delivery to optimal route
- **History**: Saves state before operation for undo capability

#### Remove Delivery from Route
- **Location**: Results area > Route Operations
- **Input**: Vendor name or ID
- **Functionality**: Removes delivery from current route plan
- **History**: Saves state before operation for undo capability

#### Truck Breakdown
- **Location**: Results area > Route Operations
- **Input**: Select route number from dropdown
- **Functionality**: Redistributes deliveries from broken-down truck
- **Confirmation**: Requires user confirmation before executing
- **History**: Saves state before operation for undo capability

#### Undo/Redo Controls (NEW)
- **Location**: Results area > Route Operations
- **Buttons**: Undo (◀) and Redo (▶)
- **Functionality**: Navigate through operation history
- **State Management**: Maintains full history stack of all changes
- **Button States**: Automatically enables/disables based on history position

### 6. Route Details Modal (NEW)
- **Trigger**: Click "Route Details" button in results
- **Content**:
  - Stop-by-stop breakdown of selected route
  - Vendor names, addresses, cargo details
  - Arrival times and service windows
- **Close Methods**: Close button, backdrop click, ESC key

### 7. Save Run Dialog (NEW)
- **Trigger**: Click "Save with Custom Name" in export section
- **Input**: Custom name for the optimization run
- **Default**: Auto-generates timestamp-based name if empty
- **Close Methods**: Close button, backdrop click, ESC key

## Saved Runs Management

### Runs Table
- **Location**: Saved Runs tab
- **Columns**:
  - Checkbox (for comparison selection)
  - Run name
  - Run ID (monospace font)
  - Creation timestamp
  - Solver type used
  - Action buttons

### Run Actions
1. **View Map**: Opens interactive map in new tab
2. **Download Input**: Downloads original CSV dataset
3. **Re-run**: Loads data and parameters for re-optimization

### Comparison Mode (NEW)

#### Selection Interface
- **Checkboxes**: Select multiple runs from table
- **Select All**: Header checkbox to select/deselect all runs
- **Counter**: Shows number of runs selected
- **Compare Button**: Appears when 2+ runs selected

#### Comparison Display
- **Layout**: Side-by-side table comparing selected runs
- **Metrics Compared**:
  - Number of routes
  - Total distance (km)
  - Total time (hours)
  - Vendors routed
  - Solver used
- **Close**: "Close" button returns to runs table

## User Interface Features

### Sidebar Toggle
- **Button**: Left side of screen, subtle black background
- **States**: "Hide Sidebar ◀" / "Show Sidebar ▶"
- **Auto-hide**: Sidebar automatically hidden on Saved Runs tab
- **Responsive**: Adjusts content area width dynamically

### Tab Navigation
- **Tabs**:
  1. Optimizer - Main optimization interface
  2. Saved Runs - Historical runs management
- **Styling**: Black active tab, gray inactive tabs
- **Enterprise Badge**: "INTELLIGENT ROUTER" badge in header (black)

### Color Scheme
- **Primary**: Black (#000000)
- **Background**: Light gray (#F5F5F5)
- **Surface**: White (#FFFFFF)
- **Accent Success**: Green (#2E7D32)
- **Accent Warning**: Orange (#F57C00)
- **Text**: Dark gray (#2C2C2C)
- **Borders**: Light gray (#E0E0E0)

### Tooltips
- **Appearance**: Dark background, white text, small font
- **Position**: Above or below element with arrow
- **Content**: Brief helpful hints about parameters
- **Applied To**:
  - Depot Start time
  - Max Driving hours
  - Max Weight capacity

### Modals
- **Backdrop**: Semi-transparent with blur effect
- **Content**: Centered white card with rounded corners
- **Animation**: Fade-in effect on show
- **Close Options**: Close button, backdrop click, ESC key

## Data Flow

### Upload/Load → Summary → Estimates → Optimize → Results → Operations → History

1. **Data Input**: CSV upload or past run loading
2. **Summary Update**: Calculates totals (vendors, weight, volume)
3. **Cost Estimation**: Estimates minimum vehicles and distance
4. **Optimization**: Runs solver with current parameters
5. **Results Display**: Shows routes, map, statistics
6. **Route Operations**: Modify solution (add/remove/breakdown)
7. **History Management**: Undo/redo through changes
8. **Export/Save**: Download or archive final solution

### 3. Results Filtering (NEW)

#### Linked Filter System
- **Location**: Results panel > Top of route list
- **Components**: Three filter dropdowns: Week, Vendor, Route
- **Behavior**: Filters are interdependent (selecting one constrains the others)
  - Select a **Week**: Only vendors and routes in that week appear in related dropdowns
  - Select a **Vendor**: Only weeks containing that vendor and routes serving that vendor appear
  - Select a **Route**: Only the week and vendors on that route appear
- **Visual Feedback**: 
  - "All weeks/vendors/routes" options always present at top of each dropdown (shows count of filtered items)
  - Available options gray out or disappear if no related data exists
  - Route summary cards update immediately to show only filtered routes

#### Cascade Reset Behavior
- **Reset Button**: "All weeks", "All vendors", "All routes" act as reset buttons
- **Cascade Effect**: Selecting "All" in one filter resets all other filters to "All"
- **Example**: Select "All vendors" → all filters reset to show all weeks, vendors, and routes

#### Route Summary Cards
- **Display**: Filtered route cards show:
  - Route number (e.g., "Route 3")
  - Stop count
  - Total cargo weight (kg)
  - Total volume (m³)
  - Total distance (km)
  - Capacity utilization percentages
  - List of vendors on route
- **Visibility**: Cards automatically hidden/shown based on active filters
- **No Toggle Sync**: Route cards no longer toggle on map layer clicks (decoupled for UX clarity)

#### Interactive Map Integration
- **Route Visibility**: Map route layers correspond to filtered route selection
- **Interaction**: Click route layer to highlight on map; route card displays related metadata
- **Layers**: Color-coded by route; OSRM real-road routing with polyline display

## Keyboard Shortcuts

- **ESC**: Close any open modal (Save Dialog, Route Details, Comparison Results)

## State Management

### Global Variables
- `vendors`: Array of vendor data loaded from CSV
- `csvFilepath`: Path to current CSV file
- `currentSolutionData`: Full optimization result object
- `operationHistory`: Array of solution states for undo/redo
- `historyIndex`: Current position in history stack
- `selectedRunsForComparison`: Set of run IDs selected for comparison

### Persistence
- **Geocode Cache**: Persistent CSV cache (never deleted)
- **Distance Matrix Cache**: JSON cache in `cache/` directory
- **Optimization Results**: Saved in `results/runs/` with metadata
- **Input CSV**: Saved in `uploads/` directory

## API Endpoints Used

### Core Endpoints
- `POST /api/upload-csv` - Upload vendor CSV
- `POST /api/optimize` - Run optimization
- `GET /api/runs` - Fetch all saved runs
- `GET /api/runs/<run_id>/data.json` - Load run metadata
- `GET /api/runs/<run_id>/input.csv` - Download input CSV

### Operation Endpoints
- `POST /api/route/add-stop-state` - Add delivery to route
- `POST /api/route/remove-stop-state` - Remove delivery from route

### Export Endpoints
- `GET /api/download-solution` - Download current solution CSV
- `GET /api/runs/download-input/<run_id>` - Download run input CSV
- `POST /api/runs/compare` - Compare multiple runs (NEW)

### New Endpoints (Future Implementation)
- `POST /api/export/excel` - Export solution to Excel
- `POST /api/export/map-image` - Generate map image

## Browser Compatibility

- **Recommended**: Chrome 90+, Firefox 88+, Safari 14+, Edge 90+
- **Required Features**:
  - ES6+ JavaScript (arrow functions, async/await, template literals)
  - CSS Grid and Flexbox
  - Fetch API
  - CSS Variables
  - CSS backdrop-filter (for modal blur)

## Performance Considerations

- **Cost Estimation**: O(n) calculation, negligible impact
- **History Management**: Deep copies on state changes, minimal memory for typical use
- **Comparison Mode**: Fetches full run data only when comparing
- **Data Summary**: Recalculated only on data load/modification
- **Map Rendering**: Handled by Folium library, lazy-loaded tile layers

## Future Enhancements

### Planned Features (Not Yet Implemented)
1. **Excel Export**: Backend endpoint to generate XLSX with formatted routes
2. **Map Image Export**: Capture map as PNG/PDF for reports
3. **Parameter Presets**: Save/load common parameter configurations
4. **Real-time Cost Estimation**: More accurate distance/time predictions using routing API
5. **Comparison Charts**: Visual charts comparing run metrics
6. **Advanced Filters**: Filter vendors by region, time window, cargo type
7. **Multi-depot Support**: Optimize with multiple depot locations

## Troubleshooting

### Common Issues

**Issue**: "Depot start time must be before close time" error  
**Solution**: Adjust depot hours in parameters, ensure start < close

**Issue**: Export buttons not visible  
**Solution**: Must run optimization first; buttons only appear after successful optimization

**Issue**: Undo/Redo buttons disabled  
**Solution**: Perform at least one route operation to build history; undo disabled at start, redo disabled at end

**Issue**: Comparison button not appearing  
**Solution**: Select 2 or more runs using checkboxes in Saved Runs tab

**Issue**: Cost estimates showing "-"  
**Solution**: Load CSV data first; estimates only calculate when vendor data is available

**Issue**: Save modal not closing with ESC  
**Solution**: Known browser compatibility issue; use Close button or click backdrop

## Version History

### v2.0.0 (Current)
- Added 12 new features:
  1. Download current solution CSV
  2. Save with custom name (modal dialog)
  3. Data summary card (vendors, weight, volume)
  4. Clear data button
  5. Parameter validation with visual feedback
  6. Better operation feedback (undo/redo)
  7. Undo/Redo history management
  8. Export options section (Excel, map image placeholders)
  9. Comparison mode for saved runs
  10. Route details modal
  11. Cost estimation display
  12. Tooltips on key parameters

### v1.0.0 (Previous)
- Initial release with core features:
  - CSV upload and optimization
  - Route operations (add/remove/breakdown)
  - Saved runs management
  - Interactive map visualization
  - Black color scheme throughout UI
