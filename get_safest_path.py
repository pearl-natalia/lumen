# get_path.py — safest *walk* route using incidents + cameras (no collisions in cost)
import os, json, math
from datetime import datetime
from dateutil import parser as dtparse
from itertools import pairwise

import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString
import requests
import osmnx as ox
import networkx as nx
from dotenv import load_dotenv

# ---------------------------
# CONFIG
# ---------------------------

INCIDENTS_CSV   = "sources/incidents.csv"         # incident_id, posted_on, incident_date, location, city, ...
RL_CAMS_CSV     = "sources/red_light_cameras.csv" # city, approach_direction, primary_road, cross_street_or_notes
SPD_CAMS_CSV    = "sources/speed_cameras.csv"     # city, approach_direction, primary_road, cross_street_or_notes
GEOCODE_CACHE   = "sources/geocode_cache.csv"     # created/updated automatically

# Load .env that sits beside this file
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
MAPBOX_TOKEN = os.getenv("MAPBOX_TOKEN")
if not MAPBOX_TOKEN:
    raise RuntimeError("MAPBOX_TOKEN not found. Add it to .env next to get_path.py")

# CRS in meters for KW area (UTM 17N)
TARGET_CRS = "EPSG:32617"

# Radii (meters) and weights
R_CAMERA   = 60
R_INC      = 120
B_INCIDENT = 1.20   # risk ↑ with incidents
C_CAMERA   = 0.30   # benefit ↓ with cameras

# Night settings
IS_NIGHT             = True
NIGHT_RISK_MULT      = 1.25
NIGHT_CAMERA_MULT    = 1.35

# Incident decay constant (hours)
TAU_H = 12.0

# Graph area buffer (meters)
DIST_BUFFER_M = 3000

# Output
SAVE_GEOJSON = "safest_route.geojson"

# --- Walking speed (meters/second) ---
WALK_SPEED_MPS = 1.33   # ~4.8 km/h

# ---------------------------
# Helpers
# ---------------------------
def ensure_latlon(place_or_pair):
    if isinstance(place_or_pair, (tuple, list)) and len(place_or_pair) == 2:
        return float(place_or_pair[0]), float(place_or_pair[1])
    return ox.geocode(place_or_pair)  # (lat, lon)

def load_cache(path):
    if os.path.exists(path):
        df = pd.read_csv(path)
        return dict(zip(df["q"], zip(df["lon"], df["lat"])))
    return {}

def save_cache(cache, path):
    if not cache:
        return
    rows = [{"q": q, "lon": lon, "lat": lat} for q, (lon, lat) in cache.items()]
    pd.DataFrame(rows).drop_duplicates("q").to_csv(path, index=False)

def mapbox_geocode(query, proximity=None):
    url = f"https://api.mapbox.com/geocoding/v5/mapbox.places/{requests.utils.quote(query)}.json"
    params = {"access_token": MAPBOX_TOKEN, "limit": 1}
    if proximity:
        params["proximity"] = f"{proximity[1]},{proximity[0]}"  # lon,lat
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    js = r.json()
    if js.get("features"):
        lon, lat = js["features"][0]["center"]
        return (lon, lat)
    return None

def read_cameras(rl_path, spd_path, prox_latlon):
    cache = load_cache(GEOCODE_CACHE)
    cams = []

    def fmt_q(city, primary, cross):
        p = str(primary).strip()
        c = str(cross).strip()
        city = str(city).strip()
        return f"{p} & {c}, {city}" if c and c.lower() != "nan" else f"{p}, {city}"

    for path, ctype in [(rl_path, "red_light"), (spd_path, "speed")]:
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path)
        for _, row in df.iterrows():
            q = fmt_q(row.get("city", ""), row.get("primary_road", ""), row.get("cross_street_or_notes", ""))
            if not q:
                continue
            coord = cache.get(q) or mapbox_geocode(q, prox_latlon)
            if coord:
                cache[q] = coord
                lon, lat = coord
                cams.append({"type": ctype, "lon": lon, "lat": lat})
    save_cache(cache, GEOCODE_CACHE)
    if not cams:
        return gpd.GeoDataFrame(columns=["type", "geometry"], geometry="geometry", crs="EPSG:4326")
    return gpd.GeoDataFrame(
        cams,
        geometry=gpd.points_from_xy([c["lon"] for c in cams], [c["lat"] for c in cams]),
        crs="EPSG:4326"
    )

