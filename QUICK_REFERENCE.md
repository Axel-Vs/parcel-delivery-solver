# ⚡ Quick Reference Card

**Save this page in your bookmarks or print it!**

---

## 🚀 Start Server
```bash
cd /Users/axelvargas/Documents/Axel/parcel_delivery/parcel-delivery-solver
source parcel_env/bin/activate
python app.py
# → http://localhost:8080
```

---

## 📊 CSV Requirements
```
Columns needed:
✓ Vendor Name
✓ Vendor Gross Weight [kg]
✓ Vendor Volume in m3 [m³]
✓ Vendor Linear Length [m]
✓ Requested Loading Date [YYYY-MM-DD HH:MM:SS]
✓ Requested Delivery Date [YYYY-MM-DD HH:MM:SS]
✓ Vendor Street, Vendor City, Vendor Postcode
```

---

## ⚙️ Key Parameters
| Name | Default | Min | Max | Units |
|------|---------|-----|-----|-------|
| **max_driving** | 67 | 67 | 200 | hours |
| **loading** | 2 | 0.5 | 4 | hours/stop |
| **driving_starts** | 6 | 0 | 10 | hours |
| **driving_stop** | 21 | 18 | 24 | hours |
| **vehicle_capacity_kg** | 5000 | 100 | 10000 | kg |
| **vehicle_capacity_ldms** | 90 | 10 | 100 | m³ |
| **max_linear_length** | 16.1 | 5 | 25 | m |

---

## 📋 Constraint Formula
**max_driving ≥ (travel_time + service_time)**

Where:
- `travel_time` = sum(vendor→vendor + vendor→depot) in hours
- `service_time` = num_stops × loading (hours)
- `max_driving` must be ≥67 for US multi-state routes

---

## 🗂️ Key Files
| File | Purpose |
|------|---------|
| `app.py` | Flask server (main entry) |
| `model/optimizer/alns_solver.py` | Optimization engine |
| `web/index.html` | Web UI |
| `data/amazon_test_dataset_*.csv` | Test datasets |

---

## 📚 Documentation
| Read This | For This |
|-----------|----------|
| **GETTING_STARTED.md** | First time setup ← START HERE |
| **README.md** | Features & overview |
| **ARCHITECTURE.md** | How the algorithm works |
| **FEATURES.md** | Detailed feature docs |
| **CHANGELOG.md** | What changed recently |
| **DEPLOYMENT.md** | Production deployment |

---

## 🎯 Typical Workflow
1. Upload CSV via web UI
2. Set `max_driving` = 67-150 (depends on geography)
3. Click "Optimize"
4. Review results in route cards + map
5. Save for future reference

---

## 🐛 Quick Fixes
| Problem | Solution |
|---------|----------|
| Server won't start | `lsof -ti:8080 \| xargs kill -9` then retry |
| "Connection refused" | Wait 5 seconds for Flask initialization |
| TIME column missing | Hard refresh: Cmd+Shift+R |
| Routes too long | Increase `max_driving` parameter |
| Optimization failed | Check `max_driving ≥ 67` if US data |

---

## 📊 Performance Expectations
| Vendors | Solver | Time | Routes | Vehicles Saved |
|---------|--------|------|--------|----------------|
| 8 | MIP | ~20s | 10-15 | 30-40% |
| 30 | ALNS | ~45s | 20-25 | 40-50% |
| 58 | ALNS | ~90s | 25-35 | 47-56% |

---

## 🎓 How It Works (30-second version)
1. **Input**: CSV with vendors & packages
2. **Clustering**: K-Medoids groups vendors by travel time distance
3. **Initial Routes**: Greedy insertion creates feasible routes per cluster
4. **Optimization**: ALNS improve routes for 2500 iterations
5. **Merging**: Combine routes while respecting constraints
6. **Output**: Map + route details with TIME metrics

---

## 💡 Pro Tips
- Use **small dataset** first to understand UI
- **max_driving = 69-75** gives best results for 58 US vendors
- **Clustering** creates ~3 vendors per cluster automatically
- **TIME** = driving + loading (visible in route cards)
- Results **auto-saved** to `results/runs/`

---

Last updated: January 2026 | System: K-Medoids ALNS Optimization
