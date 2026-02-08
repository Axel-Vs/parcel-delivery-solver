"""Persistent run storage utilities.

Stores and retrieves optimization runs with routes, matrices, and metadata.
Runs are saved under results/runs/<run_id>/.
"""
from __future__ import annotations
import os
import json
from datetime import datetime
from typing import Dict, Any, List, Optional

RUNS_ROOT = os.path.join('results', 'runs')


def _ensure_runs_root():
    os.makedirs(RUNS_ROOT, exist_ok=True)


def _run_path(run_id: str) -> str:
    return os.path.join(RUNS_ROOT, str(run_id))


def _write_json(path: str, obj: Any) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2)


def _read_json(path: str) -> Any:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def generate_run_id(prefix: Optional[str] = None) -> str:
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    return f"{prefix+'_' if prefix else ''}{ts}"


def save_run(run_id: str, name: str, state: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Save a run to disk.

    state keys: routes, distance_matrix, capacity_matrix, loading_matrix, frozen_prefix (optional)
    metadata may include: solver_type, period, num_vendors, csv_filepath, created_at, base_run_id, map_path
    """
    _ensure_runs_root()
    run_dir = _run_path(run_id)
    os.makedirs(run_dir, exist_ok=True)

    # Normalize state types for JSON
    norm_state = {}
    for key in ['routes', 'distance_matrix', 'capacity_matrix', 'loading_matrix', 'linear_length_matrix', 'frozen_prefix']:
        if key in state and state[key] is not None:
            norm_state[key] = state[key]

    meta = dict(metadata)
    meta.update({
        'run_id': run_id,
        'name': name,
        'created_at': metadata.get('created_at') or datetime.now().isoformat(),
    })

    _write_json(os.path.join(run_dir, 'run.json'), meta)
    _write_json(os.path.join(run_dir, 'state.json'), norm_state)

    print(f"\n=== RUN STORAGE DEBUG ({run_id}) ===")
    print(f"Run directory: {run_dir}")
    print(f"Map path in metadata: {metadata.get('map_path')}")
    print(f"CSV path in metadata: {metadata.get('csv_filepath')}")

    # If a map exists, copy or reference; store path only
    if 'map_path' in metadata and metadata['map_path']:
        # Optionally copy map into run folder
        try:
            import shutil
            target_map = os.path.join(run_dir, 'map.html')
            print(f"Attempting to copy map from: {metadata['map_path']}")
            print(f"Map source exists: {os.path.exists(metadata['map_path'])}")
            if os.path.exists(metadata['map_path']):
                shutil.copyfile(metadata['map_path'], target_map)
                print(f"✓ Map copied to: {target_map}")
                meta['map_path'] = target_map
                _write_json(os.path.join(run_dir, 'run.json'), meta)
            else:
                print(f"✗ Map source does not exist!")
        except Exception as e:
            print(f"✗ Failed to copy map: {e}")
    else:
        print(f"✗ No map_path in metadata or map_path is None/empty")

    # If a CSV exists, copy it into the run folder as input.csv
    if 'csv_filepath' in metadata and metadata['csv_filepath']:
        try:
            import shutil
            src = metadata['csv_filepath']
            print(f"Attempting to copy CSV from: {src}")
            print(f"CSV source exists: {os.path.exists(src)}")
            if os.path.exists(src):
                target_csv = os.path.join(run_dir, 'input.csv')
                shutil.copyfile(src, target_csv)
                print(f"✓ CSV copied to: {target_csv}")
                meta['input_csv_path'] = target_csv
                _write_json(os.path.join(run_dir, 'run.json'), meta)
            else:
                print(f"✗ CSV source does not exist!")
        except Exception as e:
            print(f"✗ Failed to copy CSV: {e}")
    else:
        print(f"✗ No csv_filepath in metadata or csv_filepath is None/empty")

    print(f"=== END RUN STORAGE DEBUG ===\n")

    return {'success': True, 'run_id': run_id, 'name': name}


def list_runs() -> List[Dict[str, Any]]:
    _ensure_runs_root()
    runs = []
    for child in sorted(os.listdir(RUNS_ROOT)):
        run_dir = os.path.join(RUNS_ROOT, child)
        if not os.path.isdir(run_dir):
            continue
        run_meta_path = os.path.join(run_dir, 'run.json')
        try:
            meta = _read_json(run_meta_path)
            runs.append(meta)
        except Exception:
            # skip invalid entries
            continue
    # Sort newest first
    runs.sort(key=lambda m: m.get('created_at', ''), reverse=True)
    return runs


def load_run(run_id: str) -> Dict[str, Any]:
    run_dir = _run_path(run_id)
    run_meta_path = os.path.join(run_dir, 'run.json')
    state_path = os.path.join(run_dir, 'state.json')
    if not os.path.exists(run_meta_path) or not os.path.exists(state_path):
        return {'success': False, 'error': 'Run not found'}
    meta = _read_json(run_meta_path)
    state = _read_json(state_path)
    # Include map path if present
    map_path = os.path.join(run_dir, 'map.html')
    if os.path.exists(map_path):
        meta['map_path'] = map_path
    return {'success': True, 'run_id': run_id, 'metadata': meta, 'state': state}
