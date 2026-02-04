"""Lightweight route-level insert/remove utilities.

These helpers avoid full re-optimization. They operate on route lists and cached
matrices, respecting capacity limits and optional frozen prefixes (immutable
sections of each route, e.g., legs already in progress).
"""
from typing import List, Optional, Tuple, Dict, Any
import math

Depot = 0


def _is_depot(node_id: int, depot_node_ids=None) -> bool:
    depot_nodes = set(depot_node_ids or [Depot])
    return node_id in depot_nodes


def _route_load(route: List[int], capacity_matrix, loading_matrix, depot_node_ids=None) -> Tuple[float, float]:
    weight = sum(capacity_matrix[node] for node in route if not _is_depot(node, depot_node_ids))
    volume = sum(loading_matrix[node] for node in route if not _is_depot(node, depot_node_ids))
    return float(weight), float(volume)


def remove_stop(
    routes: List[List[int]],
    stop: int,
    depot_node_ids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Remove a stop from the first route that contains it.

    Returns a copy of routes and metadata. If the stop is not found, returns
    success=False with a message.
    """
    new_routes = []
    found = False
    removed_from = None
    for idx, route in enumerate(routes):
        if stop in route:
            found = True
            removed_from = idx
            new_route = [node for node in route if node != stop or node == Depot]
            # Ensure route still ends at depot
            if new_route and not _is_depot(new_route[-1], depot_node_ids):
                fallback_depot = (depot_node_ids or [Depot])[0]
                new_route.append(fallback_depot)
            # Avoid empty route; keep at least depot
            if not new_route:
                new_route = [Depot]
            new_routes.append(new_route)
        else:
            new_routes.append(list(route))
    return {
        "success": found,
        "routes": new_routes,
        "route_index": removed_from,
        "message": "stop removed" if found else "stop not found",
    }


def insert_stop_best_position(
    routes: List[List[int]],
    new_stop: int,
    distance_matrix,
    capacity_matrix,
    loading_matrix,
    max_capacity_kg: float,
    max_ldms_vc: float,
    frozen_prefix: Optional[List[int]] = None,
    allow_new_route: bool = True,
    depot_node_ids: Optional[List[int]] = None,
    # Time feasibility (optional)
    time_matrix=None,
    earliest=None,
    latest=None,
    start_time_seconds: float = 0.0,
) -> Dict[str, Any]:
    """Insert a stop into the cheapest feasible position across routes.

    - Respects capacity (weight & volume).
    - Respects frozen_prefix: positions strictly before frozen_prefix[idx]
      are immutable for that route.
    - If no feasible insertion and allow_new_route, opens [0, new_stop, 0].
    - Returns success flag, updated routes, and insertion metadata.
    """
    if frozen_prefix is None:
        frozen_prefix = [0] * len(routes)

    best_delta = math.inf
    best_route_idx = None
    best_pos = None
    best_route_copy = None

    for r_idx, route in enumerate(routes):
        fp = frozen_prefix[r_idx] if r_idx < len(frozen_prefix) else 0
        # route must end with depot; insertion positions are after fp up to before first depot
        if len(route) < 2:
            continue
        end_limit = len(route)
        for i, node in enumerate(route):
            if _is_depot(node, depot_node_ids) and i > 0:
                end_limit = i
                break
        for pos in range(max(fp + 1, 1), end_limit):  # insert before route[pos]
            prev_node = route[pos - 1]
            next_node = route[pos]
            delta = (
                distance_matrix[prev_node][new_stop]
                + distance_matrix[new_stop][next_node]
                - distance_matrix[prev_node][next_node]
            )

            # Capacity check
            weight, volume = _route_load(route, capacity_matrix, loading_matrix, depot_node_ids)
            weight += capacity_matrix[new_stop]
            volume += loading_matrix[new_stop]
            if weight > max_capacity_kg or volume > max_ldms_vc:
                continue

            # Basic time-window check for new_stop only (optional)
            if time_matrix is not None and earliest is not None and latest is not None:
                # compute arrival time at insertion position by summing travel time from start
                arr = start_time_seconds
                # Sum times up to prev_node
                for i in range(1, pos):
                    a = route[i - 1]
                    b = route[i]
                    arr += time_matrix[a][b]
                # travel to new_stop
                arr += time_matrix[prev_node][new_stop]
                # Check window
                e = earliest[new_stop] if new_stop < len(earliest) else None
                l = latest[new_stop] if new_stop < len(latest) else None
                if e is not None and l is not None:
                    if not (e <= arr <= l):
                        continue

            if delta < best_delta:
                best_delta = delta
                best_route_idx = r_idx
                best_pos = pos
                best_route_copy = route

    new_routes = [list(r) for r in routes]
    if best_route_idx is not None:
        new_route = list(best_route_copy)
        new_route.insert(best_pos, new_stop)
        new_routes[best_route_idx] = new_route
        return {
            "success": True,
            "routes": new_routes,
            "route_index": best_route_idx,
            "position": best_pos,
            "delta_distance": best_delta,
            "opened_new_route": False,
        }

    if allow_new_route:
        fallback_depot = (depot_node_ids or [Depot])[0]
        new_routes.append([Depot, new_stop, fallback_depot])
        return {
            "success": True,
            "routes": new_routes,
            "route_index": len(new_routes) - 1,
            "position": 1,
            "delta_distance": None,
            "opened_new_route": True,
        }

    return {
        "success": False,
        "routes": routes,
        "message": "No feasible insertion (capacity/time window constraints)",
    }
