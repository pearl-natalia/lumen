# get_path.py (patched)
import os, json, math
from datetime import datetime, timezone
from dateutil import parser as dtparse
from itertools import pairwise  # <-- for iterating route edges

import pandas as pd
import numpy as np
import geopandas as gpd
from shapely.geometry import Point, LineString
import requests
import osmnx as ox
import networkx as nx
from dotenv import load_dotenv

# ---------------------------
# CONFIG
# ---------------------------
ORIGIN = "University of Waterloo, Waterloo, ON"   # or (lat, lon)
DEST   = "Kitchener City Hall, Kitchener, ON"     # or (lat, lon)

# Input CSVs (all inside sources/)
COLLISIONS_CSV  = "sources/collisions.csv"
INCIDENTS_CSV   = "sources/incidents.csv"
RL_CAMS_CSV     = "sources/red_light_cameras.csv"
SPD_CAMS_CSV    = "sources/speed_cameras.csv"
GEOCODE_CACHE   = "sources/geocode_cache.csv"    # will be created if not exists

# Load .env next to this file
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
MAPBOX_TOKEN = os.getenv("MAPBOX_TOKEN")
if not MAPBOX_TOKEN:
    raise RuntimeError("MAPBOX_TOKEN not found. Please add it to .env")

# radii (meters) and weights
R_CAMERA   = 60
R_COLL     = 80
R_INC      = 120

A_COLLISION = 0.25
B_INCIDENT  = 1.20
C_CAMERA    = 0.30

# Night settings
IS_NIGHT             = True
NIGHT_RISK_MULT      = 1.25
NIGHT_CAMERA_MULT    = 1.35
NIGHT_HOURS          = set(list(range(0,6)) + list(range(21,24)))  # 9pm–6am

# Incident decay constant
TAU_H = 12.0  # hours

# Graph area buffer (meters)
DIST_BUFFER_M = 3000

# Output
SAVE_GEOJSON = "safest_route.geojson"

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
    if not cache: return
    rows = [{"q": q, "lon": lon, "lat": lat} for q,(lon,lat) in cache.items()]
    pd.DataFrame(rows).drop_duplicates("q").to_csv(path, index=False)

def mapbox_geocode(query, proximity=None):
    url = f"https://api.mapbox.com/geocoding/v5/mapbox.places/{requests.utils.quote(query)}.json"
    params = {"access_token": MAPBOX_TOKEN, "limit": 1}
    if proximity:
        params["proximity"] = f"{proximity[1]},{proximity[0]}"
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    js = r.json()
    if js.get("features"):
        lon, lat = js["features"][0]["center"]
        return (lon, lat)
    return None

def read_collisions(path):
    df = pd.read_csv(path)
    df = df.dropna(subset=["LATITUDE","LONGITUDE"])
    # severity proxy: +1 if pedestrian involved
    sev = (df["PEDESTRIANINVOLVED"].astype(str).str.lower() == "true").astype(int) + 1
    # night flag
    night_flags = []
    for t in df["TIME"].astype(str):
        try:
            hh = int(str(t).split(":")[0])
        except Exception:
            hh = -1
        night_flags.append(1 if hh in NIGHT_HOURS else 0)
    df["_sev"] = sev
    df["_nightFlag"] = night_flags
    gdf = gpd.GeoDataFrame(df,
        geometry=gpd.points_from_xy(df["LONGITUDE"], df["LATITUDE"]),
        crs="EPSG:4326")
    return gdf