def read_incidents(path, prox_latlon):
    if not os.path.exists(path):
        return gpd.GeoDataFrame(columns=["geometry", "_t"], geometry="geometry", crs="EPSG:4326")
    df = pd.read_csv(path)
    cache = load_cache(GEOCODE_CACHE)

    def parse_time(s):
        if not isinstance(s, str):
            return None
        try:
            return dtparse.parse(s, fuzzy=True)
        except Exception:
            return None

    coords, times = [], []
    for _, row in df.iterrows():
        loc = str(row.get("location", "")).strip().strip('"')
        city = str(row.get("city", "")).strip().strip('"')
        if not loc:
            continue
        q = f"{loc}, {city}" if city else loc
        coord = cache.get(q) or mapbox_geocode(q, prox_latlon)
        if coord:
            cache[q] = coord
            coords.append(coord)
            times.append(parse_time(row.get("incident_date") or row.get("posted_on")))
    save_cache(cache, GEOCODE_CACHE)
    if not coords:
        return gpd.GeoDataFrame(columns=["geometry", "_t"], geometry="geometry", crs="EPSG:4326")
    gdf = gpd.GeoDataFrame(
        pd.DataFrame({"_t": times}),
        geometry=gpd.points_from_xy([c[0] for c in coords], [c[1] for c in coords]),
        crs="EPSG:4326"
    )
    return gdf

def counts_near_edges(edges_gdf, pts_gdf, radius_m, values=None):
    if pts_gdf.empty:
        return pd.Series(0.0, index=edges_gdf.index)
    buf = pts_gdf.copy()
    buf["geometry"] = buf.buffer(radius_m)  # radius in meters because we reproject to meters first
    if values is None:
        hit = gpd.sjoin(edges_gdf[["geometry"]], buf[["geometry"]], how="left", predicate="intersects")
        agg = hit.groupby(hit.index).size().astype(float)
    else:
        tmp = buf.copy()
        tmp["_val"] = values.values
        hit = gpd.sjoin(edges_gdf[["geometry"]], tmp[["_val", "geometry"]], how="left", predicate="intersects")
        agg = hit.groupby(hit.index)["_val"].sum()
    out = pd.Series(0.0, index=edges_gdf.index)
    out.loc[agg.index] = agg.values
    return out

def incident_decay(dt_val):
    if not isinstance(dt_val, datetime):
        return 1.0
    age_h = (datetime.now() - dt_val).total_seconds() / 3600.0
    age_h = max(age_h, 0.0)
    return math.exp(-age_h / TAU_H)

def best_parallel_key(G, u, v):
    # choose parallel edge with smallest weight (fallback to length)
    keys = list(G[u][v].keys())
    return min(keys, key=lambda k: G[u][v][k].get("weight", G[u][v][k].get("length", 1.0)))

