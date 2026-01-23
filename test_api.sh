#!/bin/bash

# Start Flask server in background
cd /Users/axelvargas/Documents/Axel/parcel_delivery/parcel-delivery-solver
source parcel_env/bin/activate

# Remove old server if running
lsof -ti:8080 | xargs kill -9 2>/dev/null || true

# Start server
python app.py &
SERVER_PID=$!

# Wait for server to start
sleep 3

# Test with small dataset
echo "Testing with small dataset..."
curl -s -F "file=@data/amazon_test_dataset_small.csv" http://localhost:8080/api/upload-csv | head -20

# Send optimization request
echo ""
echo "Sending optimization request..."
curl -s -X POST http://localhost:8080/api/optimize \
  -H "Content-Type: application/json" \
  -d @- << 'EOF' | python -m json.tool | head -60
{
  "vendors": [],
  "csv_filepath": "uploads/processed_20260113_111226.csv",
  "parameters": {
    "max_driving": 67,
    "max_weight": 30,
    "max_ldms": 70
  }
}
EOF

# Kill server
kill $SERVER_PID 2>/dev/null || true