def read_cameras(rl_path, spd_path, prox_latlon):
    cache = load_cache(GEOCODE_CACHE)
    cams = []

    def fmt_q(city, primary, cross):
        p = str(primary).strip()
        c = str(cross).strip()
        city = str(city).strip()
        return f"{p} & {c}, {city}" if c and c.lower() != "nan" else f"{p}, {city}"

    for path, ctype in [(rl_path,"red_light"), (spd_path,"speed")]:
        df = pd.read_csv(path)
        for _, row in df.iterrows():
            q = fmt_q(row.get("city",""), row.get("primary_road",""), row.get("cross_street_or_notes",""))
            if not q: continue
            coord = cache.get(q) or mapbox_geocode(q, prox_latlon)
            if coord:
                cache[q] = coord
                lon, lat = coord
                cams.append({"type": ctype, "lon": lon, "lat": lat})
    save_cache(cache, GEOCODE_CACHE)
    if not cams:
        return gpd.GeoDataFrame(columns=["type","geometry"], geometry="geometry", crs="EPSG:4326")
    return gpd.GeoDataFrame(cams, geometry=gpd.points_from_xy([c["lon"] for c in cams], [c["lat"] for c in cams]), crs="EPSG:4326")

def read_incidents(path, prox_latlon):
    df = pd.read_csv(path)
    cache = load_cache(GEOCODE_CACHE)

    def parse_time(s):
        if not isinstance(s, str): return None
        try:
            return dtparse.parse(s, fuzzy=True)
        except Exception:
            return None

    coords, times = [], []
    for _, row in df.iterrows():
        loc = str(row.get("location","")).strip().strip('"')
        city = str(row.get("city","")).strip().strip('"')
        q = f"{loc}, {city}" if city else loc
        coord = cache.get(q) or mapbox_geocode(q, prox_latlon)
        if coord:
            cache[q] = coord
            coords.append(coord)
            times.append(parse_time(row.get("incident_date") or row.get("posted_on")))
    save_cache(cache, GEOCODE_CACHE)
    gdf = gpd.GeoDataFrame(df.iloc[:len(coords)].copy(),
        geometry=gpd.points_from_xy([c[0] for c in coords],[c[1] for c in coords]),
        crs="EPSG:4326")
    gdf["_t"] = times
    return gdf

def counts_near_edges(edges_gdf, pts_gdf, radius_m, values=None):
    if pts_gdf.empty: 
        return pd.Series(0.0, index=edges_gdf.index)
    buf = pts_gdf.copy()
    buf["geometry"] = buf.buffer(radius_m)
    if values is None:
        hit = gpd.sjoin(edges_gdf[["geometry"]], buf[["geometry"]], how="left", predicate="intersects")
        agg = hit.groupby(hit.index).size().astype(float)
    else:
        tmp = buf.copy()
        tmp["_val"] = values.values
        hit = gpd.sjoin(edges_gdf[["geometry"]], tmp[["_val","geometry"]], how="left", predicate="intersects")
        agg = hit.groupby(hit.index)["_val"].sum()
    out = pd.Series(0.0, index=edges_gdf.index)
    out.loc[agg.index] = agg.values
    return out

def incident_decay(dt_val):
    """Exponential time-decay for live incidents (recent = higher)."""
    if not isinstance(dt_val, datetime):
        return 1.0
    # treat dt_val as local; compare to now (naive ok for decay)
    age_h = (datetime.now() - dt_val).total_seconds() / 3600.0
    age_h = max(age_h, 0.0)
    return math.exp(-age_h / TAU_H)

def route_edge_lists(G, route):
    """Return lists of edge lengths and weights along the route (osmnx>=2.0 safe)."""
    lengths, weights = [], []
    for u, v in pairwise(route):
        # choose *some* edge key (take the first)
        k = next(iter(G[u][v]))
        data = G[u][v][k]
        lengths.append(float(data.get("length", 0.0)))
        weights.append(float(data.get("weight", data.get("length", 0.0))))
    return lengths, weights