# ---------------------------
# MAIN
# ---------------------------
def get_safest_path(origin, dest):
    print('computing...')
    o_lat, o_lon = ensure_latlon(origin)
    d_lat, d_lon = ensure_latlon(dest)
    proximity = (o_lat, o_lon)

    # Build walk graph around both ends if needed
    ox.settings.use_cache = True
    ox.settings.log_console = False
    G1 = ox.graph_from_point((o_lat, o_lon), dist=DIST_BUFFER_M, network_type="walk", simplify=True)
    if ox.distance.great_circle(o_lat, o_lon, d_lat, d_lon) > DIST_BUFFER_M * 0.8:
        G2 = ox.graph_from_point((d_lat, d_lon), dist=DIST_BUFFER_M, network_type="walk", simplify=True)
        G = nx.compose(G1, G2)
    else:
        G = G1

    # Graph → GeoDataFrames
    nodes, edges = ox.graph_to_gdfs(G, nodes=True, edges=True, fill_edge_geometry=True)

    # Project to meters
    edges = edges.to_crs(TARGET_CRS)
    inc   = read_incidents(INCIDENTS_CSV, proximity).to_crs(TARGET_CRS)
    cams  = read_cameras(RL_CAMS_CSV, SPD_CAMS_CSV, proximity).to_crs(TARGET_CRS)

    # Trim to graph bbox (perf)
    minx, miny, maxx, maxy = edges.total_bounds
    inc  = inc.cx[minx:maxx, miny:maxy]
    cams = cams.cx[minx:maxx, miny:maxy]

    # Edge signals
    inc_weight = inc["_t"].apply(lambda t: incident_decay(t) if pd.notna(t) else 1.0)
    if IS_NIGHT:
        inc_weight = inc_weight * NIGHT_RISK_MULT
    edges["sum_inc"] = counts_near_edges(edges, inc, R_INC, values=inc_weight)

    cam_vals = pd.Series(1.0, index=cams.index) if not cams.empty else None
    edges["cnt_cam"] = counts_near_edges(edges, cams, R_CAMERA, values=cam_vals)
    if IS_NIGHT:
        edges["cnt_cam"] = edges["cnt_cam"] * NIGHT_CAMERA_MULT

    # Edge weights
    def edge_cost(row):
        length = float(row.get("length", row.geometry.length))  # meters
        up = 1.0 + B_INCIDENT * row["sum_inc"]
        dn = 1.0 + C_CAMERA * row["cnt_cam"]
        return max(0.1, length * (up / dn))

    edges["weight"] = edges.apply(edge_cost, axis=1)

    # Map weights back to G via (u, v, key) MultiIndex (osmnx ≥ 2.0)
    weights_by_edge = {}
    for idx, w in edges["weight"].items():  # idx is (u, v, key)
        weights_by_edge[idx] = float(w)

    for u, v, k, data in G.edges(keys=True, data=True):
        w = weights_by_edge.get((u, v, k))
        if w is None:
            # Fallback: pick closest matching edge row by (u,v) and length
            try:
                candidates = edges.loc[(u, v)]
                if isinstance(candidates, pd.Series):  # single row
                    w = float(candidates["weight"])
                else:
                    geom_len = data.get("length", data.get("geometry").length if "geometry" in data else 1.0)
                    j = (candidates["length"] - geom_len).abs().sort_values().index[0]
                    w = float(candidates.loc[j, "weight"])
            except Exception:
                w = float(data.get("weight", data.get("length", 1.0)))
        data["weight"] = w

    # Route
    o_node = ox.distance.nearest_nodes(G, X=o_lon, Y=o_lat)
    d_node = ox.distance.nearest_nodes(G, X=d_lon, Y=d_lat)
    route = nx.shortest_path(G, o_node, d_node, weight="weight")
    if len(route) < 2:
        raise RuntimeError("No walkable route found between origin and destination within the fetched graph area.")

    # Coordinates & metrics
    coords = [(G.nodes[n]["x"], G.nodes[n]["y"]) for n in route]
    trip = [(u, v, best_parallel_key(G, u, v)) for u, v in pairwise(route)]
    total_len  = sum(G[u][v][k].get("length", 0.0) for u, v, k in trip)
    total_cost = sum(G[u][v][k].get("weight", 0.0) for u, v, k in trip)
    time_s     = float(total_len) / WALK_SPEED_MPS  # <-- added (seconds)

    print(f"Safest route: {len(route)-1} segments, {total_len:.0f} m, cost={total_cost:.0f}, time≈{time_s/60:.1f} min")

    # Export GeoJSON (lon,lat)
    feature = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {
            "name": "Safest Night Walk",
            "length_m": float(total_len),
            "time_s":   time_s,            # <-- added
            "cost":     float(total_cost),
        },
    }
    with open(SAVE_GEOJSON, "w") as f:
        json.dump({"type": "FeatureCollection", "features": [feature]}, f)
    print(f"GeoJSON saved → {SAVE_GEOJSON}")