# ---------------------------
# MAIN
# ---------------------------
def main():
    o_lat, o_lon = ensure_latlon(ORIGIN)
    d_lat, d_lon = ensure_latlon(DEST)
    proximity = (o_lat, o_lon)

    # Build walkable graph covering both ends
    ox.settings.use_cache = True
    ox.settings.log_console = False
    G1 = ox.graph_from_point((o_lat, o_lon), dist=DIST_BUFFER_M, network_type="walk", simplify=True)
    if ox.distance.great_circle(o_lat, o_lon, d_lat, d_lon) > DIST_BUFFER_M * 0.8:
        G2 = ox.graph_from_point((d_lat, d_lon), dist=DIST_BUFFER_M, network_type="walk", simplify=True)
        G = nx.compose(G1, G2)  # <-- replace ox.utils_graph.graph_union
    else:
        G = G1

    nodes, edges = ox.graph_to_gdfs(G, nodes=True, edges=True, fill_edge_geometry=True)
    crs = edges.crs or "EPSG:3857"

    # load & project layers
    coll = read_collisions(COLLISIONS_CSV).to_crs(crs)
    inc  = read_incidents(INCIDENTS_CSV, proximity).to_crs(crs)
    cams = read_cameras(RL_CAMS_CSV, SPD_CAMS_CSV, proximity).to_crs(crs)

    # collisions
    coll_weight = coll["_sev"].astype(float)
    if IS_NIGHT:
        coll_weight = coll_weight * (1.0 + 0.5*coll["_nightFlag"].astype(float))
    edges["sum_coll"] = counts_near_edges(edges, coll, R_COLL, values=coll_weight)

    # incidents with decay
    inc_weight = inc["_t"].apply(lambda t: incident_decay(t) if pd.notna(t) else 1.0)
    if IS_NIGHT: inc_weight *= NIGHT_RISK_MULT
    edges["sum_inc"] = counts_near_edges(edges, inc, R_INC, values=inc_weight)

    # cameras
    cam_vals = pd.Series(1.0, index=cams.index) if not cams.empty else None
    edges["cnt_cam"] = counts_near_edges(edges, cams, R_CAMERA, values=cam_vals)
    if IS_NIGHT: edges["cnt_cam"] *= NIGHT_CAMERA_MULT

    # compute weights
    def edge_cost(row):
        length = float(row.get("length", row.geometry.length))
        up = 1.0 + A_COLLISION*row["sum_coll"] + B_INCIDENT*row["sum_inc"]
        dn = 1.0 + C_CAMERA*row["cnt_cam"]
        return max(0.1, length * (up/dn))

    edges["weight"] = edges.apply(edge_cost, axis=1)

    # push back to graph
    for u, v, k, data in G.edges(keys=True, data=True):
        geom_len = data.get("length", data.get("geometry").length if "geometry" in data else 1.0)
        candidates = edges[(edges["u"] == u) & (edges["v"] == v)]
        if not candidates.empty:
            # pick candidate with closest length
            j = (candidates["length"] - geom_len).abs().sort_values().index[0]
            data["weight"] = float(edges.at[j, "weight"])
        else:
            data["weight"] = geom_len  # fallback

    # route
    o_node = ox.distance.nearest_nodes(G, X=o_lon, Y=o_lat)
    d_node = ox.distance.nearest_nodes(G, X=d_lon, Y=d_lat)
    route = nx.shortest_path(G, o_node, d_node, weight="weight")

    coords = [(G.nodes[n]["x"], G.nodes[n]["y"]) for n in route]
    line   = LineString(coords)

    # output metrics (osmnx>=2 safe)
    edge_lengths, edge_weights = route_edge_lists(G, route)
    total_len = sum(edge_lengths)
    total_cost = sum(edge_weights)
    print(f"Safest route: {len(route)-1} segments, {total_len:.0f} m, cost={total_cost:.0f}")

    # export
    feat = {"type":"Feature",
            "geometry":{"type":"LineString","coordinates":coords},
            "properties":{"name":"Safest Night Walk","length_m":total_len,"cost":total_cost}}
    with open(SAVE_GEOJSON, "w") as f:
        json.dump({"type":"FeatureCollection","features":[feat]}, f)
    print(f"GeoJSON saved → {SAVE_GEOJSON}")

if __name__ == "__main__":
    main()
